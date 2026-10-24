from __future__ import annotations

import re
import uuid

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from wallet_ai.infrastructure.config.settings import Settings
from wallet_ai.infrastructure.tracing.logger import StructuredLogger
from wallet_ai.interfaces.api.routers import accounts, drafts, recipients

app = FastAPI(title="wallet-ai")
app.include_router(accounts.router)
app.include_router(drafts.router)
app.include_router(recipients.router)

logger = StructuredLogger()

# Patron seguro para un identificador aportado por el cliente.
_CLIENT_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


@app.middleware("http")
async def trace_middleware(request: Request, call_next):
    """El trace_id lo genera SIEMPRE el servidor (uuid4().hex, 32 hex).

    El `X-Trace-Id` de la peticion nunca se usa como identificador: aceptarlo
    permitiria a un cliente falsificar o colisionar las filas de
    `interactions` y `audit_events`. Si el valor entrante cumple el patron
    seguro se registra unicamente en los logs como `client_request_id`; si no
    cumple, se descarta sin error.
    """
    trace_id = uuid.uuid4().hex
    request.state.trace_id = trace_id
    logger.ctx.set_trace_id(trace_id)

    incoming = request.headers.get("X-Trace-Id")
    extra = {"method": request.method, "path": request.url.path}
    if incoming:
        # `fullmatch` y no `match`: el `$` de Python tambien casa justo antes
        # de un salto de linea final, y un valor como "abc\n" no debe pasar
        # por valido.
        if _CLIENT_REQUEST_ID_RE.fullmatch(incoming):
            extra["client_request_id"] = incoming
        # si no cumple el patron se descarta silenciosamente
    logger.info("request received", extra=extra)

    try:
        response = await call_next(request)
        response.headers["X-Trace-Id"] = trace_id
        return response
    except Exception:
        logger.exception(
            "unhandled exception",
            extra={"method": request.method, "path": request.url.path},
        )
        return JSONResponse(
            status_code=500, content={"detail": "internal error"}, headers={"X-Trace-Id": trace_id}
        )


@app.post("/auth/login")
async def login(request: Request):
    try:
        body = await request.json()
        email = body.get("email")
        password = body.get("password")
    except Exception:
        return JSONResponse(status_code=400, content={"detail": "bad request"})
    if email == "system@wallet.ai":
        return JSONResponse(status_code=401, content={"detail": "unauthorized"})
    s = Settings()
    from .deps.auth import create_token

    if email == "cliente@test.com" and password == "password":
        token = create_token(sub=email, role="cliente", secret=s.AUTH_JWT_SECRET)
        return JSONResponse(status_code=200, content={"access_token": token})
    return JSONResponse(status_code=401, content={"detail": "unauthorized"})
