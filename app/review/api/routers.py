"""review API 라우터."""

from typing import Annotated

from fastapi import APIRouter, Query

from app.core.deps import DBDep, RDep
from app.review.api.schemas.chat_schemas import ChatWindowOut
from app.review.api.schemas.vod_schemas import (
    StreamerOut,
    VodDetailOut,
    VodListOut,
)
from app.review.api.services import chat_services, vod_services

router = APIRouter(tags=["review"])


@router.get("/streamers")
async def list_streamers() -> list[StreamerOut]:
    return vod_services.list_streamers()


@router.get("/streamers/{bid}")
async def get_streamer(bid: str) -> StreamerOut:
    return vod_services.get_streamer(bid)


@router.get("/streamers/{bid}/vods")
async def list_vods(
    db: DBDep,
    bid: str,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    cursor: Annotated[int | None, Query(description="이전 응답의 next_cursor")] = None,
) -> VodListOut:
    return await vod_services.list_vods(db, bid, limit=limit, before=cursor)


@router.get("/vods/{vod_id}")
async def get_vod(db: DBDep, vod_id: int) -> VodDetailOut:
    return await vod_services.get_vod(db, vod_id)


@router.get("/vods/{vod_id}/chats")
async def get_chats(
    db: DBDep,
    r: RDep,
    vod_id: int,
    start: Annotated[float, Query(ge=0, description="초, 포함")],
) -> ChatWindowOut:
    return await chat_services.get_chats(db, r, vod_id, start=start)