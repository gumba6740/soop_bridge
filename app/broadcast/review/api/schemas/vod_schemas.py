"""vod 리스트, 상세보기 응답용 스키마

VodListItem, VodListOut은 방송국의 다시보기 목록(app/station)이 쓴다.

시간 단위
    duration, total_duration : 밀리초 (DB 그대로)
    offset_base_sec: 초 (플레이어가 초 단위라서)
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict

# ------------------------------------------------- VOD


class VodListItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    vod_id: int
    title: str
    total_duration: int  # 밀리초
    broad_start: datetime
    broad_end: datetime | None
    grade: int  # 19 = 성인인증 필요
    media_code: int | None  # 1: 정상, -6303: 잠김
    broad_idx: int | None
    hashtags: list[str]


class VodListOut(BaseModel):
    items: list[VodListItem]
    # 다음 페이지 요청 시 ?cursor=에 그대로 넣는다. None이면 마지막 페이지.
    next_cursor: int | None


class VodFileOut(BaseModel):
    file_order: int
    duration: int  # 밀리초
    hls_url: str | None
    # 이 파트의 0초가 VOD 전체 기준 몇 초인지. (file_start - broad_start)
    # 채팅 시간 = offset_base_sec + 플레이어의 파트 내 재생 시간
    offset_base_sec: float | None


class VodDetailOut(VodListItem):
    """VodListItem를 상속함."""

    bid: str
    nick: str
    files: list[VodFileOut]
    views: int


class ViewOut(BaseModel):
    views: int
