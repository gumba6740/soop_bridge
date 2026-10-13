import logging
from typing import NamedTuple

from redis.asyncio import Redis
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.broadcast.chat_rules import public
from app.broadcast.review.api.exceptions import VodNotFound
from app.broadcast.review.api.schemas.chat_schemas import ChatItem, ChatWindowOut
from app.core.config import settings
from app.models import Chat, ChatProgress, Vod, VodFile

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

    # 아는 타입만 가져온다. 모르는 타입(세부 타입 포함)은 원본 태그 그대로라
    # 유저 아이디가 섞여 있을 수 있어서 내보내지 않는다.
    # 모르는 타입의 data에만 t가 들어 있으므로 그걸로 가른다(chat_parser 참고).
    stmt = (
        select(Chat.type, Chat.time_offset, Chat.data)
        .where(
            Chat.vod_id == vod_id,
            Chat.time_offset >= start,
            Chat.time_offset < end,
            ~Chat.data.has_key("t"),
        )
        .order_by(Chat.time_offset, Chat.id)
    )
    result = await db.execute(stmt)

    items = [
        ChatItem(type=row.type, t=row.time_offset, data=public(row.data))
        for row in result
    ]

    return ChatWindowOut(
        start=start,
        end=end,
        complete=_is_complete(parts, start, end),
        items=items,
    )


# ------------------------------------------------ 수집 진행 상황


class _PartProgress(NamedTuple):
    """수집 대상 파트 하나. 세 값 모두 VOD 전체 기준 초."""

    start: float  # 파트의 시작 (file_start - broad_start)
    end: float  # 파트의 끝
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
        collected = part_end if part.done else part_start + (part.last_start_time or 0)
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
