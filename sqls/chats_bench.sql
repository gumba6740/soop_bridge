CREATE TABLE IF NOT EXISTS chats (
    id           BIGSERIAL PRIMARY KEY,
    stream_id    VARCHAR(50)  NOT NULL,   -- 방송(스트리머) 아이디
    user_id      VARCHAR(50)  NOT NULL,   -- 채팅 친 유저 아이디
    nickname     VARCHAR(100) NOT NULL,   -- 닉네임
    message      TEXT         NOT NULL,   -- 메시지 본문
    received_at  TIMESTAMPTZ  NOT NULL,   -- 받은 시각(서버가 안 줘서 직접 기록)
    svc_code     VARCHAR(4)   NOT NULL DEFAULT '0005'  -- 확장 대비
);