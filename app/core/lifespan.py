import asyncio
import contextlib
import json
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import cast

from fastapi import FastAPI

from app.core.config import settings
from app.core.soop_clients.review_client import aclose
from app.core.db import engine
from app.core.r import redis_client as r
import logging


logger = logging.getLogger(__name__)

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    """fastapi 앱과 생명주기를 같이 하는 함수."""

    app.state.registry = {}
    task = asyncio.create_task(reader(app))

    try:
        yield
    finally:
        task.cancel()
        await aclose()
        await r.aclose()
        await engine.dispose()


streamkey = settings.redis_stream_key


# (entry_id, fields dict)
StreamEntry = tuple[str, dict[str, str]]
# (stream_key, [entries])
StreamResp = list[tuple[str, list[StreamEntry]]]


async def reader(app: FastAPI) -> None:
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
