"""
pytest tests/core/soop_clients/live_clients/test_broad_client.py
"""

import httpx
import pytest

from app.core.soop_clients.live_clients.broad_client import (
    BROAD_DETAIL_URL,
    get_broad_detail,
)
from app.core.soop_clients.live_clients.shared import DEFAULT_HEADERS

DETAIL_URL = BROAD_DETAIL_URL.format(bid="bid1")


@pytest.mark.asyncio
async def test_get_broad_detail_live(respx_mock):
    data = {
        "broad": {"broad_no": 123, "broad_title": "제목", "current_sum_viewer": 10},
        "station": {"broad_start": "2026-10-04 18:01:57"},
    }
    route = respx_mock.get(DETAIL_URL).mock(return_value=httpx.Response(200, json=data))

    assert await get_broad_detail("bid1") == data
    # User-Agent가 없으면 SOOP이 404를 준다.
    req = route.calls.last.request
    assert req.headers["User-Agent"] == DEFAULT_HEADERS["User-Agent"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "data",
    [
        # 방송 중이 아니면 broad가 null. station에는 지난 방송 값이 남아 있다.
        {"broad": None, "station": {"broad_start": "2026-10-01 10:00:00"}},
        {"station": {}},  # broad 키가 아예 없음
    ],
)
async def test_get_broad_detail_not_live_returns_none(respx_mock, data):
    respx_mock.get(DETAIL_URL).mock(return_value=httpx.Response(200, json=data))

    assert await get_broad_detail("bid1") is None


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [404, 429, 500])
async def test_get_broad_detail_raises_on_error(respx_mock, status):
    respx_mock.get(DETAIL_URL).mock(return_value=httpx.Response(status))

    with pytest.raises(httpx.HTTPStatusError):
        await get_broad_detail("bid1")
