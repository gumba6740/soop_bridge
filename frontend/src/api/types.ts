// API 응답의 모양. 백엔드 pydantic 스키마(jsons/openapi.json)를 TypeScript로 옮긴 것.
//
// 시간 단위는 백엔드와 같다.
//   - total_duration, duration : 밀리초
//   - offset_base_sec, 채팅의 t, start/end : 초
//   - 날짜는 ISO 문자열 (예: "2026-10-23T10:26:26Z")

// ---------------------------------------------------------- station

/** GET /  ,  GET /station/{bid} */
export interface Station {
    bid: string;
    nick: string;
    is_live: boolean;
}

/** 다시보기 목록의 한 줄 (GET /station/{bid}/reviews 의 items) */
export interface VodListItem {
    vod_id: number;
    title: string;
    total_duration: number; // 밀리초
    broad_start: string;
    broad_end: string | null;
    grade: number; // 19 = 성인인증 필요
    media_code: number | null; // 1: 정상, -6303: 잠김
    broad_idx: number | null;
    hashtags: string[];
}

/** GET /station/{bid}/reviews?limit=&cursor= */
export interface VodList {
    items: VodListItem[];
  /** 다음 페이지를 받을 때 cursor로 그대로 넣는다. null이면 마지막 페이지. */
    next_cursor: number | null;
}

// ----------------------------------------------------------- review

export interface VodFile {
    file_order: number;
    duration: number; // 밀리초
    hls_url: string | null;
    /** 이 파트의 0초가 VOD 전체 기준 몇 초인지. 채팅 시간 = offset_base_sec + 파트 안 재생 시간 */
    offset_base_sec: number | null;
}

/** GET /review/{vod_id} */
export interface VodDetail extends VodListItem {
    bid: string;
    nick: string;
    files: VodFile[];
}

/**
 * 채팅 한 건의 data. 키는 type마다 다르다 (docs/review_chat_types.md).
 * 그래서 여기서는 "문자열 키 → 아무 값"으로만 두고,
 * 화면(ChatLine.tsx)에서 type을 보고 필요한 키를 꺼낸다.
 */
export type ChatData = Record<string, unknown>;

/** 다시보기 채팅 한 건 */
export interface ReviewChat {
    type: string;
    t: number; // VOD 전체 기준 초
    data: ChatData;
}

/** GET /review/{vod_id}/chats?start= */
export interface ChatWindow {
    start: number; // 포함
    end: number; // 미포함 (start + 300)
    /** false면 이 구간은 아직 수집 중. 잠시 뒤 다시 요청한다. */
    complete: boolean;
    items: ReviewChat[];
}

// ------------------------------------------------------------- live

/** GET /live/{bid}. 방송 중이 아니면 404. */
export interface LiveDetail {
    bid: string;
    nick: string;
    title: string;
    viewers: number | null;
    tags: string[];
    started_at: string | null;
    viewers_at: string | null;
    embed_url: string; // SOOP 공식 퍼가기 플레이어 주소
}

/** 라이브 채팅 SSE의 chat 이벤트 한 건. id는 SSE의 id 줄(redis 스트림 id). */
export interface LiveChat {
    id: string;
    type: string;
    data: ChatData;
}

// ------------------------------------------------------------ error

/** 에러 응답 본문 (app/core/exc.py) */
export interface ApiErrorBody {
    detail: string;
    code: string;
}