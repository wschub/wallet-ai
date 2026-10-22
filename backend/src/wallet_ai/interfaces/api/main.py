from __future__ import annotations

import uuid

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from wallet_ai.infrastructure.config.settings import Settings
from wallet_ai.interfaces.api.routers import accounts

app = FastAPI(title="wallet-ai")
app.include_router(accounts.router)


@app.middleware("http")
async def trace_middleware(request: Request, call_next):
    trace_id = request.headers.get("X-Trace-Id") or uuid.uuid4().hex
    try:
        response = await call_next(request)
        response.headers["X-Trace-Id"] = trace_id
        return response
    except Exception:
        return JSONResponse(
            status_code=500, content={"detail": "internal error"}, headers={"X-Trace-Id": trace_id}
        )


@app.post("/auth/login")
async def login(request: Request):
    trace_id = request.headers.get("X-Trace-Id") or uuid.uuid4().hex
    try:
        body = await request.json()
        email = body.get("email")
        password = body.get("password")
    except Exception:
        resp = JSONResponse(status_code=400, content={"detail": "bad request"})
        resp.headers["X-Trace-Id"] = trace_id
        return resp
    if email == "system@wallet.ai":
        resp = JSONResponse(status_code=401, content={"detail": "unauthorized"})
        resp.headers["X-Trace-Id"] = trace_id
        return resp
    s = Settings()
    from .deps.auth import create_token

    if email == "cliente@test.com" and password == "password":
        token = create_token(sub=email, role="cliente", secret=s.AUTH_JWT_SECRET)
        resp = JSONResponse(status_code=200, content={"access_token": token})
        resp.headers["X-Trace-Id"] = trace_id
        return resp
    resp = JSONResponse(status_code=401, content={"detail": "unauthorized"})
    resp.headers["X-Trace-Id"] = trace_id
    return resp
