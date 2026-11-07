FROM python:3.13-slim

# supercronic: 컨테이너용 크론. 실행한 작업의 출력을 자기 stdout으로 넘겨서 docker logs에 남는다.
ARG SUPERCRONIC_VERSION=0.2.49
# BuildKit이 빌드 환경에 맞게 자동으로 채워준다 (amd64 / arm64).
ARG TARGETARCH

RUN set -eux; \
    apt-get update; \
    # tzdata: supercronic의 TZ 처리용. 기본 이미지에 이미 있을 수 있음(확인 전).
    apt-get install -y --no-install-recommends curl ca-certificates tzdata; \
    rm -rf /var/lib/apt/lists/*; \
    arch="${TARGETARCH:-amd64}"; \
    case "$arch" in \
        amd64) sha1=e63c11a9726b775a6a11801e81af4f3fb926aa68 ;; \
        arm64) sha1=0b6c5bb743e0b0dafed1132198c81807927ac413 ;; \
        *) echo "지원하지 않는 아키텍처: $arch" >&2; exit 1 ;; \
    esac; \
    curl -fsSLo /usr/local/bin/supercronic \
        "https://github.com/aptible/supercronic/releases/download/v${SUPERCRONIC_VERSION}/supercronic-linux-${arch}"; \
    echo "${sha1}  /usr/local/bin/supercronic" | sha1sum -c -; \
    chmod +x /usr/local/bin/supercronic

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# uv: 공식 이미지에서 실행 파일만 복사해 온다.
COPY --from=ghcr.io/astral-sh/uv:0.11.25 /uv /uvx /bin/

ENV UV_PYTHON_DOWNLOADS=never \
    UV_LINK_MODE=copy \
    UV_NO_CACHE=1 \
    # uv가 만드는 가상환경(/app/.venv)을 기본 python으로 쓴다.
    # 크론과 collect.sh의 `python -m ...`이 이 가상환경의 python을 가리키게 된다.
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

# 의존성 먼저 설치 (코드만 바뀌면 이 레이어는 캐시를 재사용한다)
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --only-group review_collector

COPY app ./app
COPY scripts/collect.sh ./scripts/collect.sh
COPY docker/collector.cron /etc/collector.cron
RUN chmod +x scripts/collect.sh

# -passthrough-logs: 작업 출력을 supercronic 형식으로 감싸지 않고 그대로 내보낸다.
CMD ["supercronic", "-passthrough-logs", "/etc/collector.cron"]