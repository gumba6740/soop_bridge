"""
pytest tests/broadcast/review/collector/test_vods.py
"""

import logging
from collections.abc import Callable
from datetime import datetime
from typing import Any

import httpx
import pytest

from app.broadcast.review.collector import vods
from app.broadcast.review.shared.schemas import VodFileRow, VodRow
from app.core.soop_clients.review_client import VOD_DETAIL_URL, VOD_LIST_URL
from app.utils.tz import KST

BID = "방송인"
LIST_URL = VOD_LIST_URL.format(bid=BID)


# ---------------------------------------------------------------- fixture


# ----------------------------------------------------------- 가짜 SOOP 응답


def _page(ids: list[int | None], *, last_page: int, total: int = 999) -> dict:
    """목록 한 페이지. None 은 title_no 가 없는 항목."""
    items = [{"title_no": i} if i is not None else {"title": "id 없음"} for i in ids]
    return {
        "meta": {"last_page": last_page, "total": total, "per_page": 60},
        "data": items,
    }


def _mock_list(respx_mock, pages: dict[int, dict]):
    """페이지 번호마다 다른 목록 응답을 준다. 요청 횟수는 돌려준 route 로 센다."""

    def handler(request: httpx.Request) -> httpx.Response:
        page = int(request.url.params["page"])
        return httpx.Response(200, json=pages[page])

    return respx_mock.get(LIST_URL).mock(side_effect=handler)


def _detail(vod_id: int, *, title: str = "다시보기 제목") -> dict:
    """정상 상세 응답. 파트 하나."""
    return {
        "result": 1,
        "data": {
            "bj_id": BID,
            "full_title": title,
            "total_file_duration": 3_600_000,
            "broad_start": "2026-09-01 20:00:00",
            "write_tm": "2026-09-01 20:00:00 ~ 2026-09-01 21:00:00",
            "files": [
                {
                    "file_order": 1,
                    "duration": 3_600_000,
                    "chat": f"채팅 주소 {vod_id}",
                    "file": f"영상 주소 {vod_id}",
                    "file_start": "2026-09-01 20:00:00",
                }
            ],
        },
    }


def _mock_detail(respx_mock, responses: dict[int, Callable[[], httpx.Response]]):
    """vod_id 마다 다른 상세 응답을 준다. 요청 받은 vod_id 는 route 로 확인한다."""

    def handler(request: httpx.Request) -> httpx.Response:
        vod_id = int(request.content.decode().removeprefix("nTitleNo="))
        return responses[vod_id]()

    return respx_mock.post(VOD_DETAIL_URL).mock(side_effect=handler)


def _ok(vod_id: int) -> Callable[[], httpx.Response]:
    return lambda: httpx.Response(200, json=_detail(vod_id))


def _requested_ids(route) -> list[int]:
    """상세를 요청한 vod_id 들을 요청한 순서대로."""
    return [
        int(call.request.content.decode().removeprefix("nTitleNo="))
        for call in route.calls
    ]


# ---------------------------------------------------------------- DB 도우미


async def _fetchall(conn, sql: str, params: Any = None) -> list[tuple]:
    cur = await conn.execute(sql, params)
    return await cur.fetchall()


async def _add_vod(conn, vod_id: int, *, bid: str = BID) -> None:
    await conn.execute(
        "INSERT INTO vods (vod_id, bid, broad_start) VALUES (%s, %s, %s)",
        (vod_id, bid, datetime(2026, 9, 1, tzinfo=KST)),
    )


async def _add_failure(
    conn, vod_id: int, *, permanent: bool = False, resolved: bool = False
) -> None:
    await conn.execute(
        "INSERT INTO vod_fetch_failures (vod_id, bid, reason, permanent, resolved_at)"
        " VALUES (%s, %s, '이전 실패', %s, CASE WHEN %s THEN now() END)",
        (vod_id, BID, permanent, resolved),
    )


async def _failure(conn, vod_id: int) -> tuple | None:
    """(reason, permanent, attempts, 해결됐는지)."""
    rows = await _fetchall(
        conn,
        "SELECT reason, permanent, attempts, resolved_at IS NOT NULL"
        " FROM vod_fetch_failures WHERE vod_id = %s",
        (vod_id,),
    )
    return rows[0] if rows else None


# ---------------------------------------------------------------- _scalar


@pytest.mark.asyncio
async def test_scalar(conn):
    """성공 케이스. 첫 행의 첫 칸을 주는지."""
    assert await vods._scalar(conn, "SELECT %s", (7,)) == 7


@pytest.mark.asyncio
async def test_scalar_no_row_is_zero(conn):
    """행이 없으면 0 인지."""
    assert await vods._scalar(conn, "SELECT 1 WHERE false", None) == 0


@pytest.mark.asyncio
async def test_scalar_converts_to_int(conn):
    """numeric(Decimal) 로 와도 int 로 바꾸는지."""
    value = await vods._scalar(conn, "SELECT 5::numeric", None)

    assert value == 5
    assert type(value) is int  # Decimal('5') == 5 도 참이라 타입까지 본다


# ---------------------------------------------------------------- fetch_new_vod_ids


@pytest.mark.asyncio
async def test_fetch_new_vod_ids(respx_mock):
    """성공 케이스. 워터마크보다 큰 id 만 오름차순으로, 경계에 닿으면 더 요청하지 않는지."""
    route = _mock_list(
        respx_mock,
        {
            1: _page([130, 125, None, 120], last_page=3, total=500),
            # 목록을 넘기는 사이 밀려서 120 이 또 나온다. 100 은 워터마크와 같다
            2: _page([120, 115, 110, 100, 95], last_page=3, total=500),
            3: _page([90, 85], last_page=3, total=500),
        },
    )

    ids, total = await vods.fetch_new_vod_ids(BID, watermark=100)

    assert ids == [110, 115, 120, 125, 130]
    assert total == 500
    assert route.call_count == 2  # 2페이지에서 경계에 닿아 3페이지는 요청하지 않는다


@pytest.mark.asyncio
async def test_fetch_new_vod_ids_watermark_zero_reads_all_pages(respx_mock):
    """워터마크가 0 이면(sweep) 마지막 페이지까지 다 읽는지."""
    route = _mock_list(
        respx_mock,
        {
            1: _page([30, 20], last_page=2),
            2: _page([10], last_page=2),
        },
    )

    ids, _ = await vods.fetch_new_vod_ids(BID, watermark=0)

    assert ids == [10, 20, 30]
    assert route.call_count == 2


@pytest.mark.asyncio
async def test_fetch_new_vod_ids_without_meta(respx_mock, caplog):
    """meta 가 없으면 경고를 남기고 1페이지만 보며 total 은 0 인지."""
    route = _mock_list(respx_mock, {1: {"data": [{"title_no": 20}, {"title_no": 10}]}})

    ids, total = await vods.fetch_new_vod_ids(BID, watermark=0)

    assert (ids, total) == ([10, 20], 0)
    assert route.call_count == 1
    assert "meta 없음" in caplog.text


@pytest.mark.asyncio
async def test_fetch_new_vod_ids_stops_when_data_key_missing(respx_mock, caplog):
    """페이지에 data 키가 없으면 경고를 남기고 더 요청하지 않는지."""
    route = _mock_list(
        respx_mock,
        {
            1: _page([30, 20], last_page=3),
            2: {"meta": {"last_page": 3}},
            3: _page([10], last_page=3),
        },
    )

    ids, _ = await vods.fetch_new_vod_ids(BID, watermark=0)

    assert ids == [20, 30]
    assert route.call_count == 2
    assert "data 키 없음" in caplog.text


@pytest.mark.asyncio
async def test_fetch_new_vod_ids_stops_when_page_empty(respx_mock):
    """빈 페이지가 오면 더 요청하지 않는지."""
    route = _mock_list(
        respx_mock,
        {
            1: _page([30, 20], last_page=3),
            2: _page([], last_page=3),
            3: _page([10], last_page=3),
        },
    )

    ids, _ = await vods.fetch_new_vod_ids(BID, watermark=0)

    assert ids == [20, 30]
    assert route.call_count == 2


@pytest.mark.asyncio
async def test_fetch_new_vod_ids_skips_page_without_valid_id(respx_mock, caplog):
    """유효한 id 가 하나도 없는 페이지는 경고만 남기고 다음 페이지로 넘어가는지."""
    route = _mock_list(
        respx_mock,
        {
            1: _page([30], last_page=3),
            2: _page([None, None], last_page=3),
            3: _page([10], last_page=3),
        },
    )

    ids, _ = await vods.fetch_new_vod_ids(BID, watermark=0)

    assert ids == [10, 30]
    assert route.call_count == 3
    assert "유효 id 없음" in caplog.text


# ---------------------------------------------------------------- parse_vod_detail


def test_parse_vod_detail():
    """성공 케이스. 상세 응답을 VodRow 와 파트 목록으로 바꾸는지. dict 가 아닌 파트는 뺀다."""
    data = _detail(1000)
    data["data"]["files"].append("파트 아님")

    result = vods.parse_vod_detail(1000, data)
    assert result is not None
    vod, files = result

    assert (vod.vod_id, vod.bid, vod.title) == (1000, BID, "다시보기 제목")
    assert files == [
        VodFileRow(
            vod_id=1000,
            file_order=1,
            duration=3_600_000,
            chat_url="채팅 주소 1000",
            hls_url="영상 주소 1000",
            file_start=datetime(2026, 9, 1, 20, 0, tzinfo=KST),
        )
    ]


def test_parse_vod_detail_without_files():
    """files 가 없거나 None 이면 파트 목록이 빈 리스트인지."""
    data = _detail(1000)
    data["data"]["files"] = None

    result = vods.parse_vod_detail(1000, data)
    assert result is not None
    _, files = result

    assert files == []


@pytest.mark.parametrize("data", [{"result": 1, "data": []}, {"result": 1}, {}])
def test_parse_vod_detail_bad_shape_is_none(data, caplog):
    """data 가 dict 가 아니거나 없으면 경고를 남기고 None 인지."""
    assert vods.parse_vod_detail(1000, data) is None
    assert "응답 형식 이상" in caplog.text


def test_parse_vod_detail_invalid_is_none(caplog):
    """필수 값(broad_start)이 없어 검증에 실패하면 경고를 남기고 None 인지."""
    data = _detail(1000)
    del data["data"]["broad_start"]

    assert vods.parse_vod_detail(1000, data) is None
    assert "검증 실패" in caplog.text


# ---------------------------------------------------------------- save_vod


def _rows(vod_id: int, *, title: str = "다시보기 제목", chat: str = "채팅 주소"):
    vod = VodRow(
        vod_id=vod_id,
        bid=BID,
        title=title,
        broad_start=datetime(2026, 9, 1, 20, 0, tzinfo=KST),
    )
    files = [VodFileRow(vod_id=vod_id, file_order=1, duration=60_000, chat=chat)]
    return vod, files


@pytest.mark.asyncio
async def test_save_vod(conn):
    """성공 케이스. vods 와 vod_files 에 저장하고 True 를 주는지."""
    assert await vods.save_vod(conn, *_rows(1000)) is True

    assert await _fetchall(conn, "SELECT vod_id, title FROM vods") == [
        (1000, "다시보기 제목")
    ]
    assert await _fetchall(
        conn, "SELECT vod_id, file_order, chat_url FROM vod_files"
    ) == [(1000, 1, "채팅 주소")]


@pytest.mark.asyncio
async def test_save_vod_again_keeps_vod_but_updates_file_urls(conn):
    """이미 있는 VOD 면 False. VOD 는 그대로 두고 파트 주소만 새 값으로 바꾸는지."""
    await vods.save_vod(conn, *_rows(1000))

    inserted = await vods.save_vod(
        conn, *_rows(1000, title="바뀐 제목", chat="새 채팅 주소")
    )

    assert inserted is False
    assert await _fetchall(conn, "SELECT title FROM vods") == [("다시보기 제목",)]
    assert await _fetchall(conn, "SELECT chat_url FROM vod_files") == [
        ("새 채팅 주소",)
    ]


@pytest.mark.asyncio
async def test_save_vod_resolves_failure(conn):
    """실패 기록이 있던 VOD 를 저장하면 그 기록을 해결 처리하는지."""
    await _add_failure(conn, 1000)

    await vods.save_vod(conn, *_rows(1000))

    failure = await _failure(conn, 1000)
    assert failure is not None
    assert failure[3] is True


# --------------------------------------------------------- record_failure


@pytest.mark.asyncio
async def test_record_failure(conn):
    """성공 케이스. 처음 실패하면 attempts=1 로 기록하는지. 이유는 500자로 자른다."""
    await vods.record_failure(conn, 1000, BID, "가" * 600, permanent=False)

    failure = await _failure(conn, 1000)
    assert failure is not None
    reason, permanent, attempts, resolved = failure
    assert (len(reason), permanent, attempts, resolved) == (500, False, 1, False)


@pytest.mark.asyncio
async def test_record_failure_becomes_permanent_after_max_attempts(conn):
    """같은 VOD 가 MAX_RETRY_ATTEMPTS 번 실패하면 영구 실패가 되는지."""
    for _ in range(vods.MAX_RETRY_ATTEMPTS - 1):
        await vods.record_failure(conn, 1000, BID, "일시 실패", permanent=False)
    failure = await _failure(conn, 1000)
    assert failure is not None
    assert failure[1:3] == (False, vods.MAX_RETRY_ATTEMPTS - 1)

    await vods.record_failure(conn, 1000, BID, "일시 실패", permanent=False)

    failure = await _failure(conn, 1000)
    assert failure is not None
    assert failure[1:3] == (True, vods.MAX_RETRY_ATTEMPTS)


@pytest.mark.asyncio
async def test_record_failure_permanent(conn):
    """영구 실패로 넘기면 첫 실패부터 permanent 인지."""
    await vods.record_failure(conn, 1000, BID, "영구 실패", permanent=True)

    failure = await _failure(conn, 1000)
    assert failure is not None
    assert failure[1] is True


# ---------------------------------------------------------------- process_vod


@pytest.mark.asyncio
async def test_process_vod(conn, respx_mock):
    """성공 케이스. 상세를 받아 저장하고 True 를 주는지."""
    _mock_detail(respx_mock, {1000: _ok(1000)})

    assert await vods.process_vod(conn, 1000, BID) is True
    assert await _fetchall(conn, "SELECT vod_id FROM vods") == [(1000,)]
    assert await _failure(conn, 1000) is None


@pytest.mark.parametrize(
    ("response", "reason_prefix", "permanent"),
    [
        # 429/5xx: 일시 실패
        (lambda: httpx.Response(500), "http: ", False),
        # 404: 클라이언트가 None 을 준다(삭제/비공개)
        (lambda: httpx.Response(404), "영구 실패", True),
        # 200 인데 형식이 이상함: 스키마 변경일 수도 있어 일시 실패로 둔다
        (lambda: httpx.Response(200, json={"result": 1, "data": []}), "파싱", False),
    ],
)
@pytest.mark.asyncio
async def test_process_vod_records_failure(
    conn, respx_mock, response, reason_prefix, permanent
):
    """상세를 못 받거나 못 읽으면 False 를 주고 실패를 종류에 맞게 기록하는지."""
    _mock_detail(respx_mock, {1000: response})

    assert await vods.process_vod(conn, 1000, BID) is False

    failure = await _failure(conn, 1000)
    assert failure is not None
    reason, is_permanent, _, _ = failure
    assert reason.startswith(reason_prefix)
    assert is_permanent is permanent
    assert await _fetchall(conn, "SELECT vod_id FROM vods") == []


@pytest.mark.asyncio
async def test_process_vod_duplicate_is_false(conn, respx_mock):
    """이미 저장된 VOD 면 False 인지."""
    await _add_vod(conn, 1000)
    _mock_detail(respx_mock, {1000: _ok(1000)})

    assert await vods.process_vod(conn, 1000, BID) is False


# ---------------------------------------------------------------- collect_new_vods


@pytest.mark.asyncio
async def test_collect_new_vods(conn, respx_mock):
    """성공 케이스. DB 의 워터마크보다 새 VOD 만 상세를 받아 저장하는지."""
    await _add_vod(conn, 100)
    await _add_vod(
        conn, 200, bid="다른방송인"
    )  # 다른 스트리머는 워터마크에 안 들어간다
    _mock_list(respx_mock, {1: _page([102, 101, 100], last_page=1, total=3)})
    detail = _mock_detail(respx_mock, {101: _ok(101), 102: _ok(102)})

    saved = await vods.collect_new_vods(conn, BID)

    assert saved == 2
    assert _requested_ids(detail) == [101, 102]  # 오래된 것부터
    rows = await _fetchall(
        conn, "SELECT vod_id FROM vods WHERE bid = %s ORDER BY 1", (BID,)
    )
    assert rows == [(100,), (101,), (102,)]


@pytest.mark.asyncio
async def test_collect_new_vods_warns_when_db_has_fewer_than_api(
    conn, respx_mock, caplog
):
    """API 총계보다 처리를 마친 건수가 적으면 누락 의심 경고를 남기는지."""
    _mock_list(respx_mock, {1: _page([101], last_page=1, total=5)})
    _mock_detail(respx_mock, {101: _ok(101)})

    await vods.collect_new_vods(conn, BID)

    assert "누락 의심: 처리완료 1 < API 총계 5" in caplog.text


@pytest.mark.asyncio
async def test_collect_new_vods_counts_failures_as_accounted(conn, respx_mock, caplog):
    """해결 안 된 실패 기록도 처리를 마친 것으로 세어 경고하지 않는지."""
    await _add_failure(conn, 50)  # 영영 못 받는 VOD
    _mock_list(respx_mock, {1: _page([101], last_page=1, total=2)})
    _mock_detail(respx_mock, {101: _ok(101)})

    with caplog.at_level(logging.INFO):
        await vods.collect_new_vods(conn, BID)

    assert "누락 의심" not in caplog.text


# ---------------------------------------------------------------- sweep_missing_vods


@pytest.mark.asyncio
async def test_sweep_missing_vods(conn, respx_mock):
    """성공 케이스. 목록 전체에서 DB 에도 실패 기록에도 없는 VOD 만 받아 저장하는지."""
    await _add_vod(conn, 101)
    await _add_vod(conn, 103)
    await _add_failure(conn, 104, permanent=True)  # 실패 기록이 있으면 다시 안 받는다
    _mock_list(respx_mock, {1: _page([105, 104, 103, 102, 101], last_page=1)})
    detail = _mock_detail(respx_mock, {102: _ok(102), 105: _ok(105)})

    saved = await vods.sweep_missing_vods(conn, BID)

    assert saved == 2
    assert _requested_ids(detail) == [102, 105]


# ---------------------------------------------------------------- retry_failures


@pytest.mark.asyncio
async def test_retry_failures(conn, respx_mock):
    """성공 케이스. 해결 안 된 일시 실패만 다시 받아 저장하고 해결 처리하는지."""
    await _add_failure(conn, 201)  # 다시 받는다
    await _add_failure(conn, 202, permanent=True)  # 영구 실패는 안 받는다
    await _add_failure(conn, 203, resolved=True)  # 이미 해결된 건 안 받는다
    detail = _mock_detail(respx_mock, {201: _ok(201)})

    saved = await vods.retry_failures(conn)

    assert saved is not None
    assert saved == 1
    assert _requested_ids(detail) == [201]
    failure = await _failure(conn, 201)
    assert failure is not None
    assert failure[3] is True


@pytest.mark.asyncio
async def test_retry_failures_nothing_to_retry(conn, respx_mock):
    """재시도할 게 없으면 요청 없이 0 인지."""
    detail = _mock_detail(respx_mock, {})

    assert await vods.retry_failures(conn) == 0
    assert detail.call_count == 0
