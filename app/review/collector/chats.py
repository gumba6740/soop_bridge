"""VOD 채팅 수집.

동작
----
1. CHAT_COLLECT_SINCE 이후 VOD 중 채팅 수집이 안 끝난 것을
    오래된 순으로 하나 고르고, 그 VOD의 미완료 파트를 순서대로 처리한다.
2. 파트 단위 advisory lock을 걸어 동시성 제어.
3. last_start_time부터 chat_duration(기본 300초) 단위로 
    영상 파트가 끝나는 시점까지 채팅을 긁는다.
4. 구간(chat_duration)마다 해당 범위를 DELETE 후 INSERT 한다.
    chats에는 자연키가 없어 ON CONFLICT를 못 쓰므로, 이 방식으로 멱등성을 만든다.   
5. 구간 하나가 끝날 때마다 진행 상태를 커밋한다. 중단 지점이 곧 재개 지점.
"""

import asyncio
from datetime import datetime
import logging

import httpx
from psycopg import AsyncConnection

from app.review.shared.chat_parser import parse_chat_xml
from app.review.shared.schemas import KST
from app.review.shared.soop_client import get_vod_chat

logger = logging.getLogger(__name__)

REQUEST_DELAY = 2.0     # 초. 채팅 요청 사이 텀
CHAT_COLLECT_SINCE = datetime(2026, 9, 1, tzinfo=KST)  # 크론 자동 수집 기준일.

# ---------------------------------------------------------------- SQL
# CHAT_PARTS_SQL은 이 쿼리보다 조건이 좁으면 안 된다.
# (VOD를 골랐는데 파트가 0개면 크론이 같은 VOD에서 멈춘다.)
# 쿨다운(updated_at) 조건은 크론에만 적용하려고 여기에만 둔다.
NEXT_CHAT_TARGET_SQL = """
    SELECT f.vod_id
    FROM vod_files f
    JOIN vods v ON v.vod_id = f.vod_id
    LEFT JOIN chat_progress p
        ON p.vod_id = f.vod_id AND p.file_order = f.file_order
    WHERE f.chat_url IS NOT NULL
    AND f.file_start IS NOT NULL
    AND f.duration > 0
    AND (p.done IS NULL OR NOT p.done)
    AND v.broad_start >= %s
    AND (p.updated_at IS NULL OR p.updated_at < now() - interval '1 hour')
    ORDER BY v.broad_start ASC
    LIMIT 1
"""

CHAT_PARTS_SQL = """
    SELECT f.vod_id,
        f.file_order,
        f.chat_url,
        f.duration,
        f.file_start,
        v.broad_start,
        v.chat_duration,
        COALESCE(p.last_start_time, 0) AS last_start_time
    FROM vod_files f
    JOIN vods v ON v.vod_id = f.vod_id
    LEFT JOIN chat_progress p
            ON p.vod_id = f.vod_id AND p.file_order = f.file_order
    WHERE f.vod_id = %s
    AND f.file_start IS NOT NULL
    AND f.duration > 0
    AND f.chat_url IS NOT NULL
    AND (p.done IS NULL OR NOT p.done)
    ORDER BY f.file_order
"""

# 재수집 시 중복을 막기 위해 해당 구간을 먼저 지운다.
DELETE_RANGE_SQL = """
    DELETE FROM chats
    WHERE vod_id = %(vod_id)s
    AND file_order = %(file_order)s
    AND time_offset >= %(lo)s
    AND time_offset <  %(hi)s
"""

INSERT_CHAT_SQL = """
    INSERT INTO chats (vod_id, file_order, type, time_offset, parts)
    VALUES (%(vod_id)s, %(file_order)s, %(type)s, %(time_offset)s, %(parts)s)
"""

PROGRESS_SQL = """
    INSERT INTO chat_progress (vod_id, file_order, last_start_time, done)
    VALUES (%(vod_id)s, %(file_order)s, %(last_start_time)s, %(done)s)
    ON CONFLICT (vod_id, file_order) DO UPDATE
    SET last_start_time = EXCLUDED.last_start_time,
        done            = EXCLUDED.done,
        updated_at      = now()
"""


# ---------------------------------------------------------------- 수집

async def collect_part(
    conn: AsyncConnection,
    *,
    vod_id: int,
    file_order: int,
    chat_url: str,
    duration_ms: int,
    offset_base: float,
    chat_duration: int,
    last_start_time: int,
) -> int:
    """파트 하나의 채팅을 끝까지 수집한다. 저장한 채팅 수를 반환.

    Args:
        duration_ms     : 파트 영상 길이(밀리초).
        offset_base     : (file_start - broad_start) 초.
        chat_duration   : 기본값 300초. ex) 한 번 요청 당 300초 동안의 채팅을 가져옴.
        last_start_time : 영상의 어느 시점까지 채팅을 받았는지(초). 기본값 0.

    Return:
        저장한 채팅 수
    """
    # 채팅 수집기가 동시에 같은 파트의 채팅을 수집하지 않도록 락.
    cur = await conn.execute(
        "SELECT pg_try_advisory_lock(%s, %s)", (vod_id, file_order)
    )
    row = await cur.fetchone()
    if not (row and row[0]):
        logger.info(
            "[SKIP] vod_id=%d file_order=%d 다른 작업이 처리 중", vod_id, file_order
        )
        return 0

    total = 0
    part_seconds = duration_ms / 1000  # 밀리초를 초로 변환
    start = last_start_time

    try:
        while start < part_seconds:
            await asyncio.sleep(REQUEST_DELAY)

            try:
                xml = await get_vod_chat(chat_url, start_time=start)
            except httpx.HTTPError as e:
                logger.warning(
                    "[RETRY] vod_id=%d file_order=%d t=%d 조회 실패: %s",
                    vod_id, file_order, start, e,
                )
                # 쿨다운용. 진행 위치는 그대로 두고 updated_at만 갱신한다.
                await conn.execute(
                    PROGRESS_SQL,
                    {
                        "vod_id": vod_id,
                        "file_order": file_order,
                        "last_start_time": start,
                        "done": False,
                    },
                )
                return total

            rows = parse_chat_xml(
                xml, vod_id=vod_id, file_order=file_order, offset_base=offset_base
            )

            lo = offset_base + start
            hi = offset_base + start + chat_duration

            async with conn.transaction():
                # 재수집 대비: 이 구간을 지우고 다시 넣어 멱등하게 만든다.
                await conn.execute(
                    DELETE_RANGE_SQL,
                    {"vod_id": vod_id, "file_order": file_order, "lo": lo, "hi": hi},
                )
                if rows:
                    await conn.executemany(
                        INSERT_CHAT_SQL, [r.model_dump() for r in rows]
                    )

                start += chat_duration
                await conn.execute(
                    PROGRESS_SQL,
                    {
                        "vod_id": vod_id,
                        "file_order": file_order,
                        "last_start_time": start,
                        "done": start >= part_seconds,
                    },
                )

            total += len(rows)

        logger.info(
            "[OK] vod_id=%d file_order=%d 완료 (%d건)", vod_id, file_order, total
        )
        return total
    finally:
        await conn.execute(
            "SELECT pg_advisory_unlock(%s, %s)", (vod_id, file_order)
        )


async def next_chat_target(conn: AsyncConnection) -> int | None:
    """CHAT_COLLECT_SINCE 이후 VOD 중 미완료인 것을 오래된 순으로 하나 고른다(크론).

    Return:
        vod_id. 수집할 VOD가 없으면 None.
    """
    cur = await conn.execute(NEXT_CHAT_TARGET_SQL, (CHAT_COLLECT_SINCE,))
    row = await cur.fetchone()
    return row[0] if row else None


async def collect_vod_chats(conn: AsyncConnection, vod_id: int) -> int:
    """VOD 하나의 미완료 파트를 전부 수집한다. 저장한 채팅 수를 반환.

    크론은 next_chat_target()으로 고른 vod_id를,
    수동은 지정한 vod_id를 넘긴다.
    """
    cur = await conn.execute(CHAT_PARTS_SQL, (vod_id,))

    parts = await cur.fetchall()
    if not parts:
        logger.info("수집할 파트 없음")
        return 0

    total = 0
    for (vod_id, file_order, chat_url, duration, file_start,
        broad_start, chat_duration, last_start_time) in parts:
        offset_base = (file_start - broad_start).total_seconds()
        logger.info(
            "vod_id=%d file_order=%d 시작 (offset_base=%.0fs, t=%ds부터)",
            vod_id, file_order, offset_base, last_start_time,
        )
        total += await collect_part(
            conn,
            vod_id=vod_id,
            file_order=file_order,
            chat_url=chat_url,
            duration_ms=duration,
            offset_base=offset_base,
            chat_duration=chat_duration,
            last_start_time=last_start_time,
        )

    return total