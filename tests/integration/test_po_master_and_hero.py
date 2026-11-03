"""Integration: purchasing master-list import (upsert, receipts, close short, receive-all) and
the Hero SKU list (normalisation, scope, naive forecast)."""

from tests.conftest import needs_db, q

pytestmark = needs_db


def test_po_master_import(seeded):
    c, E, S, skus = seeded["client"], seeded["engine"], seeded["S"], seeded["skus"]
    r = c.post("/api/po/upload", files={"file": ("LIST_PO.xlsx", S.po_master_file(skus), "x")})
    j = r.json()
    assert r.status_code == 200 and j["new_lines"] == 26 and j["updated_lines"] == 0
    assert any("0206" in w for w in j["warnings"]) and any("Over" in w for w in j["warnings"])
    n_lines = q(E, "SELECT COUNT(*) FROM purchase_orders WHERE line_key IS NOT NULL")[0][0]
    assert n_lines == 26
    j2 = c.post("/api/po/upload", files={"file": ("LIST_PO.xlsx", S.po_master_file(skus), "x")}).json()
    assert j2["new_lines"] == 0 and j2["updated_lines"] == 26                    # no duplicates

    lid, qo = q(E, "SELECT id, qty_ordered FROM purchase_orders WHERE line_key IS NOT NULL AND status='pending' "
                   "ORDER BY id LIMIT 1")[0]
    e = c.patch(f"/api/po/{lid}", json={"qty_received": qo - 5}).json()
    assert e["line_status"] == "partial" and e["received_date"]
    e = c.patch(f"/api/po/{lid}", json={"qty_received": qo + 3}).json()
    assert e["line_status"] == "completed"
    c.post("/api/po/upload", files={"file": ("LIST_PO.xlsx", S.po_master_file(skus), "x")})
    assert q(E, "SELECT qty_received FROM purchase_orders WHERE id=:i", i=lid)[0][0] == qo + 3   # higher kept

    pid = q(E, "SELECT id FROM purchase_orders WHERE line_key IS NOT NULL AND status='partial' ORDER BY id LIMIT 1")[0][0]
    assert c.patch(f"/api/po/{pid}", json={"status": "closed"}).json()["line_status"] == "closed"
    assert c.patch(f"/api/po/{pid}", json={"status": "reopen"}).json()["line_status"] == "partial"
    assert c.patch(f"/api/po/{pid}", json={"status": "bogus"}).status_code == 400

    po = q(E, "SELECT po_number FROM purchase_orders WHERE status IN ('pending','partial') AND po_number IS NOT NULL "
              "ORDER BY po_number LIMIT 1")[0][0]
    ra = c.post("/api/po/receive-all", json={"po_number": po}).json()
    assert ra["lines"] >= 1
    assert q(E, "SELECT COUNT(*) FROM purchase_orders WHERE po_number=:p AND status IN ('pending','partial')", p=po)[0][0] == 0

    L = c.get("/api/po/list?status=all").json()
    assert L["aging_warn_days"] == 60 and L["count"] == len(L["data"])
    s = c.get("/api/po/summary").json()
    assert s["lead_time"]["available"] and s["lead_time"]["median_days"] is not None
    assert c.get("/api/replenishment?product_type=all").status_code == 200


def test_hero_scope(seeded):
    c, E, S, skus = seeded["client"], seeded["engine"], seeded["S"], seeded["skus"]
    base = {r[0] for r in q(E, "SELECT sku FROM forecast_results")}
    j = c.post("/api/hero-sku/upload", files={"file": ("HERO.xlsx", S.hero_file(skus, base), "x")}).json()
    assert j["skus_saved"] == 5 and j["bundles_split"] == 1 and j["duplicates_skipped"] == 1
    assert j["unmatched_count"] == 1 and j["new_products"] == 1 and j["normalized_count"] == 1
    assert c.get("/api/bestseller/scope").json()["forecast_stale"] is True
    sm = c.post("/api/forecast/run").json()["summary"]
    assert sm["hero_skus"] == 5 and sm["naive_skus"] >= 1
    now = {r[0] for r in q(E, "SELECT sku FROM forecast_results")}
    hero = {r[0] for r in q(E, "SELECT sku FROM hero_skus")}
    assert hero <= now and base <= now                                           # Pareto set unchanged
    sc = c.get("/api/bestseller/scope").json()
    assert sc["forecast_stale"] is False and sc["counts"]["hero_only"] + sc["counts"]["both"] == 5
    inv = c.get("/api/inventory?scope=bestseller&product_type=import").json()["results"]
    assert sum(1 for x in inv if x["scope_source"] in ("hero", "both")) == 5
    new = [x for x in inv if x["sku"] == "299.999.999.001"][0]
    assert new["fc_method"] == "naive" and new["fc_method_reason"] == "no_sales" and new["reco_po"] == 0
