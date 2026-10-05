"""라이브 collector: 채팅, 영상 주소, 방송 현황

스트리머마다 태스크를 하나씩 띄워서(asyncio.create_task()),
방송 중이면 채팅을 받아 redis에 넣는다.

redis 키
---------
- soop:live:{bid}:summary  (해시, 라이브 요약) 방송 중일 때만 존재. 끝나면 지운다.
    방송 시작 때: bno, nick, title, hls_sd, hls_hd
    DETAIL_INTERVAL마다: title, started_at, viewers, viewers_at
- soop:live:{bid}:chats (스트림) 채팅 한 건 = 항목 하나. 필드는 parts(JSON) 하나.
    최근 CHAT_MAXLEN개만 유지. 방송이 끝나면 CHAT_TTL_AFTER_END 뒤에 사라진다.

방송 종료 판단
---------
채팅 서버는 다시보기를 자를 때도 0007을 보낸다. 그래서 0007만으로 끝내지 않고,
채팅 수신이 끝나면 접속 정보를 다시 조회해서 RESULT가 1이 아닐 때만 종료로 본다.
아직 방송 중이면 바로 다시 접속한다.
방송 중에 연령 제한(RESULT -6)으로 바뀌면 종료로 보지 않고,
summary를 둔 채 풀릴 때까지 기다린다.
"""

import asyncio
import json
import logging
from datetime import datetime
from typing import Any

from redis.asyncio import Redis

from app.core.config import settings
from app.core.soop_clients.live_clients.broad_client import get_broad_detail
from app.core.soop_clients.live_clients.chat_client import stream_live_chats
from app.core.soop_clients.live_clients.shared import LiveStatus, get_live_info
from app.core.soop_clients.live_clients.vod_client import get_hls_url
from app.utils.tz import KST

logger = logging.getLogger(__name__)


POLL_INTERVAL = 60               # 방송 중이 아닐 때 다시 확인하는 간격(초)
RETRY_DELAY = 5                  # 에러(연결 끊김 등) 뒤 다시 시도하기까지(초)
DETAIL_INTERVAL = 60             # 방송 중일 때 시청자 수 등을 갱신하는 간격(초)
CHAT_MAXLEN = 200                # 스트림에 남길 최근 채팅 수 (대략)
CHAT_TTL_AFTER_END = 600         # 방송이 끝난 뒤 채팅 스트림을 남겨둘 시간(초)
VIDEO_QUALITIES = ("sd", "hd")   # 360p, 540p. 웹 경로로는 540p가 최대


def _kst_iso(text: str) -> str:
    """SOOP의 시각 문자열("2026-10-04 18:01:57", 한국시간)을 KST가 붙은 ISO 형식으로."""
    dt = datetime.strptime(text, "%Y-%m-%d %H:%M:%S").replace(tzinfo=KST)
    return dt.isoformat(timespec="seconds")


async def on_start(r: Redis, bid: str, info: dict[str, Any]) -> None:
    """방송이 시작됐거나 BNO가 바뀌었을 때. summary를 새로 쓴다."""
    mapping = {
        "bno": str(info.get("BNO", "")),
        "nick": str(info.get("BJNICK", "")),
        "title": str(info.get("TITLE", "")),
    }
    for quality in VIDEO_QUALITIES:
        try:
            url = await get_hls_url(bid, info, quality)
        except Exception as e:
            # 영상 주소를 못 받아도 채팅 수집은 계속한다.
            logger.warning("bid=%s quality=%s 영상 주소 실패: %r", bid, quality, e)
            url = None
        if url:
            mapping[f"hls_{quality}"] = url

    # 이전 방송의 필드가 섞이지 않도록 초기화.
    async with r.pipeline(transaction=True) as pipe:
        pipe.delete(settings.summary_key(bid))
        pipe.hset(settings.summary_key(bid), mapping=mapping)
        # chats_key는 방송 종료 후 ttl 10분이 걸림.
        # 삭제되기 전에 방송이 재개되면 ttl을 없앰.
        pipe.persist(settings.chats_key(bid))
        await pipe.execute()

    logger.info("bid=%s %s님 방송 시작 (bno=%s)", bid, mapping["nick"], mapping["bno"])


async def on_end(r: Redis, bid: str) -> None:
    """방송이 끝났을 때. summary(라이브 요약)을 지우고 채팅 스트림은 잠시 남겨둔다."""
    async with r.pipeline(transaction=True) as pipe:
        pipe.delete(settings.summary_key(bid))
        pipe.expire(settings.chats_key(bid), CHAT_TTL_AFTER_END)
        await pipe.execute()


async def detail_loop(r: Redis, bid: str, bno: str) -> None:
    """방송 중에 DETAIL_INTERVAL마다 시청자 수, 제목, 시작 시각을 summary에 갱신한다.

    watch가 방송 시작 때 create_task()로 띄우고, 방송이 끝나면 summary를 지우기 전에 멈춘다.
    """
    while True:
        try:
            data = await get_broad_detail(bid)
            # 접속 정보와 엇갈려서 다른 방송의 값이 오면 쓰지 않는다.
            if data is not None and str(data["broad"].get("broad_no")) == bno:
                broad, station = data["broad"], data.get("station") or {}
                mapping = {
                    "title": str(broad.get("broad_title", "")),
                    "viewers": str(broad.get("current_sum_viewer", "")),
                    "viewers_at": _kst_iso(data["current_timestamp"]),
                }
                if station.get("broad_start"):
                    mapping["started_at"] = _kst_iso(station["broad_start"])
                await r.hset(settings.summary_key(bid), mapping=mapping)
        except Exception as e:
            # 시청자 수를 못 받아도 채팅 수집에는 영향이 없도록 여기서 끝낸다.
            logger.warning("bid=%s 방송 현황 실패 %s: %s", bid, type(e).__name__, e)

        await asyncio.sleep(DETAIL_INTERVAL)


async def _stop(task: asyncio.Task[None] | None) -> None:
    """태스크를 취소하고 정리가 끝날 때까지 기다린다. (chat_client의 핑 태스크와 같은 방식)"""
    if task is None:
        return
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


async def relay_chats(r: Redis, bid: str, info: dict[str, Any]) -> int:
    """채팅을 받아 스트림에 넣는다. 0007이면 끝나고, 끊기면 예외가 올라간다."""
    count = 0
    async for parts in stream_live_chats(bid, info):
        await r.xadd(
            settings.chats_key(bid),
            {"parts": json.dumps(parts, ensure_ascii=False)},
            maxlen=CHAT_MAXLEN,
            approximate=True,
        )
        count += 1
    return count


async def watch(r: Redis, bid: str) -> None:
    """한 스트리머를 계속 지켜본다. 이 함수는 끝나지 않는다(취소될 때까지)."""
    # None: 시작 직후라 모름 -> 첫 확인에서 방송 중이 아니면 이전 실행이 남긴 summary를 지운다.
    live: bool | None = None
    bno: str | None = None
    detail_task: asyncio.Task[None] | None = None

    try:
        while True:
            try:
                status, info = await get_live_info(bid)

                if status == LiveStatus.LOGIN_REQUIRED and live:
                    # 방송 중에 연령 제한이 켜진 것. 방송은 계속되니 summary는 두고, 풀릴 때까지 기다린다.
                    logger.info("bid=%s 연령 제한 중. %d초 뒤 재확인", bid, POLL_INTERVAL)
                    await asyncio.sleep(POLL_INTERVAL)
                    continue
                # info가 None이면 방송 종료 상태.
                if info is None:
                    # live가 True면 방금 방송 종료한 상태.
                    # live가 None이면 watch가 방금 돌아간 상태.
                    # 두 경우 모두 False로 바꿔줌.
                    if live is not False:
                        await _stop(detail_task)
                        detail_task = None
                        await on_end(r, bid)
                        if live:
                            logger.info("bid=%s 방송 종료", bid)
                    live, bno = False, None
                    await asyncio.sleep(POLL_INTERVAL)
                    continue

                # 방송 중. 새로 시작했거나 BNO가 바뀌었으면 summary를 새로 쓴다.
                if not live or info.get("BNO") != bno:
                    await _stop(detail_task)
                    await on_start(r, bid, info)
                    live, bno = True, str(info.get("BNO"))
                    detail_task = asyncio.create_task(
                        detail_loop(r, bid, bno), name=f"detail:{bid}"
                    )
                # relay_chats가 끝날 때(0007, 끊김)까지 여기서 기다린다
                count = await relay_chats(r, bid, info)
                # 여기 오면 0007을 받은 것. 다음 반복에서 접속 정보를 다시 보고 종료인지 판단한다.
                logger.info("bid=%s 0007 수신 (%d건). 방송 상태 재확인", bid, count)
                # 접속하자마자 0007이 반복해서 오는 경우를 대비해 잠깐 쉰다.
                await asyncio.sleep(1)

            except Exception as e:
                logger.warning("bid=%s %s: %s", bid, type(e).__name__, e)
                await asyncio.sleep(RETRY_DELAY)
    finally:
        # 취소될 때(collector 종료) detail_task도 같이 정리한다.
        await _stop(detail_task)


async def watch_all(r: Redis, bids: list[str]) -> None:
    """스트리머마다 watch를 띄운다. 끝날 때(취소 포함) summary를 지운다."""
    logger.info("라이브 collector 시작: %s", ", ".join(bids))
    try:
        async with asyncio.TaskGroup() as tg:
            for bid in bids:
                tg.create_task(watch(r, bid), name=f"watch:{bid}")
    finally:
        # 꺼져 있는 동안 "방송 중"으로 남지 않도록 summary를 지운다. 다음 실행에서 다시 쓴다.
        try:
            await r.delete(*(settings.summary_key(bid) for bid in bids))
        except Exception as e:
            logger.warning("종료 정리 실패: %r", e)
        logger.info("라이브 collector 종료")
