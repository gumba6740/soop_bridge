"""홈(스트리머 목록), 방송국 정보, 방송국의 다시보기 목록."""

from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.broadcast.review.api.schemas.vod_schemas import VodListItem, VodListOut
from app.core.config import settings
from app.models import Streamer, Vod
from app.station.exceptions import StreamerNotFound
from app.station.schemas import StationOut

# ---------------------------------------------------------------- 스트리머


async def get_streamer(db: AsyncSession, bid: str) -> Streamer:
    """streamers 테이블의 스트리머. 없으면 404. live API도 bid 확인에 쓴다."""
    streamer = await db.get(Streamer, bid)
    if streamer is None:
        raise StreamerNotFound()
    return streamer


async def _is_live(r: Redis, bids: list[str]) -> list[bool]:
    """bid마다 방송 중인지. redis summary가 있으면 방송 중. 요청은 pipeline으로 한 번에."""
    async with r.pipeline(transaction=False) as pipe:
        for bid in bids:
            pipe.exists(settings.summary_key(bid))
        found: list[int] = await pipe.execute()
    return [bool(n) for n in found]


async def list_stations(db: AsyncSession, r: Redis) -> list[StationOut]:
    """홈 화면의 스트리머 목록. 닉네임 오름차순."""
    streamers = sorted(await db.scalars(select(Streamer)), key=lambda s: s.nick)
    lives = await _is_live(r, [s.bid for s in streamers])
    return [
        StationOut(bid=s.bid, nick=s.nick, is_live=live)
        for s, live in zip(streamers, lives, strict=True)
    ]


async def get_station(db: AsyncSession, r: Redis, bid: str) -> StationOut:
    s = await get_streamer(db, bid)
    [live] = await _is_live(r, [bid])
    return StationOut(bid=s.bid, nick=s.nick, is_live=live)


# ------------------------------------------------------- 다시보기 목록


async def list_reviews(
    db: AsyncSession, bid: str, *, limit: int, before: int | None
) -> VodListOut:
    """최신순 VOD 목록. before가 있으면 그 vod_id보다 오래된 것만."""
    await get_streamer(db, bid)  # 등록 안 된 bid면 404

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