"""Puerto de auditoria de llamadas a herramientas.

Los casos de uso de destinatarios no conocen `audit_events` ni el pool: reciben
un sink que escribe el evento `tool_call` ya enmascarado.
"""

from __future__ import annotations

from typing import Any, Protocol
from uuid import UUID


class ToolCallAuditor(Protocol):
    async def ensure_interaction(self, *, trace_id: str, user_id: UUID) -> None: ...

    async def record_tool_call(
        self, *, trace_id: str, user_id: UUID, payload: dict[str, Any]
    ) -> None: ...
