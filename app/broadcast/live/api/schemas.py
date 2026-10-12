"""방송 현황 응답용 스키마.

값은 collector가 redis summary에 넣은 것(라이브 요약)이다.
detail_loop가 돌기 전(방송 감지 직후 몇 초)에는 viewers, started_at, viewers_at이 비어 있을 수 있다.
"""

from datetime import datetime

from pydantic import BaseModel


class LiveItem(BaseModel):
    """방송 현황의 기본 항목. LiveDetail이 상속한다."""

    bid: str
    nick: str
    title: str
    viewers: int | None
    tags: list[str]
    started_at: datetime | None


class LiveDetail(LiveItem):
    """라이브 화면의 방송 현황."""

    viewers_at: datetime | None  # viewers를 받은 시각
    embed_url: str               # SOOP 공식 퍼가기 플레이어 주소