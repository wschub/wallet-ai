import asyncio
import os
from logging.config import fileConfig
from urllib.parse import urlparse

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = None


def _get_dsn() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL must be set in environment")
    return url


def _log_target(url: str) -> None:
    try:
        parsed = urlparse(url.replace("+asyncpg", ""))
        host = parsed.hostname or "unknown"
        port = parsed.port or "unknown"
        db = parsed.path.lstrip("/") or "unknown"
        print(f"Migrating to {host}:{port}/{db}")
    except Exception:
        pass


def run_migrations_offline() -> None:
    url = _get_dsn()
    _log_target(url)
    url = url.replace("postgresql+asyncpg://", "postgresql://").replace("postgres://", "postgresql://")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    url = _get_dsn()
    _log_target(url)
    config.set_main_option("sqlalchemy.url", url)
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
