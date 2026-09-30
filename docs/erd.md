# ERD

테이블 정의의 주인은 `sqls/schemas.sql`이다. ORM 모델(`app/models.py`)은 같은 구조를 비춘다.

```mermaid
erDiagram
    vods ||--o{ vod_files : "vod_id, ON DELETE CASCADE"
    vods ||--o{ chats : "vod_id, ON DELETE CASCADE"
    vod_files ||--o| chat_progress : "(vod_id, file_order), ON DELETE CASCADE"
    vods |o..o| vod_fetch_failures : "vod_id, 외래키 없음"

    vods {
        bigint vod_id PK
        text bid "스트리머 ID"
        text title
        bigint total_duration "밀리초"
        timestamptz broad_start
        timestamptz broad_end "nullable"
        smallint grade "19 = 성인인증"
        boolean permanence "nullable"
        timestamptz auto_delete_date "nullable"
        integer chat_duration "초, 채팅 조회 단위"
        integer media_code "nullable"
        smallint auth_no "nullable"
        bigint broad_idx "nullable, 방송 세션 번호"
        text[] hashtags
        integer view_cnt "nullable"
        integer live_total_view "nullable"
        timestamptz inserted_at
    }

    vod_files {
        bigint vod_id PK, FK
        smallint file_order PK "연속 아님"
        bigint duration "밀리초"
        text chat_url "nullable"
        text hls_url "nullable"
        timestamptz file_start "nullable"
    }

    chat_progress {
        bigint vod_id PK, FK
        smallint file_order PK, FK
        integer last_start_time "파트 내 기준 초"
        boolean done
        timestamptz updated_at
    }

    chats {
        bigint id PK "GENERATED ALWAYS AS IDENTITY"
        bigint vod_id FK
        smallint file_order "외래키 아님"
        text type "chat, ogq, balloon 등"
        float8 time_offset "VOD 전체 기준 초"
        text[] parts "docs/chat_types.md 참고"
    }

    vod_fetch_failures {
        bigint vod_id PK
        text bid
        text reason
        boolean permanent
        integer attempts
        timestamptz first_seen_at
        timestamptz last_tried_at
        timestamptz resolved_at "nullable, NULL이면 미해결"
    }
```

## 관계 설명

- **vods → vod_files**: VOD 하나에 파트 여러 개. `file_order`는 19금 파트 누락 등으로 연속이 아닐 수 있다.
- **vods → chats**: VOD 하나에 채팅 여러 개. `chats.file_order`는 구간 단위 재수집(DELETE 후 INSERT)에 쓰는 값이고, `vod_files`를 가리키는 외래키는 걸려 있지 않다.
- **vod_files → chat_progress**: 파트 하나에 진행 기록 최대 하나. 채팅 수집을 시작한 파트에만 행이 생긴다. 두 컬럼을 묶은 복합 외래키다.
- **vods ↔ vod_fetch_failures**: 같은 `vod_id`를 쓰지만 외래키가 없다(점선). 상세 조회에 실패한 VOD는 vods에 없을 수 있기 때문이다.

모든 외래키는 `ON DELETE CASCADE`라서 vods의 행을 지우면 파트, 진행 기록, 채팅이 함께 지워진다.