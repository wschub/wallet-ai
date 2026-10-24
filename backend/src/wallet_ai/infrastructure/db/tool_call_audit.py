"""Auditoria de llamadas a herramientas sobre una conexion existente.

Garantiza primero la fila de `interactions` para el `trace_id` (ON CONFLICT DO
NOTHING) y despues inserta el evento `tool_call` con el payload enmascarado por
el servicio de PII. `audit_events` es append-only (lo impone la base).
"""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

import asyncpg

from wallet_ai.domain.ports.pii_service import PIIService
from wallet_ai.infrastructure.pii.masker import PIIMasker


class ConnectionToolCallAuditor:
    def __init__(self, conn: asyncpg.Connection, pii: PIIService | None = None) -> None:
        self._conn = conn
        self._pii = pii or PIIMasker()

    def _mask(self, payload: dict[str, Any]) -> dict[str, Any]:
        raw = json.dumps(payload, default=str, ensure_ascii=False)
        return json.loads(self._pii.mask(raw))

    async def ensure_interaction(self, *, trace_id: str, user_id: UUID) -> None:
        if not trace_id:
            raise ValueError("trace_id is required")
        await self._conn.execute(
            "INSERT INTO interactions (id, trace_id, user_id, channel) "
            "VALUES (gen_random_uuid(), $1, $2, 'tool') "
            "ON CONFLICT (trace_id) DO NOTHING",
            trace_id,
            user_id,
        )

    async def record_tool_call(
        self, *, trace_id: str, user_id: UUID, payload: dict[str, Any]
    ) -> None:
        await self.ensure_interaction(trace_id=trace_id, user_id=user_id)
        await self._conn.execute(
            "INSERT INTO audit_events (id, trace_id, user_id, event_type, payload) "
            "VALUES (gen_random_uuid(), $1, $2, 'tool_call', $3::jsonb)",
            trace_id,
            user_id,
            json.dumps(self._mask(payload), ensure_ascii=False),
        )
