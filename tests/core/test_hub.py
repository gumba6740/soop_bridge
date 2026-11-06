"""
pytest tests/core/test_hub.py
"""

import asyncio
import contextlib

import pytest
import redis.asyncio as redis

from app.core import hub
from app.core.config import settings

BID = "테스트방송인"
OTHER_BID = "다른방송인"


@pytest.fixture
def fast(monkeypatch):
    """기다리는 시간을 줄여서 테스트가 빨리 끝나게 한다."""
    monkeypatch.setattr(hub, "XREAD_BLOCK_MS", 50)
    monkeypatch.setattr(hub, "RETRY_DELAY", 0)


async def _add(r, bid: str, text: str) -> str:
    """스트림에 채팅 하나를 넣고 그 id를 돌려준다."""
    return str(await r.xadd(settings.chats_key(bid), {"text": text}))


async def _get(q, timeout: float = 1):
    """큐에서 하나 꺼낸다. 오래 안 오면 테스트를 실패시킨다(멈춰 있지 않게)."""
    return await asyncio.wait_for(q.get(), timeout)


async def _assert_nothing_more(q, wait: float = 0.2) -> None:
    """잠깐 기다려도 큐에 더 들어온 것이 없는지 본다."""
    await asyncio.sleep(wait)
    assert q.empty()


async def _wait_stopped(task) -> None:
    """취소된 태스크가 실제로 끝날 때까지 기다린다."""
    with contextlib.suppress(asyncio.CancelledError):
        await asyncio.wait_for(task, 1)


# ---------------------------------------------------------------- _latest_id


@pytest.mark.asyncio
async def test_latest_id_without_stream_is_zero(chat_hub):
    assert await chat_hub._latest_id(BID) == "0-0"


@pytest.mark.asyncio
async def test_latest_id_returns_last_entry_id(r, chat_hub):
    await _add(r, BID, "첫 채팅")
    last = await _add(r, BID, "마지막 채팅")

    assert await chat_hub._latest_id(BID) == last


# ---------------------------------------------------------------- subscribe


@pytest.mark.asyncio
async def test_subscriber_gets_only_chats_after_subscribe(r, chat_hub):
    await _add(r, BID, "구독 전 채팅")
    q = await chat_hub.subscribe(BID)
    new_id = await _add(r, BID, "구독 후 채팅")

    assert await _get(q) == (new_id, {"text": "구독 후 채팅"})
    await _assert_nothing_more(q)


@pytest.mark.asyncio
async def test_subscribers_share_one_reader(r, chat_hub):
    q1 = await chat_hub.subscribe(BID)
    q2 = await chat_hub.subscribe(BID)
    entry_id = await _add(r, BID, "안녕")

    assert len(chat_hub._readers) == 1
    assert await _get(q1) == (entry_id, {"text": "안녕"})
    assert await _get(q2) == (entry_id, {"text": "안녕"})


@pytest.mark.asyncio
async def test_concurrent_subscribe_starts_one_reader(r, chat_hub):
    """두 연결이 동시에 들어와도(_latest_id 를 기다리는 사이) 루프는 하나만 뜬다."""
    q1, q2 = await asyncio.gather(chat_hub.subscribe(BID), chat_hub.subscribe(BID))
    entry_id = await _add(r, BID, "동시 입장")

    assert len(chat_hub._readers) == 1
    assert chat_hub._subs[BID] == {q1, q2}
    assert await _get(q1) == (entry_id, {"text": "동시 입장"})
    assert await _get(q2) == (entry_id, {"text": "동시 입장"})


@pytest.mark.asyncio
async def test_bids_are_kept_separate(r, chat_hub):
    q = await chat_hub.subscribe(BID)
    other_q = await chat_hub.subscribe(OTHER_BID)
    entry_id = await _add(r, BID, "내 방송 채팅")
    other_id = await _add(r, OTHER_BID, "다른 방송 채팅")

    assert await _get(q) == (entry_id, {"text": "내 방송 채팅"})
    await _assert_nothing_more(q)
    assert await _get(other_q) == (other_id, {"text": "다른 방송 채팅"})


# ---------------------------------------------------------------- unsubscribe


@pytest.mark.asyncio
async def test_unsubscribe_keeps_reader_while_others_watch(r, chat_hub):
    q1 = await chat_hub.subscribe(BID)
    q2 = await chat_hub.subscribe(BID)
    chat_hub.unsubscribe(BID, q1)
    entry_id = await _add(r, BID, "남은 사람에게")

    assert BID in chat_hub._readers
    assert await _get(q2) == (entry_id, {"text": "남은 사람에게"})
    assert q1.empty()


@pytest.mark.asyncio
async def test_unsubscribe_last_stops_reader(chat_hub):
    q = await chat_hub.subscribe(BID)
    task = chat_hub._readers[BID]

    chat_hub.unsubscribe(BID, q)
    await _wait_stopped(task)

    assert task.cancelled()
    assert BID not in chat_hub._readers
    assert BID not in chat_hub._subs


@pytest.mark.asyncio
async def test_unsubscribe_unknown_bid_is_ignored(chat_hub):
    chat_hub.unsubscribe("없는방송인", asyncio.Queue())  # 에러 없이 지나가야 한다

    assert chat_hub._readers == {}


@pytest.mark.asyncio
async def test_resubscribe_after_stop_starts_new_reader(r, chat_hub):
    q = await chat_hub.subscribe(BID)
    old_task = chat_hub._readers[BID]
    chat_hub.unsubscribe(BID, q)
    await _wait_stopped(old_task)

    q = await chat_hub.subscribe(BID)
    entry_id = await _add(r, BID, "다시 입장")

    assert chat_hub._readers[BID] is not old_task
    assert await _get(q) == (entry_id, {"text": "다시 입장"})


# ---------------------------------------------------------------- _read_loop


@pytest.mark.asyncio
async def test_reader_keeps_waiting_after_empty_response(r, chat_hub, fast):
    """BLOCK 시간 안에 채팅이 없어서 빈 응답을 받아도 루프는 계속 기다린다."""
    q = await chat_hub.subscribe(BID)
    await asyncio.sleep(0.2)  # 50ms 짜리 빈 응답을 여러 번 받는다
    entry_id = await _add(r, BID, "늦게 온 채팅")

    assert await _get(q) == (entry_id, {"text": "늦게 온 채팅"})


@pytest.mark.asyncio
async def test_reader_survives_xread_error(r, chat_hub, fast, monkeypatch, caplog):
    """redis 읽기가 한 번 실패해도 루프는 살아서 다시 읽는다."""
    real_xread = r.xread
    calls = 0

    async def flaky_xread(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise redis.ConnectionError("연결 끊김")
        return await real_xread(*args, **kwargs)

    monkeypatch.setattr(r, "xread", flaky_xread)

    q = await chat_hub.subscribe(BID)
    entry_id = await _add(r, BID, "실패 뒤 채팅")

    assert await _get(q) == (entry_id, {"text": "실패 뒤 채팅"})
    assert calls >= 2
    assert "ConnectionError" in caplog.text


@pytest.mark.asyncio
async def test_slow_subscriber_is_dropped(r, chat_hub, monkeypatch):
    """큐가 가득 찬 연결은 큐를 닫고 빼낸다. 다른 연결은 계속 받는다."""
    monkeypatch.setattr(hub, "QUEUE_MAXSIZE", 2)
    slow = await chat_hub.subscribe(BID)  # 하나도 안 꺼내는 연결
    quick = await chat_hub.subscribe(BID)  # 바로바로 꺼내는 연결

    for text in ["하나", "둘"]:
        await _add(r, BID, text)
        assert (await _get(quick))[1] == {"text": text}
    third_id = await _add(r, BID, "셋")  # 이때 slow 는 이미 2개가 차 있다

    assert await _get(quick) == (third_id, {"text": "셋"})
    assert slow not in chat_hub._subs[BID]
    with pytest.raises(asyncio.QueueShutDown):
        slow.get_nowait()
    assert not chat_hub._readers[BID].done()


# ---------------------------------------------------------------- aclose


@pytest.mark.asyncio
async def test_aclose_stops_all_readers(chat_hub):
    await chat_hub.subscribe(BID)
    await chat_hub.subscribe(OTHER_BID)
    tasks = list(chat_hub._readers.values())

    await asyncio.wait_for(
        chat_hub.aclose(), 1
    )  # 루프를 안 멈추면 여기서 시간 초과로 실패

    assert all(task.done() for task in tasks)
    assert chat_hub._readers == {}
    assert chat_hub._subs == {}
