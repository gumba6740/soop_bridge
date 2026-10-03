"""채팅 응답용 스키마

시간 단위
    t, start, end: 초 (플레이어가 초 단위라서)
"""


from typing import Annotated, Literal

from pydantic import BaseModel, Field


# ---------------------------------------------------------------- 채팅

FanLevel = Literal["none", "fan", "vip", "streamer"]
SubTier = Literal["none", "basic", "plus"]

# TODO: 미확인 된 걸 확인하면 수정
class Badges(BaseModel):
    """p, p2 비트와 fw에서 확인된 것만 담는다. 비트 뜻은 docs/chat_types.md 참고."""

    fan: FanLevel           # 팬 아님 / 일반 팬(5번, 추정) / 열혈(15번) / 스트리머(2번)
    subscriber: SubTier     # 비구독 / 베이직(p2 18번, 추정) / 플러스(p2 19번, 추정)
    sub_months: int | None  # 구독 개월 수. 비구독이면 None


# ------------------------------------------------ 채팅 타입별 모델
# 응답의 type 값을 보고 어느 모델인지 정한다.
# 화면에 쓸 필드만 담는다. 원본 필드 전체는 docs/chat_types.md 참고.

class _ChatBase(BaseModel):
    """모든 타입 공통."""

    t: float                # VOD 전체 기준 오프셋(초)


class _UserEvent(_ChatBase):
    """보낸 유저가 있는 타입 공통."""

    user_id: str            # 세션 접미사 "(2)"를 뗀 값
    nick: str


class ChatMsg(_UserEvent):
    type: Literal["chat"] = "chat"
    message: str
    badges: Badges | None


class OgqMsg(_UserEvent):
    """OGQ 이모티콘."""

    type: Literal["ogq"] = "ogq"
    message: str            # 이모티콘과 같이 보낸 글. 비어 있을 때가 많음
    badges: Badges | None


class BalloonMsg(_UserEvent):
    """별풍선."""

    type: Literal["balloon"] = "balloon"
    count: int | None


class AdBalloonMsg(_UserEvent):
    """애드벌룬."""

    type: Literal["adballoon"] = "adballoon"
    count: int | None
    message: str            # 선물 메시지


class VBalloonMsg(_UserEvent):
    """영상 별풍선(추정)."""

    type: Literal["vballoon"] = "vballoon"
    count: int | None


class FollowMsg(_UserEvent):
    """구독 알림."""

    type: Literal["follow"] = "follow"
    subscriber: SubTier     # stier 2 = 플러스, 그 외 베이직 (Badges와 같은 규칙)
    sub_months: int | None  # 현재 구독 개월 수


class FanclubMsg(_UserEvent):
    """팬클럽 가입 알림."""

    type: Literal["fanclub"] = "fanclub"


class MissionGiftMsg(_UserEvent):
    """challenge_mission의 CHALLENGE_GIFT. 미션 등록."""

    type: Literal["mission_gift"] = "mission_gift"
    title: str
    count: int | None


class MissionSettleMsg(_ChatBase):
    """challenge_mission의 CHALLENGE_SETTLE. 미션 정산. 보낸 유저 없음."""

    type: Literal["mission_settle"] = "mission_settle"
    title: str
    count: int | None


class MissionNoticeMsg(_ChatBase):
    """challenge_mission의 CHALLENGE_NOTICE. 미션 결과. 보낸 유저 없음."""

    type: Literal["mission_notice"] = "mission_notice"
    title: str
    result: str             # SUCCESS 등


class IceMsg(_ChatBase):
    """채팅 얼리기(추정). 값의 뜻은 아직 미확인이라 원본 그대로 준다."""

    type: Literal["ice"] = "ice"
    value: str              # "1" = 얼림 추정. 해제 값은 미확인


ChatItem = Annotated[
    ChatMsg
    | OgqMsg
    | BalloonMsg
    | AdBalloonMsg
    | VBalloonMsg
    | FollowMsg
    | FanclubMsg
    | MissionGiftMsg
    | MissionSettleMsg
    | MissionNoticeMsg
    | IceMsg,
    Field(discriminator="type"),
]


class ChatWindowOut(BaseModel):
    start: float
    end: float
    complete: bool          # 이 구간의 수집이 끝났는가. False면 잠시 뒤 다시 요청
    items: list[ChatItem]
