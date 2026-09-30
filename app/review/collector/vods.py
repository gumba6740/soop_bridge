"""VOD 메타 + 상세 수집.

수집 전략
---------
스트리머별 워터마크(= 해당 bid의 MAX(vod_id))를 기준으로 증분 수집한다.
SOOP VOD 목록은 최신순으로 오고, title_no(=vod_id)는 단조 증가함을 이용함.

수집: bid -> vod_id 리스트 -> vod_id 상세정보
    -> 성공시 vods, vod_files에 저장, 실패 시 vod_fetch_failures에 기록
수집 재시도: retry -> db에 기록한 수집 실패 vod_id 목록 -> vod_id 상세정보 -> 저장
누락 수집(sweep): 목록 전체 vod_id와 DB의 vod_id를 대조 -> 빠진 것만 상세정보 -> 저장

메타데이터의 해당 스트리머의 총 영상 개수(meta.total)와 DB 건수를 비교해 누락을 감지한다.
어긋나면 sweep으로 누락분을 채운다.
"""

import logging
from typing import Any

import httpx
from psycopg import AsyncConnection
from pydantic import ValidationError

from app.review.shared.schemas import VodFileRow, VodRow
from app.core.soop_clients.review_client import get_vod_detail, get_vod_list, polite_sleep

logger = logging.getLogger(__name__)

REQUEST_DELAY = 1.0      # 초. 모든 외부 요청 직전에 무조건 적용.
PER_PAGE = 60            # 서버가 20 ~ 60까지 받는 것을 확인함.
MAX_RETRY_ATTEMPTS = 5   # 이 횟수를 넘으면 permanent로 승격.
FILES_COVERAGE_WARN = 0.95  # 파트별 영상길이 합계가 total_duration(영상 총 길이)의
                            # 비율이 이것 미만이면 누락으로 판단.


# ---------------------------------------------------------------- SQL

# 다시보기 메타데이터는 사후 변경되지 않으므로 업데이트가 아니라 DO NOTHING.
# 신규 삽입 여부는 cur.rowcount 로 판별한다.
VOD_SQL = """
    INSERT INTO vods (
        vod_id, bid, title, total_duration, broad_start, broad_end,
        grade, auth_no, permanence, auto_delete_date, chat_duration,
        media_code, broad_idx, hashtags, view_cnt, live_total_view
    )
    VALUES (
        %(vod_id)s, %(bid)s, %(title)s, %(total_duration)s,
        %(broad_start)s, %(broad_end)s,
        %(grade)s, %(auth_no)s, %(permanence)s, %(auto_delete_date)s,
        %(chat_duration)s, %(media_code)s, %(broad_idx)s,
        %(hashtags)s, %(view_cnt)s, %(live_total_view)s
    )
    ON CONFLICT (vod_id) DO NOTHING
"""


VOD_FILE_SQL = """
    INSERT INTO vod_files (vod_id, file_order, duration, chat_url, hls_url, file_start)
    VALUES (%(vod_id)s, %(file_order)s, %(duration)s, %(chat_url)s, %(hls_url)s, %(file_start)s)
    ON CONFLICT (vod_id, file_order) DO UPDATE
        SET chat_url = EXCLUDED.chat_url,
            hls_url  = EXCLUDED.hls_url
"""

# vod 상세정보 수집이 실패했을 때 호출됨.
FAILURE_SQL = """
    INSERT INTO vod_fetch_failures (vod_id, bid, reason, permanent)
    VALUES (%(vod_id)s, %(bid)s, %(reason)s, %(permanent)s)
    ON CONFLICT (vod_id) DO UPDATE
        SET reason        = EXCLUDED.reason,
            permanent     = EXCLUDED.permanent
                            OR vod_fetch_failures.attempts + 1 >= %(max_attempts)s,
            attempts      = vod_fetch_failures.attempts + 1,
            last_tried_at = now()
"""

# 실패 기록된 vod 상세정보를 재시도해서 성공했을 때 호출
RESOLVE_FAILURE_SQL = """
    UPDATE vod_fetch_failures
    SET resolved_at = now()
    WHERE vod_id = %s
    AND resolved_at IS NULL
"""

WATERMARK_SQL = "SELECT COALESCE(MAX(vod_id), 0) FROM vods WHERE bid = %s"

# 저장된 건수 + 실패 건수 = "처리를 마친" 건수. meta.total과 비교한다.
ACCOUNTED_SQL = """
    SELECT (SELECT count(*) FROM vods WHERE bid = %(bid)s)
        + (SELECT count(*) FROM vod_fetch_failures
            WHERE bid = %(bid)s AND resolved_at IS NULL)
"""

# sweep 대조용. 실패 테이블도 포함해야 영구 실패분을 매번 다시 요청하지 않는다.
# 일시 실패분은 retry가 담당한다.
KNOWN_IDS_SQL = """
    SELECT vod_id FROM vods WHERE bid = %(bid)s
    UNION
    SELECT vod_id FROM vod_fetch_failures WHERE bid = %(bid)s
"""

# 재시도 대상을 고를 때 호출
RETRYABLE_SQL = """
    SELECT vod_id, bid
    FROM vod_fetch_failures
    WHERE NOT permanent
    AND resolved_at IS NULL
    ORDER BY last_tried_at
"""


# ---------------------------------------------------------------- 조회

async def _scalar(conn: AsyncConnection, sql: str, params: Any) -> int:
    cur = await conn.execute(sql, params)
    row = await cur.fetchone()
    return int(row[0]) if row else 0


async def fetch_new_vod_ids(bid: str, watermark: int) -> tuple[list[int], int]:
    """워터마크보다 큰 vod_id를 오름차순으로 반환한다. DB를 건드리지 않는다.

    watermark=0이면 경계에 도달하지 않으므로 전체 목록을 가져온다 (sweep).

    메타 데이터 파싱:
        last_page를 가져와서 루프의 끝을 정함.
        total로 해당 스트리머의 총 영상 갯수를 가져옴.

    데이터 파싱:
        가져온 vod_id들(page_ids) 중 새로운 것(new_ids)만 걸러낸다.
        워터마크 경계에 도달하면 루프를 중단한다.

    Returns:
        (신규 vod_id 리스트, meta.total). total을 모르면 0.
    """
    # 1페이지는 last_page, total을 얻기 위해 먼저 받고, 루프에서 재사용한다.
    await polite_sleep(REQUEST_DELAY)
    first = await get_vod_list(bid, page=1, per_page=PER_PAGE)

    # 메타 데이터 파싱.
    meta = (first or {}).get("meta")
    if not meta:
        logger.warning("[%s] meta 없음 — 1페이지만 시도", bid)
        meta = {}

    last_page = int(meta.get("last_page") or 1)
    total = int(meta.get("total") or 0)
    actual_per_page = int(meta.get("per_page") or PER_PAGE)
    if actual_per_page != PER_PAGE:
        logger.info(
            "[%s] per_page 요청 %d → 실제 %d (서버 상한)",
            bid, PER_PAGE, actual_per_page,
        )

    logger.info("[%s] API 총 %d개 / %d페이지", bid, total, last_page)

    vod_ids: list[int] = []

    for page in range(1, last_page + 1):
        if page == 1:
            data = first
        else:
            await polite_sleep(REQUEST_DELAY)
            data = await get_vod_list(bid, page=page, per_page=PER_PAGE)

        # 데이터 파싱.
        raw_data = (data or {}).get("data")
        if raw_data is None:
            logger.warning("[%s] 페이지 %d data 키 없음", bid, page)
            break
        raw: list[dict[str, Any]] = raw_data or []
        if not raw:
            break

        page_ids = [v["title_no"] for v in raw if v.get("title_no")]
        if not page_ids:
            logger.warning("[%s] 페이지 %d 유효 id 없음", bid, page)
            continue

        new_ids = [vid for vid in page_ids if vid > watermark]
        vod_ids.extend(new_ids)

        if len(new_ids) < len(page_ids):
            logger.info("[%s] 경계 도달 (페이지 %d에서 중단)", bid, page)
            break

    # 오래된 순으로 정렬.
    return sorted(set(vod_ids)), total


def parse_vod_detail(
    vod_id: int, data: dict[str, Any]
) -> tuple[VodRow, list[VodFileRow]] | None:
    """SOOP 상세 응답을 VodRow + VodFileRow 리스트로 파싱한다.

    검증은 pydantic에 맡긴다. 실패 시 None.
    """
    inner = (data or {}).get("data")
    if not isinstance(inner, dict):
        logger.warning(
            "[SKIP] vod_id=%d 응답 형식 이상 (%s)", vod_id, type(inner).__name__
        )
        return None

    try:
        vod = VodRow(vod_id=vod_id, **inner)
        files = [
            VodFileRow(vod_id=vod_id, **f)
            for f in (inner.get("files") or [])
            if isinstance(f, dict)
        ]
    except ValidationError as e:
        logger.warning("[SKIP] vod_id=%d 검증 실패: %s", vod_id, e.errors()[:2])
        return None

    return vod, files


# ---------------------------------------------------------------- 저장

async def save_vod(
    conn: AsyncConnection, vod: VodRow, files: list[VodFileRow]
) -> bool:
    """vods + vod_files를 한 트랜잭션으로 저장한다. 신규 삽입이면 True."""
    async with conn.transaction():
        cur = await conn.execute(VOD_SQL, vod.model_dump())
        inserted = cur.rowcount > 0

        if files:
            async with conn.cursor() as cur:
                await cur.executemany(
                    VOD_FILE_SQL, [f.model_dump() for f in files]
                )

        await conn.execute(RESOLVE_FAILURE_SQL, (vod.vod_id,))

    return inserted


async def record_failure(
    conn: AsyncConnection, vod_id: int, bid: str, reason: str, *, permanent: bool
) -> None:
    await conn.execute(
        FAILURE_SQL,
        {
            "vod_id": vod_id,
            "bid": bid,
            "reason": reason[:500],
            "permanent": permanent,
            "max_attempts": MAX_RETRY_ATTEMPTS,
        },
    )


def _log_coverage(bid: str, vod: VodRow, files: list[VodFileRow]) -> None:
    """각 영상 파트 duration 합계와 total_duration을 비교해 누락을 알린다.

    19금(grade=19) 파트의 경우 응답에서 누락될 수 있는데,
    file_order만으로는 누락 여부 확인이 불명확해서 duration으로 확인함.

    디비에는 저장하지 않고 로그 출력용.
    """
    if not files:
        logger.info(
            "[%s] vod_id=%d 파트 없음 (auth_no=%s, media_code=%s)",
            bid, vod.vod_id, vod.auth_no, vod.media_code,
        )
        return

    if vod.total_duration <= 0:
        logger.info(
            "[%s] vod_id=%d total_duration=%d, 총 영상시간 잘못됨",
            bid, vod.vod_id, vod.total_duration
        )
        return

    covered = sum(f.duration for f in files)
    # 파트별 영상시간들의 합과 총 영상시간이 다를 수 있으니 ratio로 누락을 판단.
    ratio = covered / vod.total_duration
    if ratio < FILES_COVERAGE_WARN:
        logger.info(
            "[%s] vod_id=%d 파트 일부 누락: %.0f%% (grade=%d, file_order=%s)",
            bid, vod.vod_id, ratio * 100, vod.grade,
            [f.file_order for f in files],
        )


async def process_vod(conn: AsyncConnection, vod_id: int, bid: str) -> bool:
    """VOD 1건을 수집 → 저장. 신규 저장이면 True.

    실패는 여기서 삼키고 vod_fetch_failures에 기록한다.
    """
    await polite_sleep(REQUEST_DELAY)  # 성공/실패 무관하게 요청 직전에

    try:
        data = await get_vod_detail(vod_id)
    except httpx.HTTPError as e:
        logger.warning("[RETRY] vod_id=%d 상세 조회 실패: %s", vod_id, e)
        await record_failure(conn, vod_id, bid, f"http: {e}", permanent=False)
        return False

    if data is None:
        await record_failure(conn, vod_id, bid, "영구 실패(삭제/비공개)", permanent=True)
        return False

    result = parse_vod_detail(vod_id, data)
    if result is None:
        # 스키마 변경일 수도 있으니 permanent로 단정하지 않는다.
        await record_failure(conn, vod_id, bid, "파싱/검증 실패", permanent=False)
        return False

    vod, files = result

    _log_coverage(bid, vod, files)  # 로그용

    is_inserted = await save_vod(conn, vod, files)
    logger.info(
        "[OK] vod_id=%d %s (파트 %d개)",
        vod_id, "저장" if is_inserted else "중복", len(files),
    )
    return is_inserted


async def collect_new_vods(conn: AsyncConnection, bid: str) -> int:
    """한 스트리머의 새 VOD를 워터마크 기준으로 수집 → 저장한다. 신규 저장 건수를 반환."""
    watermark = await _scalar(conn, WATERMARK_SQL, (bid,))
    logger.info("[%s] 워터마크 vod_id=%d", bid, watermark)

    vod_ids, total = await fetch_new_vod_ids(bid, watermark)

    saved = 0
    for vod_id in vod_ids:
        if await process_vod(conn, vod_id, bid):
            saved += 1

    if not vod_ids:
        logger.info("[%s] 새 VOD 없음", bid)

    # 건강검진: API가 알려준 총계와 DB에서 처리를 마친 건수를 비교한다.
    if total:
        accounted = await _scalar(conn, ACCOUNTED_SQL, {"bid": bid})
        if accounted < total:
            logger.warning(
                "[%s] 누락 의심: 처리완료 %d < API 총계 %d (%d건). --sweep 권장",
                bid, accounted, total, total - accounted,
            )
        elif accounted > total:
            # 스트리머가 VOD를 지우면 자연스럽게 발생한다. 정보성 로그.
            logger.info("[%s] DB %d > API %d (삭제분 추정)", bid, accounted, total)

    return saved


async def sweep_missing_vods(conn: AsyncConnection, bid: str) -> int:
    """목록 전체와 DB를 대조해 빠진 VOD만 수집한다. 신규 저장 건수를 반환.

    워터마크 방식은 워터마크보다 작은 id의 누락을 복구하지 못하므로,
    목록 전체 id에서 DB에 있는 id(저장 + 실패 기록)를 뺀 것만 상세 요청한다.
    """
    list_ids, _ = await fetch_new_vod_ids(bid, watermark=0)

    cur = await conn.execute(KNOWN_IDS_SQL, {"bid": bid})
    known = {row[0] for row in await cur.fetchall()}

    missing = sorted(set(list_ids) - known)
    logger.info(
        "[%s] sweep: 목록 %d / DB %d / 누락 %d",
        bid, len(list_ids), len(known), len(missing),
    )

    saved = 0
    for vod_id in missing:
        if await process_vod(conn, vod_id, bid):
            saved += 1
    return saved


# ---------------------------------------------------------------- 재시도

async def retry_failures(conn: AsyncConnection) -> int:
    """일시 실패로 기록된 VOD를 재시도한다. 수집 루프와 분리된 별도 경로."""
    cur = await conn.execute(RETRYABLE_SQL)
    rows = await cur.fetchall()
    if not rows:
        logger.info("재시도 대상 없음")
        return 0

    logger.info("재시도 대상 %d건", len(rows))
    saved = 0
    for vod_id, bid in rows:
        if await process_vod(conn, vod_id, bid):
            saved += 1
    return saved