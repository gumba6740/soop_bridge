"""
pytest tests/broadcast/review/collector/test___main__.py
"""

import logging
import sys

import pytest

from app.broadcast.review.collector import __main__ as collector_main
from tests.conftest import TEST_DB

VODS = collector_main.VODS_LOCK_KEY
CHATS = collector_main.CHATS_LOCK_KEY


# ---------------------------------------------------------------- fixture


@pytest.fixture
def targets() -> list[int]:
    """가짜 next_chat_targets 가 돌려줄 vod_id. 테스트에서 채운다."""
    return []


@pytest.fixture
def calls(monkeypatch, targets) -> list[tuple]:
    """수집 함수들을 가짜로 바꾸고, 불린 순서대로 (이름, 인자) 를 모은다."""
    record: list[tuple] = []

    async def collect_new_vods(conn, bid):
        record.append(("new", bid))
        return 1

    async def sweep_missing_vods(conn, bid):
        record.append(("sweep", bid))
        return 1

    async def retry_failures(conn):
        record.append(("retry",))
        return 1

    async def next_chat_targets(conn, limit):
        record.append(("targets", limit))
        return targets

    async def collect_vod_chats(conn, vod_id):
        record.append(("chats", vod_id))
        return 1

    async def run_worker():
        record.append(("worker",))

    async def aclose():
        record.append(("aclose",))

    for fake in (
        collect_new_vods,
        sweep_missing_vods,
        retry_failures,
        next_chat_targets,
        collect_vod_chats,
        run_worker,
        aclose,
    ):
        monkeypatch.setattr(collector_main, fake.__name__, fake)
    return record


# ---------------------------------------------------------------- 도우미


async def _add_streamer(conn, bid: str, nick: str) -> None:
    await conn.execute("INSERT INTO streamers (bid, nick) VALUES (%s, %s)", (bid, nick))


async def _try_lock(conn, key: int) -> bool:
    cur = await conn.execute("SELECT pg_try_advisory_lock(%s)", (key,))
    return (await cur.fetchone())[0]


async def _main(monkeypatch, *argv: str) -> None:
    """명령줄에 argv 를 넣은 것처럼 main 을 실행한다."""
    monkeypatch.setattr(sys, "argv", ["collector", *argv])
    await collector_main.main()


# ---------------------------------------------------------------- global_lock


@pytest.mark.asyncio
async def test_global_lock(conn, other):
    """성공 케이스. 락을 잡으면 True 를 주고, 그동안 남은 못 잡으며, 끝나면 푸는지."""
    async with collector_main.global_lock(conn, VODS) as ok:
        assert ok is True
        assert await _try_lock(other, VODS) is False

    assert await _try_lock(other, VODS) is True


@pytest.mark.asyncio
async def test_global_lock_busy(conn, other):
    """다른 연결이 잡고 있으면 False 를 주는지."""
    await _try_lock(other, VODS)

    async with collector_main.global_lock(conn, VODS) as ok:
        assert ok is False


@pytest.mark.asyncio
async def test_global_lock_is_per_key(conn, other):
    """vods 락과 chats 락은 따로 잡히는지."""
    await _try_lock(other, VODS)

    async with collector_main.global_lock(conn, CHATS) as ok:
        assert ok is True


@pytest.mark.asyncio
async def test_global_lock_released_on_error(conn, other):
    """안에서 에러가 나도 락을 푸는지."""
    with pytest.raises(RuntimeError):
        async with collector_main.global_lock(conn, VODS):
            raise RuntimeError("수집 중 에러")

    assert await _try_lock(other, VODS) is True


# ---------------------------------------------------------------- _targets


@pytest.mark.asyncio
async def test_targets(conn):
    """성공 케이스. bid 를 안 주면 streamers 전체를 (닉네임, bid) 로 주는지."""
    await _add_streamer(conn, "b1", "가나다")
    await _add_streamer(conn, "b2", "다람쥐")

    # ORDER BY 가 없어서 순서는 정해져 있지 않다
    assert sorted(await collector_main._targets(conn, None)) == [
        ("가나다", "b1"),
        ("다람쥐", "b2"),
    ]


@pytest.mark.asyncio
async def test_targets_one_bid(conn, caplog):
    """bid 를 주면 그 스트리머 하나만, 닉네임과 함께 주는지."""
    await _add_streamer(conn, "b1", "가나다")
    await _add_streamer(conn, "b2", "다람쥐")

    assert await collector_main._targets(conn, "b2") == [("다람쥐", "b2")]
    assert "streamers에 없는 bid" not in caplog.text


@pytest.mark.asyncio
async def test_targets_unknown_bid(conn, caplog):
    """streamers 에 없는 bid 면 경고를 남기고, 닉네임 자리에 bid 를 주는지."""
    assert await collector_main._targets(conn, "없는방송인") == [
        ("없는방송인", "없는방송인")
    ]
    assert "streamers에 없는 bid: 없는방송인" in caplog.text


# ---------------------------------------------------------------- run_vods


@pytest.mark.asyncio
async def test_run_vods(conn, calls):
    """성공 케이스. 스트리머마다 새 VOD 를 수집하는지."""
    await _add_streamer(conn, "b1", "가나다")
    await _add_streamer(conn, "b2", "다람쥐")

    await collector_main.run_vods(conn, bid=None, retry=False, sweep=False)

    assert sorted(calls) == [("new", "b1"), ("new", "b2")]


@pytest.mark.asyncio
async def test_run_vods_one_bid(conn, calls):
    """bid 를 주면 그 스트리머만 수집하는지."""
    await _add_streamer(conn, "b1", "가나다")
    await _add_streamer(conn, "b2", "다람쥐")

    await collector_main.run_vods(conn, bid="b2", retry=False, sweep=False)

    assert calls == [("new", "b2")]


@pytest.mark.asyncio
async def test_run_vods_sweep(conn, calls):
    """sweep 이면 sweep_missing_vods가 실행되는지"""
    await collector_main.run_vods(conn, bid="b1", retry=False, sweep=True)

    assert calls == [("sweep", "b1")]


@pytest.mark.asyncio
async def test_run_vods_retry(conn, calls):
    """retry 면 retry_failures가 실행되는지"""
    await _add_streamer(conn, "b1", "가나다")

    await collector_main.run_vods(conn, bid=None, retry=True, sweep=False)

    assert calls == [("retry",)]


@pytest.mark.asyncio
async def test_run_vods_keeps_going_after_failure(conn, calls, monkeypatch, caplog):
    """한 스트리머가 실패해도 다음 스트리머를 수집하는지."""
    await _add_streamer(conn, "b1", "가나다")
    await _add_streamer(conn, "b2", "다람쥐")
    tried = []

    async def collect_new_vods(conn, bid):
        tried.append(bid)
        if bid == "b1":
            raise RuntimeError("수집 실패")
        return 1

    monkeypatch.setattr(collector_main, "collect_new_vods", collect_new_vods)

    await collector_main.run_vods(conn, bid=None, retry=False, sweep=False)

    assert sorted(tried) == ["b1", "b2"]
    assert "가나다 (b1) 실패. 다음으로" in caplog.text


@pytest.mark.asyncio
async def test_run_vods_skips_when_locked(conn, other, calls, caplog):
    """이전 실행이 vods 락을 잡고 있으면 아무것도 하지 않는지."""
    await _add_streamer(conn, "b1", "가나다")
    await _try_lock(other, VODS)

    await collector_main.run_vods(conn, bid=None, retry=True, sweep=False)

    assert calls == []
    assert "vods: 이전 실행이 아직 진행 중" in caplog.text


# ---------------------------------------------------------------- run_chats


@pytest.mark.asyncio
async def test_run_chats(conn, calls, targets):
    """성공 케이스. count 개까지 대상을 골라 차례로 수집하는지."""
    targets.extend([1001, 1002])

    await collector_main.run_chats(conn, count=3, vod_id=None)

    assert calls == [("targets", 3), ("chats", 1001), ("chats", 1002)]


@pytest.mark.asyncio
async def test_run_chats_nothing_to_collect(conn, calls, caplog):
    """대상이 없으면 수집 없이 끝나고 그렇다고 남기는지."""
    with caplog.at_level(logging.INFO):
        await collector_main.run_chats(conn, count=3, vod_id=None)

    assert calls == [("targets", 3)]
    assert "수집할 VOD 없음" in caplog.text


@pytest.mark.asyncio
async def test_run_chats_skips_when_locked(conn, other, calls, targets, caplog):
    """크론 모드에서 이전 실행이 chats 락을 잡고 있으면 아무것도 하지 않는지."""
    targets.append(1001)
    await _try_lock(other, CHATS)

    await collector_main.run_chats(conn, count=3, vod_id=None)

    assert calls == []
    assert "chats: 이전 실행이 아직 진행 중" in caplog.text


@pytest.mark.asyncio
async def test_run_chats_one_vod_ignores_lock(conn, other, calls):
    """vod_id 를 주면 전역 락과 상관없이 그 VOD를 수집하는지."""
    await _try_lock(other, CHATS)

    await collector_main.run_chats(conn, count=None, vod_id=1000)

    assert calls == [("chats", 1000)]


# ---------------------------------------------------------------- main


@pytest.mark.parametrize(
    "argv",
    [
        ["vods", "--retry", "--bid", "b1"],  # retry 는 실패 테이블 전체가 대상
        ["vods", "--sweep"],  # sweep 은 bid 가 있어야 함
        ["vods", "--retry", "--sweep"],  # 둘 중 하나만
        ["chats"],  # --count, --vod-id 중 하나는 있어야 함
        ["chats", "--count", "3", "--vod-id", "1000"],  # 둘 중 하나만
        ["chats", "--count", "0"],  # 1 이상
        [],  # 명령이 없음
    ],
)
@pytest.mark.asyncio
async def test_main_rejects_bad_args(monkeypatch, calls, argv):
    """잘못된 옵션 조합이면 아무것도 하지 않고 에러로 끝나는지."""
    with pytest.raises(SystemExit) as exc:
        await _main(monkeypatch, *argv)

    assert exc.value.code == 2  # argparse 의 사용법 에러 코드
    assert calls == []


@pytest.mark.asyncio
async def test_main_vods(db, monkeypatch):
    """성공 케이스."""
    seen: list[object] = []

    async def collect_new_vods(conn, bid):
        seen.append((conn.info.dbname, conn.autocommit, bid))
        return 1

    async def aclose():
        seen.append("aclose")

    monkeypatch.setattr(collector_main, "collect_new_vods", collect_new_vods)
    monkeypatch.setattr(collector_main, "aclose", aclose)

    await _main(monkeypatch, "vods", "--bid", "b1")

    assert seen == [(TEST_DB, True, "b1"), "aclose"]


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        (["vods", "--retry"], [("retry",)]),
        (["vods", "--sweep", "--bid", "b1"], [("sweep", "b1")]),
        (["chats", "--vod-id", "1000"], [("chats", 1000)]),
        (["chats", "--count", "3"], [("targets", 3)]),
        (["worker"], [("worker",)]),
    ],
)
@pytest.mark.asyncio
async def test_main_dispatch(db, monkeypatch, calls, argv, expected):
    """명령과 옵션에 맞는 함수를 부르고, 끝나면 클라이언트를 닫는지."""
    await _main(monkeypatch, *argv)

    assert calls == [*expected, ("aclose",)]


@pytest.mark.asyncio
async def test_main_closes_client_on_error(monkeypatch, calls):
    """실행 중 에러가 나도 클라이언트를 닫는지."""

    async def run_worker():
        raise RuntimeError("워커 에러")

    monkeypatch.setattr(collector_main, "run_worker", run_worker)

    with pytest.raises(RuntimeError):
        await _main(monkeypatch, "worker")

    assert calls == [("aclose",)]
