#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import os
import uuid
from decimal import Decimal

import asyncpg

from wallet_ai.interfaces.api.deps.auth import hash_password

USERS = [
    ("cliente@test.com", "cliente", "Cliente Demo"),
    ("soporte@test.com", "soporte", "Soporte Demo"),
    ("compliance@test.com", "compliance", "Compliance Demo"),
]

# Destinatarios de demostracion. Dos usuarios comparten el nombre de pila a
# proposito ("María") para poder probar la ambiguedad de la busqueda por nombre.
RECIPIENTS = [
    ("maria.garcia@test.com", "cliente", "María García"),
    ("maria.gomez@test.com", "cliente", "María Gómez"),
    ("carlos.ruiz@test.com", "cliente", "Carlos Ruiz"),
]

# ONLY FOR DEMONSTRATION - DO NOT USE IN PRODUCTION
DEMO_PASSWORD = "password"


async def _ensure_system_account(conn: asyncpg.Connection) -> uuid.UUID:
    row = await conn.fetchrow("SELECT id FROM accounts WHERE is_system = true LIMIT 1")
    if row:
        return row["id"]
    srow = await conn.fetchrow("SELECT id FROM users WHERE email = 'system@wallet.ai'")
    if srow:
        suid = srow["id"]
        await conn.execute("UPDATE users SET role='cliente', password_hash='INVALID-SYSTEM' WHERE id=$1", suid)
    else:
        suid = uuid.uuid4()
        await conn.execute(
            "INSERT INTO users (id, email, role, password_hash, created_at, display_name) "
            "VALUES ($1,$2,$3,$4,now(),$5) "
            "ON CONFLICT (email) DO UPDATE SET role='cliente', password_hash='INVALID-SYSTEM', "
            "display_name='Sistema Wallet'",
            suid,
            "system@wallet.ai",
            "cliente",
            "INVALID-SYSTEM",
            "Sistema Wallet",
        )
    await conn.execute(
        "UPDATE users SET display_name = COALESCE(display_name, 'Sistema Wallet') WHERE id = $1",
        suid,
    )
    acc_id = uuid.uuid4()
    await conn.execute(
        "INSERT INTO accounts (id, user_id, currency, is_system, created_at) VALUES ($1,$2,$3,true,now())",
        acc_id,
        suid,
        "COP",
    )
    return acc_id


async def _fund_account(conn: asyncpg.Connection, account_id: uuid.UUID, amount: Decimal, key: str) -> None:
    row = await conn.fetchrow("SELECT id FROM ledger_transactions WHERE idempotency_key = $1", key)
    if row:
        return
    sys = await _ensure_system_account(conn)
    async with conn.transaction():
        txn_id = await conn.fetchval(
            "INSERT INTO ledger_transactions (id, from_user_id, from_account_id, idempotency_key, kind, request_hash) "
            "VALUES (gen_random_uuid(), $1, $2, $3, 'funding', $4) RETURNING id",
            None,
            None,
            key,
            key,
        )
        await conn.execute(
            "INSERT INTO ledger_entries (id, transaction_id, account_id, entry_type, amount) "
            "VALUES (gen_random_uuid(), $1, $2, 'DEBIT', $3)",
            txn_id,
            sys,
            amount,
        )
        await conn.execute(
            "INSERT INTO ledger_entries (id, transaction_id, account_id, entry_type, amount) "
            "VALUES (gen_random_uuid(), $1, $2, 'CREDIT', $3)",
            txn_id,
            account_id,
            amount,
        )
        await conn.execute("SET CONSTRAINTS ALL IMMEDIATE")


async def seed() -> None:
    if os.environ.get("APP_ENV") != "dev":
        raise RuntimeError("seed.py may only run in dev (APP_ENV=dev)")
    from wallet_ai.infrastructure.config.settings import Settings

    dsn = Settings().asyncpg_dsn
    conn = await asyncpg.connect(dsn)

    try:
        await _ensure_system_account(conn)
        for email, role, display_name in [*USERS, *RECIPIENTS]:
            row = await conn.fetchrow("SELECT id FROM users WHERE email = $1", email)
            if row:
                uid = row["id"]
                await conn.execute(
                    "UPDATE users SET display_name = $2 WHERE id = $1",
                    uid,
                    display_name,
                )
            else:
                uid = uuid.uuid4()
                await conn.execute(
                    "INSERT INTO users (id, email, role, password_hash, display_name, created_at) "
                    "VALUES ($1,$2,$3,$4,$5,now())",
                    uid,
                    email,
                    role,
                    hash_password(DEMO_PASSWORD),
                    display_name,
                )
            acc = await conn.fetchrow("SELECT id FROM accounts WHERE user_id = $1", uid)
            if not acc:
                acc_id = uuid.uuid4()
                await conn.execute(
                    "INSERT INTO accounts (id, user_id, currency, created_at) VALUES ($1,$2,$3,now())",
                    acc_id,
                    uid,
                    "COP",
                )
                await _fund_account(conn, acc_id, Decimal("1000.00"), f"fund-{acc_id}")
            else:
                await _fund_account(conn, acc["id"], Decimal("1000.00"), f"fund-{acc['id']}")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(seed())
