from __future__ import annotations

from typing import Any


class FakeLLM:
    def __init__(self, script: list[dict[str, Any]] | None = None) -> None:
        self.script = script or []
        self._i = 0

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
