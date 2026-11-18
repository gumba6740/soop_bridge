// 라이브 채팅 SSE (GET /live/{bid}/chats).
//
// SSE(Server-Sent Events)는 서버가 연결을 열어 둔 채로 이벤트를 계속 흘려보내는 방식이다.
// 브라우저에는 EventSource가 내장되어 있어서, 줄 단위 파싱과 재연결을 대신 해 준다.
//
// 서버가 보내는 것
//   event: chat   id: <redis 스트림 id>   data: {"type": ..., "data": {...}}
//                 처음 연결하면 최근 10개를 먼저 보낸다.
//   event: end    방송 종료. 서버가 연결을 닫는다.
//   : ping        15초마다. ':'로 시작하는 줄은 주석이라 EventSource가 무시한다.
//
// 주의할 점
//   - EventSource는 연결이 끊기면 스스로 다시 연결한다. 방송이 끝나 서버가 닫았을 때도
//     다시 연결하려 하므로, end를 받으면 직접 close() 해야 한다.
//   - 다시 연결하면 서버가 최근 10개를 또 보낸다. 받은 id를 기억해 두고 겹치는 건 버린다.
//   - 방송 중이 아니면 서버가 연결 전에 404를 준다. 이때 EventSource는 재연결을 포기하고
//     readyState가 CLOSED가 된다.

import { API_BASE } from "./client";
import type { ChatData, LiveChat } from "./types";

export type LiveChatState =
  | "connecting" // 처음 연결 중
  | "open" // 받는 중
  | "reconnecting" // 끊겨서 EventSource가 다시 연결하는 중
  | "ended" // 서버가 end를 보냄 (방송 종료)
  | "closed"; // 연결 실패 (404 등). 다시 연결하지 않는다

export interface LiveChatHandlers {
  onChat: (chat: LiveChat) => void;
  onState: (state: LiveChatState) => void;
}

/** 연결을 열고, 닫는 함수를 돌려준다. */
export function openLiveChats(bid: string, h: LiveChatHandlers): () => void {
  const es = new EventSource(`${API_BASE}/live/${encodeURIComponent(bid)}/chats`);
  const seen = new Set<string>(); // 받은 id (재연결 때 겹치는 것 거르기)

  h.onState("connecting");

  es.onopen = () => h.onState("open");

  es.addEventListener("chat", (e: MessageEvent<string>) => {
    if (e.lastEventId && seen.has(e.lastEventId)) return;
    seen.add(e.lastEventId);
    if (seen.size > 2000) seen.clear(); // 오래 켜 둬도 메모리가 계속 늘지 않게

    const body = JSON.parse(e.data) as { type: string; data: ChatData };
    h.onChat({ id: e.lastEventId, type: body.type, data: body.data });
  });

  es.addEventListener("end", () => {
    es.close();
    h.onState("ended");
  });

  es.onerror = () => {
    // CONNECTING: 브라우저가 다시 연결하는 중 / CLOSED: 포기함 (404 등)
    h.onState(es.readyState === EventSource.CLOSED ? "closed" : "reconnecting");
  };

  return () => es.close();
}