// 채팅 한 건을 그린다. 다시보기와 라이브가 같이 쓴다.
//
// data의 키는 type마다 다르다 (docs/review_chat_types.md).
// 지금은 임시 디자인: 일반 채팅 / 후원·구독(강조 상자) / 알림(회색 한 줄) 세 가지로 나눴다.
// 모르는 type이나 화면에 안 보이는 type(challenge_mission_settle)은 그리지 않는다.

import type { ReactNode } from "react";
import type { ChatData } from "../api/types";
import { formatNumber } from "../lib/format";

// ------------------------------------------------- data에서 값 꺼내기

function str(d: ChatData, key: string): string {
  const v = d[key];
  return typeof v === "string" ? v : "";
}

function num(d: ChatData, key: string): number {
  const v = d[key];
  return typeof v === "number" ? v : 0;
}

function nick(d: ChatData, key = "user_nick"): string {
  return str(d, key) || "익명";
}

// ---------------------------------------------------- 배지와 구독

const BADGES: Record<string, { label: string; className: string }> = {
  streamer: { label: "방송", className: "bg-sky-500 text-zinc-950" },
  manager: { label: "매니저", className: "bg-emerald-500 text-zinc-950" },
  vip: { label: "열혈", className: "bg-rose-500 text-white" },
  fan: { label: "팬", className: "bg-amber-400 text-zinc-950" },
  supporter: { label: "서포터", className: "bg-violet-500 text-white" },
};

function Badge({ value }: { value: unknown }) {
  const b = typeof value === "string" ? BADGES[value] : undefined;
  if (!b) return null;
  return (
    <span className={`mr-1 rounded px-1 py-px text-[10px] font-bold ${b.className}`}>
      {b.label}
    </span>
  );
}

function SubTag({ value }: { value: unknown }) {
  const s = value as { tier?: string; months?: number } | undefined;
  if (!s || (s.tier !== "basic" && s.tier !== "plus")) return null;
  const color = s.tier === "plus" ? "text-fuchsia-300" : "text-sky-300";
  return (
    <span className={`mr-1 text-[10px] font-semibold ${color}`} title="구독 개월">
      {s.tier === "plus" ? "플러스" : "베이직"}
      {s.months ? ` ${s.months}` : ""}
    </span>
  );
}

// ---------------------------------------------------- 줄 모양 세 가지

/** 일반 채팅: [배지][구독] 닉네임  메시지 */
function UserLine({ d, children }: { d: ChatData; children: ReactNode }) {
  const own = d.badge === "streamer";
  return (
    <div className={`px-3 py-0.5 leading-relaxed ${own ? "bg-sky-500/10" : ""}`}>
      <Badge value={d.badge} />
      <SubTag value={d.subscriber} />
      <span className={`mr-1.5 font-semibold ${own ? "text-sky-300" : "text-zinc-400"}`}>
        {nick(d)}
      </span>
      <span className="wrap-break-word text-zinc-100">{children}</span>
    </div>
  );
}

/** 후원·구독 같은 이벤트: 색이 있는 상자 */
function EventBox({
  tone,
  title,
  children,
}: {
  tone: "gold" | "pink" | "blue";
  title: ReactNode;
  children?: ReactNode;
}) {
  const tones = {
    gold: "border-amber-400/40 bg-amber-400/10 text-amber-200",
    pink: "border-fuchsia-400/40 bg-fuchsia-400/10 text-fuchsia-200",
    blue: "border-sky-400/40 bg-sky-400/10 text-sky-200",
  };
  return (
    <div className={`mx-2 my-1 rounded-md border px-3 py-1.5 ${tones[tone]}`}>
      <div className="font-semibold">{title}</div>
      {children && <div className="text-xs text-zinc-300">{children}</div>}
    </div>
  );
}

/** 알림: 회색 한 줄 */
function NoticeLine({ children }: { children: ReactNode }) {
  return <div className="px-3 py-1 text-xs text-zinc-500">{children}</div>;
}

// ---------------------------------------------------- 상품 이름

function subItemName(code: number): string {
  if (code === 1 || code === 11) return "베이직 구독 선물권";
  if (code === 20 || code === 21) return "플러스 구독 선물권";
  return "구독 선물권";
}

function quickviewName(code: number): string {
  const days: Record<number, number> = { 100: 7, 101: 30, 102: 90 };
  return days[code] ? `퀵뷰 플러스 ${days[code]}일권` : "퀵뷰";
}

// ---------------------------------------------------- type별

export default function ChatLine({ type, data: d }: { type: string; data: ChatData }) {
  switch (type) {
    // 채팅
    case "chat":
      return <UserLine d={d}>{str(d, "message")}</UserLine>;
    case "ogq":
      return (
        <UserLine d={d}>
          <span className="mr-1 rounded bg-zinc-800 px-1 text-xs text-zinc-400">이모티콘</span>
          {str(d, "message")}
        </UserLine>
      );

    // 후원
    case "balloon":
      return <EventBox tone="gold" title={`${nick(d)}님 별풍선 ${formatNumber(num(d, "count"))}개`} />;
    case "adballoon":
      return <EventBox tone="gold" title={`${nick(d)}님 애드벌룬 ${formatNumber(num(d, "count"))}개`} />;
    case "vballoon":
      return <EventBox tone="gold" title={`${nick(d)}님 영상풍선 ${formatNumber(num(d, "count"))}개`} />;
    case "vod_balloon":
      return <EventBox tone="gold" title={`${nick(d)}님 VOD 별풍선 ${formatNumber(num(d, "count"))}개`} />;
    case "stationadcon":
    case "vodadcon":
      if (!str(d, "user_nick")) return null; // 닉네임이 빈 중복 알림
      return (
        <EventBox tone="gold" title={`${nick(d)}님 애드벌룬 ${formatNumber(num(d, "count"))}개`}>
          {type === "stationadcon" ? "방송국에서 선물" : "VOD에서 선물"}
        </EventBox>
      );
    case "fanletter":
      return <EventBox tone="gold" title={`${nick(d)}님 스티커 ${formatNumber(num(d, "count"))}개`} />;

    // 구독, 선물
    case "follow": {
      const s = d.subscriber as { tier?: string; months?: number } | undefined;
      const tier = s?.tier === "plus" ? "플러스" : "베이직";
      const months = s?.months ?? 0;
      return (
        <EventBox
          tone="pink"
          title={months > 1 ? `${nick(d)}님 ${tier} ${months}개월째 구독 중!` : `${nick(d)}님 ${tier} 구독`}
        />
      );
    }
    case "subscription":
      return (
        <EventBox tone="pink" title={`${nick(d, "from_user_nick")} → ${nick(d, "to_user_nick")}`}>
          {subItemName(num(d, "item_code"))} 선물
        </EventBox>
      );
    case "sub_random":
      return (
        <EventBox tone="pink" title={`${nick(d, "from_user_nick")}님 구독 선물 ${num(d, "count")}개`}>
          {subItemName(num(d, "item_code"))} 랜덤 선물
        </EventBox>
      );
    case "quickview":
      return (
        <EventBox tone="blue" title={`${nick(d, "from_user_nick")} → ${nick(d, "to_user_nick")}`}>
          {quickviewName(num(d, "item_code"))} 선물
        </EventBox>
      );
    case "quick_random":
      return (
        <EventBox tone="blue" title={`${nick(d, "from_user_nick")}님 퀵뷰 선물 ${num(d, "count")}개`}>
          {quickviewName(num(d, "item_code"))} 랜덤 선물
        </EventBox>
      );
    case "sendogq":
      return (
        <EventBox tone="blue" title={`${nick(d, "from_user_nick")} → ${nick(d, "to_user_nick")}`}>
          {str(d, "item_name")} 이모티콘 선물
        </EventBox>
      );

    // 미션
    case "challenge_mission": {
      const kind = str(d, "kind");
      const title = str(d, "title");
      if (kind === "CHALLENGE_GIFT")
        return (
          <EventBox tone="gold" title={`${nick(d)}님 도전미션 ${formatNumber(num(d, "count"))}개`}>
            {title}
          </EventBox>
        );
      if (kind === "CHALLENGE_SETTLE")
        return <NoticeLine>도전미션 [{title}] 별풍선 {formatNumber(num(d, "count"))}개 획득!</NoticeLine>;
      if (kind === "CHALLENGE_NOTICE")
        return (
          <NoticeLine>
            도전미션 [{title}] {str(d, "result") === "SUCCESS" ? "성공!" : "실패"}
          </NoticeLine>
        );
      return null;
    }
    case "battle_mission": {
      const kind = str(d, "kind");
      const title = str(d, "title");
      if (kind === "GIFT")
        return (
          <EventBox tone="gold" title={`${nick(d)}님 대결미션 ${formatNumber(num(d, "count"))}개`}>
            {title} ({str(d, "bj_nick")})
          </EventBox>
        );
      if (kind === "NOTICE")
        return (
          <NoticeLine>
            대결미션 [{title}] {d.draw ? "무승부" : `${str(d, "winner")} 승리`}
          </NoticeLine>
        );
      if (kind === "SETTLE")
        return <NoticeLine>대결미션 [{title}] 별풍선 {formatNumber(num(d, "count"))}개 정산</NoticeLine>;
      return null;
    }

    // 알림
    case "fanclub":
      return <NoticeLine>{nick(d)}님이 팬클럽에 가입했습니다.</NoticeLine>;
    case "supporter":
      return <NoticeLine>{nick(d)}님이 서포터가 되셨습니다.</NoticeLine>;
    case "topfan":
      return <NoticeLine>{nick(d)}님이 열혈팬이 되셨습니다.</NoticeLine>;
    case "adm_notice":
      return <NoticeLine>📢 {str(d, "message")}</NoticeLine>;
    case "bj_notice":
      return <NoticeLine>📌 공지: {str(d, "message")}</NoticeLine>;
    case "ice":
      return <NoticeLine>{d.frozen ? "❄️ 채팅이 얼었습니다." : "채팅 얼리기가 풀렸습니다."}</NoticeLine>;
    case "adinbroad":
      return <NoticeLine>{d.active ? "쉬는시간이 설정되었습니다." : "쉬는시간이 종료되었습니다."}</NoticeLine>;

    default:
      return null;
  }
}
