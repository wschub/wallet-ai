"""Caso de uso: crear un borrador a partir de un handle de destinatario.

Resuelve el handle (mismo error generico para desconocido/vencido/ajeno/UUID) y
delega en `application.transfers.create_draft`. NO importa el ledger ni
`confirm_draft`: crear un borrador no mueve dinero.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from wallet_ai.application.recipients.resolve_handle import resolve_handle
from wallet_ai.application.transfers.create_draft import create_draft
from wallet_ai.domain.ports.audit import ToolCallAuditor
from wallet_ai.domain.ports.drafts import DraftRepositoryPort
from wallet_ai.domain.ports.recipients import RecipientRepositoryPort
from wallet_ai.domain.transfers.draft import Draft


async def create_draft_from_handle(
    drafts: DraftRepositoryPort,
    recipients: RecipientRepositoryPort,
    auditor: ToolCallAuditor,
    *,
    user_id: UUID,
    from_account_id: UUID,
    handle: str,
    amount: Decimal,
    trace_id: str,
    now: datetime | None = None,
) -> Draft:
    if not trace_id:
        raise ValueError("trace_id is required to audit a tool call")

    to_account_id = await resolve_handle(
        recipients, requester_user_id=user_id, handle=handle, now=now
    )
    # `transfer_drafts.trace_id` referencia `interactions(trace_id)`: la fila
    # debe existir antes de insertar el borrador (ON CONFLICT DO NOTHING).
    await auditor.ensure_interaction(trace_id=trace_id, user_id=user_id)
    draft = await create_draft(
        drafts,
        user_id=user_id,
        from_account_id=from_account_id,
        to_account_id=to_account_id,
        amount=amount,
        trace_id=trace_id,
        now=now,
    )
    await auditor.record_tool_call(
        trace_id=trace_id,
        user_id=user_id,
        payload={
            "tool": "draft_transfer",
            "draft_id": str(draft.id),
            "amount": f"{draft.amount:.2f}",
        },
    )
    return draft
