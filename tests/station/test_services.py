"""
pytest tests/station/test_services.py
"""

from datetime import datetime

import pytest

from app.core.config import settings
from app.models import Streamer, Vod
from app.station import services
from app.station.exceptions import StreamerNotFound
from app.station.schemas import StationOut
from app.utils.tz import KST


async def _add_streamers(db, *pairs: tuple[str, str]) -> None:
    """(bid, 닉네임) 들을 streamers 에 넣는다."""
    db.add_all(Streamer(bid=bid, nick=nick) for bid, nick in pairs)
    await db.commit()


async def _go_live(r, bid: str) -> None:
    """라이브 수집기가 하듯 summary 를 올려 방송 중으로 만든다."""
    await r.hset(settings.summary_key(bid), mapping={"title": "방송 제목"})


def _vod(vod_id: int, bid: str = "방송인") -> Vod:
    """목록에 필요한 칸을 모두 채운 VOD."""
    return Vod(
        vod_id=vod_id,
        bid=bid,
        title=f"다시보기 {vod_id}",
        total_duration=60_000,
        broad_start=datetime(2026, 9, 1, 20, 0, tzinfo=KST),
        broad_end=None,
        grade=0,
        media_code=1,
        broad_idx=None,
        hashtags=["게임"],
    )


# ---------------------------------------------------------------- get_streamer


@pytest.mark.asyncio
async def test_get_streamer(db):
    await _add_streamers(db, ("방송인", "닉네임"))

    streamer = await services.get_streamer(db, "방송인")

    assert (streamer.bid, streamer.nick) == ("방송인", "닉네임")


@pytest.mark.asyncio
async def test_get_streamer_unknown_raises(db):
    with pytest.raises(StreamerNotFound):
        await services.get_streamer(db, "없는방송인")


# ---------------------------------------------------------------- _is_live


@pytest.mark.asyncio
async def test_is_live_keeps_order(r):
    await _go_live(r, "둘째")

    assert await services._is_live(r, ["첫째", "둘째", "셋째"]) == [False, True, False]


@pytest.mark.asyncio
async def test_is_live_empty(r):
    assert await services._is_live(r, []) == []


# ---------------------------------------------------------------- list_stations


@pytest.mark.asyncio
async def test_list_stations_sorted_by_nick_with_live(db, r):
    await _add_streamers(db, ("b1", "다람쥐"), ("b2", "가나다"), ("b3", "나비"))
    await _go_live(r, "b3")

    assert await services.list_stations(db, r) == [
        StationOut(bid="b2", nick="가나다", is_live=False),
        StationOut(bid="b3", nick="나비", is_live=True),
        StationOut(bid="b1", nick="다람쥐", is_live=False),
    ]


@pytest.mark.asyncio
async def test_list_stations_empty(db, r):
    assert await services.list_stations(db, r) == []


# ---------------------------------------------------------------- get_station


@pytest.mark.parametrize("live", [True, False])
@pytest.mark.asyncio
async def test_get_station(db, r, live):
    await _add_streamers(db, ("방송인", "닉네임"))
    if live:
        await _go_live(r, "방송인")

    station = await services.get_station(db, r, "방송인")

    assert station == StationOut(bid="방송인", nick="닉네임", is_live=live)


@pytest.mark.asyncio
async def test_get_station_unknown_raises(db, r):
    with pytest.raises(StreamerNotFound):
        await services.get_station(db, r, "없는방송인")


# ---------------------------------------------------------------- list_reviews


@pytest.mark.asyncio
async def test_list_reviews_newest_first_with_next_cursor(db):
    await _add_streamers(db, ("방송인", "닉네임"))
    db.add_all(_vod(vod_id) for vod_id in [101, 104, 102, 103, 105])
    await db.commit()

    page = await services.list_reviews(db, "방송인", limit=2, before=None)

    assert [item.vod_id for item in page.items] == [105, 104]
    assert page.next_cursor == 104  # 다음 페이지는 104보다 오래된 것부터


@pytest.mark.asyncio
async def test_list_reviews_before_cursor(db):
    await _add_streamers(db, ("방송인", "닉네임"))
    db.add_all(_vod(vod_id) for vod_id in [101, 102, 103, 104, 105])
    await db.commit()

    page = await services.list_reviews(db, "방송인", limit=2, before=104)

    assert [item.vod_id for item in page.items] == [103, 102]
    assert page.next_cursor == 102


@pytest.mark.asyncio
async def test_list_reviews_last_page_has_no_cursor(db):
    """딱 limit 개만 남았으면 다음 페이지가 없다."""
    await _add_streamers(db, ("방송인", "닉네임"))
    db.add_all(_vod(vod_id) for vod_id in [101, 102])
    await db.commit()

    page = await services.list_reviews(db, "방송인", limit=2, before=None)

    assert [item.vod_id for item in page.items] == [102, 101]
    assert page.next_cursor is None


@pytest.mark.asyncio
async def test_list_reviews_only_that_streamer(db):
    await _add_streamers(db, ("방송인", "닉네임"), ("다른방송인", "다른닉"))
    db.add_all([_vod(101), _vod(102, bid="다른방송인"), _vod(103)])
    await db.commit()

    page = await services.list_reviews(db, "방송인", limit=10, before=None)

    assert [item.vod_id for item in page.items] == [103, 101]


@pytest.mark.asyncio
async def test_list_reviews_item_fields(db):
    await _add_streamers(db, ("방송인", "닉네임"))
    db.add(_vod(101))
    await db.commit()

    [item] = (await services.list_reviews(db, "방송인", limit=10, before=None)).items

    assert item.title == "다시보기 101"
    assert item.total_duration == 60_000
    assert item.broad_start == datetime(2026, 9, 1, 20, 0, tzinfo=KST)
    assert item.hashtags == ["게임"]


@pytest.mark.asyncio
async def test_list_reviews_unknown_streamer_raises(db):
    with pytest.raises(StreamerNotFound):
        await services.list_reviews(db, "없는방송인", limit=10, before=None)
