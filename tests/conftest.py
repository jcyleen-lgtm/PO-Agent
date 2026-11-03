"""Shared test setup.

Unit tests need nothing. Integration and regression tests need a throw-away PostgreSQL database:

    TEST_DATABASE_URL=postgresql://postgres@localhost:5432/stockwise_test pytest tests

Without TEST_DATABASE_URL those tests are skipped. A URL that points at Supabase is refused, so the
production database can never be reset by a test run.
"""

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

TEST_DB = os.getenv("TEST_DATABASE_URL", "").strip()
USER, PASSWORD = "admin", "rahasia123"

if TEST_DB:
    if "supabase" in TEST_DB or "pooler" in TEST_DB:
        raise RuntimeError("TEST_DATABASE_URL points at Supabase. Tests reset the database: use a local one.")
    os.environ["SUPABASE_DB_URL"] = TEST_DB
else:
    os.environ.setdefault("SUPABASE_DB_URL", "postgresql://x@/x")   # import only; unit tests never connect
os.environ.setdefault("APP_USERNAME", USER)
os.environ.setdefault("SESSION_SECRET", "x" * 40)
if "APP_PASSWORD_HASH" not in os.environ:
    from auth import hash_password
    os.environ["APP_PASSWORD_HASH"] = hash_password(PASSWORD)

needs_db = pytest.mark.skipif(not TEST_DB, reason="TEST_DATABASE_URL not set")


def reset_app_state(app_module):
    """Clear per-process caches so a fresh schema is seen (the app normally lives one process per deploy)."""
    for name in ("reset_runtime_state",):
        fn = getattr(app_module, name, None)
        if fn:
            fn()
            return
    app_module.forecast_cache.clear()
    if hasattr(app_module, "_PO_COLS_READY"):
        app_module._PO_COLS_READY = False


@pytest.fixture(scope="module")
def seeded():
    """Fresh schema + synthetic data + a logged-in TestClient. One per test module."""
    if not TEST_DB:
        pytest.skip("TEST_DATABASE_URL not set")
    import app as A
    from fastapi.testclient import TestClient
    from tests.fixtures import seed as S

    engine = A.get_engine()
    skus = S.seed(engine)
    reset_app_state(A)
    client = TestClient(A.app)
    assert client.post("/api/auth/login", json={"username": USER, "password": PASSWORD}).status_code == 200
    assert client.post("/api/forecast/run").status_code == 200
    return {"app": A, "client": client, "engine": engine, "skus": skus, "S": S}


def q(engine, sql, **params):
    from sqlalchemy import text
    with engine.connect() as k:
        return k.execute(text(sql), params).fetchall()
