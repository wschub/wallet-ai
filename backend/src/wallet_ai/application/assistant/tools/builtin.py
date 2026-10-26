"""Herramientas del asistente (tramo B2).

Cuatro herramientas, montos siempre como string, sin PII en las salidas:

- `get_balance`: saldo propio derivado de los asientos. Sin argumentos.
- `list_transactions`: movimientos propios, `limit` 1..100 (default 20) y
  `cursor` opcional.
- `find_recipient`: busca por nombre y devuelve handle + nombre enmascarado.
- `draft_transfer`: crea un borrador pendiente desde un handle. NO toca el
  ledger ni confirma nada.

Roles: las dos de lectura para cliente, soporte y compliance (siempre su
propia cuenta); `find_recipient` y `draft_transfer` solo cliente.
"""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from wallet_ai.application.accounts.service import get_balance, list_transactions
from wallet_ai.application.assistant.tools.context import ToolContext
from wallet_ai.application.assistant.tools.registry import Tool, ToolDeps, ToolRegistry
from wallet_ai.application.recipients.create_draft_from_handle import create_draft_from_handle
from wallet_ai.application.recipients.search_recipients import search_recipients
from wallet_ai.domain.accounts.errors import AccountNotFound
from wallet_ai.domain.recipients.masking import mask_display_name

READ_ROLES = ("cliente", "soporte", "compliance")
CLIENT_ROLES = ("cliente",)


# --------------------------------------------------------------------------
# esquemas de entrada (extra='forbid': nada de campos de identidad)
# --------------------------------------------------------------------------


class GetBalanceArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ListTransactionsArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    limit: int = Field(default=20, ge=1, le=100)
    cursor: str | None = None


class FindRecipientArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(min_length=1)


class DraftTransferArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # recipient_id es un HANDLE opaco, nunca un id de cuenta
    recipient_id: str = Field(min_length=1)
    amount: str

    @field_validator("amount")
    @classmethod
    def _positive_decimal_string(cls, value: str) -> str:
        try:
            amount = Decimal(value)
        except Exception as exc:
            raise ValueError("amount must be a decimal string") from exc
        if not amount.is_finite() or amount <= 0:
            raise ValueError("amount must be a positive decimal")
        return value


# --------------------------------------------------------------------------
# funciones
# --------------------------------------------------------------------------


async def _get_balance(ctx: ToolContext, args: GetBalanceArgs, deps: ToolDeps) -> dict:
    return await get_balance(deps.accounts, user_id=ctx.user_id)


async def _list_transactions(
    ctx: ToolContext, args: ListTransactionsArgs, deps: ToolDeps
) -> dict:
    return await list_transactions(
        deps.accounts,
        user_id=ctx.user_id,
        limit=args.limit,
        cursor=args.cursor,
    )


async def _find_recipient(ctx: ToolContext, args: FindRecipientArgs, deps: ToolDeps) -> dict:
    results = await search_recipients(
        deps.recipients,
        deps.auditor,
        requester_user_id=ctx.user_id,
        query=args.query,
        trace_id=ctx.trace_id,
    )
    return {
        "results": [
            {
                "recipient_id": r.recipient_id,
                "name_masked": r.name_masked,
                "expires_at": r.expires_at.isoformat(),
            }
            for r in results
        ]
    }


async def _draft_transfer(ctx: ToolContext, args: DraftTransferArgs, deps: ToolDeps) -> dict:
    from_account_id = await deps.accounts.primary_account_id(ctx.user_id)
    if from_account_id is None:
        raise AccountNotFound("no account available for user")
    draft = await create_draft_from_handle(
        deps.drafts,
        deps.recipients,
        deps.auditor,
        user_id=ctx.user_id,
        from_account_id=from_account_id,
        handle=args.recipient_id,
        amount=Decimal(args.amount),
        trace_id=ctx.trace_id,
    )
    # nombre enmascarado del destinatario: el orquestador construye el
    # evento `draft` SOLO a partir de este resultado
    display_name = await deps.recipients.display_name_for_account(draft.to_account_id)
    # sin ids de cuenta/usuario en la salida; el monto como string
    return {
        "draft_id": str(draft.id),
        "status": draft.status,
        "amount": f"{draft.amount:.2f}",
        "recipient_name_masked": mask_display_name(display_name),
        "expires_at": draft.expires_at.isoformat(),
    }


# --------------------------------------------------------------------------
# registro por defecto: exactamente estas cuatro
# --------------------------------------------------------------------------

TOOLS: tuple[Tool, ...] = (
    Tool(
        name="get_balance",
        description=(
            "Saldo derivado de los asientos del ledger de la cuenta propia "
            "del usuario autenticado. Sin argumentos."
        ),
        args_model=GetBalanceArgs,
        roles=READ_ROLES,
        fn=_get_balance,
    ),
    Tool(
        name="list_transactions",
        description=(
            "Lista los movimientos propios con paginacion estable por "
            "(created_at, id): limit 1..100 (default 20) y cursor opcional."
        ),
        args_model=ListTransactionsArgs,
        roles=READ_ROLES,
        fn=_list_transactions,
    ),
    Tool(
        name="find_recipient",
        description=(
            "Busca destinatarios por nombre y emite handles opacos ligados "
            "al solicitante (vencen en 15 min). Devuelve nombre enmascarado, "
            "sin correos ni ids de usuario/cuenta."
        ),
        args_model=FindRecipientArgs,
        roles=CLIENT_ROLES,
        fn=_find_recipient,
    ),
    Tool(
        name="draft_transfer",
        description=(
            "Crea un borrador de transferencia pendiente a partir de un "
            "handle y un monto (string decimal). NO mueve dinero: la "
            "confirmacion es un endpoint separado fuera de las herramientas."
        ),
        args_model=DraftTransferArgs,
        roles=CLIENT_ROLES,
        fn=_draft_transfer,
    ),
)


def build_default_registry() -> ToolRegistry:
    registry = ToolRegistry()
    for tool in TOOLS:
        registry.register(tool)
    return registry
