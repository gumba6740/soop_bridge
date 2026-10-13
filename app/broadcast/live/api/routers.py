"""live API 라우터."""

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from app.broadcast.live.api import services
from app.broadcast.live.api.schemas import LiveDetail
from app.core.deps import DBDep, DBFuncDep, HubDep, RDep

router = APIRouter(prefix="/live", tags=["live"])


@router.get("/{bid}")
async def get_live(db: DBDep, r: RDep, bid: str) -> LiveDetail:
    """방송 현황과 영상(퍼가기) 주소. 방송 중이 아니면 404."""
    return await services.get_live(db, r, bid)


@router.get("/{bid}/chats")
async def stream_chats(
    db: DBFuncDep,
    r: RDep,
    hub: HubDep,
    bid: str,
) -> StreamingResponse:
    """채팅 SSE. 방송 중이 아니면 404."""
    await services.check_live(db, r, bid)
    return StreamingResponse(
        services.chat_events(r, hub, bid),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            # nginx 같은 프록시가 모아서 보내지 않게
            "X-Accel-Buffering": "no",
        },
    )
