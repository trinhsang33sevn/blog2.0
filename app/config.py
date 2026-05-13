from pydantic_settings import BaseSettings
from functools import lru_cache
import sys

_UNSAFE_SECRET = "changeme-please-set-in-env"


class Settings(BaseSettings):
    SECRET_KEY: str = _UNSAFE_SECRET
    DATABASE_URL: str = "sqlite:///./data/autoblogspot.db"
    HOST: str = "127.0.0.1"
    PORT: int = 8000
    BASE_URL: str = "http://localhost:8000"
    DEBUG: bool = False

    # Email / SMTP (for password reset)
    SMTP_HOST: str = ""
    SMTP_PORT: int = 587
    SMTP_USER: str = ""
    SMTP_PASS: str = ""
    SMTP_FROM: str = ""
    SMTP_TLS: bool = True

    # Google integrations
    GOOGLE_ANALYTICS_ID: str = ""        # GA4 Measurement ID, e.g. G-XXXXXXXXXX
    GOOGLE_SITE_VERIFICATION: str = ""   # Google Search Console HTML tag verification code

    class Config:
        env_file = ".env"

    def validate_production(self) -> None:
        """Call on startup. Crash fast if insecure defaults are still set."""
        if self.SECRET_KEY == _UNSAFE_SECRET:
            print(
                "\n[FATAL] SECRET_KEY is still the default value!\n"
                "Generate one with:  python -c \"import secrets; print(secrets.token_hex(32))\"\n"
                "Then set it in .env as SECRET_KEY=<value>\n",
                file=sys.stderr,
            )
            if not self.DEBUG:
                sys.exit(1)

    @property
    def email_configured(self) -> bool:
        return bool(self.SMTP_HOST and self.SMTP_USER and self.SMTP_FROM)


@lru_cache()
def get_settings() -> Settings:
    return Settings()
