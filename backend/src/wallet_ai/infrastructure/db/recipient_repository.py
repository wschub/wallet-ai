"""Repositorio asyncpg de destinatarios y handles.

Implementa `wallet_ai.domain.ports.recipients.RecipientRepositoryPort` sobre una
conexion ya establecida. No abre transacciones por si mismo: el caso de uso (o
el endpoint) decide si emitir handles y escribir auditoria de forma atomica.

La tabla `recipient_handles` es append-only; este repositorio solo inserta y lee.
"""

from __future__ import annotations

import secrets
from datetime import datetime
from uuid import UUID

import asyncpg

from wallet_ai.domain.recipients.candidate import RecipientCandidate
from wallet_ai.domain.recipients.handle import RecipientHandle

_HANDLE_BYTES = 16
_HANDLE_ATTEMPTS = 3


def _row_to_handle(row: asyncpg.Record) -> RecipientHandle:
    return RecipientHandle(
        id=row["id"],
        handle=row["handle"],
        requester_user_id=row["requester_user_id"],
        target_account_id=row["target_account_id"],
        expires_at=row["expires_at"],
        created_at=row["created_at"],
    )


class RecipientRepository:
    def __init__(self, conn: asyncpg.Connection):
        self._conn = conn

    async def search(
        self, *, requester_user_id: UUID, query: str, limit: int = 5
    ) -> list[RecipientCandidate]:
        rows = await self._conn.fetch(
            """
            SELECT s.user_id, s.account_id, s.display_name
            FROM (
                SELECT DISTINCT ON (u.id)
                       u.id AS user_id,
                       a.id AS account_id,
                       u.display_name AS display_name
                FROM users u
                JOIN accounts a ON a.user_id = u.id
                WHERE u.role = 'cliente'
                  AND u.display_name IS NOT NULL
                  AND btrim(u.display_name) <> ''
                  AND u.display_name ILIKE '%' || $1 || '%'
                  AND u.id <> $2
                  AND a.is_system = false
                ORDER BY u.id, a.created_at, a.id
            ) s
            ORDER BY s.display_name, s.user_id
            LIMIT $3
            """,
            query,
            requester_user_id,
            limit,
        )
        return [
            RecipientCandidate(
                user_id=row["user_id"],
                account_id=row["account_id"],
                display_name=row["display_name"],
            )
            for row in rows
        ]

    async def issue_handle(
        self, *, requester_user_id: UUID, target_account_id: UUID, expires_at: datetime
    ) -> RecipientHandle:
        for _ in range(_HANDLE_ATTEMPTS):
            handle = secrets.token_urlsafe(_HANDLE_BYTES)
            try:
                row = await self._conn.fetchrow(
                    "INSERT INTO recipient_handles "
                    "(id, handle, requester_user_id, target_account_id, expires_at) "
                    "VALUES (gen_random_uuid(), $1, $2, $3, $4) "
                    "RETURNING id, handle, requester_user_id, target_account_id, "
                    "expires_at, created_at",
                    handle,
                    requester_user_id,
                    target_account_id,
                    expires_at,
                )
            except asyncpg.exceptions.UniqueViolationError:
                continue
            return _row_to_handle(row)
        raise RuntimeError("could not issue a unique recipient handle")

    async def find(
        self, *, requester_user_id: UUID, handle: str
    ) -> RecipientHandle | None:
        row = await self._conn.fetchrow(
            "SELECT id, handle, requester_user_id, target_account_id, expires_at, created_at "
            "FROM recipient_handles WHERE handle = $1 AND requester_user_id = $2",
            handle,
            requester_user_id,
        )
        return _row_to_handle(row) if row else None

    async def display_name_for_account(self, account_id: UUID) -> str | None:
        """Nombre publico del dueno de una cuenta (para enmascararlo luego).

        Lo usa `draft_transfer` para devolver el nombre enmascarado del
        destinatario sin exponer correos ni ids.
        """
        return await self._conn.fetchval(
            "SELECT u.display_name FROM users u "
            "JOIN accounts a ON a.user_id = u.id WHERE a.id = $1",
            account_id,
        )
