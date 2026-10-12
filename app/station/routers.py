"""station API 라우터. 홈(스트리머 목록)과 방송국."""

from typing import Annotated

from fastapi import APIRouter, Query

from app.broadcast.review.api.schemas.vod_schemas import VodListOut
from app.core.deps import DBDep, RDep
from app.station import services
from app.station.schemas import StationOut

router = APIRouter(tags=["station"])


@router.get("/")
async def list_stations(db: DBDep, r: RDep) -> list[StationOut]:
    """홈 화면. 스트리머 목록(닉네임순)과 방송 중 여부."""
    return await services.list_stations(db, r)


@router.get("/station/{bid}")
async def get_station(db: DBDep, r: RDep, bid: str) -> StationOut:
    return await services.get_station(db, r, bid)


@router.get("/station/{bid}/reviews")
async def list_reviews(
    db: DBDep,
    bid: str,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    cursor: Annotated[int | None, Query(description="이전 응답의 next_cursor")] = None,
) -> VodListOut:
    return await services.list_reviews(db, bid, limit=limit, before=cursor)