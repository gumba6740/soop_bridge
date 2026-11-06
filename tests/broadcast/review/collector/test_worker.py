"""
pytest tests/broadcast/review/collector/test_worker.py
"""

import asyncio
import logging

import pytest

from app.broadcast.review.collector import worker
from tests.conftest import TEST_DB, test_settings
from tests.helpers import TIMEOUT, _running, _until

QUEUE = test_settings.chat_job_queue_key
PENDING = test_settings.chat_job_pending_key
# 워커가 끝나지 않을 때 테스트가 멈추지 않고 실패하게 하는 시간(초)


# ---------------------------------------------------------------- fixture


@pytest.fixture
def handled(monkeypatch) -> list[int]:
    """채팅 수집을 가짜로 바꾸고, 받은 vod_id 를 순서대로 모은다."""
    calls: list[int] = []

    async def fake_collect(conn, vod_id: int) -> int:
        calls.append(vod_id)
        return 3

    monkeypatch.setattr(worker, "collect_vod_chats", fake_collect)
    return calls


# ---------------------------------------------------------------- 도우미


async def _add_jobs(r, *vod_ids: str) -> None:
    """API 처럼 pending 에 넣고 큐 뒤에 붙인다."""
    for vod_id in vod_ids:
        await r.sadd(PENDING, vod_id)
        await r.rpush(QUEUE, vod_id)


# ---------------------------------------------------------------- _handle


@pytest.mark.asyncio
async def test_handle(monkeypatch, caplog):
    """성공 케이스. vod_id 를 숫자로 바꿔 테스트 DB 연결과 함께 채팅 수집에 넘기는지."""
    seen = []

    async def fake_collect(conn, vod_id: int) -> int:
        seen.append((conn.info.dbname, vod_id))
        return 3

    monkeypatch.setattr(worker, "collect_vod_chats", fake_collect)

    with caplog.at_level(logging.INFO):
        await worker._handle("1000")

    assert seen == [(TEST_DB, 1000)]
    assert "일감 완료: vod_id=1000 (3건)" in caplog.text


@pytest.mark.asyncio
async def test_handle_bad_id(handled, caplog):
    """숫자가 아닌 vod_id 는 에러 로그만 남기고 수집하지 않는지."""
    await worker._handle("다시보기")

    assert handled == []
    assert "잘못된 vod_id" in caplog.text


# ---------------------------------------------------------------- _recover


@pytest.mark.asyncio
async def test_recover(r, caplog):
    """성공 케이스. pending 에만 있는 일감을 큐 맨 앞에 되돌리는지."""
    await r.sadd(PENDING, "1001", "1002", "1003")
    await r.rpush(QUEUE, "1003")  # 1003 은 아직 큐에서 기다리는 중

    await worker._recover(r, QUEUE, PENDING)

    queue = await r.lrange(QUEUE, 0, -1)
    # 되돌린 둘은 set 에서 꺼내서 순서가 정해져 있지 않다
    assert sorted(queue[:2]) == ["1001", "1002"]
    assert queue[2:] == ["1003"]
    assert "끝내지 못한 일감 2건" in caplog.text


@pytest.mark.asyncio
async def test_recover_nothing_to_recover(r, caplog):
    """pending 이 모두 큐에 있으면 큐를 그대로 두고 경고도 없는지."""
    await _add_jobs(r, "1001", "1002")

    await worker._recover(r, QUEUE, PENDING)

    assert await r.lrange(QUEUE, 0, -1) == ["1001", "1002"]
    assert "끝내지 못한 일감" not in caplog.text


# ---------------------------------------------------------------- run_worker


@pytest.mark.asyncio
async def test_run_worker(r, handled):
    """성공 케이스. 큐에 들어온 순서대로 하나씩 수집하고, 끝나면 pending 에서 빼는지."""
    await _add_jobs(r, "1001", "1002")

    async with _running(worker.run_worker()):

        async def done() -> bool:
            return len(handled) == 2 and await r.scard(PENDING) == 0

        await _until(done)

    assert handled == [1001, 1002]
    assert await r.llen(QUEUE) == 0


@pytest.mark.asyncio
async def test_run_worker_keeps_going_after_failure(r, monkeypatch, caplog):
    """일감 하나가 실패해도 워커는 다음 일감을 하고, 실패한 것도 pending 에서 빼는지."""
    handled = []

    async def fake_collect(conn, vod_id: int) -> int:
        handled.append(vod_id)
        if vod_id == 1001:
            raise RuntimeError("수집 실패")
        return 3

    monkeypatch.setattr(worker, "collect_vod_chats", fake_collect)
    await _add_jobs(r, "1001", "1002")

    async with _running(worker.run_worker()):

        async def done() -> bool:
            return len(handled) == 2 and await r.scard(PENDING) == 0

        await _until(done)

    assert handled == [1001, 1002]
    assert "일감 실패: 1001" in caplog.text


@pytest.mark.asyncio
async def test_run_worker_cancel_puts_job_back(r, monkeypatch):
    """수집 도중 종료되면 그 일감을 큐 맨 앞에 되돌리고 pending 에 남기는지."""
    started = asyncio.Event()
    release = asyncio.Event()

    async def slow_collect(conn, vod_id: int) -> int:
        started.set()
        await release.wait()  # 풀어 주기 전까지 끝나지 않는 수집
        return 0

    monkeypatch.setattr(worker, "collect_vod_chats", slow_collect)
    await _add_jobs(r, "1001", "1002")

    task = asyncio.create_task(worker.run_worker())
    try:
        await asyncio.wait_for(started.wait(), TIMEOUT)
        task.cancel()
        await asyncio.wait({task}, timeout=TIMEOUT)

        assert task.cancelled()  # 종료 신호를 삼키지 않고 멈춘다
        assert await r.lrange(QUEUE, 0, -1) == ["1001", "1002"]
        assert await r.smembers(PENDING) == {"1001", "1002"}
    finally:
        release.set()  # 워커가 멈추지 않았을 때 테스트가 끝나게 풀어 준다


@pytest.mark.asyncio
async def test_run_worker_recovers_on_start(r, handled):
    """시작할 때 끝내지 못한 일감부터 다시 하는지."""
    await r.sadd(PENDING, "1001")  # 처리 도중 워커가 죽어서 pending 에만 남음

    async with _running(worker.run_worker()):

        async def done() -> bool:
            return handled == [1001] and await r.scard(PENDING) == 0

        await _until(done)


@pytest.mark.asyncio
async def test_run_worker_waits_for_new_jobs(r, handled, monkeypatch):
    """기다리던 시간 동안 일감이 없어도 멈추지 않고, 나중에 들어온 일감을 하는지."""
    monkeypatch.setattr(worker, "BLPOP_TIMEOUT", 0.1)

    async with _running(worker.run_worker()):
        await asyncio.sleep(0.5)  # 빈손으로 몇 번 돌아오게 둔다
        await _add_jobs(r, "1001")

        async def done() -> bool:
            return handled == [1001]

        await _until(done)
