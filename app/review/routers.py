from typing import Annotated

from fastapi import APIRouter, Query
import xml.etree.ElementTree as ET

from app.vod.soop_api import get_vod_chat


from app.vod.schemas import (
    ChatMessage,
    QualityOption,
    VodChatResponse,
    VodDetailResponse,
    VodFile,
    VodItem,
    VodListResponse,
)
from app.vod.soop_api import get_vod_detail, get_vod_list

router = APIRouter(prefix="/vod", tags=["vod"])


@router.get("/streams/{bid}/vods", response_model=VodListResponse)
async def list_vods(
    bid: str,
    page: Annotated[int, Query(ge=1)] = 1,
    per_page: Annotated[int, Query(ge=1, le=60)] = 20,
) -> VodListResponse:
    """스트리머의 VOD 목록."""
    data = await get_vod_list(bid, page, per_page)

    raw_vods = data.get("data", [])
    total = data.get("TOTAL", len(raw_vods))

    vods = []
    for v in raw_vods:
        vods.append(
            VodItem(
                vod_id=v.get("title_no", 0),
                title=v.get("title_name", ""),
                duration=v.get("duration"),
                thumbnail=v.get("thumbnail"),
                reg_date=v.get("reg_date"),
            )
        )

    return VodListResponse(
        bid=bid,
        page=page,
        total_count=total,
        vods=vods,
    )


@router.get("/vods/{vod_id}", response_model=VodDetailResponse)
async def vod_detail(vod_id: int) -> VodDetailResponse:
    """VOD 상세 — 파트별 HLS URL, 채팅 URL 포함."""
    data = await get_vod_detail(vod_id)
    inner = data.get("data", {})

    files = []
    for f in inner.get("files", []):
        quality_info = [
            QualityOption(
                label=q["label"],
                resolution=q.get("resolution", ""),
                bitrate=q.get("bitrate", ""),
                file=q["file"],
            )
            for q in f.get("quality_info", [])
        ]

        files.append(
            VodFile(
                file_order=f.get("file_order", 0),
                hls_url=f.get("file", ""),
                chat_url=f.get("chat"),
                duration=f.get("duration", 0),
                file_start=f.get("file_start"),
                quality_info=quality_info,
            )
        )

    return VodDetailResponse(
        vod_id=vod_id,
        title=inner.get("full_title", ""),
        bj_id=inner.get("bj_id", ""),
        total_duration=inner.get("total_file_duration", 0),
        broad_start=inner.get("broad_start"),
        thumbnail=inner.get("thumb"),
        files=files,
    )


@router.get("/vods/{vod_id}/chat", response_model=VodChatResponse)
async def vod_chat(
    vod_id: int,
    start_time: Annotated[int, Query(ge=0)] = 0,
) -> VodChatResponse:
    """VOD 채팅 데이터 (300초 단위)."""
    detail = await get_vod_detail(vod_id)
    inner = detail.get("data", {})
    files = inner.get("files", [])
    if not files:
        return VodChatResponse(vod_id=vod_id, start_time=start_time, messages=[])

    # 1. start_time이 어느 파트에 해당하는지 찾기
    offset = 0  # 이전 파트들의 누적 시간(초)
    target_file = None
    for f in files:
        duration_sec = f.get("duration", 0) / 1000
        if start_time < offset + duration_sec:
            target_file = f
            break
        offset += duration_sec
    else:
        # start_time이 전체 VOD 길이를 초과
        target_file = files[-1]
        offset = sum(f.get("duration", 0) / 1000 for f in files[:-1])

    chat_url = target_file.get("chat", "")
    if not chat_url:
        return VodChatResponse(vod_id=vod_id, start_time=start_time, messages=[])

    # 2. 파트 내 로컬 시간으로 변환해서 요청
    local_start = start_time - offset
    xml_text = await get_vod_chat(chat_url, int(local_start))

    # 3. 파싱하면서 t를 VOD 전체 기준으로 변환
    root = ET.fromstring(xml_text)
    messages = []
    for chat in root.findall("chat"):
        messages.append(
            ChatMessage(
                message=chat.findtext("m", ""),
                user_id=chat.findtext("u", ""),
                nickname=chat.findtext("n", ""),
                time=float(chat.findtext("t", "0")) + offset,
            )
        )

    return VodChatResponse(
        vod_id=vod_id,
        start_time=start_time,
        messages=messages,
    )
