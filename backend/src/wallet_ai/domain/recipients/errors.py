"""Errores del dominio de destinatarios con handles."""

from __future__ import annotations


class RecipientError(Exception):
    """Base de los errores de destinatarios."""


class InvalidSearchQuery(RecipientError):
    """La busqueda no cumple el minimo de caracteres."""


class HandleNotResolvable(RecipientError):
    """Handle no utilizable.

    Se usa el MISMO error y el MISMO mensaje para handle desconocido, vencido,
    emitido a otro solicitante y para un UUID de cuenta usado como handle: el
    error no debe revelar cual de los casos se dio.
    """

    MESSAGE = "recipient handle not valid"

    def __init__(self) -> None:
        super().__init__(self.MESSAGE)
