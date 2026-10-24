"""trace_middleware: el trace_id SIEMPRE lo genera el servidor.

El `X-Trace-Id` entrante nunca se usa como identificador:

- la respuesta siempre lleva un `X-Trace-Id` nuevo (uuid4().hex, 32 hex);
- si el valor entrante cumple `^[A-Za-z0-9._-]{1,64}$` solo se registra en los
  logs como `client_request_id`;
- si no cumple (500 caracteres, saltos de linea), se descarta sin error y no
  llega a `interactions` ni `audit_events`.

Los tests ejercitan POST /drafts porque es la ruta que escribe
`interactions`/`audit_events` con el trace_id: asi se comprueba de verdad que
el valor del cliente no aterriza en la base.
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import UTC, datetime, timedelta

import asyncpg
import pytest
from fastapi.testclient import TestClient

from wallet_ai.infrastructure.config.settings import Settings
from wallet_ai.infrastructure.db.recipient_repository import RecipientRepository
from wallet_ai.interfaces.api.deps.auth import create_token
from wallet_ai.interfaces.api.main import app

_HEX32 = re.compile(r"[0-9a-f]{32}")


def _is_server_trace_id(value: object) -> bool:
    return isinstance(value, str) and _HEX32.fullmatch(value) is not None


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


async def _new_user(conn: asyncpg.Connection) -> uuid.UUID:
    uid = uuid.uuid4()
    await conn.execute(
        "INSERT INTO users (id, email, role, display_name) VALUES ($1, $2, 'cliente', $3)",
        uid,
        f"trace-{uuid.uuid4().hex}@test.com",
        f"Cliente {uid.hex[:8]}",
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


async def _issue_handle(conn: asyncpg.Connection, *, requester: uuid.UUID) -> str:
    target_user = await _new_user(conn)
    target = await _new_account(conn, target_user)
    record = await RecipientRepository(conn).issue_handle(
        requester_user_id=requester,
        target_account_id=target,
        expires_at=datetime.now(UTC) + timedelta(minutes=15),
    )
    return record.handle


def _cliente_token(user_id: uuid.UUID) -> str:
    return create_token(sub=str(user_id), role="cliente", secret=Settings().AUTH_JWT_SECRET)


def _client() -> TestClient:
    Settings()
    return TestClient(app)


async def _draft_payload(conn: asyncpg.Connection) -> dict:
    """Usuario con cuenta + handle resolvable para POST /drafts."""
    requester = await _new_user(conn)
    await _new_account(conn, requester)
    handle = await _issue_handle(conn, requester=requester)
    return {"requester": requester, "handle": handle}


async def _trace_counts(
    conn: asyncpg.Connection, value: str, user_id: uuid.UUID
) -> tuple[int, int]:
    """(interactions, audit_events) de `user_id` con trace_id EXACTAMENTE `value`.

    Se acota por usuario (creado fresco en cada test) porque la base es
    compartida y append-only: corridas anteriores pueden dejar filas con
    valores de cliente fijos como "abc".
    """
    interactions = await conn.fetchval(
        "SELECT count(*) FROM interactions WHERE trace_id = $1 AND user_id = $2",
        value,
        user_id,
    )
    events = await conn.fetchval(
        "SELECT count(*) FROM audit_events WHERE trace_id = $1 AND user_id = $2",
        value,
        user_id,
    )
    return interactions, events


async def _raw_asgi_post(
    path: str, headers: list[tuple[bytes, bytes]], body: bytes
) -> tuple[int, list[tuple[bytes, bytes]], bytes]:
    """POST directo a la app ASGI, sin stack HTTP.

    Permite enviar headers que un cliente real rechazaria (por ejemplo con
    saltos de linea) y comprobar que el middleware no se rompe con ellos.
    """
    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "root_path": "",
        "headers": headers,
        "client": ("testclient", 50000),
        "server": ("testserver", 80),
    }
    status = 0
    response_headers: list[tuple[bytes, bytes]] = []
    body_parts: list[bytes] = []

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(message):
        nonlocal status
        if message["type"] == "http.response.start":
            status = message["status"]
            response_headers.extend(message["headers"])
        elif message["type"] == "http.response.body":
            body_parts.append(message.get("body", b""))

    await app(scope, receive, send)
    return status, response_headers, b"".join(body_parts)


# --------------------------------------------------------------------------
# (1) X-Trace-Id: abc -> la respuesta lleva OTRO trace_id (32 hex)
# --------------------------------------------------------------------------
@pytest.mark.integration
@pytest.mark.asyncio
async def test_incoming_trace_id_is_ignored_and_server_trace_is_returned(db_conn):
    setup = await _draft_payload(db_conn)
    client = _client()
    headers = {
        "Authorization": f"Bearer {_cliente_token(setup['requester'])}",
        "X-Trace-Id": "abc",
    }

    r = client.post(
        "/drafts", json={"recipient_id": setup["handle"], "amount": "1.00"}, headers=headers
    )

    assert r.status_code == 201, r.text
    trace_id = r.headers["X-Trace-Id"]
    assert trace_id != "abc"
    assert _is_server_trace_id(trace_id)

    # el borrador creado queda ligado al trace del SERVIDOR (fila concreta)
    draft_row = await db_conn.fetchrow(
        "SELECT trace_id FROM transfer_drafts WHERE id = $1", uuid.UUID(r.json()["id"])
    )
    assert draft_row["trace_id"] == trace_id

    # y nada de ESTE usuario cuelga del valor del cliente
    assert await _trace_counts(db_conn, "abc", setup["requester"]) == (0, 0)
    assert await _trace_counts(db_conn, trace_id, setup["requester"]) == (1, 1)


# --------------------------------------------------------------------------
# (2a) 500 caracteres: respuesta normal, nada en la base
# --------------------------------------------------------------------------
@pytest.mark.integration
@pytest.mark.asyncio
async def test_oversized_incoming_trace_id_is_discarded_without_error(db_conn):
    setup = await _draft_payload(db_conn)
    client = _client()
    # exactamente 500 caracteres, unico por corrida
    huge = uuid.uuid4().hex + "-" + "y" * (500 - 33)
    assert len(huge) == 500
    headers = {
        "Authorization": f"Bearer {_cliente_token(setup['requester'])}",
        "X-Trace-Id": huge,
    }

    r = client.post(
        "/drafts", json={"recipient_id": setup["handle"], "amount": "1.00"}, headers=headers
    )

    assert r.status_code == 201, r.text
    assert _is_server_trace_id(r.headers["X-Trace-Id"])
    assert r.headers["X-Trace-Id"] != huge
    assert await _trace_counts(db_conn, huge, setup["requester"]) == (0, 0)


# --------------------------------------------------------------------------
# (2b) saltos de linea (raw ASGI): nada se rompe, nada en la base
# --------------------------------------------------------------------------
@pytest.mark.integration
@pytest.mark.asyncio
async def test_newline_incoming_trace_id_is_discarded_without_error(db_conn):
    setup = await _draft_payload(db_conn)
    # saltos de linea + sufijo unico por corrida
    nasty = f"abc\ndef\r\n{uuid.uuid4().hex}"
    token = _cliente_token(setup["requester"])
    body = json.dumps(
        {"recipient_id": setup["handle"], "amount": "1.00"}
    ).encode()
    headers = [
        (b"authorization", f"Bearer {token}".encode()),
        (b"content-type", b"application/json"),
        (b"x-trace-id", nasty.encode()),
    ]

    status, response_headers, _ = await _raw_asgi_post("/drafts", headers, body)

    assert status == 201
    raw = dict(response_headers)
    assert _is_server_trace_id(raw[b"x-trace-id"].decode())
    assert nasty.encode() not in [value for _, value in response_headers]
    assert await _trace_counts(db_conn, nasty, setup["requester"]) == (0, 0)


# --------------------------------------------------------------------------
# (3) dos peticiones con el mismo X-Trace-Id -> dos trace_id distintos
# --------------------------------------------------------------------------
@pytest.mark.integration
@pytest.mark.asyncio
async def test_same_incoming_trace_id_yields_two_distinct_server_traces(db_conn):
    setup = await _draft_payload(db_conn)
    client = _client()
    # el MISMO valor entrante en ambas peticiones (unico entre corridas)
    repeated = f"repetido-{uuid.uuid4().hex}"
    headers = {
        "Authorization": f"Bearer {_cliente_token(setup['requester'])}",
        "X-Trace-Id": repeated,
    }

    r1 = client.post(
        "/drafts", json={"recipient_id": setup["handle"], "amount": "1.00"}, headers=headers
    )
    r2 = client.post(
        "/drafts", json={"recipient_id": setup["handle"], "amount": "2.00"}, headers=headers
    )

    assert r1.status_code == 201 and r2.status_code == 201
    t1 = r1.headers["X-Trace-Id"]
    t2 = r2.headers["X-Trace-Id"]
    assert t1 != t2
    assert _is_server_trace_id(t1) and _is_server_trace_id(t2)
    assert repeated not in (t1, t2)

    # cada peticion dejo su propia interaccion y evento, y ninguna bajo el
    # valor del cliente
    assert await _trace_counts(db_conn, repeated, setup["requester"]) == (0, 0)
    assert await _trace_counts(db_conn, t1, setup["requester"]) == (1, 1)
    assert await _trace_counts(db_conn, t2, setup["requester"]) == (1, 1)


# --------------------------------------------------------------------------
# client_request_id: solo se registra en logs si el valor entrante es valido
# --------------------------------------------------------------------------
@pytest.mark.integration
@pytest.mark.asyncio
async def test_client_request_id_logged_only_when_valid(db_conn):
    import logging

    from wallet_ai.interfaces.api.main import logger

    class ListHandler(logging.Handler):
        def __init__(self):
            super().__init__()
            self.records: list[str] = []

        def emit(self, record: logging.LogRecord) -> None:
            self.records.append(record.getMessage())

    setup = await _draft_payload(db_conn)
    client = _client()
    token = _cliente_token(setup["requester"])
    # ambos validos para el patron, pero el segundo se pasa de 64 caracteres;
    # unicos por corrida para no chocar con corridas anteriores
    valid = f"cliente-valido-{uuid.uuid4().hex}"
    invalid = uuid.uuid4().hex + "-" + "y" * (500 - 33)

    handler = ListHandler()
    logger._logger.addHandler(handler)
    try:
        r_valid = client.post(
            "/drafts",
            json={"recipient_id": setup["handle"], "amount": "1.00"},
            headers={"Authorization": f"Bearer {token}", "X-Trace-Id": valid},
        )
        r_invalid = client.post(
            "/drafts",
            json={"recipient_id": setup["handle"], "amount": "2.00"},
            headers={"Authorization": f"Bearer {token}", "X-Trace-Id": invalid},
        )
    finally:
        logger._logger.removeHandler(handler)

    assert r_valid.status_code == 201 and r_invalid.status_code == 201

    parsed = [json.loads(r) for r in handler.records]
    valid_records = [p for p in parsed if p["trace_id"] == r_valid.headers["X-Trace-Id"]]
    invalid_records = [p for p in parsed if p["trace_id"] == r_invalid.headers["X-Trace-Id"]]

    # el valor valido solo aparece como client_request_id, no como trace_id
    assert valid_records
    assert any(p.get("client_request_id") == valid for p in valid_records)
    assert all(p["trace_id"] != valid for p in valid_records)
    # el invalido no aparece en ningun log
    assert invalid_records
    assert all("client_request_id" not in p for p in invalid_records)
    assert all(invalid not in p.values() for p in invalid_records if isinstance(p, dict))


# --------------------------------------------------------------------------
# (endurecimiento) la base rechaza cualquier trace_id que no sea uuid4().hex
#
# Migracion 20261024_0008: CHECK en `interactions.trace_id`. `audit_events` y
# `transfer_drafts` referencian `interactions(trace_id)` por FK, asi que
# heredan el formato. Si un bug futuro vuelve a guardar el X-Trace-Id del
# cliente como identificador, la insercion falla aqui.
# --------------------------------------------------------------------------
@pytest.mark.integration
@pytest.mark.asyncio
async def test_db_rejects_client_shaped_trace_ids(db_conn):
    user = await _new_user(db_conn)

    async def _insert(trace_id: str) -> None:
        await db_conn.execute(
            "INSERT INTO interactions (id, trace_id, user_id, channel) "
            "VALUES (gen_random_uuid(), $1, $2, 'test')",
            trace_id,
            user,
        )

    # el formato del servidor (uuid4().hex, 32 hex) pasa
    await _insert(uuid.uuid4().hex)

    # nada de lo que un cliente pudo enviar como X-Trace-Id entra: unico por
    # corrida para que el test no dependa del estado de la base
    bad_values = [
        f"abc-{uuid.uuid4().hex[:8]}",  # corto, formato de cliente
        uuid.uuid4().hex + "-" + "y" * (500 - 33),  # 500 caracteres
        f"abc\ndef\r\n{uuid.uuid4().hex}",  # saltos de linea
        f"t-{uuid.uuid4().hex}",  # formato legacy de los tests (34 chars, con '-')
    ]
    for bad in bad_values:
        with pytest.raises(asyncpg.exceptions.CheckViolationError):
            await _insert(bad)
