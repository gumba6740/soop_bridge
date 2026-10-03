import logging
import re
from collections.abc import Callable
from typing import NamedTuple

from redis.asyncio import Redis
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models import Chat, ChatProgress, Vod, VodFile
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
    db: AsyncSession, r: Redis, vod_id: int, *, start: float
) -> ChatWindowOut:
    """[start, start + CHAT_WINDOW) 구간의 채팅. start는 포함, end는 미포함.

    수집이 덜 끝난 VOD면 워커 큐에 넣고, 지금까지 받은 채팅만 돌려준다.
    """
    end = start + CHAT_WINDOW

    exists = await db.scalar(select(Vod.vod_id).where(Vod.vod_id == vod_id))
    if exists is None:
        raise VodNotFound()

    parts = await _part_progress(db, vod_id)
    # 완료되지 않은 채팅의 vod_id를 큐에 넣음. 수집기에서 진행상황 이후의 채팅을 수집함.
    if any(p.collected < p.end for p in parts):
        await _enqueue(r, vod_id)

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

    return ChatWindowOut(
        start=start,
        end=end,
        complete=_is_complete(parts, start, end),
        items=items,
    )


# ------------------------------------------------ 수집 진행 상황

class _PartProgress(NamedTuple):
    """수집 대상 파트 하나. 세 값 모두 VOD 전체 기준 초."""

    start: float      # 파트의 시작 (file_start - broad_start)
    end: float        # 파트의 끝
    collected: float  # 여기까지 채팅을 받았다


async def _part_progress(db: AsyncSession, vod_id: int) -> list[_PartProgress]:
    """수집 대상 파트들의 진행 상황.

    수집 대상의 조건은 collector의 CHAT_PARTS_SQL과 같다.
    (chat_url, file_start가 있고 duration > 0인 파트)
    chat_progress 행이 없는 파트는 아직 시작 안 한 것이라 collected = start.
    """
    stmt = (
        select(
            VodFile.duration,
            VodFile.file_start,
            Vod.broad_start,
            ChatProgress.last_start_time,
            ChatProgress.done,
        )
        .join(Vod, Vod.vod_id == VodFile.vod_id)
        .outerjoin(
            ChatProgress,
            and_(
                ChatProgress.vod_id == VodFile.vod_id,
                ChatProgress.file_order == VodFile.file_order,
            ),
        )
        .where(
            VodFile.vod_id == vod_id,
            VodFile.chat_url.is_not(None),
            VodFile.file_start.is_not(None),
            VodFile.duration > 0,
        )
        .order_by(VodFile.file_order)
    )
    result = await db.execute(stmt)

    parts = []
    for part in result:
        part_start = (part.file_start - part.broad_start).total_seconds()
        part_end = part_start + part.duration / 1000  # duration은 밀리초
        if part.done:
            collected = part_end
        else:
            collected = part_start + (part.last_start_time or 0)
        parts.append(_PartProgress(part_start, part_end, min(collected, part_end)))
    return parts


def _is_complete(parts: list[_PartProgress], start: float, end: float) -> bool:
    """[start, end) 구간에 걸친 파트들이 이 구간을 다 받았는지.

    Args:
        parts: 영상의 각 파트들. part.start, part.end는 각 파트의 시작과 끝(오프셋, 초)
        start, end: 응답할 채팅 구간(오프셋, 초)
    """
    for part in parts:
        if part.end <= start or part.start >= end:
            continue  # 이 구간과 안 겹치는 파트
        if part.collected < min(end, part.end):
            return False
    return True


async def _enqueue(r: Redis, vod_id: int) -> None:
    """워커 큐에 넣는다. pending에 있으면 넣지 않는다."""
    if await r.sadd(settings.chat_job_pending_key, str(vod_id)):
        await r.rpush(settings.chat_job_queue_key, str(vod_id))
        logger.info("채팅 수집 요청을 큐에 넣음: vod_id=%d", vod_id)


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

# 접미사 제거에 사용. "viewer01(2)" -> "viewer01"
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