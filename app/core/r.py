"""redis 클라이언트.

종료 시 정리(aclose)는 lifespan에서 한다.
"""

import redis.asyncio as redis

from app.core.config import settings

redis_client = redis.from_url(
    settings.redis_url, decode_responses=True, legacy_responses=True
)
