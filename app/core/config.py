# config.py
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    class Config:
        env_file = ".env"

    # Database settings
    postgres_user: str
    postgres_password: str
    postgres_db: str
    postgres_port: int = 5432

    @property
    def postgres_url(self) -> str:
        return (
            f"postgresql://{self.postgres_user}:{self.postgres_password}"
            f"@localhost:{self.postgres_port}/{self.postgres_db}"
        )

    # Redis settings
    redis_port: int = 6380

    @property
    def redis_url(self) -> str:
        return f"redis://localhost:{self.redis_port}"

    @property
    def redis_stream_key(self) -> str:
        return "soop:chats"


settings = Settings()
