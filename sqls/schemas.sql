-- docker compose exec -T db psql -U soop -d soop_bridge -v ON_ERROR_STOP=1 -1 < sqls/schemas.sql

-- DROP TABLE IF EXISTS chats;
-- DROP TABLE IF EXISTS vod_chat_progress;
-- DROP TABLE IF EXISTS vod_files;
-- DROP TABLE IF EXISTS vod_fetch_failures;
-- DROP TABLE IF EXISTS vods;

-------------------------------------------------------
-- vods: VOD 메타데이터

-- 컬럼 출처는 모두 상세 응답(station/video/a/view)의 data 객체.
--   grade            : 19 = 성인인증 필요. 비로그인 수집 시 해당 파트가 files에서 빠짐
--   permanence       : "Y"/"N" → boolean. N이면 자동 삭제 대상으로 추정
--   auto_delete_date : 방송 종료 + 2년. permanence 와의 정확한 관계는 미확인이라 둘 다 저장
--   chat_duration    : 채팅 조회 단위(초). 채팅 콜렉터가 값을 쓴다
--   media_code       : media_exception.code. 1 = 정상, -6303 = 구독+ 잠김
--   broad_idx        : 방송 세션 번호. 한 방송이 여러 VOD로 쪼개진 경우 묶는 키
--   broad_end        : write_tm("시작 ~ 종료")의 뒷부분
--   view_cnt/live_total_view: 차이가 다시보기 조회수.
--                      최초 수집 시점 스냅샷이며 이후 갱신되지 않는다
-------------------------------------------------------
CREATE TABLE vods (
    vod_id           bigint      PRIMARY KEY,
    bid              text        NOT NULL,
    title            text        NOT NULL DEFAULT '',
    total_duration   bigint      NOT NULL DEFAULT 0,   -- 밀리초
    broad_start      timestamptz NOT NULL,
    broad_end        timestamptz,

    grade            smallint    NOT NULL DEFAULT 0,
    permanence       boolean,
    auto_delete_date timestamptz,
    chat_duration    integer     NOT NULL DEFAULT 300, -- 초
    media_code       integer,
    auth_no          smallint,
    broad_idx        bigint,
    hashtags         text[]      NOT NULL DEFAULT '{}',

    view_cnt         integer,
    live_total_view  integer,

    inserted_at      timestamptz NOT NULL DEFAULT now()
);


-- 워터마크 조회용: SELECT MAX(vod_id) FROM vods WHERE bid = ?
CREATE INDEX idx_vods_bid_vod_id ON vods (bid, vod_id DESC);

-------------------------------------------------------
-- vod_files: VOD 파트별 정보

-- file_order: VOD의 파트 번호. 1, 2, ...
-- file_order는 연속이 아니다. 19금 영상 등으로 인해 중간이 누락될 수 있음.
-------------------------------------------------------
CREATE TABLE vod_files (
    vod_id     bigint   NOT NULL REFERENCES vods (vod_id) ON DELETE CASCADE,
    file_order smallint NOT NULL,
    duration   bigint   NOT NULL DEFAULT 0,          -- 밀리초
    chat_url   text,
    hls_url    text,
    file_start timestamptz,
    PRIMARY KEY (vod_id, file_order)
);

-------------------------------------------------------
-- vod_fetch_failures: 누락 감지를 위한 기록

-- permanent = true  : 삭제/비공개 등. 재시도 무의미.
-- permanent = false : 429/5xx/파싱 실패 등. 백오프 후 재시도.
-- first_seen_at     : 처음 수집 시도 날짜
-- last_tried_at     : 마지막 수집 시도 날짜
-------------------------------------------------------
CREATE TABLE vod_fetch_failures (
    vod_id        bigint      PRIMARY KEY,
    bid           text        NOT NULL,
    reason        text        NOT NULL,
    permanent     boolean     NOT NULL DEFAULT false,
    attempts      integer     NOT NULL DEFAULT 1,
    first_seen_at timestamptz NOT NULL DEFAULT now(),
    last_tried_at timestamptz NOT NULL DEFAULT now()
);

-------------------------------------------------------
-- chat_progress: 채팅 수집 기록

-- 채팅은 chat_duration(기본 300초) 단위로 요청을 보내므로 부분 실패 가능성이 있음.
-- 따라서 수집 현황을 기록해서 재수집 여부를 판단할 재료로 사용. 

-- 재수집 시 데이터 중복을 막기 위해 
-- 일정 구간을 DELETE한 이후 새로 받아오는 방식으로 멱등성을 확보한다.
-------------------------------------------------------
CREATE TABLE chat_progress (
    vod_id          bigint      NOT NULL,
    file_order      smallint    NOT NULL,
    last_start_time integer     NOT NULL DEFAULT 0,  -- 파트 내 기준 초
    done            boolean     NOT NULL DEFAULT false,
    updated_at      timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (vod_id, file_order),
    FOREIGN KEY (vod_id, file_order)
        REFERENCES vod_files (vod_id, file_order) ON DELETE CASCADE
);

CREATE INDEX idx_chat_progress_pending ON chat_progress (vod_id) WHERE NOT done;

-------------------------------------------------------
-- chats: VOD 채팅 데이터 통합 테이블

-- type: 'chat', 'ogq', 'balloon', 'challenge_mission'
-- time_offset: VOD 전체 기준 시간(초) = (file_start - broad_start) + 파트 내 시간

-- parts 인덱스 매핑은 타입별로 다름 (아래 참고)

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
CREATE TABLE chats (
    id          bigint   GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    vod_id      bigint   NOT NULL REFERENCES vods (vod_id) ON DELETE CASCADE,
    file_order  smallint NOT NULL,   -- 구간 단위 재삽입에 필요
    type        text     NOT NULL,
    time_offset float8   NOT NULL,
    parts       text[]   NOT NULL
);

CREATE INDEX idx_chats_vod_id_t ON chats (vod_id, time_offset);

-- 재수집 시 DELETE 용
CREATE INDEX idx_chats_vod_file ON chats (vod_id, file_order, time_offset);
