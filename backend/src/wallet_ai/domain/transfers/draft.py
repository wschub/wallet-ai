"""Entidad de dominio `Draft` (borrador de transferencia) y su maquina de estados.

El borrador nunca mueve dinero por si mismo: la confirmacion se delega al
servicio del ledger dentro de la misma transaccion de base de datos (ver
`wallet_ai.application.transfers.confirm_draft`).

Estados:
    pendiente --confirmar--> confirmada
    pendiente --rechazar---> rechazada
    pendiente --vencer------> expirada

`confirmada`, `rechazada` y `expirada` son terminales.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from uuid import UUID

PENDIENTE = "pendiente"
CONFIRMADA = "confirmada"
RECHAZADA = "rechazada"
EXPIRADA = "expirada"

STATUSES = (PENDIENTE, CONFIRMADA, RECHAZADA, EXPIRADA)
TERMINAL_STATUSES = (CONFIRMADA, RECHAZADA, EXPIRADA)


@dataclass
class Draft:
    id: UUID
    user_id: UUID
    from_account_id: UUID
    to_account_id: UUID
    amount: Decimal
    status: str
    expires_at: datetime
    confirmed_at: datetime | None = None
    ledger_transaction_id: UUID | None = None
    trace_id: str | None = None
    created_at: datetime | None = None

    def is_terminal(self) -> bool:
        return self.status in TERMINAL_STATUSES

    def is_expired(self, now: datetime) -> bool:
        return self.expires_at < now

    def can_confirm(self, now: datetime) -> bool:
        return self.status == PENDIENTE and not self.is_expired(now)

    def can_reject(self) -> bool:
        return self.status == PENDIENTE
