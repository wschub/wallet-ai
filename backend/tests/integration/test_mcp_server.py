"""Tramo B2 (T042): servidor MCP sobre el SDK `mcp` 2.x.

API del SDK usada (mcp==2.3.0, verificada en .venv antes de escribir):
- Servidor: `from mcp.server.mcpserver import MCPServer` (FastMCP fue
  renombrado a MCPServer en mcp 2.x), registro con `MCPServer.add_tool`
  (esquema derivado de la firma del wrapper) y stdio con
  `MCPServer.run("stdio")`.
- Cliente en proceso (tests): `mcp.client._memory.InMemoryTransport`
  (transporte de pruebas del SDK) + `mcp.client.session.ClientSession`.

Identidad: al arrancar lee WALLET_MCP_TOKEN, lo valida con el mismo codigo
de auth de la API y construye el ToolContext una sola vez; sin token o con
token invalido se niega a arrancar.
"""

from __future__ import annotations

import json
import re
import time
import uuid
from decimal import Decimal

import asyncpg
import pytest
from mcp.client._memory import InMemoryTransport
from mcp.client.session import ClientSession

from wallet_ai.infrastructure.config.settings import Settings
from wallet_ai.interfaces.api.deps.auth import create_token
from wallet_ai.interfaces.mcp.server import MCPIdentityError, build_mcp_server

TOOL_NAMES = {"get_balance", "list_transactions", "find_recipient", "draft_transfer"}
HEX32 = re.compile(r"[0-9a-f]{32}")


async def _new_user(conn: asyncpg.Connection) -> uuid.UUID:
    uid = uuid.uuid4()
    await conn.execute(
        "INSERT INTO users (id, email, role) VALUES ($1, $2, 'cliente')",
        uid,
        f"mcp-{uuid.uuid4().hex}@test.com",
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


async def _fund(conn: asyncpg.Connection, account_id: uuid.UUID, amount: Decimal) -> None:
    sys = await conn.fetchrow("SELECT id FROM accounts WHERE is_system = true LIMIT 1")
    assert sys is not None
    async with conn.transaction():
        txn_id = await conn.fetchval(
            "INSERT INTO ledger_transactions "
            "(id, from_user_id, from_account_id, idempotency_key, kind, request_hash) "
            "VALUES (gen_random_uuid(), $1, $2, $3, 'funding', $4) RETURNING id",
            None,
            None,
            f"mcp-{uuid.uuid4().hex}",
            "fund",
        )
        await conn.execute(
            "INSERT INTO ledger_entries (id, transaction_id, account_id, entry_type, amount) "
            "VALUES (gen_random_uuid(), $1, $2, 'DEBIT', $3)",
            txn_id,
            sys["id"],
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


def _token(user_id: uuid.UUID, role: str = "cliente") -> str:
    return create_token(sub=str(user_id), role=role, secret=Settings().AUTH_JWT_SECRET)


async def _visible_target(conn: asyncpg.Connection, display_name: str) -> uuid.UUID:
    """Usuario destino con cuenta; devuelve el id de su cuenta."""
    target_user = uuid.uuid4()
    await conn.execute(
        "INSERT INTO users (id, email, role, display_name) VALUES ($1, $2, 'cliente', $3)",
        target_user,
        f"mcp-dest-{uuid.uuid4().hex}@test.com",
        display_name,
    )
    aid = uuid.uuid4()
    await conn.execute(
        "INSERT INTO accounts (id, user_id, currency) VALUES ($1, $2, 'COP')",
        aid,
        target_user,
    )
    return aid


async def _handle_for(
    conn: asyncpg.Connection, *, requester: uuid.UUID, target_account: uuid.UUID
) -> str:
    from datetime import UTC, datetime, timedelta

    from wallet_ai.infrastructure.db.recipient_repository import RecipientRepository

    record = await RecipientRepository(conn).issue_handle(
        requester_user_id=requester,
        target_account_id=target_account,
        expires_at=datetime.now(UTC) + timedelta(minutes=15),
    )
    return record.handle


# --------------------------------------------------------------------------
# (a/g) list_tools del servidor: exactamente las cuatro del registro
# --------------------------------------------------------------------------
@pytest.mark.integration
@pytest.mark.asyncio
async def test_mcp_server_lists_exactly_the_four_tools(db_conn, monkeypatch):
    monkeypatch.setenv("WALLET_MCP_TOKEN", _token(uuid.uuid4()))
    server = build_mcp_server()

    async with InMemoryTransport(server) as streams:
        read, write = streams
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.list_tools()

    assert {t.name for t in result.tools} == TOOL_NAMES
    assert len(result.tools) == 4


# --------------------------------------------------------------------------
# (g) get_balance por el protocolo, con la identidad del token
# --------------------------------------------------------------------------
@pytest.mark.integration
@pytest.mark.asyncio
async def test_mcp_get_balance_serves_own_account_and_audits(db_conn, monkeypatch):
    user = await _new_user(db_conn)
    account = await _new_account(db_conn, user)
    await _fund(db_conn, account, Decimal("123.00"))

    monkeypatch.setenv("WALLET_MCP_TOKEN", _token(user))
    server = build_mcp_server()

    async with InMemoryTransport(server) as streams:
        read, write = streams
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool("get_balance", {})

    assert result.is_error is False
    data = json.loads(result.content[0].text)
    assert data["balance"] == "123.00"
    assert data["currency"] == "COP"

    # la invocacion quedo auditada con un trace_id de 32 hex del servidor
    rows = await db_conn.fetch(
        "SELECT trace_id FROM audit_events WHERE user_id = $1 AND payload->>'tool' = 'get_balance'",
        user,
    )
    assert rows
    assert all(HEX32.fullmatch(r["trace_id"]) for r in rows)


# --------------------------------------------------------------------------
# (g) sin WALLET_MCP_TOKEN (o invalido) el servidor se niega a arrancar
# --------------------------------------------------------------------------
@pytest.mark.integration
@pytest.mark.asyncio
async def test_server_refuses_to_start_without_token(monkeypatch):
    monkeypatch.delenv("WALLET_MCP_TOKEN", raising=False)
    with pytest.raises(MCPIdentityError):
        build_mcp_server()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_server_refuses_to_start_with_invalid_token(monkeypatch):
    monkeypatch.setenv("WALLET_MCP_TOKEN", "no-es-un-jwt")
    with pytest.raises(MCPIdentityError):
        build_mcp_server()


# --------------------------------------------------------------------------
# (1) por protocolo: argumentos extra con ids ajenos nunca sustituyen al token
# --------------------------------------------------------------------------
@pytest.mark.integration
@pytest.mark.asyncio
async def test_protocol_extra_identity_args_get_balance_still_token_user(db_conn):
    user_a = await _new_user(db_conn)
    account_a = await _new_account(db_conn, user_a)
    await _fund(db_conn, account_a, Decimal("100.00"))
    user_b = await _new_user(db_conn)
    account_b = await _new_account(db_conn, user_b)
    await _fund(db_conn, account_b, Decimal("250.00"))

    server = build_mcp_server(token=_token(user_a))
    async with InMemoryTransport(server) as streams:
        read, write = streams
        async with ClientSession(read, write) as session:
            await session.initialize()
            r = await session.call_tool(
                "get_balance",
                {"user_id": str(user_b), "account_id": str(uuid.uuid4())},
            )

    # argumentos ignorados o error de validacion: el saldo es SIEMPRE el del
    # usuario del token
    if not r.is_error:
        data = json.loads(r.content[0].text)
        assert data["balance"] == "100.00"
        assert data["currency"] == "COP"

    # queda el evento tool_call de la llamada...
    events_a = await db_conn.fetchval(
        "SELECT count(*) FROM audit_events WHERE user_id = $1 AND payload->>'tool' = 'get_balance'",
        user_a,
    )
    assert events_a >= 1
    # ...y nada a nombre del usuario ajeno
    assert await db_conn.fetchval(
        "SELECT count(*) FROM audit_events WHERE user_id = $1", user_b
    ) == 0
    assert await db_conn.fetchval(
        "SELECT count(*) FROM interactions WHERE user_id = $1", user_b
    ) == 0


@pytest.mark.integration
@pytest.mark.asyncio
async def test_protocol_extra_identity_args_find_recipient(db_conn):
    tag = uuid.uuid4().hex[:8]
    user_a = await _new_user(db_conn)
    await _new_account(db_conn, user_a)
    target = await _visible_target(db_conn, f"María{tag} Protocolo")
    user_b = await _new_user(db_conn)
    account_b = await _new_account(db_conn, user_b)
    await _fund(db_conn, account_b, Decimal("5.00"))

    server = build_mcp_server(token=_token(user_a))
    async with InMemoryTransport(server) as streams:
        read, write = streams
        async with ClientSession(read, write) as session:
            await session.initialize()
            r = await session.call_tool(
                "find_recipient",
                {
                    "query": f"María{tag}",
                    "user_id": str(user_b),
                    "account_id": str(uuid.uuid4()),
                },
            )

    handle = None
    if not r.is_error:
        data = json.loads(r.content[0].text)
        assert len(data["results"]) == 1
        handle = data["results"][0]["recipient_id"]

    # el handle (si se emitio) queda ligado al solicitante del token: resuelve
    # para A y NO para el usuario ajeno
    if handle is not None:
        from wallet_ai.application.recipients.resolve_handle import resolve_handle
        from wallet_ai.domain.recipients.errors import HandleNotResolvable
        from wallet_ai.infrastructure.db.recipient_repository import RecipientRepository

        repo = RecipientRepository(db_conn)
        assert await resolve_handle(
            repo, requester_user_id=user_a, handle=handle
        ) == target
        with pytest.raises(HandleNotResolvable):
            await resolve_handle(repo, requester_user_id=user_b, handle=handle)

    events_a = await db_conn.fetchval(
        "SELECT count(*) FROM audit_events WHERE user_id = $1 AND payload->>'tool' = 'find_recipient'",
        user_a,
    )
    assert events_a >= 1
    assert await db_conn.fetchval(
        "SELECT count(*) FROM audit_events WHERE user_id = $1", user_b
    ) == 0
    assert await db_conn.fetchval(
        "SELECT count(*) FROM interactions WHERE user_id = $1", user_b
    ) == 0


@pytest.mark.integration
@pytest.mark.asyncio
async def test_protocol_extra_identity_args_draft_transfer(db_conn):
    user_a = await _new_user(db_conn)
    account_a = await _new_account(db_conn, user_a)
    await _fund(db_conn, account_a, Decimal("10.00"))
    target = await _visible_target(db_conn, "Destino Extra Args")
    handle = await _handle_for(db_conn, requester=user_a, target_account=target)
    user_b = await _new_user(db_conn)
    account_b = await _new_account(db_conn, user_b)
    await _fund(db_conn, account_b, Decimal("5.00"))

    ledger_before = await db_conn.fetchval("SELECT count(*) FROM ledger_transactions")
    server = build_mcp_server(token=_token(user_a))
    async with InMemoryTransport(server) as streams:
        read, write = streams
        async with ClientSession(read, write) as session:
            await session.initialize()
            r = await session.call_tool(
                "draft_transfer",
                {
                    "recipient_id": handle,
                    "amount": "1.00",
                    "user_id": str(user_b),
                    "account_id": str(uuid.uuid4()),
                    "from_account_id": str(uuid.uuid4()),
                },
            )

    if not r.is_error:
        data = json.loads(r.content[0].text)
        assert data["status"] == "pendiente"
        # el borrador es del usuario del token, desde SU cuenta
        row = await db_conn.fetchrow(
            "SELECT user_id, from_account_id FROM transfer_drafts WHERE id = $1",
            uuid.UUID(data["draft_id"]),
        )
        assert row["user_id"] == user_a
        assert row["from_account_id"] == account_a

    # el ledger queda intacto y nada queda a nombre del usuario ajeno
    assert await db_conn.fetchval("SELECT count(*) FROM ledger_transactions") == ledger_before
    assert await db_conn.fetchval(
        "SELECT count(*) FROM transfer_drafts WHERE user_id = $1", user_b
    ) == 0
    events_a = await db_conn.fetchval(
        "SELECT count(*) FROM audit_events WHERE user_id = $1 AND payload->>'tool' = 'draft_transfer'",
        user_a,
    )
    assert events_a >= 1
    assert await db_conn.fetchval(
        "SELECT count(*) FROM audit_events WHERE user_id = $1", user_b
    ) == 0
    assert await db_conn.fetchval(
        "SELECT count(*) FROM interactions WHERE user_id = $1", user_b
    ) == 0


# --------------------------------------------------------------------------
# (2) el JWT se revalida en CADA llamada (exp y rol), con reloj inyectable
# --------------------------------------------------------------------------
@pytest.mark.integration
@pytest.mark.asyncio
async def test_server_revalidates_jwt_on_every_call_with_clock(db_conn):
    user = await _new_user(db_conn)
    account = await _new_account(db_conn, user)
    await _fund(db_conn, account, Decimal("50.00"))
    token = _token(user)  # expira en +3600s

    state = {"shift": 0.0}

    def clock() -> float:
        return time.time() + state["shift"]

    server = build_mcp_server(token=token, clock=clock)
    async with InMemoryTransport(server) as streams:
        read, write = streams
        async with ClientSession(read, write) as session:
            await session.initialize()

            ok = await session.call_tool("get_balance", {})
            assert ok.is_error is False

            # el token vence a mitad de la sesion
            state["shift"] = 7200.0
            denied = await session.call_tool("get_balance", {})

    assert denied.is_error is True
    text = denied.content[0].text.lower()
    # error generico: no filtra detalles del token ni del motivo
    assert "expired" not in text
    assert "exp" not in text
    assert "jwt" not in text
    assert "signature" not in text
    # fallo cerrado: la segunda llamada no ejecuto ni audito nada
    events = await db_conn.fetchval(
        "SELECT count(*) FROM audit_events WHERE user_id = $1 AND payload->>'tool' = 'get_balance'",
        user,
    )
    assert events == 1


@pytest.mark.integration
@pytest.mark.asyncio
async def test_server_refuses_to_start_if_clock_says_token_expired(db_conn):
    fresh_token = _token(uuid.uuid4())
    with pytest.raises(MCPIdentityError):
        build_mcp_server(token=fresh_token, clock=lambda: time.time() + 7200)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_role_rechecked_per_call_with_generic_refusal(db_conn):
    user = await _new_user(db_conn)
    account = await _new_account(db_conn, user)
    await _fund(db_conn, account, Decimal("30.00"))
    server = build_mcp_server(token=_token(user, role="soporte"))

    async with InMemoryTransport(server) as streams:
        read, write = streams
        async with ClientSession(read, write) as session:
            await session.initialize()

            # soporte SI puede leer su propia cuenta
            ok = await session.call_tool("get_balance", {})
            assert ok.is_error is False
            assert json.loads(ok.content[0].text)["balance"] == "30.00"

            # pero no buscar destinatarios: rechazo generico, sin filtrar el rol
            denied = await session.call_tool("find_recipient", {"query": "Maria"})
            assert denied.is_error is True
            assert "soporte" not in denied.content[0].text.lower()

    # el permiso denegado queda auditado
    denied_events = await db_conn.fetchval(
        "SELECT count(*) FROM audit_events WHERE user_id = $1 "
        "AND payload->>'tool' = 'find_recipient' AND payload->>'status' = 'permission_denied'",
        user,
    )
    assert denied_events >= 1


# --------------------------------------------------------------------------
# (3) cada invocacion genera su propio trace_id (32 hex) con su interaction
# --------------------------------------------------------------------------
@pytest.mark.integration
@pytest.mark.asyncio
async def test_each_call_gets_own_trace_id_and_interaction_row(db_conn):
    user = await _new_user(db_conn)
    account = await _new_account(db_conn, user)
    await _fund(db_conn, account, Decimal("40.00"))
    server = build_mcp_server(token=_token(user))

    async with InMemoryTransport(server) as streams:
        read, write = streams
        async with ClientSession(read, write) as session:
            await session.initialize()
            first = await session.call_tool("get_balance", {})
            second = await session.call_tool("get_balance", {})
            assert first.is_error is False and second.is_error is False

    rows = await db_conn.fetch(
        "SELECT trace_id FROM audit_events WHERE user_id = $1 AND payload->>'tool' = 'get_balance'",
        user,
    )
    traces = {r["trace_id"] for r in rows}
    assert len(rows) == 2
    assert len(traces) == 2
    assert all(HEX32.fullmatch(t) for t in traces)
    # la fila de interactions se asegura antes de auditar cada evento
    for trace in traces:
        interactions = await db_conn.fetchval(
            "SELECT count(*) FROM interactions WHERE trace_id = $1", trace
        )
        assert interactions == 1
