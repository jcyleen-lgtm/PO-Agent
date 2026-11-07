"""Purchasing groups and SKU explanations (static/js/core/decision.js), run in Node.

Rules (R7, regrouped 7 Nov 2026): Understocked → Order Now (recommended > 0 mandatory in the export; recommended 0
carries a reason: stock / PO sufficient, or the Holt forecast reaches 0 within the lead time = forecast warning);
Healthy → Covered / Monitor; Overstocked → No Action Needed; no status → Not classified.
Healthy with a recommendation > 0 is flagged System Recommended and never pre-selected.
Skipped when Node is not installed."""

import json
import os
import shutil
import subprocess

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
NODE = shutil.which("node")

FC_COVER = [207.5, 192.5, 177.5, 162.5, 147.5, 132.5, 117.5, 102.5, 87.5, 72.5, 57.5, 42.5]
FC_ZERO = [16.7, 10.2, 3.6, 0, 0, 0, 0, 0, 0, 0, 0, 0]
FC_LATE_ZERO = [3.6, 3.2, 2.9, 2.6, 2.2, 1.9, 1.5, 1.2, 0.8, 0.5, 0.1, 0]   # 0 only after the 8-week lead time

ROWS = [
    dict(sku="A", status="understocked", reco_po=2238, order_by="2026-08-10", order_by_days=-85, order_by_is_estimate=False,
         stockout_week=1, lt_demand_8w=2034.8, safety_stock=203.5, inventory_position=0, coverage=0.0, fc=FC_COVER,
         expect="order"),
    dict(sku="B", status="understocked", reco_po=0, coverage=7.0, lt_demand_8w=1240, safety_stock=124,
         inventory_position=1860, avg_l4w=264.8, fc=FC_COVER, expect="order", zero="sufficient"),
    dict(sku="C", status="understocked", reco_po=0, coverage=1.1, lt_demand_8w=30.5, safety_stock=3.1,
         inventory_position=49, avg_l4w=43.8, fc=FC_ZERO, expect="order", zero="forecast_zero"),
    dict(sku="D", status="understocked", reco_po=0, coverage=7.8, lt_demand_8w=19.1, safety_stock=1.9,
         inventory_position=35, avg_l4w=4.5, fc=FC_LATE_ZERO, expect="order", zero="sufficient"),
    dict(sku="E", status="healthy", reco_po=420, coverage=12.0, lt_demand_8w=900, safety_stock=90,
         inventory_position=570, fc=FC_COVER, expect="covered", sys=True),
    dict(sku="F", status="healthy", reco_po=0, coverage=15.0, lt_demand_8w=100, safety_stock=10,
         inventory_position=300, fc=FC_COVER, expect="covered", sys=False),
    dict(sku="G", status="overstocked", reco_po=0, coverage=26.0, fc=FC_COVER, expect="none"),
    dict(sku="H", status=None, reco_po=0, expect="unclassified"),
    dict(sku="I", status="understocked", reco_po=0, coverage=3.0, lt_demand_8w=0, safety_stock=0,
         inventory_position=6, expect="order", zero="unknown"),        # no weekly forecast loaded
    dict(sku="J", expect="unclassified"),                                # no data at all
]

SCRIPT = r"""
const fs = require('fs'), vm = require('vm');
const ctx = { console, Intl, Math, JSON, String, Number, Date, Object, Array, Promise };
vm.createContext(ctx);
const load = f => vm.runInContext(fs.readFileSync(f, 'utf8'), ctx, { filename: f });
load(process.argv[1]);   // core/format.js (with node -e, argv[1] is the first argument)
const rows = JSON.parse(process.argv[3]);
const fcMap = {}; rows.forEach(r => { if (r.fc) fcMap[r.sku] = { forecast_data: { quantities: r.fc } } });
vm.runInContext("var replData = null; var PT = 'import'; var fcDataMap = " + JSON.stringify(fcMap) + ";" +
  "function naiveWhy(k){return 'Simple average forecast'} function loadFcMap(){return Promise.resolve()}", ctx);
load(process.argv[2]);   // core/decision.js
const st = { lead_time_weeks: 8, understock_weeks: 8, overstock_weeks: 20 };
const f = n => vm.runInContext(n, ctx);
const out = rows.map(r => ({ sku: r.sku, bucket: f('bucketOf')(r), zero: f('zeroReason')(r, st), sys: f('sysRecommended')(r),
  why: f('explainSku')(r, st) }));
console.log(JSON.stringify(out));
"""


@pytest.fixture(scope="module")
def result():
    if not NODE:
        pytest.skip("node not installed")
    r = subprocess.run([NODE, "-e", SCRIPT, os.path.join(ROOT, "static/js/core/format.js"),
                        os.path.join(ROOT, "static/js/core/decision.js"), json.dumps(ROWS)],
                       capture_output=True, text=True, check=True)
    return {x["sku"]: x for x in json.loads(r.stdout)}


def test_groups_follow_status_and_recommendation(result):
    for r in ROWS:
        assert result[r["sku"]]["bucket"] == r["expect"], r["sku"]


def test_every_sku_in_exactly_one_group(result):
    assert all(result[r["sku"]]["bucket"] in {"order", "covered", "none", "unclassified"} for r in ROWS)


def test_zero_reason_separates_cover_from_forecast_warning(result):
    for r in ROWS:
        assert result[r["sku"]]["zero"] == r.get("zero"), r["sku"]


def test_system_recommended_only_for_healthy_with_recommendation(result):
    assert result["E"]["sys"] is True
    assert not any(result[k]["sys"] for k in "ABCDFGHIJ")


def test_explanations_use_given_numbers_only(result):
    a = " ".join(result["A"]["why"])
    assert "week 1" in a and "85 days ago" in a and "2.238" in a
    b = " ".join(result["B"]["why"])
    assert "Stock / incoming PO sufficient" in b and "1.860" in b
    c = " ".join(result["C"]["why"])
    assert "Forecast warning" in c and "week 4" in c and "43,8" in c and "not because stock covers demand" in c
    assert "System Recommended" in " ".join(result["E"]["why"])
    assert "26.0 weeks" in " ".join(result["G"]["why"])


def test_fallback_when_data_is_missing(result):
    assert result["J"]["why"] == ["Not enough data to explain this recommendation."]


def test_only_understocked_with_recommendation_is_mandatory():
    """The flag fetchRepl sets: Order Now rows with recommended > 0 are mandatory; recommended 0 rows stay manual."""
    mand = {r["sku"] for r in ROWS if r.get("status") == "understocked" and (r.get("reco_po") or 0) > 0}
    assert mand == {"A"}
    src = open(os.path.join(ROOT, "static/js/core/decision.js"), encoding="utf-8").read()
    assert "x.mandatory = x.bucket === 'order' && (x.reco_po || 0) > 0" in src
