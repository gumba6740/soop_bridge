from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env")

    # ---------------------------------------------------- Database
    postgres_user: str
    postgres_password: str
    postgres_db: str
    postgres_host: str = "localhost"  # 컨테이너에서는 compose가 "db"로 덮어쓴다
    postgres_port: int = 5432

    @property
    def postgres_url(self) -> str:
        return (
            f"postgresql://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def sqlalchemy_url(self) -> str:
        return (
            f"postgresql+psycopg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    # ------------------------------------------------- Redis
    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_db: int = 0

    @property
    def redis_url(self) -> str:
        return f"redis://{self.redis_host}:{self.redis_port}/{self.redis_db}"

    # 온디맨드 채팅 수집 큐. API가 넣고 collector 워커가 꺼낸다.
    @property
    def chat_job_queue_key(self) -> str:
        return "soop:chat_jobs"

    # 큐에 있거나 수집 중인 vod_id 집합. 중복 요청 방지용.
    @property
    def chat_job_pending_key(self) -> str:
        return "soop:chat_jobs:pending"

    # 라이브 요약(해시).
    def summary_key(self, bid: str) -> str:
        return f"soop:live:{bid}:summary"

    # 라이브 채팅(스트림).
    def chats_key(self, bid: str) -> str:
        return f"soop:live:{bid}:chats"

    # CORS
    cors_origins: list[str] = []


settings = Settings()
