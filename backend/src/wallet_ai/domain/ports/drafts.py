"""Puerto del repositorio de borradores de transferencia.

La implementacion asyncpg vive en
`wallet_ai.infrastructure.db.draft_repository`. El dominio solo conoce esta
interfaz, de modo que `application/transfers/*` no importa infraestructura.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Protocol
from uuid import UUID

from wallet_ai.domain.transfers.draft import Draft


class DraftRepositoryPort(Protocol):
    async def create(
        self,
        *,
        user_id: UUID,
        from_account_id: UUID,
        to_account_id: UUID,
        amount: Decimal,
        expires_at: datetime,
        trace_id: str | None = None,
    ) -> Draft: ...

    async def get(self, draft_id: UUID, *, for_update: bool = False) -> Draft | None: ...

    async def list_for_user(self, user_id: UUID) -> list[Draft]: ...

    async def mark_expired(self, draft_id: UUID) -> None: ...

    async def mark_rejected(self, draft_id: UUID) -> None: ...

    async def mark_confirmed(
        self,
        draft_id: UUID,
        *,
        ledger_transaction_id: UUID,
        confirmed_at: datetime,
    ) -> None: ...
