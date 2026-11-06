"""
pytest tests/broadcast/live/api/test_routers.py
"""

import asyncio
import json

import pytest

from app.broadcast.live.api import services
from app.core.config import settings
from app.models import Streamer

BID = "방송인"


@pytest.mark.asyncio
async def test_get_live(client, db, r):
    """성공 케이스. 방송 중이면 방송 현황을 주는지."""
    db.add(Streamer(bid=BID, nick="닉네임"))
    await db.commit()
    await r.hset(
        settings.summary_key(BID),
        mapping={"nick": "닉네임", "title": "방송 제목", "viewers": "1234"},
    )

    response = await client.get(f"/live/{BID}")

    assert response.status_code == 200
    body = response.json()
    assert (body["bid"], body["title"], body["viewers"]) == (BID, "방송 제목", 1234)
    assert body["embed_url"] == f"https://play.sooplive.com/{BID}/embed"


@pytest.mark.asyncio
async def test_stream_chats(client, db, r, monkeypatch):
    """성공 케이스. 채팅을 SSE 로 보내고, 방송이 끝나면 end 를 보내고 닫는지."""
    monkeypatch.setattr(services, "HEARTBEAT_SEC", 0.1)
    db.add(Streamer(bid=BID, nick="닉네임"))
    await db.commit()
    data = {"message": "안녕하세요", "user_id": "시청자", "user_nick": "닉네임"}
    await r.xadd(settings.chats_key(BID), {"type": "chat", "data": json.dumps(data)})
    # 방송 중으로 시작했다가 0.3초 뒤 끝나게 한다.
    # SSE 는 끝나지 않으면 응답이 계속 이어져서, 테스트가 응답을 다 받을 수 있게 끝을 만든다.
    await r.hset(settings.summary_key(BID), mapping={"title": "방송 제목"})
    await r.pexpire(settings.summary_key(BID), 300)

    response = await asyncio.wait_for(client.get(f"/live/{BID}/chats"), 3)

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["cache-control"] == "no-cache"
    assert response.headers["x-accel-buffering"] == "no"
    events = response.text.split("\n\n")
    assert events[0].startswith("id: ") and "event: chat" in events[0]
    assert '"message": "안녕하세요"' in events[0]
    assert "시청자" not in events[0]  # 유저 아이디는 빠진다
    assert response.text.endswith("event: end\ndata: {}\n\n")


@pytest.mark.asyncio
async def test_stream_chats_not_live_returns_404(client, db):
    """방송 중이 아니면 SSE 를 열기 전에 404 를 주는지."""
    db.add(Streamer(bid=BID, nick="닉네임"))
    await db.commit()

    response = await asyncio.wait_for(client.get(f"/live/{BID}/chats"), 3)

    assert response.status_code == 404
    assert response.json()["code"] == "LIVE_NOT_FOUND"
