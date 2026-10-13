"""홈(스트리머 목록), 방송국 응답용 스키마.

방송국의 다시보기 목록은 review의 VodListOut을 그대로 쓴다
(broadcast/review/api/schemas/vod_schemas.py).
"""

from pydantic import BaseModel


class StationOut(BaseModel):
    """홈 목록의 한 줄이자 방송국 정보."""

    bid: str
    nick: str
    is_live: bool  # 라이브 수집기가 redis에 방송 요약(summary)을 올려 둔 상태면 True
