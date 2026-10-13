# SOOP 다시보기 채팅 타입별 data

채팅 XML에서 루트의 자식 요소 하나가 `chats` 테이블의 한 행이 된다.
요소 이름은 `type` 컬럼에, 시간 태그 `t`는 `time_offset` 컬럼에 들어가고, 나머지는 타입별 규칙으로 골라 `data`(jsonb)에 담는다.

- 변환 코드: `app/broadcast/review/shared/chat_parser.py`. 기존 데이터를 옮긴 마이그레이션(alembic `261012_0030`)과 규칙이 같다. 한쪽을 바꾸면 다른 쪽과 결과가 달라진다.
- 값을 만드는 규칙(배지, 구독, 아이디 접미사, 숫자 변환)은 `app/broadcast/chat_rules.py`에 있다. 라이브 채팅 변환도 같은 파일을 쓴다.
- 라이브 채팅도 `chat`, `ogq`, `balloon`, `follow`는 이 문서와 같은 `type`, 같은 data 키로 올린다. 라이브 패킷의 몇 번 칸이 어느 키가 되는지는 `live_chat_packets.md` 8절.
- 각 타입의 **원본 태그** 줄은 XML 자식의 실제 순서다(`t` 제외). 마이그레이션 전의 `parts` 배열이 이 순서였다(`parts[1]`이 첫 태그).
- `?`는 의미를 아직 확인하지 못한 값이다.
- 채팅 XML 버전 `1.0.0` 기준. 원본 XML 표본은 `.local/scripts/xml_samples.py`로 받는다.
- 예시의 유저 ID, 닉네임, 채팅 메시지, 미션 제목은 가짜 값이다.

---

## 1. 공통 규칙

| 규칙 | 내용 |
|---|---|
| 키 이름 | 아는 타입은 읽기 쉬운 이름. 뜻이 확정되지 않은 값은 원본 태그 이름 그대로(`img_url`, `def_url`) |
| 보낸 유저 | `user_id`, `user_nick` |
| 받는 쪽이 스트리머 | `bid` (원본 `bj`). 받는 사람 키는 따로 두지 않는다 |
| 시청자끼리 선물 | `from_user_id`, `from_user_nick`, `to_user_id`, `to_user_nick` |
| 받는 사람이 정해지지 않은 선물 | `from_user_id`, `from_user_nick`만 |
| 유저 ID | 뒤의 `(2)` 같은 중복 접속 접미사를 떼고 저장 |
| 숫자 | 개수, 개월, 코드 등은 숫자로 저장. 숫자가 아니면 null |
| 메시지 | 하나면 `message`, 둘 이상이면 화면 순서대로 `message1`, `message2`, … |
| URL | 완성된 URL이 칸에 있을 때만 저장. 원래 값 그대로(`//`로 시작하는 것도 그대로) |
| 세부 타입 | `type` 컬럼은 그대로 두고 `data.kind`에 원본 값 그대로(`CHALLENGE_GIFT` 등) |
| 태그가 없을 때 | 키 값이 null. 태그는 있는데 비어 있으면 `""` |

### 모르는 타입

`chat_parser`의 `CONVERTERS`, `KIND_CONVERTERS`에 없는 타입(세부 타입 포함)은 **원본 태그 이름을 키로, `t`까지 전부** 문자열로 담는다. 로그에 `미등록 채팅 타입 발견`이 남는다.

```json
{"value": "1", "t": "4796.500"}
```

아는 타입의 data에는 `t`가 들어가지 않으므로, **data에 `t`가 있으면 모르는 타입**이다. API는 이걸로 걸러서 모르는 타입을 내보내지 않는다(유저 ID가 어느 태그에 있는지 모르므로).

### API로 나가는 것

| API | 한 건의 모양 |
|---|---|
| 다시보기 `GET /review/{vod_id}/chats?start=` | `{type, t, data}`. `t`는 VOD 전체 기준 초 |
| 라이브 `GET /live/{bid}/chats` (SSE) | `{type, data}` |

두 쪽 모두 data에서 `…user_id`로 끝나는 키(`user_id`, `from_user_id`, `to_user_id`)는 뺀다(`chat_rules.public`). 닉네임은 내보낸다.

### 저장하지 않는 칸

`t`를 빼고는 아래 각 타입의 표에 없는 태그는 저장하지 않는다. 다시 봐야 하면 `xml_samples.py`로 원본을 받는다.
자주 나오는 것: `ch`/`chno`(채팅방 번호), `lng`(언어 코드), `mof`(리소스 버전. 이미지 주소의 `?t=`와 같은 값), `fn`(연출 파일 이름).

---

## 2. 배지와 구독

`chat`, `ogq`에 들어간다. `p`(플래그1), `p2`(플래그2)에서 판정한다. 비트 표는 8절.

### badge

화면에는 배지가 하나만 보이므로 위에 있는 것이 이긴다.

| 순위 | 값 | p 비트 |
|---:|---|---|
| 1 | `streamer` | 2 (방송 주인) |
| 2 | `manager` | 6 또는 8 |
| 3 | `vip` | 15 (열혈) |
| 4 | `fan` | 5 |
| 5 | `supporter` | 20 |
| 6 | `none` | 위 중 아무것도 없음. `p`가 없어도 `none` |

- 매니저와 열혈 중 무엇이 이기는지는 확인하지 못했다(둘 다 가진 사람을 본 적이 없다).
- 서포터는 다른 배지에 가려진다. 서포터이면서 팬이면 `fan`.

### subscriber

```json
{"tier": "basic", "months": 15}
{"tier": "none",  "months": 0}
```

| 키 | 출처 | 값 |
|---|---|---|
| `tier` | p2 19번 → `plus`, 18번 → `basic`, 둘 다 없음 → `none` | `none` / `basic` / `plus` |
| `months` | `fw` (follow는 `period`) | 구독 개월. `tier`가 `none`이거나 값이 없으면 0. 화면에 보이는 개월과 다를 수 있다(10절) |

- 플래그1 28번(구독 여부)은 쓰지 않는다. 등급은 p2만 본다.
- `p2`, `fw`가 없는 옛 형식(9절)은 `{"tier": "none", "months": 0}`이 된다.

---

## 3. 채팅

### chat

원본 태그: `m u c mt l n p p2 fw nf nd acfw pn tlk`

| data 키 | 원본 태그 | 형식 | 예시 | 설명 |
|---|---|---|---|---|
| `message` | `m` | 문자열 | `안녕하세요` | 메시지 |
| `user_id` | `u` | 문자열 | `viewer01` | 유저 ID |
| `user_nick` | `n` | 문자열 | `시청자01` | 닉네임 |
| `badge` | `p` | 문자열 | `fan` | 2절 |
| `subscriber` | `p2`, `fw` | 객체 | `{"tier": "plus", "months": 25}` | 2절 |

저장하지 않음: `c`(?), `mt`(메시지 타입?), `l`(채팅 언어로 보임. 3 한국어, -1 판별 불가), `nf`, `nd`(닉네임 색 다크/라이트), `acfw`(누적 구독 개월), `pn`, `tlk`(?)

### ogq — OGQ 이모티콘

원본 태그: `ch m gid sid v s sn sf c l mt e fw nf nd acfw pn anm tlk`

| data 키 | 원본 태그 | 형식 | 예시 | 설명 |
|---|---|---|---|---|
| `message` | `m` | 문자열 | `""` | 이모티콘과 같이 보낸 글. 비어 있을 때가 많다 |
| `user_id` | `s` | 문자열 | `viewer01` | 보낸 유저 ID |
| `user_nick` | `sn` | 문자열 | `시청자01` | 보낸 유저 닉네임 |
| `badge` | `sf` 앞부분 | 문자열 | `fan` | `sf`는 `p\|p2` |
| `subscriber` | `sf` 뒷부분, `fw` | 객체 | `{"tier": "none", "months": 0}` | |
| `group_id` | `gid` | 문자열 | `64c91cc7de488` | 이모티콘 묶음 ID |
| `sticker_id` | `sid` | 숫자 | `7` | 묶음 안의 번호. `group_id`와 같이 봐야 하나로 정해진다 |

저장하지 않음: `ch`, `v`(?), `c`, `l`, `mt`, `e`(이미지 확장자), `nf`, `nd`, `acfw`, `pn`, `anm`(애니메이션 여부?), `tlk`

---

## 4. 후원 (받는 쪽이 스트리머)

### balloon — 별풍선

원본 태그: `bj u n c fs tfn fc fn def lng mof`

| data 키 | 원본 태그 | 형식 | 예시 |
|---|---|---|---|
| `bid` | `bj` | 문자열 | `streamer01` |
| `user_id` | `u` | 문자열 | `viewer01` |
| `user_nick` | `n` | 문자열 | `시청자01` |
| `count` | `c` | 숫자 | `10` |

저장하지 않음: `fs`, `tfn`, `fc`(?), `fn`(연출 파일 이름. 개수 그대로면 SOOP 기본 효과, `{bid}_{개수}`면 스트리머 전용 효과, 비어 있으면 연출 없음), `def`(`fn`이 비어 있을 때 1. 기본 연출로 보임), `lng`, `mof`

### adballoon — 애드벌룬

원본 태그: `bj u n c m em title img_url def_url fs tfn lng mof`

| data 키 | 원본 태그 | 형식 | 예시 |
|---|---|---|---|
| `bid` | `bj` | 문자열 | `streamer01` |
| `user_id` | `u` | 문자열 | `viewer01` |
| `user_nick` | `n` | 문자열 | `시청자01` |
| `count` | `c` | 숫자 | `5` |
| `message1` | `m` | 문자열 | `애드벌룬 5개 선물!` |
| `message2` | `em` | 문자열 | `애드벌룬 5개를 선물했습니다!` |
| `img_url` | `img_url` | 문자열 | `https://res.sooplive.com/new_player/items/img_adballoon.png` |
| `def_url` | `def_url` | 문자열 | `https://res.sooplive.com/new_player/items/adballoon/ceremony/pc_5.png?t=1774297996` |

저장하지 않음: `title`(늘 비어 있음), `fs`, `tfn`(?), `lng`, `mof`

### vballoon — 영상풍선

원본 태그: `bj u n c tn st d sub fs tfn`

| data 키 | 원본 태그 | 형식 | 예시 |
|---|---|---|---|
| `bid` | `bj` | 문자열 | `streamer01` |
| `user_id` | `u` | 문자열 | `viewer01` |
| `user_nick` | `n` | 문자열 | `시청자01` |
| `count` | `c` | 숫자 | `15` |

저장하지 않음: `tn`(재생할 영상 번호? 0인 때가 많다), `st`(영상 시작 시점?), `d`(재생 길이? 대부분 개수와 같다), `sub`, `fs`, `tfn`

### vod_balloon — VOD 별풍선

예전에 다시보기 영상에 별풍선을 보낸 사람이 라이브에 들어왔을 때 뜨는 안내로 보인다. 화면: "○○ / VOD 별풍선 N개".

원본 태그: `bj u n c fn def ch lng mof`

| data 키 | 원본 태그 | 형식 | 예시 |
|---|---|---|---|
| `bid` | `bj` | 문자열 | `streamer01` |
| `user_id` | `u` | 문자열 | `viewer01` |
| `user_nick` | `n` | 문자열 | `시청자01` |
| `count` | `c` | 숫자 | `1` |

저장하지 않음: `fn`, `def`(balloon과 같은 연출 값), `ch`, `lng`, `mof`

### stationadcon, vodadcon — 방송국 / VOD 애드벌룬

방송국 페이지나 VOD에서 애드벌룬을 선물한 사람이 라이브에 들어왔을 때 뜨는 안내로 보인다. 두 타입의 구성이 같다.

원본 태그: `bj v vn cnt default msg chno lng mof`

| data 키 | 원본 태그 | 형식 | 예시 |
|---|---|---|---|
| `bid` | `bj` | 문자열 | `streamer01` |
| `user_id` | `v` | 문자열 | `viewer01` |
| `user_nick` | `vn` | 문자열 | `시청자01` |
| `count` | `cnt` | 숫자 | `22` |
| `message` | `msg` | 문자열 | `방송국에서 시청자01님이 애드벌룬 22개를 선물 하셨습니다.` |
| `def_url` | `default` | 문자열 | `//res.sooplive.com/new_player/items/adballoon/ceremony/pc_22.png?t=1774297997` |

- `vodadcon`은 `v`, `vn`이 빈 같은 알림이 1초 전에 하나 더 오는 경우가 있다. 빈 문자열 그대로 저장한다.
- 연출이 따로 없는 개수는 `def_url`이 `pc_0.png`(기본 그림)다.

### fanletter — 스티커

화면: "○○ / 스티커 N개". 바로 뒤에 같은 초로 `supporter`가 오기도 한다.

원본 태그: `bj bjnick u n c image lettertype lettermessage lng`

| data 키 | 원본 태그 | 형식 | 예시 |
|---|---|---|---|
| `bid` | `bj` | 문자열 | `streamer01` |
| `user_id` | `u` | 문자열 | `viewer01` |
| `user_nick` | `n` | 문자열 | `시청자01` |
| `count` | `c` | 숫자 | `1` |
| `message` | `lettermessage` | 문자열 | `""` |

저장하지 않음: `bjnick`, `image`, `lettertype`(`702` 등), `lng`

---

## 5. 미션

### challenge_mission — 도전미션

`<type>`(세부 타입)에 따라 구성이 다르다. 같은 미션은 `mission_key`로 묶이지만, `CHALLENGE_NOTICE`는 키가 늘 0이라 같은 초의 `CHALLENGE_SETTLE`과 제목으로 맞춰야 한다.

**CHALLENGE_GIFT — 미션 등록**

원본 태그: `type ch subch key title fn c u n bj bj_nick`

| data 키 | 원본 태그 | 형식 | 예시 |
|---|---|---|---|
| `kind` | `type` | 문자열 | `CHALLENGE_GIFT` |
| `bid` | `bj` | 문자열 | `streamer01` |
| `bj_nick` | `bj_nick` | 문자열 | `스트리머` |
| `user_id` | `u` | 문자열 | `viewer01` |
| `user_nick` | `n` | 문자열 | `시청자01` |
| `count` | `c` | 숫자 | `100` |
| `mission_key` | `key` | 숫자 | `1715909` |
| `title` | `title` | 문자열 | `미션 제목 예시` |

**CHALLENGE_SETTLE — 정산** (화면: "별풍선 N개 획득!")

원본 태그: `type subch key title fn c bj bj_nick`

| data 키 | 원본 태그 | 형식 | 예시 |
|---|---|---|---|
| `kind` | `type` | 문자열 | `CHALLENGE_SETTLE` |
| `bid` | `bj` | 문자열 | `streamer01` |
| `bj_nick` | `bj_nick` | 문자열 | `스트리머` |
| `count` | `c` | 숫자 | `300` |
| `mission_key` | `key` | 숫자 | `1715297` |
| `title` | `title` | 문자열 | `미션 제목 예시` |

**CHALLENGE_NOTICE — 결과** (화면: "[제목] 미션을 성공하였습니다!")

원본 태그: `type title ms key`

| data 키 | 원본 태그 | 형식 | 예시 |
|---|---|---|---|
| `kind` | `type` | 문자열 | `CHALLENGE_NOTICE` |
| `title` | `title` | 문자열 | `미션 제목 예시` |
| `result` | `ms` | 문자열 | `SUCCESS` (`FAIL`도 있다고 라이브 문서에 있음) |

저장하지 않음: `ch`, `subch`, `fn`, NOTICE의 `key`(늘 0)

### challenge_mission_settle — 도전미션 정산 명세

화면에는 보이지 않는다. `CHALLENGE_SETTLE`과 같은 초에 온다. 라이브 0125는 기여한 사람 목록이 한 패킷에 오지만, 다시보기에는 한 요소에 한 사람만 있었다(7명이 건 미션에서 마지막 1명만 나온 사례). 겹친 태그가 없으므로 파서가 놓친 것은 아니다.

원본 태그: `ch fno u n c nfn`

| data 키 | 원본 태그 | 형식 | 예시 |
|---|---|---|---|
| `user_id` | `u` | 문자열 | `viewer01` |
| `user_nick` | `n` | 문자열 | `시청자01` |
| `count` | `c` | 숫자 | `500` |

저장하지 않음: `ch`, `fno`, `nfn`(?)

### battle_mission — 대결미션

`<type>`에 따라 구성이 다르다. 같은 미션은 `mission_key`로 묶인다.

**GIFT — 후원**

원본 태그: `type ch bj bj_nick u n c subch fn key title top_fan fan_order`

| data 키 | 원본 태그 | 형식 | 예시 |
|---|---|---|---|
| `kind` | `type` | 문자열 | `GIFT` |
| `bid` | `bj` | 문자열 | `streamer01` (후원을 받은 쪽) |
| `bj_nick` | `bj_nick` | 문자열 | `스트리머` |
| `user_id` | `u` | 문자열 | `viewer01` |
| `user_nick` | `n` | 문자열 | `시청자01` |
| `count` | `c` | 숫자 | `10` |
| `mission_key` | `key` | 숫자 | `72501` |
| `title` | `title` | 문자열 | `미션 제목 예시` |

**NOTICE — 결과**

원본 태그: `type draw winner loser fn key title`

| data 키 | 원본 태그 | 형식 | 예시 |
|---|---|---|---|
| `kind` | `type` | 문자열 | `NOTICE` |
| `winner` | `winner` | 문자열 | `A팀` |
| `loser` | `loser` | 문자열 | `B팀` |
| `draw` | `draw` | 참/거짓 | `false` (1이면 true. 무승부는 아직 못 봄) |
| `mission_key` | `key` | 숫자 | `72501` |
| `title` | `title` | 문자열 | `미션 제목 예시` |

**SETTLE — 정산**

원본 태그: `type bj bj_nick c fn key title`

| data 키 | 원본 태그 | 형식 | 예시 |
|---|---|---|---|
| `kind` | `type` | 문자열 | `SETTLE` |
| `bid` | `bj` | 문자열 | `streamer01` |
| `bj_nick` | `bj_nick` | 문자열 | `스트리머` |
| `count` | `c` | 숫자 | `6700` |
| `mission_key` | `key` | 숫자 | `72501` |
| `title` | `title` | 문자열 | `미션 제목 예시` |

저장하지 않음: `ch`, `subch`, `fn`, GIFT의 `top_fan`, `fan_order`

---

## 6. 구독, 퀵뷰, 선물

### follow — 직접 구독 알림

화면: "베이직 ○○ 구독하였습니다." / "플러스 ○○ 28개월째 구독 중!"

원본 태그: `bj u n type period acperiod stier stype lng mof`
선물 받은 구독권으로 시작한 구독은 `type`이 111(베이직) 또는 211(플러스)이고 `stype`이 없다(9개). 쓰는 태그의 순서는 같다.

| data 키 | 원본 태그 | 형식 | 예시 |
|---|---|---|---|
| `bid` | `bj` | 문자열 | `streamer01` |
| `user_id` | `u` | 문자열 | `viewer01` |
| `user_nick` | `n` | 문자열 | `시청자01` |
| `subscriber` | `stier`, `period` | 객체 | `{"tier": "plus", "months": 26}` |

- `stier` 1 → `basic`, 2 → `plus`. `period`는 연속 구독 개월(화면의 "N개월째").
- 저장하지 않음: `type`(-1 = 직접 구독, 111/211 = 선물 받은 구독권), `acperiod`(누적 개월), `stype`(100, 101, 103, 106, 200, 201, 9100 …. 뜻 모름), `lng`, `mof`

### subscription — 지정 구독권 선물

화면: "To. ○○ / 베이직 / 1개월 구독 선물권 선물 / from. ○○". 몇 초~1분 뒤 받은 사람의 `follow`(`type` 111)가 온다.

원본 태그: `ch s sn r rn b bn it ic is st sp sr`

| data 키 | 원본 태그 | 형식 | 예시 |
|---|---|---|---|
| `from_user_id` | `s` | 문자열 | `viewer01` |
| `from_user_nick` | `sn` | 문자열 | `시청자01` |
| `to_user_id` | `r` | 문자열 | `viewer02` |
| `to_user_nick` | `rn` | 문자열 | `시청자02` |
| `item_code` | `it` | 숫자 | `11` (아래 구독권 상품 코드) |

저장하지 않음: `ch`, `b`, `bn`(스트리머), `ic`(주문 번호), `is`, `st`, `sp`, `sr`

### sub_random — 랜덤 구독권 선물

화면: "○○님이 / 베이직 구독 선물권(30일) / N개 선물". 받는 사람은 나오지 않는다. 2026-09 무렵 생긴 기능.

원본 태그: `sender sendernick chno cnt itemcode rank`

| data 키 | 원본 태그 | 형식 | 예시 |
|---|---|---|---|
| `from_user_id` | `sender` | 문자열 | `viewer01` |
| `from_user_nick` | `sendernick` | 문자열 | `시청자01` |
| `count` | `cnt` | 숫자 | `5` |
| `item_code` | `itemcode` | 숫자 | `11` |

저장하지 않음: `chno`, `rank`(대부분 -1, 가끔 숫자. 뜻 모름)

**구독권 상품 코드** (화면과 맞춰 봄. 1과 21은 한 건씩만 봤다)

| `item_code` | 상품 |
|---:|---|
| 1, 11 | 베이직 구독 선물권 30일 (둘의 차이는 모름) |
| 20, 21 | 플러스 구독 선물권 30일 (둘의 차이는 모름. 21은 채팅에 안 뜨는 것으로 보임) |

### quickview — 지정 퀵뷰 선물

화면: "To. ○○ / 퀵뷰 플러스 7일 이용권 선물 / from. ○○". 대부분 스트리머가 보내지만 시청자도 보낼 수 있다.

원본 태그: `sender sendernick u n quickviewtype`

| data 키 | 원본 태그 | 형식 | 예시 |
|---|---|---|---|
| `from_user_id` | `sender` | 문자열 | `streamer01` |
| `from_user_nick` | `sendernick` | 문자열 | `스트리머` |
| `to_user_id` | `u` | 문자열 | `viewer01` |
| `to_user_nick` | `n` | 문자열 | `시청자01` |
| `item_code` | `quickviewtype` | 숫자 | `100` |

### quick_random — 랜덤 퀵뷰 선물

화면: "○○님이 / 퀵뷰 플러스 30일권 / N개 선물". 받는 사람은 나오지 않는다. 시청자도 보낼 수 있다.

원본 태그: `sender sendernick chno cnt itemcode`

| data 키 | 원본 태그 | 형식 | 예시 |
|---|---|---|---|
| `from_user_id` | `sender` | 문자열 | `viewer01` |
| `from_user_nick` | `sendernick` | 문자열 | `시청자01` |
| `count` | `cnt` | 숫자 | `2` |
| `item_code` | `itemcode` | 숫자 | `101` |

**퀵뷰 상품 코드**

| `item_code` | 상품 |
|---:|---|
| 100 | 퀵뷰 플러스 7일권 |
| 101 | 퀵뷰 플러스 30일권 |
| 102 | 퀵뷰 플러스 90일권 |

### sendogq — 이모티콘 선물

화면: "To. ○○ / ○○티콘 이모티콘 선물 / from. ○○"

원본 태그: `sendid sendnick recvid recvnick title url ch`

| data 키 | 원본 태그 | 형식 | 예시 |
|---|---|---|---|
| `from_user_id` | `sendid` | 문자열 | `viewer01` |
| `from_user_nick` | `sendnick` | 문자열 | `시청자01` |
| `to_user_id` | `recvid` | 문자열 | `viewer02` |
| `to_user_nick` | `recvnick` | 문자열 | `시청자02` |
| `item_name` | `title` | 문자열 | `○○티콘` |
| `img_url` | `url` | 문자열 | `//ogq-sticker-global-cdn-z01.sooplive.com/sticker/6316b437b19b7/main.png` |

---

## 7. 알림

| 타입 | 원본 태그 | data | 화면 |
|---|---|---|---|
| `fanclub` | `u n` | `user_id`, `user_nick` | 팬클럽 가입 |
| `supporter` | `u n` | `user_id`, `user_nick` | ○○님이 서포터가 되셨습니다. (`fanletter` 바로 뒤) |
| `topfan` | `u n` | `user_id`, `user_nick` | ○○님이 열혈팬이 되셨습니다. (별풍선 직후) |
| `adm_notice` | `type m` | `message` (`m`) | SOOP 안내. "○○님의 방송이 별별랭킹의 '…' 1위에 등극!" `type`은 늘 0 |
| `bj_notice` | `ch state type m` | `message` (`m`) | 채팅 규칙 공지. 채팅 줄이 아니라 채팅창 위에 고정된다. 공지가 바뀔 때마다 온다 |
| `ice` | `value` | `frozen` (`value` 1 → true) | 채팅 얼리기. 1 얼림, 0 해제. 얼림 대상을 바꾸면 1이 다시 온다 |
| `adinbroad` | `status chno [?]` | `active` (`status` 1 → true) | 쉬는시간(중간광고). 1 → "쉬는시간이 설정되었습니다.", 0 → "쉬는시간이 종료되었습니다." + "쉬는시간 중 약 N회 이상 중간광고 재생으로 후원!". 종료 때만 붙는 세 번째 태그가 N(광고 재생 횟수). 화면 문구는 플레이어가 만든다 |

---

## 8. p, p2 비트 플래그

`chat`의 `p`, `p2`와 `ogq`의 `sf`(`"p|p2"`)는 숫자 하나가 아니라 **예/아니오 스위치 32개를 묶은 값**이다.
2진수로 바꿨을 때 각 비트가 켜져 있는지로 상태를 판단한다. 채팅을 친 **그 시점**의 상태다. 라이브 패킷의 플래그와 같은 값이다(`live_chat_packets.md` 1절).

| 필드 | 비트 | 10진 값 | 뜻 | 확신도 |
|---|---:|---:|---|---|
| p | 2 | 4 | 방송 주인 | 확인 (방송 주인의 채팅 2583건 모두 있음, 다른 사람 0건) |
| p | 5 | 32 | 팬 | 확인 (팬 아닌 사람(건빵)을 화면으로 확인) |
| p | 6, 8 | 64, 256 | 매니저. 둘 다 켜져 있고 어느 쪽인지는 모름 | 확인 (다시보기 2명, 같은 방송 시청자 140명 중 0명, 라이브 3명) |
| p | 15 | 32768 | 열혈팬 | 확인 |
| p | 16 | 65536 | 거의 모든 유저. 기본값 | |
| p | 20 | 1048576 | 서포터. 방송마다 따로, 팬과 별개 | 확인 (서포터가 된 순간부터 켜지고 다른 방송에서는 꺼져 있음, 2명) |
| p | 28 | 268435456 | 구독 중. 구독해도 5번(팬)은 자동으로 켜지지 않는다 | 확인 |
| p | 14, 19, 29 | | 접속 환경으로 추정. 같은 사람도 `(2)` 세션마다 다르다 | 추정 |
| p | 9 | 512 | 무시한다. 스트리머 계정 일부와 겹치지만 그것만으로 설명되지 않는다 | |
| p | 31 | 2147483648 | ? | 미확인 |
| p2 | 15 | 32768 | 거의 모든 유저 | |
| p2 | 17 | 131072 | 접속 환경으로 추정 (p 14번과 엇갈림) | 추정 |
| p2 | 18 | 262144 | 구독 베이직 | 확인 |
| p2 | 19 | 524288 | 구독 플러스 | 확인 |
| p2 | 25 | 33554432 | ? | 미확인 |

- 방송 주인이 자기 채널을 구독한 상태(28번, 구독 개월 1~2)로 나오는 경우가 있다.
- 31번 비트가 있어서 `integer`로 바꾸면 넘칠 수 있다. SQL에서는 `bigint`로 바꿔서 비교한다.
- 비트 원본은 저장하지 않으므로, 판정 규칙을 바꾸면 과거 데이터는 다시 수집해야 한다.

---

## 9. XML 모양이 다른 경우

- **follow의 선물 받은 구독**: `type` 111/211이고 `stype`이 없다(6절).
- **옛날 VOD**(2017년 lshooooo 방송에서 확인): `chat`이 `p n u m c`뿐이다. 순서도 다르고 `p2`, `fw`가 없다. 파서는 태그 이름으로 읽으므로 메시지, 유저, 배지는 맞게 들어가고, 구독은 `none`이 된다. 크론은 2026-09-01 이후 VOD만 수집하지만, 온디맨드 워커는 옛날 VOD도 수집한다.

---

## 10. 할 일

- **남은 `?` 필드.** `vballoon`의 `tn`, `st`, `d`, `follow`의 `stype`, `sub_random`의 `rank`, 플래그 31번과 p2 25번.
- **매니저와 열혈의 우선순위.** 둘 다 가진 사람의 화면 배지 확인.
- **구독권 상품 코드 1, 21.** 한 건씩만 봤다. 11, 20과의 차이 확인.
- **battle_mission 무승부.** `draw`가 1인 사례 확인.
- **구독 개월 `fw`와 화면.** `months`는 `fw`를 쓰는데, 화면에 보이는 구독 개월은 `acfw` 자리의 값과 같았다(라이브 0005의 10번 칸으로 확인, `live_chat_packets.md` 2절). 두 값은 대부분 같지만 다른 사람은 화면과 다르게 나온다. 화면과 맞추려면 `acfw`로 바꾸고 다시보기 데이터를 다시 변환해야 한다.