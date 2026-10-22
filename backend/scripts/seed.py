#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import uuid

import asyncpg

from wallet_ai.interfaces.api.deps.auth import hash_password

USERS = [
    ("cliente@test.com", "cliente"),
    ("soporte@test.com", "soporte"),
    ("compliance@test.com", "compliance"),
]


async def seed() -> None:
    from wallet_ai.infrastructure.config.settings import Settings

    dsn = Settings().asyncpg_dsn
    conn = await asyncpg.connect(dsn)

    try:
        for email, role in USERS:
            user_id = str(uuid.uuid4())
            # check exists
            row = await conn.fetchrow("SELECT id FROM users WHERE email = $1", email)
            if row:
                uid = row["id"]
            else:
                await conn.execute(
                    "INSERT INTO users (id, email, role, password_hash, created_at) VALUES ($1,$2,$3,$4,now())",
                    uuid.UUID(user_id),
                    email,
                    role,
                    hash_password("password"),
                )
                uid = uuid.UUID(user_id)
            # account
            acc = await conn.fetchrow("SELECT id FROM accounts WHERE user_id = $1", uid)
            if not acc:
                await conn.execute(
                    "INSERT INTO accounts (id, user_id, currency, created_at) VALUES ($1,$2,$3,now())",
                    uuid.uuid4(),
                    uid,
                    "COP",
                )
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(seed())
