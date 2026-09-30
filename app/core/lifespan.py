import asyncio
import contextlib
import json
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import cast

import redis.asyncio as redis
from fastapi import FastAPI
from psycopg_pool import AsyncConnectionPool

from app.core.config import settings
from app.core.soop_clients.review_client import aclose
import logging


logger = logging.getLogger(__name__)

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    """fastapi 앱과 생명주기를 같이 하는 함수.

    앱이 켜지면 워커(uvicorn 등) 당 딱 한 번씩 호출되고, 앱이 종료되면 같이 종료됨.
    db 풀, redis 클라이언트, live api를 통해 등록된 웹소켓을 관리하며,
    채팅 reader도 여기서 관리한다(api 호출 횟수와 무관하게 딱 한번 호출하려는 목적).
    """
    async with (
        AsyncConnectionPool(
            settings.postgres_url,
            min_size=10,
            kwargs={"autocommit": True},
        ) as pool,
        redis.from_url(settings.redis_url, decode_responses=True) as r,
    ):
        app.state.pool = pool
        app.state.redis = r
        app.state.registry = {}
        task = asyncio.create_task(reader(app))

        try:
            yield
        finally:
            task.cancel()
            await aclose()


streamkey = settings.redis_stream_key


# (entry_id, fields dict)
StreamEntry = tuple[str, dict[str, str]]
# (stream_key, [entries])
StreamResp = list[tuple[str, list[StreamEntry]]]


async def reader(app: FastAPI) -> None:
    r: redis.Redis = app.state.redis
    rg = app.state.registry
    last_id = "$"

    while True:
        resp = cast(StreamResp, await r.xread({streamkey: last_id}, block=1000))

        if not resp:
            continue

        _, entries = resp[0]
        # 하단의 last_id = entry_id가 for문 바깥에 있는 게 ruff에 걸려 noqa를 명시.
        # for문이 한차례 순회한 이후 마지막 entry_id를 담기 위한 의도적인 구조.
        for entry_id, fields in entries:  # noqa: B007
            bid = fields["bid"]
            try:
                parts = json.loads(fields["parts"])
            except json.JSONDecodeError:
                logger.warning("parts 데이터 깨짐. 이 메시지는 logger.warning에서 나옴")
                continue

            try:
                message, uid, unick = parts[0], parts[1], parts[5]
            except (KeyError, IndexError):
                logger.error("키에러")
                continue

            chat = f"[{bid}] {unick}({uid}): {message}"

            # 순회 도중 웹소켓이 등록될 수 있어서 리스트를 씌워 만든 카피본을 순회하게 함.
            for ws in list(rg.get(bid, set())):
                with contextlib.suppress(Exception):
                    await ws.send_text(chat)
        last_id = entry_id
