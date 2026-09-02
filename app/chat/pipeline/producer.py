"""SOOP 채팅 수집 프로듀서.

get_soop_chats.stream_soop_chats()로 받은 raw 채팅을 Redis Stream에 적재한다.
사용되는 key는 config.py에서 관리한다.
스트림에 쌓인 메시지는 consumer가 꺼내 PostgreSQL에 저장하고,
FastAPI 앱이 실시간 브로드캐스트에 사용한다.

방송(bid) 하나당 프로세스 하나로 실행한다.
사용법: python -m app.chat.pipeline.producer <bid>
"""

import asyncio
import json
from datetime import UTC, datetime

import redis.asyncio as redis
from redis.typing import EncodableT, FieldT

from app.chat.pipeline.get_soop_chats import stream_soop_chats
from app.core.config import settings

stream_key = settings.redis_stream_key


def make_stream_to_dict(bid: str, parts: list[str]) -> dict[FieldT, EncodableT]:
    return {
        "bid": bid,
        "received_at": datetime.now(UTC).isoformat(),
        # 아스키로 표현 못 하는 한글을 글자 그대로 json화.
        "parts": json.dumps(parts, ensure_ascii=False),
    }


async def main(bid: str) -> None:
    async with redis.from_url(settings.redis_url) as r:
        print(f"{bid} 방송 채팅 프로듀서 실행")
        async for raw_parts in stream_soop_chats(bid):
            # 인덱스 0번 서비스코드는 제외
            parts = [i.decode(errors="replace") for i in raw_parts][1:]
            await r.xadd(
                stream_key,
                make_stream_to_dict(bid, parts),
                maxlen=100000,
                approximate=True,
            )
            print(f"[{bid}] {parts[5]}: {parts[0]}")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("bid")
    arg = parser.parse_args()

    try:
        asyncio.run(main(arg.bid))
    except KeyboardInterrupt:
        print("채팅 수신 종료")
