"""Errores del dominio de cuentas (lectura)."""

from __future__ import annotations


class AccountNotFound(Exception):
    """El usuario no tiene cuenta propia (no sistémica) sobre la que operar."""
