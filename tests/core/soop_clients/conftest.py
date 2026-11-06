from collections.abc import AsyncGenerator

import pytest_asyncio

from app.core.soop_clients import review_client
from app.core.soop_clients.live_clients import shared


@pytest_asyncio.fixture(autouse=True)
async def fresh_clients() -> AsyncGenerator[None]:
    """테스트마다 공유 클라이언트를 닫고 비운 상태에서 시작하고, 끝나면 다시 닫는다.

    _client와 request_counts는 모듈 전역이라,
    비우지 않으면 앞 테스트의 것이 이어서 쓰인다.
    """
    await review_client.aclose()
    await shared.aclose()
    shared.request_counts.clear()
    yield
    await review_client.aclose()
    await shared.aclose()
    shared.request_counts.clear()
