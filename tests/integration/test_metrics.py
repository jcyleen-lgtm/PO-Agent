"""Integration: unit cost and inventory turnover on a tiny hand-checked data set."""

import datetime as dt

import pytest
from sqlalchemy import text

from tests.conftest import needs_db

pytestmark = needs_db


@pytest.fixture(scope="module")
def tiny():
    import app as A
    import metrics as mt
    from config import settings
    from tests.fixtures import seed as S

    engine = A.get_engine()
    S.reset_schema(engine)
    A.reset_runtime_state()
    today = dt.date.today()
    with engine.begin() as c:
        c.execute(text("INSERT INTO products (sku, name) VALUES ('A', 'a'), ('B', 'b'), ('C', 'c')"))
        # A: verified HPP; B: cogs from Accurate (12-week window); C: no cost at all
        c.execute(text("INSERT INTO master_cogs (sku, hpp) VALUES ('A', 1000)"))
        rows = [("A", today - dt.timedelta(days=10), 10, 50000, 9000),
                ("B", today - dt.timedelta(days=10), 4, 40000, 8000),
                ("B", today - dt.timedelta(days=20), 6, 60000, 13000),
                ("B", today - dt.timedelta(weeks=20), 100, 999999, 999999),   # outside the 12-week window
                ("C", today - dt.timedelta(days=5), 1, 10000, None)]
        c.execute(text("INSERT INTO sales (sku, invoice_date, quantity, amount, cogs_accurate) VALUES (:s, :d, :q, :a, :c)"),
                  [dict(zip("sdqac", r)) for r in rows])
        d0, d1 = today - dt.timedelta(days=28), today - dt.timedelta(days=0)
        c.execute(text("INSERT INTO stock_snapshots (sku, quantity, snapshot_date) VALUES "
                       "('A', 100, :d0), ('B', 50, :d0), ('A', 60, :d1), ('B', 30, :d1), ('C', 5, :d1)"),
                  {"d0": d0, "d1": d1})
    return {"engine": engine, "mt": mt, "cfg": settings(), "d0": d0, "d1": d1}


def test_unit_cost_sources(tiny):
    with tiny["engine"].connect() as conn:
        costs = tiny["mt"].unit_costs(conn, tiny["cfg"])
    assert costs["A"] == {"cost": 1000.0, "source": "master_cogs", "verified": True}
    assert costs["B"]["cost"] == pytest.approx((8000 + 13000) / (4 + 6)) and costs["B"]["verified"] is False
    assert "C" not in costs                                     # no cost -> missing, never 0


def test_inventory_turnover(tiny):
    mt = tiny["mt"]
    with tiny["engine"].connect() as conn:
        costs = mt.unit_costs(conn, tiny["cfg"])
        t = mt.inventory_turnover(conn, costs)
    v0 = 100 * 1000 + 50 * 2100                                  # value at first snapshot
    v1 = 60 * 1000 + 30 * 2100                                   # C has no cost
    cogs = 9000 + 8000 + 13000                                   # sales after d0, up to d1
    weeks = (tiny["d1"] - tiny["d0"]).days / 7
    assert t["available"] is True
    assert t["turnover"] == round(cogs * 52 / weeks / ((v0 + v1) / 2), 2)
    assert t["snapshots"] == 2 and t["avg_inventory_value"] == round((v0 + v1) / 2)
