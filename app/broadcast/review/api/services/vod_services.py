import logging

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.models import Vod
from app.broadcast.review.api.schemas.vod_schemas import (
    StreamerOut, VodListOut, VodListItem, VodFileOut, VodDetailOut
)
from app.broadcast.review.api.exceptions import StreamerNotFound, VodNotFound

from sqlalchemy.ext.asyncio import AsyncSession



logger = logging.getLogger(__name__)

NICK_BY_BID = {bid: nick for nick, bid in BID.items()}


# ---------------------------------------------------------------- 스트리머

def list_streamers() -> list[StreamerOut]:
    return [StreamerOut(bid=bid, nick=nick) for nick, bid in BID.items()]

# TODO: 추후 streamer 상세정보를 수집하게 된다면 수정.
def get_streamer(bid: str) -> StreamerOut:
    nick = NICK_BY_BID.get(bid)
    if nick is None:
        raise StreamerNotFound()
    return StreamerOut(bid=bid, nick=nick)


# ---------------------------------------------------------------- VOD

async def list_vods(
    db: AsyncSession, bid: str, *, limit: int, before: int | None
) -> VodListOut:
    """최신순 VOD 목록. before가 있으면 그 vod_id보다 오래된 것만."""
    get_streamer(bid)  # 등록 안 된 bid면 404

    # limit보다 1개 더 가져와서, 남는 게 있으면 다음 페이지가 있다는 뜻.
    stmt = (
        select(Vod)
        .where(Vod.bid == bid)
        .order_by(Vod.vod_id.desc())
        .limit(limit + 1)
    )
    if before is not None:
        stmt = stmt.where(Vod.vod_id < before)

    vods = list(await db.scalars(stmt))
    has_more = len(vods) > limit
    vods = vods[:limit]

    return VodListOut(
        items=[VodListItem.model_validate(v) for v in vods],
        next_cursor=vods[-1].vod_id if has_more else None,
    )


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

    return VodDetailOut(
        **VodListItem.model_validate(vod).model_dump(),
        bid=vod.bid,
        nick=NICK_BY_BID.get(vod.bid),
        files=files,
    )