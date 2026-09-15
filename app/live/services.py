import json
from datetime import UTC, datetime, timedelta
from typing import cast

from fastapi import WebSocket, WebSocketDisconnect
from psycopg.rows import DictRow, dict_row
from redis.asyncio import Redis

from app.core.config import settings
from app.core.deps import DBDep
from app.core.log import logger

SQL = """SELECT bid, user_id, user_nickname, message
    FROM chats_view
    WHERE bid = %s
    ORDER BY received_at DESC 
    LIMIT %s"""


async def get_chats(
    bid: str,
    conn: DBDep,
    limit: int = 100,
) -> list[DictRow]:
    """최근 limit개의 채팅 불러오기."""
    # dict_row로 각 행을 딕셔너리로 받음. 기본값은 튜플.
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(SQL, (bid, limit))
        return await cur.fetchall()


def stat_key(bid: str, recently_m: int) -> str:
    return f"stat:{bid}:{recently_m}"


async def stat_ingredient(
    bid: str, r: Redis, recently_m: int = 10
) -> tuple[list[tuple[str, float]], list[datetime], list[str | None]]:
    """
    현재 시각을 기준으로 recently_m분 전까지의 통계를 분 단위로 가져옴.
    현재 분을 포함하지 않음.
    """

    count_by_user = cast(
        list[tuple[str, float]],
        # sorted set(z)에서 역순으로 지정한 범위의 데이터를 가져옴.
        # sorted set: 멤버: 스코어 구조.
        await r.zrevrange(f"rank:{bid}", 0, 9, withscores=True),
    )

    now = datetime.now(UTC)
    # 현재 분은 아직 집계가 안 끝났으므로 제외, 직전 N분(완료된 분)만 구함
    minutes = [now - timedelta(minutes=m + 1) for m in range(recently_m)]
    # 여기서 strftime으로 초를 버림
    keys = [f"count:{bid}:{dt.strftime('%Y%m%d%H%M')}" for dt in minutes]
    counts = cast(list[str | None], await r.mget(keys))

    return count_by_user, minutes, counts


# (entry_id, fields dict)
StreamEntry = tuple[str, dict[str, str]]
# (stream_key, [entries])
StreamResp = list[tuple[str, list[StreamEntry]]]


async def stream_chats(bid: str, r: Redis, ws: WebSocket) -> None:
    """현재 쓰이지 않음.
    ws 연결마다 redis 풀을 하나씩 잡고 안 놓아주기 때문에,
    이 함수의 로직을 lifespan으로 옮겨 redis 풀을 관리함.
    """
    last_id = "$"
    try:
        while True:
            resp = cast(
                StreamResp,
                await r.xread(
                    {settings.redis_stream_key: last_id},
                    block=1000,
                ),
            )
            if not resp:
                continue

            _, entries = resp[0]
            for entry_id, fields in entries:  # noqa: B007
                if fields["bid"] == bid:
                    try:
                        parts = json.loads(fields["parts"])
                    except json.JSONDecodeError:
                        logger.warning(
                            "parts 데이터 깨짐. 이 메시지는 logger.warning에서 나옴"
                        )
                        continue

                    try:
                        message, uid, unick = parts[0], parts[1], parts[5]
                    except (KeyError, IndexError):
                        logger.error("키에러")
                        continue
                    chat = f"[{bid}] {unick}({uid}): {message}"
                    await ws.send_text(chat)
            last_id = entry_id
    except WebSocketDisconnect:
        # print(f"{bid}님 방송의 채팅 수신 종료")
        pass
