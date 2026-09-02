# 학습노트 01 — Redis Stream 프로듀서 (1단계)

> SOOP 채팅 파이프라인 프로젝트 / 수집기 → Redis Stream → Postgres 중 **프로듀서까지 완료**

---

## 0. 이번에 한 일 요약

1. 이전 성능 측정 때 남은 Redis 데이터 전부 초기화 (`FLUSHALL`)
2. `chats` 테이블에 `inserted_at timestamptz NOT NULL DEFAULT now()` 추가
3. 더미 엔트리로 XADD → XRANGE 왕복 검증
4. `stream_soop_chats(bid)`를 붙여서 실제 채팅을 Redis Stream에 적재 (동작 확인 완료)

---

## 1. 이번에 확정한 설계 결정

| 항목 | 결정 | 이유 |
|---|---|---|
| 스트림 키 | `soop:chats` 단일 키 | per-bid로 나누면 컨슈머가 키 목록을 동적 관리해야 함. bid는 필드로 보존하니 나중에 분리 가능 |
| 엔트리 포맷 | `bid` / `received_at` / `parts` 3필드, parts는 JSON 문자열 | 필드 펼치기(`p1`~`p14`)는 parts 길이가 변하면 깨짐 |
| `received_at` | 수집기에서 yield 받은 직후 찍음, ISO8601 aware | "웹소켓에서 받은 시점"이 이 값의 정의 |
| `inserted_at` | 추가함 (DEFAULT now()) | `inserted_at - received_at` = 큐 지연 지표 |
| decode 위치 | 프로듀서 (XADD 직전) | 수집기는 raw bytes 유지 → 디버깅 시 원본 확인 가능. 기존 테스트 22개도 안 건드림 |
| MAXLEN | `~ 100000` (approximate) | 정확 트리밍은 XADD마다 비용. "워커가 죽어 있어도 버티는 시간"으로 환산 (초당 20건이면 ≈80분치) |
| 중복 vs 유실 | at-least-once (`INSERT` → `XACK` 순) | 채팅 로그는 중복이 유실보다 나음 |

**아직 미정 (컨슈머 만들면서 정할 것):** 배치 INSERT 크기, XAUTOCLAIM 설계, 재시작 정책 구체화

---

## 2. Redis 데이터 모델

### 스키마가 없다
Redis는 DDL 개념이 없음. **키 → 값**만 있고, 값의 타입(string/list/hash/stream)은 그 키에 처음 쓸 때 정해짐.

### 컨슈머 그룹은 스트림 키에 딸린 데이터

```
soop:chats  (stream 키)
 ├─ 엔트리들 (1784901540168-0, ...)
 └─ 그룹들
     └─ chat_writer
         ├─ last-delivered-id
         ├─ PEL (읽었지만 XACK 안 된 엔트리 목록)
         └─ 컨슈머들 (worker-1, ...)
```

- `DEL soop:chats` 하나로 그룹·PEL·컨슈머가 통째로 사라짐 → 따로 지울 것 없음
- **역으로: 키가 없으면 그룹도 없다.** 워커가 그룹 존재를 가정하면 안 됨

```python
try:
    await r.xgroup_create("soop:chats", "chat_writer", id="0", mkstream=True)
except redis.ResponseError as e:
    if "BUSYGROUP" not in str(e):
        raise
```

- `mkstream=True` = 스트림 키가 없으면 만들면서 그룹 생성
- `BUSYGROUP` = 이미 그룹 있음. 정상 상황이니 삼키고 진행
- ⚠️ 운영 중 `DEL`로 청소하면 `last-delivered-id`가 리셋됨. 개발 초기에만 안전

### 숫자 DB (0~15)
- URL에 지정 안 하면 **0번**. `redis-cli`도 기본 0번이라 지금까지 본 게 다 0번
- 지정하려면 `redis://localhost:6380/1`
- **쓰지 말 것.** 이름이 없어서 뭐가 뭔지 기억 못 하고, Redis Cluster에는 0번만 존재함
- 대신 **키 프리픽스**(`soop:*`)로 격리 — 의미가 있고 `SCAN MATCH`로 묶임
- 완전한 격리도 아님: 같은 프로세스/메모리/영속화 파일 공유
- 쓸 만한 예외: **테스트용 격리** (15번 DB + 테스트마다 `FLUSHDB`)

### 삭제 명령
| 명령 | 범위 |
|---|---|
| `DEL key` | 특정 키만 |
| `FLUSHDB` | 현재 DB만 |
| `FLUSHALL` | 모든 DB의 모든 키 |

지워지는 건 **데이터뿐**. 설정, ACL, replication은 안 지워짐.
⚠️ AOF 켜져 있으면 FLUSHALL도 AOF에 기록됨 → 재시작해도 되살아나지 않음 (되돌리기 불가)

### TTL은 엔트리 단위로 없음
- `EXPIRE soop:chats 3600` → 스트림 통째로 + 컨슈머 그룹까지 사라짐. 원하는 게 아님
- 대신 **트리밍**: `MAXLEN`(개수 기준) 또는 `MINID`(시간 기준, 엔트리 ID 앞부분이 ms 타임스탬프라 성립)

### 영속화
- `--appendonly yes` = AOF. 기본 fsync `everysec` → 최악 1초치 유실
- named volume(`redis_data:/data`) 없으면 AOF 켜도 컨테이너 지울 때 같이 사라짐
- 설정 파일 없이 `redis-server` 띄우면 RDB 스냅샷도 기본 동작
- **다만 이 설계에서 Redis는 버퍼고 진짜 저장소는 Postgres.** 영속화 튜닝은 "Redis가 원본을 들고 있을 때"의 얘기
- AOF는 쓰기마다 디스크 I/O → 나중에 처리량 측정할 때 변수가 됨 (`always`/`everysec`/`no` 비교해볼 만함)

---

## 3. Stream 엔트리는 flat하다

XADD 형태:

```
XADD key * field1 value1 field2 value2 ...
```

- 엔트리 하나가 `{field: value}` 맵인데 **value는 문자열만**. 중첩 불가
- `{parts: [a,b,c]}`를 넣을 방법이 프로토콜 레벨에 없음
- Redis에 list 타입이 없다는 게 아님 — list는 별개의 **최상위 키 타입**이지 엔트리 필드 값이 될 수 없다는 뜻
- → **JSON으로 평탄화**해서 문자열 하나로 넣는다

redis-py에서 값으로 허용되는 타입: `str | bytes | int | float`
dict나 list 넣으면 → `DataError: Invalid input of type: 'list'`

### 엔트리 ID
`1784901540168-0` = `밀리초 유닉스 타임스탬프-시퀀스`
같은 ms에 여러 개 들어오면 `-0`, `-1`, `-2`
`decode_responses=False`(기본)면 반환값이 `bytes` → `b'1784901540168-0'`

---

## 4. 인코딩 / 디코딩 — 이번의 핵심

### 두 층으로 나뉜다

```
"매"  ←→  U+B9E4  ←→  EB A7 A4
글자      코드포인트      바이트
        (유니코드)   (UTF-8로 인코딩한 결과)
                    ↑ 여기서만 규칙 선택이 일어남
                      UTF-16이면 B9 E4 / CP949면 B8 C5
```

- **유니코드** = 모든 글자에 번호를 붙인 표. 저장 방법은 아무 말도 안 함
- **인코딩(UTF-8 등)** = 그 번호를 실제 바이트로 적는 규칙
- "바이트를 유니코드로 변환한다"는 말은 성립 안 함. 유니코드는 결과물이지 변환 방식이 아님

UTF-8은 가변 길이:

| 글자 | 코드포인트 | UTF-8 바이트 |
|---|---|---|
| A | U+0041 | `41` (1B) |
| é | U+00E9 | `C3 A9` (2B) |
| 매 | U+B9E4 | `EB A7 A4` (3B) |
| 😀 | U+1F600 | `F0 9F 98 80` (4B) |

ASCII 범위는 1바이트 그대로 → 영문 텍스트는 ASCII 파일과 바이트가 동일. UTF-8이 표준이 된 이유.

### 바이트만 봐서는 규칙을 알 수 없다
파일에도 네트워크에도 "이건 UTF-8입니다" 표시가 없음. 그래서 HTTP는 `Content-Type: charset=utf-8`, HTML은 `<meta charset>`, 파이썬은 `open(encoding=...)`으로 따로 알려줌.

```python
b = "매".encode("utf-8")   # b'\xeb\xa7\xa4'
b.decode("utf-8")      # '매'
b.decode("latin-1")    # 'ë§¤'   ← 절대 에러 안 남. 조용히 틀림
b.decode("ascii")      # UnicodeDecodeError
```

`latin-1`은 바이트 1개=글자 1개로 무조건 매핑해서 에러가 안 남 → 인코딩 문제가 조용히 지나가는 대표 경로. "뷁" 같은 글자가 나오는 게 이것.

### `\uXXXX` vs `\xNN` — 헷갈렸던 부분

| | `\ub9e4` | `\xeb\xa7\xa4` |
|---|---|---|
| 세는 단위 | 문자(코드포인트) | 바이트 |
| 만든 주체 | `json.dumps` | redis-cli 출력 |
| **저장된 값에 있나** | **있음** | **없음 (표시만)** |
| 걷어내는 법 | `ensure_ascii=False` | `--raw` |

- `\ub9e4`는 표기 자체가 ASCII 문자 6개(`\`,`u`,`b`,`9`,`e`,`4`). 가리키는 대상 U+B9E4는 ASCII가 아님. **ASCII만 써서 비ASCII를 표현하는 우회 수단**
- `ensure_ascii=True`(기본값) = "출력이 ASCII임을 보장하라" → 한글을 이스케이프. 데이터는 안 틀림(`json.loads`로 복원됨), 눈으로 못 읽을 뿐
- redis-cli가 `\xNN`으로 찍는 이유: **Redis 값에 타입 정보가 없어서** 텍스트인지 JPEG인지 압축 데이터인지 구분 못 함. 이진 데이터를 raw로 터미널에 쏟으면 터미널이 깨지거나(→`reset` 필요) 탈출 시퀀스가 화면을 지움. 안전한 기본값
- redis-cli는 파이프로 넘길 때(`| grep`)는 자동 raw. 터미널에 직접 붙어 있을 때만 이스케이프

### 전체 흐름

```
1. 웹소켓 → bytes
2. .decode() → str            (수집기가 아니라 프로듀서에서)
3. json.dumps(ensure_ascii=False) → str   ← 직렬화 (리스트→문자열)
4. XADD → redis-py가 다시 UTF-8 encode → 소켓 → Redis는 바이트만 저장
5. 워커는 정확히 역순: 바이트 → decode → json.loads → 리스트
```

3번과 4번의 인코딩은 **다른 층**. 3번은 "구조를 문자열로"(직렬화), 4번은 "문자열을 전송 가능한 바이트로"(인코딩). `ensure_ascii`는 3번 결과물의 모양만 정함.

### 이 왕복이 낭비인가?
bytes → str → bytes 왕복이 있는 건 사실. 그런데:

- **최종 목적지가 `text[]`.** 어차피 str이어야 하고, 뒤로 미루면 소비자(워커 + API)가 각자 하게 됨
- **비용이 무시할 수준.** CPython UTF-8 변환은 C로 짜여 초당 수백 MB. 같은 경로의 Redis 왕복(수백 μs)과 Postgres INSERT(ms)가 수천 배 비쌈. **병목은 언제나 네트워크와 디스크**
- **디버깅 가능성이 훨씬 값짐.** `XRANGE --raw`로 눈 확인이 됨

→ "불필요한 변환"이 아니라 **경계에서의 정규화**. 입구에서 한 번 str로 못박으면 뒤의 모든 코드가 bytes/str을 신경 안 써도 됨. 그게 진짜 비용 절감.

### `errors="replace"`
```python
b'\xff\xfe'.decode()                    # UnicodeDecodeError
b'\xff\xfe'.decode(errors="replace")    # '��'
b'\xff\xfe'.decode(errors="ignore")     # ''
```
- 기본값 `strict`면 깨진 바이트 하나에 `async for` 밖으로 예외가 튀어서 **프로듀서 전체가 죽음**
- `ignore`는 비권장 — 조용히 사라져서 나중에 단서가 없음. `�`는 눈에 보임

---

## 5. 파이썬 / 도구 관련

### `await` 붙이는 기준
**Redis에 명령을 보내는 것들은 전부 `await`** (`xadd`, `xlen`, `ping`, `xreadgroup`...)
`from_url`은 아님 — 코루틴이 아니라 그냥 `Redis` 인스턴스를 반환하는 동기 함수. 연결은 첫 명령 때 lazy하게 열림.
→ 네트워크 왕복이 있냐 없냐로 갈림.

`RuntimeWarning: coroutine ... was never awaited` = **`await` 빠뜨림 전용 경고**라고 봐도 됨

### mypy: dict는 invariant
```
error: Argument 2 to "xadd" has incompatible type "dict[str, Any]";
       expected "dict[bytes | ... | str | int | float, ...]"
```
- 값이 아니라 **키 타입** 때문. `dict[str, X]`는 `dict[str | bytes | int | float, X]`에 대입 불가
- 이유: dict가 가변이라, 함수 안에서 `d[b"key"] = ...` 하면 호출자의 가정이 깨짐
- 해결: `from redis.typing import EncodableT, FieldT` → `dict[FieldT, EncodableT]`
- `cast`는 mypy를 속이는 거라 진짜 틀렸을 때 못 잡음
- **교훈: 내 코드에서 dict를 받는 함수는 `Mapping[str, str]`로 받자** (covariant라 이 마찰이 없음). 같은 원리로 `list[str]`도 `list[object]` 받는 함수에 못 넘김 → `Sequence`

### 포트 매핑
`6380:6379` = 호스트 6380 → 컨테이너 6379

| 어디서 실행 | 명령 | 포트 |
|---|---|---|
| 컨테이너 안 | `docker compose exec redis redis-cli ...` | 생략 (6379) |
| 호스트 | `redis-cli ...` (설치 필요) | `-p 6380` |

컨테이너 안에는 6380이라는 포트가 존재하지 않음. 파이썬 코드는 호스트에서 도니 `settings`의 6380이 맞음.
→ 나중에 워커를 컨테이너로 옮기면 `redis://redis:6379`로 바꿔야 함

### Docker CLI 힌트 끄기
```bash
export DOCKER_CLI_HINTS=false   # .zshrc에 추가
```

### config에 뭘 넣나
**넣을 것:** 환경마다 달라지는 값(호스트/포트/비번), 운영 중 조정할 튜닝 값(maxlen, 배치 크기, 타임아웃), **여러 모듈이 공유하는 값**(스트림 키, 컨슈머 그룹 이름)
**안 넣을 것:** 코드 로직의 일부인 상수(`parts[1:]`의 1, 인덱스 번호), 한 파일에서만 쓰고 안 바뀌는 값

→ 컨슈머 그룹 이름은 **반드시** settings로. 양쪽에서 오타 나면 "XADD는 되는데 워커가 아무것도 못 읽음"이라는 제일 찾기 힘든 버그가 남

### FastAPI lifespan (나중에 쓸 것)
```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.redis = from_url(settings.redis_url, decode_responses=True)  # await 아님!
    try:
        yield
    finally:
        await app.state.redis.aclose()   # close()는 5.0.1부터 deprecated
```
- 쓰는 이유: **연결 풀 수명을 앱 수명과 맞추기 위해.** 요청마다 만들면 매번 TCP 핸드셰이크
- 전역 변수 대비 장점: 종료 보장 / 테스트에서 교체 쉬움 / `ping()` 넣으면 시작 실패를 일찍 잡음
- **성능 오버헤드 사실상 0.** 앱 시작·종료 때 딱 두 번. 미들웨어(매 요청)와 혼동 주의
- 진짜 단점: Redis 죽어 있으면 앱이 아예 안 뜸 / `TestClient`를 `with` 없이 쓰면 lifespan이 안 돌아 `app.state.redis`가 없다고 터짐
- **지금 프로듀서/워커에는 안 맞음.** 요청-응답 서버가 아니라 오래 도는 스크립트라 `async with`가 같은 일을 더 적은 코드로 함
- `decode_responses`: 프로듀서는 `False`(기본, XADD만 하니 응답 읽을 일 없음) / API는 `True`가 편함. **용도별로 다른 클라이언트를 쓰는 게 정상**

---

## 6. 삽질 로그

| 증상 | 원인 |
|---|---|
| `<coroutine object Redis.execute_command>` 출력 + `never awaited` 경고 | `await r.xadd(...)` 빠짐 → Redis에 아무것도 안 들어감 |
| `Connection refused` (컨테이너 안에서) | `-p 6380`을 컨테이너 안에서 씀. 컨테이너는 6379 |
| `DataError: Invalid input of type: 'list'` | `parts`를 `json.dumps` 안 하고 넣음 |
| `TypeError: dump() missing 'fp'` | `json.dump`(파일에 쓰기) ≠ `json.dumps`(문자열 반환). 읽기도 `load`/`loads` |
| `ensure_ascii=True` 했는데 한글 안 나옴 | True가 "이스케이프해라"라는 뜻. 원하는 건 False |
| `--raw` 없이 여전히 `\x..` | redis-cli 출력 방식. 저장은 정상 |
| 필드명 `recived_at` 오타 | 워커에서 `KeyError` 날 때 원인 추적이 어려움 → 즉시 수정 + 재적재 |
| `parts[1:]` 안 함 | 헤더 `\u001b\t000500009900`가 남아 전체가 한 칸씩 밀림. Postgres 배열은 1부터 시작이라 헤더 버린 리스트의 파이썬 idx 0 = PG idx 1로 딱 맞음 |
| 슬라이스는 했는데 `raw_parts`를 넘김 | 이름 재사용(`parts` 두 개) 때문. 변수명 분리 필요 |
| `parts[5]` → `KeyError: 5` | 함수가 반환한 건 dict인데 정수 인덱싱. 그 안의 `"parts"`는 이미 JSON **문자열**이라 인덱싱하면 글자가 나옴 |

**패턴:** 실수 대부분이 "같은 이름의 다른 물건"(parts/raw_parts, dict/list) 또는 "표시 vs 저장"(`\x`, `ensure_ascii`) 혼동에서 나옴.

---

## 7. 멱등성 키 (구버전 부하테스트 코드에서 발견)

부하테스트용 프로듀서에 이런 필드가 있었음:

```python
"msg_seq": seq,               # ← 멱등성 키
"enqueued_at": time.time(),   # ← E2E 지연 측정용
```

### 왜 필요한가
`INSERT` → `XACK` 순서로 가면 **at-least-once**다. INSERT 성공 직후 XACK 전에 워커가 죽으면, 그 엔트리는 PEL에 남아 있다가 재시작 후 다시 배달됨 → **같은 채팅이 두 번 INSERT**.

반대(`XACK` → `INSERT`)면 at-most-once로 유실이 생김. 채팅 로그는 중복 > 유실이므로 전자를 선택했고, **중복 제거 수단이 따로 필요해질 수 있다**.

### 선택지
1. **프로듀서가 유일 키를 붙인다** (`msg_seq` 방식)
   - 단조 증가 시퀀스나 UUID를 엔트리에 넣고, DB에 UNIQUE 제약 + `ON CONFLICT DO NOTHING`
   - 단점: 프로듀서 재시작 시 시퀀스가 리셋됨 → bid+시퀀스 조합이나 UUID가 안전
2. **스트림 엔트리 ID를 그대로 쓴다**
   - `1784901540168-0`은 스트림 내에서 유일. 프로듀서가 따로 만들 필요 없음
   - `chats`에 `stream_id text` 컬럼 추가 + UNIQUE
   - 단점: 스트림 키가 바뀌거나 재생성되면 유일성 보장이 깨짐
3. **아무것도 안 한다**
   - 중복은 워커 크래시 시에만 발생. 채팅 로그에서 몇 건 중복이 치명적이지 않음
   - 나중에 필요하면 그때 컬럼 추가

### 현재 방침
**3번으로 가되 2번을 대비한다.** UNIQUE 인덱스는 쓰기 성능을 깎으므로(이미 "인덱스는 PK만" 결정) 지금 넣지 않는다. 다만 워커에서 엔트리 ID를 로그에 남겨두면, 나중에 중복이 실제로 문제가 됐을 때 규모를 파악할 수 있다.

⚠️ **주의:** 배치 INSERT를 도입하면 이 문제가 커진다. 배치 100건을 INSERT하고 XACK 전에 죽으면 100건이 통째로 중복됨. 배치 크기 = 중복 가능 최대치. 이것도 배치 크기 결정 시 고려할 것.

---

## 8. 파이프라이닝 (나중에 쓸 패턴)

구버전 부하테스트 코드:
```python
pipe = r.pipeline(transaction=False)
for _ in range(to_send):
    pipe.xadd(stream_key, ...)
await pipe.execute()          # ← 왕복 1번
```

현재 프로듀서는 채팅 하나당 `await` 하나 = **왕복 N번**.
실제 채팅은 초당 수십 건이라 지금은 문제없음. 하지만 **여러 방송 동시 수집** 단계에서 병목이 됨.

구버전에서 배울 것:
- `TICKS_PER_SEC`으로 1초를 잘게 쪼개 부하를 평탄화 (톱니 부하 방지)
- `carry`로 소수점 누적 오차 보정
- `behind` 카운터로 프로듀서가 못 따라간 구간 계측

---

## 9. 구버전 vs 신버전 프로듀서 비교

| | 구 (부하테스트) | 신 (현재) |
|---|---|---|
| 키 | `chat_stream:{bid}` — 방송마다 갈림 | `soop:chats` 단일 |
| 포맷 | 8필드 flat | 3필드, parts는 JSON |
| 트리밍 | 없음 (무한 적재) | `maxlen=100000, approximate` |
| 전송 | 파이프라인 (왕복 1회/N건) | 건당 await |
| 멱등성 | `msg_seq` 있음 | 없음 |
| 지연 측정 | `enqueued_at` (epoch float) | `received_at` + DB `inserted_at` |

구버전의 8필드 flat이 **틀린 게 아니라 요구가 달랐음** — 필드가 고정이고 원본 보존이 목적이 아니었음. 지금은 parts 15개를 통째로 보존해야 하고 길이가 변할 수 있어서 JSON이 맞는 것.

`decode_responses=True`는 **응답 받을 때만** 영향. 저장되는 바이트는 동일.

---

## 10. 다음 단계 (컨슈머)

1. `XGROUP CREATE` + `BUSYGROUP` 삼키기
2. `XREADGROUP`으로 **읽기만** 하고 print — DB는 아직 안 붙임
   - 반환값이 중첩 리스트라 구조를 눈으로 한 번 확인할 것
   - `decode_responses` 여부에 따라 `entry[b"parts"]` / `entry["parts"]`로 갈림
3. psycopg 연결 + INSERT 한 건씩
4. `XACK`
5. 그 다음: 배치, `XAUTOCLAIM`(죽은 컨슈머 엔트리 회수), 지연 측정

**프로듀서와 컨슈머 파일 분리** — 실행 단위가 다르고(각각 독립 프로세스, 컨슈머는 나중에 여러 개), 의존성이 다르고(websockets/httpx vs psycopg), 고장 지점이 코드 위치로 드러남.

**나중에 고려:** 엔트리 포맷 지식(필드명 + JSON 왕복)이 프로듀서/컨슈머/API 세 군데로 흩어지면 `app/chat/stream_format.py`에 `to_entry`/`from_entry` 짝을 두고 공유. 지금은 오버엔지니어링.