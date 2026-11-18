// 불러오는 중 / 에러 / 안내 문구를 보여 주는 작은 컴포넌트들
import type { ReactNode } from "react";
import { ApiError } from "../api/client";

export function Loading({ label = "불러오는 중…" }: { label?: string }) {
  return (
    <div className="flex items-center justify-center gap-2 py-16 text-sm text-zinc-400">
      <span className="size-4 animate-spin rounded-full border-2 border-zinc-600 border-t-sky-400" />
      {label}
    </div>
  );
}

export function Message({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="flex flex-col items-center gap-3 py-16 text-center">
      <p className="text-lg font-semibold text-zinc-200">{title}</p>
      {children && <div className="text-sm text-zinc-400">{children}</div>}
    </div>
  );
}

/** API 에러를 사람이 읽을 문장으로. 백엔드가 준 detail이 있으면 그걸 쓴다. */
export function errorText(error: unknown): string {
  if (error instanceof ApiError) return error.message;
  return "서버에 연결하지 못했어요. 잠시 뒤 다시 시도해 주세요.";
}

export function ErrorView({ error, children }: { error: unknown; children?: ReactNode }) {
  return <Message title={errorText(error)}>{children}</Message>;
}
