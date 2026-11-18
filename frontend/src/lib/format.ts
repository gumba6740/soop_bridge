// 화면에 보여 줄 값 만들기 (시간, 날짜, 숫자)

/** 초 → "1:02:03" 또는 "2:03" */
export function formatClock(totalSec: number): string {
  const s = Math.max(0, Math.floor(totalSec));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  const mm = String(m).padStart(h > 0 ? 2 : 1, "0");
  const ss = String(sec).padStart(2, "0");
  return h > 0 ? `${h}:${mm}:${ss}` : `${mm}:${ss}`;
}

/** 밀리초 → "7시간 46분" */
export function formatDuration(ms: number): string {
  const min = Math.round(ms / 60_000);
  const h = Math.floor(min / 60);
  const m = min % 60;
  if (h === 0) return `${m}분`;
  return m === 0 ? `${h}시간` : `${h}시간 ${m}분`;
}

const dateFmt = new Intl.DateTimeFormat("ko-KR", {
  timeZone: "Asia/Seoul",
  year: "numeric",
  month: "long",
  day: "numeric",
  weekday: "short",
  hour: "2-digit",
  minute: "2-digit",
});

/** ISO 문자열 → "2026년 10월 23일 (금) 오후 07:26" (한국 시간) */
export function formatDate(iso: string): string {
  return dateFmt.format(new Date(iso));
}

/** 시작 시각부터 지금까지 → "3시간 12분째" */
export function formatSince(iso: string, now = Date.now()): string {
  return `${formatDuration(now - new Date(iso).getTime())}째`;
}

/** 8211 → "8,211" */
export function formatNumber(n: number): string {
  return n.toLocaleString("ko-KR");
}
