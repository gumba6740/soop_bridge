import asyncio
import contextlib
from collections.abc import AsyncGenerator, Awaitable, Callable, Coroutine
from typing import Any

# 끝나지 않는 작업이 있어도 테스트가 멈추지 않고 실패하게 하는 시간(초)
TIMEOUT = 5


async def _until(check: Callable[[], Awaitable[bool] | bool]) -> None:
    """check 가 참이 될 때까지 기다린다. check 는 보통 함수도, async 함수도 된다."""

    async def loop() -> None:
        while True:
            result = check()
            if isinstance(result, Awaitable):
                result = await result
            if result:
                return
            await asyncio.sleep(0.01)

    await asyncio.wait_for(loop(), TIMEOUT)


@contextlib.asynccontextmanager
async def _running(coro: Coroutine[Any, Any, Any]) -> AsyncGenerator[asyncio.Task]:
    """coro 를 백그라운드로 띄우고, 블록이 끝나면 멈춘다."""
    task = asyncio.create_task(coro)
    try:
        yield task
    finally:
        task.cancel()
        # wait_for 는 시간이 넘으면 다시 취소하고 끝날 때까지 기다려서,
        # 취소를 무시하는 작업이면 멈춘다. wait 는 시간만 재고 돌아온다.
        await asyncio.wait({task}, timeout=TIMEOUT)
