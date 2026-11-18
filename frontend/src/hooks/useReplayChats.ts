import { useEffect, useMemo, useReducer, useRef, useState } from "react";
import { ReviewChatLoader, type WindowStatus } from "../api/reviewChats";
import type { ReviewChat } from "../api/types";

/** 채팅창에 한 번에 보여 주는 최대 줄 수 */
const MAX_LINES = 150;

/**
 * 다시보기 채팅: 영상의 현재 시간(time, VOD 전체 기준 초)까지 나온 채팅을 돌려준다.
 * 어느 구간을 언제 받을지는 ReviewChatLoader(api/reviewChats.ts)가 정한다.
 */
export function useReplayChats(
  vodId: number,
  totalSec: number,
  time: number,
): { chats: ReviewChat[]; status: WindowStatus } {
  // 구간을 새로 받을 때마다 version을 올려서 화면을 다시 그린다.
  const [version, bump] = useReducer((n: number) => n + 1, 0);
  const [loader, setLoader] = useState<ReviewChatLoader | null>(null);

  // VOD가 바뀌면 로더를 새로 만들고, 화면을 떠나면 요청을 취소한다.
  useEffect(() => {
    const l = new ReviewChatLoader(vodId, totalSec, bump);
    setLoader(l);
    return () => l.dispose();
  }, [vodId, totalSec]);

  // 재생 시간이 바뀔 때마다 필요한 구간을 확인한다.
  useEffect(() => {
    loader?.ensure(time);
  }, [loader, time]);

  // 일시정지 중이어도 "수집 중" 구간을 다시 받을 수 있게 주기적으로 확인한다.
  const timeRef = useRef(time);
  timeRef.current = time;
  useEffect(() => {
    if (!loader) return;
    const id = setInterval(() => loader.ensure(timeRef.current), 2_000);
    return () => clearInterval(id);
  }, [loader]);

  // 시간을 0.25초 단위로 끊어서 계산 횟수를 줄인다.
  const tick = Math.floor(time * 4) / 4;
  const chats = useMemo(
    () => loader?.chatsUntil(tick, MAX_LINES) ?? [],
    // version: 새 구간이 도착했을 때도 다시 계산
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [loader, tick, version],
  );

  return { chats, status: loader?.status(time) ?? "loading" };
}
