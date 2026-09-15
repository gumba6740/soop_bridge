from typing import Annotated

from fastapi import APIRouter, Query

from app.vod.schemas import (
    QualityOption,
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