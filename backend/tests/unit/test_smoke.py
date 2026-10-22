# backend/tests/unit/test_smoke.py
def test_package_imports():
    import wallet_ai

    assert wallet_ai is not None
