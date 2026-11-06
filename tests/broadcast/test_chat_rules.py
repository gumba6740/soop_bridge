"""
pytest tests/broadcast/test_chat_rules.py
"""

import pytest

from app.broadcast.chat_rules import (
    P2_BASIC,
    P2_PLUS,
    P_FAN,
    P_STREAMER,
    P_SUPPORTER,
    P_VIP,
    badge,
    num,
    public,
    stier,
    sub,
    tier,
    uid,
)

# 매니저 비트는 둘 중 어느 것이든 매니저다
P_MANAGER_A = 1 << 6
P_MANAGER_B = 1 << 8
P_OTHER = 1 << 0  # 배지와 상관없는 비트


# ---------------------------------------------------------------- uid


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("시청자(2)", "시청자"),
        ("시청자(12)", "시청자"),
        ("시청자", "시청자"),
        ("시청자(2)중간", "시청자(2)중간"),  # 끝에 붙은 것만 뗀다
        ("시청자(둘)", "시청자(둘)"),  # 괄호 안이 숫자일 때만
        (None, None),
    ],
)
def test_uid(value, expected):
    assert uid(value) == expected


# ---------------------------------------------------------------- num


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("12", 12),
        ("-3", -3),
        ("0", 0),
        ("1.5", None),
        ("열둘", None),
        ("", None),
        (" 12", None),
        (None, None),
    ],
)
def test_num(value, expected):
    assert num(value) == expected


# ---------------------------------------------------------------- badge


@pytest.mark.parametrize(
    ("p", "expected"),
    [
        (P_STREAMER, "streamer"),
        (P_MANAGER_A, "manager"),
        (P_MANAGER_B, "manager"),
        (P_VIP, "vip"),
        (P_FAN, "fan"),
        (P_SUPPORTER, "supporter"),
        (0, "none"),
        (P_OTHER, "none"),
        (None, "none"),
    ],
)
def test_badge_single_flag(p, expected):
    assert badge(p) == expected


@pytest.mark.parametrize(
    ("p", "expected"),
    [
        (P_STREAMER | P_MANAGER_A | P_VIP | P_FAN | P_SUPPORTER, "streamer"),
        (P_MANAGER_B | P_VIP | P_FAN | P_SUPPORTER, "manager"),
        (P_VIP | P_FAN | P_SUPPORTER, "vip"),
        (P_FAN | P_SUPPORTER, "fan"),
        (P_SUPPORTER | P_OTHER, "supporter"),
    ],
)
def test_badge_higher_flag_wins(p, expected):
    """비트가 여럿 켜져 있으면 위에 있는 배지가 이긴다."""
    assert badge(p) == expected


# ---------------------------------------------------------------- tier


@pytest.mark.parametrize(
    ("p2", "expected"),
    [
        (P2_PLUS, "plus"),
        (P2_BASIC, "basic"),
        (P2_PLUS | P2_BASIC, "plus"),  # 둘 다 켜져 있으면 plus
        (0, "none"),
        (None, "none"),
    ],
)
def test_tier(p2, expected):
    assert tier(p2) == expected


# ---------------------------------------------------------------- stier


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("1", "basic"),
        ("2", "plus"),
        ("3", "none"),
        ("", "none"),
        (None, "none"),
    ],
)
def test_stier(value, expected):
    assert stier(value) == expected


# ---------------------------------------------------------------- sub


@pytest.mark.parametrize(
    ("tier_name", "months", "expected"),
    [
        ("plus", 7, {"tier": "plus", "months": 7}),
        ("basic", 0, {"tier": "basic", "months": 0}),
        ("basic", -2, {"tier": "basic", "months": 0}),  # 음수는 0으로
        ("plus", None, {"tier": "plus", "months": 0}),  # 개월 수가 없으면 0
        ("none", 5, {"tier": "none", "months": 0}),  # 비구독이면 개월 수를 버린다
    ],
)
def test_sub(tier_name, months, expected):
    assert sub(tier_name, months) == expected


# ---------------------------------------------------------------- public


def test_public_removes_user_ids():
    data = {
        "user_id": "시청자",
        "from_user_id": "보낸사람",
        "to_user_id": "받는사람",
        "nick": "닉네임",
        "message": "안녕하세요",
    }

    assert public(data) == {"nick": "닉네임", "message": "안녕하세요"}


def test_public_keeps_input_unchanged():
    data = {"user_id": "시청자", "message": "안녕하세요"}

    public(data)

    assert data == {"user_id": "시청자", "message": "안녕하세요"}
