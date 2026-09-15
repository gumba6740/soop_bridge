from typing import Annotated

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect
from psycopg.rows import DictRow

from app.live.schemas import (
    GetChatsResponse,
    GetStatsResponse,
    MinutelyChat,
    UserChatRanking,
)
from app.live.services import get_chats, stat_ingredient, stat_key
from app.core.deps import DBDep, RDep, RgDep

router = APIRouter()

LimitQuery = Annotated[int, Query(ge=1, le=1000)]
RecentlyMQuery = Annotated[int, Query(ge=1, le=60)]


@router.get("/streams/{bid}/chats", response_model=list[GetChatsResponse])
async def get_chats_api(
    bid: str, conn: DBDep, limit: LimitQuery = 100
) -> list[DictRow]:
    return await get_chats(bid, conn, limit)


@router.get("/streams/{bid}/stats", response_model=GetStatsResponse)
async def get_stats(
    bid: str, r: RDep, recently_m: RecentlyMQuery = 10
) -> GetStatsResponse:

    key = stat_key(bid, recently_m)
    result = await r.get(key)

    if result is not None:
        return GetStatsResponse.model_validate_json(result)

    count_by_user, minutes, counts = await stat_ingredient(bid, r, recently_m)

    ranking = [
        UserChatRanking(user_id=member, chating_count=score)
        for member, score in count_by_user
    ]

    minutely = [
        MinutelyChat(minutes=dt, chating_count=int(c) if c is not None else 0)
        for dt, c in zip(minutes, counts, strict=True)
    ]

    resp = GetStatsResponse(ranking=ranking, minutely=minutely)

    await r.set(key, resp.model_dump_json(), ex=15)
    return resp


# 이 라우터는 stream_chats()의 결함으로 현재 쓰이지 않음.
# @router.websocket("/streams/{bid}/live")
# async def live(bid: str, ws: WebSocket) -> None:
#     await ws.accept()

#     r = ws.app.state.redis

#     await stream_chats(bid, r, ws)


@router.websocket("/streams/{bid}/live")
async def live(bid: str, ws: WebSocket, rg: RgDep) -> None:
    """
    웹소켓을 app.state.registry에 등록만 함.
    app/core/lifespan.py의 reader()가 등록된 웹소켓을 순회하면서
    실질적으로 채팅을 쏴 줌.
    """
    await ws.accept()

    rg.setdefault(bid, set()).add(ws)

    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        rg[bid].remove(ws)
