"""Caso de uso: confirmar un borrador de transferencia.

Confirmar es el UNICO punto donde se mueve dinero, y ocurre en UNA sola
transaccion de base de datos.

Este modulo vive en `application` y por tanto NO importa infraestructura: el
repositorio de borradores, el servicio del ledger y el gestor de transaccion
se inyectan como parametros (puertos). El cableado con asyncpg se hace en
`wallet_ai.infrastructure.db.draft_repository.build_confirm_draft`.

Pasos:
1. `SELECT ... FOR UPDATE` del borrador (lo hace el repositorio con
   `for_update=True`), serializando confirmaciones concurrentes.
2. Comprobar propiedad (si no -> `DraftNotFound`).
3. Si ya esta `confirmada`, devolver el MISMO `transaction_id` (idempotente).
4. Si esta en estado terminal distinto -> `DraftNotConfirmable` (409).
5. Si vencio -> marcar `expirada` (commit) y fallar con `DraftExpired`.
6. Ejecutar la transferencia con el servicio del ledger usando como clave de
   idempotencia el id del borrador (`in_transaction=True`: no abre otra
   transaccion).
7. Actualizar el borrador a `confirmada` con `ledger_transaction_id` y
   `confirmed_at`.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from wallet_ai.domain.ports.drafts import DraftRepositoryPort
from wallet_ai.domain.transfers.draft import CONFIRMADA, PENDIENTE
from wallet_ai.domain.transfers.errors import DraftExpired, DraftNotConfirmable, DraftNotFound


async def confirm_draft(
    *,
    drafts: DraftRepositoryPort,
    ledger: Any,
    transaction: Callable[[], AbstractAsyncContextManager[Any]],
    draft_id: UUID,
    user_id: UUID,
    now: datetime | None = None,
) -> dict:
    """Confirma `draft_id` y devuelve `{"draft_id", "transaction_id"}`.

    `transaction` debe abrir una transaccion de base de datos en la conexion
    sobre la que operan `drafts` y `ledger`.
    """
    expired = False
    result: dict | None = None

    async with transaction():
        draft = await drafts.get(draft_id, for_update=True)
        if draft is None or draft.user_id != user_id:
            raise DraftNotFound("draft not found")

        if draft.status == CONFIRMADA and draft.ledger_transaction_id is not None:
            return {
                "draft_id": str(draft.id),
                "transaction_id": str(draft.ledger_transaction_id),
            }

        if draft.status != PENDIENTE:
            raise DraftNotConfirmable(f"draft is {draft.status}")

        moment = now or datetime.now(UTC)
        if draft.is_expired(moment):
            # El cambio a 'expirada' se confirma y se falla despues del commit.
            await drafts.mark_expired(draft_id)
            expired = True
        else:
            transferred = await ledger.transfer(
                from_user_id=user_id,
                from_account_id=draft.from_account_id,
                to_account_id=draft.to_account_id,
                amount=draft.amount,
                idempotency_key=str(draft_id),
                in_transaction=True,
            )
            txn_id = UUID(transferred["transaction_id"])
            await drafts.mark_confirmed(
                draft_id,
                ledger_transaction_id=txn_id,
                confirmed_at=moment,
            )
            result = {"draft_id": str(draft_id), "transaction_id": str(txn_id)}

    if expired:
        raise DraftExpired("draft expired")

    assert result is not None
    return result
