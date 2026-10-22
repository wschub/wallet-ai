from wallet_ai.infrastructure.config.settings import Settings


def test_asyncpg_dsn_normalization():
    s = Settings(DATABASE_URL="postgresql+asyncpg://user:pass@host:5432/db")
    assert s.asyncpg_dsn == "postgresql://user:pass@host:5432/db"
    s2 = Settings(DATABASE_URL="postgres://user:pass@host:5432/db")
    assert s2.asyncpg_dsn == "postgresql://user:pass@host:5432/db"
    s3 = Settings(DATABASE_URL="postgresql://user:pass@host:5432/db")
    assert s3.asyncpg_dsn == "postgresql://user:pass@host:5432/db"
