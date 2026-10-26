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


class AssistantTurnAuditor(Protocol):
    """Auditoria de los eventos de un turno del asistente (tramo C).

    `record_event` escribe eventos genericos del orquestador (`llm_call`,
    `guardrail_block`, `response`) con el `trace_id` de siempre y
    garantizando ANTES la fila de `interactions` (igual que
    `record_tool_call`, pero con `event_type` libre).
    """

    async def ensure_interaction(self, *, trace_id: str, user_id: UUID) -> None: ...

    async def record_event(
        self,
        *,
        trace_id: str,
        user_id: UUID,
        event_type: str,
        payload: dict[str, Any],
    ) -> None: ...
