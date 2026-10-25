"""Tramo B2 (T042): registro de herramientas del asistente.

Reglas del registro:
- `ToolContext` (user_id, role, trace_id) inmutable, entregado SIEMPRE por
  quien invoca; nunca por argumentos (modelos pydantic con extra='forbid').
- Nombres con 'confirm' o 'execute' prohibidos; expone exactamente cuatro.
- Roles: lectura para cliente/soporte/compliance (su propia cuenta);
  find_recipient y draft_transfer solo cliente.
- TODA invocacion (exito, validacion fallida, permiso denegado, error de
  dominio) escribe un evento tool_call con el trace_id y el payload
  enmascarado por el servicio de PII, nunca con valores crudos.
- draft_transfer crea un borrador pendiente y no toca el ledger.
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import FrozenInstanceError
from decimal import Decimal

import asyncpg
import pytest
from pydantic import BaseModel, ConfigDict

from wallet_ai.application.assistant.tools import (
    Tool,
    ToolContext,
    ToolDeps,
    ToolPermissionDenied,
    ToolValidationError,
    build_default_registry,
)
from wallet_ai.domain.recipients.errors import HandleNotResolvable
from wallet_ai.infrastructure.db.account_read_repository import AccountReadRepository
from wallet_ai.infrastructure.db.draft_repository import DraftRepository
from wallet_ai.infrastructure.db.recipient_repository import RecipientRepository
from wallet_ai.infrastructure.db.tool_call_audit import ConnectionToolCallAuditor

TOOL_NAMES = ["get_balance", "list_transactions", "find_recipient", "draft_transfer"]
HEX32 = re.compile(r"[0-9a-f]{32}")


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


async def _new_user(
    conn: asyncpg.Connection, role: str = "cliente", display_name: str | None = None
) -> uuid.UUID:
    uid = uuid.uuid4()
    await conn.execute(
        "INSERT INTO users (id, email, role, display_name) VALUES ($1, $2, $3, $4)",
        uid,
        f"tools-{uuid.uuid4().hex}@test.com",
        role,
        display_name,
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
    suid = await _new_user(conn, display_name="Sistema Wallet")
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
            f"tools-{uuid.uuid4().hex}",
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


def _deps(conn: asyncpg.Connection) -> ToolDeps:
    return ToolDeps(
        accounts=AccountReadRepository(conn),
        drafts=DraftRepository(conn),
        recipients=RecipientRepository(conn),
        auditor=ConnectionToolCallAuditor(conn),
    )


def _ctx(
    user_id: uuid.UUID, role: str = "cliente", trace_id: str | None = None
) -> ToolContext:
    return ToolContext(user_id=user_id, role=role, trace_id=trace_id or uuid.uuid4().hex)


async def _target(conn: asyncpg.Connection, display_name: str | None = None) -> uuid.UUID:
    """Usuario destino con cuenta; devuelve el id de su cuenta."""
    target_user = await _new_user(conn, display_name=display_name)
    return await _new_account(conn, target_user)


async def _handle(
    conn: asyncpg.Connection, *, requester: uuid.UUID, target_account: uuid.UUID
) -> str:
    from datetime import UTC, datetime, timedelta

    record = await RecipientRepository(conn).issue_handle(
        requester_user_id=requester,
        target_account_id=target_account,
        expires_at=datetime.now(UTC) + timedelta(minutes=15),
    )
    return record.handle


async def _payloads(
    conn: asyncpg.Connection, trace_id: str, user_id: uuid.UUID
) -> list[dict]:
    rows = await conn.fetch(
        "SELECT payload FROM audit_events WHERE trace_id = $1 AND user_id = $2",
        trace_id,
        user_id,
    )
    out = []
    for r in rows:
        payload = r["payload"]
        if isinstance(payload, str):
            payload = json.loads(payload)
        out.append(payload)
    return out


# --------------------------------------------------------------------------
# (a) list_tools: exactamente las cuatro; nombres confirm/execute prohibidos
# --------------------------------------------------------------------------
@pytest.mark.integration
@pytest.mark.asyncio
async def test_registry_exposes_exactly_four_tools(db_conn):
    registry = build_default_registry()
    assert [t.name for t in registry.list_tools()] == TOOL_NAMES


@pytest.mark.integration
@pytest.mark.asyncio
async def test_registry_rejects_confirm_or_execute_names(db_conn):
    class _Args(BaseModel):
        model_config = ConfigDict(extra="forbid")

    async def _noop(ctx: ToolContext, args, deps) -> dict:
        return {}

    registry = build_default_registry()
    for name in ("confirm_transfer", "run_execute", "get_balance_confirmed"):
        with pytest.raises(ValueError):
            registry.register(
                Tool(name=name, description="", args_model=_Args, roles=("cliente",), fn=_noop)
            )
    # el registro sigue exponiendo solo las cuatro originales
    assert [t.name for t in registry.list_tools()] == TOOL_NAMES


@pytest.mark.integration
@pytest.mark.asyncio
async def test_tool_context_is_immutable(db_conn):
    ctx = _ctx(uuid.uuid4())
    with pytest.raises(FrozenInstanceError):
        ctx.role = "compliance"


# --------------------------------------------------------------------------
# (b) validacion: argumentos de identidad/extras nunca llegan al caso de uso
# --------------------------------------------------------------------------
@pytest.mark.integration
@pytest.mark.asyncio
async def test_identity_and_extra_arguments_rejected_before_use_case(db_conn):
    user = await _new_user(db_conn)
    await _new_account(db_conn, user)
    target = await _target(db_conn)
    handle = await _handle(db_conn, requester=user, target_account=target)

    registry = build_default_registry()
    deps = _deps(db_conn)
    ctx = _ctx(user)
    before = await db_conn.fetchval(
        "SELECT count(*) FROM transfer_drafts WHERE user_id = $1", user
    )

    bad_calls = [
        ("draft_transfer", {"recipient_id": handle, "amount": "1.00", "from_account_id": str(uuid.uuid4())}),
        ("draft_transfer", {"recipient_id": handle, "amount": "1.00", "user_id": str(uuid.uuid4())}),
        ("draft_transfer", {"recipient_id": handle, "amount": "1.00", "account_id": str(uuid.uuid4())}),
        ("draft_transfer", {"recipient_id": handle, "amount": 1.00}),
        ("draft_transfer", {"recipient_id": handle, "amount": "0.00"}),
        ("draft_transfer", {"recipient_id": handle, "amount": "-1.00"}),
        ("draft_transfer", {"recipient_id": handle}),
        ("get_balance", {"user_id": str(uuid.uuid4())}),
        ("get_balance", {"account_id": str(uuid.uuid4())}),
        ("list_transactions", {"limit": 0}),
        ("list_transactions", {"limit": 101}),
        ("list_transactions", {"limit": 20, "user_id": str(uuid.uuid4())}),
    ]
    for tool_name, args in bad_calls:
        with pytest.raises(ToolValidationError):
            await registry.invoke(tool_name, ctx, args, deps)

    # nada llego al caso de uso: no hay borradores nuevos
    after = await db_conn.fetchval(
        "SELECT count(*) FROM transfer_drafts WHERE user_id = $1", user
    )
    assert after == before


@pytest.mark.integration
@pytest.mark.asyncio
async def test_account_uuid_as_handle_gets_same_generic_error(db_conn):
    user = await _new_user(db_conn)
    await _new_account(db_conn, user)
    target = await _target(db_conn)

    registry = build_default_registry()
    deps = _deps(db_conn)
    ctx = _ctx(user)

    with pytest.raises(HandleNotResolvable) as bogus_exc:
        await registry.invoke(
            "draft_transfer", ctx, {"recipient_id": "handle-que-no-existe", "amount": "1.00"}, deps
        )
    with pytest.raises(HandleNotResolvable) as uuid_exc:
        await registry.invoke(
            "draft_transfer", ctx, {"recipient_id": str(target), "amount": "1.00"}, deps
        )

    assert str(bogus_exc.value) == str(uuid_exc.value) == HandleNotResolvable.MESSAGE


# --------------------------------------------------------------------------
# (c) dos usuarios: saldos distintos, cada uno solo ve lo suyo
# --------------------------------------------------------------------------
@pytest.mark.integration
@pytest.mark.asyncio
async def test_two_users_get_their_own_balance_and_movements(db_conn):
    user_a = await _new_user(db_conn, display_name="Usuario A")
    user_b = await _new_user(db_conn, display_name="Usuario B")
    account_a = await _new_account(db_conn, user_a)
    account_b = await _new_account(db_conn, user_b)
    await _fund(db_conn, account_a, Decimal("100.00"))
    await _fund(db_conn, account_b, Decimal("250.00"))

    registry = build_default_registry()
    deps = _deps(db_conn)

    balance_a = await registry.invoke("get_balance", _ctx(user_a), {}, deps)
    balance_b = await registry.invoke("get_balance", _ctx(user_b), {}, deps)
    assert balance_a["balance"] == "100.00"
    assert balance_b["balance"] == "250.00"

    movements_a = await registry.invoke("list_transactions", _ctx(user_a), {"limit": 100}, deps)
    ids_a = {it["id"] for it in movements_a["items"]}
    real_a = {
        str(r["id"])
        for r in await db_conn.fetch(
            "SELECT id FROM ledger_entries WHERE account_id = $1", account_a
        )
    }
    real_b = {
        str(r["id"])
        for r in await db_conn.fetch(
            "SELECT id FROM ledger_entries WHERE account_id = $1", account_b
        )
    }
    assert ids_a == real_a
    assert ids_a.isdisjoint(real_b)


# --------------------------------------------------------------------------
# (d) soporte y compliance: leen lo suyo, no buscan ni crean borradores
# --------------------------------------------------------------------------
@pytest.mark.integration
@pytest.mark.asyncio
async def test_support_and_compliance_read_but_cannot_search_nor_draft(db_conn):
    for role in ("soporte", "compliance"):
        user = await _new_user(db_conn, role=role)
        account = await _new_account(db_conn, user)
        await _fund(db_conn, account, Decimal("50.00"))

        registry = build_default_registry()
        deps = _deps(db_conn)
        ctx = _ctx(user, role=role)

        # lectura de SU propia cuenta: permitida
        balance = await registry.invoke("get_balance", ctx, {}, deps)
        assert balance["balance"] == "50.00"

        # busqueda y borrador: permiso denegado
        with pytest.raises(ToolPermissionDenied):
            await registry.invoke("find_recipient", ctx, {"query": "Maria"}, deps)
        with pytest.raises(ToolPermissionDenied):
            await registry.invoke(
                "draft_transfer", ctx, {"recipient_id": "x", "amount": "1.00"}, deps
            )

        # y queda auditado como permiso denegado
        payloads = await _payloads(db_conn, ctx.trace_id, user)
        statuses = {p.get("status") for p in payloads}
        assert "permission_denied" in statuses


# --------------------------------------------------------------------------
# (e) draft_transfer: borrador pendiente, ledger intacto, sin PII en salida
# --------------------------------------------------------------------------
@pytest.mark.integration
@pytest.mark.asyncio
async def test_draft_transfer_leaves_pending_draft_and_untouched_ledger(db_conn):
    user = await _new_user(db_conn)
    account = await _new_account(db_conn, user)
    await _fund(db_conn, account, Decimal("10.00"))
    target_user = await _new_user(db_conn, display_name="Destino B2")
    target = await _new_account(db_conn, target_user)
    handle = await _handle(db_conn, requester=user, target_account=target)

    registry = build_default_registry()
    deps = _deps(db_conn)
    ctx = _ctx(user)
    before = await db_conn.fetchval("SELECT count(*) FROM ledger_transactions")

    result = await registry.invoke(
        "draft_transfer", ctx, {"recipient_id": handle, "amount": "2.50"}, deps
    )

    after = await db_conn.fetchval("SELECT count(*) FROM ledger_transactions")
    assert after == before
    assert result["status"] == "pendiente"
    assert result["amount"] == "2.50"
    assert result["draft_id"]

    row = await db_conn.fetchrow(
        "SELECT status, amount, from_account_id, to_account_id FROM transfer_drafts WHERE id = $1",
        uuid.UUID(result["draft_id"]),
    )
    assert row["status"] == "pendiente"
    assert str(row["amount"]) == "2.50"
    assert row["from_account_id"] == account
    assert row["to_account_id"] == target

    # la salida no expone correos ni ids de usuario/cuenta
    serialized = json.dumps(result, default=str)
    for secret in (str(user), str(account), str(target), str(target_user)):
        assert secret not in serialized
    assert "@" not in serialized


@pytest.mark.integration
@pytest.mark.asyncio
async def test_find_recipient_returns_masked_name_and_handle_only(db_conn):
    tag = uuid.uuid4().hex[:8]
    user = await _new_user(db_conn, display_name=f"Buscador {tag}")
    await _new_account(db_conn, user)
    target_user = await _new_user(db_conn, display_name=f"María{tag} García")
    target = await _new_account(db_conn, target_user)

    registry = build_default_registry()
    deps = _deps(db_conn)
    ctx = _ctx(user)

    result = await registry.invoke("find_recipient", ctx, {"query": f"María{tag}"}, deps)

    assert len(result["results"]) == 1
    item = result["results"][0]
    assert item["name_masked"] == f"María{tag} G***"
    assert item["recipient_id"]
    assert item["expires_at"]
    # el handle resuelve a la cuenta destino del usuario elegido
    from wallet_ai.application.recipients.resolve_handle import resolve_handle

    assert await resolve_handle(
        RecipientRepository(db_conn), requester_user_id=user, handle=item["recipient_id"]
    ) == target

    serialized = json.dumps(result, default=str)
    for secret in (str(target_user), str(target)):
        assert secret not in serialized
    assert "@" not in serialized


# --------------------------------------------------------------------------
# (f) auditoria: todo resultado deja su tool_call; PII nunca en crudo
# --------------------------------------------------------------------------
@pytest.mark.integration
@pytest.mark.asyncio
async def test_every_invocation_outcome_is_audited_with_32hex_trace(db_conn):
    user = await _new_user(db_conn)
    account = await _new_account(db_conn, user)
    await _fund(db_conn, account, Decimal("30.00"))

    registry = build_default_registry()
    deps = _deps(db_conn)
    trace_id = uuid.uuid4().hex
    ctx = _ctx(user, trace_id=trace_id)
    assert HEX32.fullmatch(trace_id)

    # exito
    await registry.invoke("get_balance", ctx, {}, deps)
    # validacion fallida
    with pytest.raises(ToolValidationError):
        await registry.invoke("get_balance", ctx, {"user_id": "x"}, deps)
    # error de dominio
    with pytest.raises(HandleNotResolvable):
        await registry.invoke(
            "draft_transfer", ctx, {"recipient_id": "handle-no-existe", "amount": "1.00"}, deps
        )

    payloads = await _payloads(db_conn, trace_id, user)
    statuses = {p.get("status") for p in payloads}
    assert {"ok", "invalid_arguments", "domain_error"} <= statuses
    # todos los tool_call de este usuario llevan trace de 32 hex
    bad_traces = await db_conn.fetchval(
        "SELECT count(*) FROM interactions WHERE user_id = $1 "
        "AND trace_id !~ '^[0-9a-f]{32}$'",
        user,
    )
    assert bad_traces == 0


@pytest.mark.integration
@pytest.mark.asyncio
async def test_find_recipient_query_pii_masked_in_audit(db_conn):
    tag = uuid.uuid4().hex[:8]
    user = await _new_user(db_conn, display_name=f"Cliente PII {tag}")
    await _new_account(db_conn, user)
    await _target(db_conn, display_name=f"María{tag} Visible")

    registry = build_default_registry()
    deps = _deps(db_conn)
    ctx = _ctx(user)

    card = "4111111111111111"  # valida por Luhn
    email = f"destinatario.{tag}@example.com"
    for query in (card, email):
        result = await registry.invoke("find_recipient", ctx, {"query": query}, deps)
        assert result["results"] == []

    payloads = await _payloads(db_conn, ctx.trace_id, user)
    joined = json.dumps(payloads, default=str)
    assert card not in joined
    assert email not in joined
    assert "<CARD>" in joined
    assert "<EMAIL>" in joined
