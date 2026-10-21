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
