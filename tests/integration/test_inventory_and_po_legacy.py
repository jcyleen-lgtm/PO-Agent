"""Integration: auth, old-format PO upload, ETA hierarchy, snapshots, cross-page consistency.
Ported from the scratch DB suite used during development (18 checks)."""

import pandas as pd

from tests.conftest import needs_db, q

pytestmark = needs_db


def test_auth_enforced(seeded):
    from fastapi.testclient import TestClient
    anon = TestClient(seeded["app"].app)
    assert anon.get("/api/inventory").status_code == 401
    assert anon.post("/api/auth/login", json={"username": "admin", "password": "wrong"}).status_code == 401


def test_po_legacy_upload_and_consistency(seeded):
    c, E, S = seeded["client"], seeded["engine"], seeded["S"]
    before = dict(q(E, "SELECT status, COUNT(*) FROM purchase_orders GROUP BY status"))
    skus = [r[0] for r in q(E, "SELECT sku FROM forecast_results ORDER BY sku LIMIT 3")]
    df = pd.DataFrame({"SKU": skus, "Order Date": ["01/10/2026"] * 3, "Qty Ordered": [120, 80, 50],
                       "Expected Date": ["15/11/2026", None, None], "Supplier": ["Sup A", "Sup A", "Sup B"],
                       "Status": ["Open", "Open", "Cancelled"]})
    j = c.post("/api/po/upload", files={"file": ("po.xlsx", S.xlsx(df), "x")}).json()
    assert j["column_mapping"]["Order Date"] == "order_date" and j["column_mapping"]["Qty Ordered"] == "qty_ordered"
    after = dict(q(E, "SELECT status, COUNT(*) FROM purchase_orders GROUP BY status"))
    assert after.get("cancelled", 0) == before.get("cancelled", 0) + 1          # only the explicit cancel
    assert after.get("superseded", 0) == before.get("pending", 0) + before.get("partial", 0)
    rows = q(E, "SELECT order_date, expected_date FROM purchase_orders WHERE status IN ('pending','cancelled') "
                "AND supplier IS NOT NULL ORDER BY id")
    assert str(rows[0][0]) == "2026-10-01" and str(rows[0][1]) == "2026-11-15"   # day-first dates

    c.post("/api/po/upload", files={"file": ("po.xlsx", S.xlsx(df), "x")})        # same file again
    assert q(E, "SELECT COUNT(*) FROM purchase_orders WHERE status IN ('pending','partial')")[0][0] == 2

    src = sorted(x["eta_source"] for x in c.get("/api/po/list").json()["data"])
    assert src == ["estimated", "expected"]

    d = c.get(f"/api/replenishment/sku/{skus[0]}").json()
    inc = [w for w in d["plan"]["projection"] if w["incoming"] > 0]
    assert len(inc) == 1 and inc[0]["incoming"] == 120                            # PO lands in its ETA week
    assert len(d["safety_stock_comparison"]["sensitivity"]) == 3

    rk = c.get("/api/best-sellers-at-risk?product_type=all").json()
    assert rk["comparison_available"] is False
    assert c.get("/api/inventory/summary").json()["turnover"]["available"] is False

    stock = pd.DataFrame([[s, "x", "1.234,00"] for s in skus] + [["299.999.99.001", "new", "5"]])
    r = c.post("/api/stock/upload", files={"file": ("stok.xlsx", S.xlsx(stock), "x")}, data={"report_date": "2026-08-10"})
    assert r.status_code == 200
    assert q(E, "SELECT COUNT(*) FROM stock_snapshots WHERE snapshot_date='2026-08-10'")[0][0] >= 3
    bad = c.post("/api/stock/upload", files={"file": ("s.xlsx", S.xlsx(stock), "x")}, data={"report_date": "2099-01-01"})
    assert bad.status_code == 400

    rk = c.get("/api/best-sellers-at-risk?product_type=all").json()
    assert rk["comparison_available"] is True and rk["stock_prev_date"] == "2026-08-10"
    t = c.get("/api/inventory/summary?product_type=all").json()["turnover"]
    assert t["available"] is False and t["skipped_partial_snapshots"] == 1

    inv = {r["sku"]: r for r in c.get("/api/inventory?product_type=all").json()["results"]}
    rep = {r["sku"]: r for r in c.get("/api/replenishment?product_type=all").json()["results"]}
    key = lambda r: (r["status"], r["coverage"], r["reco_po"], r["urgency"])
    assert all(key(rep[s]) == key(inv[s]) for s in rep)
    assert all((x["coverage"], x["urgency"]) == (inv[x["sku"]]["coverage"], inv[x["sku"]]["urgency"]) for x in rk["results"])
    assert all(r["status"] in ("understocked", "healthy", "overstocked", None) for r in inv.values())
    s_ = c.get("/api/inventory/summary?product_type=all").json()["summary"]
    assert s_["understocked"] + s_["healthy"] + s_["overstocked"] + s_["unclassified"] == s_["total"]
    nc = [r for r in inv.values() if r["unit_cost"] is None]
    assert nc and all(r["inventory_value"] is None for r in nc)
