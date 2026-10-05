"""라이브 collector 실행.

    python -m app.live.collector            # streamers.BID의 스트리머 전체
    python -m app.live.collector bid1 bid2  # 지정한 스트리머만 (테스트용)
"""

import argparse
import asyncio

from redis.asyncio import Redis

from app.core.config import settings
from app.core.log import setup_logging
from app.core.soop_clients.live_clients.shared import aclose
from app.core.soop_clients.streamers import BID
from app.live.collector.producer import watch_all


async def _run(bids: list[str]) -> None:
    r = Redis.from_url(settings.redis_url, decode_responses=True)
    try:
        await watch_all(r, bids)
    finally:
        await aclose()
        await r.aclose()


def main() -> None:
    setup_logging()

    parser = argparse.ArgumentParser(prog="python -m app.live.collector")
    parser.add_argument("bids", nargs="*", help="비우면 streamers.BID 전체")
    args = parser.parse_args()

    bids = args.bids or list(BID.values())

    try:
        asyncio.run(_run(bids))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()