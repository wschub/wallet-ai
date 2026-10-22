import os

import asyncpg
import pytest

from wallet_ai.infrastructure.config.settings import Settings


@pytest.fixture
async def db_conn():
    if not os.environ.get("DATABASE_URL"):
        raise RuntimeError("DATABASE_URL must be set in environment")
    dsn = Settings().asyncpg_dsn
    conn = await asyncpg.connect(dsn)
    try:
        yield conn
    finally:
        await conn.close()
