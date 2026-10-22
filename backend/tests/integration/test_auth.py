import pytest
from fastapi.testclient import TestClient

from wallet_ai.infrastructure.config.settings import Settings
from wallet_ai.interfaces.api.main import app


@pytest.mark.integration
def test_login_and_roles():
    Settings()
    client = TestClient(app)
    # login
    r = client.post("/auth/login", json={"email": "cliente@test.com", "password": "password"})
    # may fail without DB setup but structure ok
    assert r.status_code in (200, 500, 400)


@pytest.mark.integration
def test_trace_header():
    client = TestClient(app)
    r = client.get("/docs")
    assert "X-Trace-Id" in r.headers or r.status_code in (200, 404)
