import asyncio
from collections.abc import AsyncGenerator

import httpx
import pytest
import pytest_asyncio
import redis.asyncio as redis
from psycopg import AsyncConnection
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

import app.models  # noqa: F401  모델을 import 해야 Base.metadata 에 테이블이 올라간다
from app.core.config import settings
from app.core.db import Base
from app.core.hub import ChatHub

TEST_DB = "soop_test"
TEST_REDIS_DB = 15
# .env 의 접속 정보는 그대로 쓰고 DB 이름과 redis DB 번호만 바꾼다
test_settings = settings.model_copy(
    update={"postgres_db": TEST_DB, "redis_db": TEST_REDIS_DB}
)
# 테스트가 개발 데이터를 지우지 않게, 진짜 설정과 겹치면 아예 시작하지 않는다
assert settings.postgres_db != TEST_DB, ".env 의 DB가 테스트 DB와 같다"
assert settings.redis_db != TEST_REDIS_DB, ".env 의 redis DB가 테스트 redis DB와 같다"


# ---------------------------------------------------------------- Postgres


@pytest.fixture(scope="session")
def db_tables() -> None:
    """테스트 DB의 테이블을 models.py 대로 지우고 다시 만든다. pytest 실행마다 한 번.

    끝날 때는 지우지 않는다. 테스트가 실패하면 psql 로 남은 데이터를 볼 수 있게.
    한 번만 도는 준비라 동기 엔진으로 한다(비동기 엔진은 테스트마다 이벤트 루프가 달라 같이 못 씀).
    """
    engine = create_engine(test_settings.sqlalchemy_url, poolclass=NullPool)
    try:
        Base.metadata.drop_all(engine)
        Base.metadata.create_all(engine)
    finally:
        engine.dispose()


@pytest_asyncio.fixture
async def db(db_tables) -> AsyncGenerator[AsyncSession]:
    """테스트 DB 세션. 시작할 때 모든 테이블을 비운다."""
    engine = create_async_engine(test_settings.sqlalchemy_url, poolclass=NullPool)
    tables = ", ".join(t.name for t in Base.metadata.sorted_tables)
    async with AsyncSession(engine, expire_on_commit=False) as db:
        await db.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
        await db.commit()
        yield db
    await engine.dispose()


@pytest_asyncio.fixture
async def conn(db) -> AsyncGenerator[AsyncConnection]:
    """수집기처럼 psycopg 로 테스트 DB 에 붙는다. 테이블 비우기는 db fixture 가 한다."""
    async with await AsyncConnection.connect(
        test_settings.postgres_url, autocommit=True
    ) as c:
        yield c


@pytest_asyncio.fixture
async def other(db) -> AsyncGenerator[AsyncConnection]:
    """다른 수집기 프로세스 역할을 하는 두 번째 연결. 닫히면 잡은 락도 풀린다."""
    async with await AsyncConnection.connect(
        test_settings.postgres_url, autocommit=True
    ) as c:
        yield c


# ---------------------------------------------------------------- redis


@pytest_asyncio.fixture
async def r() -> AsyncGenerator[redis.Redis]:
    """테스트 redis DB 에 붙은 클라이언트. 앞뒤로 그 DB 를 통째로 비운다.

    옵션은 app/core/r.py 와 같게 둔다(응답 모양이 같아야 해서).
    """
    client = redis.from_url(
        test_settings.redis_url, decode_responses=True, legacy_responses=True
    )
    # 실수로 다른 DB에 붙었을 때 그 DB를 비우지 않게 먼저 확인한다
    assert client.connection_pool.connection_kwargs["db"] == TEST_REDIS_DB
    await client.flushdb()
    yield client
    await client.flushdb()
    await client.aclose()


@pytest_asyncio.fixture
async def chat_hub(r) -> AsyncGenerator[ChatHub]:
    """테스트가 끝나면 읽기 루프를 모두 멈춘다.

    aclose 가 고장 나서 영원히 기다리더라도 테스트가 멈추지 않게 시간 제한을 둔다.
    """
    h = ChatHub(r)
    yield h
    await asyncio.wait_for(h.aclose(), 2)


# ---------------------------------------------------------------- API


@pytest_asyncio.fixture
async def client(db, r, chat_hub) -> AsyncGenerator[httpx.AsyncClient]:
    """앱에 HTTP 요청을 보내는 클라이언트. 서버를 띄우지 않고 앱에 바로 붙는다.

    앱의 DB 세션, redis, hub 를 테스트용으로 바꿔 끼운다.
    hub 는 원래 lifespan 이 만드는데, 이 방식은 lifespan 을 돌리지 않아서 여기서 넣는다.
    """
    # app.main 은 import 할 때 로깅 설정을 한다. 쓰는 테스트에서만 불러온다.
    from app.core.deps import get_chat_hub, get_db, get_r
    from app.main import app

    async def test_db() -> AsyncGenerator[AsyncSession]:
        yield db

    async def test_r() -> redis.Redis:
        return r

    def test_hub() -> ChatHub:
        return chat_hub

    app.dependency_overrides[get_db] = test_db
    app.dependency_overrides[get_r] = test_r
    app.dependency_overrides[get_chat_hub] = test_hub
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()
