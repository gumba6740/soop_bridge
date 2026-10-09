"""SOOP 라이브 클라이언트 공통.

chat_client와 vod_client, broad_client가 같이 쓰는 것들: 
    httpx 클라이언트, 접속 정보(CHANNEL) 조회.

- 영구 실패(방송 중 아님, 로그인 필요, 비밀번호 방송 등) -> (status, None) 반환.
- 일시 실패(429/5xx/타임아웃 등) -> 예외 전파.
"""

from enum import StrEnum
import logging
from typing import Any
from collections import Counter

import httpx

logger = logging.getLogger(__name__)

LIVE_API_URL = "https://live.sooplive.com/afreeca/player_live_api.php"
PLAY_ORIGIN = "https://play.sooplive.com"  # Referer/Origin 헤더에 넣는 값

# type=live, type=aid 요청에 공통으로 들어가는 값
LIVE_API_COMMON = {
    "from_api": "0",
    "mode": "landing",
    "player_type": "html5",
    "stream_type": "common",
}

# player_live_api 응답의 CHANNEL.RESULT 값과 대조 용도.
RESULT_OK = 1
RESULT_OFFLINE = 0          # 방송 중 아님
RESULT_LOGIN_REQUIRED = -6  # 연령 제한 방송 등

class LiveStatus(StrEnum):
    LIVE = "live"
    OFFLINE = "offline"                 # RESULT 0
    LOGIN_REQUIRED = "login_required"   # RESULT -6. 연령 제한 등
    PASSWORD = "password"               # BPWD == "Y" (추정)
    UNKNOWN = "unknown"                 # 그 밖의 RESULT

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
}

_client: httpx.AsyncClient | None = None


def get_client() -> httpx.AsyncClient:
    """모듈 전역 공유 클라이언트."""
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            headers=DEFAULT_HEADERS,
            timeout=httpx.Timeout(15.0, connect=5.0),
            limits=httpx.Limits(max_keepalive_connections=10, max_connections=20),
            transport=httpx.AsyncHTTPTransport(retries=2),
            follow_redirects=True,
            event_hooks={"request": [_count_request]},
        )
    return _client


# SOOP에 보낸 요청 수. 주소의 마지막 부분별로 센다. 읽고 비우는 건 collector가 한다.
request_counts: Counter[str] = Counter()

async def _count_request(request: httpx.Request) -> None:
    request_counts[request.url.path.rsplit("/", 1)[-1]] += 1


async def aclose() -> None:
    global _client
    if _client is not None and not _client.is_closed:
        await _client.aclose()
    _client = None


def play_headers(bid: str) -> dict[str, str]:
    """브라우저에서 방송 페이지를 연 것처럼 보이게 하는 헤더."""
    return {"Referer": f"{PLAY_ORIGIN}/{bid}", "Origin": PLAY_ORIGIN}


async def post_live_api(bid: str, data: dict[str, str]) -> dict[str, Any]:
    # soop이 form 형식으로 받아서 json= 대신 data=로 넘긴다.
    resp = await get_client().post(
        LIVE_API_URL,
        data={**LIVE_API_COMMON, **data},
        headers=play_headers(bid),
    )
    resp.raise_for_status()
    channel: dict[str, Any] = resp.json().get("CHANNEL", {})
    return channel


async def get_live_info(bid: str) -> tuple[LiveStatus, dict[str, Any] | None]:
    """방송 상태와 접속 정보(CHANNEL)를 함께 돌려준다.
    볼 수 있는 방송일 때만 CHANNEL이 있다
    """
    channel = await post_live_api(bid, {"type": "live", "bid": bid, "pwd": ""})

    result = int(channel.get("RESULT", 0))
    if result == RESULT_LOGIN_REQUIRED:
        logger.info("[PERM] bid=%s 로그인이 필요한 방송", bid)
        return LiveStatus.LOGIN_REQUIRED, None
    if result == RESULT_OFFLINE:
        # collector가 주기적으로 확인하면 계속 찍히므로 debug.
        logger.debug("[PERM] bid=%s 방송 중이 아님", bid)
        return LiveStatus.OFFLINE, None
    if result != RESULT_OK:
        logger.warning("[PERM] bid=%s 알 수 없는 RESULT=%d", bid, result)
        return LiveStatus.UNKNOWN, None
    if channel.get("BPWD") == "Y":
        logger.info("[PERM] bid=%s 비밀번호 방송", bid)
        return LiveStatus.PASSWORD, None

    return LiveStatus.LIVE, channel
