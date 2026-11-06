"""
pytest tests/broadcast/review/shared/test_chat_parser.py
"""

import logging
from typing import Any
from xml.sax.saxutils import escape

import pytest

from app.broadcast.chat_rules import P2_BASIC, P2_PLUS, P_FAN, P_VIP
from app.broadcast.review.shared.chat_parser import parse_chat_xml

VOD_ID = 1000
FILE_ORDER = 2
OFFSET_BASE = 100.0  # 이 파트가 VOD 전체 기준 100초에서 시작
P_MANAGER = 1 << 6  # 매니저 비트 둘 중 하나


def _el(tag: str, fields: dict[str, str], t: str | None = "1.5") -> str:
    """채팅 한 건의 XML. t 를 None 으로 주면 t 태그를 넣지 않는다."""
    inner = "".join(f"<{k}>{escape(v)}</{k}>" for k, v in fields.items())
    if t is not None:
        inner += f"<t>{t}</t>"
    return f"<{tag}>{inner}</{tag}>"


def _xml(*elements: str) -> str:
    return "<root><version>1.0.0</version>" + "".join(elements) + "</root>"


def _parse(xml_text: str):
    return parse_chat_xml(
        xml_text, vod_id=VOD_ID, file_order=FILE_ORDER, offset_base=OFFSET_BASE
    )


def _data(tag: str, fields: dict[str, str]) -> dict[Any, Any]:
    """채팅 한 건만 넣고 파싱해서 data 를 돌려준다."""
    rows = _parse(_xml(_el(tag, fields)))
    assert len(rows) == 1
    return rows[0].data


# --------------------------------------------------------- 파싱 흐름


@pytest.mark.parametrize("xml_text", ["", "   \n"])
def test_empty_text_returns_empty(xml_text, caplog):
    """채팅이 없는 구간이라 비어 있는 것. 파싱 실패로 보지 않는다."""
    assert _parse(xml_text) == []
    assert "XML 파싱 실패" not in caplog.text


def test_broken_xml_returns_empty_and_warns(caplog):
    assert _parse("<root><chat>") == []
    assert "XML 파싱 실패" in caplog.text


def test_row_fields():
    [row] = _parse(_xml(_el("ice", {"value": "1"}, t="12.5")))

    assert row.vod_id == VOD_ID
    assert row.file_order == FILE_ORDER
    assert row.type == "ice"
    assert row.time_offset == OFFSET_BASE + 12.5  # 파트 기준 시간에 시작 초를 더한다


def test_keeps_order_of_chats():
    rows = _parse(
        _xml(
            _el("ice", {"value": "1"}, t="1"),
            _el("adm_notice", {"m": "안내"}, t="2"),
            _el("ice", {"value": "0"}, t="3"),
        )
    )

    assert [(r.type, r.time_offset) for r in rows] == [
        ("ice", 101.0),
        ("adm_notice", 102.0),
        ("ice", 103.0),
    ]


def test_version_is_skipped():
    assert _parse("<root><version>1.0.0</version></root>") == []


def test_logs_changed_version(caplog):
    caplog.set_level(logging.INFO)

    rows = _parse("<root><version>2.0.0</version></root>")

    assert rows == []
    assert "채팅 XML 버전 변경: 2.0.0" in caplog.text


@pytest.mark.parametrize(
    "element",
    [
        "<chat><t>1.5</t></chat>",  # 자식이 t 뿐
        "<chat></chat>",  # 자식이 없음
        _el("chat", {"m": "안녕"}, t=None),  # t 가 없음
        _el("chat", {"m": "안녕"}, t=""),  # t 가 비어 있음
        _el("chat", {"m": "안녕"}, t="일분"),  # t 가 숫자가 아님
    ],
)
def test_skips_non_chat_element(element):
    assert _parse(_xml(element)) == []


def test_missing_tag_is_none_and_empty_tag_is_empty_string():
    """태그가 없으면 None, 태그는 있는데 비어 있으면 빈 문자열."""
    data = _data("chat", {"m": "", "u": "시청자"})

    assert data["message"] == ""
    assert data["user_nick"] is None


# -------------------------------------------------------- 모르는 타입


def test_unknown_type_keeps_raw_tags(caplog):
    caplog.set_level(logging.INFO)

    [row] = _parse(_xml(_el("new_gift", {"u": "시청자(2)", "x": ""}, t="3")))

    # 원본 태그 이름 그대로, t 까지. 아이디 접미사도 그대로 둔다
    assert row.type == "new_gift"
    assert row.data == {"u": "시청자(2)", "x": "", "t": "3"}
    assert row.time_offset == 103.0
    assert "미등록 채팅 타입 발견: ['new_gift']" in caplog.text


def test_unknown_kind_keeps_raw_tags(caplog):
    caplog.set_level(logging.INFO)

    [row] = _parse(_xml(_el("battle_mission", {"type": "NEW_KIND", "c": "3"})))

    assert row.data == {"type": "NEW_KIND", "c": "3", "t": "1.5"}
    assert "battle_mission/NEW_KIND" in caplog.text


def test_kind_type_without_kind_tag_keeps_raw_tags():
    [row] = _parse(_xml(_el("challenge_mission", {"title": "미션"})))

    assert row.data == {"title": "미션", "t": "1.5"}


def test_unknown_types_logged_once_sorted(caplog):
    """같은 타입이 여러 번 와도 한 번만, 이름순으로 한 줄에 남긴다."""
    caplog.set_level(logging.INFO)
    # 집합(set)은 순서가 정해져 있지 않아서, 정렬하지 않으면 거의 항상 순서가 섞인다
    tags = ["echo", "alpha", "delta", "bravo", "charlie", "alpha"]

    _parse(_xml(*(_el(tag, {"a": "1"}) for tag in tags)))

    expected = "['alpha', 'bravo', 'charlie', 'delta', 'echo']"
    assert f"미등록 채팅 타입 발견: {expected}" in caplog.text
    assert caplog.text.count("미등록 채팅 타입 발견") == 1


# ---------------------------------------------------------------- 타입별 변환

USER = {"u": "시청자(2)", "n": "닉네임"}
USER_OUT = {"user_id": "시청자", "user_nick": "닉네임"}

CASES = [
    ("adinbroad", {"status": "1", "chno": "1"}, {"active": True}),
    ("adinbroad", {"status": "0", "chno": "1"}, {"active": False}),
    (
        "adballoon",
        {"bj": "방송인", **USER, "c": "5", "m": "메시지1", "em": "메시지2",
            "img_url": "이미지", "def_url": "기본"},
        {"bid": "방송인", **USER_OUT, "count": 5, "message1": "메시지1",
            "message2": "메시지2", "img_url": "이미지", "def_url": "기본"},
    ),
    ("adm_notice", {"type": "1", "m": "별별랭킹"}, {"message": "별별랭킹"}),
    (
        "balloon",
        {"bj": "방송인", **USER, "c": "10", "fs": "1"},
        {"bid": "방송인", **USER_OUT, "count": 10},
    ),
    (
        "bj_notice",
        {"ch": "1", "state": "1", "type": "1", "m": "채팅 규칙"},
        {"message": "채팅 규칙"}
    ),
    (
        "challenge_mission_settle",
        {"ch": "1", "fno": "1", **USER, "c": "7", "nfn": "x"},
        {**USER_OUT, "count": 7},
    ),
    (
        "chat",
        {"m": "안녕하세요", **USER, "p": str(P_MANAGER | P_FAN), "p2": str(P2_BASIC), "fw": "4"},
        {"message": "안녕하세요", **USER_OUT, "badge": "manager",
            "subscriber": {"tier": "basic", "months": 4}},
    ),
    ("fanclub", USER, USER_OUT),
    (
        "fanletter",
        {"bj": "방송인", "bjnick": "방송닉", **USER, "c": "2", "lettermessage": "응원해요"},
        {"bid": "방송인", **USER_OUT, "count": 2, "message": "응원해요"},
    ),
    (
        "follow",
        {"bj": "방송인", **USER, "type": "1", "period": "3", "acperiod": "9", "stier": "2"},
        {"bid": "방송인", **USER_OUT, "subscriber": {"tier": "plus", "months": 3}},
    ),
    ("ice", {"value": "1"}, {"frozen": True}),
    ("ice", {"value": "0"}, {"frozen": False}),
    (
        "ogq",
        {"ch": "1", "m": "이모티콘", "gid": "그룹1", "sid": "42", "s": "시청자(3)",
            "sn": "닉네임", "sf": f"{P_VIP}|{P2_PLUS}", "fw": "6"},
        {
            "message": "이모티콘", **USER_OUT, "badge": "vip",
            "subscriber": {"tier": "plus", "months": 6},
            "group_id": "그룹1", "sticker_id": 42
        },
    ),
    (
        "quick_random",
        {
            "sender": "보낸사람(2)", "sendernick": "보낸닉",
            "chno": "1", "cnt": "3", "itemcode": "11"
        },
        {
            "from_user_id": "보낸사람", "from_user_nick": "보낸닉",
            "count": 3, "item_code": 11
        },
    ),
    (
        "quickview",
        {"sender": "보낸사람", "sendernick": "보낸닉", **USER, "quickviewtype": "30"},
        {"from_user_id": "보낸사람", "from_user_nick": "보낸닉",
            "to_user_id": "시청자", "to_user_nick": "닉네임", "item_code": 30},
    ),
    (
        "sendogq",
        {"sendid": "보낸사람", "sendnick": "보낸닉", "recvid": "받는사람(2)",
            "recvnick": "받는닉", "title": "이모티콘 이름", "url": "주소"},
        {"from_user_id": "보낸사람", "from_user_nick": "보낸닉", "to_user_id": "받는사람",
            "to_user_nick": "받는닉", "item_name": "이모티콘 이름", "img_url": "주소"},
    ),
    (
        "stationadcon",
        {"bj": "방송인", "v": "시청자(2)", "vn": "닉네임", "cnt": "8",
            "default": "기본", "msg": "애드콘"},
        {"bid": "방송인", **USER_OUT, "count": 8, "message": "애드콘", "def_url": "기본"},
    ),
    (
        "subscription",
        {"ch": "1", "s": "보낸사람", "sn": "보낸닉", "r": "받는사람", "rn": "받는닉", "it": "3"},
        {"from_user_id": "보낸사람", "from_user_nick": "보낸닉",
            "to_user_id": "받는사람", "to_user_nick": "받는닉", "item_code": 3},
    ),
    (
        "battle_mission",
        {"type": "GIFT", "ch": "1", "bj": "방송인", "bj_nick": "방송닉", **USER,
            "c": "50", "key": "9", "title": "대결"},
        {"kind": "GIFT", "bid": "방송인", "bj_nick": "방송닉", **USER_OUT,
            "count": 50, "mission_key": 9, "title": "대결"},
    ),
    (
        "battle_mission",
        {"type": "NOTICE", "draw": "1", "winner": "이긴쪽", "loser": "진쪽",
            "key": "9", "title": "대결"},
        {"kind": "NOTICE", "winner": "이긴쪽", "loser": "진쪽", "draw": True,
            "mission_key": 9, "title": "대결"},
    ),
    (
        "battle_mission",
        {"type": "NOTICE", "draw": "0", "winner": "이긴쪽", "loser": "진쪽",
            "key": "9", "title": "대결"},
        {"kind": "NOTICE", "winner": "이긴쪽", "loser": "진쪽", "draw": False,
            "mission_key": 9, "title": "대결"},
    ),
    (
        "battle_mission",
        {"type": "SETTLE", "bj": "방송인", "bj_nick": "방송닉", "c": "70",
            "key": "9", "title": "대결"},
        {"kind": "SETTLE", "bid": "방송인", "bj_nick": "방송닉", "count": 70,
            "mission_key": 9, "title": "대결"},
    ),
    (
        "challenge_mission",
        {"type": "CHALLENGE_GIFT", "ch": "1", "subch": "1", "key": "4", "title": "도전",
         "c": "20", **USER, "bj": "방송인", "bj_nick": "방송닉"},
        {"kind": "CHALLENGE_GIFT", "bid": "방송인", "bj_nick": "방송닉", **USER_OUT,
            "count": 20, "mission_key": 4, "title": "도전"},
    ),
    (
        "challenge_mission",
        {"type": "CHALLENGE_NOTICE", "title": "도전", "ms": "성공", "key": "0"},
        {"kind": "CHALLENGE_NOTICE", "title": "도전", "result": "성공"},
    ),
    (
        "challenge_mission",
        {"type": "CHALLENGE_SETTLE", "subch": "1", "key": "4", "title": "도전",
            "c": "30", "bj": "방송인", "bj_nick": "방송닉"},
        {"kind": "CHALLENGE_SETTLE", "bid": "방송인", "bj_nick": "방송닉", "count": 30,
            "mission_key": 4, "title": "도전"},
    ),
]  # fmt: skip


@pytest.mark.parametrize(("tag", "fields", "expected"), CASES)
def test_converts_known_type(tag, fields, expected):
    assert _data(tag, fields) == expected


@pytest.mark.parametrize(
    ("alias", "same_as", "fields"),
    [
        ("vballoon", "balloon", {"bj": "방송인", **USER, "c": "10"}),
        ("vod_balloon", "balloon", {"bj": "방송인", **USER, "c": "10"}),
        ("supporter", "fanclub", USER),
        ("topfan", "fanclub", USER),
        ("vodadcon", "stationadcon", {"bj": "방송인", "v": "", "vn": "", "cnt": "1"}),
        (
            "sub_random",
            "quick_random",
            {"sender": "보낸사람", "cnt": "2", "itemcode": "5"},
        ),
    ],
)
def test_types_sharing_a_converter(alias, same_as, fields):
    """같은 모양의 타입은 같은 규칙으로 바꾼다."""
    assert _data(alias, fields) == _data(same_as, fields)
