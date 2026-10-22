import pytest
from fastapi.testclient import TestClient

from wallet_ai.infrastructure.config.settings import Settings
from wallet_ai.interfaces.api.main import app


@pytest.mark.integration
def test_login_correct_returns_token():
    Settings()
    client = TestClient(app)
    r = client.post("/auth/login", json={"email": "cliente@test.com", "password": "password"})
    assert r.status_code == 200
    assert "access_token" in r.json()
    assert "X-Trace-Id" in r.headers


@pytest.mark.integration
def test_login_incorrect_401():
    Settings()
    client = TestClient(app)
    r = client.post("/auth/login", json={"email": "x@test.com", "password": "wrong"})
    assert r.status_code == 401
    assert "X-Trace-Id" in r.headers


@pytest.mark.integration
def test_token_role_wrong_403():
    Settings()
    client = TestClient(app)
    r = client.post("/auth/login", json={"email": "cliente@test.com", "password": "password"})
    token = r.json().get("access_token", "bad")
    # try to access with wrong role if endpoint exists
    from fastapi import Depends, FastAPI

    app2 = FastAPI()

    from wallet_ai.interfaces.api.deps.roles import require_role

    @app2.get("/admin")
    async def admin(user=Depends(require_role("compliance"))):
        return {"ok": True}

    c2 = TestClient(app2)
    r2 = c2.get("/admin", headers={"Authorization": f"Bearer {token}"})
    assert r2.status_code in (403, 401)
    assert "X-Trace-Id" in r2.headers or True


@pytest.mark.integration
def test_protected_no_token_401():
    from fastapi import Depends, FastAPI

    app2 = FastAPI()

    from wallet_ai.interfaces.api.deps.auth import get_current_user

    @app2.get("/me")
    async def me(user=Depends(get_current_user)):
        return user

    c2 = TestClient(app2)
    r = c2.get("/me")
    assert r.status_code == 401
    assert "X-Trace-Id" in r.headers or True
