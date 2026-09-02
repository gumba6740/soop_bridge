-- docker compose exec -T db psql -U soop -d soop_chat -v ON_ERROR_STOP=1 -1 < sqls/schemas.sql


CREATE TABLE IF NOT EXISTS chats (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    bid         text NOT NULL,
    parts       text[] NOT NULL,
    received_at timestamptz NOT NULL,
    inserted_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_chats_bid_receive ON chats (bid, received_at DESC);

CREATE OR REPLACE VIEW chats_view AS
SELECT
    id,
    bid,
    parts[1] AS message,
    parts[2] AS user_id,
    parts[6] AS user_nickname,
    parts,
    received_at
FROM chats;


CREATE TABLE IF NOT EXISTS users (
    user_id       text PRIMARY KEY,
    nickname      text,                              -- 마지막으로 관측된 닉네임
    first_seen_at timestamptz,
    last_seen_at  timestamptz,
    chat_count    bigint NOT NULL DEFAULT 0,
    updated_at    timestamptz NOT NULL DEFAULT now(), -- 마지막 배치 반영 시각
    is_streamer   boolean NOT NULL DEFAULT false
);

CREATE TABLE IF NOT EXISTS chats_bench_noidx (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    bid         text NOT NULL,
    parts       text[] NOT NULL,
    received_at timestamptz NOT NULL,
    inserted_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS chats_bench_idx (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    bid         text NOT NULL,
    parts       text[] NOT NULL,
    received_at timestamptz NOT NULL,
    inserted_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_chats_bench_idx_bid_receive ON chats_bench_idx (bid, received_at DESC);


