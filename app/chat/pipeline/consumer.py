"""redis 컨슈머.

producer와는 독립적인 프로세스로 작동하며,
디비에 데이터를 중복 없이 안정적으로 넣기 위해 xreadgroup 사용.
유실 및 ack 관리

pending 상태 확인: XINFO GROUPS <stream_key>
"""

import asyncio
import json
from datetime import datetime
from time import monotonic
from typing import Any

import psycopg
import redis.asyncio as redis
from redis.asyncio.client import Redis
from redis.exceptions import ResponseError

from app.core.config import settings

GROUP_NAME = "chat-to-postgres"
FLUSH_SIZE = 200
FLUSH_INTERVAL = 10.0
POLL_INTERVAL = 0.5  # flush 되는 조건이 충족되지 않았을 때 xreadgroup의 대기시간(초)


async def ensure_group(r: Redis) -> None:
    try:
        await r.xgroup_create(
            settings.redis_stream_key, GROUP_NAME, id="0", mkstream=True
        )
        print(f"{GROUP_NAME} 그룹 생성")

    except ResponseError as e:
        if "BUSYGROUP" not in str(e):
            raise
        print(f"{GROUP_NAME}이 이미 존재합니다.")


def parse_resp(resp: Any) -> tuple[list[tuple[Any, Any, Any]], list[Any]]:
    """
    redis stream에 저장할 때는 bid, parts, received_at만 넣었지만
    xreadgroup 응답은 [(stream_key, [(entry_id, fields), ...])]라서 파싱하는 작업.
    """
    _, entries = resp[0]
    rows = []
    ids = []
    for entry_id, fields in entries:
        rows.append((fields["bid"], json.loads(fields["parts"]), fields["received_at"]))
        ids.append(entry_id)

    return rows, ids


def make_sql(rows: list[tuple[Any, Any, Any]]) -> str:
    cols = "(bid, parts, received_at)"
    placeholders = ",".join(["(%s, %s, %s)"] * len(rows))
    return f"""INSERT INTO chats {cols} VALUES {placeholders}"""


def isflushready(buffer_rows: list[tuple[Any, Any, Any]], last_flush: float) -> bool:
    """
    이 함수는 버퍼의 인덱스가 FLUSH_SIZE 이상이거나, 버퍼가 비워진지 10초 이상이
    지났을 경우 True를 반환한다. 단 버퍼가 비어있다면 항상 False를 반환한다.
    """
    size_ready = len(buffer_rows) >= FLUSH_SIZE
    time_ready = monotonic() - last_flush >= FLUSH_INTERVAL
    return bool(buffer_rows) and (size_ready or time_ready)


async def broadcast_stats(buffer_rows: list[tuple[Any, Any, Any]], r: Redis) -> None:
    """
    1. 방송인(bid) 기준으로 분당 채팅수 집계. incr()
    2. 유저 id 기준으로 해당 유저 채팅 수 집계. zincrby()
    """
    for bid, parts, received_at in buffer_rows:
        dt = datetime.fromisoformat(received_at)
        minute = dt.strftime("%Y%m%d%H%M")
        await r.incr(f"count:{bid}:{minute}")
        await r.zincrby(name=f"rank:{bid}", amount=1.0, value=parts[1])
    print("방송 집계 중")


async def read_group_and_save(
    r: Redis, conn: psycopg.AsyncConnection, worker: str
) -> None:
    """
    스트림에서 데이터를 꺼내 디비에 저장하는 함수.
    유실 관리를 위해 xreadgroup 사용.
    """

    buffer_rows = []
    buffer_ids = []
    # monotonic: 가장 최근 컴퓨터 부팅 시점을 기준으로 단조 증가하는 시간을 돌려주는 함수.
    last_flush = monotonic()
    while True:
        resp = await r.xreadgroup(
            GROUP_NAME,
            consumername=worker,
            streams={settings.redis_stream_key: ">"},
            count=FLUSH_SIZE,
            block=1000,
        )
        if resp:
            rows, ids = parse_resp(resp)
            buffer_rows.extend(rows)
            buffer_ids.extend(ids)

        if isflushready(buffer_rows, last_flush):
            async with conn.cursor() as cur:
                sql = make_sql(buffer_rows)
                # psycopg 문법상 %s에 값을 넣기 위해
                # buffer_rows를 1차원 리스트로 만듦.
                flat = [x for row in buffer_rows for x in row]
                await cur.execute(sql, flat)
            await conn.commit()
            print(f"{len(buffer_rows)}개 저장 완료")
            await r.xack(settings.redis_stream_key, GROUP_NAME, *buffer_ids)
            print("ack 완료")

            await broadcast_stats(buffer_rows, r)

            last_flush = monotonic()
            buffer_rows = []
            buffer_ids = []

            print("===" * 20)
        else:
            # flush 조건이 충족됐다면 consumer가 밀릴 수 있으니 대기시간 없이 xread를 함.
            # 아닐 시 여유가 있으니 POLL_INTERVAL(초) 만큼의 대기시간을 가짐.
            await asyncio.sleep(POLL_INTERVAL)

async def main(worker: str = "chworker-1") -> None:
    async with (
        redis.from_url(settings.redis_url, decode_responses=True) as r,
        await psycopg.AsyncConnection.connect(settings.postgres_url) as conn,
    ):
        await ensure_group(r)
        await read_group_and_save(r, conn, worker)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("컨슈머 종료")
