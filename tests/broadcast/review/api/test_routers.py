"""
pytest tests/broadcast/review/api/test_routers.py
"""

from datetime import datetime, timedelta

import pytest

from app.models import Chat, Streamer, Vod, VodFile, VodView
from app.utils.tz import KST

VOD_ID = 1000
BROAD_START = datetime(2026, 9, 1, 20, 0, tzinfo=KST)


def _vod() -> Vod:
    return Vod(
        vod_id=VOD_ID,
        bid="방송인",
        title="다시보기 제목",
        total_duration=1_800_000,
        broad_start=BROAD_START,
        grade=0,
        hashtags=[],
    )


def _chat(t: float, message: str) -> Chat:
    return Chat(
        vod_id=VOD_ID,
        file_order=1,
        type="chat",
        time_offset=t,
        data={"message": message, "user_id": "시청자", "user_nick": "닉네임"},
    )


# ------------------------------------------- GET /review/{vod_id}


@pytest.mark.asyncio
async def test_get_vod(client, db):
    """성공 케이스. VOD 상세를 파트 목록과 함께 주는지."""
    db.add_all([Streamer(bid="방송인", nick="닉네임"), _vod()])
    await db.commit()
    db.add(
        VodFile(
            vod_id=VOD_ID,
            file_order=1,
            duration=1_800_000,
            hls_url="영상 주소",
            file_start=BROAD_START + timedelta(seconds=60),
        )
    )
    await db.commit()

    response = await client.get(f"/review/{VOD_ID}")

    assert response.status_code == 200
    body = response.json()
    assert (body["vod_id"], body["title"], body["nick"]) == (
        VOD_ID,
        "다시보기 제목",
        "닉네임",
    )
    assert body["views"] == 0
    assert body["files"] == [
        {
            "file_order": 1,
            "duration": 1_800_000,
            "hls_url": "영상 주소",
            "offset_base_sec": 60.0,
        }
    ]


@pytest.mark.asyncio
async def test_get_vod_unknown_returns_404(client):
    """없는 VOD 면 404 와 에러 코드를 주는지."""
    response = await client.get(f"/review/{VOD_ID}")

    assert response.status_code == 404
    assert response.json()["code"] == "VOD_NOT_FOUND"


@pytest.mark.asyncio
async def test_get_vod_rejects_non_number_id(client):
    """vod_id 가 숫자가 아니면 422 를 주는지."""
    response = await client.get("/review/다시보기")

    assert response.status_code == 422
    assert response.json()["code"] == "VALIDATION_ERROR"


# --------------------------------------- POST /review/{vod_id}/view


@pytest.mark.asyncio
async def test_add_view(client, db):
    """요청마다 1씩 올리고, 올린 뒤의 조회수를 주는지. GET 상세에도 반영되는지."""
    db.add(_vod())
    await db.commit()

    first = await client.post(f"/review/{VOD_ID}/view")
    second = await client.post(f"/review/{VOD_ID}/view")

    assert (first.status_code, first.json()) == (200, {"views": 1})
    assert (second.status_code, second.json()) == (200, {"views": 2})
    detail = await client.get(f"/review/{VOD_ID}")
    assert detail.json()["views"] == 2


@pytest.mark.asyncio
async def test_add_view_unknown_returns_404(client, db):
    """없는 VOD 면 404 와 에러 코드를 주고, 행을 만들지 않는지."""
    response = await client.post(f"/review/{VOD_ID}/view")

    assert response.status_code == 404
    assert response.json()["code"] == "VOD_NOT_FOUND"
    assert await db.get(VodView, VOD_ID) is None


# -------------------------------------- GET /review/{vod_id}/chats


@pytest.mark.asyncio
async def test_get_chats(client, db):
    """성공 케이스. start 부터 300초 구간의 채팅을 주는지."""
    db.add(_vod())
    await db.commit()
    db.add_all([_chat(10, "구간 앞"), _chat(310, "구간 안"), _chat(650, "구간 뒤")])
    await db.commit()

    response = await client.get(f"/review/{VOD_ID}/chats", params={"start": 300})

    assert response.status_code == 200
    body = response.json()
    assert (body["start"], body["end"], body["complete"]) == (300, 600, True)
    assert body["items"] == [
        {
            "type": "chat",
            "t": 310,
            "data": {"message": "구간 안", "user_nick": "닉네임"},
        }
    ]


@pytest.mark.parametrize(
    "params",
    [
        {},  # start 가 없음
        {"start": -1},  # 0 보다 작음
        {"start": "처음"},  # 숫자가 아님
    ],
)
@pytest.mark.asyncio
async def test_get_chats_rejects_bad_start(client, db, params):
    """start 가 없거나 0 보다 작거나 숫자가 아니면 422 를 주는지."""
    db.add(_vod())
    await db.commit()

    response = await client.get(f"/review/{VOD_ID}/chats", params=params)

    assert response.status_code == 422
    assert response.json()["code"] == "VALIDATION_ERROR"


@pytest.mark.asyncio
async def test_get_chats_start_zero_is_allowed(client, db):
    """start 는 0 부터 받는지(ge=0 의 경계)."""
    db.add(_vod())
    await db.commit()

    response = await client.get(f"/review/{VOD_ID}/chats", params={"start": 0})

    assert response.status_code == 200
    assert response.json()["start"] == 0
