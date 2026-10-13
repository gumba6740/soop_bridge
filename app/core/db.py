"""SQLAlchemy 비동기 엔진.

종료 시 정리(dispose)는 lifespan에서 한다.
"""

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.core.config import settings

engine = create_async_engine(
    settings.sqlalchemy_url,
    pool_pre_ping=True,  # 풀에서 꺼낼 때 끊긴 커넥션인지 확인 (DB 재시작 대비)
    pool_size=5,  # 평소 살려둘 커넥션 수
    max_overflow=10,  # 몰릴 때 추가로 허용할 수. 반납 시 바로 닫힘
)

SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass
