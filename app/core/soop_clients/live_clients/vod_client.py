"""SOOP 라이브 영상 클라이언트.

접속 정보(shared.get_live_info의 CHANNEL)에 있는 BNO, RMD, CDN으로 화질 하나의 재생 주소를 만든다.

1. player_live_api.php (type=aid) (바디: bid, BNO, 화질)
    -> 화질별 재생 토큰(aid)
2. {RMD}/broad_stream_assign.html (쿼리 스트링: CDN 매핑값, BNO와 화질로 만든 broad_key)
    -> m3u8 주소(view_url)
3. (요청 아님) 둘을 합친 "view_url?aid=aid"가 재생 주소다.

540p 초과 화질은 고화질 프로그램 전용이라, 웹 경로(player_type=html5)로는
1080p 등을 요청해도 540p로 나온다.

m3u8과 영상 조각을 받을 때도 Referer/Origin 헤더(shared.play_headers)가 필요하다.
"""

import logging
from typing import Any

import httpx

from app.core.soop_clients.live_clients.shared import (
    RESULT_OK,
    get_client,
    post_live_api,
)

logger = logging.getLogger(__name__)

# 접속 정보의 CDN 값을 broad_stream_assign의 return_type으로 바꾼다.
CDN_TYPE_MAPPING = {
    "gs_cdn": "gs_cdn_pc_web",
    "lg_cdn": "lg_cdn_pc_web",
}


async def get_aid(bid: str, bno: str, quality: str) -> str | None:
    """화질별 재생 토큰(aid)을 받는다."""
    channel = await post_live_api(
        bid,
        {"type": "aid", "bid": bid, "bno": bno, "pwd": "", "quality": quality},
    )
    if int(channel.get("RESULT", 0)) != RESULT_OK:
        logger.info("[PERM] bid=%s quality=%s aid 발급 실패", bid, quality)
        return None

    aid: str | None = channel.get("AID")
    return aid or None


async def get_view_url(rmd: str, cdn: str, bno: str, quality: str) -> str | None:
    """배정 서버에서 실제 m3u8 주소(view_url)를 받는다."""
    # cdn 값에 "gs_cdn"이 들어 있으면 "gs_cdn_pc_web"으로, 없으면 그대로 쓴다.
    return_type = next((v for k, v in CDN_TYPE_MAPPING.items() if k in cdn), cdn)

    resp = await get_client().get(
        f"{rmd}/broad_stream_assign.html",
        params={
            "return_type": return_type,
            "broad_key": f"{bno}-common-{quality}-hls",
        },
    )
    resp.raise_for_status()
    data: dict[str, Any] = resp.json()

    view_url: str | None = data.get("view_url")
    if not view_url:
        logger.info(
            "[PERM] bno=%s quality=%s view_url 없음 (stream_status=%s)",
            bno, quality, data.get("stream_status"),
        )
        return None
    return view_url


async def get_hls_url(bid: str, info: dict[str, Any], quality: str) -> str | None:
    """화질 하나의 재생 url"""
    bno, rmd, cdn = info.get("BNO"), info.get("RMD"), info.get("CDN")
    if not (bno and rmd and cdn):
        return None
    aid = await get_aid(bid, bno, quality)
    view_url = await get_view_url(rmd, cdn, bno, quality)
    if not (aid and view_url):
        return None
    return str(httpx.URL(view_url).copy_merge_params({"aid": aid}))