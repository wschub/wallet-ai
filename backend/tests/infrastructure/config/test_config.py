import pytest

from wallet_ai.infrastructure.config.settings import Settings


def test_fake_providers_ok():
    s = Settings(
        LLM_PROVIDER="fake",
        LLM_MODEL="fake",
        EMBEDDING_PROVIDER="fake",
        EMBEDDING_MODEL="fake",
        RERANKER_PROVIDER="fake",
        DATABASE_URL="postgresql+asyncpg://x:x@localhost:5432/x",
        AUTH_JWT_SECRET="dev-secret",
        APP_ENV="dev",
    )
    assert s.LLM_PROVIDER == "fake"


def test_non_fake_requires_key():
    with pytest.raises(Exception):
        Settings(
            LLM_PROVIDER="openai",
            LLM_MODEL="gpt-4o",
            EMBEDDING_PROVIDER="fake",
            EMBEDDING_MODEL="fake",
            RERANKER_PROVIDER="fake",
            DATABASE_URL="postgresql+asyncpg://x:x@localhost:5432/x",
            AUTH_JWT_SECRET="dev-secret",
            APP_ENV="dev",
        )


def test_prod_default_secret_fails():
    with pytest.raises(Exception):
        Settings(
            LLM_PROVIDER="fake",
            LLM_MODEL="fake",
            EMBEDDING_PROVIDER="fake",
            EMBEDDING_MODEL="fake",
            RERANKER_PROVIDER="fake",
            DATABASE_URL="postgresql+asyncpg://x:x@localhost:5432/x",
            AUTH_JWT_SECRET="change-me",
            APP_ENV="prod",
        )
