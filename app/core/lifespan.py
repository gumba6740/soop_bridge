import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.core.db import engine
from app.core.hub import ChatHub
from app.core.r import redis_client as r
from app.core.soop_clients.review_client import aclose

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    """fastapi 앱과 생명주기를 같이 하는 함수."""

    app.state.chat_hub = ChatHub(r)

    try:
        yield
    finally:
        await app.state.chat_hub.aclose()
        await aclose()
        await r.aclose()
        await engine.dispose()
