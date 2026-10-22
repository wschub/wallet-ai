from __future__ import annotations

import json
from typing import Any

import asyncpg

from wallet_ai.domain.ports.pii_service import PIIService
from wallet_ai.infrastructure.pii.masker import PIIMasker


class AuditRepository:
    def __init__(self, pool: asyncpg.Pool, pii: PIIService | None = None) -> None:
        self.pool = pool
        self.pii = pii or PIIMasker()

    def _mask_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        masked = json.loads(self.pii.mask(json.dumps(payload)))
        return masked

    async def append(self, event: dict[str, Any]) -> None:
        masked_payload = self._mask_payload(event.get("payload", {}))
        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO audit_events (id, trace_id, user_id, event_type, payload, reason, created_at)
                VALUES ($1, $2, $3, $4, $5, $6, COALESCE($7, now()))
                """,
                event["id"],
                event.get("trace_id"),
                event.get("user_id"),
                event["event_type"],
                json.dumps(masked_payload),
                event.get("reason"),
                event.get("created_at"),
            )

    async def find_by_trace(self, trace_id: str) -> list[dict[str, Any]]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                """SELECT id, trace_id, user_id, event_type, payload, reason, created_at
                   FROM audit_events WHERE trace_id = $1 ORDER BY created_at""",
                trace_id,
            )
        return [dict(r) for r in rows]
