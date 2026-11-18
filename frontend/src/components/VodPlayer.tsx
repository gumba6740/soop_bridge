// VOD 영상 플레이어.
//
// VOD 하나가 파일(파트) 여러 개로 나뉘어 있을 수 있다. 플레이어는 한 번에 한 파트만 재생하고,
// 채팅에 쓸 "VOD 전체 기준 시간"은 이렇게 계산해서 부모에게 알린다(onTime).
//
//   전체 시간 = 파트의 offset_base_sec + 파트 안 재생 시간(video.currentTime)
//
// hls_url은 HLS(.m3u8) 주소다. Safari는 video 태그가 바로 재생하지만,
// Chrome 등은 hls.js 라이브러리가 대신 받아서 넣어 줘야 한다.
//
// 영상을 못 틀 때(성인 VOD, 잠김, 주소 없음, 재생 실패)는 "채팅만 재생" 모드로 바꾼다.
// 타이머가 시간을 흘려보내서 채팅 다시보기는 계속 볼 수 있다.

import Hls from "hls.js";
import { useEffect, useMemo, useRef, useState } from "react";
import type { VodDetail, VodFile } from "../api/types";
import { formatClock } from "../lib/format";

type PlayablePart = VodFile & { hls_url: string; offset_base_sec: number };

interface Props {
  vod: VodDetail;
  onTime: (sec: number) => void;
}

export default function VodPlayer({ vod, onTime }: Props) {
  const parts = useMemo(
    () =>
      vod.files
        .filter((f): f is PlayablePart => f.hls_url != null && f.offset_base_sec != null)
        .sort((a, b) => a.file_order - b.file_order),
    [vod.files],
  );

  // 영상을 못 트는 이유. null이면 틀 수 있다.
  const blocked =
    vod.grade === 19
      ? "성인 인증이 필요한 VOD라서 영상을 재생할 수 없어요."
      : vod.media_code !== 1
        ? "SOOP에서 잠긴 VOD라서 영상을 재생할 수 없어요."
        : parts.length === 0
          ? "영상 주소가 없는 VOD예요."
          : null;

  const [failed, setFailed] = useState<string | null>(null);
  const [lastTime, setLastTime] = useState(0);

  function report(sec: number) {
    setLastTime(sec);
    onTime(sec);
  }

  const reason = blocked ?? failed;
  if (reason) {
    return <ChatOnlyClock totalSec={vod.total_duration / 1000} startAt={lastTime} reason={reason} onTime={report} />;
  }
  return <HlsPlayer parts={parts} onTime={report} onFail={setFailed} />;
}

// ------------------------------------------------------------ HLS 재생

function HlsPlayer({
  parts,
  onTime,
  onFail,
}: {
  parts: PlayablePart[];
  onTime: (sec: number) => void;
  onFail: (reason: string) => void;
}) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const nativeRef = useRef(false); // 브라우저가 직접 재생 중인지
  const [index, setIndex] = useState(0);
  const [autoplay, setAutoplay] = useState(false);
  const part = parts[index];

  // 파트가 바뀌면 영상 주소를 새로 붙인다.
  useEffect(() => {
    const video = videoRef.current;
    if (!video) return;

    let hls: Hls | null = null;
    nativeRef.current = false;
    if (video.canPlayType("application/vnd.apple.mpegurl")) {
      // 브라우저가 HLS를 직접 재생 (Safari 등). 이 경우 CDN의 CORS 허락이 필요 없다.
      nativeRef.current = true;
      video.src = part.hls_url;
    } else if (Hls.isSupported()) {
      // hls.js가 직접 받아서 넣어 줌 (Chrome 등). CDN이 CORS를 허락해야 한다.
      hls = new Hls();
      hls.on(Hls.Events.ERROR, (_e, data) => {
        if (!data.fatal) return; // 일시적인 에러는 hls.js가 알아서 다시 시도한다
        console.warn("HLS 재생 실패", data);
        onFail("영상을 불러오지 못했어요. 채팅만 재생합니다.");
      });
      hls.loadSource(part.hls_url);
      hls.attachMedia(video);
    } else {
      onFail("이 브라우저는 HLS 영상을 재생할 수 없어요. 채팅만 재생합니다.");
      return;
    }

    if (autoplay) video.play().catch(() => {});
    onTime(part.offset_base_sec);

    return () => {
      hls?.destroy();
      video.removeAttribute("src");
      video.load();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [part]);

  function onError() {
    // 브라우저가 직접 재생하다 난 에러. hls.js를 쓸 때는 위의 Hls.Events.ERROR로 처리한다.
    if (nativeRef.current) onFail("영상을 불러오지 못했어요. 채팅만 재생합니다.");
  }

  function goTo(i: number, play: boolean) {
    setAutoplay(play);
    setIndex(i);
  }

  return (
    <div>
      <video
        ref={videoRef}
        controls
        playsInline
        className="aspect-video w-full rounded-xl bg-black"
        onTimeUpdate={(e) => onTime(part.offset_base_sec + e.currentTarget.currentTime)}
        onSeeked={(e) => onTime(part.offset_base_sec + e.currentTarget.currentTime)}
        onEnded={() => index + 1 < parts.length && goTo(index + 1, true)}
        onError={onError}
      />
      {parts.length > 1 && (
        <div className="mt-2 flex flex-wrap gap-2">
          {parts.map((p, i) => (
            <button
              key={p.file_order}
              onClick={() => goTo(i, false)}
              className={`rounded-md px-2.5 py-1 text-xs ${
                i === index ? "bg-sky-500 font-semibold text-zinc-950" : "bg-zinc-800 text-zinc-300 hover:bg-zinc-700"
              }`}
            >
              파트 {p.file_order} · {formatClock(p.offset_base_sec)} ~{" "}
              {formatClock(p.offset_base_sec + p.duration / 1000)}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

// ------------------------------------------------------ 채팅만 재생

const TICK_MS = 250;

function ChatOnlyClock({
  totalSec,
  startAt,
  reason,
  onTime,
}: {
  totalSec: number;
  startAt: number;
  reason: string;
  onTime: (sec: number) => void;
}) {
  const [time, setTime] = useState(startAt);
  const [playing, setPlaying] = useState(false);

  // 재생 중이면 TICK_MS마다 시간을 흘려보낸다.
  useEffect(() => {
    if (!playing) return;
    const id = setInterval(() => {
      setTime((t) => Math.min(totalSec, t + TICK_MS / 1000));
    }, TICK_MS);
    return () => clearInterval(id);
  }, [playing, totalSec]);

  useEffect(() => {
    onTime(time);
    if (time >= totalSec) setPlaying(false);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [time]);

  return (
    <div className="flex aspect-video w-full flex-col justify-between rounded-xl border border-zinc-800 bg-zinc-900 p-5">
      <p className="text-sm text-zinc-400">{reason}</p>
      <div>
        <p className="mb-3 text-center font-mono text-4xl tabular-nums text-zinc-200">{formatClock(time)}</p>
        <div className="flex items-center gap-3">
          <button
            onClick={() => setPlaying((p) => !p)}
            className="w-20 rounded-md bg-sky-500 py-1.5 text-sm font-semibold text-zinc-950"
          >
            {playing ? "일시정지" : "재생"}
          </button>
          <input
            type="range"
            min={0}
            max={Math.floor(totalSec)}
            step={1}
            value={Math.floor(time)}
            onChange={(e) => setTime(Number(e.target.value))}
            className="flex-1 accent-sky-500"
          />
          <span className="w-20 text-right font-mono text-xs text-zinc-500">{formatClock(totalSec)}</span>
        </div>
      </div>
    </div>
  );
}