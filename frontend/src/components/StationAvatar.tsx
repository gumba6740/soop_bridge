// 스트리머 프로필 자리. API에 프로필 이미지가 없어서 닉네임 첫 글자로 그린다.

const COLORS = ["bg-sky-600", "bg-emerald-600", "bg-amber-600", "bg-rose-600", "bg-violet-600", "bg-teal-600"];

export default function StationAvatar({
  bid,
  nick,
  live = false,
  size = "md",
}: {
  bid: string;
  nick: string;
  live?: boolean;
  size?: "md" | "lg";
}) {
  // bid로 색을 골라서 같은 스트리머는 늘 같은 색이 되게 한다.
  const color = COLORS[[...bid].reduce((sum, ch) => sum + ch.charCodeAt(0), 0) % COLORS.length];
  const sizing = size === "lg" ? "size-16 text-2xl" : "size-12 text-lg";
  return (
    <div
      className={`grid shrink-0 place-items-center rounded-full font-bold text-white ${color} ${sizing} ${
        live ? "ring-2 ring-red-500 ring-offset-2 ring-offset-zinc-950" : ""
      }`}
    >
      {[...nick][0] ?? "?"}
    </div>
  );
}

export function LiveBadge() {
  return (
    <span className="inline-flex items-center gap-1 rounded bg-red-600 px-1.5 py-0.5 text-[11px] font-bold text-white">
      <span className="size-1.5 animate-pulse rounded-full bg-white" />
      LIVE
    </span>
  );
}
