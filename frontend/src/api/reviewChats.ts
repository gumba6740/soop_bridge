// 다시보기 채팅을 "시간 구간" 단위로 받아 두는 곳.
//
// GET /review/{vod_id}/chats?start=S 는 [S, S+300) 초의 채팅을 준다.
// 영상이 t초를 재생 중일 때 필요한 구간은 아래 세 개다.
//
//   ... | 이전 구간 | 지금 구간 (t가 여기) | 다음 구간 | ...
//         ↑ 구간이 막 바뀐 직후에도      ↑ 구간 끝까지 30초 남으면
//           직전 채팅이 보이게              미리 받아 둔다
//
// 구간 시작은 항상 300의 배수(0, 300, 600, ...)로 맞춘다.
// 그래야 같은 구간을 두 번 받지 않고, 받은 구간을 start로 찾아 쓸 수 있다.
//
// complete가 false인 구간은 아직 수집 중이다. RETRY_MS가 지나면 다시 받는다.
// (백엔드는 이 요청을 받으면 수집 워커 큐에 넣는다.)

import { ApiError, getChats } from "./client";
import type { ChatWindow, ReviewChat } from "./types";

/** 한 번에 받는 구간 길이(초). 백엔드 CHAT_WINDOW와 같아야 한다. */
export const CHAT_WINDOW_SEC = 300;
/** 미완성 구간이나 실패한 구간을 다시 받기까지 기다리는 시간 */
const RETRY_MS = 5_000;
/** 구간 끝까지 이만큼(초) 남으면 다음 구간을 미리 받는다 */
const PREFETCH_SEC = 30;

export type WindowStatus = "loading" | "partial" | "error" | "done";

interface Entry {
  window?: ChatWindow; // 마지막으로 받은 응답
  loading: boolean;
  failed: boolean;
  fetchedAt: number; // 마지막 요청이 끝난 시각 (Date.now())
}

/** t초가 들어 있는 구간의 시작 */
export function windowStart(t: number): number {
  return Math.floor(Math.max(0, t) / CHAT_WINDOW_SEC) * CHAT_WINDOW_SEC;
}

export class ReviewChatLoader {
  private entries = new Map<number, Entry>();
  private abort = new AbortController();
  private vodId: number;
  private totalSec: number;
  private onChange: () => void;

  /**
   * @param totalSec VOD 전체 길이(초). 이 뒤로는 요청하지 않는다.
   * @param onChange 구간을 새로 받을 때마다 불린다. 화면을 다시 그리는 데 쓴다.
   */
  constructor(vodId: number, totalSec: number, onChange: () => void) {
    this.vodId = vodId;
    this.totalSec = totalSec;
    this.onChange = onChange;
  }

  /** t초를 보여 주는 데 필요한 구간들을 받아 둔다. 자주 불러도 된다. */
  ensure(t: number): void {
    const cur = windowStart(t);
    const starts = [cur];
    if (cur > 0) starts.push(cur - CHAT_WINDOW_SEC);
    if (t - cur > CHAT_WINDOW_SEC - PREFETCH_SEC) starts.push(cur + CHAT_WINDOW_SEC);

    for (const s of starts) {
      if (s < this.totalSec) void this.load(s);
    }
  }

  /** t초가 들어 있는 구간의 상태 (화면에 "수집 중" 같은 안내를 띄울 때 쓴다) */
  status(t: number): WindowStatus {
    const e = this.entries.get(windowStart(t));
    if (!e || (e.loading && !e.window)) return "loading";
    if (e.failed && !e.window) return "error";
    return e.window?.complete ? "done" : "partial";
  }

  /** t초까지(t 포함) 나온 채팅 중 마지막 limit개. 이전 구간과 지금 구간에서 찾는다. */
  chatsUntil(t: number, limit: number): ReviewChat[] {
    const cur = windowStart(t);
    const result: ReviewChat[] = [];
    for (const s of [cur - CHAT_WINDOW_SEC, cur]) {
      const items = this.entries.get(s)?.window?.items;
      if (!items) continue;
      result.push(...items.slice(0, upperBound(items, t)));
    }
    return result.slice(-limit);
  }

  /** 화면을 떠날 때. 진행 중인 요청을 취소한다. */
  dispose(): void {
    this.abort.abort();
  }

  private async load(start: number): Promise<void> {
    const prev = this.entries.get(start);
    if (prev) {
      if (prev.loading || prev.window?.complete) return; // 받는 중이거나 이미 다 받음
      if (Date.now() - prev.fetchedAt < RETRY_MS) return; // 미완성/실패: 조금 더 기다린다
    }

    const entry: Entry = {
      window: prev?.window,
      loading: true,
      failed: false,
      fetchedAt: prev?.fetchedAt ?? 0,
    };
    this.entries.set(start, entry);

    try {
      const w = await getChats(this.vodId, start, this.abort.signal);
      // 백엔드가 시간순으로 주지만, 아래 이진 탐색이 정렬을 전제로 하므로 한 번 더 맞춘다.
      w.items.sort((a, b) => a.t - b.t);
      entry.window = w;
    } catch (err) {
      if (this.abort.signal.aborted) return;
      entry.failed = true;
      if (!(err instanceof ApiError)) console.warn("채팅 구간 요청 실패", start, err);
    } finally {
      entry.loading = false;
      entry.fetchedAt = Date.now();
    }
    this.onChange();
  }
}

/** 정렬된 items에서 t보다 큰 첫 위치 (= t 이하인 채팅의 개수) */
function upperBound(items: ReviewChat[], t: number): number {
  let lo = 0;
  let hi = items.length;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (items[mid].t <= t) lo = mid + 1;
    else hi = mid;
  }
  return lo;
}