"""
pytest tests/broadcast/live/shared/test_chat_parser.py
"""

import pytest

from app.broadcast.chat_rules import P2_BASIC, P2_PLUS, P_FAN, P_VIP
from app.broadcast.live.shared.chat_parser import (
    LiveChat,
    LivePacket,
    parse_packet,
    to_chat,
)


def _raw(svc: str, parts: list[str]) -> bytes:
    """받은 패킷 모양으로 만든다. 본문 길이 칸은 parse_packet 이 안 보니 0으로 채운다.

    모양: ESC TAB + 서비스 코드 4자리 + 본문 길이 6자리 + "00" + SEP 칸 SEP 칸 ... SEP
    """
    body = "".join(f"\x0c{p}" for p in parts) + "\x0c"
    return b"\x1b\t" + svc.encode() + b"000000" + b"00" + body.encode()


# ---------------------------------------------------------------- parse_packet


def test_parse_packet_splits_svc_and_parts():
    packet = parse_packet(_raw("0005", ["안녕하세요", "시청자"]))

    # 마지막 SEP 뒤의 빈 칸까지 들어간다
    assert packet == LivePacket("0005", ["안녕하세요", "시청자", ""])


def test_parse_packet_header_only():
    """헤더만 있고 SEP 이 없으면 칸이 없다."""
    assert parse_packet(b"\x1b\t0005") == LivePacket("0005", [])


@pytest.mark.parametrize(
    "raw",
    [
        "0005000000\x0c안녕".encode(),  # ESC TAB 으로 시작하지 않음
        b"\x1b\t000",  # 서비스 코드가 4자리가 안 됨
        b"",
    ],
)
def test_parse_packet_rejects_non_packet(raw):
    assert parse_packet(raw) is None


def test_parse_packet_replaces_broken_text():
    """UTF-8 이 아닌 바이트가 섞여도 에러 없이 바꿔 읽는다."""
    raw = b"\x1b\t000500000000\x0c\xff\xfe\x0c"

    packet = parse_packet(raw)

    assert packet is not None
    assert packet.parts == ["��", ""]


# ---------------------------------------------------------------- to_chat


def test_to_chat_unknown_svc_is_none():
    assert to_chat(LivePacket("9999", ["무엇이든"])) is None


def test_to_chat_chat():
    # 0005: [메시지, 아이디, 0, 0, 언어, 닉네임, 플래그1|플래그2, 개월, 색상, 색상, 개월, ...]
    parts = ["안녕하세요", "시청자(2)", "0", "0", "ko", "닉네임",
        f"{P_FAN}|{P2_PLUS}", "3", "색상", "색상", "5", ""]  # fmt: skip

    assert to_chat(LivePacket("0005", parts)) == LiveChat(
        "chat",
        {
            "message": "안녕하세요",
            "user_id": "시청자",
            "user_nick": "닉네임",
            "badge": "fan",
            "subscriber": {"tier": "plus", "months": 3},
        },
    )


def test_to_chat_chat_with_missing_parts():
    """칸이 모자라도 에러 없이 None 과 기본값으로 채운다."""
    assert to_chat(LivePacket("0005", ["안녕하세요"])) == LiveChat(
        "chat",
        {
            "message": "안녕하세요",
            "user_id": None,
            "user_nick": None,
            "badge": "none",
            "subscriber": {"tier": "none", "months": 0},
        },
    )


def test_to_chat_ogq():
    # 0109: [ch, m, gid, sid, v, s, sn, sf, c, l, mt, e, fw, ...]
    parts = ["9548", "이모티콘 메시지", "그룹1", "42", "1", "시청자(3)", "닉네임",
        f"{P_VIP}|{P2_BASIC}", "c", "l", "mt", "e", "2", ""]  # fmt: skip

    assert to_chat(LivePacket("0109", parts)) == LiveChat(
        "ogq",
        {
            "message": "이모티콘 메시지",
            "user_id": "시청자",
            "user_nick": "닉네임",
            "badge": "vip",
            "subscriber": {"tier": "basic", "months": 2},
            "group_id": "그룹1",
            "sticker_id": 42,
        },
    )


def test_to_chat_balloon():
    # 0018: [bid, 후원자 id, 닉네임, 개수, 0, 0, CHATNO, 연출 파일 이름, ...]
    parts = ["방송인", "후원자(2)", "후원닉", "100", "0", "0", "chat-1", "연출", ""]

    assert to_chat(LivePacket("0018", parts)) == LiveChat(
        "balloon",
        {
            "bid": "방송인",
            "user_id": "후원자",
            "user_nick": "후원닉",
            "count": 100,
        },
    )


def test_to_chat_follow_new_is_always_one_month():
    # 0091: [CHATNO, bid, id, 닉네임, 코드, 1, 누적 개월, 등급, uuid, 언어, 시각]
    parts = ["chat-1", "방송인", "구독자", "구독닉", "코드", "1", "5", "2",
        "uuid", "ko", "시각"]  # fmt: skip

    # 누적 개월(5)이 있어도 신규 구독은 1개월로 본다
    assert to_chat(LivePacket("0091", parts)) == LiveChat(
        "follow",
        {
            "bid": "방송인",
            "user_id": "구독자",
            "user_nick": "구독닉",
            "subscriber": {"tier": "plus", "months": 1},
        },
    )


def test_to_chat_follow_months():
    # 0093: [bid, id, 닉네임, 연속 개월, CHATNO, 코드, 누적 개월, 등급, uuid, 언어, 시각]
    parts = ["방송인", "구독자(2)", "구독닉", "7", "chat-1", "코드", "9", "1",
        "uuid", "ko", "시각"]  # fmt: skip

    # 누적(9)이 아니라 연속 개월(7)을 쓴다
    assert to_chat(LivePacket("0093", parts)) == LiveChat(
        "follow",
        {
            "bid": "방송인",
            "user_id": "구독자",
            "user_nick": "구독닉",
            "subscriber": {"tier": "basic", "months": 7},
        },
    )


def test_parse_then_convert():
    """받은 bytes 부터 채팅 한 건까지 이어서."""
    raw = _raw("0018", ["방송인", "후원자", "후원닉", "10"])

    packet = parse_packet(raw)

    assert packet is not None
    chat = to_chat(packet)
    assert chat is not None
    assert chat.type == "balloon"
    assert chat.data["count"] == 10
