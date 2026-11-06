"""
pytest tests/broadcast/live/collector/test_producer.py
"""

import asyncio
import json
import logging
from typing import Any

import pytest

from app.broadcast.live.collector import producer
from app.core.soop_clients.live_clients import shared
from app.core.soop_clients.live_clients.shared import LiveStatus
from tests.conftest import test_settings
from tests.helpers import TIMEOUT, _running, _until

BID = "방송인"
SUMMARY = test_settings.summary_key(BID)
CHATS = test_settings.chats_key(BID)
# 끝나지 않는 작업이 있어도 테스트가 멈추지 않고 실패하게 하는 시간(초)


# ---------------------------------------------------------------- fixture


@pytest.fixture(autouse=True)
def fast(monkeypatch) -> None:
    """기다리는 시간을 모두 줄인다."""
    for name in (
        "POLL_INTERVAL",
        "RETRY_DELAY",
        "RECONNECT_DELAY",
        "DETAIL_INTERVAL",
        "START_STAGGER",
    ):
        monkeypatch.setattr(producer, name, 0.01)


@pytest.fixture
def hls(monkeypatch) -> list[str]:
    """영상 주소를 가짜로 바꾼다. 접속 정보의 URL 뒤에 화질을 붙여 주고, 준 주소를 모은다."""
    given: list[str] = []

    async def get_hls_url(bid, info, quality):
        url = f"{info['URL']}/{quality}"
        given.append(url)
        return url

    monkeypatch.setattr(producer, "get_hls_url", get_hls_url)
    return given


class _Script:
    """가짜 get_live_info 가 차례로 줄 응답. 다 쓰면 finished 를 올리고 멈춘다."""

    def __init__(self) -> None:
        self.steps: list[Any] = []
        self.finished = asyncio.Event()


@pytest.fixture
def script(monkeypatch) -> _Script:
    """접속 정보 조회를 가짜로 바꾼다. 테스트가 script.steps 를 채운다.

    steps 의 항목이 예외면 그 예외를 낸다.
    """
    s = _Script()

    async def get_live_info(bid):
        if s.steps:
            step = s.steps.pop(0)
            if isinstance(step, Exception):
                raise step
            return step
        s.finished.set()
        await asyncio.Event().wait()  # 더 줄 응답이 없으면 멈춘다

    monkeypatch.setattr(producer, "get_live_info", get_live_info)
    return s


@pytest.fixture
def relayed(monkeypatch, r) -> list[dict]:
    """채팅 받기를 가짜로 바꾼다. 불릴 때마다 받은 접속 정보와 그때의 summary 를 모은다."""
    calls: list[dict] = []

    async def relay_chats(r, bid, info):
        calls.append({"info": info, "summary": await r.hgetall(SUMMARY)})
        await r.xadd(CHATS, {"type": "chat", "data": "{}"})
        return 1

    monkeypatch.setattr(producer, "relay_chats", relay_chats)
    return calls


@pytest.fixture
def details(monkeypatch) -> list[tuple[str, str]]:
    """방송 현황 갱신을 가짜로 바꾼다. 시작과 멈춤을 (start|stop, bno) 로 모은다."""
    events: list[tuple[str, str]] = []

    async def detail_loop(r, bid, bno):
        events.append(("start", bno))
        try:
            await asyncio.Event().wait()
        finally:
            events.append(("stop", bno))

    monkeypatch.setattr(producer, "detail_loop", detail_loop)
    return events


# ---------------------------------------------------------------- 도우미


def _info(bno: int, url: str = "주소1") -> dict[str, Any]:
    """볼 수 있는 방송의 접속 정보. URL 은 가짜 영상 주소의 앞부분이다."""
    return {
        "BNO": str(bno),  # SOOP 은 방송 번호를 문자열로 준다
        "BJNICK": "닉네임",
        "TITLE": "방송 제목",
        "HASH_TAGS": ["태그"],
        "URL": url,
    }


def _raw(svc: str, parts: list[str]) -> bytes:
    """채팅 서버가 보내는 패킷 모양. ESC TAB + 코드 + 길이 + 00 + SEP 칸 ... SEP"""
    body = "".join(f"\x0c{p}" for p in parts) + "\x0c"
    return b"\x1b\t" + svc.encode() + b"000000" + b"00" + body.encode()


# ---------------------------------------------------------------- 작은 함수들


def test_kst_iso():
    """성공 케이스. SOOP 시각 문자열을 KST 가 붙은 ISO 형식으로 바꾸는지."""
    assert producer._kst_iso("2026-10-04 18:01:57") == "2026-10-04T18:01:57+09:00"


def test_tag_mapping():
    """성공 케이스. 언어, 카테고리, 해시태그 순으로 합쳐 한글 그대로 JSON 으로 주는지."""
    info = {
        "LANG_TAGS": ["한국어"],
        "CATEGORY_TAGS": ["게임"],
        "HASH_TAGS": ["태그1", "태그2"],
    }

    assert producer._tag_mapping(info) == {
        "tags": '["한국어", "게임", "태그1", "태그2"]'
    }


def test_tag_mapping_missing_tags():
    """태그 칸이 없거나 None 이면 빈 목록으로 보는지."""
    assert producer._tag_mapping({"HASH_TAGS": None}) == {"tags": "[]"}


@pytest.mark.asyncio
async def test_hls_mapping(hls):
    """성공 케이스. 화질마다 영상 주소를 받아 hls_{화질} 로 주는지."""
    assert await producer._hls_mapping(BID, _info(1)) == {
        "hls_sd": "주소1/sd",
        "hls_hd": "주소1/hd",
    }


@pytest.mark.parametrize(
    "hd",
    [
        RuntimeError("주소 실패"),  # 요청이 실패함
        None,  # 요청은 됐는데 주소가 없음
    ],
)
@pytest.mark.asyncio
async def test_hls_mapping_skips_failed_quality(monkeypatch, caplog, hd):
    """hd 주소를 못 받으면 hd 만 빼고 sd 는 주는지. 실패면 경고를 남긴다."""

    async def get_hls_url(bid, info, quality):
        if quality == "sd":
            return "주소1/sd"
        if isinstance(hd, Exception):
            raise hd
        return hd

    monkeypatch.setattr(producer, "get_hls_url", get_hls_url)

    assert await producer._hls_mapping(BID, _info(1)) == {"hls_sd": "주소1/sd"}
    assert ("quality=hd 영상 주소 실패" in caplog.text) is isinstance(hd, Exception)


@pytest.mark.asyncio
async def test_stop():
    """성공 케이스. 태스크를 취소하고 끝날 때까지 기다리는지. None 이면 아무것도 안 하는지."""

    async def forever() -> None:
        await asyncio.Event().wait()

    task = asyncio.create_task(forever())

    await producer._stop(task)
    await producer._stop(None)

    assert task.cancelled()


# ---------------------------------- on_start, refresh_video, on_end


@pytest.mark.asyncio
async def test_on_start(r, hls):
    """성공 케이스. 이전 방송의 summary 를 지우고 새로 쓰며, 채팅 스트림의 만료를 없애는지."""
    await r.hset(SUMMARY, mapping={"bno": "0", "viewers": "999"})  # 이전 방송
    await r.xadd(CHATS, {"type": "chat", "data": "{}"})
    await r.expire(CHATS, 600)  # 이전 방송이 끝나 만료가 걸려 있음

    await producer.on_start(r, BID, _info(1))

    assert await r.hgetall(SUMMARY) == {
        "bno": "1",
        "nick": "닉네임",
        "title": "방송 제목",
        "hls_sd": "주소1/sd",
        "hls_hd": "주소1/hd",
        "tags": '["태그"]',
    }
    assert await r.ttl(CHATS) == -1  # 만료 없음


@pytest.mark.asyncio
async def test_refresh_video(r, monkeypatch):
    """성공 케이스. 영상 주소와 태그만 바꾸고, 받지 못한 화질의 이전 주소는 지우는지."""
    await r.hset(
        SUMMARY,
        mapping={
            "bno": "1",
            "title": "방송 제목",
            "hls_sd": "옛 sd",
            "hls_hd": "옛 hd",
        },
    )

    async def get_hls_url(bid, info, quality):
        return "새 sd" if quality == "sd" else None  # hd 는 이번에 못 받음

    monkeypatch.setattr(producer, "get_hls_url", get_hls_url)

    await producer.refresh_video(r, BID, {"HASH_TAGS": ["새 태그"]})

    assert await r.hgetall(SUMMARY) == {
        "bno": "1",
        "title": "방송 제목",
        "hls_sd": "새 sd",
        "tags": '["새 태그"]',
    }


@pytest.mark.asyncio
async def test_on_end(r):
    """성공 케이스. summary 를 지우고 채팅 스트림은 정해진 시간 뒤에 사라지게 하는지."""
    await r.hset(SUMMARY, mapping={"bno": "1"})
    await r.xadd(CHATS, {"type": "chat", "data": "{}"})

    await producer.on_end(r, BID)

    assert await r.exists(SUMMARY) == 0
    assert 0 < await r.ttl(CHATS) <= producer.CHAT_TTL_AFTER_END


# ----------------------------------------------------- detail_loop


def _detail(bno: int, *, broad_start: str | None = "2026-10-04 18:00:00") -> dict:
    """방송 현황 응답에서 쓰는 칸만."""
    return {
        "broad": {
            "broad_no": bno,
            "broad_title": "바뀐 제목",
            "current_sum_viewer": 1234,
        },
        "station": {"broad_start": broad_start} if broad_start else {},
        "current_timestamp": "2026-10-04 19:00:00",
    }


class _Details:
    """가짜 get_broad_detail 의 응답과 불린 횟수."""

    def __init__(self) -> None:
        self.responses: list = []
        self.calls = 0


@pytest.fixture
def broad_details(monkeypatch) -> _Details:
    """방송 현황 조회를 가짜로 바꾼다. 응답을 차례로 주고, 마지막 것은 계속 준다.

    응답이 예외면 그 예외를 낸다.
    """
    d = _Details()

    async def get_broad_detail(bid):
        d.calls += 1
        step = d.responses[0] if len(d.responses) == 1 else d.responses.pop(0)
        if isinstance(step, Exception):
            raise step
        return step

    monkeypatch.setattr(producer, "get_broad_detail", get_broad_detail)
    return d


@pytest.mark.asyncio
async def test_detail_loop(r, broad_details):
    """성공 케이스. 같은 방송이면 제목, 시청자 수, 시각을 summary 에 KST ISO 로 쓰는지."""
    broad_details.responses = [_detail(1)]

    async with _running(producer.detail_loop(r, BID, "1")):
        await _until(lambda: r.hexists(SUMMARY, "viewers"))

    assert await r.hgetall(SUMMARY) == {
        "title": "바뀐 제목",
        "viewers": "1234",
        "viewers_at": "2026-10-04T19:00:00+09:00",
        "started_at": "2026-10-04T18:00:00+09:00",
    }


@pytest.mark.asyncio
async def test_detail_loop_without_broad_start(r, broad_details):
    """방송 시작 시각이 없으면 started_at 만 빼고 쓰는지."""
    broad_details.responses = [_detail(1, broad_start=None)]

    async with _running(producer.detail_loop(r, BID, "1")):
        await _until(lambda: r.hexists(SUMMARY, "viewers"))

    assert await r.hexists(SUMMARY, "started_at") == 0


@pytest.mark.asyncio
async def test_detail_loop_ignores_other_broadcast(r, broad_details):
    """다른 방송의 현황이 오면 쓰지 않는지."""
    broad_details.responses = [_detail(2)]

    async with _running(producer.detail_loop(r, BID, "1")):
        await _until(lambda: broad_details.calls >= 3)  # 여러 번 돌게 둔다

    assert await r.exists(SUMMARY) == 0


@pytest.mark.asyncio
async def test_detail_loop_keeps_going_after_error(r, broad_details, caplog):
    """한 번 실패해도 멈추지 않고 다음 차례에 쓰는지."""
    broad_details.responses = [RuntimeError("현황 실패"), _detail(1)]

    async with _running(producer.detail_loop(r, BID, "1")):
        await _until(lambda: r.hexists(SUMMARY, "viewers"))

    assert "방송 현황 실패 RuntimeError" in caplog.text


# -------------------------------------------------------- relay_chats


@pytest.mark.asyncio
async def test_relay_chats(r, monkeypatch):
    """성공 케이스. 변환할 줄 아는 패킷만 다시보기 모양으로 스트림에 넣고 개수를 주는지."""

    async def stream_live_chats(bid, info):
        yield _raw("0005", ["안녕하세요", "시청자", "0", "0", "ko", "닉네임"])
        yield b"\x00\x01"  # 패킷 모양이 아님
        yield _raw("9999", ["모르는 코드"])

    monkeypatch.setattr(producer, "stream_live_chats", stream_live_chats)

    assert await producer.relay_chats(r, BID, _info(1)) == 1

    entries = await r.xrange(CHATS)
    assert len(entries) == 1
    _, fields = entries[0]
    assert fields["type"] == "chat"
    data = json.loads(fields["data"])
    assert (data["message"], data["user_nick"]) == ("안녕하세요", "닉네임")
    assert "안녕하세요" in fields["data"]  # 한글을 \\u 로 바꾸지 않는다


@pytest.mark.asyncio
async def test_relay_chats_raises_when_disconnected(r, monkeypatch):
    """끊기면 그때까지 넣은 채팅은 남기고 예외를 올려 보내는지."""

    async def stream_live_chats(bid, info):
        yield _raw("0005", ["안녕하세요"])
        raise ConnectionError("끊김")

    monkeypatch.setattr(producer, "stream_live_chats", stream_live_chats)

    with pytest.raises(ConnectionError):
        await producer.relay_chats(r, BID, _info(1))

    assert await r.xlen(CHATS) == 1


# ---------------------------------------------------------------- watch


async def _watch_until_script_ends(r, script: _Script) -> None:
    """watch 를 띄우고, 준비한 응답을 다 쓸 때까지 기다린 뒤 멈춘다."""
    async with _running(producer.watch(r, BID)):
        await asyncio.wait_for(script.finished.wait(), TIMEOUT)


@pytest.mark.asyncio
async def test_watch(r, hls, script, relayed, details, caplog):
    """성공 케이스. 방송이 시작되면 summary 를 쓰고 채팅을 받고, 끝나면 정리하는지."""
    script.steps = [(LiveStatus.LIVE, _info(1)), (LiveStatus.OFFLINE, None)]

    with caplog.at_level(logging.INFO):
        await _watch_until_script_ends(r, script)

    # 채팅을 받을 때는 summary 가 있었다
    assert [c["info"] for c in relayed] == [_info(1)]
    assert relayed[0]["summary"]["bno"] == "1"
    # 끝나면 summary 는 지우고 채팅은 잠시 남긴다
    assert await r.exists(SUMMARY) == 0
    assert await r.ttl(CHATS) > 0
    assert details == [("start", "1"), ("stop", "1")]
    assert "방송 시작" in caplog.text
    assert "방송 종료" in caplog.text


@pytest.mark.parametrize("status", [LiveStatus.OFFLINE, LiveStatus.LOGIN_REQUIRED])
@pytest.mark.asyncio
async def test_watch_clears_stale_summary_on_first_check(
    r, script, relayed, details, caplog, status
):
    """처음 확인에서 방송 중이 아니면 이전 실행이 남긴 summary 를 지우는지. 종료 로그는 없다."""
    await r.hset(SUMMARY, mapping={"bno": "0"})  # 이전 실행이 남김
    script.steps = [(status, None)]

    with caplog.at_level(logging.INFO):
        await _watch_until_script_ends(r, script)

    assert await r.exists(SUMMARY) == 0
    assert relayed == []
    assert details == []
    assert "방송 종료" not in caplog.text


@pytest.mark.asyncio
async def test_watch_reconnect_refreshes_video(
    r, hls, script, relayed, details, caplog
):
    """같은 방송에 다시 접속하면 summary 를 새로 쓰지 않고 영상 주소만 바꾸는지."""
    script.steps = [
        (LiveStatus.LIVE, _info(1, url="주소1")),
        (LiveStatus.LIVE, _info(1, url="주소2")),  # 송출이 다시 시작돼 주소가 바뀜
    ]

    with caplog.at_level(logging.INFO):
        await _watch_until_script_ends(r, script)

    assert [c["summary"]["hls_sd"] for c in relayed] == ["주소1/sd", "주소2/sd"]
    assert details == [("start", "1"), ("stop", "1")]  # 현황 갱신은 이어서 돈다
    assert caplog.text.count("방송 시작") == 1


@pytest.mark.asyncio
async def test_watch_new_broadcast(r, hls, script, relayed, details):
    """방송 번호가 바뀌면 새 방송으로 보고 summary 와 현황 갱신을 새로 시작하는지."""
    await r.hset(SUMMARY, mapping={"viewers": "999"})
    script.steps = [(LiveStatus.LIVE, _info(1)), (LiveStatus.LIVE, _info(2))]

    await _watch_until_script_ends(r, script)

    assert relayed[1]["summary"]["bno"] == "2"
    assert "viewers" not in relayed[1]["summary"]  # 이전 값은 지워졌다
    assert details == [("start", "1"), ("stop", "1"), ("start", "2"), ("stop", "2")]


@pytest.mark.asyncio
async def test_watch_age_restricted(r, hls, script, relayed, details, caplog):
    """방송 중에 연령 제한이 켜지면 종료로 보지 않고 이전 접속 정보로 다시 받는지."""
    script.steps = [
        (LiveStatus.LIVE, _info(1)),
        (LiveStatus.LOGIN_REQUIRED, None),
    ]

    with caplog.at_level(logging.INFO):
        await _watch_until_script_ends(r, script)

    assert [c["info"] for c in relayed] == [_info(1), _info(1)]
    assert hls == [
        "주소1/sd",
        "주소1/hd",
    ]  # 방송 시작 때만 받고, 이전 정보로는 받지 않는다
    assert await r.exists(SUMMARY) == 1
    assert details == [("start", "1"), ("stop", "1")]
    assert "연령 제한 중" in caplog.text
    assert "방송 종료" not in caplog.text


@pytest.mark.asyncio
async def test_watch_retries_after_error(r, hls, script, details, monkeypatch, caplog):
    """채팅을 받다 끊기면 기다렸다가 다시 확인하고, 같은 방송이면 영상 주소를 새로 받는지."""
    summaries = []

    async def relay_chats(r, bid, info):
        summaries.append(await r.hget(SUMMARY, "hls_sd"))
        if len(summaries) == 1:
            raise ConnectionError("끊김")
        return 1

    monkeypatch.setattr(producer, "relay_chats", relay_chats)
    script.steps = [
        (LiveStatus.LIVE, _info(1, url="주소1")),
        (LiveStatus.LIVE, _info(1, url="주소2")),
    ]

    await _watch_until_script_ends(r, script)

    assert summaries == ["주소1/sd", "주소2/sd"]
    assert f"bid={BID} ConnectionError: 끊김" in caplog.text


@pytest.mark.asyncio
async def test_watch_cancel_stops_detail(r, hls, script, details, monkeypatch):
    """watch 가 취소되면 현황 갱신 태스크도 같이 멈추는지."""
    receiving = asyncio.Event()

    async def relay_chats(r, bid, info):
        receiving.set()
        await asyncio.Event().wait()  # 채팅을 계속 받는 중
        return 0

    monkeypatch.setattr(producer, "relay_chats", relay_chats)
    script.steps = [(LiveStatus.LIVE, _info(1))]

    async with _running(producer.watch(r, BID)) as task:
        await asyncio.wait_for(receiving.wait(), TIMEOUT)

    assert task.cancelled()
    assert details == [("start", "1"), ("stop", "1")]


# ---------------------------------------------------------------- request_stats_loop


@pytest.fixture
def request_counts():
    """SOOP 요청 수 카운터. 앞뒤로 비운다."""
    shared.request_counts.clear()
    yield shared.request_counts
    shared.request_counts.clear()


@pytest.mark.asyncio
async def test_request_stats_loop(request_counts, monkeypatch, caplog):
    """성공 케이스. 그동안의 요청 수를 많은 순으로 한 줄 남기고 카운터를 비우는지."""
    monkeypatch.setattr(producer, "STATS_INTERVAL", 0.01)
    request_counts.update({"broad_detail": 1, "player_live_api.php": 2})

    with caplog.at_level(logging.INFO):
        async with _running(producer.request_stats_loop()):
            await _until(lambda: "[STATS]" in caplog.text)

    assert "[STATS] SOOP 요청 3건: player_live_api.php 2, broad_detail 1" in caplog.text
    assert request_counts.total() == 0


# ---------------------------------------------------------------- watch_all


@pytest.mark.asyncio
async def test_watch_all(r, monkeypatch, caplog):
    """성공 케이스. 스트리머마다 watch 를 띄우고, 끝나면 그 summary 를 지우는지."""
    watching = []

    async def watch(r, bid):
        watching.append(bid)
        await asyncio.Event().wait()

    monkeypatch.setattr(producer, "watch", watch)
    bids = ["b1", "b2"]
    for bid in [*bids, "b3"]:
        await r.hset(test_settings.summary_key(bid), mapping={"bno": "1"})

    with caplog.at_level(logging.INFO):
        async with _running(producer.watch_all(r, bids)):
            await _until(lambda: len(watching) == 2)

    assert watching == bids
    assert await r.exists(*(test_settings.summary_key(b) for b in bids)) == 0
    assert (
        await r.exists(test_settings.summary_key("b3")) == 1
    )  # 지켜보지 않던 것은 그대로
    assert "라이브 collector 종료" in caplog.text


@pytest.mark.asyncio
async def test_watch_all_cleans_up_when_watch_fails(r, monkeypatch):
    """watch 하나가 예상 못 한 에러로 죽어도 summary 를 지우고 에러를 올려 보내는지."""

    async def watch(r, bid):
        raise RuntimeError("watch 에러")

    monkeypatch.setattr(producer, "watch", watch)
    await r.hset(test_settings.summary_key("b1"), mapping={"bno": "1"})

    with pytest.raises(ExceptionGroup):
        await asyncio.wait_for(producer.watch_all(r, ["b1"]), TIMEOUT)

    assert await r.exists(test_settings.summary_key("b1")) == 0
