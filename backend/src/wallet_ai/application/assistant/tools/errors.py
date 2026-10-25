"""Errores del registro de herramientas del asistente."""

from __future__ import annotations


class ToolError(Exception):
    """Error base de la invocacion de una herramienta."""


class ToolNotFound(ToolError):
    """La herramienta pedida no esta registrada."""


class ToolValidationError(ToolError):
    """Los argumentos no cumplen el esquema de entrada (extra='forbid')."""


class ToolPermissionDenied(ToolError):
    """El rol del ToolContext no puede invocar esta herramienta."""
