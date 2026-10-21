import uuid
from contextvars import ContextVar

_ctx: ContextVar[str | None] = ContextVar("trace_id", default=None)


class TraceContext:
    @staticmethod
    def new_trace_id() -> str:
        return uuid.uuid4().hex

    def get_trace_id(self) -> str:
        tid = _ctx.get()
        if tid is None:
            tid = self.new_trace_id()
            self.set_trace_id(tid)
        return tid

    def set_trace_id(self, trace_id: str) -> None:
        _ctx.set(trace_id)
