from datetime import datetime

import pydantic as p


class VodItem(p.BaseModel):
    """VOD 목록의 개별 항목."""

    vod_id: int
    title: str
    duration: int | None = None
    thumbnail: str | None = None
    reg_date: str | None = None


class VodListResponse(p.BaseModel):
    """VOD 목록 응답."""

    bid: str
    page: int
    total_count: int
    vods: list[VodItem]


class QualityOption(p.BaseModel):
    """화질 옵션."""

    label: str
    resolution: str
    bitrate: str
    file: str


class VodFile(p.BaseModel):
    """VOD 파트 하나. 긴 방송은 여러 파트로 나뉜다."""

    file_order: int
    hls_url: str
    chat_url: str | None = None
    duration: int  # 밀리초
    file_start: str | None = None
    quality_info: list[QualityOption] = []


class VodDetailResponse(p.BaseModel):
    """VOD 상세 응답 — 프론트에서 영상 재생에 필요한 정보."""

    vod_id: int
    title: str
    bj_id: str
    total_duration: int  # 밀리초
    broad_start: str | None = None
    thumbnail: str | None = None
    files: list[VodFile]