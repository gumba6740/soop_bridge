// 스트리머 상세: 방송 중이면 라이브로 가는 배너, 그 아래 다시보기(VOD) 목록.
// VOD 목록은 20개씩 받고, "더 보기"를 누르면 next_cursor로 다음 20개를 받는다.

import { useEffect, useState } from "react";
import { Link, useParams } from "react-router";
import { getStation, listReviews } from "../api/client";
import type { VodListItem } from "../api/types";
import StationAvatar, { LiveBadge } from "../components/StationAvatar";
import { ErrorView, Loading, errorText } from "../components/Status";
import { useFetch } from "../hooks/useFetch";
import { formatDate, formatDuration } from "../lib/format";

export default function StationPage() {
  const { bid = "" } = useParams();
  const station = useFetch((signal) => getStation(bid, signal), [bid]);

  if (station.loading) return <Loading />;
  if (station.error || !station.data) return <ErrorView error={station.error} />;
  const s = station.data;

  return (
    <div className="mx-auto max-w-4xl">
      <div className="mb-6 flex items-center gap-4">
        <StationAvatar bid={s.bid} nick={s.nick} live={s.is_live} size="lg" />
        <div>
          <h1 className="text-2xl font-bold">{s.nick}</h1>
          <p className="text-sm text-zinc-500">@{s.bid}</p>
        </div>
      </div>

      {s.is_live && (
        <Link
          to={`/live/${s.bid}`}
          className="mb-8 flex items-center justify-between rounded-xl border border-red-500/40 bg-red-500/10 px-5 py-4 transition hover:bg-red-500/20"
        >
          <span className="flex items-center gap-3 font-semibold">
            <LiveBadge /> 지금 방송 중이에요
          </span>
          <span className="text-sm text-red-300">라이브 보기 →</span>
        </Link>
      )}

      <h2 className="mb-3 text-lg font-semibold">다시보기</h2>
      <VodList bid={bid} />
    </div>
  );
}

function VodList({ bid }: { bid: string }) {
  const [items, setItems] = useState<VodListItem[]>([]);
  const [cursor, setCursor] = useState<number | null>(null); // 다음 페이지 cursor
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<unknown>(null);

  async function load(from: number | null, signal?: AbortSignal) {
    setLoading(true);
    setError(null);
    try {
      const page = await listReviews(bid, { cursor: from }, signal);
      setItems((prev) => (from == null ? page.items : [...prev, ...page.items]));
      setCursor(page.next_cursor);
    } catch (e) {
      if (!signal?.aborted) setError(e);
    } finally {
      if (!signal?.aborted) setLoading(false);
    }
  }

  // 첫 페이지
  useEffect(() => {
    const ctrl = new AbortController();
    void load(null, ctrl.signal);
    return () => ctrl.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [bid]);

  if (!loading && !error && items.length === 0) {
    return <p className="py-10 text-center text-sm text-zinc-500">저장된 다시보기가 없어요.</p>;
  }

  return (
    <div>
      <ul className="divide-y divide-zinc-800 overflow-hidden rounded-xl border border-zinc-800 bg-zinc-900">
        {items.map((v) => (
          <li key={v.vod_id}>
            <VodRow vod={v} />
          </li>
        ))}
      </ul>

      <div className="mt-4 flex justify-center">
        {loading ? (
          <Loading />
        ) : error ? (
          <div className="text-center text-sm text-red-300">
            {errorText(error)}{" "}
            <button onClick={() => load(items.length ? cursor : null)} className="underline">
              다시 시도
            </button>
          </div>
        ) : cursor != null ? (
          <button
            onClick={() => load(cursor)}
            className="rounded-md bg-zinc-800 px-4 py-2 text-sm hover:bg-zinc-700"
          >
            더 보기
          </button>
        ) : null}
      </div>
    </div>
  );
}

function VodRow({ vod }: { vod: VodListItem }) {
  return (
    <Link to={`/vod/${vod.vod_id}`} className="block px-4 py-3.5 transition hover:bg-zinc-800/60">
      <div className="flex items-start justify-between gap-4">
        <p className="font-medium leading-snug">{vod.title}</p>
        <span className="shrink-0 font-mono text-xs text-zinc-400">{formatDuration(vod.total_duration)}</span>
      </div>
      <div className="mt-1.5 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-zinc-500">
        <span>{formatDate(vod.broad_start)}</span>
        {vod.grade === 19 && <span className="rounded bg-rose-500/20 px-1.5 text-rose-300">19</span>}
        {vod.media_code !== 1 && <span className="rounded bg-zinc-700 px-1.5 text-zinc-300">잠김</span>}
        {vod.hashtags.slice(0, 5).map((tag) => (
          <span key={tag} className="text-zinc-500">
            #{tag}
          </span>
        ))}
      </div>
    </Link>
  );
}
