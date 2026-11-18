import { useEffect, useState } from "react";
import { openLiveChats, type LiveChatState } from "../api/liveChats";
import type { LiveChat } from "../api/types";

const MAX_LINES = 200;
/** 채팅이 몰릴 때 한 건마다 다시 그리지 않고, 이 간격으로 모아서 반영한다 */
const FLUSH_MS = 150;

/** 라이브 채팅을 받아서 최근 MAX_LINES개를 들고 있는다. enabled가 false면 연결하지 않는다. */
export function useLiveChats(bid: string, enabled: boolean) {
  const [chats, setChats] = useState<LiveChat[]>([]);
  const [state, setState] = useState<LiveChatState>("connecting");

  useEffect(() => {
    if (!enabled) return;
    setChats([]);

    let buffer: LiveChat[] = [];
    const timer = setInterval(() => {
      if (buffer.length === 0) return;
      const incoming = buffer;
      buffer = [];
      setChats((prev) => [...prev, ...incoming].slice(-MAX_LINES));
    }, FLUSH_MS);

    const close = openLiveChats(bid, {
      onChat: (c) => buffer.push(c),
      onState: setState,
    });

    return () => {
      close();
      clearInterval(timer);
    };
  }, [bid, enabled]);

  return { chats, state };
}
