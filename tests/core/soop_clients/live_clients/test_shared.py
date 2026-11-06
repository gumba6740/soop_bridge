"""
pytest tests/core/soop_clients/live_clients/test_shared.py
"""

import httpx
import pytest
import respx

from app.core.soop_clients.live_clients import shared
from app.core.soop_clients.live_clients.shared import (
    DEFAULT_HEADERS,
    LIVE_API_COMMON,
    LIVE_API_URL,
    PLAY_ORIGIN,
    LiveStatus,
    aclose,
    get_client,
    get_live_info,
    play_headers,
    post_live_api,
)


def _form(request: httpx.Request) -> dict[str, str]:
    """form 형식(a=1&b=2)으로 보낸 본문을 dict로."""
    return dict(httpx.QueryParams(request.content.decode()))


# ------------------------------------------------------ get_client, aclose


@pytest.mark.asyncio
async def test_get_client_reuses_and_recreates_after_aclose():
    first = get_client()
    assert get_client() is first

    await aclose()
    assert first.is_closed

    second = get_client()
    assert second is not first
    assert get_client() is second


# ------------------------------------------------------ 요청 수 세기


@pytest.mark.asyncio
async def test_request_counts_by_last_path_segment(respx_mock):
    respx_mock.post(LIVE_API_URL).mock(return_value=httpx.Response(200, json={}))
    respx_mock.get("https://chapi.sooplive.com/api/bid1/station").mock(
        return_value=httpx.Response(200, json={})
    )

    await post_live_api("bid1", {})
    await post_live_api("bid1", {})
    await get_client().get("https://chapi.sooplive.com/api/bid1/station")

    # 주소의 마지막 부분별로 센다.
    assert shared.request_counts == {"player_live_api.php": 2, "station": 1}


# ------------------------------------------------------ play_headers


def test_play_headers():
    assert play_headers("bid1") == {
        "Referer": f"{PLAY_ORIGIN}/bid1",
        "Origin": PLAY_ORIGIN,
    }


# ------------------------------------------------------ post_live_api


@pytest.mark.asyncio
async def test_post_live_api_request_and_channel(respx_mock):
    route = respx_mock.post(LIVE_API_URL).mock(
        return_value=httpx.Response(200, json={"CHANNEL": {"RESULT": 1}})
    )

    channel = await post_live_api("bid1", {"type": "live", "bid": "bid1"})

    assert channel == {"RESULT": 1}
    req = route.calls.last.request
    # 공통 값과 넘긴 값이 합쳐져 form으로 간다.
    assert _form(req) == {**LIVE_API_COMMON, "type": "live", "bid": "bid1"}
    assert req.headers["Referer"] == f"{PLAY_ORIGIN}/bid1"
    assert req.headers["Origin"] == PLAY_ORIGIN
    assert req.headers["User-Agent"] == DEFAULT_HEADERS["User-Agent"]


@pytest.mark.asyncio
async def test_post_live_api_without_channel(respx_mock):
    respx_mock.post(LIVE_API_URL).mock(return_value=httpx.Response(200, json={}))

    assert await post_live_api("bid1", {}) == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [404, 500])
async def test_post_live_api_raises_on_error(respx_mock, status):
    respx_mock.post(LIVE_API_URL).mock(return_value=httpx.Response(status))

    with pytest.raises(httpx.HTTPStatusError):
        await post_live_api("bid1", {})


# ------------------------------------------------------ get_live_info


def _mock_channel(
    respx_mock: respx.MockRouter, channel: dict[str, object]
) -> respx.Route:
    """get_live_info가 받을 CHANNEL을 정한다."""
    return respx_mock.post(LIVE_API_URL).mock(
        return_value=httpx.Response(200, json={"CHANNEL": channel})
    )


@pytest.mark.asyncio
async def test_get_live_info_live(respx_mock):
    channel = {"RESULT": 1, "BNO": "123", "BJNICK": "닉네임"}
    route = _mock_channel(respx_mock, channel)

    status, info = await get_live_info("bid1")

    assert status == LiveStatus.LIVE
    assert info == channel
    form = _form(route.calls.last.request)
    assert (form["type"], form["bid"], form["pwd"]) == ("live", "bid1", "")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("result", "expected"),
    [
        (0, LiveStatus.OFFLINE),
        (-6, LiveStatus.LOGIN_REQUIRED),
        (-14, LiveStatus.SUB_PLUS),
        (-99, LiveStatus.UNKNOWN),
    ],
)
async def test_get_live_info_not_watchable(respx_mock, result, expected):
    _mock_channel(respx_mock, {"RESULT": result})

    assert await get_live_info("bid1") == (expected, None)


@pytest.mark.asyncio
async def test_get_live_info_password(respx_mock):
    _mock_channel(respx_mock, {"RESULT": 1, "BPWD": "Y"})

    assert await get_live_info("bid1") == (LiveStatus.PASSWORD, None)


@pytest.mark.asyncio
async def test_get_live_info_result_as_string(respx_mock):
    # RESULT가 문자열로 와도 int()로 바꿔서 비교한다.
    _mock_channel(respx_mock, {"RESULT": "1"})

    status, _ = await get_live_info("bid1")
    assert status == LiveStatus.LIVE


@pytest.mark.asyncio
async def test_get_live_info_without_result_is_offline(respx_mock):
    # RESULT가 없으면 0(방송 중 아님)으로 본다.
    _mock_channel(respx_mock, {})

    assert await get_live_info("bid1") == (LiveStatus.OFFLINE, None)
