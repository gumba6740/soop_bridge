"""SOOP 라이브 채팅 패킷 → LivePacket → 채팅 한 건(type, data) 변환.

네트워크를 건드리지 않는 순수 함수 모듈.
chat_client(core)는 받은 패킷을 원본 bytes로 넘기기만 하고, 나누고 거르는 일은 여기서 한다.
웹소켓 접속과 0007 처리는 chat_client가 한다.


패킷 구조
---------
    ESC TAB {서비스 코드 4자리}{본문 길이 6자리}00 SEP 칸0 SEP 칸1 SEP ... SEP

- SEP(\\x0c)로 나누면 첫 덩어리가 헤더(ESC, TAB, 서비스 코드, 길이, 00)이고, 나머지가 본문의 칸이다.
- 서비스 코드는 svc로, 본문의 칸은 parts로 나눠 담는다. 헤더의 나머지(길이, 00)는 버린다.
- 코드별 칸의 뜻은 docs/live_chat_packets.md 참고. 마지막 칸은 보통 빈 문자열이다.

변환
----
- 다시보기 채팅과 같은 type 이름, 같은 data 키를 쓴다(docs/review_chat_types.md).
    값을 만드는 규칙(배지, 구독, 아이디 접미사 등)은 app/chat_rules.py(다시보기와 공용)에 있다.
- CONVERTERS에 있는 서비스 코드만 변환한다. 나머지는 버린다(로그도 남기지 않는다).
- 칸 번호는 parts 기준(0부터).
"""

from collections.abc import Callable
from typing import Any, NamedTuple

from app.broadcast.chat_rules import badge, num, stier, sub, tier, uid

HEADER_START = b"\x1b\t"
SEP = b"\x0c"

Data = dict[str, Any]


class LivePacket(NamedTuple):
    svc: str  # 서비스 코드 4자리. 예: "0005"
    parts: list[
        str
    ]  # 헤더를 뺀 본문의 칸. 0005면 parts[0]이 메시지, parts[1]이 유저 id


class LiveChat(NamedTuple):
    type: str  # 다시보기와 같은 타입 이름. 예: "chat"
    data: Data  # 다시보기와 같은 키


def parse_packet(raw: bytes) -> LivePacket | None:
    """웹소켓으로 받은 패킷 하나를 서비스 코드와 칸으로 나눈다.

    Returns:
        LivePacket. 패킷 모양이 아니면(시작이 ESC TAB이 아니거나 너무 짧으면) None.
    """
    if not raw.startswith(HEADER_START) or len(raw) < 6:
        return None
    svc = raw[2:6].decode("ascii", errors="replace")
    parts = [p.decode(errors="replace") for p in raw.split(SEP)[1:]]
    return LivePacket(svc, parts)


# ------------------------------------------------------ 코드별 변환


def _at(parts: list[str], i: int) -> str | None:
    """칸이 모자라면 None (다시보기에서 태그가 없을 때와 같다)."""
    return parts[i] if i < len(parts) else None


def _flags(parts: list[str], i: int) -> tuple[int | None, int | None]:
    """ "플래그1|플래그2" 칸을 두 숫자로."""
    p, _, p2 = (_at(parts, i) or "").partition("|")
    return num(p), num(p2)


def _chat(parts: list[str]) -> Data:
    # 0005: [메시지, 아이디, 0, 0, 언어, 닉네임, 플래그1|플래그2, 개월(fw),
    #        색상, 색상, 개월(acfw), ...]
    p, p2 = _flags(parts, 6)
    return {
        "message": _at(parts, 0),
        "user_id": uid(_at(parts, 1)),
        "user_nick": _at(parts, 5),
        "badge": badge(p),
        "subscriber": sub(tier(p2), num(_at(parts, 7))),
    }


def _ogq(parts: list[str]) -> Data:
    # 0109: 다시보기 ogq와 칸 순서가 같다
    # [ch, m, gid, sid, v, s, sn, sf, c, l, mt, e, fw, nf, nd, acfw, ...]
    p, p2 = _flags(parts, 7)
    return {
        "message": _at(parts, 1),
        "user_id": uid(_at(parts, 5)),
        "user_nick": _at(parts, 6),
        "badge": badge(p),
        "subscriber": sub(tier(p2), num(_at(parts, 12))),
        "group_id": _at(parts, 2),
        "sticker_id": num(_at(parts, 3)),
    }


def _balloon(parts: list[str]) -> Data:
    # 0018: [bid, 후원자 id, 닉네임, 개수, 0, 0, CHATNO, 연출 파일 이름, ...]
    return {
        "bid": _at(parts, 0),
        "user_id": uid(_at(parts, 1)),
        "user_nick": _at(parts, 2),
        "count": num(_at(parts, 3)),
    }


def _follow_new(parts: list[str]) -> Data:
    # 0091 신규 구독: [CHATNO, bid, id, 닉네임, 코드, 1, 누적 개월, 등급, uuid, 언어, 시각]
    # 첫 달이라 연속 개월 칸이 없다. 항상 1개월로 본다
    return {
        "bid": _at(parts, 1),
        "user_id": uid(_at(parts, 2)),
        "user_nick": _at(parts, 3),
        "subscriber": sub(stier(_at(parts, 7)), 1),
    }


def _follow_months(parts: list[str]) -> Data:
    # 0093 N개월째 구독:
    # [bid, id, 닉네임, 연속 개월, CHATNO, 코드, 누적 개월, 등급, uuid, 언어, 시각]
    return {
        "bid": _at(parts, 0),
        "user_id": uid(_at(parts, 1)),
        "user_nick": _at(parts, 2),
        "subscriber": sub(stier(_at(parts, 7)), num(_at(parts, 3))),
    }


# 서비스 코드 → (다시보기 type, 변환 함수). 여기 없는 코드는 버린다.
CONVERTERS: dict[str, tuple[str, Callable[[list[str]], Data]]] = {
    "0005": ("chat", _chat),
    "0109": ("ogq", _ogq),
    "0018": ("balloon", _balloon),
    "0091": ("follow", _follow_new),
    "0093": ("follow", _follow_months),
}


def to_chat(packet: LivePacket) -> LiveChat | None:
    """패킷 하나를 채팅 한 건으로. 변환하지 않는 코드면 None."""
    conv = CONVERTERS.get(packet.svc)
    if conv is None:
        return None
    type_, func = conv
    return LiveChat(type_, func(packet.parts))
