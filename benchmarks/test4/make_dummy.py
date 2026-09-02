"""
로커스트 부하 테스트를 위해 디비에 100만행을 저장하는 코드
--insert: INSERT로 저장, 없으면 COPY
--no: chats_bench_noidx 테이블에 저장, 없으면 chats_bench_idx 테이블에 저장
"""

import itertools
import random
from datetime import UTC, datetime, timedelta
from time import perf_counter

import psycopg

from app.core.config import settings

BIDS = [f"bid_{i + 1}" for i in range(100)]
WEIGHTS = [1 / (i + 1) for i in range(100)]

cum = list(itertools.accumulate(WEIGHTS))


def make_bid():
    # bid = random.choices(BIDS, WEIGHTS, k=1)[0]
    bid = random.choices(BIDS, cum_weights=cum, k=1)[0]

    return bid


PARTS = [
    "message",
    "uid",
    "2",
    "3",
    "4",
    "unick",
    "6",
    "7",
    "8",
    "9",
    "10",
    "11",
    "12",
    "13",
    "14",
    "15",
]


COPY_SQL = "COPY chats (bid, parts, received_at) FROM STDIN"
now = datetime(2026, 8, 1, tzinfo=UTC)


def copy_dummy(cur):
    start = perf_counter()

    with cur.copy(COPY_SQL) as copy:
        for _ in range(1_000_000):
            received_at = now - timedelta(seconds=random.randint(0, 30 * 24 * 3600))
            row = (make_bid(), PARTS, received_at)

            copy.write_row(row)

    end = perf_counter()
    print(f"100만행 저장 종료. 총 {(end - start):.2f} 초 걸림")


def make_row():
    received_at = now - timedelta(seconds=random.randint(0, 30 * 24 * 3600))
    return (make_bid(), PARTS, received_at)


def make_sql(rows, no) -> str:
    cols = "(bid, parts, received_at)"
    placeholders = ",".join(["(%s, %s, %s)"] * len(rows))
    if no:
        return f"""INSERT INTO chats_bench_noidx {cols} VALUES {placeholders}"""
    else:
        return f"""INSERT INTO chats_bench_idx {cols} VALUES {placeholders}"""


def insert_dummy(cur, conn, rows, no, num):
    start = perf_counter()

    for i in range(0, len(rows), 200):
        r = rows[i : i + 200]
        flat = [x for row in r for x in row]
        sql = make_sql(r, no)
        cur.execute(sql, flat)
        conn.commit()

    end = perf_counter()
    print(f"{num + 1}0만행 저장 종료. {(end - start):.2f} 초 걸림")


def main(num, insert, no):
    random.seed(42)

    with psycopg.connect(settings.postgres_url) as conn, conn.cursor() as cur:
        if insert:
            last = 0
            for i in range(num):
                rows = []
                for _ in range(100_000):
                    row = make_row()
                    rows.append(row)

                start = perf_counter()
                insert_dummy(cur, conn, rows, no, i)
                end = perf_counter()
                last += end - start
            print(f"총 {last:.2f}초 걸림")
        else:
            copy_dummy(cur)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--insert", action="store_true")
    parser.add_argument("--no", action="store_true")
    parser.add_argument("num", type=int)
    arg = parser.parse_args()

    print("저장 시작")
    main(arg.num, insert=arg.insert, no=arg.no)
