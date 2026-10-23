from __future__ import annotations


class TransfersError(Exception):
    pass


class DraftNotFound(TransfersError):
    """El borrador no existe o no pertenece al usuario (404)."""


class DraftNotConfirmable(TransfersError):
    """El borrador esta en un estado terminal distinto de confirmada (409)."""


class DraftExpired(TransfersError):
    """El borrador vencio; se marca 'expirada' al intentar confirmar."""


class InvalidDraft(TransfersError):
    """Datos invalidos al crear un borrador (mismo origen/destino, etc.)."""
