"""SOOP API 응답 → DB row 매핑 스키마.

상세 응답의 data 객체를 그대로 언패킹해서 받는다.
data의 키와 DB 컬럼명이 다른 경우만 alias로 매핑한다.

    bj_id               -> bid
    full_title          -> title
    total_file_duration -> total_duration
    write_tm            -> broad_end   ("시작 ~ 종료" 중 뒷부분만)
    chat                -> chat_url
    file                -> hls_url

media_exception.code 는 중첩이라 alias로 못 잡으므로
model_validator에서 media_code 로 평탄화한다.
"""

from datetime import datetime
import logging
from typing import Any


from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from app.utils.tz import KST

logger = logging.getLogger(__name__)




def _to_kst(v: datetime | None) -> datetime | None:
    """pg에 datetime 삽입을 위해 aware하게 만듦"""
    if v is not None and v.tzinfo is None:
        return v.replace(tzinfo=KST)
    return v


class _SoopModel(BaseModel):
    """SOOP 응답을 그대로 언패킹해서 받을 수 있는 공통 베이스."""

    model_config = ConfigDict(
        populate_by_name=True,  # 필드명/alias 둘 다 허용
        extra="ignore",         # 응답에 모르는 키가 늘어나도 깨지지 않음
        str_strip_whitespace=True,
    )


class VodRow(_SoopModel):
    vod_id: int
    bid: str = Field(alias="bj_id")
    title: str = Field(default="", alias="full_title")
    total_duration: int = Field(default=0, alias="total_file_duration")  # 밀리초
    broad_start: datetime
    broad_end: datetime | None = Field(default=None, alias="write_tm")

    grade: int = 0                              # 19 = 성인인증 필요
    auth_no: int | None = None                  # 107 = 구독+ 전용 추정
    permanence: bool | None = None              # "Y"/"N"
    auto_delete_date: datetime | None = None
    chat_duration: int = 300                    # 초. 채팅 조회 단위
    media_code: int | None = None               # media_exception.code
    broad_idx: int | None = None                # 방송 세션 번호
    hashtags: list[str] = Field(default_factory=list)  # "a,b,c" -> ["a", "b", "c"]

    view_cnt: int | None = None
    live_total_view: int | None = None

    @model_validator(mode="before")
    @classmethod
    def _flatten_media_exception(cls, data: Any) -> Any:
        if isinstance(data, dict) and "media_code" not in data:
            me = data.get("media_exception")
            if isinstance(me, dict):
                return {**data, "media_code": me.get("code")}
        return data

    @field_validator("broad_end", mode="before")
    @classmethod
    def _split_write_tm(cls, v: Any) -> Any:
        """write_tm "2026-09-04 19:25:20 ~ 2026-09-05 05:17:11" -> 종료 시각."""
        if isinstance(v, str) and "~" in v:
            tail = v.split("~", 1)[1].strip()
            return tail or None
        # TODO: soop에서 write_tm을 None으로 주는 게 정상 응답이라면 로거 위치 변경
        logger.warning("write_tm 포맷 변경 감지: %s", v)
        return None

    @field_validator("permanence", mode="before")
    @classmethod
    def _yn_to_bool(cls, v: Any) -> Any:
        if isinstance(v, str):
            return v.strip().upper() == "Y"
        return v

    @field_validator("broad_start", "broad_end", "auto_delete_date", mode="after")
    @classmethod
    def _ensure_tzinfo(cls, v: datetime | None) -> datetime | None:
        return _to_kst(v)

    @field_validator("hashtags", mode="before")
    @classmethod
    def _split_hashtags(cls, v: Any) -> Any:
        if v is None:
            return []
        if isinstance(v, str):
            return [t.strip() for t in v.split(",") if t.strip()]
        return v


class VodFileRow(_SoopModel):
    vod_id: int
    file_order: int
    duration: int = 0  # 밀리초
    chat_url: str | None = Field(default=None, alias="chat")
    hls_url: str | None = Field(default=None, alias="file")
    file_start: datetime | None = None

    @field_validator("file_start", mode="after")
    @classmethod
    def _ensure_tzinfo(cls, v: datetime | None) -> datetime | None:
        return _to_kst(v)


class ChatRow(BaseModel):
    """chats 테이블 한 행. chat_parser.py에서 응답 xml을 파싱해서 넣어줌.

    time_offset은 영상 파트마다 0으로 초기화되는 값이 아니라, 영상 전체 기준임(초).
    data의 키는 타입마다 다르다. docs/review_chat_types.md 참고.
    """

    # 파싱을 pydantic에게 맡기지 않고 직접 파싱해서 넣어줌
    model_config = ConfigDict(extra="forbid")

    vod_id: int
    file_order: int
    type: str
    time_offset: float
    data: dict[str, Any]