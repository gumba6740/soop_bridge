"""SOOP에서 받아온 채팅을 redis 스트림에 흘려보내는 모듈."""

import asyncio
import sys

import redis.asyncio as redis

from app.core.config import settings
from benchmarks.chat.soop_chat import get_chat


async def main(bid: str):
    r = redis.from_url(settings.redis_url, decode_responses=True)
    stream_key = f"chat_stream:{bid}"
    count = 0
    try:
        async for svc_code, received_at, uid, nick, msg in get_chat(bid):
            await r.xadd(
                stream_key,
                {
                    "stream_id": bid,
                    "user_id": uid,
                    "nickname": nick,
                    "message": msg,
                    "received_at": received_at.isoformat(),
                    "svc_code": svc_code,
                },
                maxlen=100000,
                approximate=True,
            )
            count += 1
            print(f"[{count}] xadd → {nick}: {msg}")
    finally:
        await r.aclose()
        print(f"\n총 {count}건 큐에 넣음")


if __name__ == "__main__":
    try:
        bid = sys.argv[1] if len(sys.argv) >= 2 else "htvv2i"
        asyncio.run(main(bid))
    except KeyboardInterrupt:
        print("\n콜렉터 종료")
