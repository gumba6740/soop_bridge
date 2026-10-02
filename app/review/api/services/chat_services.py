import logging
import re
from collections.abc import Callable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Chat, Vod
from app.review.api.exceptions import VodNotFound
from app.review.api.schemas.chat_schemas import (
    AdBalloonMsg,
    Badges,
    BalloonMsg,
    ChatItem,
    ChatMsg,
    ChatWindowOut,
    FanclubMsg,
    FanLevel,
    FollowMsg,
    IceMsg,
    MissionGiftMsg,
    MissionNoticeMsg,
    MissionSettleMsg,
    OgqMsg,
    SubTier,
    VBalloonMsg,
)


logger = logging.getLogger(__name__)


CHAT_WINDOW = 300.0  # 초. 채팅을 한 번에 300초 분량씩 넘겨준다.

async def get_chats(
    db: AsyncSession, vod_id: int, *, start: float
) -> ChatWindowOut:
    """[start, start + CHAT_WINDOW) 구간의 채팅. start는 포함, end는 미포함."""
    end = start + CHAT_WINDOW

    exists = await db.scalar(select(Vod.vod_id).where(Vod.vod_id == vod_id))
    if exists is None:
        raise VodNotFound()

    # idx_chats_vod_id_t (vod_id, time_offset)를 탄다.
    # 해석할 줄 아는 타입(_PARSERS에 등록된 것)만 가져온다.
    stmt = (
        select(Chat.type, Chat.time_offset, Chat.parts)
        .where(
            Chat.vod_id == vod_id,
            Chat.time_offset >= start,
            Chat.time_offset < end,
            Chat.type.in_(list(_PARSERS)),
        )
        .order_by(Chat.time_offset, Chat.id)
    )
    result = await db.execute(stmt)

    items = []
    for row in result:
        item = to_chat_item(row.type, row.time_offset, row.parts)
        if item is not None:
            items.append(item)

    return ChatWindowOut(start=start, end=end, items=items)


# ------------------------------------------------ parts 해석
# 인덱스는 파이썬 기준(0부터). docs/chat_types.md는 1부터라 1씩 빼서 읽는다.

# p 비트
P_STREAMER = 1 << 2       # 해당 방송의 스트리머
P_FAN = 1 << 5            # 추정: 거의 모두 켜져 있음
P_VIP = 1 << 15
P_SUBSCRIBER = 1 << 28
# p2 비트
P2_TIER_1 = 1 << 18       # 추정: 구독 베이직
P2_TIER_2 = 1 << 19       # 추정: 구독 플러스

# "viewer01(2)" -> "viewer01"
_SESSION_SUFFIX = re.compile(r"\(\d+\)$")


def _at(parts: list[str], i: int) -> str:
    """parts가 짧아도 터지지 않게 빈 문자열을 돌려준다."""
    return parts[i] if i < len(parts) else ""


def _to_int(v: str) -> int | None:
    try:
        return int(v)
    except ValueError:
        return None


def _months(v: str) -> int | None:
    """구독 개월 수. 음수(-1 = 비구독)나 숫자가 아니면 None."""
    n = _to_int(v)
    return n if n is not None and n >= 0 else None


def _user_id(raw: str) -> str:
    return _SESSION_SUFFIX.sub("", raw)


def _badges(p: str, p2: str, fw: str) -> Badges | None:
    p_bits, p2_bits = _to_int(p), _to_int(p2)
    if p_bits is None or p2_bits is None:
        return None

    fan: FanLevel
    if p_bits & P_STREAMER:
        fan = "streamer"
    elif p_bits & P_VIP:
        fan = "vip"
    elif p_bits & P_FAN:
        fan = "fan"
    else:
        fan = "none"

    sub: SubTier
    if not (p_bits & P_SUBSCRIBER):
        sub = "none"
    elif p2_bits & P2_TIER_2:
        sub = "plus"
    else:
        sub = "basic"

    months = _months(fw) if sub != "none" else None

    return Badges(fan=fan, subscriber=sub, sub_months=months)


# ------------------------------------------------ 타입별 해석 함수

def _parse_chat(t: float, parts: list[str]) -> ChatItem:
    return ChatMsg(
        t=t,
        message=_at(parts, 0),
        user_id=_user_id(_at(parts, 1)),
        nick=_at(parts, 5),
        badges=_badges(_at(parts, 6), _at(parts, 7), _at(parts, 8)),
    )


def _parse_ogq(t: float, parts: list[str]) -> ChatItem:
    # sf는 "p|p2" 한 칸에 들어 있다.
    sf = _at(parts, 7).split("|")
    badges = _badges(sf[0], sf[1], _at(parts, 12)) if len(sf) == 2 else None
    return OgqMsg(
        t=t,
        message=_at(parts, 1),
        user_id=_user_id(_at(parts, 5)),
        nick=_at(parts, 6),
        badges=badges,
    )


# 풍선 세 타입은 u, n, c가 모두 2~4번째 칸에 있다.
def _parse_balloon(t: float, parts: list[str]) -> ChatItem:
    return BalloonMsg(
        t=t,
        user_id=_user_id(_at(parts, 1)),
        nick=_at(parts, 2),
        count=_to_int(_at(parts, 3)),
    )


def _parse_adballoon(t: float, parts: list[str]) -> ChatItem:
    return AdBalloonMsg(
        t=t,
        user_id=_user_id(_at(parts, 1)),
        nick=_at(parts, 2),
        count=_to_int(_at(parts, 3)),
        message=_at(parts, 4),
    )


def _parse_vballoon(t: float, parts: list[str]) -> ChatItem:
    return VBalloonMsg(
        t=t,
        user_id=_user_id(_at(parts, 1)),
        nick=_at(parts, 2),
        count=_to_int(_at(parts, 3)),
    )


def _parse_follow(t: float, parts: list[str]) -> ChatItem:
    # stier: 2 = 플러스(추정), 그 외 베이직. _badges와 같은 규칙.
    sub: SubTier = "plus" if _at(parts, 6) == "2" else "basic"
    return FollowMsg(
        t=t,
        user_id=_user_id(_at(parts, 1)),
        nick=_at(parts, 2),
        subscriber=sub,
        sub_months=_months(_at(parts, 4)),
    )


def _parse_fanclub(t: float, parts: list[str]) -> ChatItem:
    return FanclubMsg(
        t=t,
        user_id=_user_id(_at(parts, 0)),
        nick=_at(parts, 1),
    )


def _parse_ice(t: float, parts: list[str]) -> ChatItem:
    return IceMsg(t=t, value=_at(parts, 0))


def _parse_challenge_mission(t: float, parts: list[str]) -> ChatItem | None:
    """parts[0](세부 타입)에 따라 칸 구성이 다르다. 모르는 세부 타입이면 None."""
    kind = _at(parts, 0)

    if kind == "CHALLENGE_GIFT":
        return MissionGiftMsg(
            t=t,
            title=_at(parts, 4),
            count=_to_int(_at(parts, 6)),
            user_id=_user_id(_at(parts, 7)),
            nick=_at(parts, 8),
        )
    if kind == "CHALLENGE_SETTLE":
        return MissionSettleMsg(
            t=t,
            title=_at(parts, 3),
            count=_to_int(_at(parts, 5)),
        )
    if kind == "CHALLENGE_NOTICE":
        return MissionNoticeMsg(
            t=t,
            title=_at(parts, 1),
            result=_at(parts, 2),
        )

    logger.warning("모르는 challenge_mission 세부 타입: %s", kind)
    return None


# DB의 type 값 -> 해석 함수.
# 여기 등록된 타입만 get_chats가 DB에서 가져온다.
_PARSERS: dict[str, Callable[[float, list[str]], ChatItem | None]] = {
    "chat": _parse_chat,
    "ogq": _parse_ogq,
    "balloon": _parse_balloon,
    "adballoon": _parse_adballoon,
    "vballoon": _parse_vballoon,
    "follow": _parse_follow,
    "fanclub": _parse_fanclub,
    "ice": _parse_ice,
    "challenge_mission": _parse_challenge_mission,
}


def to_chat_item(type_: str, t: float, parts: list[str]) -> ChatItem | None:
    """chats 한 행 -> ChatItem. 모르는 타입이면 None."""
    parser = _PARSERS.get(type_)
    if parser is None:
        return None
    return parser(t, parts)