import asyncio
import json
from datetime import UTC, datetime
from time import time
from typing import Any

import redis.asyncio as redis

from app.core.config import settings


def make_dummy_dict() -> dict[str, str]:
    parts = [
        str(time()),
        "uid",
        "2",
        "3",
        "4",
        "unick",
        "6",
        "7",
        "8",
        "9",
        "10",
        "11",
        "12",
        "13",
        "14",
        "15",
    ]

    return {
        "bid": "bid_1",
        "parts": json.dumps(parts),
        "received_at": datetime.now(UTC).isoformat(),
    }


stream_key = settings.redis_stream_key


async def main(n):
    async with redis.from_url(settings.redis_url, decode_responses=True) as r:
        while True:
            row = make_dummy_dict()
            await r.xadd(
                stream_key, 
                row,  # type: ignore[arg-type]
                maxlen=100_000, 
                approximate=True
            )
            await asyncio.sleep(1 / n)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("n", type=int)
    args = parser.parse_args()
    try:
        print("k6 스트림 시작")
        asyncio.run(main(args.n))
    except KeyboardInterrupt:
        print("k6 스트림 종료")
