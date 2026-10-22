from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from uuid import UUID


class LedgerService:
    def __init__(self, repo):
        self._repo = repo

    @staticmethod
    def _request_hash(from_user_id, from_account_id, to_account_id, amount: Decimal) -> str:
        data = {
            "from_user_id": str(from_user_id),
            "from_account_id": str(from_account_id),
            "to_account_id": str(to_account_id),
            "amount": str(amount),
        }
        return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()

    async def transfer(
        self,
        *,
        from_user_id: UUID | str,
        from_account_id: UUID | str,
        to_account_id: UUID | str,
        amount,
        idempotency_key: str,
    ):
        d = self._repo._validate_amount(amount)
        h = self._request_hash(from_user_id, from_account_id, to_account_id, d)
        res = await self._repo.transfer(
            from_user_id=from_user_id,
            from_account_id=from_account_id,
            to_account_id=to_account_id,
            amount=d,
            idempotency_key=idempotency_key,
            request_hash=h,
        )
        return res

    async def get_balance(self, account_id):
        b = await self._repo.get_balance(account_id)
        return str(Decimal(str(b)).quantize(Decimal("0.01")))

    async def list_accounts_with_balance(self, user_id):
        return await self._repo.list_accounts_with_balance(user_id)

    async def list_transactions(self, account_id, limit: int = 20, cursor: str | None = None):
        return await self._repo.list_transactions(account_id, limit=limit, cursor=cursor)
