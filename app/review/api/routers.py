"""review API 라우터. vod, chat 기능이 함께 있음."""

from typing import Annotated

from fastapi import APIRouter, Query

from app.core.deps import DBDep
from app.review.api import services
from app.review.api.schemas.vod_schemas import (
    StreamerOut, VodDetailOut, VodListOut
)
from app.review.api.schemas.chat_schemas import ChatWindowOut

router = APIRouter(tags=["review"])


@router.get("/streamers")
async def list_streamers() -> list[StreamerOut]:
    return services.list_streamers()


@router.get("/streamers/{bid}")
async def get_streamer(bid: str) -> StreamerOut:
    return services.get_streamer(bid)


@router.get("/streamers/{bid}/vods")
async def list_vods(
    db: DBDep,
    bid: str,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    cursor: Annotated[int | None, Query(description="이전 응답의 next_cursor")] = None,
) -> VodListOut:
    return await services.list_vods(db, bid, limit=limit, before=cursor)


@router.get("/vods/{vod_id}")
async def get_vod(db: DBDep, vod_id: int) -> VodDetailOut:
    return await services.get_vod(db, vod_id)


@router.get("/vods/{vod_id}/chats")
async def get_chats(
    db: DBDep,
    vod_id: int,
    start: Annotated[float, Query(ge=0, description="초, 포함")],
) -> ChatWindowOut:
    return await services.get_chats(db, vod_id, start=start)
