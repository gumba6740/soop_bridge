// 홈: 스트리머 목록. 방송 중인 스트리머를 앞에 둔다.
import { Link } from "react-router";
import { listStations } from "../api/client";
import StationAvatar, { LiveBadge } from "../components/StationAvatar";
import { ErrorView, Loading, Message } from "../components/Status";
import { useFetch } from "../hooks/useFetch";

export default function HomePage() {
  const { data, error, loading } = useFetch((signal) => listStations(signal), []);

  if (loading) return <Loading />;
  if (error) return <ErrorView error={error} />;
  if (!data || data.length === 0) return <Message title="등록된 스트리머가 없어요" />;

  // 방송 중인 스트리머 먼저. sort는 원래 순서(닉네임순)를 유지한다.
  const stations = [...data].sort((a, b) => Number(b.is_live) - Number(a.is_live));
  const liveCount = data.filter((s) => s.is_live).length;

  return (
    <div>
      <div className="mb-6 flex items-end justify-between">
        <div>
          <h1 className="text-2xl font-bold">스트리머</h1>
          <p className="mt-1 text-sm text-zinc-400">
            {data.length}명 · 방송 중 {liveCount}명
          </p>
        </div>
      </div>

      <ul className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {stations.map((s) => (
          <li key={s.bid}>
            <Link
              to={`/station/${s.bid}`}
              className="flex items-center gap-4 rounded-xl border border-zinc-800 bg-zinc-900 p-4 transition hover:border-zinc-600 hover:bg-zinc-800/60"
            >
              <StationAvatar bid={s.bid} nick={s.nick} live={s.is_live} />
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2">
                  <span className="truncate font-semibold">{s.nick}</span>
                  {s.is_live && <LiveBadge />}
                </div>
                <p className="truncate text-sm text-zinc-500">@{s.bid}</p>
              </div>
            </Link>
          </li>
        ))}
      </ul>
    </div>
  );
}
