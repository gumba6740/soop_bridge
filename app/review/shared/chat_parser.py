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
- 자식 요소의 텍스트를 문서 순서대로 parts: list[]에 담는다.
    t 태그는 time_offset 컬럼으로 분리하고, 나머지를 parts 필드에 넣음.
    type별로 구조가 다름. 인덱스 매핑은 docs/chat_types.md 참고.
"""

import logging
from xml.etree import ElementTree as ET

from app.review.shared.schemas import ChatRow

logger = logging.getLogger(__name__)

# 시간 필드로 쓰는 태그 이름
TIME_TAG = "t"

# TODO: challenge_mission은 세부 타입에 따라 구조가 바뀌므로 데이터 확인
# 알고 있는 타입. 여기 없는 태그가 나오면 경고만 남기고 그대로 저장한다.
KNOWN_TYPES = frozenset({
    "chat", "ogq", "balloon", "challenge_mission",
    "adballoon", "vballoon", "follow", "fanclub", "ice",
})
# 스킵할 태그 목록
# version: 위치는 <root>의 내부. 영상 시작 시 딱 한번 등장.
SKIP_TAGS = frozenset({"version"})


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
        if tag not in KNOWN_TYPES:
            unknown.add(tag)

        parts = [(child.text or "") for child in el if child.tag != TIME_TAG]
        if not parts:
            continue

        t = _extract_time(el)
        if t is None:
            logger.debug("[SKIP] vod_id=%d %s t 태그 없음", vod_id, tag)
            continue

        rows.append(
            ChatRow(
                vod_id=vod_id,
                file_order=file_order,
                type=tag,
                time_offset=offset_base + t,
                parts=parts,
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