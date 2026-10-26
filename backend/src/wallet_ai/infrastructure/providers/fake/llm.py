"""Fake LLM (T009/T039). Sin red, determinista.

Dos modos:

- Heuristico (default, sin guion): reglas fijas en espanol. Decide SOLO a
  partir del ultimo mensaje del usuario y del ultimo sobre JSON de
  herramienta (rol 'tool'); el texto final se construye unicamente a
  partir de los resultados de las herramientas. Cualquier consulta sin
  herramienta asociada recibe una abstencion explicita.
- Guion (`script=...`): consume pasos fijos, para tests adversariales.
  Un paso puede pedir varias llamadas con la clave `calls`, y los
  argumentos pueden referenciar el handle del ultimo `find_recipient`
  con el valor `"$last_recipient"` (o el `selected_recipient_id`).

`generate` conserva la API de diccionarios del tramo de fundacion (lo usan
RAG/evals y sus tests); `chat` es la superficie tipada del orquestador.
"""

from __future__ import annotations

import json
import re
from typing import Any

from wallet_ai.domain.ports.llm import LLMResponse, ToolCallRequest

ABSTENTION_MESSAGE = "No tengo documentación disponible para responder eso todavía."
PERMISSION_DENIED_MESSAGE = "No puedes hacer esa acción con tu rol actual."
CANNOT_DO_MESSAGE = "No pude completar esa acción."
NO_RECIPIENT_MESSAGE = "No encontré un destinatario con ese nombre."
SEVERAL_RECIPIENTS_MESSAGE = "Encontré varios destinatarios con ese nombre."

_SALDO_RE = re.compile(r"(?i)\b(saldo|balance)\b")
_MOVIMIENTOS_RE = re.compile(r"(?i)\b(movimientos?|transacciones?|historial)\b")
_LIMIT_RE = re.compile(
    r"(?i)(?:[úu]ltim[oa]s?\s+(\d{1,3}))|(?:\s(\d{1,3})\s+movimientos?)|"
    r"(?:\s(\d{1,3})\s+transacciones?)"
)
_WORD = r"[A-Za-zÁÉÍÓÚÜÑáéíóúüñ0-9']+"
# 'transfiere/envía/manda <monto> a <nombre>': el monto va justo antes del
# separador (a|al|para), nunca se toma de los digitos internos del nombre
_TRANSFER_RE = re.compile(
    rf"(?i)\b(?:transfi[eé]re|transfi[eé]reme|env[ií]a(?:me|le)?|m[aá]nda(?:me|le)?|manda|haz)\b"
    r".{0,60}?"
    r"(\d+(?:[.,]\d+)?)\s*(?:cop|pesos?|eur(?:os?)?|d[oó]lares?)?\s+"
    rf"(?:a|al|para)\s+({_WORD}(?:\s+{_WORD}){{0,2}})"
)
_AMOUNT_RE = re.compile(r"(\d+(?:[.,]\d+)?)")

_DEFAULT_LIMIT = 20


def _clamp_limit(value: int) -> int:
    return max(1, min(int(value), 100))


def _amount_in(text: str) -> str | None:
    match = _AMOUNT_RE.search(text)
    if not match:
        return None
    return match.group(1).replace(",", ".")


def _parse_envelope(message: dict) -> dict[str, Any]:
    """Sobre JSON de herramienta: {"tool": nombre, "data": resultado}."""
    try:
        parsed = json.loads(message.get("content", ""))
        if isinstance(parsed, dict):
            return parsed
    except (TypeError, ValueError):
        pass
    return {}


class FakeLLM:
    provider_name = "fake"

    def __init__(self, script: list[dict[str, Any]] | None = None) -> None:
        self.script = script or []
        self._i = 0
        # guion presente => modo guion (tests adversariales); sin guion,
        # heuristico determinista
        self.mode = "script" if self.script else "heuristic"
        self.model_name = "fake-script" if self.script else "fake-heuristic"

    # ------------------------------------------------------------------
    # API legacy de diccionarios (sin cambios)
    # ------------------------------------------------------------------
    async def generate(
        self,
        messages: list[dict],
        docs: list[dict] | None = None,
        **kwargs,
    ) -> dict[str, Any]:
        if self._i < len(self.script):
            step = self.script[self._i]
            self._i += 1
            if step.get("type") == "final":
                citations = step.get("citations") or []
                if docs and not citations:
                    citations = [d.get("id") for d in docs if d.get("id")]
                return {
                    "type": "final",
                    "content": step.get("content", ""),
                    "citations": citations,
                }
            return step
        # default
        citations = []
        if docs:
            citations = [d.get("id") for d in docs if d.get("id")]
        return {"type": "final", "content": "", "citations": citations}

    # ------------------------------------------------------------------
    # API tipada del orquestador
    # ------------------------------------------------------------------
    async def chat(
        self,
        messages: list[dict],
        *,
        tools: list[dict] | None = None,
        selected_recipient_id: str | None = None,
        **kwargs,
    ) -> LLMResponse:
        if self.mode == "script":
            return self._scripted_chat(messages, selected_recipient_id)
        return self._heuristic_chat(messages, selected_recipient_id)

    # -- modo guion -----------------------------------------------------

    def _scripted_chat(
        self, messages: list[dict], selected: str | None
    ) -> LLMResponse:
        if self._i < len(self.script):
            step = self.script[self._i]
            self._i += 1
        else:
            return LLMResponse(content="")
        if step.get("type") in ("tool_call", "tool_calls"):
            calls = step.get("calls") or [step]
            requests = tuple(
                ToolCallRequest(
                    name=call["tool"],
                    arguments=self._substitute(call.get("args") or {}, messages, selected),
                )
                for call in calls
            )
            return LLMResponse(tool_calls=requests)
        return LLMResponse(content=step.get("content", ""))

    def _substitute(
        self, args: dict[str, Any], messages: list[dict], selected: str | None
    ) -> dict[str, Any]:
        """Reemplaza "$last_recipient" por el handle elegido/disponible."""
        handle = selected
        if handle is None:
            for message in reversed(messages):
                if message.get("role") != "tool":
                    continue
                envelope = _parse_envelope(message)
                if envelope.get("tool") == "find_recipient":
                    results = (envelope.get("data") or {}).get("results") or []
                    if results:
                        handle = results[0].get("recipient_id")
                    break
        return {
            key: (handle if value == "$last_recipient" else value)
            for key, value in args.items()
        }

    # -- modo heuristico -------------------------------------------------

    def _heuristic_chat(
        self, messages: list[dict], selected: str | None
    ) -> LLMResponse:
        user_messages = [m for m in messages if m.get("role") == "user"]
        last_user = user_messages[-1].get("content", "") if user_messages else ""
        tool_messages = [m for m in messages if m.get("role") == "tool"]

        if not tool_messages:
            decision = self._first_step(last_user)
        else:
            decision = self._after_tool(last_user, tool_messages[-1], selected)

        if decision[0] == "tool":
            return LLMResponse(
                tool_calls=(ToolCallRequest(name=decision[1], arguments=decision[2]),)
            )
        return LLMResponse(content=decision[1])

    def _first_step(self, text: str) -> tuple:
        if _SALDO_RE.search(text):
            return ("tool", "get_balance", {})
        if _MOVIMIENTOS_RE.search(text):
            match = _LIMIT_RE.search(text)
            limit = next((g for g in (match.groups() if match else ()) if g), None)
            return ("tool", "list_transactions", {"limit": _clamp_limit(int(limit) if limit else _DEFAULT_LIMIT)})
        transfer = _TRANSFER_RE.search(text)
        if transfer:
            query = transfer.group(2).strip()
            return ("tool", "find_recipient", {"query": query})
        return ("final", ABSTENTION_MESSAGE)

    def _after_tool(self, last_user: str, tool_message: dict, selected: str | None) -> tuple:
        envelope = _parse_envelope(tool_message)
        tool = envelope.get("tool")
        data = envelope.get("data") or {}

        error = data.get("error")
        if error == "permission_denied":
            return ("final", PERMISSION_DENIED_MESSAGE)
        if error:
            return ("final", CANNOT_DO_MESSAGE)

        if tool == "get_balance":
            return ("final", f"Tu saldo es {data.get('balance', '0.00')} {data.get('currency', 'COP')}.")
        if tool == "list_transactions":
            items = data.get("items") or []
            summary = ", ".join(f"{item.get('amount')} {item.get('type')}" for item in items)
            head = f"Encontré {len(items)} movimientos recientes."
            return ("final", f"{head}: {summary}." if summary else head)
        if tool == "find_recipient":
            results = data.get("results") or []
            if not results:
                return ("final", NO_RECIPIENT_MESSAGE)
            if len(results) == 1 or selected:
                handle = selected if selected else results[0].get("recipient_id")
                amount = _amount_in(last_user)
                if amount is None:
                    return ("final", CANNOT_DO_MESSAGE)
                return ("tool", "draft_transfer", {"recipient_id": handle, "amount": amount})
            return ("final", SEVERAL_RECIPIENTS_MESSAGE)
        if tool == "draft_transfer":
            if data.get("status"):
                return (
                    "final",
                    f"Preparé un borrador de transferencia por {data.get('amount')}. "
                    f"Estado: {data.get('status')}. Queda pendiente de confirmación.",
                )
            return ("final", CANNOT_DO_MESSAGE)
        return ("final", ABSTENTION_MESSAGE)
