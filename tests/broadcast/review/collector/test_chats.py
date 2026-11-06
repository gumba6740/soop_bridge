"""
pytest tests/broadcast/review/collector/test_chats.py
"""

import asyncio
import logging
from datetime import datetime, timedelta

import httpx
import pytest
from psycopg.types.json import Jsonb

from app.broadcast.review.collector import chats
from app.utils.tz import KST
from tests.helpers import TIMEOUT

VOD_ID = 1000
BID = "방송인"
BROAD_START = datetime(2026, 9, 1, 20, 0, tzinfo=KST)
LIMIT = chats.CHAT_LIMIT_SECONDS
SINCE = chats.CHAT_COLLECT_SINCE
# 수집이 끝나지 않고 계속 돌면 테스트가 멈추지 않고 실패하게 하는 시간(초)


# ---------------------------------------------------------------- fixture


# ---------------------------------------------------------------- 가짜 SOOP 응답


def _chat_url(file_order: int = 1, vod_id: int = VOD_ID) -> str:
    return f"https://chat.test/{vod_id}/{file_order}?rowKey=abc"


def _xml(*times: float) -> str:
    """t 마다 채팅 한 건씩 담은 채팅 XML."""
    items = "".join(
        f"<chat><m>채팅 {t}</m><u>시청자</u><n>닉네임</n><t>{t}</t></chat>"
        for t in times
    )
    return f"<root><version>1.0.0</version>{items}</root>"


def _mock_chat(respx_mock, responses: dict[int, str | int], file_order: int = 1):
    """startTime 마다 다른 채팅 응답을 준다. 문자열은 200 과 그 XML, 숫자는 그 상태 코드."""

    def handler(request: httpx.Request) -> httpx.Response:
        res = responses[int(request.url.params["startTime"])]
        if isinstance(res, int):
            return httpx.Response(res)
        return httpx.Response(200, text=res)

    return respx_mock.get(host="chat.test", path=f"/{VOD_ID}/{file_order}").mock(
        side_effect=handler
    )


def _starts(route) -> list[int]:
    """요청한 startTime 들을 요청한 순서대로."""
    return [int(call.request.url.params["startTime"]) for call in route.calls]


def _status_error(status: int) -> httpx.HTTPStatusError:
    request = httpx.Request("GET", _chat_url())
    return httpx.HTTPStatusError(
        "에러", request=request, response=httpx.Response(status, request=request)
    )


# ---------------------------------------------------------------- DB 도우미


async def _add_vod(
    conn,
    vod_id: int = VOD_ID,
    *,
    broad_start: datetime = BROAD_START,
    chat_duration: int = 300,
) -> None:
    await conn.execute(
        "INSERT INTO vods (vod_id, bid, broad_start, chat_duration)"
        " VALUES (%s, %s, %s, %s)",
        (vod_id, BID, broad_start, chat_duration),
    )


async def _add_part(
    conn,
    file_order: int = 1,
    *,
    vod_id: int = VOD_ID,
    duration_sec: int = 900,
    start_sec: int | None = 0,
    has_chat_url: bool = True,
) -> None:
    """파트 하나. start_sec 는 방송 시작으로부터 몇 초 뒤에 시작했는지. None 이면 모름."""
    file_start = (
        None if start_sec is None else BROAD_START + timedelta(seconds=start_sec)
    )
    chat_url = _chat_url(file_order, vod_id) if has_chat_url else None
    await conn.execute(
        "INSERT INTO vod_files (vod_id, file_order, duration, chat_url, file_start)"
        " VALUES (%s, %s, %s, %s, %s)",
        (vod_id, file_order, duration_sec * 1000, chat_url, file_start),
    )


async def _add_progress(
    conn,
    file_order: int = 1,
    *,
    vod_id: int = VOD_ID,
    last_start_time: int = 0,
    done: bool = False,
    hours_ago: int = 2,
) -> None:
    """진행 기록. hours_ago 는 마지막 시도가 몇 시간 전인지."""
    await conn.execute(
        "INSERT INTO chat_progress (vod_id, file_order, last_start_time, done, updated_at)"
        " VALUES (%s, %s, %s, %s, now() - make_interval(hours => %s))",
        (vod_id, file_order, last_start_time, done, hours_ago),
    )


async def _add_chat(conn, file_order: int, time_offset: float, message: str) -> None:
    await conn.execute(
        "INSERT INTO chats (vod_id, file_order, type, time_offset, data)"
        " VALUES (%s, %s, 'chat', %s, %s)",
        (VOD_ID, file_order, time_offset, Jsonb({"message": message})),
    )


async def _progress(conn, file_order: int = 1) -> tuple | None:
    """(last_start_time, done)."""
    cur = await conn.execute(
        "SELECT last_start_time, done FROM chat_progress"
        " WHERE vod_id = %s AND file_order = %s",
        (VOD_ID, file_order),
    )
    return await cur.fetchone()


async def _saved(conn) -> list[tuple]:
    """저장된 채팅 (file_order, time_offset, message). 파트, 시간 순."""
    cur = await conn.execute(
        "SELECT file_order, time_offset, data->>'message' FROM chats"
        " ORDER BY file_order, time_offset"
    )
    return await cur.fetchall()


async def _try_lock(conn, file_order: int = 1) -> bool:
    cur = await conn.execute(
        "SELECT pg_try_advisory_lock(%s, %s)", (VOD_ID, file_order)
    )
    return (await cur.fetchone())[0]


async def _collect(
    conn,
    *,
    duration_sec: int,
    last_start_time: int = 0,
    offset_base: float = 0,
    chat_duration: int = 300,
) -> int:
    coro = chats.collect_part(
        conn,
        vod_id=VOD_ID,
        file_order=1,
        chat_url=_chat_url(),
        duration_ms=duration_sec * 1000,
        offset_base=offset_base,
        chat_duration=chat_duration,
        last_start_time=last_start_time,
    )
    return await asyncio.wait_for(coro, TIMEOUT)


async def _collect_vod(conn) -> int:
    return await asyncio.wait_for(chats.collect_vod_chats(conn, VOD_ID), TIMEOUT)


# ------------------------------------------------------------ _classify_error


@pytest.mark.parametrize(
    ("error", "start", "expected"),
    [
        (_status_error(404), 0, "empty"),
        (_status_error(404), LIMIT, "empty"),  # 5시간 뒤여도 404 는 빈 구간
        (_status_error(500), LIMIT, "tail"),  # 경계값
        (_status_error(500), LIMIT + 300, "tail"),
        (_status_error(500), LIMIT - 300, "retry"),  # 5시간 안쪽의 500 은 서버 문제
        (_status_error(503), LIMIT, "retry"),  # 500 이 아닌 5xx
        (_status_error(429), 0, "retry"),
        (httpx.ConnectError("연결 실패"), LIMIT, "retry"),  # 응답 자체를 못 받음
    ],
)
def test_classify_error(error, start, expected):
    """성공 케이스. 상태 코드와 구간 위치에 따라 empty/tail/retry 로 나누는지."""
    assert chats._classify_error(error, start) == expected


# ---------------------------------------------------------------- _part_lock


@pytest.mark.asyncio
async def test_part_lock(conn, other):
    """성공 케이스. 락을 잡으면 True 를 주고, 그동안 남은 못 잡으며, 끝나면 푸는지."""
    async with chats._part_lock(conn, VOD_ID, 1) as ok:
        assert ok is True
        assert await _try_lock(other) is False

    assert await _try_lock(other) is True


@pytest.mark.asyncio
async def test_part_lock_busy(conn, other):
    """다른 연결이 잡고 있으면 False 를 주는지."""
    await _try_lock(other)

    async with chats._part_lock(conn, VOD_ID, 1) as ok:
        assert ok is False


@pytest.mark.asyncio
async def test_part_lock_is_per_part(conn, other):
    """같은 VOD 라도 다른 파트의 락은 따로 잡히는지."""
    await _try_lock(other, file_order=1)

    async with chats._part_lock(conn, VOD_ID, 2) as ok:
        assert ok is True


@pytest.mark.asyncio
async def test_part_lock_released_on_error(conn, other):
    """안에서 에러가 나도 락을 푸는지."""
    with pytest.raises(RuntimeError):
        async with chats._part_lock(conn, VOD_ID, 1):
            raise RuntimeError("수집 중 에러")

    assert await _try_lock(other) is True


# ------------------------------------------------------------ collect_part


@pytest.mark.asyncio
async def test_collect_part(conn, respx_mock):
    """성공 케이스. 파트 끝까지 구간마다 받아 VOD 기준 시간으로 저장하고 완료로 기록하는지."""
    await _add_vod(conn)
    await _add_part(conn, duration_sec=650)
    route = _mock_chat(respx_mock, {0: _xml(10, 299.5), 300: _xml(305), 600: _xml(620)})

    total = await _collect(conn, duration_sec=650, offset_base=60)

    assert total == 4
    assert _starts(route) == [0, 300, 600]
    # 파트 안 시간 + offset_base(60)
    assert [t for _, t, _ in await _saved(conn)] == [70, 359.5, 365, 680]
    assert await _progress(conn) == (900, True)


@pytest.mark.asyncio
async def test_collect_part_resumes_from_last_start_time(conn, respx_mock):
    """last_start_time 부터 이어 받고, 파트 길이에 딱 닿으면 완료인지."""
    await _add_vod(conn)
    await _add_part(conn, duration_sec=900)
    route = _mock_chat(respx_mock, {0: _xml(10), 300: _xml(310), 600: _xml(610)})

    total = await _collect(conn, duration_sec=900, last_start_time=300)

    assert total == 2
    assert _starts(route) == [300, 600]
    assert await _progress(conn) == (900, True)


@pytest.mark.asyncio
async def test_collect_part_skips_empty_section(conn, respx_mock):
    """404 구간은 채팅 0개로 보고 다음 구간으로 넘어가는지."""
    await _add_vod(conn)
    await _add_part(conn, duration_sec=600)
    route = _mock_chat(respx_mock, {0: 404, 300: _xml(310)})

    total = await _collect(conn, duration_sec=600)

    assert total == 1
    assert _starts(route) == [0, 300]
    assert [t for _, t, _ in await _saved(conn)] == [310]
    assert await _progress(conn) == (600, True)


@pytest.mark.asyncio
async def test_collect_part_retry_keeps_position(conn, respx_mock, caplog):
    """일시 실패면 거기서 멈추고, 받은 구간은 남기고, 실패한 구간부터 다시 하게 기록하는지."""
    await _add_vod(conn)
    await _add_part(conn, duration_sec=900)
    route = _mock_chat(respx_mock, {0: _xml(10), 300: 503})

    total = await _collect(conn, duration_sec=900)

    assert total == 1
    assert _starts(route) == [0, 300]  # 600 은 요청하지 않는다
    assert [t for _, t, _ in await _saved(conn)] == [10]
    assert await _progress(conn) == (300, False)
    assert "[RETRY]" in caplog.text


@pytest.mark.asyncio
async def test_collect_part_tail_marks_done(conn, respx_mock):
    """5시간을 넘긴 구간에서 500 이면 남은 구간을 버리고 완료로 기록하는지."""
    await _add_vod(conn)
    await _add_part(conn, duration_sec=LIMIT + 600)
    route = _mock_chat(respx_mock, {LIMIT: 500})

    total = await _collect(conn, duration_sec=LIMIT + 600, last_start_time=LIMIT)

    assert total == 0
    assert _starts(route) == [LIMIT]
    assert await _progress(conn) == (LIMIT, True)


@pytest.mark.asyncio
async def test_collect_part_replaces_section_chats(conn, respx_mock):
    """다시 받으면 그 구간의 채팅만 지우고 새로 넣는지. 다른 구간, 다른 파트는 그대로."""
    await _add_vod(conn)
    await _add_part(conn, duration_sec=300)
    # offset_base 60, 구간 300초 → 지우는 범위는 60 이상 360 미만
    await _add_chat(conn, 1, 60, "옛 채팅")  # 범위 시작(포함)
    await _add_chat(conn, 1, 359.5, "옛 채팅")  # 범위 끝 바로 앞
    await _add_chat(conn, 1, 360, "다음 구간")  # 범위 끝(제외)
    await _add_chat(conn, 2, 100, "다른 파트")
    _mock_chat(respx_mock, {0: _xml(20)})

    await _collect(conn, duration_sec=300, offset_base=60)

    assert await _saved(conn) == [
        (1, 80, "채팅 20"),
        (1, 360, "다음 구간"),
        (2, 100, "다른 파트"),
    ]


@pytest.mark.asyncio
async def test_collect_part_skips_when_locked(conn, other, respx_mock):
    """다른 작업이 그 파트를 잡고 있으면 요청 없이 0 을 주는지."""
    await _add_vod(conn)
    await _add_part(conn, duration_sec=300)
    route = _mock_chat(respx_mock, {0: _xml(20)})
    await _try_lock(other)

    assert await _collect(conn, duration_sec=300) == 0
    assert route.call_count == 0
    assert await _progress(conn) is None


# ------------------------------------------------------ next_chat_targets


@pytest.mark.asyncio
async def test_next_chat_targets(conn):
    """성공 케이스. 안 끝난 VOD 를 오래된 순으로 limit 개까지, 한 번씩만 주는지."""
    # 1001: 파트 1은 끝났고 파트 2가 남음
    await _add_vod(conn, 1001, broad_start=BROAD_START + timedelta(days=2))
    await _add_part(conn, 1, vod_id=1001)
    await _add_part(conn, 2, vod_id=1001)
    await _add_progress(conn, 1, vod_id=1001, done=True)
    # 1002: 가장 오래됨. 안 끝난 파트가 둘이어도 한 번만
    await _add_vod(conn, 1002, broad_start=BROAD_START + timedelta(days=1))
    await _add_part(conn, 1, vod_id=1002)
    await _add_part(conn, 2, vod_id=1002)
    # 1003: 가장 새로움. limit 에 걸린다
    await _add_vod(conn, 1003, broad_start=BROAD_START + timedelta(days=3))
    await _add_part(conn, 1, vod_id=1003)

    assert await chats.next_chat_targets(conn, 2) == [1002, 1001]


@pytest.mark.parametrize(
    ("vod", "part", "progress", "picked"),
    [
        ({}, {}, None, True),  # 진행 기록 없음
        ({"broad_start": SINCE}, {}, None, True),  # 기준일 경계
        ({"broad_start": SINCE - timedelta(seconds=1)}, {}, None, False),  # 기준일 전
        ({}, {"has_chat_url": False}, None, False),
        ({}, {"start_sec": None}, None, False),
        ({}, {"duration_sec": 0}, None, False),
        ({}, {}, {"done": True}, False),
        ({}, {}, {"hours_ago": 0}, False),  # 1시간 안에 시도함(쿨다운)
        ({}, {}, {"hours_ago": 2}, True),  # 쿨다운이 지남
    ],
)
@pytest.mark.asyncio
async def test_next_chat_targets_conditions(conn, vod, part, progress, picked):
    """기준일, 채팅 주소, 시작 시각, 길이, 완료 여부, 쿨다운에 따라 대상에 드는지."""
    await _add_vod(conn, **vod)
    await _add_part(conn, **part)
    if progress is not None:
        await _add_progress(conn, **progress)

    assert await chats.next_chat_targets(conn, 10) == ([VOD_ID] if picked else [])


# --------------------------------------------------------- collect_vod_chats


@pytest.mark.asyncio
async def test_collect_vod_chats(conn, respx_mock):
    """성공 케이스. 안 끝난 파트만 이어 받고, 파트마다 방송 시작 기준 시간으로 저장하는지."""
    await _add_vod(conn, chat_duration=600)  # VOD 의 구간 길이를 쓴다
    # 파트 1: 600초까지 받아 둠 → 600 부터
    await _add_part(conn, 1, duration_sec=900, start_sec=0)
    await _add_progress(conn, 1, last_start_time=600)
    # 파트 2: 방송 시작 1시간 뒤에 시작 → 처음부터
    await _add_part(conn, 2, duration_sec=900, start_sec=3600)
    # 파트 3: 끝남, 파트 4: 채팅 주소 없음 → 요청하지 않는다(등록 안 한 주소라 요청하면 에러)
    await _add_part(conn, 3)
    await _add_progress(conn, 3, done=True)
    await _add_part(conn, 4, has_chat_url=False)
    part1 = _mock_chat(respx_mock, {600: _xml(610)}, file_order=1)
    part2 = _mock_chat(respx_mock, {0: _xml(10), 600: _xml()}, file_order=2)

    total = await _collect_vod(conn)

    assert total == 2
    assert (_starts(part1), _starts(part2)) == ([600], [0, 600])
    assert [(o, t) for o, t, _ in await _saved(conn)] == [(1, 610), (2, 3610)]


@pytest.mark.asyncio
async def test_collect_vod_chats_unknown_vod(conn, respx_mock, caplog):
    """vods 에 없는 VOD 면 요청 없이 0 이고, vods 부터 받으라고 남기는지."""
    with caplog.at_level(logging.INFO):
        assert await _collect_vod(conn) == 0

    assert "vods에 없음" in caplog.text


@pytest.mark.asyncio
async def test_collect_vod_chats_nothing_left(conn, respx_mock):
    """파트가 다 끝난 VOD 면 요청 없이 0 인지."""
    await _add_vod(conn)
    await _add_part(conn)
    await _add_progress(conn, done=True)

    assert await _collect_vod(conn) == 0
