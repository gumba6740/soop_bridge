"""
pytest tests/broadcast/live/collector/test___main__.py
"""

import pytest

from app.broadcast.live.collector import __main__ as collector_main
from app.models import Streamer
from tests.conftest import test_settings


@pytest.fixture
def use_test_db(monkeypatch):
    """_all_bids 가 테스트 DB 에 접속하게 한다. 이 모듈이 쓰는 settings 만 바꾼다."""
    monkeypatch.setattr(collector_main, "settings", test_settings)


@pytest.mark.asyncio
async def test_all_bids(db, use_test_db):
    """성공 케이스. streamers 의 bid 를 빠짐없이 주는지."""
    db.add_all(
        [
            Streamer(bid="방송인1", nick="가나다"),
            Streamer(bid="방송인2", nick="나비"),
            Streamer(bid="방송인3", nick="다람쥐"),
        ]
    )
    await db.commit()

    # ORDER BY 가 없어서 순서는 정해져 있지 않다
    assert sorted(await collector_main._all_bids()) == ["방송인1", "방송인2", "방송인3"]


@pytest.mark.asyncio
async def test_all_bids_empty(db, use_test_db):
    """스트리머가 없으면 빈 목록인지."""
    assert await collector_main._all_bids() == []
