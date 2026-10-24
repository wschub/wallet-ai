"""Repositorio asyncpg de cuentas.

Minimo necesario para el tramo B1: obtener la cuenta desde la que un usuario
cliente crea un borrador. No hay todavia un puerto de dominio para cuentas en
este tramo.
"""

from __future__ import annotations

from uuid import UUID

import asyncpg


class AccountRepository:
    def __init__(self, conn: asyncpg.Connection):
        self._conn = conn

    async def primary_for_user(self, user_id: UUID) -> UUID | None:
        return await self._conn.fetchval(
            "SELECT id FROM accounts "
            "WHERE user_id = $1 AND is_system = false "
            "ORDER BY created_at, id LIMIT 1",
            user_id,
        )
