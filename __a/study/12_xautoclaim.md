# 학습노트 12 — consumer 구조 복기 & xautoclaim

## 데이터 흐름 복기

```
SOOP WS → get_soop_chats (raw bytes, \x0c split)
       → producer (서비스코드 [1:]로 제거, bytes→str, received_at 부착)
       → Redis Stream XADD (bid, parts(JSON), received_at)
       → consumer xreadgroup → 버퍼 → 배치 INSERT → commit → XACK
       → chats 테이블 (parts는 text[], JSON 아님)
```

- `received_at`은 producer가 `datetime.now(UTC)`로 찍는다. SOOP 패킷에 타임스탬프가 없어서 이게 유일한 방법.
- `inserted_at`은 DB default now(). `inserted_at - received_at` = 큐 체류 시간.

## parts 인덱스

producer가 `[1:]`로 서비스코드를 잘라내기 때문에 인덱스가 한 칸씩 당겨진다.

| 필드 | 파이썬 (0-based) | PostgreSQL (1-based) |
|------|-----------------|---------------------|
| message | parts[0] | parts[1] |
| user_id | parts[1] | parts[2] |
| nickname | parts[5] | parts[6] |

## SOOP 프로토콜 프레임

- `\x1b`(ESC) 계열: UUID 슬롯 없음. 내 앱이 쓰는 포맷.
- `\x1d` 계열: UUID 슬롯 있음. 브라우저가 쓰는 포맷.
- 서버는 클라이언트의 첫 바이트를 보고 같은 포맷으로 응답한다.
- 일반 채팅 패킷(`0005`)에 메시지 ID는 없다 → 중복 제거 키 확보 불가.

## 중복 vs 유실 — 설계 결정

- **유실 방지**: commit → XACK 순서 + xautoclaim으로 죽은 워커의 pending 회수.
- **중복 허용**: SOOP이 메시지 ID를 주지 않아 exactly-once 불가. at-least-once를 명시적 설계 결정으로 선언.
- 중복이 발생하는 조건: commit 성공 후 XACK 전 크래시. 확률은 극히 낮음.

## xautoclaim

죽은 consumer가 가져간 채 XACK 안 한 메시지를 다른 consumer가 회수하는 명령.

```python
_, claimed, _ = await r.xautoclaim(
    stream_key, GROUP, consumer,
    min_idle_time=10000,  # 10초 이상 방치된 것만
    start_id="0-0",
    count=FLUSH_SIZE,
)
```

- 반환값: (다음 스캔 커서, 회수된 메시지 리스트, 삭제된 메시지 ID 리스트)
- RECLAIM_INTERVAL(10초)마다 호출. 조건문 비용은 나노초라 매 루프 체크해도 무방.
- 벤치마크 worker.py에는 있었지만 app consumer에는 없었음 → 추가 완료.

## xreadgroup 동작

- `count=200`: 최대 200개까지 가져옴. 채울 때까지 기다리지 않음.
- `block=1000`: 스트림이 **비었을 때만** 최대 1초 대기. 하나라도 있으면 즉시 반환.
- 진짜 배치 효과는 xreadgroup이 아니라 `isflushready` 버퍼 로직이 만든다.

## 멀티로우 INSERT

```python
# SQL 쪽: 괄호로 행 구분
VALUES (%s, %s, %s), (%s, %s, %s), (%s, %s, %s)

# 값 쪽: 1차원 리스트로 펴서 전달
flat = [x for row in rows for x in row]
```

## monotonic()

- 컴퓨터 부팅 시점 기준으로 단조 증가하는 초 단위 시각을 돌려주는 함수.
- 시계 동기화(NTP)에 영향 안 받아서 경과 시간 측정에 적합.
- 시작값은 부팅마다 다름. 절대값이 아니라 차이만 의미 있음.
- 호출 비용은 나노초. 루프에서 여러 번 불러도 무방.

## 성능 — 왕복이 핵심

- Redis 명령 처리: 마이크로초. 네트워크 왕복: 0.05~0.1ms. **왕복이 처리보다 100배 비쌈.**
- DB도 마찬가지. 멀티로우 INSERT 29.5배 향상의 본질은 왕복 199번 제거.
- 최적화 습관: **"루프 안에서 왕복이 몇 번 나가나"를 세는 것.**
- broadcast_stats: 루프당 왕복 400번 → pipeline으로 1번으로 줄일 수 있음 (다음 작업).