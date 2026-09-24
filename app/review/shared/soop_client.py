"""SOOP API 클라이언트.

실패 계약
---------
- 영구 실패(삭제/비공개 VOD 등)  -> None 반환. 재시도해도 소용없음.
- 일시 실패(429/5xx/타임아웃 등) -> 예외 전파. 호출부에서 재시도 대상으로 기록.

이 구분이 있어야 워터마크 방식에서 "영영 못 가져오는 VOD" 때문에
재시도 큐가 무한히 쌓이는 걸 막을 수 있다.
"""

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

VOD_LIST_URL = "https://chapi.sooplive.com/api/{bid}/vods/review"
VOD_DETAIL_URL = "https://api.m.sooplive.com/station/video/a/view"

CHAT_HEADERS = {
    "Origin": "https://vod.sooplive.com",
    "Referer": "https://vod.sooplive.com/",
}

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
}

# 영구 실패로 간주할 HTTP 상태
PERMANENT_STATUSES = frozenset({400, 401, 403, 404, 410})

_client: httpx.AsyncClient | None = None


def get_client() -> httpx.AsyncClient:
    """모듈 전역 공유 클라이언트.

    요청마다 AsyncClient를 새로 만들면 매번 TLS 핸드셰이크가 발생하고
    커넥션 풀 이득이 없어짐.
    """
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            headers=DEFAULT_HEADERS,
            timeout=httpx.Timeout(15.0, connect=5.0),
            limits=httpx.Limits(max_keepalive_connections=10, max_connections=20),
            transport=httpx.AsyncHTTPTransport(retries=2),  # 커넥션 재시도
            follow_redirects=True,
        )
    return _client


async def aclose() -> None:
    global _client
    if _client is not None and not _client.is_closed:
        await _client.aclose()
    _client = None


def _body_failed(data: dict[str, Any]) -> str | None:
    """응답 상태코드 200인데 본문에 실패가 담긴 경우를 잡아낸다.
    없는 vod_id로 요청, 시청에 일정한 자격이 필요한 영상의 경우가 이렇다.
    """
    if not isinstance(data, dict):
        return "응답이 dict가 아님"

    result = data.get("result")
    if isinstance(result, int) and result <= 0:
        return f"result={result}"

    return None


async def get_vod_list(
    bid: str, page: int = 1, per_page: int = 60
) -> dict[str, Any]:
    """스트리머의 VOD 목록을 가져온다 (reg_date 최신순).

    Raises:
        httpx.HTTPStatusError: 목록 조회 실패. 목록은 영구 실패 개념이 없으므로
            그대로 전파해서 해당 bid 동기화를 중단시킨다.
    """
    params = {
        "page": page,
        "per_page": per_page,
        "orderby": "reg_date",
    }

    resp = await get_client().get(VOD_LIST_URL.format(bid=bid), params=params)
    resp.raise_for_status()
    data: dict[str, Any] = resp.json()
    return data


async def get_vod_detail(vod_id: int) -> dict[str, Any] | None:
    """VOD 상세 정보를 가져온다.

    HLS 영상 URL, 채팅 URL, 방송 시작 시각 등이 포함된다.

    Returns:
        정상 응답 dict. 영구 실패(삭제/비공개 등)면 None.

    Raises:
        httpx.HTTPStatusError | httpx.TransportError: 일시 실패. 재시도 대상.
    """
    form_data = {"nTitleNo": str(vod_id)}

    try:
        resp = await get_client().post(VOD_DETAIL_URL, data=form_data)
        resp.raise_for_status()
    except httpx.HTTPStatusError as e:
        if e.response.status_code in PERMANENT_STATUSES:
            logger.info(
                "[PERM] vod_id=%d status=%d", vod_id, e.response.status_code
            )
            return None
        raise  # 429 / 5xx -> 재시도 대상

    try:
        data: dict[str, Any] = resp.json()
    except ValueError:
        logger.warning("[PERM] vod_id=%d JSON 파싱 불가", vod_id)
        return None

    reason = _body_failed(data)  # 200OK로 왔지만 본문에 실패코드가 담겨있을 때 걸러내기
    if reason is not None:
        logger.info("[PERM] vod_id=%d 본문 실패: %s", vod_id, reason)
        return None

    return data


async def get_vod_chat(chat_url: str, start_time: int = 0) -> str:
    """VOD 채팅 데이터를 가져온다 (300초 단위).

    Args:
        chat_url: VOD 상세 응답의 chat_url (쿼리스트링 포함 여부와 무관)
        start_time: 시작 시간 (초)

    Returns:
        XML 문자열
    """
    # f-string으로 "&startTime=" 을 붙이면 chat_url에 '?'가 없을 때 깨진다.
    # params로 넘기면 httpx가 기존 쿼리스트링에 안전하게 병합한다.
    resp = await get_client().get(
        chat_url,  # soop의 vod 상세 정보 응답 혹은 db에서 가져와서 넣어준다.
        params={"startTime": start_time},
        headers=CHAT_HEADERS,
    )
    resp.raise_for_status()
    return resp.text