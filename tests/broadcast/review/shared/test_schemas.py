"""
pytest tests/broadcast/review/shared/test_schemas.py
"""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.broadcast.review.shared.schemas import ChatRow, VodFileRow, VodRow
from app.utils.tz import KST


def _vod(**fields) -> VodRow:
    """필수 값만 채운 상세 응답에 fields 를 덮어써서 VodRow 로 만든다."""
    return VodRow(
        **{
            "vod_id": 1000,
            "bj_id": "방송인",
            "broad_start": "2026-09-04 19:25:20",
            **fields,
        }
    )


# ----------------------------------------------------------- VodRow 기본


def test_vod_maps_aliases():
    vod = _vod(
        full_title="방송 제목",
        total_file_duration=3_600_000,
        write_tm="2026-09-04 19:25:20 ~ 2026-09-05 05:17:11",
    )

    assert vod.bid == "방송인"
    assert vod.title == "방송 제목"
    assert vod.total_duration == 3_600_000
    assert vod.broad_end == datetime(2026, 9, 5, 5, 17, 11, tzinfo=KST)


def test_vod_accepts_field_names_too():
    """alias 가 아니라 필드 이름으로 넣어도 받는다(populate_by_name)."""
    vod = VodRow(
        vod_id=1000, bid="방송인", title="방송 제목", broad_start=datetime(2026, 9, 4)
    )

    assert vod.bid == "방송인"
    assert vod.title == "방송 제목"


def test_vod_defaults():
    vod = _vod()

    assert vod.title == ""
    assert vod.total_duration == 0
    assert vod.broad_end is None
    assert vod.grade == 0
    assert vod.chat_duration == 300
    assert vod.media_code is None
    assert vod.hashtags == []


def test_vod_ignores_unknown_keys():
    vod = _vod(새로운_키="무엇이든")

    assert not hasattr(vod, "새로운_키")


def test_vod_strips_whitespace():
    assert _vod(full_title="  방송 제목  ").title == "방송 제목"


# ---------------------------------------------------------- media_code


def test_media_code_from_media_exception():
    assert _vod(media_exception={"code": -6303, "msg": "잠김"}).media_code == -6303


def test_media_code_keeps_given_value():
    """media_code 가 이미 있으면 media_exception 보다 먼저다."""
    vod = _vod(media_code=1, media_exception={"code": -6303})

    assert vod.media_code == 1


@pytest.mark.parametrize("media_exception", [None, "잠김", []])
def test_media_code_none_when_not_dict(media_exception):
    assert _vod(media_exception=media_exception).media_code is None


# --------------------------------------------------------- write_tm


def test_write_tm_without_end_is_none():
    assert _vod(write_tm="2026-09-04 19:25:20 ~ ").broad_end is None


@pytest.mark.parametrize("write_tm", ["2026-09-05 05:17:11", None])
def test_write_tm_unknown_format_is_none_and_warns(write_tm, caplog):
    vod = _vod(write_tm=write_tm)

    assert vod.broad_end is None
    assert "write_tm 포맷 변경 감지" in caplog.text


def test_write_tm_missing_does_not_warn(caplog):
    """키가 아예 없으면 기본값(None)이라 검사를 거치지 않는다."""
    vod = _vod()

    assert vod.broad_end is None
    assert "write_tm" not in caplog.text


# --------------------------------------------------------- permanence


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("Y", True),
        (" y ", True),
        ("N", False),
        ("", False),
        (True, True),  # 이미 bool 이면 그대로
        (None, None),
    ],
)
def test_permanence(value, expected):
    assert _vod(permanence=value).permanence is expected


# ------------------------------------------------------------ 시간대


@pytest.mark.parametrize("field", ["broad_start", "auto_delete_date"])
def test_naive_time_becomes_kst(field):
    vod = _vod(**{field: "2026-09-04 19:25:20"})

    assert getattr(vod, field) == datetime(2026, 9, 4, 19, 25, 20, tzinfo=KST)


def test_aware_time_keeps_its_timezone():
    """시간대가 이미 있으면 KST 로 바꿔 붙이지 않는다."""
    vod = _vod(broad_start="2026-09-04T10:25:20+00:00")

    assert vod.broad_start == datetime(2026, 9, 4, 10, 25, 20, tzinfo=UTC)
    assert vod.broad_start.tzinfo == UTC


# ----------------------------------------------------------- hashtags


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("게임,토크", ["게임", "토크"]),
        (" 게임 , ,토크, ", ["게임", "토크"]),  # 앞뒤 공백과 빈 칸은 버린다
        ("", []),
        (None, []),
        (["게임", "토크"], ["게임", "토크"]),  # 이미 리스트면 그대로
    ],
)
def test_hashtags(value, expected):
    assert _vod(hashtags=value).hashtags == expected


# ------------------------------------------------------------- VodFileRow


def test_vod_file_maps_aliases_and_kst():
    f = VodFileRow(
        vod_id=1000,
        file_order=1,
        duration=1_800_000,
        chat="채팅 주소",
        file="영상 주소",
        file_start="2026-09-04 19:30:00",
    )

    assert f.chat_url == "채팅 주소"
    assert f.hls_url == "영상 주소"
    assert f.file_start == datetime(2026, 9, 4, 19, 30, tzinfo=KST)


def test_vod_file_defaults():
    f = VodFileRow(vod_id=1000, file_order=1)

    assert f.duration == 0
    assert f.chat_url is None
    assert f.hls_url is None
    assert f.file_start is None


# --------------------------------------------------------- ChatRow


def test_chat_row_rejects_unknown_keys():
    """chat_parser 가 직접 채워 넣으니, 모르는 키가 오면 실수다."""
    with pytest.raises(ValidationError):
        ChatRow(
            vod_id=1000,
            file_order=1,
            type="chat",
            time_offset=1.5,
            data={},
            모르는_키="값",  # type: ignore[call-arg]
        )
