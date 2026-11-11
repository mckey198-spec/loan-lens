"""Environment-driven runtime configuration (Architecture §14)."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime keys named for providers, retries, upload size, and worker lease TTL."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    APP_ENV: Literal["local", "test", "production"] = "local"
    DEBUG: bool = False
    WORKER_LEASE_TTL_SECONDS: int = 60
    MAX_PROCESSING_RETRIES: int = 3
    MAX_UPLOAD_SIZE_BYTES: int = 26_214_400  # 25 MiB
    DATABASE_URL: str = "postgresql://postgres:postgres@localhost:5432/loanlens"
    QUARANTINE_DIR: str = "./data/quarantine"
    CLEAN_UPLOADS_DIR: str = "./data/clean_uploads"
    CLAMAV_HOST: str = "localhost"
    CLAMAV_PORT: int = 3310
    # "mock" for tests / local without clamd; Compose sets "clamav".
    CLAMAV_BACKEND: Literal["clamav", "mock"] = "mock"
    # When backend is clamav and clamd is down, fall back to mock outside production.
    CLAMAV_FALLBACK_TO_MOCK: bool = True
    OPENAI_API_KEY: str = ""
    ANTHROPIC_API_KEY: str = ""

    # Session and Argon2id knobs are configuration (ADR-019 / TOOLS.md §2.2).
    # Values are not product security requirements; override via environment.
    SESSION_COOKIE_NAME: str = "loanlens_session"
    SESSION_IDLE_TIMEOUT_SECONDS: int = 1_800
    SESSION_ABSOLUTE_TIMEOUT_SECONDS: int = 86_400
    ARGON2_TIME_COST: int = 2
    ARGON2_MEMORY_COST: int = 65_536
    ARGON2_PARALLELISM: int = 1

    # Comma-separated browser origins for CORS. Empty disables cross-origin
    # credentialed access. Never use '*' with credentials (Architecture §12).
    CORS_ALLOWED_ORIGINS: str = "http://localhost:5173,http://127.0.0.1:5173"

    def cors_allowed_origins_list(self) -> list[str]:
        """Parse CORS_ALLOWED_ORIGINS into a strict allow-list."""
        return [
            origin.strip()
            for origin in self.CORS_ALLOWED_ORIGINS.split(",")
            if origin.strip()
        ]


@lru_cache
def get_settings() -> Settings:
    """Parse settings once; subsequent calls return the cached instance."""
    return Settings()
