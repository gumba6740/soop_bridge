from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio

from app.broadcast.review.collector import __main__ as collector_main
from app.broadcast.review.collector import chats, vods, worker
from app.core.soop_clients import review_client
from tests.conftest import test_settings


@pytest.fixture(autouse=True)
def use_test_settings(monkeypatch) -> None:
    """main 과 워커가 테스트 DB, 테스트 redis DB 에 붙게 한다.

    이 모듈들이 쓰는 settings 만 바꾼다.
    """
    monkeypatch.setattr(collector_main, "settings", test_settings)
    monkeypatch.setattr(worker, "settings", test_settings)


@pytest_asyncio.fixture(autouse=True)
async def fast(monkeypatch) -> AsyncGenerator[None]:
    """요청 사이에 쉬지 않게 하고, SOOP 클라이언트를 테스트마다 새로 쓴다."""

    async def no_sleep(*args, **kwargs) -> None:
        pass

    monkeypatch.setattr(vods, "polite_sleep", no_sleep)
    monkeypatch.setattr(chats, "polite_sleep", no_sleep)
    await review_client.aclose()
    yield
    await review_client.aclose()
