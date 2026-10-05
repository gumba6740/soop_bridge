"""SOOP 라이브 채팅 클라이언트.

호출부가 넘겨준 접속 정보(shared.get_live_info의 결과)로 채팅 서버에 웹소켓으로 접속하고,
채팅 패킷을 받아 async generator로 흘려보낸다.

종료 계약
---------
- 방송이 끝나면(0007) -> 그냥 끝난다(return).
- 연결이 끊기거나 RECV_TIMEOUT 동안 아무것도 안 오면 -> 예외 전파.
    재접속할지는 호출부(collector)가 정한다.
"""

import asyncio
import logging
import uuid
from collections.abc import AsyncGenerator
from typing import Any

import websockets
from websockets.asyncio.client import ClientConnection


logger = logging.getLogger(__name__)

SVC_CHAT = b"0005"
SVC_BROAD_END = b"0007"

PING_INTERVAL = 60  # 초
# 이 시간 동안 패킷이 하나도 안 오면 연결이 죽은 것으로 본다.
RECV_TIMEOUT = 300  # 초


def _make_packets(info: dict[str, Any]) -> tuple[bytes, bytes, bytes]:
    """soop 채팅 소켓 연결에 필요한 패킷 생성

    ws 연결에 필요한 요청은 총 세 가지

    1. connect: 채팅 서버 연결
    2. join: 채팅방 입장
    3. ping: 채팅 서버와 연결 유지

    패킷의 구분자는 총 여섯 가지

    1. ESC, TAB: 패킷의 시작부.
    2. SEP: 1차 구분자. 전체 패킷을 큰 덩어리로 쪼갬.
    3. X11: 키, 값 구분자. 키=값을 키{X11}값으로 표현.
    4. X12: 키와 값 묶음들을 서로 구분. ex) 키=값{x12}키=값.
    5. X6: 옵션(화질, 지역 등) 내부 구분자.
    """
    ESC, TAB, SEP, X6, X11, X12 = "\x1b", "\x09", "\x0c", "\x06", "\x11", "\x12"

    def make_packet(svc_code: str, body: str) -> bytes:
        body_bytes = body.encode("utf-8")
        return f"{ESC}{TAB}{svc_code}{len(body_bytes):06d}00".encode() + body_bytes

    connect_packet = make_packet("0001", f"{SEP}{SEP}{SEP}16{SEP}")

    chatno, ftk = info["CHATNO"], info["FTK"]
    opts = {
        "set_bps": "8000",
        "view_bps": "1000",
        "quality": "normal",
        "uuid": uuid.uuid4().hex,
        "geo_cc": info.get("geo_cc", "KR"),
        "geo_rc": info.get("geo_rc", "41"),
        "acpt_lang": info.get("acpt_lang") or "ko_KR",
        "svc_lang": info.get("svc_lang", "ko_KR"),
        "is_iframeapi": "false",
        "subscribe": "0",
        "lowlatency": "0",
        "mode": "landing",
    }
    options = "".join(f"{X6}&{X6}{k}{X6}={X6}{v}" for k, v in opts.items())

    join_body = (
        f"{SEP}{chatno}{SEP}{ftk}{SEP}0{SEP}{SEP}log{X11}{options}"
        f"{X12}pwd{X11}{X12}auth_info{X11}NULL{X12}pver{X11}2"
        f"{X12}access_system{X11}html5{X12}nation_lang{X11}ko_KR{X12}{SEP}"
    )
    join_packet = make_packet("0002", join_body)

    ping_packet = make_packet("0000", SEP)

    return connect_packet, join_packet, ping_packet


async def _ping_loop(ws: ClientConnection, ping_packet: bytes) -> None:
    """soop 채팅 소켓에서 1분마다 보내는 연결 유지 신호를 모방함."""
    while True:
        await asyncio.sleep(PING_INTERVAL)
        await ws.send(ping_packet)


async def stream_live_chats(bid: str, info: dict[str, Any]) -> AsyncGenerator[list[str]]:
    """채팅 한 건마다 parts(문자열 리스트)를 내보낸다.

    패킷 맨 앞의 헤더(ESC, TAB, 서비스 코드 등)는 빼고 내보낸다.
    그래서 parts[0]이 메시지, parts[1]이 유저 id, parts[5]가 닉네임이다.
    """

    strmr_nick = info["BJNICK"]
    socket_url = f"wss://{info['CHDOMAIN']}:{int(info['CHPT']) + 1}/Websocket/{bid}"
    connect_packet, join_packet, ping_packet = _make_packets(info)

    async with websockets.connect(
        socket_url,
        subprotocols=[websockets.Subprotocol("chat")],
        # websockets가 자체적으로 보내는 핑을 차단. 허용하면 응답이 없어서 터짐.
        ping_interval=None,
    ) as ws:
        await ws.send(connect_packet)
        # connect 이후 지연 없이 너무 빠르게 join 신호를 보내면 작동하지 않음.
        # 핸드셰이크 순서가 꼬이는 것으로 추측.
        await asyncio.sleep(1)
        await ws.send(join_packet)

        logger.info("bid=%s %s님의 채팅 수신 시작", bid, strmr_nick)

        # 변수에 담지 않으면 약한 참조 -> 가비지 컬렉터에 의해 작업이 종료될 가능성 있음.
        ping_task = asyncio.create_task(_ping_loop(ws, ping_packet))

        try:
            while True:
                async with asyncio.timeout(RECV_TIMEOUT):
                    raw = await ws.recv()
                if not isinstance(raw, bytes):
                    continue

                svc = raw[2:6]  # ESC, TAB 바로 뒤에 오는 서비스 코드 추출

                if svc == SVC_CHAT:
                    parts = raw.split(b"\x0c")[1:]
                    yield [p.decode(errors="replace") for p in parts]

                elif svc == SVC_BROAD_END:
                    logger.info("bid=%s %s님의 방송 종료", bid, strmr_nick)
                    return

        finally:
            ping_task.cancel()
            # cancel만 하고 끝내면 핑 태스크가 정리되기 전에 함수가 끝난다.
            # gather로 정리가 끝날 때까지 기다림.
            await asyncio.gather(ping_task, return_exceptions=True)


if __name__ == "__main__":
    import argparse

    from app.core.log import setup_logging
    from app.core.soop_clients.live_clients.shared import aclose, get_live_info

    async def _main(bid: str, raw: bool) -> None:
        try:
            status, info = await get_live_info(bid)
            if info is None:
                print(f"볼 수 없는 방송입니다. ({status})")
                return

            async for parts in stream_live_chats(bid, info):
                print(parts if raw else f"{parts[5]}: {parts[0]}")
        finally:
            await aclose()

    setup_logging()
    parser = argparse.ArgumentParser()
    parser.add_argument("--raw", action="store_true")
    parser.add_argument("bid")
    args = parser.parse_args()

    try:
        asyncio.run(_main(args.bid, args.raw))
    except KeyboardInterrupt:
        print("채팅 수신 종료")