# 학습노트 — FastAPI 서버 골격 + 컨슈머 집계 (INCR/ZINCRBY)

> SOOP 채팅 백엔드. 이번 세션에서 `GET /chats`를 완성하고, `/stats`를 위한
> 컨슈머 집계 로직(INCR/ZINCRBY)을 얹었다. 아래는 그 과정에서 씹은 개념들.

---

## 1. FastAPI 프로세스 구조

- **프로듀서 / 컨슈머 / FastAPI는 서로 다른 OS 프로세스다.** 한 프로그램 안에서
  다 도는 게 아니라, 각각 `python producer.py`, `python consumer.py`,
  `uvicorn app.main:app`으로 따로 뜬다.
- 셋은 서로 import 하지 않는다. 오직 **Redis(스트림)와 Postgres(테이블)**를 통해
  간접적으로만 이어진다. 이것이 설계 원칙 "수집과 처리의 분리".
- FastAPI는 이 그림에서 "이미 Postgres에 쌓인 데이터를 읽어 클라이언트에
  내보내는 네 번째 참여자"일 뿐. 프로듀서가 죽어 있어도 `GET /chats`는 동작한다.

---

## 2. "채팅을 보내준다"는 두 갈래 — 소스가 다르다

| 엔드포인트 | 성격 | 데이터 소스 |
|---|---|---|
| `GET /chats` | 지난 채팅 조회 | **Postgres** (`chats_view`) |
| `GET /stats` | 실시간 집계(분당 수·순위) | **Redis** (카운터/sorted set) |
| `WS /live` | 실시간 채팅 밀어주기 | 스트리밍 (미결정) |

- **Redis 스트림을 "최근 채팅 조회소"로 쓰면 안 되는 이유:** ① 스트림은 RAM이라
  오래된 걸 trim해서 과거가 사라짐 ② 스트림은 큐지 조회 엔진이 아님(유저별·시간
  역순 등 질의 불가) ③ 원본은 Postgres에 구조화되어 있음.
- 비유: Redis 스트림 = **컨베이어 벨트**(지나가라고 있는 것), Postgres = **선반**
  (쌓아두고 꺼내 보는 것). "지난주에 뭐 들어왔어?"는 벨트가 아니라 선반을 본다.

---

## 3. lifespan + 커넥션 풀

### 왜 풀인가
- 컨슈머는 연결 **1개**로 충분했다(루프가 배치를 순차 처리하니까).
- 웹서버는 요청이 **동시에** 여러 개 온다. 연결 1개를 공유하면 줄을 서고(직렬화),
  psycopg 연결 하나를 여러 코루틴이 동시에 만지는 것도 안전하지 않다.
- 그래서 **풀**: 연결 여러 개를 미리 열어 통에 담아두고 빌려주고 반납받는다.
  개수는 `min_size`(바닥, 항상 유지) ~ `max_size`(천장) 사이에서 트래픽 따라
  **유동적**으로 늘었다 줄었다 한다. 안 지정하면 기본값.
- **풀 ≠ ORM, 풀 ≠ 스레드.** 풀은 "연결(통로)들을 담아둔 재사용 보관함"일 뿐.
  psycopg는 연결 하나만 만들 줄 알아서, 풀이 필요하면 `psycopg_pool`을 따로 쓴다.
  (redis는 클라이언트가 풀을 내장 → 우리가 안 만듦. 그래서 비대칭.)

### 왜 lifespan인가
- 풀을 요청 처리 **시작 전 한 번** 열고 **종료 시** 닫아야 하는데, 서버는
  이벤트 루프를 무한히 붙잡아서 `main()`을 `async with`로 통째 감쌀 수 없다.
- FastAPI의 lifespan 훅이 그 자리를 대신한다: `yield` 앞 = startup, 뒤 = shutdown.
- **이벤트 루프는 uvicorn이 켠다.** lifespan은 그 루프 위에서 돌기 때문에 그
  안에서는 `await`을 쓸 수 있다.

### open=False 가 필요한 이유 (핵심)
- `AsyncConnectionPool(...)`을 그냥 만들면 "생성자에서 여는 것은 deprecated"
  경고가 뜬다.
- 이유는 **루프 유무가 아니라** `__init__`이 `def`(async 아님)라서 `await`을
  못 쓰는데, 풀 여는 작업엔 `await`이 필요하기 때문. "await 필요한 일을 await
  못 쓰는 자리에서" 하려니 꼼수가 되고, 그게 신뢰 불가라 경고.
- 해결: `open=False`로 생성자에선 안 열고, **async인 자리**에서 연다.
  - 방법 A: `await pool.open()` (lifespan 본문 = async)
  - 방법 B: `async with AsyncConnectionPool(..., open=False) as pool:`
    → `__aenter__`(async def)가 대신 열어줌
- `open`은 `bool | None`이고 기본값은 `None`. 생략(None)도 "생성자에서 안 엶"으로
  동작해 경고가 없다. `open=False`는 의도를 코드에 드러내려고 명시하는 것(권장).
- **deprecated = "되지만 곧 안 됨".** open=True도 지금은 동작하지만(경고일 뿐),
  미래에 에러가 되고 지금도 미묘하게 불안정. "된다 ≠ 써도 된다."

### 최종 형태
```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    async with (
        AsyncConnectionPool(settings.postgres_url, open=False) as pool,
        redis.from_url(settings.redis_url, decode_responses=True) as r,
    ):
        app.state.pool = pool
        app.state.redis = r
        yield
```
- 여러 자원은 **한 `async with`에 콤마로 나란히**(consumer.py 패턴과 동일).
- 각각 `app.state`에 **따로** 담는다(묶어서 넘기는 게 아님).

---

## 4. yield / app.state / 순환참조

- **lifespan의 `yield`는 값 전달이 아니라 "경계"다.** 위=startup, 아래=shutdown.
  `yield`가 함수를 그 자리에 **멈춰 세워서** `async with` 블록이 끝나지 못하게
  붙잡고, 그 덕에 풀이 열린 채 유지된다. 종료 신호가 오면 재개→블록 벗어남→닫힘.
  - 인과 체인: `yield 멈춤` → `async with 안 끝남` → `__aexit__ 안 불림` → `풀 유지`
- **전달은 `yield`가 아니라 `app.state`가 한다.** 풀을 `app.state.pool`에 담아두고,
  라우터가 나중에 꺼내 쓴다. (냉장고에 넣어두고 꺼내 먹는 구조.)
- **`app.state`** = 서버 내내 살아있는 `app` 객체에 딸린, 아무거나 담는 공용
  보관함(Starlette에서 상속). "애플리케이션 전체에서 동일한 하나의 객체, 요청마다
  안 바뀜". 공식 문서 주석: state 직접 접근보다 **dependency 권장**.
- **순환참조 아님.** lifespan은 `app`을 파일 상단에서 import 하지 않고 **인자로
  받는다**. 함수 정의 시점엔 `app`이 없어도 되고(정의는 레시피일 뿐), 실제 호출
  시점(startup)에 FastAPI가 자기 자신을 넘겨준다. `FastAPI(lifespan=lifespan)`은
  함수를 "건네줄" 뿐 실행하지 않는다.

---

## 5. 라우터에서 풀 꺼내기 + 파라미터

- `app`을 import 하지 않는다(순환참조 회피). 대신 **`request` 객체**를 통해:
  ```python
  async def read_chats(bid: str, request: Request):
      pool = request.app.state.pool
  ```
  `request.app` = 그 하나뿐인 앱. `request: Request`는 타입 힌트만 선언하면
  FastAPI가 실제 요청 객체를 자동 주입.
- **path vs query 파라미터를 가르는 것은 "함수에서의 위치"가 아니라 "이름이
  경로(`@router.get`)에 있느냐".**
  - 경로에 `{bid}` 있음 → path 파라미터
  - 경로에 없음(`limit`) → query 파라미터 (`?limit=100`)
  - `limit: int = 100`을 **뒤에** 두는 건 파이썬 문법(기본값 있는 인자는 뒤)
    때문이지, 위치가 쿼리 여부를 정하는 게 아니다.

---

## 6. SQL 파라미터화 (보안 핵심)

- **`.format()`으로 값을 SQL에 직접 꽂으면 SQL 인젝션.** 절대 금지.
- **항상 `execute(SQL, (값,))`으로 값을 분리**해서 넘긴다(consumer.py 패턴).
  - `%s`는 문자열 치환이 아니라 **placeholder(자리 표시)**. 파이썬 `%` 포맷팅과
    생김새만 같고 다름.
  - 안전한 이유: **DB가 SQL 구조를 먼저 확정(파싱/컴파일)한 뒤에 값을 "데이터로만"
    꽂는다.** 값이 명령으로 해석될 순서적·구조적 기회가 없다.
  - 인젝션 문자열(`'; DROP TABLE ...`)을 넣어도 **그 문자열과 같은 bid를 찾을 뿐**,
    문장으로 해석하지 않는다 → 그런 방송 없으니 0건.
- 읽기(SELECT)여도 위험: `UNION SELECT`로 남의 테이블 유출, 조건 우회 가능.
  "안전한지 판단하지 말고 **무조건 파라미터화**" — 판단할 필요를 없애는 게 핵심.
- 값 하나여도 튜플로: `(bid,)` — **콤마 필수**. `(bid)`는 그냥 문자열.
  - `execute(SQL, bid)`처럼 str을 넘기면
    `TypeError: query parameters should be a sequence or a mapping, got str`.

---

## 7. View

- `chats` 원본엔 `parts text[]` 한 덩어리로 들어감(message/user_id/nickname이
  따로 컬럼이 아님).
- `chats_view`는 원본 위에 얹은 **창(窓)**: `parts[1] AS message`,
  `parts[2] AS user_id`, `parts[6] AS user_nickname`로 꺼내 이름 붙여 보여줌.
  (+ `bid`, `id`, `received_at`은 원본 그대로 통과)
- 그래서 `user_id`류로 조회하려면 **`FROM chats_view`** 여야 한다(`chats`엔 그
  이름의 컬럼이 없음 → `UndefinedColumn`).
- view 조회 시 DB가 속으로 정의(`FROM chats`)를 펼쳐 결국 원본을 읽는다.

---

## 8. 커넥션 풀에서 연결 빌리기 + 컨텍스트 매니저

- **컨텍스트 매니저** = `async with`에 넣을 수 있는 객체(`__aenter__`/`__aexit__`
  보유). 들어갈 때 준비, **나갈 때 반드시 정리**(예외든 정상이든)를 보장.
  - `async with`의 계약은 `open` 같은 이름이 아니라 **`__aenter__`/`__aexit__`**
    두 개뿐. `open`은 psycopg_pool이 자기 `__aenter__` 안에서 부르는 내부 사정.
- `pool.connection()`은 연결을 **바로 주는 게 아니라** "연결 든 봉투(컨텍스트
  매니저)"를 준다. `async with pool.connection() as conn:`으로 **열어야** 진짜
  `conn`이 나온다.
- **연결은 "여는" 게 아니라 "빌리는" 것.** 풀이 이미 열어둔 연결을 꺼내 쓰고
  반납. `conn.open()` 같은 건 부를 일 없다. (풀 여는 `pool.open()`은 lifespan에서
  서버 생애 1번, 이건 별개.)

### 겪은 버그 3종 (전부 "사용법" 문제, 컨텍스트 매니저 탓 아님)
1. `async with request.app.state.pool as pool` — **공유 풀 자체를 async with** →
   요청 끝날 때 풀을 닫음 → 두 번째 요청부터
   `PoolClosed: pool has already been opened/closed and cannot be reused`.
   (풀은 한 번 닫으면 **재사용 불가**. 열고/닫기는 lifespan 소관.)
2. `conn = pool.connection()` (async with 없이) → 봉투째 넘김 →
   `'_AsyncGeneratorContextManager' object has no attribute 'cursor'`.
3. `getconn()`만 쓰고 반납 안 함 → **당장은 동작**하지만 빌린 연결이 안 돌아와
   풀이 마름(connection leak) → 트래픽 쌓인 뒤 멈춤. `PoolClosed`보다 고약(늦게
   터짐).
- **정답 한 줄:** `async with request.app.state.pool.connection() as conn:`
  → 풀 안 닫고(공유 유지) + 봉투 열어 conn 꺼냄 + 블록 나가며 자동 반납.

---

## 9. 의존성 주입 (Dependency Injection)

```python
async def get_db(request: Request):
    async with request.app.state.pool.connection() as conn:
        yield conn                    # postgres: 빌리고→넘기고→반납

def get_r(request: Request):
    return request.app.state.redis    # redis: 반납 없으니 그냥 return

DBDep = Annotated[AsyncConnection, Depends(get_db)]   # 대괄호! (소괄호 아님)
RDep = Annotated[Redis, Depends(get_r)]
```
- dependency의 `yield`도 lifespan처럼 "쓰기 전/후"를 나눈다: `yield conn` 위=빌림,
  아래=반납. 요청 끝나면 아래(반납)가 실행.
- postgres는 빌림·반납이 있어 `yield`, redis는 반납이 없어 `return`(비대칭).
- 라우터가 깔끔해짐: `request`도 `pool = ...`도 `async with`도 사라지고
  `async def read_chats(bid: str, conn: DBDep, limit: int = 100):` 로 conn을
  바로 받음. "연결이 어디서 오는지"를 라우터가 몰라도 됨(관심사 분리).

### 응답 스키마
```python
class ReadChatResponse(p.BaseModel):
    bid: str; user_id: str; user_nickname: str; message: str
```
- `@router.get(..., response_model=list[ReadChatResponse])` 로 "응답은 이 스키마의
  리스트"라 선언 → 검증 + `/docs` 문서화 자동.
- 커서를 `conn.cursor(row_factory=dict_row)`로 열면 rows가 **이름 붙은 dict**로
  나와서 모델 필드와 자동 매칭 → `return rows` 그대로. (`from psycopg.rows import
  dict_row` 필요.)

---

## 10. 컨슈머 집계 — INCR / ZINCRBY (`/stats`의 선행작업)

### 왜 Redis에 미리 세나 (캐싱)
- "유저 순위"를 매번 Postgres에서 `GROUP BY COUNT`로 세면 수백만 row를 훑어 느림.
- 발상 전환: **매번 세지 말고, 채팅 들어올 때마다 미리 +1 해두고 읽기만.**
  이게 학습목표 "캐싱".

### 스트림 말고 다른 자료구조
같은 Redis 안에 공존하지만 용도가 다르다.
```
soop:chats                      ← Stream(큐). 프로듀서→컨슈머 통로. [기존]
count:{bid}:{yyyymmddHHMM}      ← String 카운터. 분당 채팅 수. INCR.  [추가]
rank:{bid}                      ← Sorted Set. 유저별 순위. ZINCRBY.    [추가]
```

### INCR vs ZINCRBY
- **INCR** = 키 하나에 숫자 하나. "분당 수"처럼 단일 값. 없는 키는 0에서 시작.
- **ZINCRBY** = sorted set 안에서 멤버별 점수를 올리고 **자동 정렬 유지**.
  "유저 순위"처럼 여러 항목을 점수로 줄 세울 때.
  - Sorted Set = 키 하나에 "멤버+점수" 쌍 여럿. 멤버는 유일(유저당 한 자리),
    점수로 자동 정렬. (DB "행"과 달리 멤버1개+점수1개뿐.)
  - 순위에 INCR 못 쓰는 이유: 값이 흩어지고 정렬 개념이 없어 상위 N 뽑기가 지옥.

### 왜 분마다 키를 나누나 (내 아이디어 반박)
- "(카운트, 시작시각, 최근시각) 하나면 여러 해상도 다 구한다" → **전체 평균만**
  나옴. 방송의 핵심인 "언제 터졌나"(분당 추이)를 못 복원. 평균이 시간축을 뭉갬.
- 원칙: **잘게 저장→합쳐서 크게 보긴 쉽지만, 크게 저장→쪼개긴 불가능.** 그래서
  가장 작은 의미 단위(분)로 저장. 시간당은 60개 분 키를 합치면 됨.
- 키 폭발 걱정의 해법은 "뭉치기"가 아니라 **TTL**(`expire`로 오래된 키 자동 삭제).

### 어디에 넣나 — 프로듀서 ✗, 컨슈머 ✓
- 집계는 "처리"라 처리 담당인 **컨슈머**가 한다(수집/처리 분리). 프로듀서는
  방송마다 따로 떠서 흩어지고, 저장과 타이밍도 안 맞음.
- **배치(flush)로 집계**하기로 결정: 왕복 덜 하고, 실시간 불필요(10초 늦어도 OK),
  저장과 타이밍 일치. (즉시성이 필요한 건 `/live`뿐 → 거긴 반대로 즉시 전송.)

### 집계 함수 (현재)
```python
async def broadcast_stats(buffer_rows, r):   # 이름은 update_stats류가 더 정확
    for bid, parts, received_at in buffer_rows:
        dt = datetime.fromisoformat(received_at)
        minute = dt.strftime("%Y%m%d%H%M")
        await r.incr(f"count:{bid}:{minute}")
        await r.zincrby(name=f"rank:{bid}", amount=1.0, value=parts[1])
```
- **count의 "분"은 채팅의 `received_at` 기준**(저장 시각 X). 버퍼가 모았다
  flush하면 저장 시각이 늦고, 200개가 서로 다른 시각이라 저장 시각으로 하면 왜곡.
- 점수 +1(채팅 하나 = 1), `amount=1.0`(float, sorted set 점수는 실수).
- user_id는 `parts[1]` (프로듀서가 `parts=[...][1:]`로 서비스코드 잘라 인덱스
  밀림. 실제 찍어서 확인함).

### 원자성 (둘 다 성공/실패) — 현실
- "Postgres 저장 + Redis 집계가 둘 다 되거나 둘 다 안 되거나"가 이상적이지만,
  **서로 다른 시스템이라 하나로 묶는 트랜잭션이 없다.** commit 후 죽으면 집계만
  누락 가능.
- 전략: **원본(Postgres)은 정확히 먼저 → 집계는 뒤에, 대략 맞으면 OK.** 집계는
  정확도가 생명이 아니고, 어긋나면 **원본에서 언제든 재계산 가능**(원본이 진실의
  원천). 순서: `commit → xack → 집계`.

### 남은 버그/개선 (다음 세션에서 처리)
- ⚠️ **버그: `broadcast_stats(buffer_rows, r)`를 `buffer_rows = []`로 비운 뒤에
  호출** → 빈 리스트가 넘어가 for가 안 돎(집계 0). **비우기 전으로 옮겨야 함.**
  (commit → ack → `broadcast_stats` → 비우기)
- `isflushready`가 `buffer_rows and (...)` 반환 → 빈 리스트일 때 `[]` 반환(타입
  힌트 bool과 불일치). `bool(buffer_rows) and (...)`로 감싸면 정확(동작엔 지장
  없음).
- TTL 미적용(count 키 영구 잔존). 파이프라인 최적화 때 `incr+expire` 함께.
- 왕복 최적화: 지금 for로 하나씩 `await`(배치당 최대 400 왕복). → Redis
  **파이프라인**으로 묶기(나중).

### redis-cli 확인 명령 (포트 6380)
```
redis-cli -p 6380
KEYS count:*
GET count:{bid}:{yyyymmddHHMM}
ZREVRANGE rank:{bid} 0 9 WITHSCORES   # 상위 10명+점수, 높은 순
ZSCORE rank:{bid} {user_id}
ZCARD rank:{bid}
```

### AOF vs 메모리
- **AOF ≠ 메모리 누수.** AOF는 "재부팅해도 RAM에 복원"(지속성). 메모리 증가 원인은
  **TTL 없음**(누수 아니라 "지우는 정책 부재"). 학습량 수준이면 걱정 X, 최적화 때
  TTL로 정리.