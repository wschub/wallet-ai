"""US2 tramo B1 - destinatarios con handles y creacion de borradores por REST.

Tests de integracion contra Postgres real (ver `backend/README.md`).

Reglas:
- No asumen una base vacia: cada test crea sus propios usuarios/cuentas.
- Usan un token aleatorio en los nombres para no chocar con el seed ni con
  otros tests, de modo que "Maria" solo pueda coincidir con lo que crea el test.
- Los rechazos se comprueban por clase de error concreta y por mensaje exacto
  (el error de handle es generico a proposito).
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import asyncpg
import pytest
from fastapi.testclient import TestClient

from wallet_ai.infrastructure.config.settings import Settings
from wallet_ai.interfaces.api.deps.auth import create_token
from wallet_ai.interfaces.api.main import app

# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


async def _new_user(
    conn: asyncpg.Connection, *, display_name: str | None = None, role: str = "cliente"
) -> uuid.UUID:
    uid = uuid.uuid4()
    await conn.execute(
        "INSERT INTO users (id, email, role, display_name) VALUES ($1, $2, $3, $4)",
        uid,
        f"rec-{uuid.uuid4().hex}@test.com",
        role,
        display_name,
    )
    return uid


async def _new_account(
    conn: asyncpg.Connection, user_id: uuid.UUID, *, is_system: bool = False
) -> uuid.UUID:
    aid = uuid.uuid4()
    await conn.execute(
        "INSERT INTO accounts (id, user_id, currency, is_system) VALUES ($1, $2, 'COP', $3)",
        aid,
        user_id,
        is_system,
    )
    return aid


async def _ensure_system_account(conn: asyncpg.Connection) -> uuid.UUID:
    row = await conn.fetchrow("SELECT id FROM accounts WHERE is_system = true LIMIT 1")
    if row:
        return row["id"]
    suid = await _new_user(conn, display_name="Sistema Wallet")
    sid = await _new_account(conn, suid, is_system=True)
    return sid


def _cliente_token(user_id: uuid.UUID) -> str:
    return create_token(sub=str(user_id), role="cliente", secret=Settings().AUTH_JWT_SECRET)


def _client() -> TestClient:
    Settings()
    return TestClient(app)


async def _ledger_count(conn: asyncpg.Connection) -> int:
    return await conn.fetchval("SELECT count(*) FROM ledger_transactions")


def _recipient_repo(conn: asyncpg.Connection):
    from wallet_ai.infrastructure.db.recipient_repository import RecipientRepository

    return RecipientRepository(conn)


def _auditor(conn: asyncpg.Connection):
    from wallet_ai.infrastructure.db.tool_call_audit import ConnectionToolCallAuditor

    return ConnectionToolCallAuditor(conn)


async def _search(
    conn: asyncpg.Connection,
    *,
    requester: uuid.UUID,
    query: str,
    trace_id: str,
):
    from wallet_ai.application.recipients.search_recipients import search_recipients

    return await search_recipients(
        _recipient_repo(conn),
        _auditor(conn),
        requester_user_id=requester,
        query=query,
        trace_id=trace_id,
    )


async def _resolve(conn: asyncpg.Connection, *, requester: uuid.UUID, handle: str):
    from wallet_ai.application.recipients.resolve_handle import resolve_handle

    return await resolve_handle(
        _recipient_repo(conn), requester_user_id=requester, handle=handle
    )


async def _create_from_handle(
    conn: asyncpg.Connection,
    *,
    user_id: uuid.UUID,
    from_account: uuid.UUID,
    handle: str,
    amount: Decimal,
    trace_id: str,
):
    from wallet_ai.application.recipients.create_draft_from_handle import (
        create_draft_from_handle,
    )
    from wallet_ai.infrastructure.db.draft_repository import DraftRepository

    return await create_draft_from_handle(
        DraftRepository(conn),
        _recipient_repo(conn),
        _auditor(conn),
        user_id=user_id,
        from_account_id=from_account,
        handle=handle,
        amount=amount,
        trace_id=trace_id,
    )


async def _issue(
    conn: asyncpg.Connection,
    *,
    requester: uuid.UUID,
    target: uuid.UUID,
    expires_at: datetime | None = None,
) -> str:
    record = await _recipient_repo(conn).issue_handle(
        requester_user_id=requester,
        target_account_id=target,
        expires_at=expires_at or (datetime.now(UTC) + timedelta(minutes=15)),
    )
    return record.handle


# --------------------------------------------------------------------------
# T032a - busqueda: minimo 3 caracteres
# --------------------------------------------------------------------------
@pytest.mark.integration
@pytest.mark.asyncio
async def test_search_rejects_query_shorter_than_three_characters(db_conn):
    from wallet_ai.domain.recipients.errors import InvalidSearchQuery

    requester = await _new_user(db_conn, display_name="Ana Solicitante")
    await _new_account(db_conn, requester)

    with pytest.raises(InvalidSearchQuery):
        await _search(db_conn, requester=requester, query="Ma", trace_id=uuid.uuid4().hex)


# --------------------------------------------------------------------------
# T032b - dos Marias -> dos resultados enmascarados, handles distintos, sin PII
# --------------------------------------------------------------------------
@pytest.mark.integration
@pytest.mark.asyncio
async def test_search_two_maria_results_have_distinct_handles_and_no_pii(db_conn):
    tag = uuid.uuid4().hex[:8]
    requester = await _new_user(db_conn, display_name=f"Buscador {tag}")
    await _new_account(db_conn, requester)

    maria_garcia = await _new_user(db_conn, display_name=f"María{tag} García")
    maria_gomez = await _new_user(db_conn, display_name=f"María{tag} Gómez")
    account_garcia = await _new_account(db_conn, maria_garcia)
    account_gomez = await _new_account(db_conn, maria_gomez)

    trace_id = uuid.uuid4().hex
    results = await _search(
        db_conn, requester=requester, query=f"María{tag}", trace_id=trace_id
    )

    assert len(results) == 2
    names = {r.name_masked for r in results}
    assert names == {f"María{tag} G***"}
    handles = {r.recipient_id for r in results}
    assert len(handles) == 2

    # El resultado no expone ids de usuario ni de cuenta ni correos.
    payload = json.dumps([r.__dict__ for r in results], default=str)
    for secret in (str(maria_garcia), str(maria_gomez), str(account_garcia), str(account_gomez)):
        assert secret not in payload
    assert "@" not in payload
    for r in results:
        assert set(r.__dict__) == {"recipient_id", "name_masked", "expires_at"}
        assert "user_id" not in r.__dict__
        assert "account_id" not in r.__dict__

    # Cada handle resuelve a una cuenta distinta y a una de las dos Marias.
    targets = {await _resolve(db_conn, requester=requester, handle=h) for h in handles}
    assert targets == {account_garcia, account_gomez}


# --------------------------------------------------------------------------
# T032c - nunca el propio solicitante ni cuentas de sistema
# --------------------------------------------------------------------------
@pytest.mark.integration
@pytest.mark.asyncio
async def test_search_excludes_requester_and_system_accounts(db_conn):
    tag = uuid.uuid4().hex[:8]
    requester = await _new_user(db_conn, display_name=f"María{tag} Solicitante")
    requester_account = await _new_account(db_conn, requester)
    system_account = await _ensure_system_account(db_conn)

    sys_user = await _new_user(db_conn, display_name=f"María{tag} Sistema")
    await _new_account(db_conn, sys_user, is_system=True)

    visible = await _new_user(db_conn, display_name=f"María{tag} Visible")
    visible_account = await _new_account(db_conn, visible)

    results = await _search(
        db_conn, requester=requester, query=f"María{tag}", trace_id=uuid.uuid4().hex
    )
    assert len(results) == 1
    assert results[0].name_masked == f"María{tag} V***"

    target = await _resolve(db_conn, requester=requester, handle=results[0].recipient_id)
    assert target == visible_account
    assert target not in {requester_account, system_account}


# --------------------------------------------------------------------------
# T032d - usuarios sin display_name no se buscan ni se muestran
# --------------------------------------------------------------------------
@pytest.mark.integration
@pytest.mark.asyncio
async def test_search_excludes_users_without_display_name(db_conn):
    tag = uuid.uuid4().hex[:8]
    requester = await _new_user(db_conn, display_name=f"Buscador {tag}")
    await _new_account(db_conn, requester)

    visible = await _new_user(db_conn, display_name=f"ConNombre{tag} Visible")
    visible_account = await _new_account(db_conn, visible)
    without_name = await _new_user(db_conn, display_name=None)
    await _new_account(db_conn, without_name)
    with_empty_name = await _new_user(db_conn, display_name="")
    await _new_account(db_conn, with_empty_name)

    results = await _search(
        db_conn, requester=requester, query=f"ConNombre{tag}", trace_id=uuid.uuid4().hex
    )
    assert len(results) == 1
    assert results[0].name_masked == f"ConNombre{tag} V***"
    assert await _resolve(
        db_conn, requester=requester, handle=results[0].recipient_id
    ) == visible_account


# --------------------------------------------------------------------------
# T032d - el middleware genera y propaga el trace_id al caso de uso
# --------------------------------------------------------------------------
@pytest.mark.integration
@pytest.mark.asyncio
async def test_search_without_trace_header_is_audited_with_generated_trace_id(db_conn):
    tag = uuid.uuid4().hex[:8]
    requester = await _new_user(db_conn, display_name=f"Cliente Trace {tag}")
    await _new_account(db_conn, requester)
    target_user = await _new_user(db_conn, display_name=f"María{tag} Trace")
    await _new_account(db_conn, target_user)

    client = _client()
    headers = {"Authorization": f"Bearer {_cliente_token(requester)}"}
    response = client.get("/recipients", params={"q": f"María{tag}"}, headers=headers)

    assert response.status_code == 200, response.text
    trace_id = response.headers["X-Trace-Id"]
    assert trace_id
    assert response.json()["items"][0]["name_masked"] == f"María{tag} T***"

    rows = await db_conn.fetch(
        "SELECT event_type, payload FROM audit_events WHERE trace_id = $1", trace_id
    )
    assert rows
    assert all(row["event_type"] == "tool_call" for row in rows)


# --------------------------------------------------------------------------
# T032d - maximo 5 resultados
# --------------------------------------------------------------------------
@pytest.mark.integration
@pytest.mark.asyncio
async def test_search_returns_at_most_five_results(db_conn):
    tag = uuid.uuid4().hex[:8]
    prefix = f"Limite{tag}"
    requester = await _new_user(db_conn, display_name=f"Buscador {tag}")
    await _new_account(db_conn, requester)
    for i in range(7):
        uid = await _new_user(db_conn, display_name=f"{prefix} Persona{i}")
        await _new_account(db_conn, uid)

    results = await _search(
        db_conn, requester=requester, query=prefix, trace_id=uuid.uuid4().hex
    )
    assert len(results) == 5
    assert len({r.recipient_id for r in results}) == 5


# --------------------------------------------------------------------------
# T032d - una busqueda sin resultados no revela informacion por el status
# --------------------------------------------------------------------------
@pytest.mark.integration
@pytest.mark.asyncio
async def test_search_without_results_returns_empty_list_and_is_audited(db_conn):
    tag = uuid.uuid4().hex[:8]
    requester = await _new_user(db_conn, display_name=f"Enumeracion {tag}")
    await _new_account(db_conn, requester)
    target_user = await _new_user(db_conn, display_name=f"Hallado{tag} Resultado")
    await _new_account(db_conn, target_user)

    no_match = uuid.uuid4().hex[:3]
    while await db_conn.fetchval(
        "SELECT EXISTS (SELECT 1 FROM users WHERE display_name ILIKE '%' || $1 || '%')",
        no_match,
    ):
        no_match = uuid.uuid4().hex[:3]

    client = _client()
    headers = {"Authorization": f"Bearer {_cliente_token(requester)}"}

    empty_client_trace = f"t-{uuid.uuid4().hex}"
    empty_response = client.get(
        "/recipients",
        params={"q": no_match},
        headers={**headers, "X-Trace-Id": empty_client_trace},
    )
    assert empty_response.status_code == 200, empty_response.text
    assert empty_response.json() == {"items": [], "next_cursor": None}

    found_client_trace = f"t-{uuid.uuid4().hex}"
    found_response = client.get(
        "/recipients",
        params={"q": f"Hallado{tag}"},
        headers={**headers, "X-Trace-Id": found_client_trace},
    )
    assert found_response.status_code == 200, found_response.text
    assert len(found_response.json()["items"]) == 1

    # la auditoria queda bajo los trace_id que genero el servidor, nunca bajo
    # los que trajo el cliente
    empty_trace = empty_response.headers["X-Trace-Id"]
    found_trace = found_response.headers["X-Trace-Id"]
    assert empty_trace not in (empty_client_trace, found_client_trace)
    assert found_trace not in (empty_client_trace, found_client_trace)
    assert empty_trace != found_trace

    audited_payloads = []
    for trace_id, expected_results in ((empty_trace, 0), (found_trace, 1)):
        interaction = await db_conn.fetchrow(
            "SELECT trace_id FROM interactions WHERE trace_id = $1", trace_id
        )
        assert interaction is not None
        rows = await db_conn.fetch(
            "SELECT event_type, payload FROM audit_events WHERE trace_id = $1", trace_id
        )
        assert rows
        assert all(row["event_type"] == "tool_call" for row in rows)
        payload = rows[0]["payload"]
        if isinstance(payload, str):
            payload = json.loads(payload)
        assert payload["tool"] == "find_recipient"
        assert payload["results"] == expected_results
        audited_payloads.append(set(payload))

    assert audited_payloads[0] == audited_payloads[1]


# --------------------------------------------------------------------------
# T032e - handle vencido, ajeno, o UUID de cuenta: MISMO error generico
# --------------------------------------------------------------------------
@pytest.mark.integration
@pytest.mark.asyncio
async def test_expired_handle_is_rejected(db_conn):
    from wallet_ai.domain.recipients.errors import HandleNotResolvable

    requester = await _new_user(db_conn, display_name="Solicitante Vencido")
    await _new_account(db_conn, requester)
    target_user = await _new_user(db_conn, display_name="Destino Vencido")
    target = await _new_account(db_conn, target_user)
    handle = await _issue(
        db_conn,
        requester=requester,
        target=target,
        expires_at=datetime.now(UTC) - timedelta(minutes=1),
    )

    with pytest.raises(HandleNotResolvable):
        await _resolve(db_conn, requester=requester, handle=handle)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_handle_issued_to_another_user_is_rejected(db_conn):
    from wallet_ai.domain.recipients.errors import HandleNotResolvable

    requester = await _new_user(db_conn, display_name="Solicitante Ajeno")
    await _new_account(db_conn, requester)
    other = await _new_user(db_conn, display_name="Otro Solicitante")
    await _new_account(db_conn, other)
    target_user = await _new_user(db_conn, display_name="Destino Ajeno")
    target = await _new_account(db_conn, target_user)

    handle = await _issue(db_conn, requester=other, target=target)

    with pytest.raises(HandleNotResolvable):
        await _resolve(db_conn, requester=requester, handle=handle)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_account_uuid_used_as_handle_is_rejected(db_conn):
    from wallet_ai.domain.recipients.errors import HandleNotResolvable

    requester = await _new_user(db_conn, display_name="Solicitante UUID")
    await _new_account(db_conn, requester)
    target_user = await _new_user(db_conn, display_name="Destino UUID")
    target = await _new_account(db_conn, target_user)

    with pytest.raises(HandleNotResolvable):
        await _resolve(db_conn, requester=requester, handle=str(target))


@pytest.mark.integration
@pytest.mark.asyncio
async def test_all_invalid_handles_share_the_same_generic_message(db_conn):
    from wallet_ai.domain.recipients.errors import HandleNotResolvable

    requester = await _new_user(db_conn, display_name="Solicitante Mensajes")
    await _new_account(db_conn, requester)
    other = await _new_user(db_conn, display_name="Otro Mensajes")
    await _new_account(db_conn, other)
    target_user = await _new_user(db_conn, display_name="Destino Mensajes")
    target = await _new_account(db_conn, target_user)

    expired = await _issue(
        db_conn,
        requester=requester,
        target=target,
        expires_at=datetime.now(UTC) - timedelta(minutes=1),
    )
    foreign = await _issue(db_conn, requester=other, target=target)

    messages = []
    for handle in ("handle-que-no-existe", expired, foreign, str(target)):
        with pytest.raises(HandleNotResolvable) as exc:
            await _resolve(db_conn, requester=requester, handle=handle)
        messages.append(str(exc.value))

    assert len(set(messages)) == 1
    assert messages[0] == HandleNotResolvable.MESSAGE
    assert messages[0] == "recipient handle not valid"


# --------------------------------------------------------------------------
# T032e - HTTP: los cuatro handles invalidos tienen la MISMA respuesta
# --------------------------------------------------------------------------
@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "invalid_handle_kind",
    ["unknown", "expired", "issued_to_another_user", "real_account_uuid"],
)
async def test_post_drafts_invalid_handles_have_uniform_http_response(
    db_conn, invalid_handle_kind
):
    from wallet_ai.domain.recipients.errors import HandleNotResolvable

    tag = uuid.uuid4().hex[:8]
    requester = await _new_user(db_conn, display_name=f"Cliente HTTP {tag}")
    await _new_account(db_conn, requester)
    target_user = await _new_user(db_conn, display_name=f"Destino HTTP {tag}")
    target = await _new_account(db_conn, target_user)
    other = await _new_user(db_conn, display_name=f"Otro HTTP {tag}")
    await _new_account(db_conn, other)

    if invalid_handle_kind == "unknown":
        handle = f"handle-que-no-existe-{tag}"
    elif invalid_handle_kind == "expired":
        handle = await _issue(
            db_conn,
            requester=requester,
            target=target,
            expires_at=datetime.now(UTC) - timedelta(minutes=1),
        )
    elif invalid_handle_kind == "issued_to_another_user":
        handle = await _issue(db_conn, requester=other, target=target)
    else:
        handle = str(target)

    client = _client()
    response = client.post(
        "/drafts",
        json={"recipient_id": handle, "amount": "1.00"},
        headers={"Authorization": f"Bearer {_cliente_token(requester)}"},
    )

    assert response.status_code == 404, response.text
    assert response.json() == {"detail": HandleNotResolvable.MESSAGE}
    assert response.status_code != 400
    assert response.json().get("detail") != "invalid draft"


# --------------------------------------------------------------------------
# T032f - crear borrador desde handle: pendiente, sin tocar el ledger
# --------------------------------------------------------------------------
@pytest.mark.integration
@pytest.mark.asyncio
async def test_create_draft_from_handle_creates_pending_draft_without_ledger(db_conn):
    tag = uuid.uuid4().hex[:8]
    requester = await _new_user(db_conn, display_name=f"Solicitante {tag}")
    source = await _new_account(db_conn, requester)
    target_user = await _new_user(db_conn, display_name=f"Destino {tag}")
    target = await _new_account(db_conn, target_user)
    handle = await _issue(db_conn, requester=requester, target=target)

    before = await _ledger_count(db_conn)
    draft = await _create_from_handle(
        db_conn,
        user_id=requester,
        from_account=source,
        handle=handle,
        amount=Decimal("3.00"),
        trace_id=uuid.uuid4().hex,
    )
    after = await _ledger_count(db_conn)

    assert after == before
    assert draft.status == "pendiente"
    assert draft.amount == Decimal("3.00")
    assert draft.from_account_id == source
    assert draft.to_account_id == target

    row = await db_conn.fetchrow(
        "SELECT status FROM transfer_drafts WHERE id = $1", draft.id
    )
    assert row["status"] == "pendiente"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_create_draft_from_handle_rejects_account_uuid(db_conn):
    from wallet_ai.domain.recipients.errors import HandleNotResolvable

    requester = await _new_user(db_conn, display_name="Solicitante UUID Draft")
    source = await _new_account(db_conn, requester)
    target_user = await _new_user(db_conn, display_name="Destino UUID Draft")
    target = await _new_account(db_conn, target_user)

    with pytest.raises(HandleNotResolvable):
        await _create_from_handle(
            db_conn,
            user_id=requester,
            from_account=source,
            handle=str(target),
            amount=Decimal("1.00"),
            trace_id=uuid.uuid4().hex,
        )


# --------------------------------------------------------------------------
# T040 - GET /recipients + POST /drafts (solo cliente, X-Trace-Id, montos string)
# --------------------------------------------------------------------------
@pytest.mark.integration
@pytest.mark.asyncio
async def test_post_drafts_creates_pending_draft_and_does_not_touch_ledger(db_conn):
    tag = uuid.uuid4().hex[:8]
    requester = await _new_user(db_conn, display_name=f"Cliente {tag}")
    source = await _new_account(db_conn, requester)
    target_user = await _new_user(db_conn, display_name=f"María{tag} Destino")
    target = await _new_account(db_conn, target_user)
    handle = await _issue(db_conn, requester=requester, target=target)

    client = _client()
    headers = {
        "Authorization": f"Bearer {_cliente_token(requester)}",
        "X-Trace-Id": f"t-{uuid.uuid4().hex}",
    }

    before = await _ledger_count(db_conn)
    r = client.post(
        "/drafts", json={"recipient_id": handle, "amount": "3.00"}, headers=headers
    )
    after = await _ledger_count(db_conn)

    assert r.status_code == 201, r.text
    assert "X-Trace-Id" in r.headers
    # el trace_id de la respuesta es el generado por el servidor (32 hex),
    # no el que trajo el cliente
    assert r.headers["X-Trace-Id"] != headers["X-Trace-Id"]
    assert re.fullmatch(r"[0-9a-f]{32}", r.headers["X-Trace-Id"])
    body = r.json()
    assert body["status"] == "pendiente"
    assert body["amount"] == "3.00"
    assert isinstance(body["amount"], str)
    assert "to_account_id" not in body
    assert "from_account_id" not in body
    assert str(target) not in json.dumps(body)
    assert after == before

    row = await db_conn.fetchrow(
        "SELECT status, amount, from_account_id, to_account_id FROM transfer_drafts WHERE id = $1",
        uuid.UUID(body["id"]),
    )
    assert row["status"] == "pendiente"
    assert str(row["amount"]) == "3.00"
    assert row["from_account_id"] == source
    assert row["to_account_id"] == target


@pytest.mark.integration
@pytest.mark.asyncio
async def test_recipients_and_drafts_endpoints_require_cliente_and_auth(db_conn):
    tag = uuid.uuid4().hex[:8]
    requester = await _new_user(db_conn, display_name=f"Cliente Roles {tag}")
    await _new_account(db_conn, requester)
    target_user = await _new_user(db_conn, display_name=f"María{tag} Roles")
    target = await _new_account(db_conn, target_user)
    handle = await _issue(db_conn, requester=requester, target=target)

    client = _client()
    compliance = create_token(
        sub=str(requester), role="compliance", secret=Settings().AUTH_JWT_SECRET
    )
    comp_headers = {"Authorization": f"Bearer {compliance}"}
    cliente_headers = {"Authorization": f"Bearer {_cliente_token(requester)}"}

    # sin token -> 401 (con X-Trace-Id en todas las respuestas)
    r = client.get("/recipients", params={"q": f"María{tag}"})
    assert r.status_code == 401
    assert "X-Trace-Id" in r.headers
    r = client.post("/drafts", json={"recipient_id": handle, "amount": "1.00"})
    assert r.status_code == 401
    assert "X-Trace-Id" in r.headers

    # rol compliance -> 403
    r = client.get("/recipients", params={"q": f"María{tag}"}, headers=comp_headers)
    assert r.status_code == 403
    assert "X-Trace-Id" in r.headers
    r = client.post(
        "/drafts", json={"recipient_id": handle, "amount": "1.00"}, headers=comp_headers
    )
    assert r.status_code == 403
    assert "X-Trace-Id" in r.headers

    # query de 2 caracteres -> 400
    r = client.get("/recipients", params={"q": "Ma"}, headers=cliente_headers)
    assert r.status_code == 400
    assert "X-Trace-Id" in r.headers

    # cliente -> 200 / 201
    r = client.get("/recipients", params={"q": f"María{tag}"}, headers=cliente_headers)
    assert r.status_code == 200, r.text
    assert "X-Trace-Id" in r.headers
    items = r.json()["items"]
    assert len(items) == 1
    assert items[0]["name_masked"] == f"María{tag} R***"
    # Cada busqueda emite un handle NUEVO (el anterior sigue siendo valido).
    search_handle = items[0]["recipient_id"]
    assert search_handle != handle
    assert await _resolve(db_conn, requester=requester, handle=search_handle) == target

    r = client.post(
        "/drafts",
        json={"recipient_id": search_handle, "amount": "1.00"},
        headers=cliente_headers,
    )
    assert r.status_code == 201, r.text
    assert "X-Trace-Id" in r.headers


# --------------------------------------------------------------------------
# T032g - auditoria: tool_call con payload enmascarado y sin cuentas/correos
# --------------------------------------------------------------------------
@pytest.mark.integration
@pytest.mark.asyncio
async def test_search_and_draft_creation_write_masked_audit_events(db_conn):
    tag = uuid.uuid4().hex[:8]
    requester = await _new_user(db_conn, display_name=f"Cliente Audit {tag}")
    source = await _new_account(db_conn, requester)
    target_user = await _new_user(db_conn, display_name=f"María{tag} Auditada")
    target = await _new_account(db_conn, target_user)
    handle = await _issue(db_conn, requester=requester, target=target)

    search_client_trace = f"t-{uuid.uuid4().hex}"
    draft_client_trace = f"t-{uuid.uuid4().hex}"
    client = _client()
    headers = {"Authorization": f"Bearer {_cliente_token(requester)}"}
    search_headers = {**headers, "X-Trace-Id": search_client_trace}
    draft_headers = {**headers, "X-Trace-Id": draft_client_trace}

    r_search = client.get(
        "/recipients", params={"q": f"María{tag}"}, headers=search_headers
    )
    assert r_search.status_code == 200, r_search.text
    r_draft = client.post(
        "/drafts",
        json={"recipient_id": handle, "amount": "2.50"},
        headers=draft_headers,
    )
    assert r_draft.status_code == 201, r_draft.text

    # los eventos quedan bajo los trace_id que genero el servidor
    search_trace = r_search.headers["X-Trace-Id"]
    draft_trace = r_draft.headers["X-Trace-Id"]
    assert search_trace not in (search_client_trace, draft_client_trace)
    assert draft_trace not in (search_client_trace, draft_client_trace)

    for trace_id in (search_trace, draft_trace):
        interaction = await db_conn.fetchrow(
            "SELECT trace_id FROM interactions WHERE trace_id = $1", trace_id
        )
        assert interaction is not None
        rows = await db_conn.fetch(
            "SELECT event_type, payload FROM audit_events WHERE trace_id = $1", trace_id
        )
        assert rows, f"sin eventos de auditoria para {trace_id}"
        assert all(row["event_type"] == "tool_call" for row in rows)

        for row in rows:
            payload = row["payload"]
            if isinstance(payload, str):
                payload = json.loads(payload)
            serialized = json.dumps(payload, default=str)
            assert "@" not in serialized
            assert str(source) not in serialized
            assert str(target) not in serialized
            assert str(requester) not in serialized
            assert str(target_user) not in serialized


# --------------------------------------------------------------------------
# T032g - auditoria: busquedas por email/tarjeta quedan enmascaradas
# --------------------------------------------------------------------------
@pytest.mark.integration
@pytest.mark.asyncio
async def test_recipient_search_audit_masks_email_and_card_queries(db_conn, capsys):
    tag = uuid.uuid4().hex[:8]
    requester = await _new_user(db_conn, display_name=f"Cliente PII {tag}")
    await _new_account(db_conn, requester)

    email = f"destinatario.{tag}@example.com"
    card = "4111111111111111"
    client = _client()
    headers = {"Authorization": f"Bearer {_cliente_token(requester)}"}

    for query, placeholder in ((email, "<EMAIL>"), (card, "<CARD>")):
        client_trace = f"t-{uuid.uuid4().hex}"
        response = client.get(
            "/recipients",
            params={"q": query},
            headers={**headers, "X-Trace-Id": client_trace},
        )
        assert response.status_code == 200, response.text
        assert response.json()["items"] == []

        # el evento queda bajo el trace_id generado por el servidor
        trace_id = response.headers["X-Trace-Id"]
        assert trace_id != client_trace

        row = await db_conn.fetchrow(
            "SELECT payload FROM audit_events WHERE trace_id = $1", trace_id
        )
        assert row is not None
        payload = row["payload"]
        if isinstance(payload, str):
            payload = json.loads(payload)
        serialized = json.dumps(payload, default=str)
        assert query not in serialized
        assert placeholder in serialized

        captured, _ = capsys.readouterr()
        assert query not in captured


# --------------------------------------------------------------------------
# T036 - recipient_handles: append-only y privilegios de wallet_app
# --------------------------------------------------------------------------
@pytest.mark.integration
@pytest.mark.asyncio
async def test_recipient_handles_are_append_only_for_owner(db_conn):
    tag = uuid.uuid4().hex[:8]
    requester = await _new_user(db_conn, display_name=f"Append Owner {tag}")
    await _new_account(db_conn, requester)
    target_user = await _new_user(db_conn, display_name=f"Append Target {tag}")
    target = await _new_account(db_conn, target_user)
    handle = await _issue(db_conn, requester=requester, target=target)

    with pytest.raises(asyncpg.exceptions.RaiseError, match="append-only"):
        await db_conn.execute(
            "UPDATE recipient_handles SET expires_at = now() WHERE handle = $1", handle
        )
    with pytest.raises(asyncpg.exceptions.RaiseError, match="append-only"):
        await db_conn.execute("DELETE FROM recipient_handles WHERE handle = $1", handle)

    transaction = db_conn.transaction()
    await transaction.start()
    try:
        with pytest.raises(asyncpg.exceptions.RaiseError, match="append-only"):
            await db_conn.execute("TRUNCATE recipient_handles")
    finally:
        await transaction.rollback()

    row = await db_conn.fetchrow(
        "SELECT handle FROM recipient_handles WHERE handle = $1", handle
    )
    assert row is not None


@pytest.mark.integration
@pytest.mark.asyncio
async def test_wallet_app_can_select_and_insert_handles_but_not_update_or_delete(db_conn):
    tag = uuid.uuid4().hex[:8]
    requester = await _new_user(db_conn, display_name=f"Priv Requester {tag}")
    await _new_account(db_conn, requester)
    target_user = await _new_user(db_conn, display_name=f"Priv Target {tag}")
    target = await _new_account(db_conn, target_user)

    privileges = await db_conn.fetchrow(
        "SELECT has_table_privilege('wallet_app','recipient_handles','SELECT') AS can_select, "
        "has_table_privilege('wallet_app','recipient_handles','INSERT') AS can_insert, "
        "has_table_privilege('wallet_app','recipient_handles','UPDATE') AS can_update, "
        "has_table_privilege('wallet_app','recipient_handles','DELETE') AS can_delete"
    )
    assert privileges["can_select"] is True
    assert privileges["can_insert"] is True
    assert privileges["can_update"] is False
    assert privileges["can_delete"] is False

    await db_conn.execute("SET ROLE wallet_app")
    try:
        existing = await db_conn.fetch("SELECT handle FROM recipient_handles LIMIT 1")
        assert isinstance(existing, list)

        new_handle = uuid.uuid4().hex
        await db_conn.execute(
            "INSERT INTO recipient_handles "
            "(id, handle, requester_user_id, target_account_id, expires_at) "
            "VALUES (gen_random_uuid(), $1, $2, $3, now() + interval '15 min')",
            new_handle,
            requester,
            target,
        )

        with pytest.raises(asyncpg.exceptions.InsufficientPrivilegeError):
            await db_conn.execute(
                "UPDATE recipient_handles SET expires_at = now() WHERE handle = $1",
                new_handle,
            )
        with pytest.raises(asyncpg.exceptions.InsufficientPrivilegeError):
            await db_conn.execute(
                "DELETE FROM recipient_handles WHERE handle = $1", new_handle
            )
    finally:
        await db_conn.execute("RESET ROLE")
