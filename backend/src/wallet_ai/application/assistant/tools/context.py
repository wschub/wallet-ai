"""Identidad con la que se invoca una herramienta.

`ToolContext` lo construye SIEMPRE quien invoca (el servidor MCP a partir del
JWT, un test, o el orquestador futuro): las herramientas jamas reciben
user_id, rol ni trace_id por argumentos, y los esquemas de entrada usan
`extra='forbid'` para que un intento de inyectarlos falle por validacion.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True)
class ToolContext:
    user_id: UUID
    role: str
    trace_id: str
