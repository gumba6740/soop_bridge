import { Link, Outlet } from "react-router";

export default function Layout() {
  return (
    <div className="min-h-dvh">
      <header className="sticky top-0 z-10 border-b border-zinc-800 bg-zinc-950/90 backdrop-blur">
        <div className="mx-auto flex h-14 max-w-7xl items-center gap-3 px-4">
          <Link to="/" className="flex items-center gap-2 font-bold tracking-tight">
            <span className="grid size-7 place-items-center rounded-md bg-sky-500 text-sm text-zinc-950">
              S
            </span>
            SOOP Bridge
          </Link>
          <span className="hidden text-sm text-zinc-500 sm:inline">
            다시보기 채팅 · 실시간 채팅 모아 보기
          </span>
        </div>
      </header>
      <main className="mx-auto max-w-7xl px-4 py-6">
        <Outlet />
      </main>
    </div>
  );
}
