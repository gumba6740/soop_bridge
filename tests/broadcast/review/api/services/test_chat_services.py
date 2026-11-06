"""
pytest tests/broadcast/review/api/services/test_chat_services.py
"""

from datetime import datetime, timedelta

import pytest

from app.broadcast.review.api.exceptions import VodNotFound
from app.broadcast.review.api.services import chat_services
from app.broadcast.review.api.services.chat_services import _PartProgress
from app.core.config import settings
from app.models import Chat, ChatProgress, Vod, VodFile
from app.utils.tz import KST

VOD_ID = 1000
OTHER_VOD_ID = 2000
BROAD_START = datetime(2026, 9, 1, 20, 0, tzinfo=KST)


async def _add(db, *objs) -> None:
    """넣고 바로 커밋한다. 외래키 때문에 vod → 파트 → 진행 상황 순서로 나눠 부른다."""
    db.add_all(objs)
    await db.commit()


def _vod(vod_id: int = VOD_ID) -> Vod:
    return Vod(vod_id=vod_id, bid="방송인", broad_start=BROAD_START)


def _part(
    file_order: int,
    start_sec: float | None,
    duration_ms: int,
    *,
    vod_id: int = VOD_ID,
    chat_url: str | None = "채팅 주소",
) -> VodFile:
    """방송 시작부터 start_sec 초 뒤에 시작하는 파트. start_sec 가 None 이면 시작 시각 없음."""
    file_start = (
        None if start_sec is None else BROAD_START + timedelta(seconds=start_sec)
    )
    return VodFile(
        vod_id=vod_id,
        file_order=file_order,
        duration=duration_ms,
        chat_url=chat_url,
        file_start=file_start,
    )


def _progress(file_order: int, last_start_time: int, *, done: bool = False):
    return ChatProgress(
        vod_id=VOD_ID, file_order=file_order, last_start_time=last_start_time, done=done
    )


def _chat(t: float, message: str, *, vod_id: int = VOD_ID, data: dict | None = None):
    return Chat(
        vod_id=vod_id,
        file_order=1,
        type="chat",
        time_offset=t,
        data=data or {"message": message, "user_id": "시청자", "user_nick": "닉네임"},
    )


# ---------------------------------------------------------------- _part_progress


@pytest.mark.asyncio
async def test_part_progress(db):
    """성공 케이스 + 저장 순서가 섞여 있어도 file_order 순으로 나오는지."""
    await _add(db, _vod())
    # 일부러 file_order 를 섞어서 넣는다
    await _add(
        db,
        _part(3, 7200, 600_000),  # 진행 상황 없음
        _part(1, 60, 3_600_000),  # 다 받음
        _part(2, 3700, 1_800_000),  # 600초까지 받음
    )
    # 1번은 마지막 구간에 채팅이 없어 위치가 끝(3600)에 못 미친 채 done 이 된 경우
    await _add(db, _progress(1, 3300, done=True), _progress(2, 600))

    assert await chat_services._part_progress(db, VOD_ID) == [
        _PartProgress(60, 3660, 3660),
        _PartProgress(3700, 5500, 4300),
        _PartProgress(7200, 7800, 7200),
    ]


@pytest.mark.asyncio
async def test_part_progress_only_collectable_parts(db):
    """채팅 주소·시작 시각·길이가 없는 파트와 다른 VOD 의 파트는 빼는지."""
    await _add(db, _vod(), _vod(OTHER_VOD_ID))
    await _add(
        db,
        _part(1, 0, 600_000),  # 이것만 남아야 한다
        _part(2, 600, 600_000, chat_url=None),
        _part(3, None, 600_000),
        _part(4, 1800, 0),
        _part(1, 0, 600_000, vod_id=OTHER_VOD_ID),
    )

    assert await chat_services._part_progress(db, VOD_ID) == [_PartProgress(0, 600, 0)]


@pytest.mark.asyncio
async def test_part_progress_caps_collected_at_part_end(db):
    """저장된 위치가 파트 길이를 넘으면 파트 끝으로 자르는지."""
    await _add(db, _vod())
    await _add(db, _part(1, 0, 1_000_000))  # 1000초짜리 파트
    await _add(db, _progress(1, 1200))  # 수집기가 마지막에 300을 더해 저장한 값

    assert await chat_services._part_progress(db, VOD_ID) == [
        _PartProgress(0, 1000, 1000)
    ]


@pytest.mark.asyncio
async def test_part_progress_no_parts(db):
    """파트가 하나도 없으면 빈 목록인지."""
    await _add(db, _vod())

    assert await chat_services._part_progress(db, VOD_ID) == []


# ---------------------------------------------------------------- _is_complete

PARTS = [
    _PartProgress(0, 100, 90),  # 파트1: 90까지만 받음
    _PartProgress(100, 200, 200),  # 파트2: 다 받음
    _PartProgress(200, 300, 250),  # 파트3: 250까지 받음
]


def test_is_complete_true():
    """안 겹치는 파트는 건너뛰고, 걸친 파트는 구간 안쪽만 보는지."""
    assert chat_services._is_complete(PARTS, 100, 250) is True


def test_is_complete_false():
    """걸친 파트를 덜 받았으면 False 인지."""
    assert chat_services._is_complete(PARTS, 50, 150) is False


# ---------------------------------------------------------------- _enqueue


@pytest.mark.asyncio
async def test_enqueue_once(r):
    """같은 VOD 를 여러 번 요청해도 큐에는 한 번만 넣는지."""
    await chat_services._enqueue(r, VOD_ID)
    await chat_services._enqueue(r, VOD_ID)

    assert await r.lrange(settings.chat_job_queue_key, 0, -1) == [str(VOD_ID)]
    assert await r.smembers(settings.chat_job_pending_key) == {str(VOD_ID)}


# ---------------------------------------------------------------- get_chats


@pytest.mark.asyncio
async def test_get_chats_unknown_vod_raises(db, r):
    """없는 VOD 면 VodNotFound 를 내는지."""
    with pytest.raises(VodNotFound):
        await chat_services.get_chats(db, r, VOD_ID, start=0)


@pytest.mark.parametrize("done", [False, True])
@pytest.mark.asyncio
async def test_get_chats_enqueues_unfinished_vod(db, r, done):
    """수집이 덜 끝났으면 큐에 넣고 complete=False, 다 끝났으면 안 넣고 True 인지."""
    await _add(db, _vod())
    await _add(db, _part(1, 0, 600_000))
    await _add(db, _progress(1, 600 if done else 100, done=done))

    window = await chat_services.get_chats(db, r, VOD_ID, start=0)

    assert window.complete is done
    queued = await r.lrange(settings.chat_job_queue_key, 0, -1)
    assert queued == ([] if done else [str(VOD_ID)])


@pytest.mark.asyncio
async def test_get_chats_excludes_unknown_types(db, r):
    """t 라는 키가 있으면(모르는 타입) 결과에서 빼는지."""
    await _add(db, _vod())
    await _add(
        db,
        _chat(10, "아는 타입"),
        _chat(20, "", data={"u": "시청자(2)", "x": "값", "t": "20"}),
    )

    window = await chat_services.get_chats(db, r, VOD_ID, start=0)

    assert [item.data["message"] for item in window.items] == ["아는 타입"]


@pytest.mark.asyncio
async def test_get_chats(db, r):
    """구간 [start, start + 300) 의 이 VOD 채팅을 시간순으로, 아이디는 빼고 주는지."""
    await _add(db, _vod(), _vod(OTHER_VOD_ID))
    # 일부러 시간 순서를 섞어서 넣는다. 셋째와 넷째는 시간이 같아서 넣은 순서(id)로 정한다
    await _add(
        db,
        _chat(450, "셋째"),
        _chat(300, "첫째"),  # 딱 start: 들어온다
        _chat(600, "구간 밖"),  # 딱 end: 빠진다
        _chat(299.5, "구간 앞"),
        _chat(450, "넷째"),
        _chat(310, "둘째"),
        _chat(400, "다른 VOD", vod_id=OTHER_VOD_ID),
    )

    window = await chat_services.get_chats(db, r, VOD_ID, start=300)

    assert (window.start, window.end) == (300, 600)
    assert [(item.t, item.data["message"]) for item in window.items] == [
        (300, "첫째"),
        (310, "둘째"),
        (450, "셋째"),
        (450, "넷째"),
    ]
    assert window.items[0].type == "chat"
    assert window.items[0].data == {"message": "첫째", "user_nick": "닉네임"}
