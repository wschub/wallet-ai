"""Tipos de la busqueda de destinatarios.

`RecipientCandidate` es lo que ve el repositorio (incluye ids internos).
`RecipientResult` es lo unico que sale hacia la interfaz: handle + nombre
enmascarado + vencimiento, sin ids de usuario ni de cuenta.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID


@dataclass(frozen=True)
class RecipientCandidate:
    user_id: UUID
    account_id: UUID
    display_name: str


@dataclass(frozen=True)
class RecipientResult:
    recipient_id: str
    name_masked: str
    expires_at: datetime
