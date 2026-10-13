"""채팅 응답용 스키마

시간 단위
    t, start, end: 초 (플레이어가 초 단위라서)
"""

from typing import Any

from pydantic import BaseModel


class ChatItem(BaseModel):
    """채팅 한 건. data의 키는 type마다 다르다. docs/review_chat_types.md 참고.

    유저 아이디(user_id, from_user_id, to_user_id)는 내보내지 않는다.
    """

    type: str
    t: float  # VOD 전체 기준 오프셋(초)
    data: dict[str, Any]


class ChatWindowOut(BaseModel):
    start: float
    end: float
    complete: bool  # 이 구간의 수집이 끝났는가. False면 잠시 뒤 다시 요청
    items: list[ChatItem]
