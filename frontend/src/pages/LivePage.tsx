// 라이브: 왼쪽 SOOP 퍼가기 영상, 오른쪽 실시간 채팅(SSE).
//
// 방송 정보(GET /live/{bid})를 먼저 받아서 방송 중인지 확인하고, 그다음 채팅을 연결한다.
// 시청자 수는 1분마다 다시 받는다.

import { useEffect, useState } from "react";
import { Link, useParams } from "react-router";
import { ApiError, getLive } from "../api/client";
import type { LiveChatState } from "../api/liveChats";
import type { LiveDetail } from "../api/types";
import ChatList, { ChatPanel } from "../components/ChatList";
import { LiveBadge } from "../components/StationAvatar";
import { ErrorView, Loading, Message } from "../components/Status";
import { useFetch } from "../hooks/useFetch";
import { useLiveChats } from "../hooks/useLiveChats";
import { formatNumber, formatSince } from "../lib/format";

const REFRESH_MS = 60_000;

export default function LivePage() {
  const { bid = "" } = useParams();
  const first = useFetch((signal) => getLive(bid, signal), [bid]);

  if (first.loading) return <Loading />;
  if (first.error instanceof ApiError && first.error.code === "LIVE_NOT_FOUND") {
    return <Offline bid={bid} />;
  }
  if (first.error || !first.data) return <ErrorView error={first.error} />;
  return <LiveView key={bid} initial={first.data} />;
}

function Offline({ bid, ended = false }: { bid: string; ended?: boolean }) {
  return (
    <Message title={ended ? "방송이 끝났어요" : "지금은 방송 중이 아니에요"}>
      <Link to={`/station/${bid}`} className="text-sky-400 hover:underline">
        다시보기 목록으로 →
      </Link>
    </Message>
  );
}

function LiveView({ initial }: { initial: LiveDetail }) {
  const [live, setLive] = useState(initial);
  const [ended, setEnded] = useState(false);
  const { chats, state } = useLiveChats(initial.bid, !ended);

  // 채팅 SSE가 end를 보내면 방송 종료
  useEffect(() => {
    if (state === "ended") setEnded(true);
  }, [state]);

  // 방송 정보(시청자 수 등)를 주기적으로 다시 받는다. 404면 방송이 끝난 것.
  useEffect(() => {
    if (ended) return;
    const id = setInterval(() => {
      getLive(initial.bid).then(setLive, (e) => {
        if (e instanceof ApiError && e.code === "LIVE_NOT_FOUND") setEnded(true);
      });
    }, REFRESH_MS);
    return () => clearInterval(id);
  }, [initial.bid, ended]);

  if (ended) return <Offline bid={initial.bid} ended />;

  return (
    <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_360px]">
      <div className="min-w-0">
        <div className="aspect-video w-full overflow-hidden rounded-xl bg-black">
          <iframe
            src={live.embed_url}
            title={`${live.nick} 라이브`}
            className="size-full"
            allow="autoplay; fullscreen; picture-in-picture"
          />
        </div>

        <div className="mt-4">
          <div className="flex items-center gap-2">
            <LiveBadge />
            <h1 className="text-xl font-bold leading-snug">{live.title}</h1>
          </div>
          <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-sm text-zinc-400">
            <Link to={`/station/${live.bid}`} className="font-semibold text-zinc-200 hover:underline">
              {live.nick}
            </Link>
            {live.viewers != null && <span>시청자 {formatNumber(live.viewers)}명</span>}
            {live.started_at && <span>{formatSince(live.started_at)}</span>}
          </div>
          {live.tags.length > 0 && (
            <div className="mt-3 flex flex-wrap gap-1.5">
              {live.tags.map((tag) => (
                <span key={tag} className="rounded-full bg-zinc-800 px-2.5 py-0.5 text-xs text-zinc-300">
                  {tag}
                </span>
              ))}
            </div>
          )}
        </div>
      </div>

      <ChatPanel title="실시간 채팅" status={<StateDot state={state} />}>
        <ChatList rows={chats} empty="채팅을 기다리는 중…" />
      </ChatPanel>
    </div>
  );
}

function StateDot({ state }: { state: LiveChatState }) {
  const map: Record<LiveChatState, [string, string]> = {
    connecting: ["bg-zinc-500", "연결 중"],
    open: ["bg-emerald-400", "연결됨"],
    reconnecting: ["bg-amber-400", "다시 연결 중"],
    ended: ["bg-zinc-500", "방송 종료"],
    closed: ["bg-red-500", "연결 실패"],
  };
  const [color, label] = map[state];
  return (
    <span className="flex items-center gap-1.5 text-xs text-zinc-400">
      <span className={`size-2 rounded-full ${color}`} />
      {label}
    </span>
  );
}
