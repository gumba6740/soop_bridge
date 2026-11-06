"""
pytest tests/core/soop_clients/live_clients/test_vod_client.py
"""

import httpx
import pytest
import respx

from app.core.soop_clients.live_clients.shared import LIVE_API_URL, PLAY_ORIGIN
from app.core.soop_clients.live_clients.vod_client import (
    get_aid,
    get_hls_url,
    get_view_url,
)

RMD = "https://rmd.example.com"
ASSIGN_URL = f"{RMD}/broad_stream_assign.html"
VIEW_URL = "https://cdn.example.com/live/abc/playlist.m3u8"
INFO = {"BNO": "123", "RMD": RMD, "CDN": "gs_cdn"}


def _form(request: httpx.Request) -> dict[str, str]:
    """form 형식(a=1&b=2)으로 보낸 본문을 dict로."""
    return dict(httpx.QueryParams(request.content.decode()))


def _mock_aid(respx_mock: respx.MockRouter, channel: dict[str, object]) -> respx.Route:
    """get_aid가 받을 CHANNEL을 정한다."""
    return respx_mock.post(LIVE_API_URL).mock(
        return_value=httpx.Response(200, json={"CHANNEL": channel})
    )


def _mock_assign(respx_mock: respx.MockRouter, data: dict[str, object]) -> respx.Route:
    """get_view_url이 받을 배정 서버 응답을 정한다."""
    return respx_mock.get(ASSIGN_URL).mock(return_value=httpx.Response(200, json=data))


# ------------------------------------------------------ get_aid


@pytest.mark.asyncio
async def test_get_aid_ok(respx_mock):
    route = _mock_aid(respx_mock, {"RESULT": 1, "AID": "aid-token"})

    assert await get_aid("bid1", "123", "hd") == "aid-token"
    req = route.calls.last.request
    form = _form(req)
    assert (form["type"], form["bid"], form["bno"], form["pwd"], form["quality"]) == (
        "aid",
        "bid1",
        "123",
        "",
        "hd",
    )
    assert req.headers["Referer"] == f"{PLAY_ORIGIN}/bid1"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "channel",
    [
        {"RESULT": 0, "AID": "aid-token"},  # 발급 실패
        {"RESULT": 1},  # AID 없음
        {"RESULT": 1, "AID": ""},  # AID가 빈 문자열
    ],
)
async def test_get_aid_returns_none(respx_mock, channel):
    _mock_aid(respx_mock, channel)

    assert await get_aid("bid1", "123", "hd") is None


# ------------------------------------------------------ get_view_url


@pytest.mark.asyncio
async def test_get_view_url_ok(respx_mock):
    route = _mock_assign(respx_mock, {"view_url": VIEW_URL})

    assert await get_view_url(RMD, "gs_cdn", "123", "hd") == VIEW_URL
    assert dict(route.calls.last.request.url.params) == {
        "return_type": "gs_cdn_pc_web",
        "broad_key": "123-common-hd-hls",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("cdn", "return_type"),
    [
        ("gs_cdn", "gs_cdn_pc_web"),
        ("lg_cdn", "lg_cdn_pc_web"),
        ("gs_cdn_pc_app", "gs_cdn_pc_web"),  # "gs_cdn"이 들어 있으면 바꾼다
        ("other_cdn", "other_cdn"),  # 모르는 값은 그대로
    ],
)
async def test_get_view_url_cdn_mapping(respx_mock, cdn, return_type):
    route = _mock_assign(respx_mock, {"view_url": VIEW_URL})

    await get_view_url(RMD, cdn, "123", "hd")

    assert route.calls.last.request.url.params["return_type"] == return_type


@pytest.mark.asyncio
@pytest.mark.parametrize("data", [{}, {"view_url": ""}, {"stream_status": "off"}])
async def test_get_view_url_without_view_url(respx_mock, data):
    _mock_assign(respx_mock, data)

    assert await get_view_url(RMD, "gs_cdn", "123", "hd") is None


@pytest.mark.asyncio
async def test_get_view_url_raises_on_error(respx_mock):
    respx_mock.get(ASSIGN_URL).mock(return_value=httpx.Response(500))

    with pytest.raises(httpx.HTTPStatusError):
        await get_view_url(RMD, "gs_cdn", "123", "hd")


# ------------------------------------------------------ get_hls_url


@pytest.mark.asyncio
async def test_get_hls_url_joins_view_url_and_aid(respx_mock):
    _mock_aid(respx_mock, {"RESULT": 1, "AID": "aid-token"})
    _mock_assign(respx_mock, {"view_url": VIEW_URL})

    assert await get_hls_url("bid1", INFO, "hd") == f"{VIEW_URL}?aid=aid-token"


@pytest.mark.asyncio
async def test_get_hls_url_keeps_view_url_query(respx_mock):
    _mock_aid(respx_mock, {"RESULT": 1, "AID": "aid-token"})
    _mock_assign(respx_mock, {"view_url": f"{VIEW_URL}?token=t"})

    url = await get_hls_url("bid1", INFO, "hd")

    assert url is not None
    assert dict(httpx.URL(url).params) == {"token": "t", "aid": "aid-token"}


@pytest.mark.asyncio
@pytest.mark.parametrize("missing", ["BNO", "RMD", "CDN"])
async def test_get_hls_url_missing_info_returns_none(respx_mock, missing):
    # 요청을 하나도 등록하지 않았다. 요청을 보내면 respx가 에러를 낸다.
    info = {k: v for k, v in INFO.items() if k != missing}

    assert await get_hls_url("bid1", info, "hd") is None


@pytest.mark.asyncio
async def test_get_hls_url_without_aid_returns_none(respx_mock):
    _mock_aid(respx_mock, {"RESULT": 0})
    _mock_assign(respx_mock, {"view_url": VIEW_URL})

    assert await get_hls_url("bid1", INFO, "hd") is None


@pytest.mark.asyncio
async def test_get_hls_url_without_view_url_returns_none(respx_mock):
    _mock_aid(respx_mock, {"RESULT": 1, "AID": "aid-token"})
    _mock_assign(respx_mock, {})

    assert await get_hls_url("bid1", INFO, "hd") is None
