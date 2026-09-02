from collections.abc import AsyncGenerator
from typing import Annotated

from fastapi import Depends, Request, WebSocket
from psycopg import AsyncConnection
from redis.asyncio import Redis


async def get_db(request: Request) -> AsyncGenerator[AsyncConnection]:
    async with request.app.state.pool.connection() as conn:
        yield conn


async def get_r(request: Request) -> Redis:
    r: Redis = request.app.state.redis
    return r


async def get_registry(request: WebSocket) -> dict[str, set[WebSocket]]:
    registry: dict[str, set[WebSocket]] = request.app.state.registry
    return registry


DBDep = Annotated[AsyncConnection, Depends(get_db)]
RDep = Annotated[Redis, Depends(get_r)]
RgDep = Annotated[dict[str, set[WebSocket]], Depends(get_registry)]
