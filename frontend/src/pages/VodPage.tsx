// VOD 다시보기: 왼쪽 영상, 오른쪽 채팅. 영상 시간에 맞춰 그 시점까지의 채팅을 보여 준다.
//
// 흐름
//   VodPlayer가 재생 시간(VOD 전체 기준 초)을 onTime으로 알려 줌
//   → time 상태가 바뀜
//   → useReplayChats가 그 시간에 필요한 채팅 구간을 받아 두고, time까지의 채팅을 돌려줌
//   → ChatList가 그림

import { useState, useEffect } from "react";
import { Link, useParams } from "react-router";
import { getVod, addView } from "../api/client";
import type { VodDetail } from "../api/types";
import ChatList, { ChatPanel } from "../components/ChatList";
import { ErrorView, Loading, Message } from "../components/Status";
import VodPlayer from "../components/VodPlayer";
import { useFetch } from "../hooks/useFetch";
import { useReplayChats } from "../hooks/useReplayChats";
import { formatClock, formatDate, formatDuration } from "../lib/format";
import { isAdmin } from "../lib/admin";

export default function VodPage() {
  const vodId = Number(useParams().vodId);
  const { data, error, loading } = useFetch((signal) => getVod(vodId, signal), [vodId]);

  if (!Number.isInteger(vodId)) return <Message title="잘못된 VOD 주소예요" />;
  if (loading) return <Loading />;
  if (error || !data) return <ErrorView error={error} />;
  // key: 다른 VOD로 넘어가면 재생 시간 등 상태를 처음부터 다시 시작
  return <VodView key={data.vod_id} vod={data} />;
}

function VodView({ vod }: { vod: VodDetail }) {
  const [time, setTime] = useState(0);
  const { chats, status } = useReplayChats(vod.vod_id, vod.total_duration / 1000, time);
  const [admin] = useState(isAdmin);
  // 처음엔 상세 응답의 조회수, 조회수 요청이 성공하면 올라간 값으로 바꾼다
  const [views, setViews] = useState(vod.views);

  // 페이지를 열 때마다 조회수 +1. 관리자 브라우저는 보내지 않는다.
  // 개발 모드(StrictMode)에서는 effect가 두 번 돌아서 2씩 오른다. 배포 빌드는 1.
  useEffect(() => {
    if (admin) return;
    addView(vod.vod_id)
      .then((res) => setViews(res.views))
      .catch(() => {}); // 조회수 실패는 화면에 알리지 않는다
  }, [vod.vod_id, admin]);

  const statusText = {
    loading: <span className="text-xs text-zinc-500">불러오는 중…</span>,
    partial: <span className="text-xs text-amber-300">이 구간 수집 중</span>,
    error: <span className="text-xs text-red-300">불러오기 실패</span>,
    done: <span className="font-mono text-xs text-zinc-500">{formatClock(time)}</span>,
  }[status];

  return (
    <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_360px]">
      <div className="min-w-0">
        <VodPlayer vod={vod} onTime={setTime} />

        <div className="mt-4">
          <h1 className="text-xl font-bold leading-snug">{vod.title}</h1>
          <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-sm text-zinc-400">
            <Link to={`/station/${vod.bid}`} className="font-semibold text-zinc-200 hover:underline">
              {vod.nick}
            </Link>
            <span>{formatDate(vod.broad_start)}</span>
            <span>{formatDuration(vod.total_duration)}</span>
            <span>조회 {views.toLocaleString()}</span>
            {admin && <span className="text-xs text-amber-300">관리자</span>}
          </div>
          {vod.hashtags.length > 0 && (
            <div className="mt-3 flex flex-wrap gap-1.5">
              {vod.hashtags.map((tag) => (
                <span key={tag} className="rounded-full bg-zinc-800 px-2.5 py-0.5 text-xs text-zinc-300">
                  #{tag}
                </span>
              ))}
            </div>
          )}
        </div>
      </div>

      <ChatPanel title="채팅 다시보기" status={statusText}>
        {status === "partial" && (
          <p className="border-b border-zinc-800 bg-amber-400/10 px-3 py-2 text-xs text-amber-200">
            이 시간대 채팅은 아직 모으는 중이에요. 잠시 뒤 자동으로 다시 불러옵니다.
          </p>
        )}
        <ChatList
          rows={chats}
          empty={status === "loading" ? "채팅을 불러오는 중…" : "아직 채팅이 없어요. 영상을 재생해 보세요."}
        />
      </ChatPanel>
    </div>
  );
}
