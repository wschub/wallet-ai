"""Servidor MCP del asistente (tramo B2): adaptador del registro de tools.

API del SDK usada (mcp==2.3.0, verificada en el codigo instalado en .venv):
- `from mcp.server.mcpserver import MCPServer`: en mcp 2.x `FastMCP` fue
  renombrado a `MCPServer` (importar `mcp.server.fastmcp` lanza un error de
  migracion).
- Registro: `MCPServer.add_tool(fn, name=..., description=...)` con el
  esquema de entrada derivado de la firma del wrapper; el SDK ignora los
  argumentos extra (el modelo derivado no usa extra='forbid'), asi que los
  ids de identidad ajenos nunca llegan al registro, que ademas validaria con
  extra='forbid'.
- Errores: `mcp.server.mcpserver.exceptions.ToolError` se reporta al cliente
  como resultado con is_error=True; este adaptador mapea todos los fallos a
  mensajes genericos (unauthorized / invalid arguments / tool error).
- Transporte: `MCPServer.run("stdio")` basta (ver `main`).
- Para pruebas en proceso, el propio SDK trae
  `mcp.client._memory.InMemoryTransport` + `mcp.client.session.ClientSession`.

Identidad y vigencia:
- Al arrancar lee el JWT de `WALLET_MCP_TOKEN`, lo valida con el mismo
  codigo de auth de la API (`decode_token`) y se niega a arrancar si falta o
  es invalido (`MCPIdentityError`).
- La revocacion de acceso del MCP equivale al vencimiento del JWT: se usan
  tokens de vida corta y CADA llamada vuelve a decodificar el token (exp y
  rol) con `decode_token`. Si vencio o el rol ya no permite la herramienta,
  la llamada falla cerrado con un error generico y no ejecuta nada.
- `clock` es inyectable para que los tests simulen el vencimiento a mitad
  de sesion; por defecto es `time.time`.
- Cada invocacion construye su propio `ToolContext` con un trace_id nuevo
  (uuid4().hex, 32 hex); el auditor asegura la fila de `interactions` antes
  de escribir el evento `tool_call`.

Este modulo no importa nada de `application.transfers.confirm_draft`,
`application.ledger` ni `infrastructure.db.ledger_repository` (lo impone el
contrato import-linter "Confirmation isolation"): el servidor expone las
cuatro herramientas de solo lectura/borrador y ninguna confirma ni ejecuta.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from collections.abc import Callable
from typing import Any

import asyncpg
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from wallet_ai.application.assistant.tools import (
    ToolContext,
    ToolDeps,
    ToolNotFound,
    ToolPermissionDenied,
    ToolRegistry,
    ToolValidationError,
)
from wallet_ai.application.assistant.tools.builtin import build_default_registry
from wallet_ai.infrastructure.config.settings import Settings
from wallet_ai.infrastructure.db.account_read_repository import AccountReadRepository
from wallet_ai.infrastructure.db.draft_repository import DraftRepository
from wallet_ai.infrastructure.db.recipient_repository import RecipientRepository
from wallet_ai.infrastructure.db.tool_call_audit import ConnectionToolCallAuditor
from wallet_ai.interfaces.api.deps.auth import decode_token

registry: ToolRegistry = build_default_registry()


class MCPIdentityError(RuntimeError):
    """Falta el token del servidor MCP o es invalido: no se puede arrancar."""


def list_tools() -> list[Any]:
    """Herramientas expuestas (definiciones del registro)."""
    return registry.list_tools()


def build_context_from_token(
    token: str | None, clock: Callable[[], float] | None = None
) -> ToolContext:
    """Valida el JWT (exp y rol) y construye un ToolContext con trace nuevo.

    Se usa tanto al arrancar (se niega a arrancar si falla) como en CADA
    llamada: cada invocacion vuelve a decodificar el token y genera su
    propio trace_id.
    """
    if not token:
        raise MCPIdentityError("WALLET_MCP_TOKEN is required to start the MCP server")
    try:
        payload = decode_token(token, Settings().AUTH_JWT_SECRET, clock=clock)
    except Exception as exc:
        raise MCPIdentityError("invalid WALLET_MCP_TOKEN") from exc
    sub = payload.get("sub")
    role = payload.get("role")
    if not sub or not role:
        raise MCPIdentityError("invalid WALLET_MCP_TOKEN: missing sub/role")
    try:
        user_id = uuid.UUID(str(sub))
    except ValueError as exc:
        raise MCPIdentityError("invalid WALLET_MCP_TOKEN: sub must be a user id") from exc
    return ToolContext(user_id=user_id, role=str(role), trace_id=uuid.uuid4().hex)


def build_mcp_server(
    token: str | None = None,
    tools: ToolRegistry | None = None,
    *,
    clock: Callable[[], float] | None = None,
) -> MCPServer:
    """Construye el servidor MCP con la identidad del token.

    `token` explicito o, si no viene, el de la variable de entorno
    `WALLET_MCP_TOKEN`; si falta o es invalido se niega a arrancar. `clock`
    permite inyectar el reloj (expiracion simulada en tests).
    """
    if clock is None:
        clock = time.time
    raw_token = token if token is not None else os.environ.get("WALLET_MCP_TOKEN")
    # validacion de arranque: sin token valido no hay servidor
    build_context_from_token(raw_token, clock)

    reg = tools if tools is not None else registry

    async def _invoke(name: str, arguments: dict[str, Any]) -> dict:
        # CADA llamada vuelve a decodificar el token (exp y rol) y genera su
        # propio trace_id; si vencio, falla cerrado antes de ejecutar nada.
        ctx = build_context_from_token(raw_token, clock)
        conn = await asyncpg.connect(Settings().asyncpg_dsn)
        try:
            deps = ToolDeps(
                accounts=AccountReadRepository(conn),
                drafts=DraftRepository(conn),
                recipients=RecipientRepository(conn),
                auditor=ConnectionToolCallAuditor(conn),
            )
            return await reg.invoke(name, ctx, arguments, deps)
        finally:
            await conn.close()

    async def _call(name: str, arguments: dict[str, Any]) -> str:
        try:
            return json.dumps(await _invoke(name, arguments), ensure_ascii=False, default=str)
        except (MCPIdentityError, ToolPermissionDenied) as exc:
            # fallo cerrado con error generico: vencimiento o rol insuficiente
            raise ToolError("unauthorized") from exc
        except ToolValidationError as exc:
            raise ToolError("invalid arguments") from exc
        except ToolNotFound as exc:
            raise ToolError("unknown tool") from exc
        except Exception as exc:
            raise ToolError("tool error") from exc

    # wrappers con firma explicita: el SDK deriva el esquema de entrada (e
    # ignora los argumentos extra); el registro sigue siendo el punto unico
    # de validacion (roles, extra='forbid')
    async def get_balance() -> str:
        return await _call("get_balance", {})

    async def list_transactions(limit: int = 20, cursor: str | None = None) -> str:
        return await _call("list_transactions", {"limit": limit, "cursor": cursor})

    async def find_recipient(query: str) -> str:
        return await _call("find_recipient", {"query": query})

    async def draft_transfer(recipient_id: str, amount: str) -> str:
        return await _call("draft_transfer", {"recipient_id": recipient_id, "amount": amount})

    wrappers = {
        "get_balance": get_balance,
        "list_transactions": list_transactions,
        "find_recipient": find_recipient,
        "draft_transfer": draft_transfer,
    }

    server = MCPServer(
        name="wallet-ai",
        instructions=(
            "Herramientas del asistente wallet-ai. Los montos siempre llegan "
            "como string decimal y recipient_id es un handle opaco. Ninguna "
            "herramienta confirma ni ejecuta transferencias."
        ),
    )
    for tool in reg.list_tools():
        if tool.name not in wrappers:
            raise ValueError(f"tool {tool.name!r} has no MCP wrapper")
        server.add_tool(wrappers[tool.name], name=tool.name, description=tool.description)
    return server


def main() -> None:
    server = build_mcp_server()
    server.run("stdio")


if __name__ == "__main__":
    main()
