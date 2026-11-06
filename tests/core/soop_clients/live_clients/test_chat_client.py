"""
pytest tests/core/soop_clients/live_clients/test_chat_client.py
"""

import asyncio
import contextlib
from collections.abc import AsyncGenerator
from typing import Any

import pytest
import websockets

from app.core.soop_clients.live_clients import chat_client, shared
from app.core.soop_clients.live_clients.chat_client import (
    _make_packets,
    _ping_loop,
    stream_live_chats,
)

INFO: dict[str, Any] = {
    "BJNICK": "닉네임",
    "CHDOMAIN": "chat.example.com",
    "CHPT": "8000",
    "CHATNO": "chat-1",
    "FTK": "ftk-토큰",
}


def _split(packet: bytes) -> tuple[bytes, int, bytes]:
    """패킷을 (서비스 코드, 헤더에 적힌 길이, 본문)으로 나눈다.

    모양: ESC TAB + 서비스 코드 4자리 + 본문 길이 6자리 + "00" + 본문
    """
    assert packet[:2] == b"\x1b\t"
    assert packet[12:14] == b"00"
    return packet[2:6], int(packet[6:12]), packet[14:]


def _packet(svc: bytes) -> bytes:
    """테스트용으로 받을 패킷. stream_live_chats는 서비스 코드만 본다."""
    return b"\x1b\t" + svc + b"00000000"


class FakeWebSocket:
    """받을 패킷을 순서대로 돌려주고, 보낸 패킷을 기록한다.

    다 돌려준 뒤에는 아무것도 오지 않는 상태로 기다린다(타임아웃을 낼 때 쓴다).
    목록에 숫자가 있으면 그만큼(초) 기다렸다가 다음 것을 준다.
    목록에 예외가 있으면 그 차례에 던진다(연결이 끊긴 경우).
    """

    def __init__(self, incoming: list[bytes | str | float | Exception]) -> None:
        self.incoming = list(incoming)  # 서버가 나에게 주는 것
        self.sent: list[bytes] = []  # 내가 서버에게 주는 것

    async def send(self, data: bytes) -> None:
        self.sent.append(data)

    async def recv(self) -> bytes | str:
        await asyncio.sleep(0)  # 다른 태스크(핑 루프 등)가 돌 기회를 준다
        while self.incoming and isinstance(self.incoming[0], float):
            await asyncio.sleep(self.incoming.pop(0))  # type: ignore[arg-type]
        if not self.incoming:
            await asyncio.Event().wait()  # 영원히 기다림
        item = self.incoming.pop(0)
        if isinstance(item, Exception):
            raise item
        assert not isinstance(item, float)
        return item


@pytest.fixture
def fast(monkeypatch):
    """기다리는 시간을 줄여서 테스트가 빨리 끝나게 한다."""
    monkeypatch.setattr(chat_client, "STATE_PAIR_WINDOW", 0.05)
    monkeypatch.setattr(chat_client, "RECV_TIMEOUT", 0.2)
    monkeypatch.setattr(chat_client, "JOIN_DELAY", 0)


def _use_fake_ws(monkeypatch, incoming) -> tuple[FakeWebSocket, list[Any]]:
    """websockets.connect가 FakeWebSocket을 주게 한다.

    (웹소켓, 접속 기록)을 돌려준다.
    """
    ws = FakeWebSocket(incoming)
    calls: list[Any] = []

    @contextlib.asynccontextmanager
    async def connect(url: str, **kwargs: Any) -> AsyncGenerator[FakeWebSocket]:
        calls.append((url, kwargs))
        yield ws

    monkeypatch.setattr(websockets, "connect", connect)
    return ws, calls


async def _collect(bid: str = "bid1") -> list[bytes]:
    """stream_live_chats가 끝날 때까지 받은 패킷을 모은다.

    코드가 잘못되면 끝나지 않을 수 있어서 2초 제한을 둔다.
    """
    async with asyncio.timeout(2):
        return [raw async for raw in stream_live_chats(bid, INFO)]


# ------------------------------------------------------ _make_packets


def test_make_packets_service_codes():
    connect, join, ping = _make_packets(INFO)

    assert _split(connect)[0] == b"0001"
    assert _split(join)[0] == b"0002"
    assert _split(ping)[0] == b"0000"


def test_make_packets_length_is_body_bytes():
    # 헤더의 길이는 글자 수가 아니라 바이트 수다. FTK에 한글이 있어서 둘이 다르다.
    for packet in _make_packets(INFO):
        _, length, body = _split(packet)
        assert length == len(body)


def test_make_packets_join_has_chatno_and_ftk():
    _, join, _ = _make_packets(INFO)
    body = _split(join)[2].decode()

    assert "\x0cchat-1\x0cftk-토큰\x0c" in body


def test_make_packets_option_defaults():
    _, join, _ = _make_packets(INFO)  # geo_cc 등이 없는 접속 정보
    body = _split(join)[2].decode()

    assert "geo_cc\x06=\x06KR" in body
    assert "acpt_lang\x06=\x06ko_KR" in body


def test_make_packets_uuid_differs_each_time():
    _, join1, _ = _make_packets(INFO)
    _, join2, _ = _make_packets(INFO)

    assert join1 != join2


# ------------------------------------------------------ _ping_loop


@pytest.mark.asyncio
async def test_ping_loop_sends_ping_repeatedly(monkeypatch):
    monkeypatch.setattr(chat_client, "PING_INTERVAL", 0.01)
    ws = FakeWebSocket([])

    task = asyncio.create_task(_ping_loop(ws, b"PING"))  # type: ignore[arg-type]
    await asyncio.sleep(0.05)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)

    assert len(ws.sent) >= 2
    assert set(ws.sent) == {b"PING"}


# ------------------------------------------------------ stream_live_chats


@pytest.mark.asyncio
async def test_stream_connects_and_yields_packets(monkeypatch, fast):
    chat1, chat2 = _packet(b"0005"), _packet(b"0018")
    state = _packet(b"0007")
    ws, calls = _use_fake_ws(monkeypatch, [chat1, chat2, state])

    # 0007 뒤에 짝이 오지 않아서 끝난다.
    assert await _collect() == [chat1, chat2]

    (url, kwargs) = calls[0]
    assert url == "wss://chat.example.com:8001/Websocket/bid1"  # 포트는 CHPT + 1
    assert kwargs["ping_interval"] is None
    assert kwargs["subprotocols"] == ["chat"]
    connect, join, _ = _make_packets(INFO)
    assert ws.sent[0] == connect
    assert _split(ws.sent[1])[0] == b"0002"  # join (uuid가 달라서 코드만 비교)
    assert shared.request_counts["chat_ws"] == 1


@pytest.mark.asyncio
async def test_stream_state_pair_keeps_connection(monkeypatch, fast):
    chat1, chat2 = _packet(b"0005"), _packet(b"0005")
    state = _packet(b"0007")
    # 0007 두 번(짝) → 연결 유지 → 짝 대기 시간(0.05초)보다 오래 조용함
    # → 채팅 → 0007 한 번(짝 없음) → 끝
    _use_fake_ws(monkeypatch, [chat1, state, state, 0.1, chat2, state])

    assert await _collect() == [chat1, chat2]


@pytest.mark.asyncio
async def test_stream_single_state_ends_connection(monkeypatch, fast):
    chat1, chat2, late = _packet(b"0005"), _packet(b"0005"), _packet(b"0005")
    state = _packet(b"0007")
    # 0007 한 번 → 짝 대기 시간보다 오래 조용함 → 그 뒤에 온 채팅은 받지 않고 끝
    _use_fake_ws(monkeypatch, [chat1, chat2, state, 0.1, late])

    assert await _collect() == [chat1, chat2]


@pytest.mark.asyncio
async def test_stream_skips_non_bytes(monkeypatch, fast):
    chat = _packet(b"0005")
    _use_fake_ws(monkeypatch, ["text", chat, _packet(b"0007")])

    assert await _collect() == [chat]


@pytest.mark.asyncio
async def test_stream_raises_when_nothing_comes(monkeypatch, fast):
    chat = _packet(b"0005")
    _use_fake_ws(monkeypatch, [chat])  # 그 뒤로 아무것도 안 옴
    received = []

    # 2초 제한을 pytest.raises 바깥에 둬야, 제한에 걸린 경우를 통과로 착각하지 않는다.
    async with asyncio.timeout(2):
        with pytest.raises(TimeoutError):
            async for raw in stream_live_chats("bid1", INFO):
                received.append(raw)

    assert received == [chat]


@pytest.mark.asyncio
async def test_stream_raises_when_disconnected_and_stops_ping(monkeypatch, fast):
    """연결이 끊기면 에러를 삼키지 않고 전파하는지, 이때 핑 태스크도 정리하는지."""
    # 핑 루프 대신, 취소되면 표시를 남기는 가짜를 넣는다.
    ping_cancelled = asyncio.Event()

    async def fake_ping_loop(ws, ping_packet):
        try:
            await asyncio.Event().wait()
        # return_exceptions=True 때문에 이 에러는 삼켜짐.
        except asyncio.CancelledError:
            ping_cancelled.set()
            raise

    monkeypatch.setattr(chat_client, "_ping_loop", fake_ping_loop)
    _use_fake_ws(monkeypatch, [ConnectionError("끊김")])

    with pytest.raises(ConnectionError):
        await _collect()

    assert ping_cancelled.is_set()  # finally에서 핑 태스크를 정리했다
