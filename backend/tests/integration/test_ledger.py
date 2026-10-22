"""US1 - Ledger: invariantes de doble entrada, idempotencia e inmutabilidad.

Tests de integración contra Postgres real (ver `backend/README.md`).

Reglas de estos tests:
- No asumen una base vacía: cada test crea sus propios usuarios/cuentas.
- No afirman conteos globales: se verifica por id (fila concreta).
- Los rechazos se comprueban por clase de error cuando es posible
  (`asyncpg.exceptions.*`) y, cuando el error lo emite un trigger propio,
  también por el mensaje estable "append-only".
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import asyncpg
import pytest

AMOUNT = Decimal("100.00")
OTHER_AMOUNT = Decimal("50.25")


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
async def _new_user(conn: asyncpg.Connection) -> uuid.UUID:
    user_id = uuid.uuid4()
    await conn.execute(
        "INSERT INTO users (id, email, role) VALUES ($1, $2, 'cliente')",
        user_id,
        f"ledger-{uuid.uuid4().hex}@test.com",
    )
    return user_id


async def _new_account(conn: asyncpg.Connection, user_id: uuid.UUID) -> uuid.UUID:
    account_id = uuid.uuid4()
    await conn.execute(
        "INSERT INTO accounts (id, user_id, currency) VALUES ($1, $2, 'COP')",
        account_id,
        user_id,
    )
    return account_id


async def _insert_transaction(
    conn: asyncpg.Connection,
    from_user: uuid.UUID,
    from_account: uuid.UUID,
    key: str,
) -> uuid.UUID:
    txn_id = uuid.uuid4()
    await conn.execute(
        "INSERT INTO ledger_transactions (id, from_user_id, from_account_id, idempotency_key) "
        "VALUES ($1, $2, $3, $4)",
        txn_id,
        from_user,
        from_account,
        key,
    )
    return txn_id


async def _insert_entry(
    conn: asyncpg.Connection,
    txn_id: uuid.UUID,
    account_id: uuid.UUID,
    entry_type: str,
    amount: Decimal,
) -> uuid.UUID:
    entry_id = uuid.uuid4()
    await conn.execute(
        "INSERT INTO ledger_entries (id, transaction_id, account_id, entry_type, amount) "
        "VALUES ($1, $2, $3, $4, $5)",
        entry_id,
        txn_id,
        account_id,
        entry_type,
        amount,
    )
    return entry_id


async def _create_balanced(db_conn: asyncpg.Connection) -> dict:
    """Crea una transacción de doble entrada balanceada y persistida."""
    from_user = await _new_user(db_conn)
    from_account = await _new_account(db_conn, from_user)
    to_user = await _new_user(db_conn)
    to_account = await _new_account(db_conn, to_user)
    await _fund(db_conn, from_account, AMOUNT)
    txn_id = uuid.uuid4()
    key = f"idem-{uuid.uuid4().hex}"

    async with db_conn.transaction():
        txn_id = await _insert_transaction(db_conn, from_user, from_account, key)
        debit_id = await _insert_entry(db_conn, txn_id, from_account, "DEBIT", AMOUNT)
        credit_id = await _insert_entry(db_conn, txn_id, to_account, "CREDIT", AMOUNT)
        await db_conn.execute("SET CONSTRAINTS ALL IMMEDIATE")

    return {
        "txn_id": txn_id,
        "debit_id": debit_id,
        "credit_id": credit_id,
        "from_user": from_user,
        "from_account": from_account,
        "to_user": to_user,
        "to_account": to_account,
        "key": key,
    }


async def _ensure_system_account(conn: asyncpg.Connection) -> uuid.UUID:
    row = await conn.fetchrow("SELECT id FROM accounts WHERE is_system = true LIMIT 1")
    if row:
        return row["id"]
    suid = uuid.uuid4()
    await conn.execute(
        "INSERT INTO users (id, email, role) VALUES ($1, $2, 'compliance') ON CONFLICT DO NOTHING",
        suid,
        f"sys-{uuid.uuid4().hex}@test.com",
    )
    sid = uuid.uuid4()
    await conn.execute(
        "INSERT INTO accounts (id, user_id, currency, is_system) VALUES ($1, $2, 'COP', true)",
        sid,
        suid,
    )
    return sid


async def _fund(conn: asyncpg.Connection, account_id: uuid.UUID, amount: Decimal) -> None:
    sys = await _ensure_system_account(conn)
    async with conn.transaction():
        txn_id = await conn.fetchval(
            "INSERT INTO ledger_transactions (id, from_user_id, from_account_id, idempotency_key, kind, request_hash) "
            "VALUES (gen_random_uuid(), $1, $2, $3, 'funding', $4) RETURNING id",
            None,
            None,
            f"fund-{account_id}-{uuid.uuid4().hex}",
            "fund",
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


# --------------------------------------------------------------------------
# 1. transacción balanceada se acepta
# --------------------------------------------------------------------------
@pytest.mark.integration
@pytest.mark.asyncio
async def test_balanced_transaction_is_accepted(db_conn):
    ctx = await _create_balanced(db_conn)

    txn = await db_conn.fetchrow(
        "SELECT id, idempotency_key FROM ledger_transactions WHERE id = $1",
        ctx["txn_id"],
    )
    assert txn is not None
    assert txn["idempotency_key"] == ctx["key"]

    entries = await db_conn.fetch(
        "SELECT entry_type, amount FROM ledger_entries WHERE transaction_id = $1 ORDER BY entry_type DESC",
        ctx["txn_id"],
    )
    assert len(entries) == 2
    debit, credit = entries
    assert debit["entry_type"] == "DEBIT"
    assert credit["entry_type"] == "CREDIT"
    assert Decimal(debit["amount"]) == AMOUNT
    assert Decimal(credit["amount"]) == AMOUNT


# --------------------------------------------------------------------------
# 2. transacción desbalanceada se rechaza (rollback siempre)
# --------------------------------------------------------------------------
@pytest.mark.integration
@pytest.mark.asyncio
async def test_unbalanced_transaction_is_rejected(db_conn):
    from_user = await _new_user(db_conn)
    from_account = await _new_account(db_conn, from_user)
    to_account = await _new_account(db_conn, await _new_user(db_conn))
    txn_id = uuid.uuid4()
    key = f"idem-{uuid.uuid4().hex}"

    with pytest.raises(asyncpg.exceptions.RaiseError) as exc:
        async with db_conn.transaction():
            txn_id = await _insert_transaction(db_conn, from_user, from_account, key)
            await _insert_entry(db_conn, txn_id, from_account, "DEBIT", AMOUNT)
            # el crédito no cuadra con el débito -> sum(DEBIT) != sum(CREDIT)
            await _insert_entry(db_conn, txn_id, to_account, "CREDIT", OTHER_AMOUNT)
            await db_conn.execute("SET CONSTRAINTS ALL IMMEDIATE")

    assert "unbalanced" in str(exc.value).lower()
    row = await db_conn.fetchrow("SELECT id FROM ledger_transactions WHERE id = $1", txn_id)
    assert row is None


@pytest.mark.integration
@pytest.mark.asyncio
async def test_transaction_without_debit_or_credit_is_rejected(db_conn):
    from_user = await _new_user(db_conn)
    from_account = await _new_account(db_conn, from_user)
    txn_id = uuid.uuid4()
    key = f"idem-{uuid.uuid4().hex}"

    with pytest.raises(asyncpg.exceptions.RaiseError) as exc:
        async with db_conn.transaction():
            txn_id = await _insert_transaction(db_conn, from_user, from_account, key)
            await db_conn.execute("SET CONSTRAINTS ALL IMMEDIATE")

    assert "unbalanced" in str(exc.value).lower()
    row = await db_conn.fetchrow("SELECT id FROM ledger_transactions WHERE id = $1", txn_id)
    assert row is None


# --------------------------------------------------------------------------
# 3. amount cero o negativo se rechaza
# --------------------------------------------------------------------------
@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.parametrize("bad_amount", [Decimal("0.00"), Decimal("-1.00"), Decimal("-0.01")])
async def test_non_positive_amount_is_rejected(db_conn, bad_amount):
    from_user = await _new_user(db_conn)
    from_account = await _new_account(db_conn, from_user)
    to_account = await _new_account(db_conn, await _new_user(db_conn))
    txn_id = uuid.uuid4()
    key = f"idem-{uuid.uuid4().hex}"

    with pytest.raises(asyncpg.exceptions.CheckViolationError):
        async with db_conn.transaction():
            txn_id = await _insert_transaction(db_conn, from_user, from_account, key)
            await _insert_entry(db_conn, txn_id, from_account, "DEBIT", bad_amount)
            await _insert_entry(db_conn, txn_id, to_account, "CREDIT", AMOUNT)
            await db_conn.execute("SET CONSTRAINTS ALL IMMEDIATE")

    row = await db_conn.fetchrow("SELECT id FROM ledger_transactions WHERE id = $1", txn_id)
    assert row is None
    entries = await db_conn.fetch("SELECT id FROM ledger_entries WHERE transaction_id = $1", txn_id)
    assert entries == []


# --------------------------------------------------------------------------
# 4. idempotency_key
# --------------------------------------------------------------------------
@pytest.mark.integration
@pytest.mark.asyncio
async def test_duplicate_idempotency_key_for_same_user_and_account_is_rejected(db_conn):
    from_user = await _new_user(db_conn)
    from_account = await _new_account(db_conn, from_user)
    to_user = await _new_user(db_conn)
    to_account = await _new_account(db_conn, to_user)
    await _fund(db_conn, from_account, AMOUNT)
    await _fund(db_conn, to_account, AMOUNT)
    key = f"idem-{uuid.uuid4().hex}"

    async with db_conn.transaction():
        txn_id = await _insert_transaction(db_conn, from_user, from_account, key)
        await _insert_entry(db_conn, txn_id, from_account, "DEBIT", AMOUNT)
        await _insert_entry(db_conn, txn_id, to_account, "CREDIT", AMOUNT)
        await db_conn.execute("SET CONSTRAINTS ALL IMMEDIATE")

    # mismo usuario + misma cuenta + misma clave -> rechazado
    with pytest.raises(asyncpg.exceptions.UniqueViolationError):
        async with db_conn.transaction():
            await _insert_transaction(db_conn, from_user, from_account, key)

    # otro usuario (y otra cuenta) con la misma clave -> aceptado
    async with db_conn.transaction():
        other_txn = await _insert_transaction(db_conn, to_user, to_account, key)
        await _insert_entry(db_conn, other_txn, to_account, "DEBIT", AMOUNT)
        await _insert_entry(db_conn, other_txn, from_account, "CREDIT", AMOUNT)
        await db_conn.execute("SET CONSTRAINTS ALL IMMEDIATE")


# --------------------------------------------------------------------------
# 5. inmutabilidad: UPDATE / DELETE / TRUNCATE -> "append-only"
# --------------------------------------------------------------------------
@pytest.mark.integration
@pytest.mark.asyncio
async def test_update_is_rejected_and_row_survives(db_conn):
    ctx = await _create_balanced(db_conn)

    with pytest.raises(asyncpg.exceptions.RaiseError, match="append-only"):
        await db_conn.execute("UPDATE ledger_entries SET amount = 999 WHERE id = $1", ctx["debit_id"])

    row = await db_conn.fetchrow("SELECT amount FROM ledger_entries WHERE id = $1", ctx["debit_id"])
    assert row is not None
    assert Decimal(row["amount"]) == AMOUNT


@pytest.mark.integration
@pytest.mark.asyncio
async def test_delete_is_rejected_and_row_survives(db_conn):
    ctx = await _create_balanced(db_conn)

    with pytest.raises(asyncpg.exceptions.RaiseError, match="append-only"):
        await db_conn.execute("DELETE FROM ledger_entries WHERE id = $1", ctx["debit_id"])

    row = await db_conn.fetchrow("SELECT id FROM ledger_entries WHERE id = $1", ctx["debit_id"])
    assert row is not None


@pytest.mark.integration
@pytest.mark.asyncio
async def test_update_transaction_is_rejected_and_row_survives(db_conn):
    ctx = await _create_balanced(db_conn)

    with pytest.raises(asyncpg.exceptions.RaiseError, match="append-only"):
        await db_conn.execute(
            "UPDATE ledger_transactions SET idempotency_key = 'otro' WHERE id = $1",
            ctx["txn_id"],
        )

    row = await db_conn.fetchrow(
        "SELECT idempotency_key FROM ledger_transactions WHERE id = $1",
        ctx["txn_id"],
    )
    assert row is not None
    assert row["idempotency_key"] == ctx["key"]


@pytest.mark.integration
@pytest.mark.asyncio
async def test_delete_transaction_is_rejected_and_row_survives(db_conn):
    ctx = await _create_balanced(db_conn)

    with pytest.raises(asyncpg.exceptions.RaiseError, match="append-only"):
        await db_conn.execute("DELETE FROM ledger_transactions WHERE id = $1", ctx["txn_id"])

    row = await db_conn.fetchrow("SELECT id FROM ledger_transactions WHERE id = $1", ctx["txn_id"])
    assert row is not None


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "statement",
    [
        "TRUNCATE ledger_entries",
        "TRUNCATE ledger_transactions CASCADE",
        "TRUNCATE accounts CASCADE",
        "TRUNCATE users CASCADE",
    ],
)
async def test_truncate_is_rejected_and_rows_survive(db_conn, statement):
    ctx = await _create_balanced(db_conn)

    transaction = db_conn.transaction()
    await transaction.start()
    try:
        with pytest.raises(asyncpg.exceptions.RaiseError, match="append-only"):
            await db_conn.execute(statement)
    finally:
        await transaction.rollback()

    row = await db_conn.fetchrow("SELECT id FROM ledger_entries WHERE id = $1", ctx["debit_id"])
    assert row is not None
    row = await db_conn.fetchrow("SELECT id FROM ledger_transactions WHERE id = $1", ctx["txn_id"])
    assert row is not None


# --------------------------------------------------------------------------
# 6. rol wallet_app: INSERT/SELECT sí; UPDATE/DELETE/TRUNCATE/ALTER no
# --------------------------------------------------------------------------
@pytest.mark.integration
@pytest.mark.asyncio
async def test_wallet_app_can_insert_and_select_ledger_but_not_modify_it(db_conn):
    ctx = await _create_balanced(db_conn)
    await _fund(db_conn, ctx["from_account"], AMOUNT)

    await db_conn.execute("SET ROLE wallet_app")
    try:
        # INSERT + SELECT funcionan
        new_key = f"idem-{uuid.uuid4().hex}"
        async with db_conn.transaction():
            new_txn = await _insert_transaction(db_conn, ctx["from_user"], ctx["from_account"], new_key)
            await _insert_entry(db_conn, new_txn, ctx["from_account"], "DEBIT", AMOUNT)
            await _insert_entry(db_conn, new_txn, ctx["to_account"], "CREDIT", AMOUNT)
            await db_conn.execute("SET CONSTRAINTS ALL IMMEDIATE")

        rows = await db_conn.fetch("SELECT id FROM ledger_transactions WHERE id = $1", new_txn)
        assert len(rows) == 1
        rows = await db_conn.fetch("SELECT id FROM ledger_entries WHERE transaction_id = $1", new_txn)
        assert len(rows) == 2

        # UPDATE / DELETE / TRUNCATE / ALTER -> InsufficientPrivilegeError
        with pytest.raises(asyncpg.exceptions.InsufficientPrivilegeError):
            await db_conn.execute("UPDATE ledger_entries SET amount = 1 WHERE id = $1", ctx["debit_id"])
        with pytest.raises(asyncpg.exceptions.InsufficientPrivilegeError):
            await db_conn.execute("DELETE FROM ledger_entries WHERE id = $1", ctx["debit_id"])
        with pytest.raises(asyncpg.exceptions.InsufficientPrivilegeError):
            await db_conn.execute("TRUNCATE ledger_entries")
        with pytest.raises(asyncpg.exceptions.InsufficientPrivilegeError):
            await db_conn.execute("ALTER TABLE ledger_entries DISABLE TRIGGER ALL")
        with pytest.raises(asyncpg.exceptions.InsufficientPrivilegeError):
            await db_conn.execute("ALTER TABLE ledger_transactions DISABLE TRIGGER ALL")

        # la fila sigue existiendo
        row = await db_conn.fetchrow("SELECT amount FROM ledger_entries WHERE id = $1", ctx["debit_id"])
        assert row is not None
        assert Decimal(row["amount"]) == AMOUNT
    finally:
        await db_conn.execute("RESET ROLE")


# --------------------------------------------------------------------------
# 7. accounts.is_system (cuenta de sistema "fondeo")
# --------------------------------------------------------------------------
@pytest.mark.integration
@pytest.mark.asyncio
async def test_accounts_is_system_defaults_to_false_and_can_be_set(db_conn):
    user_id = await _new_user(db_conn)

    normal = uuid.uuid4()
    await db_conn.execute(
        "INSERT INTO accounts (id, user_id, currency) VALUES ($1, $2, 'COP')",
        normal,
        user_id,
    )
    row = await db_conn.fetchrow("SELECT is_system FROM accounts WHERE id = $1", normal)
    assert row is not None
    assert row["is_system"] is False

    system = uuid.uuid4()
    await db_conn.execute(
        "INSERT INTO accounts (id, user_id, currency, is_system) VALUES ($1, $2, 'COP', true)",
        system,
        user_id,
    )
    row = await db_conn.fetchrow("SELECT is_system FROM accounts WHERE id = $1", system)
    assert row is not None
    assert row["is_system"] is True
