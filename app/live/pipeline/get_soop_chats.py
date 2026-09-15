"""SOOP 라이브 채팅 웹소켓 수집 모듈.

방송 정보를 조회하고, 해당 정보를 바탕으로 채팅 서버에 웹소켓으로 접속한다.
수신한 raw 채팅 패킷을 파싱해 async generator로 흘려보낸다.
파이프라인상의 진입점으로, producer가 이 모듈의 stream_soop_chats()를 소비한다.
"""

import asyncio
import uuid
from collections.abc import AsyncGenerator
from typing import Any

import httpx
import websockets
from websockets.asyncio.client import ClientConnection

URL = "https://live.sooplive.com/afreeca/player_live_api.php"


async def get_soop_broad_info(bid: str) -> dict[str, Any]:
    """
    스트리머 id, 채팅 도메인 등이 들어있는 soop 방송 정보 가져오기.
    채팅을 수신하는 데에는 헤더, 쿠키, 파라미터가 필요하지 않고 스트리머의 id만 있으면 된다.
    """
    data = {
        "bid": bid,
    }

    async with httpx.AsyncClient() as client:
        # soop이 form 형식으로 받아서 "json=" 대신 "data="으로 넘겨줌
        resp = await client.post(URL, data=data)
    resp.raise_for_status()
    info: dict[str, Any] = resp.json().get("CHANNEL", {})
    return info


def make_soop_chat_ws_packet(info: dict[str, Any]) -> tuple[bytes, bytes, bytes]:
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


async def ping_loop(ws: ClientConnection, ping_packet: bytes) -> None:
    """soop 채팅 소켓에서 1분마다 보내는 연결 유지 신호를 모방함."""
    while True:
        await asyncio.sleep(60)
        await ws.send(ping_packet)


async def stream_soop_chats(bid: str) -> AsyncGenerator[list[bytes]]:
    info = await get_soop_broad_info(bid)
    if info.get("RESULT") != 1:
        print("방송 중이 아닙니다.")
        return

    strmr_nick = info["BJNICK"]
    chdomain = info["CHDOMAIN"]
    chpt = info["CHPT"]
    socket_url = f"wss://{chdomain}:{int(chpt) + 1}/Websocket/{bid}"

    connect_packet, join_packet, ping_packet = make_soop_chat_ws_packet(info)

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

        print(f"{strmr_nick}님의 채팅을 수신합니다.")

        # 변수에 담지 않으면 약한 참조 -> 가비지 컬렉터에 의해 작업이 종료될 가능성 있음.
        ping_task = asyncio.create_task(ping_loop(ws, ping_packet))

        try:
            while True:
                raw = await ws.recv()  # receive
                if not isinstance(raw, bytes):
                    continue
                parts = raw.split(b"\x0c")

                svc = raw[2:6]  # ESC, TAB 바로 뒤에 오는 서비스 코드 추출

                if svc == b"0005":
                    yield parts

                elif svc == b"0007":
                    print(f"{strmr_nick}님의 방송이 종료되었습니다.")
                    break

        finally:
            ping_task.cancel()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()

    async def _test() -> None:
        parser.add_argument("--raw", action="store_true")
        parser.add_argument("bid")
        arg = parser.parse_args()
        if not arg.raw:
            async for parts in stream_soop_chats(arg.bid):
                chat, nick = parts[1].decode(), parts[6].decode()
                print(f"{nick}: {chat}")
        else:
            async for parts in stream_soop_chats(arg.bid):
                print(parts)

    try:
        asyncio.run(_test())
    except KeyboardInterrupt:
        print("채팅 수신 종료")
