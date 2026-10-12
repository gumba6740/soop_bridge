"""collector 엔트리포인트.

    python -m app.broadcast.review.collector vods                    # streamers 테이블 전체 증분 수집
    python -m app.broadcast.review.collector vods --bid beadyo97     # 특정 스트리머만
    python -m app.broadcast.review.collector vods --retry            # 일시 실패분 재시도
    python -m app.broadcast.eview.collector vods --sweep --bid X  # 목록과 DB 대조해 누락분 수집

    python -m app.broadcast.review.collector chats --count 3         # 크론: 미완료 VOD 최대 3건
    python -m app.broadcast.review.collector chats --vod-id 12345    # 수동: 지정 VOD

    python -m app.broadcast.review.collector worker                  # 온디맨드 채팅 수집 워커(상시 실행)

chats는 --count와 --vod-id 중 하나를 반드시 붙여야 한다.

전역 락
-------
vods는 모든 모드에서, chats는 크론 모드에서만 전역 락을 잡는다.
이미 잡혀 있으면 이전 실행이 진행 중인 것으로 보고 건너뛴다.
chats --vod-id는 전역 락 없이 돌고, 동시성은 collect_part의 파트 단위 락이 막는다.
"""

import argparse
import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
import logging

from psycopg import AsyncConnection

from app.core.config import settings
from app.core.log import setup_logging
from app.broadcast.review.collector.chats import collect_vod_chats, next_chat_targets
from app.broadcast.review.collector.vods import (
    collect_new_vods,
    retry_failures,
    sweep_missing_vods,
)
from app.broadcast.review.collector.worker import run_worker
from app.core.soop_clients.review_client import aclose

logger = logging.getLogger(__name__)

VODS_LOCK_KEY = 0x50D_A001
CHATS_LOCK_KEY = 0x50D_B001


# ---------------------------------------------------------------- 공통

@asynccontextmanager
async def global_lock(
    conn: AsyncConnection, key: int
) -> AsyncGenerator[bool, None]:
    """세션 레벨 advisory lock. 잡았으면 True를 넘기고, 끝나면 푼다.

    프로세스가 강제 종료돼도 커넥션이 끊기면 자동으로 풀린다.
    """
    cur = await conn.execute("SELECT pg_try_advisory_lock(%s)", (key,))
    row = await cur.fetchone()
    acquired = bool(row and row[0])
    try:
        yield acquired
    finally:
        if acquired:
            await conn.execute("SELECT pg_advisory_unlock(%s)", (key,))


async def _targets(conn: AsyncConnection, bid: str | None) -> list[tuple[str, str]]:
    """(닉네임, bid) 목록. bid를 주면 그 하나만, 없으면 streamers 테이블 전체."""
    cur = await conn.execute("SELECT nick, bid FROM streamers")
    rows: list[tuple[str, str]] = [(n, b) for n, b in await cur.fetchall()]
    if bid is None:
        return rows
    nick = next((n for n, b in rows if b == bid), bid)
    return [(nick, bid)]


# ---------------------------------------------------------------- vods

async def run_vods(
    conn: AsyncConnection, *, bid: str | None, retry: bool, sweep: bool
) -> None:
    async with global_lock(conn, VODS_LOCK_KEY) as ok:
        if not ok:
            logger.warning("vods: 이전 실행이 아직 진행 중. 이번 실행 건너뜀")
            return

        if retry:
            n = await retry_failures(conn)
            logger.info("=== 재시도 완료: %d건 저장 ===", n)
            return

        for nick, b in await _targets(conn, bid):
            logger.info("=== %s (%s) 시작%s ===", nick, b, " [sweep]" if sweep else "")
            try:
                # saved: 새로 저장된 VOD 수
                if sweep:
                    saved = await sweep_missing_vods(conn, b)
                else:
                    saved = await collect_new_vods(conn, b)
            except Exception:
                # 한 스트리머 실패가 나머지를 죽이지 않게 격리
                logger.exception("=== %s (%s) 실패. 다음으로 ===", nick, b)
                continue
            logger.info("=== %s (%s) 완료: %d건 저장 ===", nick, b, saved)


# ---------------------------------------------------------------- chats

async def run_chats(
    conn: AsyncConnection, *, count: int | None, vod_id: int | None
) -> None:
    # 수동: 지정한 VOD. 기준일/쿨다운 무시, 전역 락 없음.
    if vod_id is not None:
        await collect_vod_chats(conn, vod_id)
        return

    assert count is not None  # argparse가 --count/--vod-id 중 하나를 강제한다

    # 크론: 미완료 VOD를 최대 count개 골라서 하나씩 수집.
    async with global_lock(conn, CHATS_LOCK_KEY) as ok:
        if not ok:
            logger.warning("chats: 이전 실행이 아직 진행 중. 이번 실행 건너뜀")
            return

        targets = await next_chat_targets(conn, count)
        if not targets:
            logger.info("수집할 VOD 없음")
            return

        for i, target in enumerate(targets, 1):
            logger.info("chats 진행 [%d/%d]", i, len(targets))
            await collect_vod_chats(conn, target)



# ---------------------------------------------------------------- 엔트리


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="collector", description="SOOP VOD 수집")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("vods", help="VOD 메타 + 상세 수집")
    p.add_argument("--bid", help="이 스트리머만 수집 (없으면 streamers 테이블 전체)")
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--retry", action="store_true", help="일시 실패분만 재시도")
    mode.add_argument(
        "--sweep",
        action="store_true",
        help="목록과 DB를 대조해 누락분 수집 (--bid 필수)"
    )

    p = sub.add_parser("chats", help="채팅 수집")
    target = p.add_mutually_exclusive_group(required=True)
    target.add_argument("--count", type=int, help="미완료 VOD를 최대 이만큼 수집 (크론)")
    target.add_argument("--vod-id", type=int, help="이 VOD만 수집 (기준일/쿨다운 무시)")

    sub.add_parser("worker", help="온디맨드 채팅 수집 워커 (redis 큐 대기)")

    return parser


async def main() -> None:
    setup_logging()

    parser = _build_parser()
    args = parser.parse_args()

    if args.command == "vods" and args.retry and args.bid:
        parser.error("--retry는 실패 테이블 전체를 대상으로 하므로 --bid와 같이 쓸 수 없다")
    if args.command == "vods" and args.sweep and not args.bid:
        parser.error("--sweep은 --bid와 같이 써야 한다")
    if args.command == "chats" and args.count is not None and args.count < 1:
        parser.error("--count는 1 이상이어야 한다")

    try:
        if args.command == "worker":
            await run_worker()
            return

        # autocommit=True: 요청 사이 sleep 동안 트랜잭션이 열려 있지 않게 한다.
        # 묶어야 하는 쓰기는 각 함수에서 conn.transaction()으로 명시한다.
        async with await AsyncConnection.connect(
            settings.postgres_url, autocommit=True
        ) as conn:
            if args.command == "vods":
                await run_vods(conn, bid=args.bid, retry=args.retry, sweep=args.sweep)
            elif args.command == "chats":
                await run_chats(conn, count=args.count, vod_id=args.vod_id)
    finally:
        # httpx 클라이언트는 만든 이벤트 루프 안에서 닫아야 한다.
        await aclose()


if __name__ == "__main__":
    asyncio.run(main())