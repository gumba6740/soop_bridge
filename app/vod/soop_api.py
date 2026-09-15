"""SOOP VOD API 클라이언트.

방송이 끝난 후 다시보기(VOD) 목록과 상세 정보를 가져온다.
상세 정보에는 HLS 영상 URL과 채팅 URL이 포함되어 있다.
"""

from typing import Any

import httpx

# SOOP VOD 관련 엔드포인트
VOD_LIST_URL = "https://chapi.sooplive.com/api/{bid}/vods/review"
VOD_DETAIL_URL = "https://api.m.sooplive.com/station/video/a/view"


async def get_vod_list(
    bid: str, page: int = 1, per_page: int = 20
) -> dict[str, Any]:
    """스트리머의 VOD 목록을 가져온다.

    Args:
        bid: 스트리머 ID
        page: 페이지 번호
        per_page: 페이지당 VOD 수

    Returns:
        SOOP API 응답 (VOD 목록 포함)
    """
    params = {
        "page": page,
        "per_page": per_page,
        "orderby": "reg_date",
    }

    async with httpx.AsyncClient() as client:
        resp = await client.get(
            VOD_LIST_URL.format(bid=bid),
            params=params,
        )
    resp.raise_for_status()
    data: dict[str, Any] = resp.json()
    return data


async def get_vod_detail(vod_id: int) -> dict[str, Any]:
    """VOD 상세 정보를 가져온다.

    HLS 영상 URL, 채팅 URL, 방송 시작 시각 등이 포함된다.

    Args:
        vod_id: VOD 고유번호 (nTitleNo)

    Returns:
        SOOP API 응답 (영상 URL, 채팅 URL 등)
    """
    form_data = {"nTitleNo": str(vod_id)}

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            VOD_DETAIL_URL,
            data=form_data,
        )
    resp.raise_for_status()
    data: dict[str, Any] = resp.json()
    return data