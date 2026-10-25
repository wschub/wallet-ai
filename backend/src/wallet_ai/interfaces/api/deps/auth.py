from __future__ import annotations

import hashlib
import hmac
import json
import time
from collections.abc import Callable
from typing import Any

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from wallet_ai.infrastructure.config.settings import Settings

bearer = HTTPBearer(auto_error=False)


def hash_password(password: str, salt: bytes | None = None) -> str:
    salt = salt or hashlib.sha256(password.encode()).digest()[:16]
    key = hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1)
    return salt.hex() + ":" + key.hex()


def verify_password(password: str, stored: str) -> bool:
    if not stored or stored in ("INVALID-SYSTEM",):
        return False
    try:
        if ":" not in stored:
            return False
        salt_hex, key_hex = stored.split(":", 1)
        if len(salt_hex) < 8 or len(key_hex) < 8:
            return False
        salt = bytes.fromhex(salt_hex)
        key = hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1)
        return hmac.compare_digest(key.hex(), key_hex)
    except Exception:
        return False


def create_token(sub: str, role: str, secret: str, exp_seconds: int = 3600) -> str:
    header = {"alg": "HS256", "typ": "JWT"}
    payload = {"sub": sub, "role": role, "exp": int(time.time()) + exp_seconds}
    h = json.dumps(header).encode()
    p = json.dumps(payload).encode()
    import base64

    def b64url(x: bytes) -> str:
        return base64.urlsafe_b64encode(x).rstrip(b"=").decode()

    msg = b64url(h) + "." + b64url(p)
    sig = hmac.new(secret.encode(), msg.encode(), hashlib.sha256).digest()
    return msg + "." + b64url(sig)


def decode_token(
    token: str, secret: str, *, clock: Callable[[], float] | None = None
) -> dict[str, Any]:
    try:
        import base64

        def b64url(x: str) -> bytes:
            pad = "=" * (-len(x) % 4)
            return base64.urlsafe_b64decode(x + pad)

        parts = token.split(".")
        if len(parts) != 3:
            raise ValueError("bad token")
        payload = json.loads(b64url(parts[1]))
        # `clock` inyectable: el servidor MCP revalida el token en cada
        # llamada y los tests pueden simular el vencimiento a mitad de sesion.
        now = int(clock()) if clock is not None else int(time.time())
        if payload.get("exp", 0) < now:
            raise ValueError("expired")
        msg = parts[0] + "." + parts[1]
        sig = hmac.new(secret.encode(), msg.encode(), hashlib.sha256).digest()
        import base64 as b

        if b.urlsafe_b64encode(sig).rstrip(b"=").decode() != parts[2]:
            raise ValueError("bad sig")
        return payload
    except Exception as e:
        raise HTTPException(status_code=401, detail="invalid token") from e


async def get_current_user(
    creds: HTTPAuthorizationCredentials | None = Depends(bearer),
) -> dict[str, Any]:
    if not creds:
        raise HTTPException(status_code=401, detail="missing token")
    s = Settings()
    payload = decode_token(creds.credentials, s.AUTH_JWT_SECRET)
    # el X-Trace-Id de la peticion no se expone aqui: el trace_id lo genera
    # siempre el middleware y el valor del cliente nunca es identificador
    return {"sub": payload.get("sub"), "role": payload.get("role")}
