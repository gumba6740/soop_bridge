"""review API 라우터."""

from typing import Annotated

from fastapi import APIRouter, Query

from app.broadcast.review.api.schemas.chat_schemas import ChatWindowOut
from app.broadcast.review.api.schemas.vod_schemas import ViewOut, VodDetailOut
from app.broadcast.review.api.services import chat_services, vod_services
from app.core.deps import DBDep, RDep

router = APIRouter(prefix="/review", tags=["review"])


@router.get("/{vod_id}")
async def get_vod(db: DBDep, vod_id: int) -> VodDetailOut:
    return await vod_services.get_vod(db, vod_id)


@router.post("/{vod_id}/view")
async def add_view(db: DBDep, vod_id: int) -> ViewOut:
    """VOD 페이지를 열 때 프론트가 한 번 보낸다. 올린 뒤의 조회수를 돌려준다."""
    return await vod_services.add_view(db, vod_id)


@router.get("/{vod_id}/chats")
async def get_chats(
    db: DBDep,
    r: RDep,
    vod_id: int,
    start: Annotated[float, Query(ge=0, description="초, 포함")],
) -> ChatWindowOut:
    return await chat_services.get_chats(db, r, vod_id, start=start)
