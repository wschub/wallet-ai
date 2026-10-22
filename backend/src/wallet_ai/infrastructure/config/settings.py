from __future__ import annotations

from typing import Any

try:
    from pydantic_settings import BaseSettings
except Exception:  # pragma: no cover
    from pydantic import BaseModel

    class BaseSettings(BaseModel):
        class Config:
            env_prefix = ""


class Settings(BaseSettings):
    LLM_PROVIDER: str = "fake"
    LLM_MODEL: str = "fake"
    EMBEDDING_PROVIDER: str = "fake"
    EMBEDDING_MODEL: str = "fake"
    RERANKER_PROVIDER: str = "fake"
    DATABASE_URL: str = "postgresql+asyncpg://localhost:5432/wallet"
    AUTH_JWT_SECRET: str = "dev-secret"
    APP_ENV: str = "dev"
    OPENAI_API_KEY: str | None = None
    AZURE_OPENAI_API_KEY: str | None = None

    @property
    def asyncpg_dsn(self) -> str:
        url = self.DATABASE_URL
        if url.startswith("postgresql+asyncpg://"):
            return url.replace("postgresql+asyncpg://", "postgresql://", 1)
        if url.startswith("postgres://"):
            return url.replace("postgres://", "postgresql://", 1)
        return url

    def model_post_init(self, __context: Any) -> None:
        # non-fake providers require key
        has_key = bool(self.OPENAI_API_KEY or self.AZURE_OPENAI_API_KEY)
        if self.LLM_PROVIDER != "fake" and not has_key:
            raise ValueError("API key required for non-fake provider")
        if self.EMBEDDING_PROVIDER != "fake" and not has_key:
            raise ValueError("API key required for non-fake provider")
        if self.RERANKER_PROVIDER != "fake" and not has_key:
            raise ValueError("API key required for non-fake provider")
        # prod must not use default secret
        if self.APP_ENV != "dev" and self.AUTH_JWT_SECRET == "change-me":
            raise ValueError("JWT secret must be changed in non-dev")
        if self.APP_ENV == "prod" and self.AUTH_JWT_SECRET == "dev-secret":
            raise ValueError("JWT secret must be changed in prod")
