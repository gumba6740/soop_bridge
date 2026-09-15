-- docker compose exec -T db psql -U soop -d soop_bridge -v ON_ERROR_STOP=1 -1 < sqls/schemas.sql

-------------------------------------------------------
-- vods: VOD 메타데이터
-------------------------------------------------------
CREATE TABLE IF NOT EXISTS vods (
    vod_id         bigint      PRIMARY KEY,
    bid            text        NOT NULL,
    title          text        NOT NULL DEFAULT '',
    total_duration bigint      NOT NULL DEFAULT 0,   -- 밀리초
    broad_start    timestamptz NOT NULL,
    inserted_at    timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_vods_bid_broad_start 
ON vods (bid, broad_start);

-------------------------------------------------------
-- vod_files: VOD 파트별 정보
-------------------------------------------------------
CREATE TABLE IF NOT EXISTS vod_files (
    vod_id     bigint   NOT NULL REFERENCES vods (vod_id),
    file_order smallint NOT NULL,
    duration   bigint   NOT NULL DEFAULT 0,          -- 밀리초
    chat_url   text,
    file_start timestamptz,
    PRIMARY KEY (vod_id, file_order)
);

-------------------------------------------------------
-- chats: VOD 채팅 데이터 통합 테이블
--
-- type: 'chat', 'ogq', 'balloon', 'challenge_mission'
-- t:    VOD 전체 기준 시간(초), 파트 offset 적용 완료
--
-- parts 인덱스 매핑은 타입별로 다름 (아래 참고)
--
-- chat:
--   [1]  m     메시지
--   [2]  u     유저 ID
--   [3]  c     ?
--   [4]  mt    메시지 타입
--   [5]  l     레벨
--   [6]  n     닉네임
--   [7]  p     ?
--   [8]  p2    ?
--   [9]  fw    팬 등급 (-1 = 없음)
--   [10] nf    닉네임 색상 (다크모드)
--   [11] nd    닉네임 색상 (라이트모드)
--   [12] acfw  ?
--   [13] pn    ?
--   [14] tlk   ?
--
-- ogq:
--   [1]  ch    채널?
--   [2]  m     메시지
--   [3]  gid   OGQ 그룹 ID
--   [4]  sid   스티커 ID
--   [5]  v     ?
--   [6]  s     보낸 유저 ID (+ 정보)
--   [7]  sn    보낸 유저 닉네임
--   [8]  sf    ?
--   [9]  c     ?
--   [10] l     레벨
--   [11] mt    ?
--   [12] e     확장자 (png 등)
--   [13] fw    팬 등급
--   [14] nf    닉네임 색상 (다크모드)
--   [15] nd    닉네임 색상 (라이트모드)
--   [16] acfw  ?
--   [17] pn    ?
--   [18] anm   애니메이션 여부?
--   [19] tlk   ?
--
-- balloon:
--   [1]  bj    스트리머 ID
--   [2]  u     보낸 유저 ID
--   [3]  n     보낸 유저 닉네임
--   [4]  c     개수
--   [5]  fs    ?
--   [6]  tfn   ?
--   [7]  fc    ?
--   [8]  fn    ?
--   [9]  def   ?
--   [10] lng   언어 코드
--   [11] mof   ?
--
-- challenge_mission:
--   [1]  type     미션 타입 (CHALLENGE_GIFT 등)
--   [2]  ch       채널?
--   [3]  subch    서브채널?
--   [4]  key      미션 키
--   [5]  title    미션 제목
--   [6]  fn       파일명?
--   [7]  c        개수/금액
--   [8]  u        보낸 유저 ID
--   [9]  n        보낸 유저 닉네임
--   [10] bj       스트리머 ID
--   [11] bj_nick  스트리머 닉네임
-------------------------------------------------------
CREATE TABLE IF NOT EXISTS chats (
    id     bigint  GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    vod_id bigint  NOT NULL REFERENCES vods (vod_id),
    type   text    NOT NULL,
    time_offset    float8  NOT NULL,
    parts  text[]  NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_chats_vod_id_t      ON chats (vod_id, time_offset);