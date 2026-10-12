"""온디맨드 채팅 수집 워커.

API가 redis 큐에 vod_id를 넣으면, 여기서 하나씩 꺼내 채팅을 수집한다.
크론은 최신 영상의 채팅만 수집하므로(CHAT_COLLECT_SINCE 이후), 
유저가 채팅이 없는 영상의 다시보기 요청 시 즉각 수집하여 올려준다.
동시 요청이 오더라도 soop 서버에 가는 부하를 고려해 순차 처리하도록 한다.

    API : SADD pending vod_id(중복 방지) → 새로 추가됐을 때만 RPUSH queue vod_id
    워커: BLPOP queue → collect_vod_chats → SREM pending vod_id

종료 신호(SIGINT)로 수집 도중에 멈추면, 그 vod_id를 큐 맨 앞에 돌려놓는다.
다시 켜졌을 때 chat_progress의 진행 위치부터 이어서 수집한다.
"""

import asyncio
import logging
from typing import cast

import redis.asyncio as redis
from psycopg import AsyncConnection

from app.core.config import settings
from app.broadcast.review.collector.chats import collect_vod_chats

logger = logging.getLogger(__name__)


async def _handle(raw: str) -> None:
    """vod_id를 하나 받아서 채팅 수집 처리."""
    try:
        vod_id = int(raw)
    except ValueError:
        logger.error("잘못된 vod_id. 건너뜀: %r", raw)
        return

    logger.info("일감 시작: vod_id=%d", vod_id)

    async with await AsyncConnection.connect(
        settings.postgres_url, autocommit=True
    ) as conn:
        total = await collect_vod_chats(conn, vod_id)
    logger.info("일감 완료: vod_id=%d (%d건)", vod_id, total)


async def run_worker() -> None:
    r = redis.from_url(
        settings.redis_url, decode_responses=True, socket_timeout=30
    )
    queue = settings.chat_job_queue_key
    pending = settings.chat_job_pending_key
    logger.info("워커 시작. 큐=%s", queue)

    try:
        await _recover(r, queue, pending)
        while True:
            # 일감이 올 때까지 잠든다. timeout=0은 무한 대기.
            item = await r.blpop([queue], timeout=20)
            if item is None:
                continue
            _, raw_id = cast(tuple[str, str], item)

            try:
                await _handle(raw_id)  # 
            except asyncio.CancelledError:
                # 종료 신호로 중단됨. pending은 그대로 두고 큐 맨 앞에 돌려놓는다.
                logger.warning("종료 신호로 중단. 큐에 되돌려 놓음: %s", raw_id)
                await r.lpush(queue, raw_id)
                raise
            except Exception:
                # 일감 하나의 실패가 워커 전체를 죽이지 않게 격리.
                logger.exception("일감 실패: %s", raw_id)

            # 성공이든 실패든 끝났으면 pending에서 뺀다. 사용자가 다시 요청하면 새로 들어온다.
            await r.srem(pending, raw_id)
    finally:
        await r.aclose()


async def _recover(r: redis.Redis, queue: str, pending: str) -> None:
    """이전 워커가 강제 종료돼 끝내지 못한 일감을 큐 맨 앞에 되돌린다.

    pending에는 있는데 큐에는 없는 vod_id(처리 도중 워커가 죽은 일감)을
    다시 큐에 넣는 작업을 함.
    """
    in_queue = set(await r.lrange(queue, 0, -1))
    orphans = set(await r.smembers(pending)) - in_queue
    for raw in orphans:
        await r.lpush(queue, raw)
    if orphans:
        logger.warning("끝내지 못한 일감 %d건을 큐에 되돌림: %s", len(orphans), orphans)
