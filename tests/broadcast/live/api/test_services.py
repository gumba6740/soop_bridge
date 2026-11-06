"""
pytest tests/broadcast/live/api/test_services.py
"""

import asyncio
import json
from datetime import datetime

import pytest

from app.broadcast.live.api import services
from app.broadcast.live.api.exceptions import LiveNotFound
from app.broadcast.live.api.schemas import LiveDetail, LiveItem
from app.core import hub
from app.core.config import settings
from app.models import Streamer
from app.station.exceptions import StreamerNotFound
from app.utils.tz import KST

BID = "방송인"

SUMMARY = {
    "nick": "닉네임",
    "title": "방송 제목",
    "viewers": "1234",
    "tags": json.dumps(["게임", "토크"]),
    "started_at": "2026-09-04T19:00:00+09:00",
    "viewers_at": "2026-09-04T19:05:00+09:00",
}


@pytest.fixture
def fast(monkeypatch):
    """채팅이 없을 때 기다리는 시간을 줄인다."""
    monkeypatch.setattr(services, "HEARTBEAT_SEC", 0.1)


async def _add_streamer(db) -> None:
    db.add(Streamer(bid=BID, nick="닉네임"))
    await db.commit()


async def _go_live(r, summary: dict[str, str] | None = None) -> None:
    await r.hset(settings.summary_key(BID), mapping=summary or SUMMARY)


async def _add_chat(r, message: str) -> str:
    """수집기가 하듯 채팅 한 건을 스트림에 넣고 id 를 돌려준다."""
    data = {"message": message, "user_id": "시청자", "user_nick": "닉네임"}
    fields = {"type": "chat", "data": json.dumps(data)}
    return str(await r.xadd(settings.chats_key(BID), fields))


async def _next(gen, timeout: float = 1) -> str:
    """SSE 본문에서 다음 이벤트 하나. 오래 안 오면 실패시킨다."""
    return await asyncio.wait_for(anext(gen), timeout)


def _message(event: str) -> str:
    """chat 이벤트 문자열에서 메시지만 꺼낸다."""
    data_line = next(line for line in event.splitlines() if line.startswith("data: "))
    return json.loads(data_line.removeprefix("data: "))["data"]["message"]


# ---------------------------------------------------------------- _to_item


def test_to_item_full():
    """SUMMARY 를 빠짐없이 바르게 옮기는지."""
    assert services._to_item(BID, SUMMARY) == LiveItem(
        bid=BID,
        nick="닉네임",
        title="방송 제목",
        viewers=1234,
        tags=["게임", "토크"],
        started_at=datetime(2026, 9, 4, 19, 0, tzinfo=KST),
    )


def test_to_item_before_detail_loop():
    """값이 빈 문자열이어도 기본값으로 바꾸는지."""
    item = services._to_item(
        BID,
        {
            "nick": "닉네임",
            "title": "방송 제목",
            "viewers": "",
            "tags": "",
            "started_at": "",
        },
    )

    assert item.viewers is None
    assert item.tags == []
    assert item.started_at is None


def test_to_item_empty_summary():
    """키가 아예 없어도 기본값으로 채우는지."""
    item = services._to_item(BID, {})

    assert (item.nick, item.title, item.viewers, item.tags) == ("", "", None, [])


# ---------------------------------------------------------------- get_live


@pytest.mark.asyncio
async def test_get_live(db, r):
    """방송 중일 때 라이브 관련 정보를 빠짐없이 바르게 주는지."""
    await _add_streamer(db)
    await _go_live(r)

    live = await services.get_live(db, r, BID)

    assert live == LiveDetail(
        **services._to_item(BID, SUMMARY).model_dump(),
        viewers_at=datetime(2026, 9, 4, 19, 5, tzinfo=KST),
        embed_url=f"https://play.sooplive.com/{BID}/embed",
    )


@pytest.mark.asyncio
async def test_get_live_without_viewers_at(db, r):
    """viewers_at 키가 없으면 None 으로 주는지."""
    await _add_streamer(db)
    await _go_live(r, {"nick": "닉네임", "title": "방송 제목"})

    assert (await services.get_live(db, r, BID)).viewers_at is None


@pytest.mark.asyncio
async def test_get_live_not_live_raises(db, r):
    """스트리머가 방송 중이 아니면 LiveNotFound 를 내는지."""
    await _add_streamer(db)

    with pytest.raises(LiveNotFound):
        await services.get_live(db, r, BID)


@pytest.mark.asyncio
async def test_get_live_unknown_streamer_raises(db, r):
    """방송 중이어도 등록 안 된 에러를 내는지."""
    await _go_live(r)

    with pytest.raises(StreamerNotFound):
        await services.get_live(db, r, BID)


# ------------------------------------------------------- check_live


@pytest.mark.asyncio
async def test_check_live_passes_when_live(db, r):
    """방송 중이면 check_live()가 에러 없이 통과하는지."""
    await _add_streamer(db)
    await _go_live(r)

    await services.check_live(db, r, BID)


@pytest.mark.asyncio
async def test_check_live_not_live_raises(db, r):
    """방송 중이 아니면 check_live()가 에러를 내는지."""
    await _add_streamer(db)

    with pytest.raises(LiveNotFound):
        await services.check_live(db, r, BID)


@pytest.mark.asyncio
async def test_check_live_unknown_streamer_raises(db, r):
    """방송 중이어도 등록 안 된 스트리머면 에러를 내는지."""
    await _go_live(r)

    with pytest.raises(StreamerNotFound):
        await services.check_live(db, r, BID)


# ---------------------------------------------------------------- _id_key, _sse


def test_id_key():
    assert services._id_key("1760540000000-3") == (1760540000000, 3)


def test_id_key_compares_as_numbers():
    """id 를 문자열이 아니라 숫자로 비교하는지."""
    assert services._id_key("9-0") < services._id_key("10-0")
    assert services._id_key("10-2") < services._id_key("10-10")


def test_sse_format():
    """채팅을 SSE 형식으로 바꾸면서 유저아이디는 빼고 한글은 그대로 두는지."""
    data = {"message": "안녕하세요", "user_id": "시청자", "user_nick": "닉네임"}

    event = services._sse("1-0", {"type": "chat", "data": json.dumps(data)})

    expected = {
        "type": "chat",
        "data": {"message": "안녕하세요", "user_nick": "닉네임"},
    }
    assert event == (
        f"id: 1-0\nevent: chat\ndata: {json.dumps(expected, ensure_ascii=False)}\n\n"
    )


# ----------------------------------------------------------- chat_events


@pytest.mark.asyncio
async def test_chat_events_sends_recent_history_then_new_chats(
    r, chat_hub, monkeypatch
):
    """처음엔 최근 채팅을 오래된 것부터 보내고, 그 뒤로 새 채팅을 이어 보내는지."""
    monkeypatch.setattr(services, "INITIAL_CHATS", 2)
    for message in ["하나", "둘", "셋"]:
        await _add_chat(r, message)
    gen = services.chat_events(r, chat_hub, BID)

    # 최근 2개를 오래된 것부터
    assert _message(await _next(gen)) == "둘"
    assert _message(await _next(gen)) == "셋"

    await _add_chat(r, "넷")
    assert _message(await _next(gen)) == "넷"
    await gen.aclose()


@pytest.mark.asyncio
async def test_chat_events_skips_chat_already_sent_in_history(r, chat_hub, monkeypatch):
    """같은 채팅을 중복해서 보내지 않는지."""
    real_xrevrange = r.xrevrange

    async def xrevrange_after_new_chat(key, *args, count=None, **kwargs):
        if count == services.INITIAL_CHATS:  # chat_events 의 지난 채팅 읽기
            await _add_chat(r, "사이에 온 채팅")
        return await real_xrevrange(key, *args, count=count, **kwargs)

    monkeypatch.setattr(r, "xrevrange", xrevrange_after_new_chat)
    gen = services.chat_events(r, chat_hub, BID)

    assert _message(await _next(gen)) == "사이에 온 채팅"  # 지난 채팅으로 한 번
    await asyncio.sleep(0.2)  # 큐에도 같은 채팅이 들어올 시간
    await _add_chat(r, "다음 채팅")
    assert _message(await _next(gen)) == "다음 채팅"  # 큐의 중복은 건너뛰었다
    await gen.aclose()


@pytest.mark.asyncio
async def test_chat_events_pings_while_live(r, chat_hub, fast):
    """방송 중에 채팅이 없으면 ping 을 계속 보내는지."""
    await _go_live(r)
    gen = services.chat_events(r, chat_hub, BID)

    assert await _next(gen) == ": ping\n\n"
    assert await _next(gen) == ": ping\n\n"
    await gen.aclose()


@pytest.mark.asyncio
async def test_chat_events_ends_when_broadcast_ends(r, chat_hub, fast):
    """방송이 끝나면 end 를 보내고 연결과 구독을 닫는지."""
    gen = services.chat_events(r, chat_hub, BID)

    assert await _next(gen) == "event: end\ndata: {}\n\n"
    with pytest.raises(StopAsyncIteration):
        await _next(gen)
    assert BID not in chat_hub._subs
    assert BID not in chat_hub._readers


@pytest.mark.asyncio
async def test_chat_events_ends_when_hub_drops_slow_connection(
    r, chat_hub, monkeypatch
):
    """받아 가지 못할 만큼 느리면 hub 가 큐를 닫고, SSE 도 끝내고 구독을 푸는지."""
    monkeypatch.setattr(hub, "QUEUE_MAXSIZE", 2)
    await _add_chat(r, "하나")
    gen = services.chat_events(r, chat_hub, BID)
    await _next(gen)  # 지난 채팅 하나를 받고, 다음을 달라고 하지 않는다(느린 연결)

    for message in ["둘", "셋", "넷"]:  # 큐 크기(2)보다 많이 쌓이게 한다
        await _add_chat(r, message)
    await asyncio.sleep(0.2)  # hub 가 큐에 넣다가 가득 차서 닫을 시간

    with pytest.raises(StopAsyncIteration):
        await _next(gen)
    assert BID not in chat_hub._subs
    assert BID not in chat_hub._readers


@pytest.mark.asyncio
async def test_chat_events_unsubscribes_when_client_leaves(r, chat_hub):
    """시청자가 연결을 끊으면 구독을 푸는지."""
    await _add_chat(r, "하나")
    gen = services.chat_events(r, chat_hub, BID)
    await _next(gen)

    await gen.aclose()  # 브라우저가 연결을 끊으면 StreamingResponse 가 닫는다

    assert BID not in chat_hub._subs
    assert BID not in chat_hub._readers
