"""SOOP 채팅 XML → ChatRow 변환.

db나 http를 건드리지 않는 순수 함수 모듈.


XML 구조
---------------
    <root>
        <version>1.0.0</version>
        <chat><m>안녕</m><u>userid</u>...<t>12.3</t></chat>
        <ogq><ch>9548</ch><m/><gid>...</gid>...<t>7.3</t></ogq>
    </root>

- 루트의 직계 자식 하나가 채팅 1건이고, 태그명이 chats 테이블의 type 컬럼이다.
- 자식 요소를 {태그: 값} 딕셔너리로 읽고, 타입별 규칙으로 data를 만든다.
    t 태그는 time_offset 컬럼으로 분리한다.
    아는 타입은 읽기 쉬운 키로 필요한 값만 담는다. 키 정의는 docs/chat_types.md 참고.
    모르는 타입(세부 타입 포함)은 원본 태그 이름을 그대로 키로 쓰고, t까지 전부 담는다.
- 변환 규칙은 기존 데이터를 옮긴 마이그레이션(alembic 261012_0030)과 같다.
    한쪽을 바꾸면 다른 쪽 결과와 달라지니 같이 본다.
"""

from collections.abc import Callable
import logging
import re
from typing import Any
from xml.etree import ElementTree as ET

from app.review.shared.schemas import ChatRow

logger = logging.getLogger(__name__)

# 시간 필드로 쓰는 태그 이름
TIME_TAG = "t"
# 세부 타입이 들어 있는 태그 이름 (challenge_mission, battle_mission)
KIND_TAG = "type"

# 스킵할 태그 목록
# version: 위치는 <root>의 내부. 영상 시작 시 딱 한번 등장.
SKIP_TAGS = frozenset({"version"})

# 원본 xml을 {태그: 값} 형식으로 파싱. 
# 태그가 없으면 get이 None, 태그는 있는데 값이 비면 ""
Fields = dict[str, str]
# db에 저장될 모양
Data = dict[str, Any]


# -------------------------------------------------------- 값 변환

# 접미사 제거에 사용. "viewer01(2)" -> "viewer01"
_SESSION_SUFFIX = re.compile(r"\(\d+\)$")
_INT = re.compile(r"^-?\d+$")

# 플래그1(p) 비트. 근거는 docs/live_chat_packets.md 1절
P_STREAMER = 1 << 2               # 방송 주인
P_MANAGER = (1 << 6) | (1 << 8)   # 매니저 (둘 중 어느 것인지 모름)
P_VIP = 1 << 15                   # 열혈
P_FAN = 1 << 5                    # 팬
P_SUPPORTER = 1 << 20             # 서포터
# 플래그2(p2) 비트
P2_PLUS = 1 << 19
P2_BASIC = 1 << 18


def _uid(v: str | None) -> str | None:
    """아이디 뒤의 (2) 같은 중복 접속 접미사를 뗀다."""
    return None if v is None else _SESSION_SUFFIX.sub("", v)


def _num(v: str | None) -> int | None:
    """숫자가 아니면 None."""
    return int(v) if v is not None and _INT.match(v) else None


def _badge(p: int | None) -> str:
    """배지 하나. 위에 있는 것이 이긴다."""
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


def _tier(p2: int | None) -> str:
    if p2 is None:
        return "none"
    if p2 & P2_PLUS:
        return "plus"
    if p2 & P2_BASIC:
        return "basic"
    return "none"


def _stier(v: str | None) -> str:
    """follow의 stier(1 베이직, 2 플러스)."""
    return {"1": "basic", "2": "plus"}.get(v or "", "none")


def _sub(tier: str, months: int | None) -> Data:
    """{tier, months}. 비구독이거나 개월 수가 없으면 months 0."""
    if tier == "none" or months is None:
        return {"tier": tier, "months": 0}
    return {"tier": tier, "months": max(months, 0)}


def _user(f: Fields, uid_tag: str = "u", nick_tag: str = "n", prefix: str = "") -> Data:
    return {f"{prefix}user_id": _uid(f.get(uid_tag)), f"{prefix}user_nick": f.get(nick_tag)}


# ---------------------------------------------------- 타입별 변환
# 주석의 태그 목록은 원본 XML의 자식 순서(t 제외).

def _adinbroad(f: Fields) -> Data:
    # status chno [광고 재생 횟수]  쉬는시간(중간광고). 1 시작, 0 종료
    return {"active": f.get("status") == "1"}


def _adballoon(f: Fields) -> Data:
    # bj u n c m em title img_url def_url fs tfn lng mof
    return {"bid": f.get("bj"), **_user(f), "count": _num(f.get("c")),
            "message1": f.get("m"), "message2": f.get("em"),
            "img_url": f.get("img_url"), "def_url": f.get("def_url")}


def _adm_notice(f: Fields) -> Data:
    # type m  (별별랭킹 안내)
    return {"message": f.get("m")}


def _balloon(f: Fields) -> Data:
    # balloon:     bj u n c fs tfn fc fn def lng mof
    # vballoon:    bj u n c tn st d sub fs tfn
    # vod_balloon: bj u n c fn def ch lng mof
    return {"bid": f.get("bj"), **_user(f), "count": _num(f.get("c"))}


def _battle_gift(f: Fields) -> Data:
    # type ch bj bj_nick u n c subch fn key title top_fan fan_order
    return {"kind": f.get("type"), "bid": f.get("bj"), "bj_nick": f.get("bj_nick"),
            **_user(f), "count": _num(f.get("c")),
            "mission_key": _num(f.get("key")), "title": f.get("title")}


def _battle_notice(f: Fields) -> Data:
    # type draw winner loser fn key title
    return {"kind": f.get("type"), "winner": f.get("winner"), "loser": f.get("loser"),
            "draw": f.get("draw") == "1",
            "mission_key": _num(f.get("key")), "title": f.get("title")}


def _mission_settle(f: Fields) -> Data:
    # battle_mission SETTLE:              type bj bj_nick c fn key title
    # challenge_mission CHALLENGE_SETTLE: type subch key title fn c bj bj_nick
    return {"kind": f.get("type"), "bid": f.get("bj"), "bj_nick": f.get("bj_nick"),
            "count": _num(f.get("c")),
            "mission_key": _num(f.get("key")), "title": f.get("title")}


def _challenge_gift(f: Fields) -> Data:
    # type ch subch key title fn c u n bj bj_nick
    return _battle_gift(f)  # 쓰는 태그 이름이 같다


def _challenge_notice(f: Fields) -> Data:
    # type title ms key(늘 0)
    return {"kind": f.get("type"), "title": f.get("title"), "result": f.get("ms")}


def _message_only(f: Fields) -> Data:
    # bj_notice: ch state type m  (채팅 규칙 공지)
    return {"message": f.get("m")}


def _challenge_mission_settle(f: Fields) -> Data:
    # ch fno u n c nfn  (도전미션 정산 명세. 한 사람씩)
    return {**_user(f), "count": _num(f.get("c"))}


def _chat(f: Fields) -> Data:
    # m u c mt l n p p2 fw nf nd acfw pn tlk
    return {"message": f.get("m"), **_user(f),
            "badge": _badge(_num(f.get("p"))),
            "subscriber": _sub(_tier(_num(f.get("p2"))), _num(f.get("fw")))}


def _user_only(f: Fields) -> Data:
    # fanclub, supporter, topfan: u n
    return _user(f)


def _fanletter(f: Fields) -> Data:
    # bj bjnick u n c image lettertype lettermessage lng  (스티커)
    return {"bid": f.get("bj"), **_user(f), "count": _num(f.get("c")),
            "message": f.get("lettermessage")}


def _follow(f: Fields) -> Data:
    # bj u n type period acperiod stier stype lng mof
    # 선물 받은 구독(type 111, 211)은 stype이 없다
    return {"bid": f.get("bj"), **_user(f),
            "subscriber": _sub(_stier(f.get("stier")), _num(f.get("period")))}


def _ice(f: Fields) -> Data:
    # value  (1 얼림, 0 해제)
    return {"frozen": f.get("value") == "1"}


def _ogq(f: Fields) -> Data:
    # ch m gid sid v s sn sf c l mt e fw nf nd acfw pn anm tlk.  sf = "p|p2"
    p, _, p2 = (f.get("sf") or "").partition("|")
    return {"message": f.get("m"), **_user(f, "s", "sn"),
            "badge": _badge(_num(p)),
            "subscriber": _sub(_tier(_num(p2)), _num(f.get("fw"))),
            "group_id": f.get("gid"), "sticker_id": _num(f.get("sid"))}


def _quick_random(f: Fields) -> Data:
    # sender sendernick chno cnt itemcode
    # sub_random도 같은 모양: sender sendernick chno cnt itemcode rank
    return {**_user(f, "sender", "sendernick", "from_"),
            "count": _num(f.get("cnt")), "item_code": _num(f.get("itemcode"))}


def _quickview(f: Fields) -> Data:
    # sender sendernick u n quickviewtype
    return {**_user(f, "sender", "sendernick", "from_"), **_user(f, prefix="to_"),
            "item_code": _num(f.get("quickviewtype"))}


def _sendogq(f: Fields) -> Data:
    # sendid sendnick recvid recvnick title url ch
    return {**_user(f, "sendid", "sendnick", "from_"),
            **_user(f, "recvid", "recvnick", "to_"),
            "item_name": f.get("title"), "img_url": f.get("url")}


def _adcon(f: Fields) -> Data:
    # stationadcon, vodadcon: bj v vn cnt default msg chno lng mof
    # vodadcon은 v, vn이 빈 중복 알림이 오기도 한다. 빈 문자열 그대로 둔다
    return {"bid": f.get("bj"), **_user(f, "v", "vn"), "count": _num(f.get("cnt")),
            "message": f.get("msg"), "def_url": f.get("default")}


def _subscription(f: Fields) -> Data:
    # ch s sn r rn b bn it ic is st sp sr  (지정 구독권 선물)
    return {**_user(f, "s", "sn", "from_"), **_user(f, "r", "rn", "to_"),
            "item_code": _num(f.get("it"))}


Converter = Callable[[Fields], Data]

# 세부 타입이 없는 타입
CONVERTERS: dict[str, Converter] = {
    "adinbroad": _adinbroad,
    "adballoon": _adballoon,
    "adm_notice": _adm_notice,
    "balloon": _balloon,
    "bj_notice": _message_only,
    "challenge_mission_settle": _challenge_mission_settle,
    "chat": _chat,
    "fanclub": _user_only,
    "fanletter": _fanletter,
    "follow": _follow,
    "ice": _ice,
    "ogq": _ogq,
    "quick_random": _quick_random,
    "quickview": _quickview,
    "sendogq": _sendogq,
    "stationadcon": _adcon,
    "sub_random": _quick_random,
    "subscription": _subscription,
    "supporter": _user_only,
    "topfan": _user_only,
    "vballoon": _balloon,
    "vod_balloon": _balloon,
    "vodadcon": _adcon,
}

# 세부 타입(<type>의 값)마다 변환이 다른 타입
KIND_CONVERTERS: dict[str, dict[str, Converter]] = {
    "battle_mission": {
        "GIFT": _battle_gift,
        "NOTICE": _battle_notice,
        "SETTLE": _mission_settle,
    },
    "challenge_mission": {
        "CHALLENGE_GIFT": _challenge_gift,
        "CHALLENGE_NOTICE": _challenge_notice,
        "CHALLENGE_SETTLE": _mission_settle,
    },
}

# 알고 있는 타입. 여기 없는 태그가 나오면 경고만 남기고 원본 태그 그대로 저장한다.
KNOWN_TYPES = frozenset(CONVERTERS) | frozenset(KIND_CONVERTERS)


def _convert(tag: str, fields: Fields) -> Data | None:
    """아는 타입이면 data, 모르는 타입(세부 타입 포함)이면 None."""
    if tag in KIND_CONVERTERS:
        conv = KIND_CONVERTERS[tag].get(fields.get(KIND_TAG) or "")
    else:
        conv = CONVERTERS.get(tag)
    return conv(fields) if conv else None


# ------------------------------------------------------ 파싱

def parse_chat_xml(
    xml_text: str,
    *,
    vod_id: int,
    file_order: int,
    offset_base: float,
) -> list[ChatRow]:
    """채팅 XML 한 덩어리(chat_duration 초 분량)를 ChatRow 리스트로 변환한다.

    Args:
        offset_base: 이 파트의 VOD 전체 기준 시작 초.
            (file_start - vods.broad_start).

    Returns:
        ChatRow 리스트. 파싱 불가한 XML이면 빈 리스트.
    """
    if not xml_text or not xml_text.strip():
        return []

    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        logger.warning(
            "[PARSE] vod_id=%d file_order=%d XML 파싱 실패: %s", vod_id, file_order, e
        )
        return []

    rows: list[ChatRow] = []
    unknown: set[str] = set()  # 알지 못하는 태그가 왔을 때, 그걸 담을 그릇
    for el in root:
        tag = el.tag
        if tag in SKIP_TAGS:
            if tag == "version" and el.text and el.text.strip() != "1.0.0":
                logger.info("[%d] 채팅 XML 버전 변경: %s", vod_id, el.text.strip())
            continue

        # 자식이 t뿐이거나 없으면 채팅이 아니다
        if not any(child.tag != TIME_TAG for child in el):
            continue

        t = _extract_time(el)
        if t is None:
            logger.debug("[SKIP] vod_id=%d %s t 태그 없음", vod_id, tag)
            continue

        fields = {child.tag: (child.text or "") for child in el if child.tag != TIME_TAG}
        data = _convert(tag, fields)
        if data is None:
            # 모르는 타입: 원본 태그 그대로, t까지 전부
            data = {child.tag: (child.text or "") for child in el}
            kind = fields.get(KIND_TAG)
            unknown.add(f"{tag}/{kind}" if tag in KIND_CONVERTERS else tag)

        rows.append(
            ChatRow(
                vod_id=vod_id,
                file_order=file_order,
                type=tag,
                time_offset=offset_base + t,
                data=data,
            )
        )

    if unknown:
        logger.info(
            "[%d] 미등록 채팅 타입 발견: %s", vod_id, sorted(unknown)
        )

    return rows


def _extract_time(el: ET.Element) -> float | None:
    """요소에서 파트 내 시간(초)을 꺼낸다."""
    node = el.find(TIME_TAG)
    if node is None or not node.text:
        return None
    try:
        return float(node.text)
    except ValueError:
        return None