"""chats_parts_to_data

chats.parts(text[], 값만 순서대로) → chats.data(jsonb, 이름 붙은 값).
기존 행을 타입별로 변환하고 parts를 지운다. 키 정의는 docs/review_chat_types.md.

- parts의 칸 번호는 SQL 배열 기준(1부터)이다. parts = XML 요소의 자식 중 t를 뺀 값, 문서 순서.
- 모르는 타입은 {"parts": [...]}로 그대로 옮긴다. 키를 정할 때 다시 변환한다. (지금은 해당 없음)
- 트랜잭션 하나로 돈다. 중간에 실패하면 전부 되돌아간다.
- downgrade는 지원하지 않는다. 떼어 낸 (n) 접미사와 버린 칸은 복구할 수 없으니 백업에서 복원한다.

Revision ID: 261012_0030
Revises: 261001_1036
Create Date: 2026-10-12 00:30:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = '261012_0030'
down_revision: Union[str, Sequence[str], None] = '261001_1036'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# ---------------------------------------------------------------- 도우미 함수
# pg_temp: 이 연결에서만 보이는 임시 함수. 마이그레이션이 끝나면 사라진다.
# LANGUAGE sql + IMMUTABLE이라 UPDATE 안에 그대로 풀려 들어가서 빠르다.

HELPERS = [
    # 아이디 뒤의 (2) 같은 중복 접속 접미사를 뗀다
    """
    CREATE FUNCTION pg_temp.uid(x text) RETURNS text
    LANGUAGE sql IMMUTABLE AS $$ SELECT regexp_replace(x, '\\(\\d+\\)$', '') $$
    """,
    # 숫자가 아니면 NULL (변환 뒤 null 개수로 확인한다)
    """
    CREATE FUNCTION pg_temp.num(x text) RETURNS bigint
    LANGUAGE sql IMMUTABLE AS $$ SELECT CASE WHEN x ~ '^-?\\d+$' THEN x::bigint END $$
    """,
    # 배지: 위에 있는 것이 이긴다. p는 플래그1(31번 비트가 있어 bigint)
    """
    CREATE FUNCTION pg_temp.badge(p bigint) RETURNS text
    LANGUAGE sql IMMUTABLE AS $$
        SELECT CASE
            WHEN p & 4       <> 0 THEN 'streamer'    -- 2번
            WHEN p & 320     <> 0 THEN 'manager'     -- 6번(64) 또는 8번(256)
            WHEN p & 32768   <> 0 THEN 'vip'         -- 15번
            WHEN p & 32      <> 0 THEN 'fan'         -- 5번
            WHEN p & 1048576 <> 0 THEN 'supporter'   -- 20번
            ELSE 'none'
        END
    $$
    """,
    # 구독 등급: 플래그2 19번 plus, 18번 basic, 둘 다 없으면 none
    """
    CREATE FUNCTION pg_temp.tier(p2 bigint) RETURNS text
    LANGUAGE sql IMMUTABLE AS $$
        SELECT CASE
            WHEN p2 & 524288 <> 0 THEN 'plus'
            WHEN p2 & 262144 <> 0 THEN 'basic'
            ELSE 'none'
        END
    $$
    """,
    # {tier, months}. 비구독이면 months 0
    """
    CREATE FUNCTION pg_temp.sub(tier text, months bigint) RETURNS jsonb
    LANGUAGE sql IMMUTABLE AS $$
        SELECT jsonb_build_object(
            'tier', tier,
            'months', CASE WHEN tier = 'none' THEN 0 ELSE greatest(months, 0) END
        )
    $$
    """,
    # follow의 stier(1 basic, 2 plus)를 tier로
    """
    CREATE FUNCTION pg_temp.stier(x text) RETURNS text
    LANGUAGE sql IMMUTABLE AS $$
        SELECT CASE x WHEN '1' THEN 'basic' WHEN '2' THEN 'plus' ELSE 'none' END
    $$
    """,
]


# ---------------------------------------------------------------- 타입별 변환
# (WHERE 조건, data를 만드는 식). 주석의 태그 이름은 원본 XML 태그.

CONVERSIONS: list[tuple[str, str]] = [
    # adinbroad: status chno [?]  쉬는시간(중간광고). 1 시작, 0 종료. 종료 때만 붙는 3번 칸은 광고 재생 횟수
    ("type = 'adinbroad'", """jsonb_build_object('active', parts[1] = '1')"""),

    # adballoon: bj u n c m em title img_url def_url fs tfn lng mof
    ("type = 'adballoon'", """jsonb_build_object(
        'bid', parts[1], 'user_id', pg_temp.uid(parts[2]), 'user_nick', parts[3],
        'count', pg_temp.num(parts[4]),
        'message1', parts[5], 'message2', parts[6],
        'img_url', parts[8], 'def_url', parts[9])"""),

    # adm_notice: type m
    ("type = 'adm_notice'", """jsonb_build_object('message', parts[2])"""),

    # balloon: bj u n c fs tfn fc fn def lng mof
    ("type = 'balloon'", """jsonb_build_object(
        'bid', parts[1], 'user_id', pg_temp.uid(parts[2]), 'user_nick', parts[3],
        'count', pg_temp.num(parts[4]))"""),

    # battle_mission GIFT: type ch bj bj_nick u n c subch fn key title top_fan fan_order
    ("type = 'battle_mission' AND parts[1] = 'GIFT'", """jsonb_build_object(
        'kind', parts[1], 'bid', parts[3], 'bj_nick', parts[4],
        'user_id', pg_temp.uid(parts[5]), 'user_nick', parts[6],
        'count', pg_temp.num(parts[7]),
        'mission_key', pg_temp.num(parts[10]), 'title', parts[11])"""),

    # battle_mission NOTICE: type draw winner loser fn key title
    ("type = 'battle_mission' AND parts[1] = 'NOTICE'", """jsonb_build_object(
        'kind', parts[1], 'winner', parts[3], 'loser', parts[4],
        'draw', parts[2] = '1',
        'mission_key', pg_temp.num(parts[6]), 'title', parts[7])"""),

    # battle_mission SETTLE: type bj bj_nick c fn key title
    ("type = 'battle_mission' AND parts[1] = 'SETTLE'", """jsonb_build_object(
        'kind', parts[1], 'bid', parts[2], 'bj_nick', parts[3],
        'count', pg_temp.num(parts[4]),
        'mission_key', pg_temp.num(parts[6]), 'title', parts[7])"""),

    # bj_notice: ch state type m
    ("type = 'bj_notice'", """jsonb_build_object('message', parts[4])"""),

    # challenge_mission CHALLENGE_GIFT: type ch subch key title fn c u n bj bj_nick
    ("type = 'challenge_mission' AND parts[1] = 'CHALLENGE_GIFT'", """jsonb_build_object(
        'kind', parts[1], 'bid', parts[10], 'bj_nick', parts[11],
        'user_id', pg_temp.uid(parts[8]), 'user_nick', parts[9],
        'count', pg_temp.num(parts[7]),
        'mission_key', pg_temp.num(parts[4]), 'title', parts[5])"""),

    # challenge_mission CHALLENGE_SETTLE: type subch key title fn c bj bj_nick
    ("type = 'challenge_mission' AND parts[1] = 'CHALLENGE_SETTLE'", """jsonb_build_object(
        'kind', parts[1], 'bid', parts[7], 'bj_nick', parts[8],
        'count', pg_temp.num(parts[6]),
        'mission_key', pg_temp.num(parts[3]), 'title', parts[4])"""),

    # challenge_mission CHALLENGE_NOTICE: type title ms key(늘 0)
    ("type = 'challenge_mission' AND parts[1] = 'CHALLENGE_NOTICE'", """jsonb_build_object(
        'kind', parts[1], 'title', parts[2], 'result', parts[3])"""),

    # challenge_mission_settle: ch fno u n c nfn
    ("type = 'challenge_mission_settle'", """jsonb_build_object(
        'user_id', pg_temp.uid(parts[3]), 'user_nick', parts[4],
        'count', pg_temp.num(parts[5]))"""),

    # chat: m u c mt l n p p2 fw nf nd acfw pn tlk
    ("type = 'chat'", """jsonb_build_object(
        'message', parts[1], 'user_id', pg_temp.uid(parts[2]), 'user_nick', parts[6],
        'badge', pg_temp.badge(pg_temp.num(parts[7])),
        'subscriber', pg_temp.sub(pg_temp.tier(pg_temp.num(parts[8])), pg_temp.num(parts[9])))"""),

    # fanclub: u n
    ("type = 'fanclub'", """jsonb_build_object(
        'user_id', pg_temp.uid(parts[1]), 'user_nick', parts[2])"""),

    # fanletter: bj bjnick u n c image lettertype lettermessage lng
    ("type = 'fanletter'", """jsonb_build_object(
        'bid', parts[1], 'user_id', pg_temp.uid(parts[3]), 'user_nick', parts[4],
        'count', pg_temp.num(parts[5]), 'message', parts[8])"""),

    # follow: bj u n type period acperiod stier [stype] lng mof
    # 선물 받은 구독(type 111)은 stype이 없어 9칸이지만, 쓰는 칸(1~7)의 위치는 같다
    ("type = 'follow'", """jsonb_build_object(
        'bid', parts[1], 'user_id', pg_temp.uid(parts[2]), 'user_nick', parts[3],
        'subscriber', pg_temp.sub(pg_temp.stier(parts[7]), pg_temp.num(parts[5])))"""),

    # ice: value (1 얼림, 0 해제)
    ("type = 'ice'", """jsonb_build_object('frozen', parts[1] = '1')"""),

    # ogq: ch m gid sid v s sn sf c l mt e fw nf nd acfw pn anm tlk.  sf = "p|p2"
    ("type = 'ogq'", """jsonb_build_object(
        'message', parts[2], 'user_id', pg_temp.uid(parts[6]), 'user_nick', parts[7],
        'badge', pg_temp.badge(pg_temp.num(split_part(parts[8], '|', 1))),
        'subscriber', pg_temp.sub(
            pg_temp.tier(pg_temp.num(split_part(parts[8], '|', 2))), pg_temp.num(parts[13])),
        'group_id', parts[3], 'sticker_id', pg_temp.num(parts[4]))"""),

    # quick_random: sender sendernick chno cnt itemcode
    ("type = 'quick_random'", """jsonb_build_object(
        'from_user_id', pg_temp.uid(parts[1]), 'from_user_nick', parts[2],
        'count', pg_temp.num(parts[4]), 'item_code', pg_temp.num(parts[5]))"""),

    # quickview: sender sendernick u n quickviewtype
    ("type = 'quickview'", """jsonb_build_object(
        'from_user_id', pg_temp.uid(parts[1]), 'from_user_nick', parts[2],
        'to_user_id', pg_temp.uid(parts[3]), 'to_user_nick', parts[4],
        'item_code', pg_temp.num(parts[5]))"""),

    # sendogq: sendid sendnick recvid recvnick title url ch
    ("type = 'sendogq'", """jsonb_build_object(
        'from_user_id', pg_temp.uid(parts[1]), 'from_user_nick', parts[2],
        'to_user_id', pg_temp.uid(parts[3]), 'to_user_nick', parts[4],
        'item_name', parts[5], 'img_url', parts[6])"""),

    # stationadcon, vodadcon: bj v vn cnt default msg chno lng mof
    # vodadcon은 v, vn이 빈 중복 알림이 오기도 한다. 빈 문자열 그대로 둔다
    ("type IN ('stationadcon', 'vodadcon')", """jsonb_build_object(
        'bid', parts[1], 'user_id', pg_temp.uid(parts[2]), 'user_nick', parts[3],
        'count', pg_temp.num(parts[4]), 'message', parts[6], 'def_url', parts[5])"""),

    # sub_random: sender sendernick chno cnt itemcode rank
    ("type = 'sub_random'", """jsonb_build_object(
        'from_user_id', pg_temp.uid(parts[1]), 'from_user_nick', parts[2],
        'count', pg_temp.num(parts[4]), 'item_code', pg_temp.num(parts[5]))"""),

    # subscription: ch s sn r rn b bn it ic is st sp sr
    ("type = 'subscription'", """jsonb_build_object(
        'from_user_id', pg_temp.uid(parts[2]), 'from_user_nick', parts[3],
        'to_user_id', pg_temp.uid(parts[4]), 'to_user_nick', parts[5],
        'item_code', pg_temp.num(parts[8]))"""),

    # supporter, topfan: u n
    ("type IN ('supporter', 'topfan')", """jsonb_build_object(
        'user_id', pg_temp.uid(parts[1]), 'user_nick', parts[2])"""),

    # vballoon: bj u n c tn st d sub fs tfn
    # vod_balloon: bj u n c fn def ch lng mof
    ("type IN ('vballoon', 'vod_balloon')", """jsonb_build_object(
        'bid', parts[1], 'user_id', pg_temp.uid(parts[2]), 'user_nick', parts[3],
        'count', pg_temp.num(parts[4]))"""),
]

# 위에 없는 타입. 키를 정한 뒤 다시 변환한다.
FALLBACK = "jsonb_build_object('parts', to_jsonb(parts))"


def upgrade() -> None:
    conn = op.get_bind()

    # 1. data 컬럼 추가 (처음엔 NULL 허용)
    op.add_column('chats', sa.Column('data', postgresql.JSONB(), nullable=True))

    # 2. 타입별 변환
    for sql in HELPERS:
        conn.execute(sa.text(sql))
    for where, expr in CONVERSIONS:
        result = conn.execute(sa.text(f"UPDATE chats SET data = {expr} WHERE {where}"))
        print(f"  {where}: {result.rowcount}")
    result = conn.execute(sa.text(f"UPDATE chats SET data = {FALLBACK} WHERE data IS NULL"))
    print(f"  (모르는 타입, parts 그대로): {result.rowcount}")

    # 3. 확인: 비어 있는 행이 있으면 멈춘다 (전부 되돌아감)
    missing = conn.execute(sa.text("SELECT count(*) FROM chats WHERE data IS NULL")).scalar()
    if missing:
        raise RuntimeError(f"data가 비어 있는 행 {missing}개")

    # 참고용: 값이 null인 키가 있는 타입 (숫자 칸에 숫자가 아닌 값이 있었던 것). 멈추지는 않는다
    rows = conn.execute(sa.text("""
        SELECT type, count(*) FROM chats
        WHERE EXISTS (SELECT 1 FROM jsonb_each(data) e WHERE e.value = 'null'::jsonb)
        GROUP BY type ORDER BY type
    """)).all()
    for type_, n in rows:
        print(f"  ! null 값이 있는 행: {type_} {n}")

    # 4. NOT NULL, 5. parts 삭제
    op.alter_column('chats', 'data', nullable=False)
    op.drop_column('chats', 'parts')


def downgrade() -> None:
    raise NotImplementedError("되돌릴 수 없다. 백업(pg_dump)에서 복원한다.")