import logging

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.models import Streamer, Vod
from app.broadcast.review.api.schemas.vod_schemas import (
    VodListItem, VodFileOut, VodDetailOut
)
from app.broadcast.review.api.exceptions import VodNotFound

from sqlalchemy.ext.asyncio import AsyncSession



logger = logging.getLogger(__name__)


# ---------------------------------------------------------------- VOD

async def get_vod(db: AsyncSession, vod_id: int) -> VodDetailOut:
    # files는 lazy="raise"라서 selectinload로 명시해서 같이 가져온다.
    stmt = select(Vod).where(Vod.vod_id == vod_id).options(selectinload(Vod.files))
    vod = await db.scalar(stmt)
    if vod is None:
        raise VodNotFound()

    files = [
        VodFileOut(
            file_order=f.file_order,
            duration=f.duration,
            hls_url=f.hls_url,
            offset_base_sec=(
                (f.file_start - vod.broad_start).total_seconds()
                if f.file_start is not None
                else None
            ),
        )
        for f in vod.files
    ]

    streamer = await db.get(Streamer, vod.bid)

    return VodDetailOut(
        **VodListItem.model_validate(vod).model_dump(),
        bid=vod.bid,
        # streamers 테이블에 없는 스트리머의 vod에 대비. 수동으로 돌린 경우 
        # 이 케이스가 생길 수 있음.
        nick=streamer.nick if streamer is not None else vod.bid,
        files=files,
    )