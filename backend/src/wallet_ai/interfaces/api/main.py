from __future__ import annotations

import uuid

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from wallet_ai.infrastructure.config.settings import Settings

app = FastAPI(title="wallet-ai")


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
    try:
        body = await request.json()
        email = body.get("email")
        password = body.get("password")
    except Exception:
        return JSONResponse(status_code=400, content={"detail": "bad request"})
    s = Settings()
    from .deps.auth import create_token

    if email == "cliente@test.com" and password == "password":
        token = create_token(sub=email, role="cliente", secret=s.AUTH_JWT_SECRET)
        return {"access_token": token}
    return JSONResponse(status_code=401, content={"detail": "unauthorized"})
