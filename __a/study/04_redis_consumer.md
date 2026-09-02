# 학습노트 — Redis Streams 컨슈머 구현

> SOOP 채팅 백엔드 프로젝트. 프로듀서(Redis에 채팅 적재)는 완성돼 있었고,
> 이번에 **컨슈머(Redis → Postgres 저장)**를 처음부터 직접 구현하며 배운 것 정리.

---

## 1. 전체 구조

```
SOOP → [프로듀서] → Redis Stream → [컨슈머] → Postgres
                    (완충재)
```

- Redis를 중간에 끼우는 이유: 채팅이 몰려 DB가 느려도 프로듀서가 안 멈추게. **완충재.**
- 프로듀서 / 컨슈머 / (나중에) FastAPI는 **서로 다른 프로세스** → 메모리 공유 불가
  → 그래서 Redis·DB 같은 **외부 저장소**로 데이터를 주고받음.
- `r`(Redis 클라이언트)은 데이터가 아니라 **Redis로 가는 전화선**. 프로세스마다 각자 만듦.
  창고(Redis)는 하나, 전화선은 여러 개.

---

## 2. 컨슈머 그룹 핵심 개념

- **스트림** = 번호표 뽑는 대기열. 채팅마다 `1706-0` 같은 ID가 붙어 줄줄이 쌓임.
- **컨슈머 그룹** = Redis가 대신 관리해주는 장치:
  - 어디까지 나눠줬는지 기억 (책갈피 = `last-delivered-id`)
  - 워커 여러 개면 안 겹치게 분배
  - "줬는데 아직 XACK 안 온 것" 목록 관리 (**PEL**)

### 그룹 이름 vs 컨슈머 이름
- **그룹 이름** = "팀 이름". 같은 일을 하는 워커들이 **공유**. (예: `chat-to-postgres`)
- **컨슈머 이름** = "팀원 이름". 워커마다 **달라야** 함. 같으면 PEL이 섞여서 크래시 복구가 꼬임.

### 같은 스트림에 그룹 여러 개 가능
- **같은 그룹 안** → 채팅을 나눠 가짐 (분배)
- **그룹끼리** → 같은 채팅을 각자 전부 받음 (독립)
- → 나중에 "DB 저장 그룹"과 "실시간 중계 그룹"을 **같은 스트림에 따로** 붙일 수 있음.
  저장은 느긋해도 되고(중계가 실시간 담당), 서로 영향 없음.

---

## 3. ensure_group — 그룹 생성 (멱등)

```python
async def ensure_group(r) -> None:
    try:
        await r.xgroup_create(stream_key, GROUP_NAME, id="0", mkstream=True)
    except ResponseError as e:
        if "BUSYGROUP" not in str(e):
            raise
        # 이미 있으면 조용히 넘어감
```

- **`mkstream=True`**: 스트림 키가 아예 없을 때(새 환경/CI) 빈 스트림을 만들어줌. 없으면 `no such key` 에러.
- **`id` (시작 위치)** — **그룹이 처음 생성될 때 딱 한 번만** 유효:
  - `"0"` = 맨 처음부터 (쌓인 것 전부)
  - `"$"` = 지금부터 (이미 쌓인 건 무시)
  - 재시작해도 이 값은 무시됨 → 그룹이 책갈피를 기억하므로 이어서 감.
- **BUSYGROUP만 삼키고 나머지는 raise** — `except ResponseError: pass`로 뭉개면 오타/권한 에러까지 먹음.
  `if "BUSYGROUP" not in str(e): raise` 로 뒤집어서, 예상 못 한 에러는 다시 던짐.
- `e`는 예외 **객체**라 문자열 검사하려면 `str(e)` 필요.

---

## 4. XREADGROUP — 읽기 (+ PEL 기록)

```python
resp = await r.xreadgroup(
    GROUP_NAME, consumername=worker,
    streams={stream_key: ">"},   # 필수. 기본값 없음
    count=100, block=1000,
)
```

- XREADGROUP은 **읽기만** 함. DB 저장·XACK은 전부 내 몫.
- 단, **부작용 있는 읽기** — 읽는 순간 "내가 책임진다"고 PEL에 기록됨.
- **`streams` 값**:
  - `">"` = 아직 아무도 안 받은 **새 엔트리** (책갈피 이후). 평소엔 이거.
  - `"0"` = 내 **PEL에 있는 것** (받았는데 XACK 안 한 것) 다시. **크래시 복구용.**
  - `>`는 "스트림에 남은 것"이 아니라 "책갈피 뒤"만 봄 → 스트림에 데이터 많아도 책갈피가 끝을 따라잡으면 빈 리스트.
- **`count`** = 한 번에 **최대** 개수 (목표 아님). 넘쳐도 유실 아니고 다음 호출에 받음. `None`=무제한(위험).
- **`block`** (ms):
  - `None`(기본) = 안 기다림 → while과 만나면 CPU 폭주 ❌
  - `0` = 무한 대기
  - `5000` = 5초 대기 후 빈 결과. **유한값 추천.**
- **트리거**: "100개 찰 때까지"가 아니라 **1개라도 있으면 즉시 반환**. 0개일 때만 block 대기.

### XREAD vs XREADGROUP
| | XREAD | XREADGROUP |
|---|---|---|
| 위치 기억 | 내가 변수로 직접 | Redis가(책갈피) |
| 분배 | ❌ 다 같은 것 받음 | ✅ 나눠 받음 |
| PEL(크래시 복구) | ❌ | ✅ |

---

## 5. 반환값 구조 (3겹) + 파싱

```python
[ ['soop:chats', [ (entry_id, {fields}), (entry_id, {fields}), ... ]] ]
  └바깥: 스트림 목록(1개)   └안쪽: 엔트리들(최대 count개)
```

- `resp[0]` = `['soop:chats', [엔트리들]]`
- `resp[0][0]` = 스트림 키
- `resp[0][1]` = 엔트리 리스트
- `resp[0][1][0]` = `(entry_id, {fields})` 튜플
  - `[0]` = **엔트리 ID** (XACK용, 꼭 보관!)
  - `[1]` = 필드 딕셔너리
- **바깥은 `[0]`으로 까고, 안쪽은 `for`로 순회** (엔트리가 여러 개니까):
  ```python
  _, entries = resp[0]
  for entry_id, fields in entries:
      ...
  ```
- for 언패킹 = 인덱스 파는 것과 동일. `for a, b in x` → `a=x[i][0], b=x[i][1]`.

### decode_responses
- Redis는 항상 bytes로 저장. `decode_responses=True`면 redis-py가 **받을 때 str로 변환**.
- **저장된 값은 안 바뀜.** redis-py가 나에게 건네는 형태만 바뀜. → pending 삭제 불필요.
- 컨슈머는 켜는 게 편함 (한글도 제대로, 파싱 깔끔).

### 필드 형태 (이 프로젝트)
```python
{'bid':..., 'received_at':..., 'parts': '["메시지","유저id",...]'}
```
- `parts`는 JSON 문자열 → `json.loads()`로 리스트로 풀기.
- `received_at`은 ISO 문자열 → DB의 timestamptz에 넣으려면 `datetime.fromisoformat()`.
  (isoformat=넣을 때, fromisoformat=꺼낼 때. 짝.)

---

## 6. INSERT + XACK — 순서가 생명

```python
await cur.execute(sql, flat)   # 1. INSERT
await conn.commit()            # 2. 커밋 (DB 확정)
await r.xack(stream_key, GROUP_NAME, *ids)   # 3. 커밋 후 XACK
```

- **반드시 커밋 → XACK 순서.** 커밋 전 XACK하면 DB엔 없는데 PEL에서 지워져 **유실**.
- INSERT 후 XACK 전에 죽으면 → PEL에 남아 재처리(중복). → **at-least-once** (중복 > 유실).
- **conn vs cursor**:
  - `conn` = 전화선 + 트랜잭션 단위. commit/rollback 권한.
  - `cursor` = 쿼리 보내는 창구. execute만.
  - conn은 한 번 열어 재사용, cursor는 배치마다 새로.
- XACK 안 하면 → PEL에 무한정 쌓임 (유실 아님, 오히려 안 지워짐).

### 진단 명령
```bash
redis-cli XINFO GROUPS soop:chats     # pending, last-delivered-id, lag
redis-cli XPENDING soop:chats <group> # PEL 요약 (누가 몇 개 물고 있나)
```
- `pending` = XACK 안 된 개수 (제대로 돌면 0 근처)
- `lag` = 아직 안 읽고 남은 새 엔트리 수

---

## 7. multi-row INSERT vs executemany

```python
placeholders = ",".join(["(%s, %s, %s)"] * len(rows))
flat = [x for row in rows for x in row]   # 튜플 껍데기 벗겨 1차 배열로
sql = f"INSERT INTO chats (bid, parts, received_at) VALUES {placeholders}"
await cur.execute(sql, flat)
```

- **multi-row가 executemany보다 ~1.4배 빠름** (네트워크 왕복 1번으로 줄임).
- `flat` 필요 이유: `execute`는 `%s` 개수만큼 **1차 배열**을 먹음. 튜플 중첩을 못 알아들음.
  (parts는 리스트여도 `%s` 하나에 대응 = 안 펼쳐짐 = OK → `text[]`로 저장)
- **진짜 단점은 하나**: 파라미터 상한 **65535개** (3컬럼이면 ~21845행). 배치 크면 청크 분할 필요.
  (나머지 "단점"들은 실측으로 무의미하거나 executemany랑 차이 없음)
- 트레이드오프: multi-row는 **메모리 조금 더**, **CPU/네트워크 덜**. → 속도를 메모리로 삼.

### 컴프리헨션 중첩 순서 규칙
- `[값 for 바깥 for 안쪽]` — **for문 위→아래 순서 그대로 왼→오**.
- `[x for row in rows for x in row]` ✅ / `[x for x in row for row in rows]` ❌ (row 정의 전 사용).
- 헷갈리면 for문으로 먼저 쓰고 그대로 눕히기.

---

## 8. 버퍼 flush (배치 축적)

Redis엔 "N개 찰 때까지 대기" 옵션이 **없음** → 컨슈머에서 직접 구현.

```python
buffer_rows, buffer_ids = [], []
last_flush = monotonic()

while True:
    resp = await r.xreadgroup(..., block=1000)  # block은 짧게(1초)
    if resp:                                     # 받으면 쌓기만
        rows, ids = parse_resp(resp)
        buffer_rows.extend(rows)                 # append 아님! extend
        buffer_ids.extend(ids)

    # flush 체크는 if resp 밖! (채팅 없어도 시간 조건 확인해야)
    size_ready = len(buffer_rows) >= FLUSH_SIZE
    time_ready = monotonic() - last_flush >= FLUSH_INTERVAL
    if buffer_rows and (size_ready or time_ready):   # 빈 버퍼 방어
        # ... 저장 + commit + xack(*buffer_ids) ...
        buffer_rows, buffer_ids = [], []             # 비우기 + 리셋
        last_flush = monotonic()
```

### 자주 틀린 포인트
- `extend`(원소 이어붙이기) vs `append`(통째로 넣기 → 중첩됨).
- flush 조건은 **`>=`** + `len()` (리스트 자체를 비교하면 TypeError).
- flush 체크는 **`if resp` 밖**. 안에 넣으면 채팅 없을 때 시간 flush를 못 함.
- XACK은 **`*buffer_ids`** (버퍼 전체). 이번 바퀴 `ids`만 하면 이전 것 갇힘.
- flush 후 **버퍼 비우기 + last_flush 리셋** 필수.
- 빈 버퍼일 땐 아무것도 안 건드림 (`if buffer_rows and ...`). 타이머도 리셋 X.

### 핵심 이해
- `FLUSH_SIZE`는 상한이 아니라 **"이만큼 넘으면 flush"** (초과분도 같이 나감).
- `FLUSH_INTERVAL`은 주기가 아니라 **"저장할 게 있을 때 최대 대기"** (빈 버퍼면 무시).
- `block`으로 10초 기다리는 게 아니라, block=1초로 자주 깨서 **시간 조건을 직접 체크**.
- `time.monotonic()` 사용 (`time.time()`은 NTP로 거꾸로 갈 수 있음).
- 트레이드오프: 배치 크게/간격 길게 → 효율↑, 크래시 시 재처리량↑, 지연↑.
  버퍼는 메모리라 크래시 시 사라짐 → 단, XACK 전이라 PEL에 남음 → **복구 로직 있으면 안전**.

---

## 9. async / 스레드 / OS 층위 (개념 정리)

```
[하드웨어] CPU 코어 (물리 계산 장치)
[OS]      프로세스(독립 메모리) → 스레드(실행 흐름, 코어에 배정됨)
[파이썬]   이벤트 루프(스레드가 돌리는 while문) → 태스크/코루틴
```

- **스레드는 OS 소속.** OS가 만들고, 재우고, 깨우고, CPU에 배정. 독립적 존재 아님.
- **이벤트 루프 = 스레드가 실행하는 파이썬 while문** (스케줄러). CPU 스케줄러(OS)와 다른 층.
  - "이벤트 루프가 스레드를 부른다" ❌ / "스레드가 이벤트 루프를 돌린다" ✅
- **`await` = 제어권을 이벤트 루프에 반납하는 지점.** 여기서만 다른 태스크로 전환.
  - await 있는 줄(I/O) = 손 떼고 대기 / await 없는 줄(계산) = 스레드가 붙잡고 직접 실행.
- **잠든다 = CPU를 놓음** (OS가 딴 데 씀). **메모리는 유지.**
- I/O 완료 감지는 **OS**가, "뭘 할지"는 **스레드가 이벤트 루프 보고** 결정. OS는 깨우기만.
- **워커 ≠ 스레드.** 워커는 개념. async 태스크 여러 개 = 한 스레드가 번갈아 (요리사 1명, 냄비 여러 개).
- **GIL**: 파이썬은 스레드 여러 개여도 CPU 계산은 한 번에 하나. → CPU는 멀티프로세싱, I/O는 async.
- 태스크 늘리기 = I/O 대기로 한가할 때만 빨라짐. **DB 병목이면 소용없음** (문 하나).
- `threading` = 진짜 OS 스레드. 윈도우/리눅스/맥 다 됨 (파이썬이 OS 차이 흡수).
- **버퍼는 list로 충분.** deque(최근 N개 maxlen용), Queue(멀티스레드 공유용)는 지금 불필요.
  단일 이벤트 루프라 동시 접근 없음 → 락 불필요.

---

## 10. 싱글톤 / 커넥션 (FastAPI 대비)

- 파이썬 싱글톤 = **모듈 레벨 변수** (`settings = Settings()`가 이미 그것). getInstance() 안 씀.
- Redis 클라이언트를 **모듈 최상단**에 두면 안 됨 (async):
  - 이벤트 루프가 여러 번 생기면(pytest, `asyncio.run` 2회) **루프 충돌** → `Event loop is closed` 등.
  - 그래서 스크립트는 `async with`, FastAPI는 `lifespan`에서 만들고 닫음.
- `from_url()`이 주는 `r`은 **커넥션 풀을 안은 객체** → 이미 공유용. 명령마다 새 연결 안 함.
- **웹소켓 + 각자 블로킹 읽기** = 접속 내내 소켓 점유 → 시청자 수만큼 소켓. → **팬아웃**(읽는 태스크 1개)으로 해결.

---

## 11. 용량 감각

- 1 row ≈ **300바이트** (text[] 오버헤드로 순수 데이터의 2~3배 부풀음, PK 인덱스 포함).
- **1GB ≈ 300만 row.** 활발한 방송 1개 24시간 = 하루 ~25만 row → 1GB 차는 데 ~2주.
- 512GB 노트북이면 취미 규모로 몇 년 여유.
- 커지면 순서대로: ①안 쓸 필드 빼기 → ②TimescaleDB(압축+자동보존) → ③파티션 DROP → ④콜드 스토리지 → ⑤클라우드(마지막).

---

## 12. 다음 단계 (FastAPI — design 6.4)

컨슈머로 "쓰는 쪽(write)" 완성 → 이제 "읽는 쪽(read)" FastAPI.

만들 API 3개:
1. **`GET /streams/{id}/chats?limit=100`** — 최근 채팅 조회 (Postgres). 제일 단순, 여기부터.
2. **`GET /streams/{id}/stats`** — 집계 조회 (Redis). ⚠️ 선행: 컨슈머에 집계 로직(`count:`, `rank:` 키) 추가 필요.
3. **`WS /streams/{id}/live`** — 실시간 중계 (팬아웃). ⚠️ 데이터 소스 미결정 (Pub/Sub vs XREAD tail).

여기서 lifespan, 커넥션 풀, 팬아웃이 실제로 등장.