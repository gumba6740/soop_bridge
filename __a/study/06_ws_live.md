# 학습노트 — FastAPI WebSocket 실시간 채팅 중계 (`/streams/{bid}/live`)

> SOOP 채팅 백엔드 프로젝트. 이번 세션에서 `GET /stats` 마무리 + `WS live` 완성까지.
> "받던 입장 → 주는 입장" 전환의 핵심 단계.

---

## 0. 이번 세션에서 완성한 것

- `GET /streams/{bid}/stats` — 유저 순위(ZREVRANGE) + 분당 채팅 수(MGET) 조회. **완성**
- `WS /streams/{bid}/live` — Redis Stream tail을 읽어 구독자에게 실시간 중계. **완성**
  (연결 → 중계 → parts 파싱 → 연결 종료 처리까지)

---

## 1. `GET /stats` — 집계 읽기

### 1.1 Redis 자료구조는 "타입" 기준으로 명령을 고른다
- `rank:{bid}` = **Sorted Set** → 넣을 때 `ZINCRBY`, 셀 때 `ZCARD`, 뽑을 때 `ZREVRANGE`.
  같은 상자에 서로 다른 Z 명령을 쓰는 것뿐. "ZINCRBY로 만들었으니 ZINCRBY 계열만"이 아님.
- `count:{bid}:{분}` = **String 카운터** → 넣을 때 `INCR`, 읽을 때 `GET`(하나)/`MGET`(여러 개).

### 1.2 redis는 row_factory가 없다 — 이름은 내가 붙인다
- psycopg는 응답에 컬럼명이 딸려와서 `dict_row`로 자동 매핑됨.
- redis는 값만 오고 "이게 user_id다"라는 정보가 없음. `ZREVRANGE`는 `[(member, score), ...]` 튜플만 줌.
- 그래서 pydantic 모델로 손수 매핑:
  ```python
  ranking = [UserChatRanking(user_id=member, chating_count=score)
             for member, score in count_by_user]
  ```
- `withscores=True` 응답 모양은 클라이언트/버전마다 다름(튜플 리스트 vs 평평한 리스트). `print`로 확인하는 습관.

### 1.3 MGET = Multi GET (여러 키 한 번에)
- `GET` 10번 = 왕복 10번. `MGET` = 왕복 1번에 값 리스트로.
- **없는 키는 `None`으로 옴** → "그 분엔 채팅 0"을 자연스럽게 표현.
- `MGET`은 키 이름을 내가 다 나열해줘야 함(ZREVRANGE와 다름).

### 1.4 "최근 N분 키"는 KEYS로 긁지 말고 시각으로 만든다
- **KEYS는 API에서 금지** (전체 블로킹). 확인용 redis-cli에서만.
- 지금 시각 기준으로 키를 **생성**해서 MGET:
  ```python
  now = datetime.now(timezone.utc)
  minutes = [now - timedelta(minutes=m+1) for m in range(recently_m)]
  keys = [f"count:{bid}:{dt.strftime('%Y%m%d%H%M')}" for dt in minutes]
  counts = await r.mget(keys)
  ```
- KEYS 방식은 "빈 분"을 누락시킴(시간축이 끊김). 시각 생성 방식은 빈 분도 None으로 잡아 0으로 채움 → 연속된 시간축.

### 1.5 UTC 함정 (오래 헤맴)
- 저장(`broadcast_stats`)은 `received_at`(UTC)로 키의 분을 만듦.
- 읽을 때 `datetime.now()`는 **로컬(KST)** → 9시간 어긋나 전부 None.
- **원칙: 저장과 읽기의 시간대(기준)를 반드시 일치시킨다.** → 읽기도 `datetime.now(timezone.utc)`.

### 1.6 응답 구조 설계 — 이종의 묶음은 list가 아니라 이름 붙은 객체
- 순위 + 분당수처럼 **종류가 다른 것**을 묶을 땐 `list[list[...]]`가 아니라 필드 있는 모델:
  ```python
  class GetStatsResponse(BaseModel):
      ranking: list[UserChatRanking]
      minutely: list[MinutelyChat]
  ```
- 원칙: **동종의 반복 → list. 이종의 묶음 → 이름 붙은 객체.** 확장(집계 추가)에도 강함.

### 1.7 미완성 분(진행 중 버킷) 제외
- 지금이 12:xx:30이면 현재 분 버킷은 30초치뿐 → 통계가 낮게 왜곡됨.
- `m+1`로 현재 분을 빼고 "직전 완성된 분부터" 가져오는 건 근거 있는 설계 결정.
- 원칙: **집계에는 "확정된 것"과 "진행 중인 것"이 있다. 진행 중 버킷을 확정값처럼 섞지 않는다.**

### 1.8 정보를 뭉갰다가 복원하지 말 것
- 시각을 문자열로 만들어 버리고 나중에 `strptime`으로 복원 → 지저분.
- datetime 리스트를 **버리지 않고** 들고 있다가 키도 만들고 스키마도 채움(`zip(minutes, counts)`).
- 원칙: **문자열로 뭉갰다 복원하는 코드가 보이면, 원본을 안 버리는 쪽으로 바꿀 수 있나부터 의심.**

---

## 2. `WS live` — 데이터 소스 선택 (Pub/Sub vs Stream tail)

### 2.1 왜 "다리"가 필요한가
- 컨슈머와 API는 **서로 다른 프로세스**. 컨슈머의 `buffer_rows`(파이썬 변수)는 API에서 안 보임.
- 그래서 프로세스 사이를 잇는 Redis(Pub/Sub 또는 Stream)가 필요.

### 2.2 두 방법
- **A: Pub/Sub (라디오)** — 컨슈머가 `PUBLISH`, API가 `SUBSCRIBE`.
  - Pub/Sub은 **저장 안 함**(휘발성). 켜놓은 순간부터만 들림. `xadd`(저장)와 근본이 다름.
  - `publish`는 값을 저장/덮어쓰지 않음. `live:{bid}`는 저장 키가 아니라 채널(주파수) 이름.
- **B: Stream tail (꼬리 읽기)** — API가 `soop:chats`를 `xread`로 `$`(지금 이후)부터 직접 읽기.
  - 컨슈머 안 건드림. 이미 있는 스트림 재활용.

### 2.3 xread vs xreadgroup (핵심 대비)
| | 저장 컨슈머 (`xreadgroup`) | live 중계 (`xread`) |
|---|---|---|
| 목적 | 정확한 저장 (유실 0, 중복 0) | 실시간 구경 |
| 분배 | O (워커끼리 나눠 먹음) | X (구독자 다 같은 걸 봄) |
| ack | O (처리 완료 표시) | X (그냥 흘림) |
| 과거 | 전부 (id=0부터) | 필요 없음 ($부터) |

### 2.4 `$`의 의미
- `last_id="$"` = "지금 이 순간 이후 새 것만".
- 단, 루프에서 `$` 고정이면 두 xread 사이 채팅을 놓칠 수 있음 → `last_id = entry_id`로 커서를 넘겨 이어읽기.

### 2.5 결정: B (Stream tail) 선택 — 근거는 직접 측정
- 걱정: "xread가 pub/sub보다 리소스/지연에서 비효율 아닌가?"
- **직접 벤치마크**(bench_latency.py / bench_idle_cpu.py):
  - 지연: pub/sub이 일관되게 ~15-20% 빠름. **단, 절대값은 0.1ms 차** → WS 인터넷 구간(수십 ms)에 묻힘.
  - 대기 중 CPU: 둘 다 사실상 0. `xread block`은 busy polling이 아니라 조용히 대기.
- 원칙들:
  - **"이론상 더 빠름"만으로 고르지 않는다.** ①우리 규모에서 체감되나 ②더 빠른 대신 뭘 포기하나 ③구조에 얼마나 맞나.
  - **비율이 클 때(20%) 절대값(0.1ms)도 같이 본다.**
  - "차이가 있어 보인다"와 "있다"는 다름. 여러 번 재서 **재현될 때만** 진짜로 친다.
  - 측정의 신뢰 범위: A·B **상대 비교**까지. 절대값·극한부하는 별개.
- 결론: **B로 간다. 근거 갖고 단순하게. 필요하면(T5 부하) 그때 pub/sub 재검토.**

---

## 3. WS 연결 — 삽질에서 배운 것 (원인 2개 동시)

기능이 다 맞는데 `/streams/{bid}/live`가 계속 1006으로 실패 + 서버 터미널 무음. 원인이 **두 개 동시**라 오래 걸림.

### 3.1 원인 1 (초반 오진): FastAPI 라우팅 — 사실은 무죄
- `app.routes`에 `_IncludedRouter`라는 낯선 덩어리가 보여 "버그"로 의심 → 0.115로 다운그레이드.
- **하지만 0.139로 되돌려 재검증하니 그대로 잘 됨.** `_IncludedRouter`는 0.139의 **지연평가(lazy) 정상 동작**이었음. 출력이 낯설 뿐 기능 정상.
- 교훈: **낯선 내부 객체를 봤을 때 "버그다"보다 "새 정상 동작일 수 있다"를 먼저 검증.**

### 3.2 원인 2 (진짜 범인): 브라우저 CSP
- `chrome://` 내부 페이지(새 탭, devtools 등)의 콘솔에서 WS를 열면 **CSP가 localhost 접속을 차단**.
  → 접속이 시작조차 안 됨 → 서버 무음 + 1006.
- 에러에 `Content Security Policy` / `chrome://...`가 보이면 이 신호.
- **해결: `http://` 페이지(예: `http://localhost:8000/docs`)의 콘솔에서 접속.** 같은 출처라 통과.

### 3.3 주소 두 개를 구분
- **콘솔 여는 페이지**: `http://localhost:8000/...` (주소창)
- **WS 접속**: `ws://localhost:8000/...` (`new WebSocket(...)` 안, 코드에서만)
- 주소창에 `ws://` 직접 못 침. `ws://`는 항상 코드 안에서.

### 3.4 그 외 확인한 것 (진짜 원인은 아니었지만 정상 조건)
- FastAPI WS는 `websockets`(또는 wsproto) 패키지 필요 — 둘 중 하나면 됨.
- uvicorn은 `[standard]`면 WS 백엔드 자동 연결. `--ws auto`도 가능.
- `curl`로 WS 경로 치면 404/400 — WS는 HTTP 라우팅 테이블과 별개라 정상.

### 3.5 디버깅 교훈 (제일 값진 것)
- **"안 된다"가 반복될 때 서버/코드만 의심하고 클라이언트 환경을 안 의심하면 헤맨다.**
- **서버 터미널 무음 = 요청이 서버에 도달조차 못 함 = 클라이언트 쪽에서 막힘**의 신호였는데 계속 서버를 팠음.
- 원인이 여러 개일 때: 변수를 하나씩만 바꿔가며(app vs router / 경로 모양 / 버전 / 접속 페이지) 좁힌다.

---

## 4. `WS live` 최종 구현

```python
import json
from fastapi import WebSocket, WebSocketDisconnect

@router.websocket("/streams/{bid}/live")
async def live(bid: str, ws: WebSocket):
    await ws.accept()
    r = ws.app.state.redis
    last_id = "$"                       # 구독 이후 것만
    try:
        while True:
            resp = await r.xread(
                {settings.redis_stream_key: last_id},
                block=1000,
            )
            if not resp:
                continue
            _, entries = resp[0]
            for entry_id, fields in entries:
                if fields["bid"] == bid:            # 이 방송만
                    parts = json.loads(fields["parts"])   # 문자열 → 리스트
                    message, uid, unick = parts[0], parts[1], parts[5]
                    await ws.send_text(f"[{bid}] {unick}({uid}): {message}")
                last_id = entry_id                  # if 밖! 커서는 항상 전진
    except WebSocketDisconnect:
        print(f"[{bid}] 구독자 연결 종료")
```

### 4.1 parts는 문자열이다 (리스트 아님)
- Redis 스트림엔 리스트를 못 넣어 → 컨슈머가 `json.dumps`로 문자열 저장.
- 그래서 `parts[0]`을 그냥 하면 **문자열의 첫 글자**(`[`)가 나옴.
- `json.loads(fields["parts"])`로 리스트 복원 후 인덱싱. (컨슈머 `parse_resp`가 하던 것과 동일.)

### 4.2 인덱스 매핑 (Postgres 1-based vs Python 0-based)
- schemas.sql view: `parts[2]=user_id`, `parts[6]=user_nickname` (Postgres, 1-based)
- Python: `parts[0]=message`, `parts[1]=user_id`, `parts[5]=nickname` (0-based)

### 4.3 연결 종료는 예외로 온다
- 브라우저 닫힘 → `starlette.websockets.WebSocketDisconnect(code=1006)`.
- `send`만 해도 이 예외가 남(uvicorn이 disconnect 감지).
- `while True` 전체를 `try`로 감싸 잡으면 traceback 없이 조용히 종료. 서버는 계속 살아있음.
- 원칙: **WS 핸들러의 정상 종료는 예외를 통해 일어난다.** DisConnect는 "에러"가 아니라 "종료 신호".

### 4.4 보는 페이지 (선택)
- `GET /streams/{bid}/live/view` 로 HTML 서빙 → 그 안 JS가 `ws://.../live`에 붙어 `<li>`로 표시.
- HTTP(보기)와 WS(중계) 경로를 분리: `.../live/view`(http) vs `.../live`(websocket).

---

## 5. 다음 할 일
- **에러 케이스 한꺼번에 보강** (다음 세션): `limit` 범위 검증, 방송 없음/빈 결과, DB·Redis 다운 시 동작을 `/chats`·`/stats`·`/live` 전체에 일관되게.
- 그다음 테스트 T4 (Locust/k6 부하, 인덱스·캐싱 비교) — 한 변수씩.