# 학습노트 — FastAPI 에러·엣지 케이스 보강 세션

> `/chats`, `/stats`, `/live` 세 엔드포인트에 입력 검증·예외 처리를 한꺼번에 보강하고,
> 앱 전역 에러 핸들러를 붙인 세션. 아래는 이번에 실제로 손댄 것 + 그 과정에서
> 잡은 개념 + 의도적으로 미뤄둔 것 정리.

---

## 1. 이번 세션에 실제로 한 것 (요약)

| 대상 | 한 일 | 핵심 결정 |
|------|-------|-----------|
| `/chats` | `limit` 범위 검증 (`ge=1, le=1000`), 별칭으로 분리 | 없는 bid는 빈 리스트 유지 |
| `/stats` | `recently_m` 범위 검증 (`ge=1, le=60`) | 빈 결과(없는 bid) 응답이 정상 모양인지 확인 |
| `/live` | JSON 깨짐 → warning+skip / 구조 깨짐 → error+skip | 둘 다 **연결 유지** (트레이스백 안 뜸) |
| 에러 핸들러 | `BaseAppException` + `RequestValidationError` 공통 형식 통일 | `Exception` fallback은 뺌 (`raise exc`) |

---

## 2. 입력 검증 (`/chats`, `/stats`)

### 2.1 검증은 "핸들러 본문에 들어오기 전"에 돈다
- `limit: int` 처럼 타입/제약을 선언하면, FastAPI가 **라우팅 → 파라미터 검증 →
  통과해야 본문 실행** 순으로 처리. 검증 실패면 내 함수는 한 줄도 안 돈다.
- 그래서 `try/except`로 감쌀 필요 없음. 앞단에서 막힌다.

### 2.2 왜 422인가
- 검증 실패 시 FastAPI가 자동으로 `422` + "어느 필드가 왜 틀렸는지" JSON 반환.
- 400이 아니라 422인 이유: 요청 형식 자체(HTTP·타입)는 멀쩡한데 **의미상**
  허용 범위를 벗어났기 때문.
- (최신 표준에서 `422 Unprocessable Content`로 이름만 바뀜. 의미 동일.)

### 2.3 `ge`/`le`/`gt`/`lt`
- `ge` = 이상(≥), `le` = 이하(≤), `gt` = 초과(>), `lt` = 미만(<).
- `limit`: `ge=1`(0·음수 무의미) + `le=1000`(DB를 지키는 방어선).
  - **상한의 근거**: `limit`이 크면 DB가 그만큼 큰 조회를 준비해야 함
    (`limit=999999`는 결과가 3개든 0개든 "100만 개까지 준비"라는 부담).
- `recently_m`: `ge=1` + `le=60`.
  - **상한의 근거 2가지**: ① 값이 크면 만드는 키 개수·`mget` 양이 늘어 Redis 부담
    ② "최근 통계"인데 `recently_m`이 너무 크면 며칠 전까지 긁어 **의미**에 안 맞음.
    60분(1시간)을 "최근"의 상식적 상한으로 잡음.

### 2.4 별칭으로 빼기 (`Annotated`)
```python
LimitQuery      = Annotated[int, Query(ge=1, le=1000)]
RecentlyMQuery  = Annotated[int, Query(ge=1, le=60)]

async def get_chats_api(bid: str, conn: DBDep, limit: LimitQuery = 100): ...
async def get_stats(bid: str, r: RDep, recently_m: RecentlyMQuery = 10): ...
```
- `Annotated[int, Query(...)]`는 **타입**이라 이름 붙여 밖으로 뺄 수 있음
  (`deps.py`의 `DBDep = Annotated[..., Depends(...)]`와 같은 패턴).
- **기본값(`= 100`)은 별칭 안에 못 넣고 파라미터에 남음** — 타입 정보와 기본값은
  별개라서. 오히려 같은 별칭을 다른 기본값으로 재사용 가능.

### 2.5 `LIMIT`은 "최대 이만큼"이지 "정확히 이만큼"이 아니다
- DB에 3개뿐인데 `limit=100` 요청 → 에러 없이 3개만 반환. 모자라도 에러 안 남.
- 그래서 상한 `le=1000`은 "결과가 그만큼 있어서"가 아니라 "요청이 DB를
  크게 때리는 걸 막으려고".

---

## 3. `async def` 호출엔 `await`
- `async def` 함수를 `await` 없이 부르면 **실행이 안 되고 코루틴 객체만 생성**됨.
- `return get_chats(...)` (X) → `return await get_chats(...)` (O)
- 감각: `async def` 호출엔 거의 항상 `await`이 짝. 안 붙이면 "실행 예약표"만 받은 셈.

---

## 4. `/live` 웹소켓 예외 처리

### 4.1 요청-응답 vs 오래 유지되는 연결
- `/chats`·`/stats`는 "요청 1번 → 응답 1번 → 끝". 검증(입력값)이 주 관심사.
- `/live`는 **연결이 살아있는 내내 루프가 돈다.** 관심사가 "입력값"이 아니라
  "이 긴 루프가 도는 도중 뭐가 터지나".

### 4.2 두 종류의 깨진 데이터 (A / B)
스트림의 `parts`는 원래 **JSON 문자열**(프로듀서가 `json.dumps(list)`로 넣음).
- **A. `json.loads` 자체 실패** (잘린 JSON, 비-JSON 쓰레기, 빈 문자열)
  → `json.JSONDecodeError` → **조용히 skip** (`warning` + `continue`). 연결 유지.
  → 이유: 데이터가 오다 깨진 것. 그 채팅만 버리면 됨.
- **B. 파싱은 됐는데 모양이 다름** (dict라서 `parts[0]`→KeyError / 리스트가
  짧아서 `parts[5]`→IndexError)
  → 이건 "깨진" 게 아니라 **구조가 바뀐** 것 = SOOP 정책 변경 신호.

### 4.3 A/B를 가르는 방어 코드
```python
if fields["bid"] == bid:
    try:
        parts = json.loads(fields["parts"])
    except json.JSONDecodeError:          # A: JSON 문법 깨짐
        logger.warning("...")
        continue

    try:
        message, uid, unick = parts[0], parts[1], parts[5]
    except (KeyError, IndexError):        # B: 구조 다름
        logger.error("...")
        continue

    chat = f"[{bid}] {unick}({uid}): {message}"
    await ws.send_text(chat)
```
- **try는 두 개로 분리**: A는 `warning`, B는 `error` — 레벨·메시지가 다르니 따로.
- **try 범위는 "실패할 수 있는 그 줄만" 최소로.** `chat=...`, `send_text`는 실패
  가능성이 없으니(이미 꺼낸 변수만 씀) try 밖. 좁게 잡을수록 의도가 또렷.
- `parts[0], parts[1], parts[5]`는 **한 줄**이라 한 번에 감싸면 셋 다 커버.
  파이썬은 왼쪽부터 평가하다 첫 실패에서 멈춤. → 방어 지점은 5군데가 아니라 1군데.
- `parts`를 인덱싱하는 곳이 이 한 줄뿐이라, **어떤 깨진 값이 와도 여기로 수렴**.

### 4.4 처음 생각을 바꾼 지점 (중요한 관점 전환)
- 처음엔 "에러는 그냥 터지게 두는 게 정직하다"고 생각.
- 바뀐 결론: **연결 유지 = UX용 / 로그 = 개발자가 알기 위함.** 두 목적을 분리.
  - 사용자에겐 매끄러운 경험(연결 안 죽음), 개발자에겐 원인 파악 수단(로그).
  - "터뜨려서 알기" 대신 **"삼키되 로그로 알기"** → 둘 다 챙김.

### 4.5 잡을 예외는 **구체적으로** 지정
- `except Exception`으로 다 잡으면, 터뜨리고 싶던 신호(구조 변경)까지 삼켜짐.
- 잡을 예외 이름을 구체적으로 적어야 "이건 잡고, 저건 터뜨린다"가 성립.

---

## 5. 로거 (logging 모듈) — 최소만 도입

### 5.1 로깅 모듈 = "레벨 있고, 출처 붙고, 목적지 바꿀 수 있는 똑똑한 print"
- **레벨**: `debug < info < warning < error < critical`.
  - 기본 레벨이 WARNING이라 **설정 없이도 `warning`/`error`는 콘솔에 뜸**,
    `info`/`debug`는 안 뜸. (그래서 이번에 깨진 데이터엔 `warning`이 딱 맞았음)
- **출처**: `logger = logging.getLogger(__name__)` → 어느 모듈에서 난 로그인지
  이름표가 내부적으로 붙음.
- **목적지 분리**: 지금은 콘솔이지만 나중에 파일·외부 전송으로 코드 안 건드리고 전환.

### 5.2 지금 넣은 최소 세팅 (2줄)
```python
import logging
logger = logging.getLogger(__name__)
```
- uvicorn이 로깅 기본 설정을 깔아둬서 `logger.warning(...)`이 바로 콘솔에 찍힘.

### 5.3 `__name__`이 로그에 왜 안 보였나
- 이름표는 **갖고 있지만**, 그걸 **출력 줄에 찍을지는 "포맷 설정"이 결정.**
- 비유: 봉투에 발신인 주소는 적어뒀지만(=`getLogger(__name__)`),
  겉면에 인쇄할지는 프린터 설정(=포맷) 나름.
- 보이게 하려면 (나중에):
  ```python
  logging.basicConfig(format="%(levelname)s | %(name)s | %(message)s")
  ```
  `%(name)s`가 `__name__` 자리. → **이건 로거 본격 정리 때 (5단계)**.

---

## 6. 앱 전역 에러 핸들러

### 6.1 핸들러가 하는 일
- FastAPI 기본 422 JSON을 **내 서비스 공통 형식**으로 바꾸는 장치.
- 최종 응답 형식 통일: `{"detail": ..., "code": ...}` (+ 검증 에러는 `errors`도).
  - `code`(기계용 식별자)를 넣으면 클라이언트가 `code == "VALIDATION_ERROR"`로 분기 가능.

### 6.2 등록 2가지 방식 — 둘은 같은 일
```python
# 방식 A: 데코레이터 (정의+등록 한자리, "예외 1 : 함수 1"에 적합)
@app.exception_handler(RequestValidationError)
async def h(request, exc): ...

# 방식 B: 함수 등록 (정의 따로 / 등록 따로, "함수 1 : 여러 예외"에 적합)
app.add_exception_handler(BaseAppException, exception_handler)
app.add_exception_handler(RequestValidationError, exception_handler)
```
- 내 설계는 **"핸들러 함수 하나 + `isinstance` 내부 분기"** → 여러 예외를 같은
  함수에 몰아주는 **방식 B(`add_exception_handler`)가 맞음.** (데코레이터 안 쓴 게
  실수가 아니라 설계에 맞는 선택)

### 6.3 try/except vs 에러 핸들러 — 서로 간섭 안 함 (핵심 원리)
> **예외는 가장 가까운 적합한 except에서 잡히고, 거기서 끝. 더 위로 안 올라감.**
- `/live`의 `try/except json.JSONDecodeError`가 **먼저** 잡음 → 처리 끝 →
  에러 핸들러까지 **안 감**. (핸들러가 안 잡는 게 아니라 잡을 기회가 안 옴)
- 에러 핸들러 = **최후의 그물.** 아무 except도 안 잡아서 FastAPI 최상단까지
  올라온 예외만 받음.
- 비유: try/except = 방마다의 소화기(그 방에서 즉시 끔) /
  에러 핸들러 = 건물 전체 스프링클러(방 소화기가 다 실패했을 때 최후 작동).
- 그래서 핸들러의 실제 무대:
  - `RequestValidationError` (검증 실패, 내가 try로 못 감싸는 단계) — **주 무대**
  - `BaseAppException` (내가 `raise`할 때) — 아직 raise 하는 곳 없어 **대기 상태**

### 6.4 `Exception` fallback을 뺀 이유 + mypy 해결
- `Exception`까지 등록하면 **예상 못 한 버그 예외도 500으로 뭉개져** 트레이스백이
  안 보임 → 로컬 학습 단계에선 오히려 디버깅에 불리. 그래서 뺌.
- fallback `return`을 지우니 mypy: `Missing return statement`.
  - 원인: 두 `if`를 다 통과하는 exc가 오면 `return` 없이 `None`으로 끝날 수 있다고
    mypy가 봄 (mypy는 등록 부분까지 안 봐서 "별별 exc가 다 온다"고 가정).
  - 해결: 마지막 줄 `raise exc` — "처리 못 하는 건 도로 던진다"(= 터지게 둠,
    fallback 뺀 결정과 일관). `raise`는 "정상 종료 안 하고 예외로 빠짐"이라
    "return 없이 None" 경로가 사라져 mypy도 만족. (fail fast)

---

## 7. 파이썬 클래스 개념 (`BaseAppException` 뜯어보기)

```python
class BaseAppException(Exception):
    status_code: int = 500                    # ← 클래스 속성 (공유 기본값)
    code: str = "INTERNAL_ERROR"
    detail: str = "서버 에러가 발생했습니다."

    def __init__(self, detail: str | None = None, *, code: str | None = None):
        self.detail = detail if detail is not None else self.detail   # 인자 있으면 그것, 없으면 클래스 기본값
        self.code = code if code is not None else self.code
        super().__init__(self.detail)         # 부모(Exception) 초기화도 챙김
```

- **클래스 속성**: `__init__` 밖·클래스 본문에 적힌 셋. "이 클래스로 만든 모든
  객체가 공유하는 기본값." `self.detail = ... else self.detail`의 오른쪽 `self.detail`이
  인자 없을 때 이 기본값으로 fallback.
- **상속** (`class Base(Exception)`): 부모가 가진 걸 전부 물려받음. `Exception`을
  상속했기에 `raise`가 가능(진짜 예외가 됨). 상속 안 했으면 `raise` 불가.
- **`__init__`**: 객체 생성 시 자동 호출. **안 쓰면 부모 것을 씀.** 근데 `Exception`의
  `__init__`은 위치 인자만 받아 `code=`같은 키워드를 못 받음 → 그래서 오버라이드 필요.
- **오버라이드**: 물려받은 걸 자식에서 새로 써서 덮어씀. 여기선 `__init__`을 덮어
  "`detail`·`code`를 받겠다"로 바꿈.
- **`super().__init__(...)`**: 오버라이드했지만 **부모의 초기화도 불러줌.** `Exception`
  이 `args`를 세팅해야 예외가 문자열/트레이스백에서 제대로 동작. 빼면 반쪽 예외.
- **`*` (keyword-only)**: `def __init__(self, detail=None, *, code=None)`의 `*`는
  "이 뒤 인자는 반드시 키워드로." `code`는 `code="X"`로만 넘길 수 있음(위치 X).

---

## 8. 이번 세션 관통한 감각들

- **덜 하는 게 더 하는 것보다 낫다.** 프레임워크가 공짜로 해주는 걸(int 변환·422)
  손으로 다시 짜지 말 것. `Query(ge=, le=)` 한 줄 > `is_valid` 함수 + if + 에러 생성.
- **fail fast.** 잘못된 입력은 조용히 봐주기(빈 리스트·자동 자르기)보다 **큰소리로
  거절**(422)하는 게 클라이언트에게 친절. 조용히 봐주면 클라이언트가 자기 실수를
  모른 채 "성공(200)"으로 착각 → 디버깅 지옥.
- **정직한 응답은 가진 데이터가 허락하는 만큼만.** "없는 스트리머 vs 방송중 아님"을
  구분하려면 그 데이터(streamers 테이블 등)가 있어야 함. 없는데 아는 척하면 거짓말.
- **판단이 안 서면 지금은 안 건드리는 게 정답일 때가 많다.** (인프라 500 미루기)
- **엔드포인트의 존재 이유를 물으면 검증 수준이 따라 정해진다.** `/chats`는 T4
  부하 실험용 → 그 역할에 `ge=1, le=1000`이면 충분, 그 이상은 과설계.
- **필요가 배움을 끌고 가게 둔다.** 지금 없는 코드(미래의 parts 사용처, 자식 예외)를
  미리 감싸지/만들지 말 것. 필요가 생기면 그때.
- **한 번에 한 가지만.** consumer의 print 정리를 이번 FastAPI 세션에 끌어들이지 않음.

---

## 9. 의도적으로 미뤄둔 것 (다음에 만날 지점)

| 미뤄둔 것 | 언제 |
|-----------|------|
| 인프라 실패(DB/Redis 다운) 500 가공 | 5단계 T5-3 장애 주입 |
| 없는 bid 3-way 구분 (없는 스트리머 vs 방송중 아님 vs 채팅 0) | `streamers` 테이블 생기면 |
| **로거 본격 정리** (print→logger 일괄 전환 + 레벨 매기기 + `basicConfig` 포맷/레벨 + live 로그 메시지 다듬고 실제 값 담기) | 5단계 Observability |
| `Exception` fallback 등록 (트레이스백 노출 막기) | 프로덕션 근접 시 |

> 특히 로거 건은 항목이 여러 개 뭉쳐 있으니 통째로 5단계에서.

---

## 10. 다음 세션 예고
- 테스트 **T4**: Locust로 REST 부하, k6로 WebSocket 부하, **인덱스 유/무 비교**,
  **캐싱 on/off 비교**. (부하 도구 설치 + 측정 방법론 — 성격이 확 달라 별도 세션)