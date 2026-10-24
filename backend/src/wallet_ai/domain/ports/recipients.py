"""Puerto del repositorio de destinatarios.

La implementacion asyncpg vive en
`wallet_ai.infrastructure.db.recipient_repository`. El dominio solo conoce esta
interfaz: `application/recipients/*` no importa infraestructura.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol
from uuid import UUID

from wallet_ai.domain.recipients.candidate import RecipientCandidate
from wallet_ai.domain.recipients.handle import RecipientHandle


class RecipientRepositoryPort(Protocol):
    async def search(
        self, *, requester_user_id: UUID, query: str, limit: int = 5
    ) -> list[RecipientCandidate]: ...

    async def issue_handle(
        self, *, requester_user_id: UUID, target_account_id: UUID, expires_at: datetime
    ) -> RecipientHandle: ...

    async def find(
        self, *, requester_user_id: UUID, handle: str
    ) -> RecipientHandle | None: ...
