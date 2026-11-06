"""
pytest tests/station/test_routers.py
"""

from datetime import datetime

import pytest

from app.core.config import settings
from app.models import Streamer, Vod
from app.utils.tz import KST


def _vod(vod_id: int) -> Vod:
    return Vod(
        vod_id=vod_id,
        bid="방송인",
        title=f"다시보기 {vod_id}",
        total_duration=60_000,
        broad_start=datetime(2026, 9, 1, 20, 0, tzinfo=KST),
        grade=0,
        hashtags=[],
    )


@pytest.mark.asyncio
async def test_list_stations(client, db, r):
    """성공 케이스. 홈 화면 목록을 닉네임순으로, 방송 중 여부와 함께 주는지."""
    db.add_all([Streamer(bid="b1", nick="다람쥐"), Streamer(bid="b2", nick="가나다")])
    await db.commit()
    await r.hset(settings.summary_key("b1"), mapping={"title": "방송 제목"})

    response = await client.get("/")

    assert response.status_code == 200
    assert response.json() == [
        {"bid": "b2", "nick": "가나다", "is_live": False},
        {"bid": "b1", "nick": "다람쥐", "is_live": True},
    ]


@pytest.mark.asyncio
async def test_get_station(client, db):
    """성공 케이스. 경로의 bid 에 맞는 방송국 정보를 주는지."""
    # 다른 스트리머도 있어야 bid 로 골랐는지 알 수 있다
    db.add_all(
        [Streamer(bid="방송인", nick="닉네임"), Streamer(bid="다른", nick="가가")]
    )
    await db.commit()

    response = await client.get("/station/방송인")

    assert response.status_code == 200
    assert response.json() == {"bid": "방송인", "nick": "닉네임", "is_live": False}


@pytest.mark.asyncio
async def test_list_reviews(client, db):
    """성공 케이스. limit 과 cursor 를 받아 다시보기 목록을 이어서 주는지."""
    db.add(Streamer(bid="방송인", nick="닉네임"))
    await db.commit()
    db.add_all([_vod(101), _vod(102), _vod(103)])
    await db.commit()

    response = await client.get(
        "/station/방송인/reviews", params={"limit": 1, "cursor": 103}
    )

    assert response.status_code == 200
    body = response.json()
    assert [item["vod_id"] for item in body["items"]] == [102]
    assert body["next_cursor"] == 102
