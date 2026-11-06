"""Integration: the Pareto 80% best-seller cut uses only the last `pareto_window_weeks` (26 ≈ 6 months) of
import revenue, ending at the latest sales week. A SKU that sold a lot only before that window is not a Pareto
best seller; the forecast run and /api/bestseller/scope use the same set."""

from sqlalchemy import text

from tests.conftest import needs_db, q

pytestmark = needs_db


def _pareto_sql(E, weeks=26):
    rows = q(E, """
        WITH w AS (SELECT s.sku, DATE_TRUNC('week', s.invoice_date)::date wk, SUM(s.amount) a
                   FROM sales s JOIN products p ON p.sku = s.sku
                   WHERE s.invoice_date IS NOT NULL AND COALESCE(p.product_type, 'import') = 'import' GROUP BY 1, 2),
             mx AS (SELECT MAX(wk) m FROM w),
             r AS (SELECT sku, SUM(a) rev FROM w, mx WHERE wk > mx.m - :d GROUP BY sku HAVING SUM(a) > 0),
             c AS (SELECT sku, SUM(rev) OVER (ORDER BY rev DESC, sku) - rev prev, SUM(rev) OVER () tot FROM r)
        SELECT sku FROM c WHERE prev < 0.8 * tot""", d=weeks * 7)
    return {r[0] for r in rows}


def test_pareto_uses_last_26_weeks(seeded, monkeypatch):
    monkeypatch.setenv("SW_PARETO_STOCKOUT_ADJUST", "false")    # plain revenue here; the correction is tested below
    c, E = seeded["client"], seeded["engine"]
    last = q(E, "SELECT MAX(invoice_date) FROM sales")[0][0]
    # A product with very large revenue, but only ~40 weeks before the latest sale: outside the 6-month window.
    with E.begin() as k:
        k.execute(text("INSERT INTO products (sku, name, product_type) VALUES ('OLD-STAR', 'Old star', 'import') "
                       "ON CONFLICT (sku) DO NOTHING"))
        for i in range(6):
            k.execute(text("INSERT INTO sales (invoice_date, sku, quantity, amount) "
                           "VALUES (:d, 'OLD-STAR', 500, 900000000)"), {"d": last - __import__('datetime').timedelta(weeks=40 + i)})
    sm = c.post("/api/forecast/run").json()["summary"]
    assert sm["pareto_window_weeks"] == 26
    pareto = {r[0] for r in q(E, "SELECT sku FROM forecast_results "
                                 "WHERE (forecast_data::jsonb->'scope'->>'pareto')::boolean")}
    want = _pareto_sql(E)
    assert sm["pareto_skus_included"] == len(want)
    assert pareto <= want                       # Pareto SKUs without recent sales are skipped by the forecast run
    assert "OLD-STAR" not in want and "OLD-STAR" not in pareto
    sc = c.get("/api/bestseller/scope").json()
    assert sc["period"]["window_weeks"] == 26 and sc["pareto"]["skus"] == len(want)


def test_stockout_weeks_keep_a_best_seller_in_pareto(seeded, monkeypatch):
    """RAN-OUT sold a lot just before the window, then had no sales for 23 weeks until a PO was received, and
    sells again: raw window revenue is tiny, but the stockout weeks are credited, so it stays a Pareto SKU.
    Forecast run and /api/bestseller/scope agree; with the correction switched off it drops out."""
    from datetime import timedelta
    c, E = seeded["client"], seeded["engine"]
    last = q(E, "SELECT MAX(invoice_date) FROM sales")[0][0]
    with E.begin() as k:
        k.execute(text("INSERT INTO products (sku, name, product_type) VALUES ('RAN-OUT', 'Ran out', 'import') "
                       "ON CONFLICT (sku) DO NOTHING"))
        for w in (26, 27, 28, 29):                    # baseline: the 4 weeks just before the 26-week window
            k.execute(text("INSERT INTO sales (invoice_date, sku, quantity, amount) VALUES (:d, 'RAN-OUT', 500, 900000000)"),
                      {"d": last - timedelta(weeks=w)})
        for w in (2, 1, 0):                           # sells again after the PO arrived
            k.execute(text("INSERT INTO sales (invoice_date, sku, quantity, amount) VALUES (:d, 'RAN-OUT', 1, 1000)"),
                      {"d": last - timedelta(weeks=w)})
        k.execute(text("INSERT INTO purchase_orders (sku, qty_ordered, qty_received, status, order_date, received_date) "
                       "VALUES ('RAN-OUT', 2000, 2000, 'received', :o, :r)"),
                  {"o": last - timedelta(weeks=12), "r": last - timedelta(weeks=3)})

    def pareto():
        return {r[0] for r in q(E, "SELECT sku FROM forecast_results "
                                   "WHERE (forecast_data::jsonb->'scope'->>'pareto')::boolean")}

    monkeypatch.setenv("SW_PARETO_STOCKOUT_ADJUST", "true")
    sm = c.post("/api/forecast/run").json()["summary"]
    assert sm["pareto_stockout_adjust"] is True and sm["pareto_stockout_skus"] >= 1
    assert "RAN-OUT" in pareto()
    row = q(E, "SELECT forecast_data::jsonb->'scope' FROM forecast_results WHERE sku = 'RAN-OUT'")[0][0]
    assert row["revenue"] == 3000 and row["stockout_weeks"] == 23 and row["revenue_adjusted"] > row["revenue"]
    sc = c.get("/api/bestseller/scope").json()
    assert sc["pareto"]["skus"] == sm["pareto_skus_included"] and sc["stockout"]["enabled"] is True
    r = next(x for x in sc["rows"] if x["sku"] == "RAN-OUT")
    assert r["pareto"] and r["stockout_weeks"] == 23 and r["revenue"] == 3000

    monkeypatch.setenv("SW_PARETO_STOCKOUT_ADJUST", "false")
    c.post("/api/forecast/run")
    assert "RAN-OUT" not in pareto()
