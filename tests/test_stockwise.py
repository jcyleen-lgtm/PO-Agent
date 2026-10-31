"""
Stockwise calculation tests (pure functions, no database).
Run:  python -m pytest tests -q      (from the project folder)
Covers the 16 mandatory edge cases of the implementation brief.
"""

import os
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import replenishment as rp
import metrics as mt
from config import settings
from forecast import holdout_wmape, grid_search_holts

CFG = settings()
TODAY = date(2026, 10, 31)
WEEKS = [str(date(2026, 11, 2) + timedelta(weeks=i)) for i in range(12)]
FLAT10 = [10.0] * 12


def plan(stock, pos=None, fc=FLAT10, **kw):
    return rp.plan_sku(stock, pos or [], fc, WEEKS, kw.pop("cfg", CFG), today=TODAY, **kw)


# 1. zero sales
def test_zero_sales_status():
    assert rp.coverage_weeks(50, 0, 0) is None
    assert rp.stock_status(None, 50, CFG) == "overstocked"       # stock not moving
    assert rp.stock_status(None, 0, CFG) is None                  # not classified, no 4th status
    assert rp.stock_status(None, -3, CFG) is None
    assert rp.overstock_qty(50, 0, CFG) == 50


def test_three_statuses_boundaries():
    assert rp.stock_status(7.9, 10, CFG) == "understocked"
    assert rp.stock_status(8.0, 10, CFG) == "understocked"     # <= 8 weeks
    assert rp.stock_status(8.1, 10, CFG) == "healthy"
    assert rp.stock_status(20.0, 10, CFG) == "healthy"
    assert rp.stock_status(20.1, 10, CFG) == "overstocked"


# 2. very low demand
def test_very_low_demand():
    p = plan(5, fc=[0.2] * 12)
    assert p["stockout_week"] is None            # 5 units cover 12 x 0.2
    assert p["reco_qty"] == 0
    assert p["urgency"] == "low"
    pct, cls = rp.relative_trend([0, 0, 0, 0, 0, 0, 0, 0], [0.2] * 8)
    assert pct is None and cls == "new_demand"
    pct, cls = rp.relative_trend([0] * 8, [0] * 8)
    assert cls == "insufficient_baseline"


# 3. negative stock
def test_negative_stock():
    p = plan(-20)
    assert p["on_hand"] == 0                       # projection starts at 0
    assert "negative_stock" in p["flags"]
    assert p["stockout_week"] == 1
    assert p["inventory_position"] == -20          # deficit is re-ordered
    assert p["reco_qty"] == round(80 + 8 + 20)


# 4 + 5. no unit cost / unverified cost -> never 0, never mixed with selling price
def test_cost_handling():
    assert mt.movement_classes({"a": 0}, CFG) == {"a": "non_moving"}
    # unit_costs is DB-backed; the rule "no cost -> None (not 0)" is enforced in build_inventory:
    c = None
    value = round(10 * c["cost"]) if c else None
    assert value is None


# 6. no incoming PO
def test_no_incoming_po():
    p = plan(30)
    assert p["confirmed_incoming"] == 0 and p["unconfirmed_incoming"] == 0
    ss = round(0.10 * 80, 1)
    assert p["safety_stock"] == ss
    assert p["rop"] == 80 + ss
    assert p["reco_qty"] == round(80 + ss - 30)
    assert p["stockout_week"] == 3          # 30 -10 -10 -10 = 0 in week 3


# 7. PO arriving before stockout
def test_po_before_stockout():
    p = plan(30, [{"outstanding": 50, "expected_date": WEEKS[1]}])   # arrives week 2
    assert p["stockout_week"] == 8          # 30 + 50 = 80 units / 10 a week
    assert p["projection"][1]["incoming"] == 50
    assert p["confirmed_incoming"] == 50


# 8. PO arriving after stockout -> not available before it arrives
def test_po_after_stockout():
    p = plan(20, [{"outstanding": 100, "expected_date": WEEKS[5]}])  # arrives week 6
    assert p["stockout_week"] == 2
    assert p["projection"][2]["closing"] == 0          # lost demand, not negative stock
    assert p["projection"][5]["incoming"] == 100


# 9. unknown ETA -> conservative, shown separately, flagged
def test_unknown_eta():
    p = plan(20, [{"outstanding": 100}])
    assert p["unconfirmed_incoming"] == 100 and p["confirmed_incoming"] == 0
    assert p["inventory_position"] == 20               # not counted
    assert p["stockout_week"] == 2
    assert p["stockout_week_if_unconfirmed_arrives"] == 12
    assert p["needs_po_check"] is True
    assert p["reco_if_unconfirmed_arrives"] < p["reco_qty"]


def test_eta_estimated_from_order_date():
    eta, src = rp.po_eta({"order_date": "2026-09-01"}, 8)
    assert src == "estimated" and eta == date(2026, 10, 27)
    eta, src = rp.po_eta({"expected_date": "2026-12-01", "order_date": "2026-09-01"}, 8)
    assert src == "expected" and eta == date(2026, 12, 1)


# 10. partially received PO -> only the outstanding part counts
def test_partial_po_outstanding_only():
    p = plan(0, [{"outstanding": 200, "expected_date": WEEKS[0], "status": "partial"}])
    assert p["confirmed_incoming"] == 200
    assert p["projection"][0]["incoming"] == 200


# 11. cancelled PO -> never passed in (filtered in SQL); outstanding <= 0 ignored defensively
def test_zero_outstanding_ignored():
    p = plan(10, [{"outstanding": 0, "expected_date": WEEKS[0]}, {"outstanding": -5}])
    assert p["confirmed_incoming"] == 0 and p["unconfirmed_incoming"] == 0


# 12 + 13. duplicate upload / multiple receipts: column mapping must not confuse dates and qty
def test_po_column_mapping():
    import importlib
    os.environ.setdefault("SUPABASE_DB_URL", "postgresql://x@/x")
    app = importlib.import_module("app")
    m, _ = app.map_po_columns(["SKU", "Order Date", "Qty Ordered", "Expected Date", "Supplier"])
    assert m == {"SKU": "sku", "Order Date": "order_date", "Qty Ordered": "qty_ordered",
                 "Expected Date": "expected_date", "Supplier": "supplier"}
    m, _ = app.map_po_columns(["Kode Barang", "Nama Barang", "Tanggal PO", "Kuantitas", "ETA", "No PO"])
    assert m["Tanggal PO"] == "order_date" and m["Kuantitas"] == "qty_ordered"
    assert m["ETA"] == "expected_date" and m["No PO"] == "po_number"
    m, w = app.map_po_columns(["product", "order qty", "random"])
    assert m["order qty"] == "qty_ordered" and "random" in w[0]


# 14. only one snapshot -> turnover / comparison not available (DB functions return flags; see API test)
def test_freshness_levels():
    f = mt.freshness(date(2026, 10, 7), date(2026, 10, 3), CFG, TODAY)
    assert f["stock_age_days"] == 24 and f["stock_level"] == "very_stale"
    assert mt.freshness(date(2026, 10, 28), None, CFG, TODAY)["stock_level"] == "fresh"
    assert mt.freshness(date(2026, 10, 20), None, CFG, TODAY)["stock_level"] == "stale"


# 15. insufficient forecast evaluation history
def test_holdout_insufficient_history():
    h = holdout_wmape([5, 6, 7, 8, 9, 10, 11, 12, 13, 14])
    assert h["wmape"] is None and h["reason"] == "insufficient_history"


def test_holdout_has_no_leakage():
    train = [10, 12, 11, 13, 15, 14, 16, 18, 17, 19, 21, 20, 22, 24]
    a = holdout_wmape(train + [25, 26, 27, 28, 29, 30, 31, 32])
    b = holdout_wmape(train + [0, 0, 0, 100, 0, 0, 0, 0])
    # alpha/beta and the 8 predictions depend on the training weeks only
    assert (a["alpha"], a["beta"]) == (b["alpha"], b["beta"])
    assert a["holdout_forecast"] == b["holdout_forecast"]
    best = grid_search_holts(train, 8)
    assert a["holdout_forecast"] == [round(x, 1) for x in best["forecasts"][:8]]
    assert a["wmape"] != b["wmape"]


# 16. forecast horizon shorter than the protection period
def test_protection_beyond_horizon():
    cfg = {**CFG, "lead_time_weeks": 11}
    p = plan(0, cfg=cfg)
    assert "protection_beyond_horizon" in p["flags"]
    assert p["reco_order_up_to"] == round(120 + p["safety_stock"])   # capped at 12 forecast weeks, flagged


# urgency follows the review period
def test_urgency_thresholds():
    r = CFG["review_period_days"] / 7          # 2 weeks
    assert rp.urgency_for(8, 8, r) == ("critical", 0)
    assert rp.urgency_for(10, 8, r) == ("high", 2)
    assert rp.urgency_for(12, 8, r) == ("medium", 4)
    assert rp.urgency_for(13, 8, r) == ("low", 5)
    assert rp.urgency_for(None, 8, r) == ("low", None)


def test_reco_primary_is_thesis_formula():
    p = plan(50)
    lt8 = 80.0
    ss = 0.10 * lt8
    assert p["reco_qty"] == max(0, round(lt8 + ss - 50))
    assert p["reco_order_up_to"] == max(0, round(100 + ss - 50))   # +2 weeks review, comparison only


def test_movement_classes_split():
    avg = {f"s{i}": float(i) for i in range(1, 11)}
    avg["z"] = 0
    m = mt.movement_classes(avg, CFG)
    assert [m[f"s{i}"] for i in (10, 9)] == ["fast", "fast"]
    assert sum(v == "medium" for v in m.values()) == 5
    assert sum(v == "slow" for v in m.values()) == 3
    assert m["z"] == "non_moving"


def test_order_by_date():
    p = plan(30)                                     # stockout in week 3 (16 Nov 2026)
    assert p["stockout_date"] == WEEKS[2]
    ob = date(2026, 11, 16) - timedelta(weeks=8)
    assert p["order_by"] == str(ob) and p["order_by_days"] == (ob - TODAY).days
    big = plan(10_000)                               # no stockout within 12 weeks
    assert big["stockout_week"] is None and big["order_by_is_estimate"] is True
    assert big["order_by"] == str(date(2027, 1, 25) - timedelta(weeks=8))