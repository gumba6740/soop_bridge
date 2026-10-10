from collections.abc import AsyncGenerator
from typing import Annotated

from fastapi import Depends, Request, WebSocket
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import SessionLocal
from app.core.hub import ChatHub
from app.core.r import redis_client


async def get_db() -> AsyncGenerator[AsyncSession]:
    async with SessionLocal() as session:
        yield session


async def get_r() -> Redis:
    return redis_client


async def get_registry(request: WebSocket) -> dict[str, set[WebSocket]]:
    registry: dict[str, set[WebSocket]] = request.app.state.registry
    return registry


def get_chat_hub(request: Request) -> ChatHub:
    hub: ChatHub = request.app.state.chat_hub
    return hub


DBDep = Annotated[AsyncSession, Depends(get_db)]
RDep = Annotated[Redis, Depends(get_r)]
RgDep = Annotated[dict[str, set[WebSocket]], Depends(get_registry)]
HubDep = Annotated[ChatHub, Depends(get_chat_hub)]