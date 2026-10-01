from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Double,
    ForeignKey,
    ForeignKeyConstraint,
    Identity,
    Index,
    Integer,
    PrimaryKeyConstraint,
    SmallInteger,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base

# timestamptz
TSTZ = DateTime(timezone=True)


# ---------------------------------------------------------------- vods

class Vod(Base):
    __tablename__ = "vods"

    vod_id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=False
    )
    bid: Mapped[str] = mapped_column(Text)
    title: Mapped[str] = mapped_column(Text, server_default="")
    total_duration: Mapped[int] = mapped_column(  # 밀리초
        BigInteger, server_default=text("0")
    )
    broad_start: Mapped[datetime] = mapped_column(TSTZ)
    broad_end: Mapped[datetime | None] = mapped_column(TSTZ)

    grade: Mapped[int] = mapped_column(SmallInteger, server_default=text("0"))
    permanence: Mapped[bool | None] = mapped_column(Boolean)
    auto_delete_date: Mapped[datetime | None] = mapped_column(TSTZ)
    chat_duration: Mapped[int] = mapped_column(  # 초
        Integer, server_default=text("300")
    )
    media_code: Mapped[int | None] = mapped_column(Integer)
    auth_no: Mapped[int | None] = mapped_column(SmallInteger)
    broad_idx: Mapped[int | None] = mapped_column(BigInteger)
    hashtags: Mapped[list[str]] = mapped_column(
        ARRAY(Text), server_default=text("'{}'")
    )

    view_cnt: Mapped[int | None] = mapped_column(Integer)
    live_total_view: Mapped[int | None] = mapped_column(Integer)

    inserted_at: Mapped[datetime] = mapped_column(TSTZ, server_default=func.now())

    files: Mapped[list["VodFile"]] = relationship(
        back_populates="vod",
        order_by="VodFile.file_order",
        lazy="raise",
    )
# 워터마크 조회용: SELECT MAX(vod_id) FROM vods WHERE bid = ?
Index("idx_vods_bid_vod_id", Vod.bid, Vod.vod_id.desc())


# ---------------------------------------------- vod_files

class VodFile(Base):
    __tablename__ = "vod_files"
    __table_args__ = (PrimaryKeyConstraint("vod_id", "file_order"),)

    vod_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("vods.vod_id", ondelete="CASCADE"),
    )
    file_order: Mapped[int] = mapped_column(SmallInteger)
    duration: Mapped[int] = mapped_column(  # 밀리초
        BigInteger, server_default=text("0")
    )
    chat_url: Mapped[str | None] = mapped_column(Text)
    hls_url: Mapped[str | None] = mapped_column(Text)
    file_start: Mapped[datetime | None] = mapped_column(TSTZ)

    vod: Mapped[Vod] = relationship(back_populates="files", lazy="raise")


# -------------------------------------- vod_fetch_failures

class VodFetchFailure(Base):
    __tablename__ = "vod_fetch_failures"

    # vods에 저장되지 못한 VOD의 기록이라 외래키가 없다.
    vod_id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=False
    )
    bid: Mapped[str] = mapped_column(Text)
    reason: Mapped[str] = mapped_column(Text)
    permanent: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    attempts: Mapped[int] = mapped_column(Integer, server_default=text("1"))
    first_seen_at: Mapped[datetime] = mapped_column(TSTZ, server_default=func.now())
    last_tried_at: Mapped[datetime] = mapped_column(TSTZ, server_default=func.now())
    resolved_at: Mapped[datetime | None] = mapped_column(TSTZ)


# ------------------------------------------------- chat_progress

class ChatProgress(Base):
    __tablename__ = "chat_progress"
    __table_args__ = (
        PrimaryKeyConstraint("vod_id", "file_order"),
        # 컬럼 두 개를 묶은 외래키는 컬럼 하나에 ForeignKey를 달 수 없어서 여기서 정의한다.
        ForeignKeyConstraint(
            ["vod_id", "file_order"],
            ["vod_files.vod_id", "vod_files.file_order"],
            ondelete="CASCADE",
        ),
        # 부분 인덱스: 미완료 행만 담는다.
        Index(
            "idx_chat_progress_pending", "vod_id", postgresql_where=text("NOT done")
        ),
    )

    vod_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    file_order: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    last_start_time: Mapped[int] = mapped_column(  # 파트 내 기준 초
        Integer, server_default=text("0")
    )
    done: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    updated_at: Mapped[datetime] = mapped_column(TSTZ, server_default=func.now())


# ---------------------------------------------------------------- chats

class Chat(Base):
    """parts의 인덱스 매핑은 docs/chat_types.md 참고.

    ORM으로 받은 parts는 파이썬 리스트라 0부터, SQL 안에서는 1부터 센다.
    """

    __tablename__ = "chats"
    __table_args__ = (
        Index("idx_chats_vod_id_t", "vod_id", "time_offset"),
        # 재수집 시 DELETE 용
        Index("idx_chats_vod_file", "vod_id", "file_order", "time_offset"),
    )

    # GENERATED ALWAYS AS IDENTITY
    id: Mapped[int] = mapped_column(
        BigInteger, Identity(always=True), primary_key=True
    )
    vod_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("vods.vod_id", ondelete="CASCADE")
    )
    file_order: Mapped[int] = mapped_column(SmallInteger)  # 구간 단위 재삽입에 필요
    type: Mapped[str] = mapped_column(Text)
    time_offset: Mapped[float] = mapped_column(Double)  # float8
    parts: Mapped[list[str]] = mapped_column(ARRAY(Text))


# ---------------------------------------------------------------- vod_views

class VodView(Base):
    """우리 서비스에서 집계하는 조회수. SOOP 데이터와 주인이 달라서 분리."""

    __tablename__ = "vod_views"

    vod_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("vods.vod_id", ondelete="CASCADE"),
        primary_key=True,
        autoincrement=False,
    )
    cnt: Mapped[int] = mapped_column(BigInteger, server_default=text("0"))