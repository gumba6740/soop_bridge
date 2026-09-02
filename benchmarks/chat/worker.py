import asyncio
import sys
import time
from datetime import datetime

import psycopg
import redis.asyncio as redis

from app.core.config import settings

SQL = """INSERT INTO chats (stream_id, user_id, nickname, message, received_at, svc_code, msg_seq)
    VALUES (%s, %s, %s, %s, %s, %s, %s)
    ON CONFLICT (stream_id, msg_seq) DO NOTHING"""

GROUP = "chat_workers"  # consumer group 이름
REPORT_EVERY = 5.0  # report()는 최소 이 정도에 한번씩 호출됨.
BATCH = 100
RECLAIM_IDLE_MS = (
    10000  # 밀리초. 이 이상 방치된 pending만 회수
)


async def ensure_group(r, stream_key):
    """consumer group을 만든다. 이미 있으면 그냥 넘어감."""
    try:
        await r.xgroup_create(stream_key, GROUP, id="0", mkstream=True)
        print(f"그룹 생성: {GROUP}")
    except redis.ResponseError as e:
        if "BUSYGROUP" in str(e):
            print(f"그룹 이미 존재: {GROUP}")
        else:
            raise


def dump_latencies(consumer, latencies):
    """종료 시 지연값 전체를 CSV로 뱉고, 요약(p50/p95/p99)을 화면에 찍는다."""
    if not latencies:
        print("지연 기록 없음 (enqueued_at 없는 채팅만 들어왔거나 처리 0건)")
        return
    path = f"/tmp/lat_{consumer}.csv"
    with open(path, "w") as f:
        f.write("latency_ms\n")
        for lat in latencies:
            f.write(f"{lat * 1000:.3f}\n")

    s = sorted(latencies)
    n = len(s)

    def pct(p):
        return s[min(n - 1, int(n * p))] * 1000

    print(
        f"★ 지연 요약 (n={n}): "
        f"p50={pct(0.50):.1f}ms  p95={pct(0.95):.1f}ms  "
        f"p99={pct(0.99):.1f}ms  max={s[-1] * 1000:.1f}ms"
    )
    print(f"  전체 기록: {path}")


async def main(bid: str, consumer: str, measure: bool):
    """
    redis에서 pel 또는 스트림을 읽어서 디비에 저장.
    measure 옵션이 켜져 있으면 채팅 처리 지연시간이 latencies(파이썬 리스트)에 계속 쌓이니 주의.
    """
    stream_key = f"chat_stream:{bid}"
    r = redis.from_url(settings.redis_url, decode_responses=True, socket_timeout=None)
    aconn = await psycopg.AsyncConnection.connect(settings.postgres_url)

    await ensure_group(r, stream_key)
    print(
        f"워커 시작 [{consumer}]. 계측 {'ON (T3-2)' if measure else 'OFF (기준선)'}. "
        f"큐 대기 중... ({stream_key})"
    )

    total = 0  # 누적 처리 건수
    t_start = None  # 워커가 소켓을 통해 처음 메시지를 받은 시각. 상수.
    t_last = time.perf_counter()  # 마지막 리포트 시각. 리포트 출력 이후 now로 덮어쓰여짐.
    last_total = 0  # 직전 total로 덮어쓰여짐.
    latencies = []  # 각 채팅이 redis 큐에 들어가서 DB에 적재될 때까지 걸린 시간들을 
                    #전부 append. measure=True일 때만 채워짐.
    reclaimed_total = 0  # pending에 10초 이상 올라간 데이터가 몇 개 회수됐는지.

    def report(final=False):
        """
        report()는 worker가 작동되는 동안 최소 5초 간격으로 계속 호출되며, 
        이 간격 동안의 초당 처리량을 출력함.
        또한 지금까지의 총 처리 데이터, 총 경과 시간, 평균초당처리량, 평균지연시간, 회수량을 출력함.
        
        프로세스 종료 시 final=True가 되면서 최종임을 출력함.
        """
        nonlocal t_last, last_total
        if total == last_total and not final:
            return
        now = time.perf_counter()
        if t_start is None:
            return
        window = now - t_last  
        chunk = total - last_total
        rate_now = chunk / window if window > 0 else 0
        elapsed = now - t_start
        rate_avg = total / elapsed if elapsed > 0 else 0
        tag = "최종" if final else "진행"
        # 계측 켰을 때만 지연을 곁들여 보여준다 (끄면 T3-1과 똑같은 출력)
        lat_str = (
            f"  평균지연 {sum(latencies) / len(latencies) * 1000:.1f}ms"
            if (measure and latencies)
            else ""
        )
        rc_str = f"  회수 {reclaimed_total}" if reclaimed_total else ""
        print(
            f"[{consumer}] {tag}: 누적 {total}건  경과 {elapsed:.1f}s  "
            f"최근 {rate_now:.0f}/s  평균 {rate_avg:.0f}/s{lat_str}{rc_str}"
        )
        t_last, last_total = now, total

    reclaim_cursor = "0-0"

    try:
        while True:
            try:
                # pel에 10초 이상 지난 메시지를 회수해서 messages에 넘겨줌.
                # 각각 커서, 회수된 메시지, pel에는 있지만 스트림에서는 삭제된 메시지의 id
                reclaim_cursor, claimed, _ = await r.xautoclaim(
                    stream_key,
                    GROUP,
                    consumer,
                    min_idle_time=RECLAIM_IDLE_MS,
                    start_id=reclaim_cursor,
                    count=BATCH,
                )
            except redis.RedisError as e:
                print(f"XAUTOCLAIM 에러: {e}")
                claimed, reclaim_cursor = [], "0-0"

            if claimed:
                messages = claimed
                reclaimed_total += len(claimed)
            else:
                try:
                    # 회수할 게 없으면 xreadgroup()
                    resp = await r.xreadgroup(
                        GROUP,
                        consumer,
                        {stream_key: ">"},
                        count=BATCH,
                        block=5000,
                    )
                except redis.RedisError as e:
                    print(f"Redis 에러: {e}")
                    await asyncio.sleep(1)
                    continue

                # 큐가 비어서 continue를 해도 report()는 찍음
                if not resp:
                    report()
                    continue

                _, messages = resp[0]

            rows = []
            ids = []
            enq_times = []  # measure=True일 때만 각 채팅 "발송 시각"을 담는다
            for msg_id, fields in messages:
                if t_start is None:
                    t_start = time.perf_counter()
                rows.append(
                    (
                        fields["stream_id"],
                        fields["user_id"],
                        fields["nickname"],
                        fields["message"],
                        datetime.fromisoformat(fields["received_at"]),
                        fields["svc_code"],
                        int(fields["msg_seq"]),
                    )
                )
                ids.append(msg_id)

                # enqueued_at(채팅이 큐에 들어간 시각)을 꺼내 enq_times에 담는다.
                # 이후 enq_times를 사용해 지연시간을 계산해 latencies에 담는다.
                if measure:
                    enq_times.append(
                        float(fields["enqueued_at"])  # 이 필드는 더미 데이터에만 있음
                        if "enqueued_at" in fields
                        else None
                    )

            # DB 저장 ===================================================
            COLS = (
                "stream_id, user_id, nickname, message, received_at, svc_code, msg_seq"
            )
            async with aconn.cursor() as cur:
                placeholders = ",".join(["(%s,%s,%s,%s,%s,%s,%s)"] * len(rows))
                flat = [x for row in rows for x in row]
                sql = (
                    f"INSERT INTO chats ({COLS}) VALUES {placeholders} "
                    f"ON CONFLICT (stream_id, msg_seq) DO NOTHING"
                )

            async with aconn.cursor() as cur:
                await cur.execute(sql, flat)
            await aconn.commit()

            # latencies에 지연시간을 담음.
            if measure:
                saved_at = time.time()
                for enq in enq_times:
                    if enq is not None:
                        latencies.append(saved_at - enq)

            await r.xack(stream_key, GROUP, *ids)

            total += len(rows)

            if time.perf_counter() - t_last >= REPORT_EVERY:
                report()
    finally:
        if measure:
            dump_latencies(
                consumer, latencies
            )
        report(final=True)  # "진행"으로 출력되던 게 "최종"으로 출력됨.
        if reclaimed_total:
            print(f"[{consumer}] 회수 누적(XAUTOCLAIM): {reclaimed_total}건")
        await aconn.close()
        await r.aclose()


if __name__ == "__main__":
    # 사용: python -m app.chat.redis.worker [bid] [consumer] [--measure]
    import argparse
    try:
        parser = argparse.ArgumentParser()
        parser.add_argument("bid")
        parser.add_argument("consumer")
        parser.add_argument("--measure", action="store_true")
        args = parser.parse_args()
        
        asyncio.run(main(args.bid, args.consumer, args.measure))
    except KeyboardInterrupt:
        print("\n워커 종료")
