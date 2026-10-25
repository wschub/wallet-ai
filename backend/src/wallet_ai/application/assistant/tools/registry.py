"""Registro de herramientas del asistente.

Reglas:
- Nombres con 'confirm' o 'execute' prohibidos: ninguna herramienta del
  asistente puede confirmar ni ejecutar nada (la confirmacion de borradores
  vive solo en `interfaces/api/routers/drafts.py`).
- El ToolContext lo entrega SIEMPRE quien invoca; nunca viaja en argumentos.
- TODA invocacion escribe un evento de auditoria `tool_call`: exito,
  validacion fallida, permiso denegado y error de dominio. El payload pasa
  por el servicio de PII del auditor, nunca contiene valores crudos.
- Los errores de dominio se propagan tal cual (mensaje generico incluido).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ValidationError

from wallet_ai.application.assistant.tools.context import ToolContext
from wallet_ai.application.assistant.tools.errors import (
    ToolNotFound,
    ToolPermissionDenied,
    ToolValidationError,
)
from wallet_ai.domain.ports.account_reader import AccountReaderPort
from wallet_ai.domain.ports.audit import ToolCallAuditor
from wallet_ai.domain.ports.drafts import DraftRepositoryPort
from wallet_ai.domain.ports.recipients import RecipientRepositoryPort

FORBIDDEN_NAME_PARTS = ("confirm", "execute")


@dataclass(frozen=True)
class Tool:
    """Definicion de una herramienta del asistente.

    - `args_model`: modelo pydantic del esquema de entrada (extra='forbid').
    - `roles`: roles del ToolContext autorizados a invocarla.
    - `fn(ctx, args, deps)`: coroutine que ejecuta la herramienta; recibe la
      identidad por contexto, nunca por argumentos.
    """

    name: str
    description: str
    args_model: type[BaseModel]
    roles: tuple[str, ...]
    fn: Any  # Callable[[ToolContext, BaseModel, ToolDeps], Awaitable[dict]]


@dataclass
class ToolDeps:
    """Puertos que necesita una invocacion; los cablea quien invoca."""

    accounts: AccountReaderPort
    drafts: DraftRepositoryPort
    recipients: RecipientRepositoryPort
    auditor: ToolCallAuditor
    extras: dict[str, Any] = field(default_factory=dict)


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        lowered = tool.name.lower()
        if any(part in lowered for part in FORBIDDEN_NAME_PARTS):
            raise ValueError(
                f"tool name {tool.name!r} is forbidden: no confirm/execute tools"
            )
        if tool.name in self._tools:
            raise ValueError(f"tool already registered: {tool.name!r}")
        self._tools[tool.name] = tool

    def list_tools(self) -> list[Tool]:
        return list(self._tools.values())

    def get(self, name: str) -> Tool:
        try:
            return self._tools[name]
        except KeyError:
            raise ToolNotFound(f"unknown tool: {name!r}") from None

    async def invoke(
        self,
        name: str,
        ctx: ToolContext,
        arguments: dict[str, Any] | None,
        deps: ToolDeps,
    ) -> dict:
        try:
            tool = self.get(name)
        except ToolNotFound:
            await self._audit(deps, ctx, name, "unknown_tool", arguments)
            raise

        try:
            args = tool.args_model.model_validate(arguments or {})
        except ValidationError as exc:
            await self._audit(deps, ctx, name, "invalid_arguments", arguments)
            raise ToolValidationError(f"invalid arguments for tool {name!r}") from exc

        if ctx.role not in tool.roles:
            await self._audit(deps, ctx, name, "permission_denied", arguments)
            raise ToolPermissionDenied(f"role {ctx.role!r} cannot call {name!r}")

        try:
            result = await tool.fn(ctx, args, deps)
        except Exception as exc:
            await self._audit(
                deps, ctx, name, "domain_error", arguments, error=type(exc).__name__
            )
            raise

        await self._audit(deps, ctx, name, "ok", arguments)
        return result

    async def _audit(
        self,
        deps: ToolDeps,
        ctx: ToolContext,
        name: str,
        status: str,
        arguments: dict[str, Any] | None,
        error: str | None = None,
    ) -> None:
        payload: dict[str, Any] = {
            "tool": name,
            "status": status,
            "arguments": dict(arguments or {}),
        }
        if error:
            payload["error"] = error
        # `record_tool_call` enmascara el payload con el servicio de PII y
        # garantiza la fila de `interactions` para el trace_id.
        await deps.auditor.record_tool_call(
            trace_id=ctx.trace_id, user_id=ctx.user_id, payload=payload
        )
