"""live API 라우터."""

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from app.core.deps import RDep, HubDep
from app.live.api.schemas import LiveDetail, LiveItem
from app.live.api import services


router = APIRouter(prefix="/live", tags=["live"])


@router.get("")
async def list_lives(r: RDep) -> list[LiveItem]:
    """지금 방송 중인 스트리머 목록. 홈 화면의 라이브 표시용."""
    return await services.list_lives(r)


@router.get("/{bid}")
async def get_live(r: RDep, bid: str) -> LiveDetail:
    """방송 현황과 영상(퍼가기) 주소. 방송 중이 아니면 404."""
    return await services.get_live(r, bid)


@router.get("/{bid}/chats/stream")
async def stream_chats(
    r: RDep,
    hub: HubDep,
    bid: str,
) -> StreamingResponse:
    """채팅 SSE. 방송 중이 아니면 404."""
    await services.check_live(r, bid)
    return StreamingResponse(
        services.chat_events(r, hub, bid),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            # nginx 같은 프록시가 모아서 보내지 않게
            "X-Accel-Buffering": "no",
        },
    )
