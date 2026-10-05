import functools
from time import perf_counter


def atimer(func):  # type: ignore[no-untyped-def]
    @functools.wraps(func)
    async def wrapper(*args, **kwargs):  # type: ignore[no-untyped-def]
        t0 = perf_counter()
        try:
            return await func(*args, **kwargs)
        finally:
            ms = (perf_counter() - t0) * 1000
            print(f"{ms:6.2f}ms | {func.__name__}")

    return wrapper
