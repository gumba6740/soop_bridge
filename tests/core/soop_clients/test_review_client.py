"""app/core/soop_clients/review_client.py 테스트.

실제 SOOP에 요청하지 않는다.
respx가 httpx의 요청을 가로채서 등록해 둔 가짜 응답을 준다.
클라이언트는 get_client()가 만든 진짜를 쓰므로 기본 헤더 같은 설정도 함께 확인된다.

uv run pytest tests/core/soop_clients/test_review_client.py
"""

import httpx
import pytest

from app.core.soop_clients import review_client
from app.core.soop_clients.review_client import (
    CHAT_HEADERS,
    DEFAULT_HEADERS,
    PERMANENT_STATUSES,
    VOD_DETAIL_URL,
    VOD_LIST_URL,
    _body_failed,
    aclose,
    get_client,
    get_vod_chat,
    get_vod_detail,
    get_vod_list,
)

LIST_URL = VOD_LIST_URL.format(bid="streamer1")
CHAT_URL = "https://chat.example.com/chat"


# ------------------------------------------------------ _body_failed


def test_body_failed_ok():
    assert _body_failed({"result": 1, "data": {}}) is None


def test_body_failed_without_result():
    assert _body_failed({"data": {}}) is None


@pytest.mark.parametrize("result", [0, -1])
def test_body_failed_result_not_positive(result):
    assert _body_failed({"result": result}) == f"result={result}"


def test_body_failed_result_not_int():
    # 숫자가 아닌 result는 실패로 보지 않는다.
    assert _body_failed({"result": "0"}) is None


def test_body_failed_not_dict():
    assert _body_failed([1, 2]) == "응답이 dict가 아님"  # type: ignore[arg-type]


# ------------------------------------------------------ get_client, aclose


@pytest.mark.asyncio
async def test_get_client_reuses_and_recreates_after_aclose():
    first = get_client()
    assert get_client() is first  # 두 번째부터는 같은 클라이언트

    await aclose()
    assert first.is_closed
    assert review_client._client is None

    second = get_client()  # 닫힌 뒤에는 새로 만든다
    assert second is not first
    assert second is get_client()


# ------------------------------------------------------ get_vod_list


@pytest.mark.asyncio
async def test_get_vod_list_request_and_response(respx_mock):
    route = respx_mock.get(LIST_URL).mock(
        return_value=httpx.Response(200, json={"data": [{"title_no": 1}]})
    )

    data = await get_vod_list("streamer1", page=2, per_page=30)

    assert data == {"data": [{"title_no": 1}]}
    req = route.calls.last.request
    assert dict(req.url.params) == {
        "page": "2",
        "per_page": "30",
        "orderby": "reg_date",
    }
    # get_client()의 기본 헤더가 붙는다. 없으면 SOOP이 막는다.
    assert req.headers["User-Agent"] == DEFAULT_HEADERS["User-Agent"]


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [404, 500])
async def test_get_vod_list_raises_on_error(respx_mock, status):
    # 목록에는 영구 실패가 없다. 404도 예외로 올린다.
    respx_mock.get(LIST_URL).mock(return_value=httpx.Response(status))

    with pytest.raises(httpx.HTTPStatusError):
        await get_vod_list("streamer1")


# ------------------------------------------------------ get_vod_detail


@pytest.mark.asyncio
async def test_get_vod_detail_ok(respx_mock):
    route = respx_mock.post(VOD_DETAIL_URL).mock(
        return_value=httpx.Response(200, json={"result": 1, "data": {"title": "t"}})
    )

    data = await get_vod_detail(123)

    assert data == {"result": 1, "data": {"title": "t"}}
    assert route.calls.last.request.content == b"nTitleNo=123"  # form 형식


@pytest.mark.asyncio
@pytest.mark.parametrize("status", sorted(PERMANENT_STATUSES))
async def test_get_vod_detail_permanent_status_returns_none(respx_mock, status):
    respx_mock.post(VOD_DETAIL_URL).mock(return_value=httpx.Response(status))

    assert await get_vod_detail(123) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [429, 500, 503])
async def test_get_vod_detail_temporary_status_raises(respx_mock, status):
    respx_mock.post(VOD_DETAIL_URL).mock(return_value=httpx.Response(status))

    with pytest.raises(httpx.HTTPStatusError):
        await get_vod_detail(123)


@pytest.mark.asyncio
async def test_get_vod_detail_transport_error_raises(respx_mock):
    respx_mock.post(VOD_DETAIL_URL).mock(side_effect=httpx.ConnectError("연결 실패"))

    with pytest.raises(httpx.ConnectError):
        await get_vod_detail(123)


@pytest.mark.asyncio
async def test_get_vod_detail_invalid_json_returns_none(respx_mock):
    respx_mock.post(VOD_DETAIL_URL).mock(
        return_value=httpx.Response(200, text="<html>")
    )

    assert await get_vod_detail(123) is None


@pytest.mark.asyncio
async def test_get_vod_detail_body_failed_returns_none(respx_mock):
    # 200으로 왔지만 본문에 실패가 담긴 경우.
    respx_mock.post(VOD_DETAIL_URL).mock(
        return_value=httpx.Response(200, json={"result": -1})
    )

    assert await get_vod_detail(123) is None


# ------------------------------------------------------ get_vod_chat


@pytest.mark.asyncio
async def test_get_vod_chat_keeps_existing_query(respx_mock):
    route = respx_mock.get(CHAT_URL).mock(
        return_value=httpx.Response(200, text="<root></root>")
    )

    text = await get_vod_chat(f"{CHAT_URL}?rowKey=abc", 300)

    assert text == "<root></root>"
    req = route.calls.last.request
    # rowKey가 사라지지 않고 startTime이 더해져야 한다.
    assert dict(req.url.params) == {"rowKey": "abc", "startTime": "300"}
    assert req.headers["Origin"] == CHAT_HEADERS["Origin"]
    assert req.headers["Referer"] == CHAT_HEADERS["Referer"]


@pytest.mark.asyncio
async def test_get_vod_chat_without_query(respx_mock):
    route = respx_mock.get(CHAT_URL).mock(return_value=httpx.Response(200, text=""))

    await get_vod_chat(CHAT_URL)

    assert dict(route.calls.last.request.url.params) == {"startTime": "0"}  # 기본값 0
