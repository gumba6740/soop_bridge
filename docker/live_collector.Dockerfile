FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# uv: 공식 이미지에서 실행 파일만 복사해 온다.
COPY --from=ghcr.io/astral-sh/uv:0.11.25 /uv /uvx /bin/

ENV UV_PYTHON_DOWNLOADS=never \
    UV_LINK_MODE=copy \
    UV_NO_CACHE=1 \
    # uv가 만드는 가상환경(/app/.venv)을 기본 python으로 쓴다.
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

# 의존성 먼저 설치 (코드만 바뀌면 이 레이어는 캐시를 재사용한다)
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --only-group live_collector

COPY app ./app

CMD ["python", "-m", "app.live.collector"]