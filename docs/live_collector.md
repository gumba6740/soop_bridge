# 라이브 collector

SOOP 라이브 방송의 채팅, 영상 주소, 방송 현황을 받아 redis에 올리는 collector의 정리.
SOOP의 각 응답에 무엇이 들어 있고, 그중 무엇을 왜 쓰는지, 그리고 collector가 그 값을 어떤 순서로 다루는지를 적는다.

- 코드: `app/core/soop_clients/live_clients/`(SOOP과 통신), `app/live/shared/chat_parser.py`(채팅 패킷 나누기, 올릴 코드), `app/live/collector/`(수집 흐름)
- 실행: `python -m app.live.collector [bid ...]` (bid를 주지 않으면 `streamers.py`의 전원)
- 컨테이너: compose의 `live-collector`. `docker compose up -d --build live-collector`로 띄우고, 로그는 `docker compose logs -f live-collector`. 코드를 고치면 `--build`로 다시 띄워야 반영된다
- 확인 근거: 직접 요청과 `.local/live_test.py` 기록. "확인"이라고 적은 것은 실제 응답으로 본 것이고, "추정"은 아직 근거가 부족한 것이다.

---

## 1. 용어

| 용어 | 가리키는 것 | 코드 |
|---|---|---|
| 접속 정보 | `player_live_api.php` 응답의 `CHANNEL`. 채팅 서버와 영상에 접속하는 데 필요한 값 | `shared.get_live_info` |
| 방송 현황 | `/station` 응답. 생방송 화면 아래 설명 영역의 값(시청자 수, 제목 등) | `broad_client.get_broad_detail` |
| 라이브 요약 | 위 둘에서 API가 쓸 것만 골라 redis에 둔 해시 | `soop:live:{bid}:summary` |
| 재생 토큰(aid) | 라이브 m3u8 주소 뒤에 붙이는 값 | `vod_client.get_aid` |
| 재생 주소 | `view_url?aid=...`. 플레이어에 넘기는 라이브 m3u8 주소 | `vod_client.get_hls_url` |
| 방송 상태 변경 | 채팅 웹소켓으로 오는 0007. 방송 종료 말고도 온다(7절) | `chat_client.SVC_BROAD_STATE` |

---

## 2. 전체 흐름

```
스트리머마다 watch 하나 (TaskGroup)
│
├─ 접속 정보 조회 ── 방송 중 아님 ──→ 요약 삭제 → 60초 뒤 다시 조회
│        │
│        ├─ 연령 제한(-6), 직전에 방송 중 → 요약 유지, 마지막 접속 정보로 재접속
│        │
│        └─ 방송 중
│             ├─ 새로 시작 / BNO 변경 → on_start: 재생 주소 2개 발급, 요약 새로 쓰기
│             │                        → detail_loop 시작 (60초마다 방송 현황 → 요약 갱신)
│             │
│             ├─ 같은 방송의 재접속 → refresh_video: 재생 주소만 다시 발급
│             │
│             └─ relay_chats: 채팅 웹소켓 접속 → 채팅, 후원, 구독 패킷마다 XADD
│                    ├─ 0007, 1초 안에 짝 → 연결 유지, 계속 수신
│                    ├─ 0007, 짝 없음 → 1초 뒤 접속 정보 다시 조회 (종료인지 판단)
│                    └─ 끊김/600초 무응답 → 20초 뒤 다시 조회
│
└─ collector 종료 → detail_loop 정리 → 모든 요약 삭제
```

SOOP에 보내는 요청과 순서는 다음과 같다.

| 순서 | 요청 | 언제 | 결과의 쓰임 |
|---|---|---|---|
| 1 | 접속 정보 (`player_live_api`, type=live) | 방송 중 아닐 때 60초마다, 짝 없는 0007이나 끊김 뒤 | 방송 중인지, 2~4에 넣을 값 |
| 2 | 재생 토큰 (`player_live_api`, type=aid) | 접속 정보가 LIVE로 올 때마다, on_start 또는 refresh_video로 요청. (화질마다) | 재생 주소 |
| 3 | m3u8 주소 (`broad_stream_assign`) | 2와 같은 때. (화질마다) | 재생 주소 |
| 4 | 채팅 웹소켓 | 방송 중 계속 | 채팅 스트림 |
| 5 | 방송 현황 (`/station`) | 방송 중 60초마다 | 시청자 수, 시작 시각, 제목 |

---

## 3. 접속 정보: `player_live_api.php` (type=live)

```
POST https://live.sooplive.com/afreeca/player_live_api.php
헤더: Referer: https://play.sooplive.com/{bid}, Origin: https://play.sooplive.com, 브라우저 User-Agent
본문(form): type=live, bid, pwd="", from_api=0, mode=landing, player_type=html5, stream_type=common
```

본문은 JSON이 아니라 form 형식이다. `from_api`, `mode`, `player_type`, `stream_type`은 streamlink를 따라 넣은 값이고, 뜻은 이름으로 추정한 것이다. `player_type=html5`(웹 플레이어) 때문에 화질이 540p까지로 제한된다(5절 참고).

### RESULT

| 값 | 뜻 | 응답 | `LiveStatus` |
|---|---|---|---|
| 1 | 정상(방송 중) | 아래 필드 전체 | `LIVE` (BPWD가 Y면 `PASSWORD`) |
| 0 | 방송 중 아님 (확인) | `geo_cc`, `geo_rc`, `acpt_lang`, `svc_lang`, `ISSP`, `RESULT`, `GDPR`만 | `OFFLINE` |
| -6 | 로그인 필요 (확인) | 연령 제한 방송. 방송 중간에 연령 제한을 켜도 이 값이 된다 | `LOGIN_REQUIRED` |
| -14 | 구독플러스 방송 (확인) | 구독자 전용. debug 로그만 남긴다 | `SUB_PLUS` |
| 그 밖 | 모름 | | `UNKNOWN` (warning 로그) |

### 쓰는 필드

| 필드 | 예 | 쓰임 |
|---|---|---|
| `RESULT` | `1` (숫자) | 방송 상태 판단 |
| `BPWD` | `"N"` | `"Y"`면 비밀번호 방송으로 본다(추정). 우리는 비밀번호를 모르니 접속하지 않는다 |
| `BNO` | `"9자리 INT"` (문자열) | 방송 번호. 재생 토큰과 m3u8 요청에 넣고, 방송이 바뀌었는지 판단한다 |
| `BJNICK` | `"닉네임"` | 스트리머 닉네임. 요약의 `nick` |
| `TITLE` | | 방송 제목. 요약의 `title` 첫 값 |
| `CHDOMAIN`, `CHPT` | `"chat-76DBFCCB.sooplive.com"`, `"9000"` | 채팅 서버 주소. 웹소켓 포트는 `CHPT + 1` |
| `CHATNO` | `"4146"` | 채팅방 번호. join 패킷에 넣는다 |
| `FTK` | `"<해시>_<bid>_<BNO>_html5_1"` | 채팅방 입장 토큰. join 패킷에 넣는다. 로그인하면 끝자리가 `_0`이었다 |
| `RMD` | `"https://livestream-manager.sooplive.com"` | m3u8 배정 서버 주소 |
| `CDN` | `"lg_cdn"` | CDN 종류. `broad_stream_assign`의 `return_type`으로 바꿔 넣는다 |
| `LANG_TAGS`, `CATEGORY_TAGS`, `HASH_TAGS` | `["한국어"]`, `["게임"]` | 언어, 카테고리, 해시태그. 이 순서로 합쳐 요약의 `tags`에 넣는다 |
| `VIEWPRESET` | 아래 화질표 참고 | 화질 목록 |
| `geo_cc`, `geo_rc`, `acpt_lang`, `svc_lang` | `"KR"`, `"41"`, `""`, `"ko_KR"` | join 패킷 옵션. `acpt_lang`은 로그인하지 않으면 빈 문자열이라 기본값 `ko_KR`을 쓴다 |

`VIEWPRESET`의 `name`은 요청에 넣는 화질 이름이고 `label`은 화면 표시용이다. `bps`가 비트레이트(kbps)라서, `hd4k`의 4k는 해상도가 아니라 4000kbps다. `original`은 스트리머가 송출하는 원본 화질이라 방송마다 해상도가 다르다(1440p인 방송, 1080p인 방송을 확인).

| name | label | bps |
|---|---|---|
| `sd` | 360p | 500 |
| `hd` | 540p | 1000 |
| `hd4k` | 720p | 4000 |
| `hd8k` | 1080p | 8000 |
| `original` | 원본 (1080p, 1440p 등) | 8000 ~ 16000 |
| `auto` | 자동 | 실제 스트림이 아니라 플레이어의 선택 방식. 요청하지 않는다 |

### 쓰지 않지만 확인한 필드

| 필드 | 내용 |
|---|---|
| `BTIME` | 방송 시작 후 지난 초. `지금 - BTIME`이 방송 현황의 `broad_start`와 몇 초 안으로 맞았다 |
| `CATE` | 카테고리 번호(8자리). 방송 현황의 `broad_cate_no`와 같은 값. 4자리씩 두 단계 분류로 보인다(추정) |
| `AUTO_HASHTAGS` | 자동 해시태그로 추정. 본 방송은 모두 빈 리스트였다 |
| `BSTATUS` | `"BROADING"`(방송 중) |
| `RESOLUTION`, `BPS`, `S1440P` | 송출 해상도, 비트레이트, 1440p 여부 |
| `TS`, `TS_SNAPSHOT` | `live-tm-sub-...` 주소의 `playlist.m3u8`, `tile.m3u8`. 타임머신(뒤로 돌려보기)과 썸네일로 추정. 확인 안 함 |
| `PCON_OBJECT` | 구독 개월별 배지 이미지 |
| `PREROLL`, `MIDROLL` 등 | 광고 설정 |

---

## 4. 재생 주소 만들기

### 4-1. 재생 토큰: `player_live_api.php` (type=aid)

```
본문: 3절의 공통값 + type=aid, bid, bno, pwd="", quality=<VIEWPRESET.name>
응답: CHANNEL.RESULT == 1 이면 CHANNEL.AID
```

확인한 성질:
- 같은 방송, 같은 화질이면 몇 시간이 지나 다시 받아도 같은 값이었다(6시간 반 간격).
- 방송(BNO)이 바뀌면 다른 값이 된다.
- 웹 경로에서는 `hd` 이상을 요청하면 전부 같은 값(540p 토큰)이 나온다.
- 앞부분 `.A32.pxqRXFPZNcY9Qg1.`은 서로 다른 방송 여섯 개와 `COLONY_CONTENT`, 로그인 토큰에도 똑같이 붙어 있었다. 방송이 아니라 토큰 형식이나 요청한 쪽을 나타내는 값으로 보인다. 다른 IP로 요청하면 다르게 올 수 있음.

### 4-2. m3u8 주소: `broad_stream_assign.html`

```
GET {RMD}/broad_stream_assign.html?return_type=<CDN 매핑값>&broad_key={BNO}-common-{quality}-hls
응답: view_url (예: https://live-pcweb-kr-cdn-z02.sooplive.com/live-stmc-38/auth_playlist.m3u8)
```

`RMD`가 "어느 영상 서버에서 받으면 되는지" 골라주는 배정 서버다. `return_type`은 접속 정보의 `CDN`에 `_pc_web`을 붙인 값이다(`gs_cdn` → `gs_cdn_pc_web`, `lg_cdn` → `lg_cdn_pc_web`). 이 경로와 매핑은 streamlink의 SOOP 플러그인에서 가져왔다.

### 4-3. 재생 주소

`view_url?aid=<재생 토큰>`이 재생 주소다. 요청이 아니라 4-1과 4-2의 결과를 합친 것이다. collector는 `sd`, `hd` 두 화질만 만든다.

---

## 5. 라이브 영상(m3u8)

재생 주소로 받는 m3u8은 2초짜리 조각(.TS) 목록이고, 플레이어가 몇 초마다 다시 받아 새 조각을 이어 붙인다.

```
#EXTM3U
#EXT-X-MEDIA-SEQUENCE:17592        ← 목록의 첫 조각 번호. 다시 받을 때마다 늘어난다
#EXT-X-TARGETDURATION:2            ← 조각 하나는 최대 2초
#EXTINF:2.007,
960x540/297556777-common-hd-hls_17596_....TS   ← 상대 주소. 해상도 폴더가 앞에 붙는다
```

| 항목 | 확인한 내용 |
|---|---|
| 헤더 | m3u8과 조각 모두 **Referer**가 있어야 200. Origin만이거나 헤더가 없으면 403 |
| aid | m3u8에만 필요. 조각 주소에는 없다 |
| CORS | `Access-Control-Allow-Origin: https://play.sooplive.com`. 다른 사이트의 자바스크립트로는 읽을 수 없다 |
| 화질 | 웹 경로로는 540p(`hd`)가 최대. 그 이상은 SOOP 고화질 프로그램 전용 |
| 유지 | 같은 재생 주소로 방송 내내 재생됐다. 토큰을 받은 지 8시간이 지나도 됐다. 다만 0007 뒤 송출이 다시 시작되면, BNO와 aid가 같아도 이전 주소가 400이 되고 새로 받은 주소는 정상인 경우가 있었다(두 방송). 배정 서버가 바뀐 것으로 보인다. 그 순간에는 `broad_stream_assign`이 잠깐 400을 주기도 했다 |
| 방송 종료 후 | 같은 주소가 400(본문은 `Not Found`)이 된다. `#EXT-X-ENDLIST`는 오지 않는다 |
| 크기 | 540p 조각 약 360KB / 2초 (약 1.4Mbps), 360p 약 230KB / 2초 |

브라우저는 Referer를 바꿀 수 없고 CORS에도 막히므로, 시청자의 브라우저가 이 주소를 직접 재생할 수는 없다. 시청자에게 보여주려면 서버가 받아서 전달(프록시)하거나 SOOP 공식 플레이어를 써야 한다. 이건 API 설계에서 정한다.

---

## 6. 방송 현황: `/station`

```
GET https://chapi.sooplive.com/api/{bid}/station
헤더: 브라우저 User-Agent (없으면 404를 준다. 막혔다고 하지 않고 없는 페이지인 척한다)
```

주소는 `/station`이지만 방송국 페이지가 아니라 **생방송 화면 아래 설명 영역**의 데이터다.

### 쓰는 필드

| 필드 | 예 | 쓰임 |
|---|---|---|
| `broad` | 객체 / `null` | 방송 중이 아니면 `null` |
| `broad.broad_no` | `9자리 INT` (숫자) | 접속 정보의 BNO와 같은 값. 다른 방송의 값을 거르는 데 쓴다. 타입이 달라서 문자열로 바꿔 비교한다 |
| `broad.broad_title` | | 요약의 `title`(최신 제목) |
| `broad.current_sum_viewer` | `513` | 요약의 `viewers` |
| `station.broad_start` | `"2026-10-04 18:01:57"` | 요약의 `started_at`. **마지막 방송**의 시작 시각이라 방송 중이 아니어도 값이 있다. `broad`가 있을 때만 쓴다 |
| `current_timestamp` | `"2026-10-05 00:14:38"` | 응답 시각. 요약의 `viewers_at` |

시각 문자열에는 시간대가 없고 값은 한국시간이다. 저장할 때 `+09:00`을 붙인 ISO 형식으로 바꾼다. 브라우저에 표시되는 시작 시각과는 2초 정도 차이가 있었다.

### 쓰지 않지만 확인한 필드

| 필드 | 내용 |
|---|---|
| `upd.fan_cnt`, `upd.total_view_cnt`, `upd.total_visit_cnt` | 팬 수, 누적 시청, 누적 방문 |
| `subscription.total`, `tier1`, `tier2` | 구독자 수 |
| `profile_image`, `station.user_nick`, `station.station_name` | 프로필 |
| `menus`, `vods` | 방송국 게시판 메뉴 |
| `starballoon_top`, `sticker_top` | 후원 상위 시청자의 아이디와 닉네임. **다른 사람의 정보라 저장하지 않는다** |

접속 정보와 비교해서 확인한 것:
- 연령 제한(-6) 중에도 `broad`가 오고 시청자 수가 갱신된다.
- 방송이 끝나면 RESULT 0과 `broad` null이 같은 시점에 나온다.
- `broad_start`와 `지금 - BTIME`의 차이는 -2 ~ +1초였다.

---

## 7. 채팅 웹소켓

```
wss://{CHDOMAIN}:{CHPT + 1}/Websocket/{bid}   (subprotocol: chat)
```

### 패킷

모든 패킷은 `ESC TAB {서비스 코드 4자리}{본문 길이 6자리}00{본문}` 모양이다. 본문 안은 `\x0c`(SEP)로 칸이 나뉜다.

| 보내는 패킷 | 코드 | 내용 |
|---|---|---|
| connect | 0001 | 접속 직후 |
| join | 0002 | connect 뒤 **1초 쉬고** 보낸다. 바로 보내면 동작하지 않는다. `CHATNO`, `FTK`, 옵션이 들어간다 |
| ping | 0000 | 60초마다. SOOP 웹 플레이어를 따라 한 것. 서버는 응답하지 않는다(확인) |

웹소켓 라이브러리의 자체 핑은 끈다(`ping_interval=None`). 켜두면 서버가 답하지 않아 연결이 끊긴다.

### 받는 패킷

docs/live_chat_packets.md 참고.

0007(방송 상태 변경)은 방송 종료뿐 아니라 다시보기 자르기, 연령 제한을 켜고 끌 때도 온다. 그래서 0007만으로 끝내지 않고 접속 정보로 다시 판단한다. 0007 뒤 같은 방송이면 BNO가 바뀐 적은 없었다. 서버는 0007을 보내도 연결을 끊지 않는다.

패킷은 `ESC TAB 0007 000003 00 SEP {값} SEP`이고, 값은 `1` 또는 `0` 한 글자다. 뜻은 모른다(11절).

| 경우 | 본 것 |
|---|---|
| 방송 종료 (2026-10-07 1건) | `0`이 15초 간격으로 두 번, 그 뒤 패킷 없음. 서버는 연결을 끊지 않았고 RECV_TIMEOUT으로 끝났다 |
| 일반 방송 (2026-10-06, 10-08 세 방송) | `1` 다음 `0`이 짝으로(대부분 수 ms, 길게는 0.4초 뒤) 5~7초마다 계속 왔다. 채팅도 계속 왔다 |
| 스트리머 본인 입장 (2026-10-07 1건) | `0` 하나가 스트리머 아이디의 0004(입장)와 같은 밀리초에 왔다 |
| 그 밖 | 30건 남짓 중 가장 짧은 간격이 17초. 보통은 수십 분에서 몇 시간에 한 번 |

`0` 패킷은 종료 때와 일반 방송 때가 바이트까지 같아서, 본문만으로 종료를 가를 수 없다. 그래서 chat_client는 0007 뒤 `STATE_PAIR_WINDOW`(1초) 안에 0007이 또 오면 짝으로 보고 연결을 유지하고, 안 오면 연결을 끝내서 watch가 접속 정보로 판단하게 한다. 짝인지는 값이 아니라 개수로 본다(짝의 모양을 몇 번밖에 못 봐서, 틀렸을 때 재접속이 반복되는 쪽보다 종료를 늦게 아는 쪽을 택했다). 0007에서 바로 끊던 때는 짝이 오는 방송에서 5초마다 재접속했다.

### 0005의 parts

헤더 덩어리(첫 칸)를 뺀 나머지다. 다른 코드도 같은 방식으로 나눈다(`app/live/shared/chat_parser.parse_packet`). chat_client는 원본 bytes를 넘기고, relay_chats가 나눠서 올린다.

| 칸 | 내용 |
|---|---|
| 0 | 메시지 |
| 1 | 유저 아이디 |
| 5 | 닉네임 |
| 그 밖 | docs/live_chat_packets.md 2절 |

### 연결 유지

- 14시간 동안 연결이 한 번도 끊기지 않았다(세 방송). 이후 여섯 방송, 약 15시간에서도 끊김은 없었다.
- 패킷 사이 간격은 처음 테스트에서 가장 길 때 66초, 이후 204초였다. 0005 말고도 다른 코드가 꾸준히 와서 이 정도로 짧다. 지금은 300초(`RECV_TIMEOUT`)로 끊는다.
- 300초(`RECV_TIMEOUT`) 동안 아무것도 안 오면 죽은 연결로 보고 끊는다. 핑에 응답이 없으니 이게 죽은 연결을 알아챌 유일한 방법이다.
- 연령 제한(-6)이 켜져도 이미 연결된 채팅은 계속 들어왔다(약 2시간, 2시간 반 구간 확인).
- 연령 제한 중에 연결이 끊겨도, 마지막으로 받은 접속 정보로 다시 접속할 수 있었다.

---

## 8. redis 키

### `soop:live:{bid}:summary` (해시, 라이브 요약)

방송 중일 때만 있다. 이 키가 있으면 방송 중으로 본다.

| 필드 | 출처 | 쓰는 곳 |
|---|---|---|
| `bno` | 접속 정보 `BNO` | on_start |
| `nick` | 접속 정보 `BJNICK` | on_start |
| `title` | 접속 정보 `TITLE` → 이후 방송 현황 `broad_title` | on_start, detail_loop |
| `hls_sd`, `hls_hd` | 재생 주소 (4절) | on_start, 같은 방송의 재접속(refresh_video). 발급에 실패하면 필드가 없다 |
| `started_at` | 방송 현황 `station.broad_start` | detail_loop |
| `viewers` | 방송 현황 `current_sum_viewer` | detail_loop |
| `viewers_at` | 방송 현황 `current_timestamp` | detail_loop |
| `tags` | 접속 정보 `LANG_TAGS` + `CATEGORY_TAGS` + `HASH_TAGS` (JSON 리스트) | on_start, 같은 방송의 재접속(refresh_video) |

값은 전부 문자열이고 시각은 `+09:00`이 붙은 ISO 형식이다. on_start 직후 detail_loop가 바로 한 번 돌기 때문에, `started_at`과 `viewers`는 보통 시작 직후 곧바로 채워진다. 방송 현황 요청이 실패하면 그동안은 없다.

### `soop:live:{bid}:chats` (스트림)

| 항목 | 내용 |
|---|---|
| 항목 | 올리는 패킷 한 건 = 항목 하나. 필드는 `svc`(서비스 코드 4자리)와 `parts`(JSON 문자열) |
| 올리는 코드 | `app/live/shared/chat_parser.RELAY_SVCS`: 0005 채팅, 0018 별풍선, 0087 애드벌룬, 0121 도전미션, 0091 신규 구독, 0093 N개월째 구독, 0108 구독권 선물, 0109 OGQ 이모티콘, 0125 도전미션 정산 명세, 0058 별별랭킹. 모두 같은 스트림, 같은 개수 제한 안에 들어간다 |
| 길이 | 최근 약 `CHAT_MAXLEN`개만 남긴다(MAXLEN ~) |
| ID | redis가 붙이는 `밀리초-순번`. 읽는 쪽이 "이 ID 다음부터"로 이어 읽는다 |

### 언제 생기고 사라지나

| 때 | summary | chats |
|---|---|---|
| 방송 시작, BNO 변경 (on_start) | 지우고 새로 쓴다 | 만료 예약이 있으면 취소한다(PERSIST) |
| 방송 중 | detail_loop가 60초마다 일부 필드를 갱신 | XADD |
| 방송 종료 확인 (on_end) | 지운다 | 600초 뒤 삭제 예약(EXPIRE) |
| 종료 후 10분 안에 재시작 | 새로 쓴다 | 예약 취소, 이전 채팅에 이어서 쌓인다 |
| collector 정상 종료 | 지운다 (watch_all) | 그대로 |
| collector 강제 종료 | 남는다 | 남는다 |
| collector 재시작, 방송 없음 | 첫 확인에서 지운다 | 600초 뒤 삭제 예약(첫 확인의 on_end) |
| 방송 중에 `streamers.py`에서 뺌 | (정상 종료 때 지워짐) | 만료 없이 남는다. 직접 지운다 |

redis는 XADD나 HSET으로 값을 바꿔도 만료 시간을 초기화하지 않는다. 그래서 재시작할 때 PERSIST로 직접 취소한다.

---

## 9. watch 상세

### 상태

watch는 직전 상태를 `live`에 적어두고, 지금 조회한 접속 정보와 비교해서 할 일을 정한다.

| 직전 `live` | 지금 | 할 일 |
|---|---|---|
| `None` (막 켬) | 볼 수 없음 | 이전 실행이 남긴 summary 삭제. "종료" 로그는 남기지 않는다 |
| `True` | 볼 수 없음 | detail_loop 정지 → on_end. "방송 종료" 로그 |
| `False` | 볼 수 없음 | 없음. 60초 쉰다 |
| `True` | 연령 제한(-6) | summary 유지, 마지막 접속 정보로 채팅 재접속 (재생 주소는 갱신하지 않는다) |
| `None`, `False` | 방송 중 | on_start → detail_loop 시작 → 채팅 |
| `True` | 방송 중, 같은 BNO | 재생 주소 갱신 → 채팅 재접속 |
| `True` | 방송 중, 다른 BNO | detail_loop 정지 → on_start → detail_loop 시작 → 채팅 |

"볼 수 없음"은 방송 중 아님, 비밀번호 방송, 알 수 없는 RESULT, 그리고 직전에 방송 중이 아니었던 상태의 연령 제한이다.

### 순서가 중요한 곳

- **detail_loop를 멈춘 뒤에 summary를 지운다.** 반대로 하면 detail_loop의 HSET이 summary를 다시 만든다.
- **detail_loop를 새로 띄우기 전에 이전 것을 멈춘다.** BNO가 바뀔 때 두 개가 동시에 돌지 않게 하려는 것이다.

### 태스크

```
watch_all (TaskGroup)
└─ watch                  스트리머마다 하나. 채팅 수신은 watch가 직접 한다 (await relay_chats)
   ├─ detail_loop         방송마다 하나. 채팅 재접속과 관계없이 방송 내내 돈다
   └─ 핑                   채팅 연결마다 하나 (chat_client 안)
```

모두 한 프로세스, 한 스레드의 이벤트 루프에서 번갈아 돈다. 대부분의 시간을 네트워크나 시간을 기다리며 보내서 서로 거의 느려지지 않는다.

### 열고 닫는 곳

| 자원 | 여는 곳 | 닫는 곳 |
|---|---|---|
| redis 연결 | `__main__._run` | `_run`의 finally |
| httpx 클라이언트 | 처음 쓸 때 `shared.get_client()` | `_run`의 finally (프로세스 공용이라 프로세스를 띄운 곳이 닫는다) |
| watch 태스크 | `watch_all` | TaskGroup이 자동으로 |
| detail_loop | `watch` | `watch`의 `_stop` |
| 웹소켓 | `stream_live_chats` | `async with` |
| 핑 | `stream_live_chats` | 그 함수의 finally |

### 설정값

| 이름 | 값 | 뜻 |
|---|---|---|
| `POLL_INTERVAL` | 60초 | 방송 중이 아닐 때 다시 확인하는 간격 |
| `RETRY_DELAY` | 20초 | 끊김이나 에러 뒤 다시 시도하기까지 |
| `DETAIL_INTERVAL` | 60초 | 방송 현황 갱신 간격 |
| `CHAT_MAXLEN` | 200 | 채팅 스트림에 남길 개수 |
| `CHAT_TTL_AFTER_END` | 600초 | 방송 종료 후 채팅 스트림을 남겨두는 시간 |
| `VIDEO_QUALITIES` | `sd`, `hd` | 재생 주소를 만드는 화질 |
| `PING_INTERVAL` | 60초 | 채팅 핑 간격 (chat_client) |
| `RECV_TIMEOUT` | 600초 | 이 시간 동안 패킷이 없으면 끊는다 (chat_client) |
| `STATE_PAIR_WINDOW` | 1초 | 0007 뒤 이 시간 안에 0007이 또 오면 짝으로 보고 연결을 유지한다 (chat_client) |
| `START_STAGGER` | 0.5초 | watch를 띄우는 간격. 요청이 한순간에 몰리지 않게 |
| `STATS_INTERVAL` | 60초 | 서버에서 나가는 SOOP 요청 수를 `[STATS]` 로그로 남기는 간격 (producer)(라이브 한정) |

---

## 10. 참고: 다시보기 영상(m3u8)

라이브와 규칙이 달라서 비교용으로 적는다.

```
마스터:  .../{파일}.smil/manifest.m3u8?rp=p02&ut=1790944014
화질별:  ../{파일}_1000k.mp4/manifest.m3u8?cv=v1&ut=...&rp=p02
조각:    seg-0.m4s?cv=v1&ut=...&rp=p02   (앞에 init.m4s를 먼저 받는다)
```

| 항목 | 라이브 | 다시보기 |
|---|---|---|
| 목록 | 계속 갱신 | `#EXT-X-PLAYLIST-TYPE:VOD`. 처음부터 끝까지 한 번에 |
| 조각 | 2초 `.TS` | 6초 `.m4s` (`#EXT-X-MAP`의 `init.m4s`가 형식 정보) |
| 토큰 | m3u8에 aid | 모든 주소에 `ut`, `rp`. `ut`는 유닉스 시간(영상이 만들어진 날짜로 보임) |
| Referer | 필요 | 필요 없음 (헤더 없이 200) |
| CORS | SOOP 사이트만 | SOOP 사이트만 (`vary: Origin`, SOOP Origin에만 허용 헤더) |
| 최대 화질 | 540p (웹) | 원본까지 (`hd` 540p 1Mbps, `hd4k` 720p 4Mbps, `hd8k` 1080p 8Mbps, `original` 1440p 16Mbps) |
| 브라우저 직접 재생 | 안 됨 | `<video src>`처럼 브라우저가 직접 재생하면 됨. hls.js 같은 자바스크립트 재생은 CORS에 막힘 |

---

## 11. 아직 모르는 것

| 질문 | 확인 방법 |
|---|---|
| 다시보기 자르기 때 BNO가 바뀌는지 | 0007을 여러 번 받았지만 BNO가 바뀐 적은 없다 |
| aid가 IP마다 다른지 | 다른 IP(핫스팟)에서 받아 비교 |
| 비밀번호 방송의 RESULT와 BPWD | 만나면 확인 |
| 방송 상태 변경(0007)의 본문 `1`, `0`의 뜻. 스트리머 본인의 채팅 접속과 관련 있을 수 있다(7절) | 웹 플레이어 개발자 도구로 0007 직후의 요청과 화면 변화 확인 / 짝이 오는 방송에서 0004가 같이 오는지 확인 / 다른 방송의 0007 본문 수집 |
| 방송 종료 뒤에도 패킷이 계속 오는 방송이 있는지. 마지막 0007이 짝이면 RECV_TIMEOUT으로 종료를 아는데, 패킷이 계속 오면 그것도 걸리지 않는다 | live_check로 `viewers_at`이 멈춘 채 남아 있는 summary가 있는지 확인 |
| 짝으로 오는 방송에서 송출 재시작으로 재생 주소가 바뀌었을 때 갱신할 계기. 연결을 유지해서 refresh_video가 일어나지 않는다 | API 설계 때 정한다 |
| refresh_video가 일시적인 400으로 두 화질 모두 실패하면 다음 재접속까지 재생 주소가 없다 | API 설계 때 재시도 여부를 정한다 |