"""라이브 목록, 방송 현황, 채팅 SSE."""

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from datetime import datetime

from redis.asyncio import Redis

from app.core.config import settings
from app.core.soop_clients.streamers import BID
from app.live.api.exceptions import LiveNotFound, StreamerNotFound
from app.live.api.schemas import LiveDetail, LiveItem
from app.core.hub import ChatHub

logger = logging.getLogger(__name__)

# SOOP 공식 퍼가기. bno 없이도 지금 방송이 나온다
EMBED_URL = "https://play.sooplive.com/{bid}/embed"
INITIAL_CHATS = 10  # SSE를 처음 열었을 때 먼저 보내는 최근 채팅 수
HEARTBEAT_SEC = 15  # 채팅이 없을 때 연결 유지용 신호를 보내는 간격. 이때 방송 종료도 확인한다

NICK_BY_BID = {bid: nick for nick, bid in BID.items()}


# ------------------------------------------------------ 방송 현황

def _check_bid(bid: str) -> None:
    if bid not in NICK_BY_BID:
        raise StreamerNotFound()


def _to_item(bid: str, s: dict[str, str]) -> LiveItem:
    return LiveItem(
        bid=bid,
        nick=s.get("nick", ""),
        title=s.get("title", ""),
        viewers=int(s["viewers"]) if s.get("viewers") else None,
        tags=json.loads(s.get("tags") or "[]"), 
        started_at=datetime.fromisoformat(s["started_at"]) if s.get("started_at") else None,
    )


async def list_lives(r: Redis) -> list[LiveItem]:
    """지금 방송 중인 스트리머. BID 순서대로."""
    bids = list(NICK_BY_BID)
    async with r.pipeline(transaction=False) as pipe:
        for bid in bids:
            pipe.hgetall(settings.summary_key(bid))
        summaries: list[dict[str, str]] = await pipe.execute()
    return [_to_item(bid, s) for bid, s in zip(bids, summaries, strict=True) if s]


async def get_live(r: Redis, bid: str) -> LiveDetail:
    _check_bid(bid)
    s: dict[str, str] = await r.hgetall(settings.summary_key(bid))
    if not s:
        raise LiveNotFound()
    item = _to_item(bid, s)
    return LiveDetail(
        **item.model_dump(),
        viewers_at=datetime.fromisoformat(s["viewers_at"]) if s.get("viewers_at") else None,
        embed_url=EMBED_URL.format(bid=bid),
    )


# -------------------------------------------------------- 채팅 SSE

async def check_live(r: Redis, bid: str) -> None:
    """SSE를 열기 전에 확인한다. 응답이 시작된 뒤에는 404를 줄 수 없어서 미리 한다."""
    _check_bid(bid)
    if not await r.exists(settings.summary_key(bid)):
        raise LiveNotFound()


def _id_key(entry_id: str) -> tuple[int, int]:
    """스트림 id를 비교할 수 있게. "11111-1" -> (11111, 1)"""
    ms, seq = entry_id.split("-")
    return int(ms), int(seq)


def _sse(entry_id: str, fields: dict[str, str]) -> str:
    """채팅 한 건을 SSE 이벤트로. data는 {"svc": ..., "parts": [...]}."""
    data = {"svc": fields.get("svc", ""), "parts": json.loads(fields.get("parts", "[]"))}
    return f"id: {entry_id}\nevent: chat\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


async def chat_events(
    r: Redis, hub: ChatHub, bid: str) -> AsyncIterator[str]:
    """채팅 SSE 본문. 처음엔 최근 INITIAL_CHATS개를, 그 뒤로는 새 채팅을 보낸다.

    - 방송이 끝나면 "end" 이벤트를 보내고 닫는다.
    - 너무 느린 연결은 hub가 큐를 닫아서 끝난다.
    """
    key = settings.chats_key(bid)
    # 먼저 구독해 두고 지난 채팅을 읽는다.
    # 그 사이에 들어온 건 큐에도 쌓이니 id로 겹치는 것만 거른다.
    q = await hub.subscribe(bid)
    try:
        history = list(reversed(await r.xrevrange(key, count=INITIAL_CHATS)))
        sent = (0, 0)
        for entry_id, fields in history:
            # routers.py의 StreamingResponse가 소비
            yield _sse(entry_id, fields)
            sent = _id_key(entry_id)

        while True:
            try:
                entry = await asyncio.wait_for(q.get(), HEARTBEAT_SEC)
            except asyncio.QueueShutDown:  # 너무 느린 연결이라 hub가 큐를 닫았다
                return
            # HEARTBEAT_SEC 동안 채팅이 안왔을 때
            except TimeoutError:
                # summary_key가 있으면 방송 중
                if not await r.exists(settings.summary_key(bid)):
                    yield "event: end\ndata: {}\n\n"
                    return
                # 주석 줄(:)은 브라우저가 무시한다.
                # 중간 프록시가 연결을 끊지 않게 하려는 것
                yield ": ping\n\n"
                continue

            entry_id, fields = entry
            if _id_key(entry_id) <= sent:
                continue
            yield _sse(entry_id, fields)
            sent = _id_key(entry_id)
    finally:
        hub.unsubscribe(bid, q)