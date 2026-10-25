"""Registro de herramientas del asistente (tramo B2)."""

from wallet_ai.application.assistant.tools.builtin import build_default_registry
from wallet_ai.application.assistant.tools.context import ToolContext
from wallet_ai.application.assistant.tools.errors import (
    ToolError,
    ToolNotFound,
    ToolPermissionDenied,
    ToolValidationError,
)
from wallet_ai.application.assistant.tools.registry import Tool, ToolDeps, ToolRegistry

__all__ = [
    "Tool",
    "ToolContext",
    "ToolDeps",
    "ToolError",
    "ToolNotFound",
    "ToolPermissionDenied",
    "ToolRegistry",
    "ToolValidationError",
    "build_default_registry",
]
