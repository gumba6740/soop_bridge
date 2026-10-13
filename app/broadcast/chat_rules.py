"""다시보기와 라이브 채팅이 같이 쓰는 판정 규칙.

값을 꺼내는 방법(다시보기는 XML 태그, 라이브는 칸 번호)은 각 파서가 따로 하고,
꺼낸 값을 data로 바꾸는 규칙만 여기 둔다. 두 쪽의 결과가 같아야 하므로 한 곳에서 고친다.

    app/broadcast/review/shared/chat_parser.py  (다시보기)
    app/broadcast/live/shared/chat_parser.py    (라이브)

기존 다시보기 데이터를 옮긴 마이그레이션(alembic 261012_0030)도 같은 규칙을 SQL로 갖고 있다.
규칙을 바꾸면 이미 저장된 데이터와 달라진다. 근거는 docs/review_chat_types.md, docs/live_chat_packets.md.
"""

import re
from typing import Any

# 접미사 제거에 사용. "viewer01(2)" -> "viewer01"
_SESSION_SUFFIX = re.compile(r"\(\d+\)$")
_INT = re.compile(r"^-?\d+$")

# 플래그1(p) 비트
P_STREAMER = 1 << 2               # 방송 주인
P_MANAGER = (1 << 6) | (1 << 8)   # 매니저 (둘 중 어느 것인지 모름)
P_VIP = 1 << 15                   # 열혈
P_FAN = 1 << 5                    # 팬
P_SUPPORTER = 1 << 20             # 서포터
# 플래그2(p2) 비트
P2_PLUS = 1 << 19
P2_BASIC = 1 << 18


def uid(v: str | None) -> str | None:
    """아이디 뒤의 (2) 같은 중복 접속 접미사를 뗀다."""
    return None if v is None else _SESSION_SUFFIX.sub("", v)


def num(v: str | None) -> int | None:
    """숫자가 아니면 None."""
    return int(v) if v is not None and _INT.match(v) else None


def badge(p: int | None) -> str:
    """배지 하나. 위에 있는 것이 이긴다. 값이 없으면 none."""
    if p is None:
        return "none"
    if p & P_STREAMER:
        return "streamer"
    if p & P_MANAGER:
        return "manager"
    if p & P_VIP:
        return "vip"
    if p & P_FAN:
        return "fan"
    if p & P_SUPPORTER:
        return "supporter"
    return "none"


def tier(p2: int | None) -> str:
    """플래그2로 구독 등급."""
    if p2 is None:
        return "none"
    if p2 & P2_PLUS:
        return "plus"
    if p2 & P2_BASIC:
        return "basic"
    return "none"


def stier(v: str | None) -> str:
    """구독 알림의 등급 값(1 베이직, 2 플러스)으로 구독 등급."""
    return {"1": "basic", "2": "plus"}.get(v or "", "none")


def sub(tier_name: str, months: int | None) -> dict[str, Any]:
    """{tier, months}. 비구독이거나 개월 수가 없으면 months 0."""
    if tier_name == "none" or months is None:
        return {"tier": tier_name, "months": 0}
    return {"tier": tier_name, "months": max(months, 0)}


def public(data: dict[str, Any]) -> dict[str, Any]:
    """API로 내보낼 data. 유저 아이디(user_id, from_user_id, to_user_id)를 뺀다."""
    return {k: v for k, v in data.items() if not k.endswith("user_id")}