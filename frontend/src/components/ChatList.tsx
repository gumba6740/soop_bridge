// 채팅창: 새 채팅이 오면 맨 아래로 내려간다.
// 사용자가 위로 스크롤해서 지난 채팅을 보고 있으면 따라 내려가지 않고,
// "최신 채팅으로" 버튼을 띄운다.

import { useLayoutEffect, useRef, useState, type ReactNode } from "react";
import type { ChatData } from "../api/types";
import ChatLine from "./ChatLine";

export interface ChatRow {
  type: string;
  data: ChatData;
}

// React가 줄마다 구분할 key. 같은 채팅 객체에는 늘 같은 번호를 준다.
const keys = new WeakMap<object, number>();
let nextKey = 0;
function keyOf(row: object): number {
  let k = keys.get(row);
  if (k === undefined) {
    k = nextKey++;
    keys.set(row, k);
  }
  return k;
}

export default function ChatList({ rows, empty }: { rows: ChatRow[]; empty?: string }) {
  const boxRef = useRef<HTMLDivElement>(null);
  const [stick, setStick] = useState(true); // 맨 아래에 붙어 있는지

  useLayoutEffect(() => {
    const el = boxRef.current;
    if (el && stick) el.scrollTop = el.scrollHeight;
  }, [rows, stick]);

  function onScroll() {
    const el = boxRef.current;
    if (!el) return;
    setStick(el.scrollHeight - el.scrollTop - el.clientHeight < 40);
  }

  return (
    <div className="relative min-h-0 flex-1">
      <div ref={boxRef} onScroll={onScroll} className="thin-scroll absolute inset-0 overflow-y-auto py-2 text-sm">
        {rows.length === 0 && empty && <p className="px-3 py-8 text-center text-xs text-zinc-500">{empty}</p>}
        {rows.map((r) => (
          <ChatLine key={keyOf(r)} type={r.type} data={r.data} />
        ))}
      </div>
      {!stick && (
        <button
          onClick={() => setStick(true)}
          className="absolute bottom-3 left-1/2 -translate-x-1/2 rounded-full bg-sky-500 px-3 py-1 text-xs font-semibold text-zinc-950 shadow-lg"
        >
          최신 채팅으로 ↓
        </button>
      )}
    </div>
  );
}

/** 채팅창 바깥 틀 (제목 줄 + 채팅 목록). 다시보기와 라이브가 같이 쓴다. */
export function ChatPanel({
  title,
  status,
  children,
}: {
  title: string;
  status?: ReactNode;
  children: ReactNode;
}) {
  return (
    <aside className="flex h-[70vh] flex-col overflow-hidden rounded-xl border border-zinc-800 bg-zinc-900 lg:h-[calc(100dvh-7.5rem)] lg:sticky lg:top-20">
      <div className="flex items-center justify-between gap-2 border-b border-zinc-800 px-3 py-2.5">
        <h2 className="text-sm font-semibold">{title}</h2>
        {status}
      </div>
      {children}
    </aside>
  );
}
