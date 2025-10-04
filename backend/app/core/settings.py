from __future__ import annotations

"""Application settings management using Pydantic Settings."""

from functools import lru_cache
from typing import Any

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Strongly typed application settings."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    ENV: str = Field(default="development", description="Deployment environment name.")
    LOG_LEVEL: str = Field(default="INFO", description="Application log level.")
    DATABASE_URL: str = Field(
        default="postgresql+psycopg://postgres:postgres@localhost:5432/pocket_db_1",
        description="Primary database connection string.",
    )
    ALLOWED_ORIGINS: list[str] = Field(
        default_factory=lambda: [
            "http://localhost:3000",
            "http://localhost:8000",
            "http://localhost:5173",
            "http://127.0.0.1:5173",
        ],
        description="Comma-separated list of allowed CORS origins.",
    )
    JWT_SECRET: str = Field(default="change-me", description="JWT signing secret placeholder.")
    JWT_ISSUER: str = Field(default="https://auth.pocket.ai", description="Expected JWT issuer.")
    JWT_AUDIENCE: str = Field(default="pocket-ai-api", description="Expected JWT audience.")
    JWT_CLOCK_SKEW_SECONDS: int = Field(default=60, description="Allowed JWT clock skew in seconds.")
    JWT_ACCESS_TTL_SECONDS: int = Field(default=900, description="Access token lifetime in seconds.")
    CAPTCHA_SECRET: str = Field(default="", description="Shared secret used to validate CAPTCHA responses; blank disables enforcement.")
    GOOGLE_OAUTH_CLIENT_ID: str = Field(default="", description="OAuth client id for Google sign-in verification.")
    RATE_LIMIT_REGISTRATION_SESSION: str = Field(default="5/min", description="Rate limit for starting registration sessions.")
    RATE_LIMIT_REGISTRATION_BUSINESS: str = Field(default="10/min", description="Rate limit for business profile updates.")
    RATE_LIMIT_REGISTRATION_AGENT: str = Field(default="10/min", description="Rate limit for agent configuration requests.")
    RATE_LIMIT_REGISTRATION_UPLOADS: str = Field(default="5/min", description="Rate limit for uploads attachment requests.")
    RATE_LIMIT_REGISTRATION_COMPLETE: str = Field(default="10/min", description="Rate limit for completion requests.")

    @field_validator("ALLOWED_ORIGINS", mode="before")
    @classmethod
    def split_origins(cls, value: Any) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        if isinstance(value, (list, tuple, set)):
            return [str(origin).strip() for origin in value if str(origin).strip()]
        raise TypeError("ALLOWED_ORIGINS must be a list or comma-separated string")

    @property
    def is_production(self) -> bool:
        return self.ENV.lower() == "production"


@lru_cache
def get_settings() -> Settings:
    """Return cached application settings."""

    return Settings()
