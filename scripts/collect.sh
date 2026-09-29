#!/usr/bin/env bash
# collector 수동 실행 스크립트.

# 사용 예:
#   docker compose exec collector scripts/collect.sh vods
#   docker compose exec collector scripts/collect.sh vods --bid <bid>
#   docker compose exec collector scripts/collect.sh vods --retry
#   docker compose exec collector scripts/collect.sh vods --sweep --bid <bid>
# 
#   docker compose exec collector scripts/collect.sh chats --count <int>
#   docker compose exec collector scripts/collect.sh chats --vod-id <vod_id>

set -o pipefail   # 파이프 구간 중 하나라도 실패하면 스크립트도 실패로 끝낸다.

export PYTHONUNBUFFERED=1   # 파이프로 보낼 때도 출력을 모아두지 않고 바로 내보낸다.
export RUN_SOURCE=manual    # 로그에 [manual] 표시 (setup_logging이 읽는다)

if [ -w /proc/1/fd/1 ]; then
    # 터미널 + 도커 로그.
    python -m app.review.collector "$@" 2>&1 | tee /proc/1/fd/1
else
    # 터미널에만 찍히고 로그는 안 찍힘.
    python -m app.review.collector "$@"
fi
