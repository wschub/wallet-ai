"""US2 tramo A - Borradores y confirmacion separada (TDD).

Tests de integracion contra Postgres real (ver `backend/README.md`).

Reglas:
- No asumen una base vacia: cada test crea sus propios usuarios/cuentas.
- No afirman conteos globales: se verifica por id (fila concreta) o por
  conteo acotado al transaccion/borrador recien creado.
- Los rechazos se comprueban por clase de error concreta cuando es posible.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import asyncpg
import pytest
from fastapi.testclient import TestClient

from wallet_ai.infrastructure.config.settings import Settings
from wallet_ai.infrastructure.db.ledger_repository import LedgerRepository
from wallet_ai.interfaces.api.deps.auth import create_token
from wallet_ai.interfaces.api.main import app

# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


async def _new_user(conn: asyncpg.Connection, role: str = "cliente") -> uuid.UUID:
    uid = uuid.uuid4()
    await conn.execute(
        "INSERT INTO users (id, email, role) VALUES ($1, $2, $3)",
        uid,
        f"draft-{uuid.uuid4().hex}@test.com",
        role,
    )
    return uid


async def _new_account(conn: asyncpg.Connection, user_id: uuid.UUID) -> uuid.UUID:
    aid = uuid.uuid4()
    await conn.execute(
        "INSERT INTO accounts (id, user_id, currency) VALUES ($1, $2, 'COP')",
        aid,
        user_id,
    )
    return aid


async def _ensure_system_account(conn: asyncpg.Connection) -> uuid.UUID:
    row = await conn.fetchrow("SELECT id FROM accounts WHERE is_system = true LIMIT 1")
    if row:
        return row["id"]
    suid = await _new_user(conn, "cliente")
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
            "INSERT INTO ledger_transactions "
            "(id, from_user_id, from_account_id, idempotency_key, kind, request_hash) "
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


async def _insert_draft(
    conn: asyncpg.Connection,
    *,
    user_id: uuid.UUID,
    from_account_id: uuid.UUID,
    to_account_id: uuid.UUID,
    amount: Decimal = Decimal("1.00"),
    status: str = "pendiente",
    expires_at: datetime | None = None,
) -> uuid.UUID:
    draft_id = uuid.uuid4()
    await conn.execute(
        "INSERT INTO transfer_drafts "
        "(id, user_id, from_account_id, to_account_id, amount, status, expires_at) "
        "VALUES ($1, $2, $3, $4, $5, $6, $7)",
        draft_id,
        user_id,
        from_account_id,
        to_account_id,
        amount,
        status,
        expires_at or (datetime.now(UTC) + timedelta(minutes=15)),
    )
    return draft_id


async def _ledger_txn_count(conn: asyncpg.Connection) -> int:
    return await conn.fetchval("SELECT count(*) FROM ledger_transactions")


async def _insert_real_transfer_txn(
    conn: asyncpg.Connection,
    *,
    user: uuid.UUID,
    src: uuid.UUID,
    dst: uuid.UUID,
    amount: Decimal = Decimal("1.00"),
) -> uuid.UUID:
    """Inserta una transferencia real balanceada y devuelve su id.

    Se usa para satisfacer el FK `transfer_drafts.ledger_transaction_id`.
    """
    async with conn.transaction():
        txn_id = await conn.fetchval(
            "INSERT INTO ledger_transactions "
            "(id, from_user_id, from_account_id, idempotency_key, kind, request_hash) "
            "VALUES (gen_random_uuid(), $1, $2, $3, 'transfer', $4) RETURNING id",
            user,
            src,
            f"seed-{uuid.uuid4().hex}",
            "seed",
        )
        await conn.execute(
            "INSERT INTO ledger_entries (id, transaction_id, account_id, entry_type, amount) "
            "VALUES (gen_random_uuid(), $1, $2, 'DEBIT', $3)",
            txn_id,
            src,
            amount,
        )
        await conn.execute(
            "INSERT INTO ledger_entries (id, transaction_id, account_id, entry_type, amount) "
            "VALUES (gen_random_uuid(), $1, $2, 'CREDIT', $3)",
            txn_id,
            dst,
            amount,
        )
        await conn.execute("SET CONSTRAINTS ALL IMMEDIATE")
    return txn_id


def _cliente_token(user_id: uuid.UUID) -> str:
    return create_token(sub=str(user_id), role="cliente", secret=Settings().AUTH_JWT_SECRET)


def _client() -> TestClient:
    Settings()
    return TestClient(app)


async def _confirm(conn: asyncpg.Connection, *, draft_id: uuid.UUID, user_id: uuid.UUID):
    """Cablea el caso de uso `confirm_draft` como lo hace el endpoint."""
    from wallet_ai.application.ledger.service import LedgerService
    from wallet_ai.application.transfers.confirm_draft import confirm_draft
    from wallet_ai.infrastructure.db.draft_repository import DraftRepository

    return await confirm_draft(
        drafts=DraftRepository(conn),
        ledger=LedgerService(LedgerRepository(conn)),
        transaction=conn.transaction,
        draft_id=draft_id,
        user_id=user_id,
    )


async def _fresh_conn() -> asyncpg.Connection:
    """Otra conexion del pool.

    Necesaria para leer estado que sobrevivio a una transaccion revertida: la
    conexion que ejecuto la confirmacion fallida no debe asumirse util para
    observar el resultado (su transaccion se revirtio).
    """
    return await asyncpg.connect(Settings().asyncpg_dsn)


# --------------------------------------------------------------------------
# T029a - confirmar mueve el dinero exactamente una vez
# --------------------------------------------------------------------------
@pytest.mark.integration
@pytest.mark.asyncio
async def test_confirm_draft_moves_money_exactly_once(db_conn):
    user = await _new_user(db_conn)
    src = await _new_account(db_conn, user)
    dst = await _new_account(db_conn, user)
    await _fund(db_conn, src, Decimal("10.00"))
    draft_id = await _insert_draft(
        db_conn, user_id=user, from_account_id=src, to_account_id=dst, amount=Decimal("3.00")
    )
    ledger = LedgerRepository(db_conn)

    before = await _ledger_txn_count(db_conn)
    result = await _confirm(db_conn, draft_id=draft_id, user_id=user)
    after = await _ledger_txn_count(db_conn)

    assert result["transaction_id"]
    assert after == before + 1

    row = await db_conn.fetchrow(
        "SELECT status, ledger_transaction_id, confirmed_at FROM transfer_drafts WHERE id = $1",
        draft_id,
    )
    assert row["status"] == "confirmada"
    assert str(row["ledger_transaction_id"]) == result["transaction_id"]
    assert row["confirmed_at"] is not None

    entries = await db_conn.fetch(
        "SELECT count(*) AS c FROM ledger_entries WHERE transaction_id = $1",
        uuid.UUID(result["transaction_id"]),
    )
    assert entries[0]["c"] == 2
    assert await ledger.get_balance(src) == Decimal("7.00")
    assert await ledger.get_balance(dst) == Decimal("3.00")


# --------------------------------------------------------------------------
# T029b - 20 confirmaciones simultaneas: una sola transaccion, mismo id
# --------------------------------------------------------------------------
@pytest.mark.integration
@pytest.mark.asyncio
async def test_twenty_concurrent_confirms_yield_single_transaction(db_conn):
    user = await _new_user(db_conn)
    src = await _new_account(db_conn, user)
    dst = await _new_account(db_conn, user)
    await _fund(db_conn, src, Decimal("10.00"))
    draft_id = await _insert_draft(
        db_conn, user_id=user, from_account_id=src, to_account_id=dst, amount=Decimal("3.00")
    )

    async def confirm_once():
        conn = await asyncpg.connect(Settings().asyncpg_dsn)
        try:
            return await _confirm(conn, draft_id=draft_id, user_id=user)
        finally:
            await conn.close()

    results = await asyncio.gather(*[confirm_once() for _ in range(20)], return_exceptions=True)

    ids = set()
    for r in results:
        assert not isinstance(r, BaseException), r
        ids.add(r["transaction_id"])
    assert len(ids) == 1

    txn_id = uuid.UUID(next(iter(ids)))
    entries = await db_conn.fetch(
        "SELECT count(*) AS c FROM ledger_entries WHERE transaction_id = $1", txn_id
    )
    assert entries[0]["c"] == 2
    # exactamente una transaccion en el ledger con el id del borrador como clave
    count = await db_conn.fetchval(
        "SELECT count(*) FROM ledger_transactions WHERE idempotency_key = $1", str(draft_id)
    )
    assert count == 1


# --------------------------------------------------------------------------
# T029b2 - bloqueo del borrador: confirm_draft usa SELECT ... FOR UPDATE
# --------------------------------------------------------------------------
@pytest.mark.integration
@pytest.mark.asyncio
async def test_confirm_draft_waits_for_row_lock(db_conn):
    """Prueba de bloqueo, con el mismo patron del test del ledger.

    Una segunda conexion abre una transaccion y toma el borrador con
    `FOR KEY SHARE`. Esa es la unica modalidad que aisla el bloqueo del
    repositorio: `FOR KEY SHARE` conflictua con el `SELECT ... FOR UPDATE` que
    hace `DraftRepository.get(for_update=True)` (y por tanto retiene la
    confirmacion), pero NO conflictua con el `FOR NO KEY UPDATE` que toma el
    `UPDATE transfer_drafts ... SET status='confirmada'` ni con las lecturas de
    FK. Asi el test distingue "el SELECT bloquea" de "la escritura final
    bloquea": si se quita ` FOR UPDATE` del repositorio, la confirmacion pasa de
    largo y el test FALLA (mutacion comprobada).

    `confirm_draft` corre como tarea: NO debe terminar mientras el bloqueo siga
    vivo. Al commitear quien lo retiene, la tarea termina con exito y deja UNA
    sola transaccion en el ledger.
    """
    user = await _new_user(db_conn)
    src = await _new_account(db_conn, user)
    dst = await _new_account(db_conn, user)
    await _fund(db_conn, src, Decimal("10.00"))
    draft_id = await _insert_draft(
        db_conn, user_id=user, from_account_id=src, to_account_id=dst, amount=Decimal("3.00")
    )

    holder = await _fresh_conn()
    tr = holder.transaction()
    await tr.start()
    try:
        await holder.execute(
            "SELECT id FROM transfer_drafts WHERE id = $1 FOR KEY SHARE", draft_id
        )

        task = asyncio.create_task(_confirm(db_conn, draft_id=draft_id, user_id=user))
        done, pending = await asyncio.wait({task}, timeout=0.5)
        assert task not in done, "confirm_draft no deberia haber terminado con el borrador bloqueado"
        assert task in pending, "confirm_draft deberia seguir esperando el bloqueo del borrador"

        # Al soltar el bloqueo, la confirmacion procede.
        await tr.commit()
        result = await asyncio.wait_for(task, timeout=15)
    finally:
        if holder.is_in_transaction():
            await tr.rollback()
        await holder.close()

    assert result["transaction_id"]
    count = await db_conn.fetchval(
        "SELECT count(*) FROM ledger_transactions WHERE idempotency_key = $1", str(draft_id)
    )
    assert count == 1
    row = await db_conn.fetchrow(
        "SELECT status, ledger_transaction_id FROM transfer_drafts WHERE id = $1", draft_id
    )
    assert row["status"] == "confirmada"
    assert str(row["ledger_transaction_id"]) == result["transaction_id"]


# --------------------------------------------------------------------------
# T029c - borrador vencido: falla, queda 'expirada', ledger intacto
# --------------------------------------------------------------------------
@pytest.mark.integration
@pytest.mark.asyncio
async def test_confirm_expired_draft_marks_expired_and_touches_nothing(db_conn):
    from wallet_ai.domain.transfers.errors import DraftExpired

    user = await _new_user(db_conn)
    src = await _new_account(db_conn, user)
    dst = await _new_account(db_conn, user)
    await _fund(db_conn, src, Decimal("10.00"))
    draft_id = await _insert_draft(
        db_conn,
        user_id=user,
        from_account_id=src,
        to_account_id=dst,
        amount=Decimal("3.00"),
        expires_at=datetime.now(UTC) - timedelta(minutes=1),
    )
    before = await _ledger_txn_count(db_conn)

    with pytest.raises(DraftExpired):
        await _confirm(db_conn, draft_id=draft_id, user_id=user)

    after = await _ledger_txn_count(db_conn)
    assert after == before
    # El estado se lee desde OTRA conexion: la transaccion que ejecuto la
    # confirmacion fallida fue revertida, y no debe asumirse que esa conexion
    # observa el 'expirada' que el caso de uso confirmo antes de fallar.
    observer = await _fresh_conn()
    try:
        row = await observer.fetchrow("SELECT status FROM transfer_drafts WHERE id = $1", draft_id)
    finally:
        await observer.close()
    assert row["status"] == "expirada"


# --------------------------------------------------------------------------
# T029d - saldo insuficiente al confirmar: falla, borrador queda pendiente
# --------------------------------------------------------------------------
@pytest.mark.integration
@pytest.mark.asyncio
async def test_confirm_insufficient_funds_keeps_draft_pendiente(db_conn):
    from wallet_ai.domain.ledger.errors import InsufficientFunds

    user = await _new_user(db_conn)
    src = await _new_account(db_conn, user)
    dst = await _new_account(db_conn, user)
    await _fund(db_conn, src, Decimal("1.00"))
    draft_id = await _insert_draft(
        db_conn, user_id=user, from_account_id=src, to_account_id=dst, amount=Decimal("50.00")
    )
    before = await _ledger_txn_count(db_conn)

    with pytest.raises(InsufficientFunds):
        await _confirm(db_conn, draft_id=draft_id, user_id=user)

    after = await _ledger_txn_count(db_conn)
    assert after == before
    row = await db_conn.fetchrow("SELECT status FROM transfer_drafts WHERE id = $1", draft_id)
    assert row["status"] == "pendiente"


# --------------------------------------------------------------------------
# T029e - borrador ajeno -> DraftNotFound
# --------------------------------------------------------------------------
@pytest.mark.integration
@pytest.mark.asyncio
async def test_confirm_foreign_draft_not_found(db_conn):
    from wallet_ai.domain.transfers.errors import DraftNotFound

    owner = await _new_user(db_conn)
    other = await _new_user(db_conn)
    src = await _new_account(db_conn, owner)
    dst = await _new_account(db_conn, owner)
    await _fund(db_conn, src, Decimal("10.00"))
    draft_id = await _insert_draft(
        db_conn, user_id=owner, from_account_id=src, to_account_id=dst, amount=Decimal("1.00")
    )

    with pytest.raises(DraftNotFound):
        await _confirm(db_conn, draft_id=draft_id, user_id=other)


# --------------------------------------------------------------------------
# T029f - confirmar un borrador ya confirmado devuelve el MISMO transaction_id
# --------------------------------------------------------------------------
@pytest.mark.integration
@pytest.mark.asyncio
async def test_confirm_already_confirmed_is_idempotent(db_conn):
    user = await _new_user(db_conn)
    src = await _new_account(db_conn, user)
    dst = await _new_account(db_conn, user)
    await _fund(db_conn, src, Decimal("10.00"))
    draft_id = await _insert_draft(
        db_conn, user_id=user, from_account_id=src, to_account_id=dst, amount=Decimal("2.00")
    )

    first = await _confirm(db_conn, draft_id=draft_id, user_id=user)
    second = await _confirm(db_conn, draft_id=draft_id, user_id=user)
    assert first["transaction_id"] == second["transaction_id"]

    count = await db_conn.fetchval(
        "SELECT count(*) FROM ledger_transactions WHERE idempotency_key = $1", str(draft_id)
    )
    assert count == 1


# --------------------------------------------------------------------------
# T029g - rechazado/expirado al confirmar -> DraftNotConfirmable (409)
# --------------------------------------------------------------------------
@pytest.mark.integration
@pytest.mark.asyncio
async def test_confirm_rejected_or_expired_draft_conflict(db_conn):
    from wallet_ai.domain.transfers.errors import DraftNotConfirmable

    user = await _new_user(db_conn)
    src = await _new_account(db_conn, user)
    dst = await _new_account(db_conn, user)
    await _fund(db_conn, src, Decimal("10.00"))

    rejected = await _insert_draft(
        db_conn, user_id=user, from_account_id=src, to_account_id=dst, status="rechazada"
    )
    with pytest.raises(DraftNotConfirmable):
        await _confirm(db_conn, draft_id=rejected, user_id=user)

    expired = await _insert_draft(
        db_conn, user_id=user, from_account_id=src, to_account_id=dst, status="expirada"
    )
    with pytest.raises(DraftNotConfirmable):
        await _confirm(db_conn, draft_id=expired, user_id=user)


# --------------------------------------------------------------------------
# T029h - triggers: UPDATE de campos prohibidos, DELETE y TRUNCATE (<append-only>)
# --------------------------------------------------------------------------
@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "column,value",
    [
        ("user_id", "gen_random_uuid()"),
        ("from_account_id", "gen_random_uuid()"),
        ("to_account_id", "gen_random_uuid()"),
        ("amount", "999.00"),
        ("expires_at", "now() + interval '1 day'"),
    ],
)
async def test_trigger_blocks_immutable_field_update(db_conn, column, value):
    user = await _new_user(db_conn)
    src = await _new_account(db_conn, user)
    dst = await _new_account(db_conn, user)
    draft_id = await _insert_draft(
        db_conn, user_id=user, from_account_id=src, to_account_id=dst
    )

    with pytest.raises(asyncpg.exceptions.RaiseError):
        await db_conn.execute(
            f"UPDATE transfer_drafts SET {column} = {value} WHERE id = $1", draft_id
        )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_trigger_blocks_status_transition_from_terminal(db_conn):
    user = await _new_user(db_conn)
    src = await _new_account(db_conn, user)
    dst = await _new_account(db_conn, user)
    await _fund(db_conn, src, Decimal("5.00"))
    real_txn = await _insert_real_transfer_txn(db_conn, user=user, src=src, dst=dst)
    confirmed = uuid.uuid4()
    await db_conn.execute(
        "INSERT INTO transfer_drafts "
        "(id, user_id, from_account_id, to_account_id, amount, status, expires_at, "
        " ledger_transaction_id, confirmed_at) "
        "VALUES ($1, $2, $3, $4, 1.00, 'confirmada', now() + interval '15 min', $5, now())",
        confirmed,
        user,
        src,
        dst,
        real_txn,
    )

    with pytest.raises(asyncpg.exceptions.RaiseError):
        await db_conn.execute(
            "UPDATE transfer_drafts SET status = 'rechazada' WHERE id = $1", confirmed
        )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_trigger_requires_ledger_transaction_and_confirmed_at_on_confirm(db_conn):
    user = await _new_user(db_conn)
    src = await _new_account(db_conn, user)
    dst = await _new_account(db_conn, user)
    draft_id = await _insert_draft(
        db_conn, user_id=user, from_account_id=src, to_account_id=dst
    )

    # pasar a 'confirmada' sin ledger_transaction_id ni confirmed_at -> rechazado
    with pytest.raises(asyncpg.exceptions.RaiseError):
        await db_conn.execute(
            "UPDATE transfer_drafts SET status = 'confirmada' WHERE id = $1", draft_id
        )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_delete_is_rejected(db_conn):
    user = await _new_user(db_conn)
    src = await _new_account(db_conn, user)
    dst = await _new_account(db_conn, user)
    draft_id = await _insert_draft(
        db_conn, user_id=user, from_account_id=src, to_account_id=dst
    )

    with pytest.raises(asyncpg.exceptions.RaiseError, match="append-only"):
        await db_conn.execute("DELETE FROM transfer_drafts WHERE id = $1", draft_id)

    row = await db_conn.fetchrow("SELECT id FROM transfer_drafts WHERE id = $1", draft_id)
    assert row is not None


@pytest.mark.integration
@pytest.mark.asyncio
async def test_truncate_is_rejected(db_conn):
    user = await _new_user(db_conn)
    src = await _new_account(db_conn, user)
    dst = await _new_account(db_conn, user)
    await _insert_draft(db_conn, user_id=user, from_account_id=src, to_account_id=dst)

    transaction = db_conn.transaction()
    await transaction.start()
    try:
        with pytest.raises(asyncpg.exceptions.RaiseError, match="append-only"):
            await db_conn.execute("TRUNCATE transfer_drafts")
    finally:
        await transaction.rollback()


# --------------------------------------------------------------------------
# T029i - reglas de origen/destino: no iguales, no cuentas de sistema
# --------------------------------------------------------------------------
@pytest.mark.integration
@pytest.mark.asyncio
async def test_draft_rejects_same_origin_and_destination(db_conn):
    user = await _new_user(db_conn)
    src = await _new_account(db_conn, user)

    with pytest.raises(asyncpg.exceptions.CheckViolationError):
        await _insert_draft(
            db_conn, user_id=user, from_account_id=src, to_account_id=src
        )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_draft_rejects_system_accounts(db_conn):
    user = await _new_user(db_conn)
    src = await _new_account(db_conn, user)
    sys = await _ensure_system_account(db_conn)

    with pytest.raises(asyncpg.exceptions.RaiseError, match="system accounts"):
        await _insert_draft(
            db_conn, user_id=user, from_account_id=src, to_account_id=sys
        )
    with pytest.raises(asyncpg.exceptions.RaiseError, match="system accounts"):
        await _insert_draft(
            db_conn, user_id=user, from_account_id=sys, to_account_id=src
        )


# --------------------------------------------------------------------------
# T029j - wallet_app: SELECT/INSERT/UPDATE si, DELETE no (InsufficientPrivilege)
# --------------------------------------------------------------------------
@pytest.mark.integration
@pytest.mark.asyncio
async def test_wallet_app_can_update_draft_but_not_delete(db_conn):
    user = await _new_user(db_conn)
    src = await _new_account(db_conn, user)
    dst = await _new_account(db_conn, user)
    draft_id = await _insert_draft(
        db_conn, user_id=user, from_account_id=src, to_account_id=dst
    )

    await db_conn.execute("SET ROLE wallet_app")
    try:
        # SELECT + INSERT + UPDATE permitidos
        rows = await db_conn.fetch("SELECT id FROM transfer_drafts WHERE id = $1", draft_id)
        assert len(rows) == 1
        new_id = uuid.uuid4()
        await db_conn.execute(
            "INSERT INTO transfer_drafts (id, user_id, from_account_id, to_account_id, amount, expires_at) "
            "VALUES ($1, $2, $3, $4, 1.00, now() + interval '15 min')",
            new_id,
            user,
            src,
            dst,
        )
        await db_conn.execute(
            "UPDATE transfer_drafts SET status = 'rechazada' WHERE id = $1", new_id
        )

        with pytest.raises(asyncpg.exceptions.InsufficientPrivilegeError):
            await db_conn.execute("DELETE FROM transfer_drafts WHERE id = $1", new_id)
    finally:
        await db_conn.execute("RESET ROLE")


# --------------------------------------------------------------------------
# T041a - endpoints: GET /drafts, GET /drafts/{id}, reject, confirm + trace/roles
# --------------------------------------------------------------------------
@pytest.mark.integration
@pytest.mark.asyncio
async def test_endpoints_drafts_flow_and_roles(db_conn):
    user = await _new_user(db_conn)
    src = await _new_account(db_conn, user)
    dst = await _new_account(db_conn, user)
    await _fund(db_conn, src, Decimal("10.00"))
    pending = await _insert_draft(
        db_conn, user_id=user, from_account_id=src, to_account_id=dst, amount=Decimal("2.00")
    )
    rejectable = await _insert_draft(
        db_conn, user_id=user, from_account_id=src, to_account_id=dst, amount=Decimal("1.00")
    )

    client = _client()
    token = _cliente_token(user)
    headers = {"Authorization": f"Bearer {token}"}

    # GET lista propia
    r = client.get("/drafts", headers=headers)
    assert r.status_code == 200
    assert "X-Trace-Id" in r.headers
    ids = {d["id"] for d in r.json()}
    assert str(pending) in ids and str(rejectable) in ids
    for d in r.json():
        assert isinstance(d["amount"], str)

    # GET individual
    r = client.get(f"/drafts/{pending}", headers=headers)
    assert r.status_code == 200
    assert r.json()["status"] == "pendiente"
    assert isinstance(r.json()["amount"], str)

    # reject
    r = client.post(f"/drafts/{rejectable}/reject", headers=headers)
    assert r.status_code == 200
    assert "X-Trace-Id" in r.headers
    row = await db_conn.fetchrow("SELECT status FROM transfer_drafts WHERE id = $1", rejectable)
    assert row["status"] == "rechazada"

    # confirm
    r = client.post(f"/drafts/{pending}/confirm", headers=headers)
    assert r.status_code == 200
    assert "X-Trace-Id" in r.headers
    body = r.json()
    assert body["draft_id"] == str(pending)
    assert body["transaction_id"]

    # confirmar otra vez -> mismo transaction_id (idempotente)
    r2 = client.post(f"/drafts/{pending}/confirm", headers=headers)
    assert r2.status_code == 200
    assert r2.json()["transaction_id"] == body["transaction_id"]

    # confirmar rechazado -> 409
    r3 = client.post(f"/drafts/{rejectable}/confirm", headers=headers)
    assert r3.status_code == 409

    # confirma el efecto en el ledger: exactamente una transaccion con la clave del borrador
    count = await db_conn.fetchval(
        "SELECT count(*) FROM ledger_transactions WHERE idempotency_key = $1", str(pending)
    )
    assert count == 1
    # sanity check usando el servicio directo (mismo id)
    again = await _confirm(db_conn, draft_id=pending, user_id=user)
    assert again["transaction_id"] == body["transaction_id"]


@pytest.mark.integration
@pytest.mark.asyncio
async def test_endpoints_require_cliente_role_and_auth(db_conn):
    user = await _new_user(db_conn)
    src = await _new_account(db_conn, user)
    dst = await _new_account(db_conn, user)
    draft_id = await _insert_draft(
        db_conn, user_id=user, from_account_id=src, to_account_id=dst
    )
    client = _client()

    # sin token -> 401
    assert client.get("/drafts").status_code == 401
    assert client.post(f"/drafts/{draft_id}/confirm").status_code == 401

    # rol compliance -> 403
    comp_token = create_token(sub=str(user), role="compliance", secret=Settings().AUTH_JWT_SECRET)
    headers = {"Authorization": f"Bearer {comp_token}"}
    assert client.get("/drafts", headers=headers).status_code == 403
    assert client.post(f"/drafts/{draft_id}/confirm", headers=headers).status_code == 403


@pytest.mark.integration
@pytest.mark.asyncio
async def test_endpoints_foreign_draft_404(db_conn):
    owner = await _new_user(db_conn)
    other = await _new_user(db_conn)
    src = await _new_account(db_conn, owner)
    dst = await _new_account(db_conn, owner)
    draft_id = await _insert_draft(
        db_conn, user_id=owner, from_account_id=src, to_account_id=dst
    )
    client = _client()
    headers = {"Authorization": f"Bearer {_cliente_token(other)}"}

    assert client.get(f"/drafts/{draft_id}", headers=headers).status_code == 404
    assert client.post(f"/drafts/{draft_id}/confirm", headers=headers).status_code == 404
