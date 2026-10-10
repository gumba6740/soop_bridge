"""라이브 채팅을 SSE 연결들에 나눠 주는 곳.

스트리머마다 redis 읽기 루프(XREAD BLOCK)를 하나만 둔다.
시청자가 여럿이어도 redis에서는 한 번만 읽고, 연결마다 가진 큐에 넣어 준다.
그 스트리머를 보는 연결이 완전히 없어지면 루프도 멈춘다.
(예전에 연결마다 redis를 읽게 했다가 부하 테스트에서 연결이 바닥난 적이 있어서 이렇게 한다.)

용례:
    q = await hub.subscribe(bid)
    entry_id, fields = await q.get()
    ...
    hub.unsubscribe(bid, q)

큐가 닫히면(QueueShutDown) 그 연결이 너무 느려서 끊는다는 뜻이다.
"""

import asyncio
import logging
from typing import cast

from redis.asyncio import Redis

from app.core.config import settings

logger = logging.getLogger(__name__)

XREAD_BLOCK_MS = 3000   # 새 채팅을 기다리는 시간. 이 안에 없으면 빈 응답을 받고 다시 기다린다
XREAD_COUNT = 100       # 한 번에 읽는 최대 개수
QUEUE_MAXSIZE = 1000    # 연결 하나가 밀려 있을 수 있는 최대 채팅 수
RETRY_DELAY = 1         # redis 읽기 실패 뒤 다시 시도하기까지(초)

# (entry_id, fields)
StreamEntry = tuple[str, dict[str, str]]
ChatQueue = asyncio.Queue[StreamEntry]


class ChatHub:
    def __init__(self, r: Redis) -> None:
        self._r = r
        # bid -> 그 방송인을 보는 SSE 연결들의 큐. 개수가 곧 지금 시청 중인 연결 수.
        self._subs: dict[str, set[ChatQueue]] = {}
        # bid -> 그 방송인의 읽기 루프(태스크). 방송인당 하나만 돌고, 보는 연결이 없으면 멈춘다.
        # 풀(self._r)에서 연결 하나를 빌려 XREAD로 기다리고, 읽은 채팅을 _subs의 큐마다 넣는다.
        self._readers: dict[str, asyncio.Task[None]] = {}

    async def subscribe(self, bid: str) -> ChatQueue:
        """bid의 새 채팅을 받을 큐를 만든다. 읽기 루프가 없으면 띄운다.

        루프는 "지금 스트림의 마지막 항목" 다음부터 읽는다. 그 전 채팅은 호출부가 따로 읽는다.
        """
        q: ChatQueue = asyncio.Queue(maxsize=QUEUE_MAXSIZE)
        # 생방송 중일 때 and 해당 방송에 첫 시청 요청이 있었을 때 이 조건을 만족함.
        if bid not in self._readers:
            start_id = await self._latest_id(bid)
            # 위 await 동안 다른 연결이 먼저 루프를 띄웠을 수 있다.
            if bid not in self._readers:
                self._subs[bid] = set()
                self._readers[bid] = asyncio.create_task(
                    self._read_loop(bid, start_id), name=f"chat_hub:{bid}"
                )
        self._subs[bid].add(q)
        return q

    def unsubscribe(self, bid: str, q: ChatQueue) -> None:
        """연결이 끝날 때. 마지막 연결이면 읽기 루프를 멈춘다."""
        subs = self._subs.get(bid)
        if subs is None:
            return
        subs.discard(q)
        if not subs:
            # _readers와 모양을 맞춰 주려고 _subs의 키도 삭제함.
            del self._subs[bid]
            self._readers.pop(bid).cancel()

    async def aclose(self) -> None:
        """앱이 꺼질 때. 모든 읽기 루프를 멈추고 끝날 때까지 기다린다."""
        tasks = list(self._readers.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._readers.clear()
        self._subs.clear()

    async def _latest_id(self, bid: str) -> str:
        """스트림의 마지막 항목 id. 스트림이 없거나 비었으면 처음부터("0-0")."""
        last = await self._r.xrevrange(settings.chats_key(bid), count=1)
        return str(last[0][0]) if last else "0-0"

    async def _read_loop(self, bid: str, last_id: str) -> None:
        key = settings.chats_key(bid)
        while True:
            try:
                resp = await self._r.xread(
                    {key: last_id}, block=XREAD_BLOCK_MS, count=XREAD_COUNT
                )
            except Exception as e:
                # redis가 잠깐 안 되더라도 루프는 살려 둔다. 연결들은 그대로 기다린다.
                logger.warning("bid=%s 채팅 읽기 실패 %s: %s", bid, type(e).__name__, e)
                await asyncio.sleep(RETRY_DELAY)
                continue
            if not resp:
                continue

            entries = cast(list[StreamEntry], resp[0][1])
            for entry_id, fields in entries:
                last_id = entry_id
                # 나눠 주는 도중 연결이 끊길 수 있어서 복사본을 돈다.
                for q in list(self._subs.get(bid, ())):
                    self._put(bid, q, (entry_id, fields))


    def _put(self, bid: str, q: ChatQueue, entry: StreamEntry) -> None:
        try:
            q.put_nowait(entry)
        except asyncio.QueueFull:
            # 받아 가지 못할 만큼 느린 연결. 큐를 닫아 밀린 것을 버리고, 꺼내는 쪽을 끝낸다.
            logger.info("bid=%s 느린 채팅 연결을 끊는다 (%d건 밀림)", bid, q.qsize())
            q.shutdown(immediate=True)
            self._subs.get(bid, set()).discard(q)