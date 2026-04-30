from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    SECRET_KEY: str = "changeme-please-set-in-env"
    DATABASE_URL: str = "sqlite:///./data/autoblogspot.db"
    HOST: str = "127.0.0.1"
    PORT: int = 8000

    class Config:
        env_file = ".env"


@lru_cache()
def get_settings() -> Settings:
    return Settings()
