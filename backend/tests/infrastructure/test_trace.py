import asyncio

from wallet_ai.infrastructure.tracing.context import TraceContext
from wallet_ai.infrastructure.tracing.logger import StructuredLogger


def test_trace_propagates_across_tasks():
    ctx = TraceContext()
    tid = ctx.new_trace_id()
    ctx.set_trace_id(tid)

    async def child():
        return ctx.get_trace_id()

    async def run():
        t1 = asyncio.create_task(child())
        t2 = asyncio.create_task(child())
        return await asyncio.gather(t1, t2)

    res = asyncio.run(run())
    assert res[0] == tid and res[1] == tid


def test_trace_isolated():
    ctx = TraceContext()
    tid1 = ctx.new_trace_id()
    tid2 = ctx.new_trace_id()

    async def get_tid():
        return ctx.get_trace_id()

    async def run():
        ctx.set_trace_id(tid1)
        r1 = await get_tid()
        ctx.set_trace_id(tid2)
        r2 = await get_tid()
        return r1, r2

    r1, r2 = asyncio.run(run())
    assert r1 == tid1 and r2 == tid2


def test_structured_logger_masks_pii(capsys):
    from wallet_ai.infrastructure.pii.masker import PIIMasker

    logger = StructuredLogger(PIIMasker())
    logger.info("msg", extra={"email": "test@example.com", "card": "4111111111111111"})
    out, err = capsys.readouterr()
    assert "test@example.com" not in out
    assert "4111111111111111" not in out
    assert "<EMAIL>" in out
    assert "<CARD>" in out
    assert "trace_id" in out


def test_structured_logger_masks_nested_dict_and_list(capsys):
    from wallet_ai.infrastructure.pii.masker import PIIMasker

    logger = StructuredLogger(PIIMasker())
    extra = {
        "user": {"contact": "a.b+c@dominio.com.co"},
        "items": ["tel +573001112233", {"card": "4111111111111111"}],
    }
    logger.info("msg", extra=extra)
    out, err = capsys.readouterr()
    assert "a.b+c@dominio.com.co" not in out
    assert "3001112233" not in out
    assert "4111111111111111" not in out
    assert "<EMAIL>" in out
    assert "<PHONE>" in out
    assert "<CARD>" in out


def test_structured_logger_masks_exc_info_with_card(capsys):
    from wallet_ai.infrastructure.pii.masker import PIIMasker

    logger = StructuredLogger(PIIMasker())
    try:
        raise ValueError("card 4111111111111111 used")
    except ValueError:
        logger.error("failure", extra={"exc": "error with 4111111111111111"})
    out, err = capsys.readouterr()
    assert "4111111111111111" not in out
    assert "<CARD>" in out


def test_trace_middleware_returns_generic_500_and_logs_masked_exception():
    import json
    import logging
    import re

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from wallet_ai.interfaces.api.main import logger, trace_middleware

    class ListHandler(logging.Handler):
        def __init__(self):
            super().__init__()
            self.records: list[str] = []

        def emit(self, record: logging.LogRecord) -> None:
            self.records.append(record.getMessage())

    failure_app = FastAPI()
    failure_app.middleware("http")(trace_middleware)

    @failure_app.get("/boom")
    async def boom():
        raise RuntimeError("contact user@example.com with card 4111111111111111")

    handler = ListHandler()
    logger._logger.addHandler(handler)
    try:
        client = TestClient(failure_app, raise_server_exceptions=False)
        trace_id = "trace-middleware-log-test"
        response = client.get("/boom", headers={"X-Trace-Id": trace_id})
    finally:
        logger._logger.removeHandler(handler)

    assert response.status_code == 500
    assert response.json() == {"detail": "internal error"}
    # el trace_id de la respuesta SIEMPRE lo genera el servidor (32 hex):
    # el que trae el cliente nunca se usa como identificador
    server_trace = response.headers["X-Trace-Id"]
    assert server_trace != trace_id
    assert re.fullmatch(r"[0-9a-f]{32}", server_trace)
    assert "user@example.com" not in response.text
    assert "4111111111111111" not in response.text
    assert "RuntimeError" not in response.text

    # log de request + log de excepcion
    assert len(handler.records) == 2
    request_record = json.loads(handler.records[0])
    exception_record = json.loads(handler.records[1])
    assert request_record["message"] == "request received"
    assert request_record["trace_id"] == server_trace
    # el valor del cliente solo aparece como client_request_id
    assert request_record["client_request_id"] == trace_id
    assert exception_record["trace_id"] == server_trace
    assert exception_record["message"] == "unhandled exception"
    assert "RuntimeError" in exception_record["exception"]
    assert "user@example.com" not in exception_record["exception"]
    assert "4111111111111111" not in exception_record["exception"]
    assert "<EMAIL>" in exception_record["exception"]
    assert "<CARD>" in exception_record["exception"]
