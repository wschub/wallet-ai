"""Entidad `RecipientHandle` del dominio.

Un handle es un alias opaco, de un solo solicitante y con vencimiento, hacia una
cuenta destino. La fila de base de datos es append-only: la entidad es inmutable
y su vigencia se decide con `expires_at`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID


@dataclass(frozen=True)
class RecipientHandle:
    handle: str
    requester_user_id: UUID
    target_account_id: UUID
    expires_at: datetime
    id: UUID | None = None
    created_at: datetime | None = None

    def is_expired(self, now: datetime) -> bool:
        return self.expires_at <= now
