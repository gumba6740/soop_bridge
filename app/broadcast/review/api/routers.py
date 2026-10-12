"""review API 라우터."""

from typing import Annotated

from fastapi import APIRouter, Query

from app.core.deps import DBDep, RDep
from app.broadcast.review.api.schemas.chat_schemas import ChatWindowOut
from app.broadcast.review.api.schemas.vod_schemas import VodDetailOut
from app.broadcast.review.api.services import chat_services, vod_services

router = APIRouter(prefix="/review", tags=["review"])


@router.get("/{vod_id}")
async def get_vod(db: DBDep, vod_id: int) -> VodDetailOut:
    return await vod_services.get_vod(db, vod_id)


@router.get("/{vod_id}/chats")
async def get_chats(
    db: DBDep,
    r: RDep,
    vod_id: int,
    start: Annotated[float, Query(ge=0, description="초, 포함")],
) -> ChatWindowOut:
    return await chat_services.get_chats(db, r, vod_id, start=start)