"""Puerto de lectura de cuentas y movimientos (sin pasar por el ledger).

La implementacion asyncpg (SELECT-only) vive en
`wallet_ai.infrastructure.db.account_read_repository`. El saldo se deriva de
los asientos del ledger con SQL directo; este puerto NO expone nada del
servicio del ledger ni de su repositorio.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Protocol
from uuid import UUID


@dataclass(frozen=True)
class AccountBalance:
    balance: Decimal
    currency: str


@dataclass(frozen=True)
class Movement:
    id: UUID
    entry_type: str
    amount: Decimal
    created_at: datetime


class AccountReaderPort(Protocol):
    async def primary_account_id(self, user_id: UUID) -> UUID | None: ...

    async def balance(self, *, account_id: UUID) -> AccountBalance: ...

    async def movements(
        self, *, account_id: UUID, limit: int, cursor: str | None
    ) -> tuple[list[Movement], str | None]: ...
