"""SOOP 방송 현황 클라이언트.

- 브라우저 User-Agent가 없으면 404를 준다. (shared.get_client의 기본 헤더에 들어 있다)
- 방송 중이 아니면 broad가 null로 온다.
- station.broad_start는 "마지막 방송의 시작 시각"이라, 방송 중이 아니어도 값이 있다.
    broad가 있을 때만 이번 방송의 시작 시각으로 쓴다.
- 시각 문자열("2026-10-04 18:01:57")에는 시간대가 없고, 값은 한국시간이다.

실패 계약 (review_client와 같음)
---------
- 영구 실패(방송 중 아님) -> None 반환.
- 일시 실패(429/5xx/타임아웃 등) -> 예외 전파.
"""

import logging
from typing import Any

from app.core.soop_clients.live_clients.shared import get_client

logger = logging.getLogger(__name__)

BROAD_DETAIL_URL = "https://chapi.sooplive.com/api/{bid}/station"


async def get_broad_detail(bid: str) -> dict[str, Any] | None:
    """시청자 수, 방송 시작 시각, 방송 제목, 방문자 수 등 방송 현황 api"""
    resp = await get_client().get(BROAD_DETAIL_URL.format(bid=bid))
    resp.raise_for_status()
    data: dict[str, Any] = resp.json()

    if data.get("broad") is None:
        logger.debug("[PERM] bid=%s 방송 중이 아님 (broad=null)", bid)
        return None
    return data
