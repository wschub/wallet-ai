from __future__ import annotations

import json
from decimal import Decimal
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Query, Request

from wallet_ai.infrastructure.config.settings import Settings
from wallet_ai.interfaces.api.deps.auth import get_current_user

router = APIRouter(prefix="/accounts", tags=["accounts"])


async def _get_conn():
    dsn = Settings().asyncpg_dsn
    conn = await asyncpg.connect(dsn)
    try:
        yield conn
    finally:
        await conn.close()


@router.get("/me")
async def get_me_accounts(request: Request, user=Depends(get_current_user), conn=Depends(_get_conn)):
    user_id = user.get("sub") or user.get("id")
    try:
        uid = UUID(str(user_id))
    except Exception:
        raise HTTPException(status_code=400, detail="bad user")
    rows = await conn.fetch(
        "SELECT a.id, a.currency, "
        "COALESCE(SUM(CASE WHEN e.entry_type='CREDIT' THEN e.amount ELSE 0 END),0) "
        "- COALESCE(SUM(CASE WHEN e.entry_type='DEBIT' THEN e.amount ELSE 0 END),0) AS balance "
        "FROM accounts a LEFT JOIN ledger_entries e ON e.account_id = a.id "
        "WHERE a.user_id = $1 GROUP BY a.id, a.currency ORDER BY a.id",
        uid,
    )
    # return first account or list? openapi says AccountBalance for /accounts/me
    if not rows:
        raise HTTPException(status_code=404, detail="no accounts")
    r = rows[0]
    bal = Decimal(str(r["balance"])).quantize(Decimal("0.01"))
    return {"account_id": str(r["id"]), "balance": f"{bal:.2f}", "currency": r["currency"]}


@router.get("/me/transactions")
async def get_me_transactions(
    request: Request,
    user=Depends(get_current_user),
    conn=Depends(_get_conn),
    limit: int = Query(20, ge=1, le=100),
    cursor: str | None = None,
):
    user_id = user.get("sub") or user.get("id")
    try:
        uid = UUID(str(user_id))
    except Exception:
        raise HTTPException(status_code=400, detail="bad user")
    acc = await conn.fetchrow("SELECT id FROM accounts WHERE user_id = $1 ORDER BY id LIMIT 1", uid)
    if not acc:
        return {"items": [], "next_cursor": None}
    account_id = acc["id"]
    limit = max(1, min(limit, 100))
    cur_ts = None
    cur_id = None
    if cursor:
        try:
            c = json.loads(cursor)
            from datetime import datetime

            cur_ts = datetime.fromisoformat(c["ts"].replace("Z", "+00:00"))
            cur_id = UUID(c["id"])
        except Exception:
            pass
    if cur_ts and cur_id:
        rows = await conn.fetch(
            "SELECT e.id, e.entry_type, e.amount, e.created_at "
            "FROM ledger_entries e WHERE e.account_id = $1 AND (e.created_at, e.id) < ($2, $3) "
            "ORDER BY e.created_at DESC, e.id DESC LIMIT $4",
            account_id,
            cur_ts,
            cur_id,
            limit + 1,
        )
    else:
        rows = await conn.fetch(
            "SELECT e.id, e.entry_type, e.amount, e.created_at "
            "FROM ledger_entries e WHERE e.account_id = $1 "
            "ORDER BY e.created_at DESC, e.id DESC LIMIT $2",
            account_id,
            limit + 1,
        )
    has_more = len(rows) > limit
    items = rows[:limit]
    next_cursor = None
    if has_more and items:
        last = items[-1]
        next_cursor = json.dumps({"ts": last["created_at"].isoformat(), "id": str(last["id"])})
    result = []
    for it in items:
        bal_it = Decimal(str(it["amount"])).quantize(Decimal("0.01"))
        if hasattr(it["created_at"], "isoformat"):
            created_at_str = it["created_at"].isoformat().replace("+00:00", "Z")
        else:
            created_at_str = str(it["created_at"])
        result.append(
            {
                "id": str(it["id"]),
                "amount": f"{bal_it:.2f}",
                "type": it["entry_type"],
                "created_at": created_at_str,
            }
        )
    return {"items": result, "next_cursor": next_cursor}
