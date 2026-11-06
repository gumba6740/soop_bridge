"""
pytest tests/broadcast/review/api/services/test_vod_services.py
"""

from datetime import datetime, timedelta

import pytest

from app.broadcast.review.api.exceptions import VodNotFound
from app.broadcast.review.api.schemas.vod_schemas import VodDetailOut, VodFileOut
from app.broadcast.review.api.services import vod_services
from app.models import Streamer, Vod, VodFile
from app.utils.tz import KST

VOD_ID = 1000
BID = "방송인"
BROAD_START = datetime(2026, 9, 1, 20, 0, tzinfo=KST)


async def _add(db, *objs) -> None:
    """넣고 커밋한다."""
    db.add_all(objs)
    await db.commit()


def _vod() -> Vod:
    """목록에 필요한 칸을 모두 채운 VOD."""
    return Vod(
        vod_id=VOD_ID,
        bid=BID,
        title="다시보기 제목",
        total_duration=5_400_000,
        broad_start=BROAD_START,
        broad_end=BROAD_START + timedelta(hours=2),
        grade=0,
        media_code=1,
        broad_idx=777,
        hashtags=["게임", "토크"],
    )


def _part(file_order: int, start_sec: float | None) -> VodFile:
    file_start = (
        None if start_sec is None else BROAD_START + timedelta(seconds=start_sec)
    )
    return VodFile(
        vod_id=VOD_ID,
        file_order=file_order,
        duration=1_800_000,
        hls_url=f"영상 주소 {file_order}",
        file_start=file_start,
    )


@pytest.mark.asyncio
async def test_get_vod_unknown_raises(db):
    """없는 VOD 면 VodNotFound 를 내는지."""
    with pytest.raises(VodNotFound):
        await vod_services.get_vod(db, VOD_ID)


@pytest.mark.asyncio
async def test_get_vod(db):
    """성공 케이스. 파트는 file_order 순으로, 시작 시각이 없는 파트는 offset 을 None 으로 주는지."""
    await _add(db, Streamer(bid=BID, nick="닉네임"), _vod())
    # 일부러 file_order 를 섞어서 넣는다
    await _add(db, _part(3, None), _part(1, 0), _part(2, 1800))

    vod = await vod_services.get_vod(db, VOD_ID)

    assert vod == VodDetailOut(
        vod_id=VOD_ID,
        title="다시보기 제목",
        total_duration=5_400_000,
        broad_start=BROAD_START,
        broad_end=BROAD_START + timedelta(hours=2),
        grade=0,
        media_code=1,
        broad_idx=777,
        hashtags=["게임", "토크"],
        bid=BID,
        nick="닉네임",
        files=[
            VodFileOut(
                file_order=1,
                duration=1_800_000,
                hls_url="영상 주소 1",
                offset_base_sec=0,
            ),
            VodFileOut(
                file_order=2,
                duration=1_800_000,
                hls_url="영상 주소 2",
                offset_base_sec=1800,
            ),
            VodFileOut(
                file_order=3,
                duration=1_800_000,
                hls_url="영상 주소 3",
                offset_base_sec=None,  # 시작 시각을 모르는 파트
            ),
        ],
    )


@pytest.mark.asyncio
async def test_get_vod_nick_falls_back_to_bid(db):
    """streamers 에 없는 스트리머의 VOD 면 nick 자리에 bid 를 주는지."""
    await _add(db, _vod())

    vod = await vod_services.get_vod(db, VOD_ID)

    assert vod.nick == BID
