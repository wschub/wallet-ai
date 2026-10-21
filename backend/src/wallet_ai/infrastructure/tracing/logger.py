import json
import logging
from typing import Any

from wallet_ai.infrastructure.pii.masker import PIIMasker
from wallet_ai.infrastructure.tracing.context import TraceContext


class StructuredLogger:
    def __init__(self, pii: PIIMasker | None = None) -> None:
        self.pii = pii or PIIMasker()
        self.ctx = TraceContext()
        import sys

        self._logger = logging.getLogger(f"wallet_ai.{id(self)}")
        self._logger.handlers.clear()
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter("%(message)s"))
        self._logger.addHandler(handler)
        self._logger.setLevel(logging.INFO)
        self._logger.propagate = False

    def _log(self, level: int, msg: str, extra: dict[str, Any] | None = None) -> None:
        payload: dict[str, Any] = {
            "trace_id": self.ctx.get_trace_id(),
            "message": msg,
        }
        if extra:
            masked_extra = {k: self._mask(v) for k, v in extra.items()}
            payload.update(masked_extra)
        self._logger.log(level, json.dumps(payload))

    def _mask(self, v: Any) -> Any:
        if isinstance(v, str):
            return self.pii.mask(v)
        if isinstance(v, dict):
            return {k: self._mask(val) for k, val in v.items()}
        if isinstance(v, list):
            return [self._mask(val) for val in v]
        if isinstance(v, tuple):
            return tuple(self._mask(val) for val in v)
        return v

    def info(self, msg: str, extra: dict[str, Any] | None = None) -> None:
        self._log(logging.INFO, msg, extra)

    def error(self, msg: str, extra: dict[str, Any] | None = None) -> None:
        self._log(logging.ERROR, msg, extra)
