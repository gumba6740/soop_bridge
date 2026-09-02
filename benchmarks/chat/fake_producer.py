"""더미 데이터를 생성해서 redis 스트림에 데이터를 흘려보내는 모듈"""

import asyncio
import random
import sys
import time
from datetime import UTC, datetime

import redis.asyncio as redis

from app.core.config import settings

# 가짜 채팅 재료
MESSAGES = [
    "ㅋㅋㅋㅋ",
    "뭐야 저거",
    "ㅁㅊ",
    "오 대박",
    "ㅋㅋㅋㅋㅋㅋ",
    "그래서 뭐?",
    "찐이네",
    "우와",
    "아니 근데",
    "ㄹㅇ",
]

TICKS_PER_SEC = 50  # 1초를 50조각(=20ms)으로 쪼갬.


def make_fake_chat(stream_id: str, seq: int) -> dict:
    """가짜 채팅 하나. msg_seq로 유일하게 식별됨."""
    return {
        "stream_id": stream_id,
        "user_id": f"user_{random.randint(1, 500)}",
        "nickname": f"nick_{random.randint(1, 500)}",
        "message": random.choice(MESSAGES),
        "received_at": datetime.now(UTC).isoformat(),
        "svc_code": "0005",
        "msg_seq": seq,  # ← 멱등성 키
        "enqueued_at": time.time(),  # ← E2E 지연용 (큐 진입 시각)
    }


async def main(bid: str, per_sec: int, total: int):
    stream_key = f"chat_stream:{bid}"
    r = redis.from_url(settings.redis_url, decode_responses=True)

    print(
        f"producer 시작: {stream_key}, 목표 초당 {per_sec}개, 총 {total}개 "
        f"({TICKS_PER_SEC}조각/초로 균등 분산)"
    )

    seq = 0
    sent = 0
    start_all = time.perf_counter()

    tick_interval = 1.0 / TICKS_PER_SEC  # 0.02초 = 20ms
    per_tick = per_sec / TICKS_PER_SEC  # 이 조각에 보낼 개수 (소수 가능)
    carry = 0.0  # 소수점 누적 오차 보정
    behind = 0  # 20ms 안에 못 보낸 조각 수

    # ── 예전: 1초에 한 번 per_sec개를 몰아 던지고 남는 시간 잠 (톱니 부하)
    # ── 지금: 20ms마다 per_tick개씩 잘게 던진다 (평탄한 부하)
    while sent < total:
        tick = time.perf_counter()

        carry += per_tick
        to_send = int(carry)  # 이번 20ms 조각에 보낼 정수 개수
        carry -= to_send
        to_send = min(to_send, total - sent)

        # 조각이 작아 보통 파이프라인 1번이면 끝난다
        if to_send > 0:
            pipe = r.pipeline(transaction=False)
            for _ in range(to_send):
                seq += 1
                pipe.xadd(stream_key, make_fake_chat(bid, seq))
                sent += 1
            await pipe.execute()

        # 20ms 채우고 남은 시간만 잠
        elapsed = time.perf_counter() - tick
        if elapsed < tick_interval:
            await asyncio.sleep(tick_interval - elapsed)
        else:
            behind += 1  # producer가 이 조각을 제때 못 보냄

    dur = time.perf_counter() - start_all
    print(f"완료: {sent}개 전송, {dur:.2f}초, 실측 초당 {sent / dur:.0f}개")
    if behind:
        print(
            f"  [주의] {behind}개 조각이 20ms를 넘김 → producer가 부하를 못 따라간 구간 있음"
        )

    await r.aclose()


if __name__ == "__main__":
    # 사용: python producer.py [bid] [초당개수] [총개수]
    bid = sys.argv[1] if len(sys.argv) >= 2 else "loadtest"
    per_sec = int(sys.argv[2]) if len(sys.argv) >= 3 else 1000
    total = int(sys.argv[3]) if len(sys.argv) >= 4 else 10000
    asyncio.run(main(bid, per_sec, total))
