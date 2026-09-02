"""방송 플랫폼 SOOP에서 채팅을 받아오는 모듈"""

import asyncio
import ssl
import uuid
from datetime import datetime

import httpx
import websockets

CLIENT_UUID = uuid.uuid4().hex
INFO_URL = "https://live.sooplive.co.kr/afreeca/player_live_api.php"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Origin": "https://play.sooplive.co.kr",
    "Referer": "https://play.sooplive.co.kr/",
}

# SOOP 채팅 패킷 규칙
F = "\x0c"  # 필드 구분자 (0x0C)
ESC = "\x1b\t"  # 패킷 시작 신호 (0x1B 0x09)

DEBUG = False  # True 로 켜면 서버 원본 패킷을 그대로 볼 수 있음

# CONNECT(0001) 패킷에 들어가는 접속 토큰.
# 이 기본값("16")으로 채팅이 안 오면, 이 토큰을 실제 값으로 교체해야 함.
CONNECT_TOKEN = "16"


async def get_channel_info(bid: str) -> dict:
    async with httpx.AsyncClient() as client:
        resp = await client.post(INFO_URL, headers=HEADERS, data={"bid": bid})
        resp.raise_for_status()
        data = resp.json()
    return data["CHANNEL"]


# =========================================================
# 패킷 만들기 / 파싱
# =========================================================
def make_packet(svc_code: str, body: str) -> str:
    """ESC + 서비스코드(4) + 길이(6) + '00' + 내용"""
    return f"{ESC}{svc_code}{len(body):06d}00{body}"


def parse_chat(message: str, svc_code: str) -> tuple[str, str, str, str] | None:
    """0005(채팅)만 골라 (시간, 아이디, 닉네임, 메시지)로 뽑는다."""
    if svc_code != "0005":  # 서비스코드가 채팅이 아니면 무시
        return None

    parts = message.split(F)  # 구분자 F로 필드 쪼개기
    if len(parts) < 7:  # parts[6]까지 없으면 정상 채팅 아님 → 버림
        return None

    msg = parts[1]  # 메시지
    uid = parts[2]  # 아이디
    nick = parts[6]  # 닉네임

    if not msg.strip():  # 빈 메시지(시스템 신호)는 버림
        return None

    now = datetime.now()  # 받은 시각을 직접 찍음
    return svc_code, now, uid, nick, msg


async def ping_loop(ws) -> None:
    ping_packet = f"{ESC}000000000100{F}"
    while True:
        await asyncio.sleep(60)
        await ws.send(ping_packet)


async def get_chat(bid: str):
    # --- 1단계: 정보 받기 ---
    info = await get_channel_info(bid)
    if info.get("RESULT") != 1:
        print(f"[!] '{bid}' 는 지금 방송 중이 아니에요. RESULT =", info.get("RESULT"))
        return

    chdomain = info["CHDOMAIN"]
    ws_port = int(info["CHPT"]) + 1  # 웹소켓은 +1 포트
    chatno = info["CHATNO"]

    ftk = info.get("FTK", "")  # ★ JOIN 에 넣을 인증 토큰 (API 응답에 이미 있음)

    ws_url = f"wss://{chdomain}:{ws_port}/Websocket/{bid}"

    # 내부 도메인 인증서 문제로 막히지 않게 검증 완화
    ssl_ctx = ssl.create_default_context()
    ssl_ctx.check_hostname = False
    ssl_ctx.verify_mode = ssl.CERT_NONE

    # SOOP 내부 구분자들 (JOIN 메타데이터에 사용)
    DC1, ACK, DC2 = "\x11", "\x06", "\x12"

    # CONNECT(0001): 브라우저 필드 구조를 맞춤 = |토큰||524304|
    #   서버가 뒤쪽 필드까지 읽어서, 필드가 모자라면 "규격 위반"으로 튕김.
    connect_packet = make_packet("0001", f"{F}{CONNECT_TOKEN}{F}{F}524304{F}")

    # JOIN(0002): 브라우저가 보낸 구조를 그대로 재현
    #   = CHATNO + FTK토큰 + "0" + (빈칸) + log메타데이터
    log_blob = (
        f"log{DC1}{ACK}&{ACK}set_bps{ACK}={ACK}8000"
        f"{ACK}&{ACK}view_bps{ACK}={ACK}8000"
        f"{ACK}&{ACK}quality{ACK}={ACK}ori"
        f"{ACK}&{ACK}uuid{ACK}={ACK}{CLIENT_UUID}"
        f"{ACK}&{ACK}geo_cc{ACK}={ACK}KR{ACK}&{ACK}geo_rc{ACK}={ACK}41"
        f"{ACK}&{ACK}acpt_lang{ACK}={ACK}ko_KR{ACK}&{ACK}svc_lang{ACK}={ACK}ko_KR"
        f"{ACK}&{ACK}subscribe{ACK}={ACK}0{ACK}&{ACK}lowlatency{ACK}={ACK}1"
        f"{ACK}&{ACK}mode{ACK}={ACK}landing"
        f"{DC2}pwd{DC1}{DC2}auth_info{DC1}NULL{DC2}pver{DC1}2"
        f"{DC2}access_system{DC1}html5{DC2}nation_lang{DC1}ko_KR{DC2}"
    )
    join_body = f"{F}{chatno}{F}{ftk}{F}0{F}{F}{log_blob}{F}"
    join_packet = make_packet("0002", join_body)

    # --- 2단계: 웹소켓 접속 ---
    async with websockets.connect(
        ws_url,
        subprotocols=["chat"],
        ssl=ssl_ctx,
        ping_interval=None,  # ★ 핵심: websockets 자체 핑 끄기.
        #                       SOOP는 표준 핑에 응답 안 해서, 안 끄면 20초 후 끊김.
    ) as ws:
        await ws.send(connect_packet)
        await asyncio.sleep(0.5)
        await ws.send(join_packet)

        # 핑 백그라운드 시작
        ping_task = asyncio.create_task(ping_loop(ws))

        # ── 채팅만 골라서 깔끔하게 출력 ──
        try:
            async for raw in ws:
                if isinstance(raw, bytes):
                    raw = raw.decode("utf-8", errors="ignore")

                svc_code = raw[2:6]

                if svc_code == "0007":
                    print("[*] 방송 종료 신호(0007) 수신 → 연결을 정리합니다.")
                    break

                result = parse_chat(
                    raw, svc_code
                )  # 채팅이면 (시간,아이디,닉,메시지), 아니면 None
                if result:
                    yield result
        finally:
            ping_task.cancel()
