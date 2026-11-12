"""baseline

Revision ID: 260930_0000
Revises:
Create Date: 2026-09-30 00:00:00
"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = '260930_0000'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# sqls/schemas.sql과 같은 내용. 문장 하나씩 실행한다.
STATEMENTS = [
    """
    CREATE TABLE vods (
        vod_id           bigint      PRIMARY KEY,
        bid              text        NOT NULL,
        title            text        NOT NULL DEFAULT '',
        total_duration   bigint      NOT NULL DEFAULT 0,
        broad_start      timestamptz NOT NULL,
        broad_end        timestamptz,

        grade            smallint    NOT NULL DEFAULT 0,
        permanence       boolean,
        auto_delete_date timestamptz,
        chat_duration    integer     NOT NULL DEFAULT 300,
        media_code       integer,
        auth_no          smallint,
        broad_idx        bigint,
        hashtags         text[]      NOT NULL DEFAULT '{}',

        view_cnt         integer,
        live_total_view  integer,

        inserted_at      timestamptz NOT NULL DEFAULT now()
    )
    """,
    "CREATE INDEX idx_vods_bid_vod_id ON vods (bid, vod_id DESC)",
    """
    CREATE TABLE vod_files (
        vod_id     bigint   NOT NULL REFERENCES vods (vod_id) ON DELETE CASCADE,
        file_order smallint NOT NULL,
        duration   bigint   NOT NULL DEFAULT 0,
        chat_url   text,
        hls_url    text,
        file_start timestamptz,
        PRIMARY KEY (vod_id, file_order)
    )
    """,
    """
    CREATE TABLE vod_fetch_failures (
        vod_id        bigint      PRIMARY KEY,
        bid           text        NOT NULL,
        reason        text        NOT NULL,
        permanent     boolean     NOT NULL DEFAULT false,
        attempts      integer     NOT NULL DEFAULT 1,
        first_seen_at timestamptz NOT NULL DEFAULT now(),
        last_tried_at timestamptz NOT NULL DEFAULT now(),
        resolved_at   timestamptz
    )
    """,
    """
    CREATE TABLE chat_progress (
        vod_id          bigint      NOT NULL,
        file_order      smallint    NOT NULL,
        last_start_time integer     NOT NULL DEFAULT 0,
        done            boolean     NOT NULL DEFAULT false,
        updated_at      timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY (vod_id, file_order),
        FOREIGN KEY (vod_id, file_order)
            REFERENCES vod_files (vod_id, file_order) ON DELETE CASCADE
    )
    """,
    "CREATE INDEX idx_chat_progress_pending ON chat_progress (vod_id) WHERE NOT done",
    """
    CREATE TABLE chats (
        id          bigint   GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        vod_id      bigint   NOT NULL REFERENCES vods (vod_id) ON DELETE CASCADE,
        file_order  smallint NOT NULL,
        type        text     NOT NULL,
        time_offset float8   NOT NULL,
        parts       text[]   NOT NULL
    )
    """,
    "CREATE INDEX idx_chats_vod_id_t ON chats (vod_id, time_offset)",
    "CREATE INDEX idx_chats_vod_file ON chats (vod_id, file_order, time_offset)",
]


def upgrade() -> None:
    for sql in STATEMENTS:
        op.execute(sql)


def downgrade() -> None:
    # 만든 순서의 반대로 지운다 (외래키 때문에).
    for table in ["chats", "chat_progress", "vod_fetch_failures", "vod_files", "vods"]:
        op.execute(f"DROP TABLE {table}")