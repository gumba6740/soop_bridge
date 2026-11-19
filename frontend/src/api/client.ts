// REST API를 부르는 함수 모음.
// 화면 코드는 fetch를 직접 쓰지 않고 여기 있는 함수만 부른다.

// 요청 주소 = API_BASE + 경로
//   개발: API_BASE = "/api" → Vite 프록시가 /api를 떼고 FastAPI로 넘김
//   배포: API_BASE = VITE_API_BASE → nginx → FastAPI

import type {
  ApiErrorBody,
  ChatWindow,
  LiveDetail,
  Station,
  VodDetail,
  ViewCount,
  VodList,
} from "./types";

export const API_BASE: string = import.meta.env.VITE_API_BASE ?? "/api";

/** 2xx가 아닌 응답. status와 백엔드가 보낸 code("VOD_NOT_FOUND" 등)를 담는다. */
export class ApiError extends Error {
  status: number;
  code: string;

  constructor(status: number, code: string, detail: string) {
    super(detail);
    this.status = status;
    this.code = code;
  }
}

/** 요청을 보내고 JSON을 돌려준다. 실패하면 ApiError를 던진다. */
async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const res = await fetch(API_BASE + path, init);

  if (!res.ok) {
    // 백엔드 에러 응답은 {"detail": "...", "code": "..."} 모양.
    // nginx 502처럼 JSON이 아닌 응답도 올 수 있어서 실패하면 기본값을 쓴다.
    const body = (await res.json().catch(() => null)) as ApiErrorBody | null;
    throw new ApiError(
      res.status,
      body?.code ?? "HTTP_ERROR",
      body?.detail ?? `요청 실패 (${res.status})`,
    );
  }
  return (await res.json()) as T;
}

function get<T>(path: string, signal?: AbortSignal): Promise<T> {
  return request<T>(path, { signal });
}

/** 본문 없는 POST. 본문이 없어서 CORS 사전 요청(OPTIONS)도 생기지 않는다. */
function post<T>(path: string, signal?: AbortSignal): Promise<T> {
  return request<T>(path, { method: "POST", signal });
}

// ---------------------------------------------------------- station

/** 홈: 스트리머 목록(닉네임순)과 방송 중 여부 */
export function listStations(signal?: AbortSignal) {
  return get<Station[]>("/", signal);
}

/** 방송국 정보. 등록되지 않은 bid면 404 (STREAMER_NOT_FOUND) */
export function getStation(bid: string, signal?: AbortSignal) {
  return get<Station>(`/station/${encodeURIComponent(bid)}`, signal);
}

/** 다시보기 목록(최신순). 다음 페이지는 이전 응답의 next_cursor를 cursor로 넘긴다. */
export function listReviews(
  bid: string,
  { limit = 20, cursor }: { limit?: number; cursor?: number | null } = {},
  signal?: AbortSignal,
) {
  const q = new URLSearchParams({ limit: String(limit) });
  if (cursor != null) q.set("cursor", String(cursor));
  return get<VodList>(`/station/${encodeURIComponent(bid)}/reviews?${q}`, signal);
}

// ----------------------------------------------------------- review

/** VOD 정보와 파트(파일) 목록. 없으면 404 (VOD_NOT_FOUND) */
export function getVod(vodId: number, signal?: AbortSignal) {
  return get<VodDetail>(`/review/${vodId}`, signal);
}

/** 조회수 +1. 올린 뒤의 조회수를 돌려준다. 없는 VOD면 404 (VOD_NOT_FOUND) */
export function addView(vodId: number, signal?: AbortSignal) {
  return post<ViewCount>(`/review/${vodId}/view`, signal);
}

/**
 * [start, start + 300) 초 구간의 채팅.
 * 구간을 어떻게 나눠 부르는지는 reviewChats.ts 참고.
 */
export function getChats(vodId: number, start: number, signal?: AbortSignal) {
  return get<ChatWindow>(`/review/${vodId}/chats?start=${start}`, signal);
}

// ------------------------------------------------------------- live

/** 방송 현황. 방송 중이 아니면 404 (LIVE_NOT_FOUND) */
export function getLive(bid: string, signal?: AbortSignal) {
  return get<LiveDetail>(`/live/${encodeURIComponent(bid)}`, signal);
}

// 라이브 채팅(SSE)은 liveChats.ts