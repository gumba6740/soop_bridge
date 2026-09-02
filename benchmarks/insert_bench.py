#!/usr/bin/env python3
"""chats 테이블 INSERT 성능 측정 하네스

A: execute 1회마다 commit 1회. 단일 행 적재 sql.
B: 단일 행 적재 execute가 batch_size번 호출될 때마다 commit 1회.
C: execute 1회마다 commit 1회. batch_size개의 행 적재 sql.
D: executemany 1회마다 commit 1회. 한번에 batch_size개.
E: copy 1회마다 commit 1회. 한번에 batch_size개.

이 스크립트의 비교 기준:
    1. execute 횟수는 동일, commit 횟수에서 차이.             -> A vs B
    2. 문장 형태만 다르고 commit 횟수는 동일. B는 대조군으로 사용.  -> B vs C/D/E
    3. 인덱스 조건에서 저장 실험. 5만 개 데이터를 아래 조건 당 3번씩 돌려서 중앙값을 뽑음.
        데이터가 5만 개라 과소평가 됐을 수 있음.

        1. pk 컬럼에만 인덱스가 있을 때의 저장 완료 시간.
        2. pk + received_at(단조 증가 데이터).
        3. pk + user_id(램덤 데이터).
        4. pk + received_at + user_id + (stream_id, received_at DESC) 4개.
        5. pk + unique(stream_id, user_id, received_at).

    4. fsync 응답의 대기시간이 적재 속도에 얼마나 영향이 있는지를 측정.
        옵션(synchronous_commit)의 on, off 상태에서의 속도를 비교.



    --experiment methods : 인덱스는 PK만 고정하고 INSERT 방식만 바꿈(A~E).
    --experiment indexes : 방법은 executemany로 고정하고 인덱스 조합만 바꿈.

    ★ A처럼 너무 느린 방식은 인덱스 실험에 노이즈가 있을 수 있어 executemany로.

옵션:
    --dsn: 기본값은 settings.postgres_url.
    --size: 취급할 행 개수. 기본값 50000.
    --batch: 기본값 1000. A를 제외한 방식에서 쓰임.
    --repeats: 각 조건의 반복 시행 횟수. 기본값 3. 중앙값을 뽑기 위한 반복.
    --experiment: methods, indexes, both 중 하나. 기본값 both.
        methods : 인덱스는 PK만 고정하고 INSERT 방식만 바꿈(A to E).
        indexes : 방법은 executemany로 고정하고 인덱스 조합만 바꿈.
    --source: 데이터 출처. generate(더미 생성) 또는 db(실데이터). 기본값 db.
    --source-table: source가 db일 때 데이터를 가져올 테이블 이름. 기본값 chats.
    --sync-off: store_true. 이 옵션이 있으면 fsync 대기 off.
    --out: 테스트 결과물의 저장 경로(csv). 기본값 benchmarks/csv/bench_result.csv.
"""

import argparse
import csv
import math
import random
import statistics
import string
from datetime import UTC, datetime, timedelta
from time import perf_counter

import psycopg

from app.core.config import settings

DEFAULT_DSN = settings.postgres_url

COLS = "stream_id, user_id, nickname, message, received_at"
BENCH_TABLE = "chats_bench"  # 실험용 임시 테이블 (매 조건마다 DROP 후 재생성)


# ======================================================================
# 1. 데이터 준비: 실데이터가 아닌 더미 데이터를 쓸 때만
# ======================================================================
def generate_rows(n):
    """
    랜덤 데이터와 단조 증가 데이터에서 인덱스의 성능 차이를 확인하기 위해
    user_id는 랜덤하게, received_at은 단조 증가로 만든다.
    """
    base = datetime(2026, 7, 1, tzinfo=UTC)
    streams = [f"bj{i:02d}" for i in range(1, 11)]
    rows = []
    for i in range(n):
        uid = f"u{random.randint(1, 2000):05d}"
        msg_len = random.randint(10, 70)
        msg = "".join(random.choices(string.ascii_letters + " ㅋㅇ", k=msg_len))
        rows.append(
            (
                random.choice(streams),  # stream_id
                uid,  # user_id
                f"nick_{uid}",  # nickname
                msg,  # message
                base + timedelta(microseconds=i),  # received_at
            )
        )
    return rows


def load_from_db(conn, source_table, limit):
    with conn.cursor() as cur:
        cur.execute(f"SELECT {COLS} FROM {source_table} ORDER BY id LIMIT %s", (limit,))
        return cur.fetchall()  # received_at 은 datetime 으로 돌아옴


# ======================================================================
# 2. 테이블 세팅
# ======================================================================
INDEX_CONFIGS = {
    "1_pk_only": [],
    "2_monotonic": ["CREATE INDEX idx_recv ON {t} (received_at)"],
    "3_random": ["CREATE INDEX idx_user ON {t} (user_id)"],
    "4_three": [
        "CREATE INDEX idx_recv ON {t} (received_at)",
        "CREATE INDEX idx_user ON {t} (user_id)",
        "CREATE INDEX idx_stream_time ON {t} (stream_id, received_at DESC)",
    ],
    "5_unique": [
        "CREATE UNIQUE INDEX idx_uniq ON {t} (stream_id, user_id, received_at)"
    ],
}


def setup_table(conn, table, index_sqls):
    with conn.cursor() as cur:
        cur.execute(f"DROP TABLE IF EXISTS {table}")
        cur.execute(f"""
            CREATE TABLE {table} (
                id           BIGINT       GENERATED ALWAYS AS IDENTITY 
                                            PRIMARY KEY,
                stream_id    VARCHAR(50)  NOT NULL,
                user_id      VARCHAR(50)  NOT NULL,
                nickname     VARCHAR(100) NOT NULL,
                message      TEXT         NOT NULL,
                received_at  TIMESTAMPTZ  NOT NULL,
                svc_code     VARCHAR(4)   NOT NULL DEFAULT '0005'
            )
        """)
        for s in index_sqls:
            cur.execute(s.format(t=table))
    conn.commit()


def count_rows(conn, table):
    with conn.cursor() as cur:
        cur.execute(f"SELECT count(*) FROM {table}")
        return cur.fetchone()[0]


# ────────────────────────────────────────────────────────────────────────────
# 3. 적재 방법
#     각 함수는 "flush 1회당 걸린 시간(ms) 리스트"를 반환한다.
# ────────────────────────────────────────────────────────────────────────────
def m_A_per_row_commit(conn, table, rows, _):
    """batch를 사용하지 않는 건당 커밋."""
    lat = []
    sql = f"INSERT INTO {table} ({COLS}) VALUES (%s,%s,%s,%s,%s)"
    with conn.cursor() as cur:
        for r in rows:
            t = perf_counter()
            cur.execute(sql, r)
            conn.commit()
            lat.append((perf_counter() - t) * 1000)
    return lat


def m_A2_per_row_no_commit(conn, table, rows, batch_size):
    """1행 당 execute 1번. commit은 가장 마지막에 1번.
    건당 execute 1번에 드는 비용을 확인하려는 목적.
    """
    lat = []
    sql = f"INSERT INTO {table} ({COLS}) VALUES (%s,%s,%s,%s,%s)"
    with conn.cursor() as cur:
        for r in rows:
            t = perf_counter()
            cur.execute(sql, r)
            lat.append((perf_counter() - t) * 1000)
        conn.commit()
    return lat


def m_B_batch_commit(conn, table, rows, batch_size):
    """1행 당 execute 1번, execute batch_size번 당 1 커밋."""

    lat = []
    sql = f"INSERT INTO {table} ({COLS}) VALUES (%s,%s,%s,%s,%s)"
    with conn.cursor() as cur:
        for i in range(0, len(rows), batch_size):
            batch = rows[i : i + batch_size]
            t = perf_counter()
            for r in batch:
                cur.execute(sql, r)
            conn.commit()
            lat.append((perf_counter() - t) * 1000)
    return lat


def m_C_multirow(conn, table, rows, batch_size):
    """
    batch 개의 행 당 1 execute, execute 당 1 커밋. 
    """
    lat = []
    with conn.cursor() as cur:
        for i in range(0, len(rows), batch_size):
            batch = rows[i : i + batch_size]
            placeholders = ",".join(["(%s,%s,%s,%s,%s)"] * len(batch))
            flat = [x for r in batch for x in r]
            sql = f"INSERT INTO {table} ({COLS}) VALUES {placeholders}"
            t = perf_counter()
            cur.execute(sql, flat)
            conn.commit()
            lat.append((perf_counter() - t) * 1000)
    return lat


def m_D_executemany(conn, table, rows, batch_size):
    """batch_size 개의 행 당 1 executemany. 1executemany 당 1 commit."""
    lat = []
    sql = f"INSERT INTO {table} ({COLS}) VALUES (%s,%s,%s,%s,%s)"
    with conn.cursor() as cur:
        for i in range(0, len(rows), batch_size):
            batch = rows[i : i + batch_size]
            t = perf_counter()
            # executemany에는 2차원 배열을 넣어야 함.
            cur.executemany(sql, batch)
            conn.commit()
            lat.append((perf_counter() - t) * 1000)
    return lat


def m_E_copy(conn, table, rows, batch_size):
    """
    COPY를 사용한 벌크 삽입 방식.

    copy: SQL 파싱 비용이 거의 없고 WAL 처리 효율도 높아, 
        일반적으로 가장 빠른 삽입 성능을 제공한다.
    """
    lat = []
    with conn.cursor() as cur:
        for i in range(0, len(rows), batch_size):
            batch = rows[i : i + batch_size]
            t = perf_counter()
            with cur.copy(f"COPY {table} ({COLS}) FROM STDIN") as copy:
                for r in batch:
                    copy.write_row(r)
            conn.commit()
            lat.append((perf_counter() - t) * 1000)
    return lat


METHODS = {
    "A_per_row_commit": m_A_per_row_commit,
    "A2_per_row_no_commit": m_A2_per_row_no_commit,
    "B_batch_commit": m_B_batch_commit,
    "C_multirow": m_C_multirow,
    "D_executemany": m_D_executemany,
    "E_copy": m_E_copy,
}


# ======================================================================
# 4. 측정 실행기
# ======================================================================
def percentile(vals, p):
    """
    vals: lat(지연시간을 측정한 리스트).
    p: 0~100까지의 정수. p95, p99 등.
    """
    if not vals:
        return float("nan")
    s = sorted(vals)
    k = (len(s) - 1) * (p / 100)
    f, c = math.floor(k), math.ceil(k)  # f, c를 s의 인덱스로 사용함
    if f == c:
        return s[int(k)]
    return s[f] + (s[c] - s[f]) * (k - f)


def measure_single_run(conn, rows, method_fn, index_sqls, batch_size):
    """
    매 측정마다 테이블을 비운 뒤, DB 적재 함수 1개를 실행해서 결과를 집계.

    n: 적재할 데이터 개수
    stored: 적재된 데이터 개수
    lost: 유실된 데이터 개수
    seconds: 총 소요시간
    throughput: 초당 처리량
    p50_ms: p50
    p95_ms: p95
    """
    setup_table(conn, BENCH_TABLE, index_sqls)
    t0 = perf_counter()
    lat = method_fn(conn, BENCH_TABLE, rows, batch_size)
    wall = perf_counter() - t0
    stored = count_rows(conn, BENCH_TABLE)
    return {
        "n": len(rows),
        "stored": stored,
        "lost": len(rows) - stored,
        "seconds": wall,
        "throughput": len(rows) / wall,
        "p50_ms": percentile(lat, 50),
        "p95_ms": percentile(lat, 95),
    }


def run_repeats(conn, rows, method_fn, index_sqls, batch_size, repeats):
    """measure_single_run()을 repeats번 돌려서 집계."""
    runs = [
        measure_single_run(conn, rows, method_fn, index_sqls, batch_size) for _ in range(repeats)
    ]
    tps = [r["throughput"] for r in runs]
    med = statistics.median(tps)
    return {
        "runs": runs,
        "throughput_median": med,
        "throughput_all": tps,
        "lost_any": any(r["lost"] for r in runs),
    }


# ======================================================================
# 5. 실험
# ======================================================================
def experiment_methods(conn, rows, batch_size, repeats, results):
    """METHODS(A~E) 별로 run_repeats를 돌려서 집계 및 결과 출력."""
    print("\n=== 실험 1: INSERT 방법 (인덱스=PK만 고정) ===")
    print(
        f"{'method':<18}{'건/초(중앙값)':>16}{'op p50(ms)':>13}{'op p95(ms)':>13}{'유실':>7}"
    )
    for name, fn in METHODS.items():
        r = run_repeats(conn, rows, fn, INDEX_CONFIGS["1_pk_only"], batch_size, repeats)
        last = r["runs"][-1]
        print(
            f"{name:<18}{r['throughput_median']:>16,.0f}"
            f"{last['p50_ms']:>13.2f}{last['p95_ms']:>13.2f}"
            f"{('있음' if r['lost_any'] else '0'):>7}"
        )
        results.append(
            {
                "experiment": "methods",
                "condition": name,
                "index": "pk_only",
                "batch_size": batch_size,
                "throughput_median": r["throughput_median"],
                "p50_ms": last["p50_ms"],
                "p95_ms": last["p95_ms"],
            }
        )


def experiment_indexes(conn, rows, batch_size, repeats, results):
    """인덱스 조건을 바꿔가면서 run_repeats()를 실행, 집계 및 출력"""
    print("\n=== 실험 2: 인덱스 (방법=executemany 빠른경로 고정) ===")
    print("  ※ 느린 경로에서 재면 fsync가 인덱스 비용을 덮어 신호가 안 보인다.")
    print(
        f"{'index_config':<16}{'건/초(중앙값)':>16}{'PK만 대비':>12}{'op p95(ms)':>13}"
    )
    baseline = None
    for name, idx in INDEX_CONFIGS.items():
        r = run_repeats(conn, rows, m_D_executemany, idx, batch_size, repeats)
        med = r["throughput_median"]
        if baseline is None:
            baseline = med
        ratio = med / baseline
        last = r["runs"][-1]
        print(f"{name:<16}{med:>16,.0f}{ratio:>11.2f}x{last['p95_ms']:>13.2f}")
        results.append(
            {
                "experiment": "indexes",
                "condition": name,
                "index": name,
                "batch_size": batch_size,
                "throughput_median": med,
                "p50_ms": last["p50_ms"],
                "p95_ms": last["p95_ms"],
            }
        )

# ======================================================================
# 6. 메인
# ======================================================================
def main():
    # 1. 옵션 지정
    ap = argparse.ArgumentParser()
    ap.add_argument("--dsn", default=DEFAULT_DSN)
    ap.add_argument("--size", type=int, default=50000)
    ap.add_argument("--batch", type=int, default=1000, help="배치/커밋 단위")
    ap.add_argument("--repeats", type=int, default=3)
    ap.add_argument(
        "--experiment", choices=["methods", "indexes", "both"], default="both"
    )
    ap.add_argument("--source", choices=["generate", "db"], default="db")
    ap.add_argument("--source-table", default="chats")
    ap.add_argument(
        "--sync-off",
        action="store_true",
        help="synchronous_commit=off 로 fsync 대시 시간을 측정.",
    )
    ap.add_argument("--out", default="benchmarks/csv/bench_result.csv")
    args = ap.parse_args()

    # 2. DB 연결 및 설정(sync on/off)
    conn = psycopg.connect(args.dsn)
    conn.autocommit = False
    if args.sync_off:
        with conn.cursor() as cur:
            cur.execute("SET synchronous_commit = off")
            cur.execute("SHOW synchronous_commit")
            print("확인:", cur.fetchone()[0])  # 'off' 나와야 함
        conn.commit()
        print("⚠️  synchronous_commit = OFF (fsync 대기 안 함)")

    # 3. 실데이터를 쓸 것인가, 더미 데이터를 쓸 것인가를 정함.
    if args.source == "db":
        print(f"실제 데이터 로드: {args.source_table} 에서 {args.size}건")
        rows = load_from_db(conn, args.source_table, args.size)
    else:
        print(f"합성 데이터 생성: {args.size}건")
        rows = generate_rows(args.size)
    print(f"준비된 행: {len(rows)}건, 배치={args.batch}, 반복={args.repeats}")

    # 4. 실험할 방식을 정함.
    results = []  # 실험 결과가 results에 담김.
    if args.experiment in ("methods", "both"):
        experiment_methods(conn, rows, args.batch, args.repeats, results)
    if args.experiment in ("indexes", "both"):
        experiment_indexes(conn, rows, args.batch, args.repeats, results)

    # 5. args.out(csv 저장 경로)에 실험 결과 저장
    if results:
        with open(args.out, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(results[0].keys()))
            w.writeheader()
            w.writerows(results)
        print(f"\n{args.out} 저장")

    # 6. 실험 테이블 제거
    with conn.cursor() as cur:
        cur.execute(f"DROP TABLE IF EXISTS {BENCH_TABLE}")
    conn.commit()
    conn.close()


if __name__ == "__main__":
    main()
