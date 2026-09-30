"""ipython 셸 시작 스크립트 (django-extensions의 shell_plus 모방).

사용:
ipython -i scripts/shell.py
"""
# ruff: noqa: F401
import atexit
from sqlalchemy import func, select, text
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.core.db import SessionLocal, engine
from app.core.r import redis_client
from app.models import (
    Chat,
    ChatProgress,
    Vod,
    VodFetchFailure,
    VodFile,
)

s = SessionLocal()
r = redis_client


def _close_on_exit(session=s, redis=r, eng=engine) -> None:
    """ipython을 exit로 종료 시 발생하는 에러 방지 함수."""
    from IPython.core.async_helpers import get_asyncio_loop

    async def _close() -> None:
        await session.close()
        await redis.aclose()
        await eng.dispose()

    loop = get_asyncio_loop()
    if not loop.is_closed():
        # 비동기 함수를 동기 함수 내부에서 돌림.
        loop.run_until_complete(_close())


atexit.register(_close_on_exit)


def _print_imports() -> None:
    # 함수 안에서 import해서 셸 네임스페이스와 출력 목록에 섞이지 않게 한다
    import ast

    kw = "\033[1;95m{}\033[0m".format

    with open(__file__) as f:
        tree = ast.parse(f.read())

    for node in tree.body:  # 맨 위 수준의 문장만 본다 (함수 안 import는 제외)
        names = ", ".join(
            a.name if a.asname is None else f"{a.name} {kw('as')} {a.asname}"
            for a in getattr(node, "names", [])
        )
        if isinstance(node, ast.ImportFrom):
            print(f"{kw('from')} {node.module} {kw('import')} {names}")
        elif isinstance(node, ast.Import):
            print(f"{kw('import')} {names}")


_print_imports()
del _print_imports