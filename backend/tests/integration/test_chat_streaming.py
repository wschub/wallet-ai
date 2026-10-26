"""US2 tramo C - chat SSE sobre HTTP real (Parte 2).

1) El primer evento (trace) llega ANTES de que termine el turno: se sirve la
   app con uvicorn en un subproceso, con un LLM de guion retardado entre
   herramientas, y se mide con httpx en modo streaming que el trace llega
   mucho antes que done. Si el middleware almacenara el stream en búfer,
   trace y done llegarían juntos al final.
2) Desconexión real: el cliente corta el socket tras el primer evento y en
   menos de 3 segundos aparece en audit_events un evento response con
   estado cancelled; el servidor no queda colgado.
3) display_name_for_account solo se invoca con cuentas resueltas desde un
   handle válido del usuario autenticado: un handle ajeno o vencido jamás
   llega a esa función (misma señal para el espía y para la base).

Nota: `httpx` se importa DE FORMA PEREZOSA dentro del fixture y los tests:
importarlo a nivel de modulo lo dejaria en sys.modules durante la coleccion,
antes de que corra `test_fake_llm_no_network` (que exige que httpx no este
cargado).
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
import subprocess
import sys
import time
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import asyncpg
import pytest

from wallet_ai.application.assistant.orchestrator import run_turn
from wallet_ai.application.assistant.tools import (
    ToolContext,
    ToolDeps,
    build_default_registry,
)
from wallet_ai.infrastructure.config.settings import Settings
from wallet_ai.infrastructure.db.account_read_repository import AccountReadRepository
from wallet_ai.infrastructure.db.draft_repository import DraftRepository
from wallet_ai.infrastructure.db.recipient_repository import RecipientRepository
from wallet_ai.infrastructure.db.tool_call_audit import ConnectionToolCallAuditor
from wallet_ai.infrastructure.pii.masker import PIIMasker
from wallet_ai.infrastructure.providers.fake.llm import FakeLLM
from wallet_ai.interfaces.api.deps.auth import create_token

BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
LLM_DELAY_SECONDS = 0.4

# Wrapper de la app real para el subproceso: mismo FastAPI, mismo registro,
# solo un retardo configurable en cada llamada al LLM (equivale a un guion
# con pausas entre herramientas).
_APP_WRAPPER = '''
import asyncio
import os

from wallet_ai.infrastructure.providers.fake.llm import FakeLLM
from wallet_ai.interfaces.api.main import app  # noqa: F401

_DELAY = float(os.environ.get("WALLET_TEST_LLM_DELAY_SECONDS", "0"))
_orig_chat = FakeLLM.chat


async def _delayed_chat(self, messages, **kwargs):
    if _DELAY:
        await asyncio.sleep(_DELAY)
    return await _orig_chat(self, messages, **kwargs)


FakeLLM.chat = _delayed_chat
'''


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
async def chat_server(tmp_path):
    import httpx

    wrapper = tmp_path / "chat_stream_app.py"
    wrapper.write_text(_APP_WRAPPER)

    env = dict(os.environ)
    assert env.get("DATABASE_URL"), "DATABASE_URL debe estar en el entorno"
    env["PYTHONPATH"] = str(tmp_path)
    env["WALLET_TEST_LLM_DELAY_SECONDS"] = str(LLM_DELAY_SECONDS)

    port = _free_port()
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "chat_stream_app:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--log-level",
            "warning",
        ],
        cwd=BACKEND_DIR,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )

    base = f"http://127.0.0.1:{port}"
    try:
        async with httpx.AsyncClient(timeout=1.0) as client:
            for _ in range(100):
                if proc.poll() is not None:
                    out = proc.stdout.read().decode() if proc.stdout else ""
                    raise RuntimeError(f"uvicorn murio al arrancar:\n{out}")
                try:
                    await client.get(f"{base}/openapi.json")
                    break
                except httpx.HTTPError:
                    await asyncio.sleep(0.1)
            else:
                raise RuntimeError("uvicorn no respondio en 10s")
        yield base
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)


# --------------------------------------------------------------------------
# helpers de datos (mismo patron que el resto de tests de integracion)
# --------------------------------------------------------------------------


async def _new_user(conn: asyncpg.Connection, role: str = "cliente", display_name=None):
    uid = uuid.uuid4()
    await conn.execute(
        "INSERT INTO users (id, email, role, display_name) VALUES ($1, $2, $3, $4)",
        uid,
        f"stream-{uuid.uuid4().hex}@test.com",
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
    system = await _ensure_system_account(conn)
    async with conn.transaction():
        txn_id = await conn.fetchval(
            "INSERT INTO ledger_transactions "
            "(id, from_user_id, from_account_id, idempotency_key, kind, request_hash) "
            "VALUES (gen_random_uuid(), $1, $2, $3, 'funding', $4) RETURNING id",
            None,
            None,
            f"stream-{uuid.uuid4().hex}",
            "fund",
        )
        for account, kind in ((system, "DEBIT"), (account_id, "CREDIT")):
            await conn.execute(
                "INSERT INTO ledger_entries "
                "(id, transaction_id, account_id, entry_type, amount) "
                "VALUES (gen_random_uuid(), $1, $2, $3, $4)",
                txn_id,
                account,
                kind,
                amount,
            )
        await conn.execute("SET CONSTRAINTS ALL IMMEDIATE")


def _token(user_id: uuid.UUID) -> str:
    return create_token(sub=str(user_id), role="cliente", secret=Settings().AUTH_JWT_SECRET)


def _headers(user_id: uuid.UUID) -> dict:
    return {"Authorization": f"Bearer {_token(user_id)}"}


async def _funded_user(conn: asyncpg.Connection) -> uuid.UUID:
    user = await _new_user(conn)
    account = await _new_account(conn, user)
    await _fund(conn, account, Decimal("200.00"))
    return user


# --------------------------------------------------------------------------
# 1) el primer evento llega antes de que termine el turno (sin buffer)
# --------------------------------------------------------------------------
@pytest.mark.integration
@pytest.mark.asyncio
async def test_1_first_event_streams_before_turn_ends(db_conn, chat_server):
    import httpx

    base = chat_server
    user = await _funded_user(db_conn)

    start = time.perf_counter()
    trace_at = None
    done_at = None
    first_event = None
    async with httpx.AsyncClient(timeout=30) as client:
        async with client.stream(
            "POST",
            f"{base}/chat/stream",
            json={"message": "¿cuál es mi saldo?"},
            headers=_headers(user),
        ) as response:
            assert response.status_code == 200
            assert response.headers["x-trace-id"]
            current = None
            async for line in response.aiter_lines():
                if line.startswith("event:"):
                    current = line.split(":", 1)[1].strip()
                    first_event = first_event or current
                elif line.startswith("data:") and current:
                    if current == "trace" and trace_at is None:
                        trace_at = time.perf_counter()
                    elif current == "done":
                        done_at = time.perf_counter()

    assert first_event == "trace"
    assert trace_at is not None and done_at is not None
    # el turno son >= 2 llamadas al LLM con retardo: done llega al menos un
    # retardo despues del trace
    assert done_at - trace_at >= LLM_DELAY_SECONDS
    # y el trace llego mucho antes (si hubiera buffer, llegaria junto a done)
    assert trace_at - start < (done_at - trace_at) / 2


# --------------------------------------------------------------------------
# 2) desconexión real: response/cancelled en <3s y servidor sano
# --------------------------------------------------------------------------
@pytest.mark.integration
@pytest.mark.asyncio
async def test_2_real_disconnect_audits_cancelled_and_server_stays_healthy(
    db_conn, chat_server
):
    import httpx

    base = chat_server
    user = await _funded_user(db_conn)

    async with httpx.AsyncClient(timeout=30) as client:
        async with client.stream(
            "POST",
            f"{base}/chat/stream",
            json={"message": "¿cuál es mi saldo?"},
            headers=_headers(user),
        ) as response:
            assert response.status_code == 200
            trace_id = None
            async for line in response.aiter_lines():
                if line.startswith("data:"):
                    trace_id = json.loads(line[len("data:") :].strip())["trace_id"]
                    break
            assert trace_id
            # el cliente corta el socket tras el primer evento
            await response.aclose()

    # en menos de 3 segundos aparece el response con estado cancelled
    deadline = time.perf_counter() + 3.0
    found = False
    while time.perf_counter() < deadline:
        row = await db_conn.fetchrow(
            "SELECT payload FROM audit_events "
            "WHERE trace_id = $1 AND event_type = 'response'",
            trace_id,
        )
        if row:
            payload = row["payload"]
            if isinstance(payload, str):
                payload = json.loads(payload)
            if payload.get("status") == "cancelled":
                found = True
                break
        await asyncio.sleep(0.1)
    assert found, "no aparecio response/cancelled en 3s tras la desconexion"

    # y la fila de interactions quedo garantizada antes del evento
    assert await db_conn.fetchval(
        "SELECT count(*) FROM interactions WHERE trace_id = $1", trace_id
    ) == 1

    # el servidor no queda colgado: otro turno completo funciona
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(
            f"{base}/chat/stream",
            json={"message": "¿cuál es mi saldo?"},
            headers=_headers(user),
        )
        assert r.status_code == 200
        assert "event: done" in r.text
        assert "event: error" not in r.text


# --------------------------------------------------------------------------
# 3) display_name_for_account: solo con handles validos del usuario
# --------------------------------------------------------------------------
class _SpyRecipients:
    """Envoltorio espia: delega todo y anota cada llamada a
    display_name_for_account (la unica forma de que un nombre salga)."""

    def __init__(self, inner: RecipientRepository) -> None:
        self._inner = inner
        self.display_calls: list[uuid.UUID] = []

    async def search(self, **kwargs):
        return await self._inner.search(**kwargs)

    async def issue_handle(self, **kwargs):
        return await self._inner.issue_handle(**kwargs)

    async def find(self, **kwargs):
        return await self._inner.find(**kwargs)

    async def display_name_for_account(self, account_id):
        self.display_calls.append(account_id)
        return await self._inner.display_name_for_account(account_id)


async def _run_turn_with(db_conn, ctx, llm, deps):
    events = []
    async for event in run_turn(
        ctx=ctx,
        message="transfiere 1.00 a María",
        registry=build_default_registry(),
        deps=deps,
        llm=llm,
        pii=PIIMasker(),
        auditor=ConnectionToolCallAuditor(db_conn),
    ):
        events.append(event)
    return events


@pytest.mark.integration
@pytest.mark.asyncio
async def test_3_display_name_never_called_with_unresolved_handles(db_conn):
    user = await _new_user(db_conn)
    await _fund(db_conn, await _new_account(db_conn, user), Decimal("10.00"))
    target_user = await _new_user(db_conn, display_name="María Resuelta")
    target_account = await _new_account(db_conn, target_user)
    other_user = await _new_user(db_conn)
    other_account = await _new_account(db_conn, other_user)

    repo = RecipientRepository(db_conn)
    expires = datetime.now(UTC) + timedelta(minutes=15)
    valid = await repo.issue_handle(
        requester_user_id=user, target_account_id=target_account, expires_at=expires
    )
    foreign = await repo.issue_handle(
        requester_user_id=other_user, target_account_id=other_account, expires_at=expires
    )
    expired = await repo.issue_handle(
        requester_user_id=user,
        target_account_id=target_account,
        expires_at=datetime.now(UTC) - timedelta(minutes=1),
    )

    spy = _SpyRecipients(repo)
    deps = ToolDeps(
        accounts=AccountReadRepository(db_conn),
        drafts=DraftRepository(db_conn),
        recipients=spy,
        auditor=ConnectionToolCallAuditor(db_conn),
    )

    for handle in (foreign.handle, expired.handle):
        spy.display_calls.clear()
        script = [
            {
                "type": "tool_call",
                "tool": "draft_transfer",
                "args": {"recipient_id": handle, "amount": "1.00"},
            },
            {"type": "final", "content": "listo"},
        ]
        ctx = ToolContext(user_id=user, role="cliente", trace_id=uuid.uuid4().hex)
        events = await _run_turn_with(db_conn, ctx, FakeLLM(script=script), deps)

        # el borrador falla con el error generico y no se crea nada
        tool_events = [e for e in events if e.type == "tool"]
        assert tool_events[-1].payload() == {"tool": "draft_transfer", "status": "error"}
        assert "draft" not in [e.type for e in events]
        assert await db_conn.fetchval(
            "SELECT count(*) FROM transfer_drafts WHERE user_id = $1", user
        ) == 0
        # y display_name_for_account JAMAS se invoco: el nombre no se toco
        assert spy.display_calls == []

    # control: handle valido -> exactamente una llamada con la cuenta resuelta
    spy.display_calls.clear()
    script = [
        {
            "type": "tool_call",
            "tool": "draft_transfer",
            "args": {"recipient_id": valid.handle, "amount": "1.00"},
        },
        {"type": "final", "content": "listo"},
    ]
    ctx = ToolContext(user_id=user, role="cliente", trace_id=uuid.uuid4().hex)
    events = await _run_turn_with(db_conn, ctx, FakeLLM(script=script), deps)
    tool_events = [e for e in events if e.type == "tool"]
    assert tool_events[-1].payload() == {"tool": "draft_transfer", "status": "ok"}
    assert spy.display_calls == [target_account]
