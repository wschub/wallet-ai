"""Servicio de solo lectura de cuentas del asistente (tramo B2).

Expone el saldo propio (derivado de los asientos) y el listado de movimientos
con paginacion estable por (created_at, id). No conoce el ledger: solo el
puerto `AccountReaderPort`. La identidad la fija quien invoca (user_id), asi
que un cliente de este servicio solo puede leer SU cuenta.
"""

from __future__ import annotations

from uuid import UUID

from wallet_ai.domain.accounts.errors import AccountNotFound
from wallet_ai.domain.ports.account_reader import AccountReaderPort

DEFAULT_LIMIT = 20
MIN_LIMIT = 1
MAX_LIMIT = 100


async def get_balance(accounts: AccountReaderPort, *, user_id: UUID) -> dict:
    account_id = await accounts.primary_account_id(user_id)
    if account_id is None:
        raise AccountNotFound("no account available for user")
    overview = await accounts.balance(account_id=account_id)
    return {"balance": f"{overview.balance:.2f}", "currency": overview.currency}


async def list_transactions(
    accounts: AccountReaderPort,
    *,
    user_id: UUID,
    limit: int = DEFAULT_LIMIT,
    cursor: str | None = None,
) -> dict:
    account_id = await accounts.primary_account_id(user_id)
    if account_id is None:
        raise AccountNotFound("no account available for user")
    bounded = max(MIN_LIMIT, min(int(limit), MAX_LIMIT))
    movements, next_cursor = await accounts.movements(
        account_id=account_id, limit=bounded, cursor=cursor
    )
    return {
        "items": [
            {
                "id": str(m.id),
                "amount": f"{m.amount:.2f}",
                "type": m.entry_type,
                "created_at": m.created_at.isoformat(),
            }
            for m in movements
        ],
        "next_cursor": next_cursor,
    }
