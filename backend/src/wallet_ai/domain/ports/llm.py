"""Puerto del proveedor LLM.

`generate` es el fluente sin herramientas (RAG/evals): recibe mensajes y
documentos y devuelve una respuesta de diccionario del proveedor.

`chat` es el que usa el orquestador del asistente (tramo C): modela llamadas
a herramientas de forma tipada. Contrato:

- La respuesta es SIEMPRE o llamadas a herramientas o el texto final,
  nunca ambas cosas a la vez (`tool_calls` vacio => `content` es final).
- Los resultados de herramientas llegan como datos en un sobre JSON con
  rol 'tool' (ver `application/assistant/orchestrator.py`), nunca
  concatenados a instrucciones.
- `provider_name`/`model_name` permiten auditar la llamada sin texto.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class ToolCallRequest:
    """Peticion del LLM de invocar una herramienta del registro."""

    name: str
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class LLMResponse:
    """Respuesta tipada del LLM para el bucle de herramientas.

    `tool_calls` no vacio => el modelo pide herramientas y `content` se
    ignora. `tool_calls` vacio => `content` es la respuesta final del turno.
    """

    content: str = ""
    tool_calls: tuple[ToolCallRequest, ...] = ()
    citations: tuple[str, ...] = ()


class LLMProvider(Protocol):
    provider_name: str
    model_name: str

    async def generate(self, messages: list[dict], **kwargs) -> dict[str, Any]: ...

    async def chat(
        self,
        messages: list[dict],
        *,
        tools: list[dict] | None = None,
        **kwargs: Any,
    ) -> LLMResponse: ...
