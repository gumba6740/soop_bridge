import logging

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.broadcast.review.api.exceptions import VodNotFound
from app.broadcast.review.api.schemas.vod_schemas import (
    ViewOut,
    VodDetailOut,
    VodFileOut,
    VodListItem,
)
from app.models import Streamer, Vod, VodView

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
    view = await db.get(VodView, vod_id)

    return VodDetailOut(
        **VodListItem.model_validate(vod).model_dump(),
        bid=vod.bid,
        # streamers 테이블에 없는 스트리머의 vod에 대비. 수동으로 돌린 경우
        # 이 케이스가 생길 수 있음.
        nick=streamer.nick if streamer is not None else vod.bid,
        views=view.cnt if view is not None else 0,
        files=files,
    )


# ---------------------------------------------------------------- 조회수


async def add_view(db: AsyncSession, vod_id: int) -> ViewOut:
    """다시보기 조회수 +1"""
    stmt = (
        insert(VodView)
        .values(vod_id=vod_id, cnt=1)
        .on_conflict_do_update(
            index_elements=[VodView.vod_id],
            set_={"cnt": VodView.cnt + 1},
        )
        .returning(VodView.cnt)
    )
    try:
        cnt = (await db.execute(stmt)).scalar_one()
    except IntegrityError:
        # vod_id 외래키 위반 = vods에 없는 VOD
        await db.rollback()
        raise VodNotFound() from None
    await db.commit()
    return ViewOut(views=cnt)
