"""SOOP 라이브 채팅 패킷 → LivePacket 변환.

네트워크를 건드리지 않는 순수 함수 모듈.
chat_client(core)는 받은 패킷을 원본 bytes로 넘기기만 하고, 나누고 거르는 일은 여기서 한다.
웹소켓 접속과 0007 처리는 chat_client가 한다.


패킷 구조
---------
    ESC TAB {서비스 코드 4자리}{본문 길이 6자리}00 SEP 칸0 SEP 칸1 SEP ... SEP

- SEP(\\x0c)로 나누면 첫 덩어리가 헤더(ESC, TAB, 서비스 코드, 길이, 00)이고, 나머지가 본문의 칸이다.
- 서비스 코드는 svc로, 본문의 칸은 parts로 나눠 담는다. 헤더의 나머지(길이, 00)는 버린다.
- 코드별 칸의 뜻은 docs/live_chat_packets.md 참고. 마지막 칸은 보통 빈 문자열이다.
"""

from typing import NamedTuple

HEADER_START = b"\x1b\t"
SEP = b"\x0c"

SVC_CHAT = "0005"

# 채팅 스트림에 올리는 서비스 코드. 화면에 채팅 한 줄로 보이는 것들이다.
# 그 밖의 코드(입장/퇴장, 플래그 변경 등)는 버린다. 뜻은 docs/live_chat_packets.md
RELAY_SVCS = frozenset({
    SVC_CHAT,  # 채팅
    "0018",    # 별풍선
    "0087",    # 애드벌룬
    "0121",    # 도전미션 (등록, 결과, 정산)
    "0125",    # 도전미션 정산 명세
    "0091",    # 신규 구독
    "0093",    # N개월째 구독
    "0108",    # 구독권 선물
    "0109",    # OGQ 이모티콘(스티커)
    "0058",    # 별별랭킹
})


class LivePacket(NamedTuple):
    svc: str          # 서비스 코드 4자리. 예: "0005"
    parts: list[str]  # 헤더를 뺀 본문의 칸. 0005면 parts[0]이 메시지, parts[1]이 유저 id


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