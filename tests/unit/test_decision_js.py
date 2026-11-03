"""Action Center buckets and SKU explanations (static/js/core/decision.js), run in Node.

Checks the approved rules (spec §9.2, Phase 5 decisions A1/A2): first match wins, every SKU in exactly one bucket,
explanations only from given fields with a fallback. Skipped when Node is not installed."""

import json
import os
import shutil
import subprocess

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
NODE = shutil.which("node")

ROWS = [
    # sku, order_by_days, estimate, status, reco_po, confirmed_incoming -> expected bucket
    dict(sku="A", order_by="2026-08-10", order_by_days=-85, order_by_is_estimate=False, status="understocked",
         reco_po=2238, confirmed_incoming=0, stockout_week=1, lt_demand_8w=2034.8, safety_stock=203.5,
         inventory_position=0, expect="now"),
    dict(sku="B", order_by="2026-11-03", order_by_days=0, order_by_is_estimate=False, status="healthy",
         reco_po=0, confirmed_incoming=0, stockout_week=10, lt_demand_8w=900, safety_stock=90,
         inventory_position=1234, expect="now"),
    dict(sku="C", order_by="2026-11-10", order_by_days=7, order_by_is_estimate=False, status="healthy",
         reco_po=50, confirmed_incoming=0, stockout_week=9, expect="soon"),
    dict(sku="D", order_by="2026-11-17", order_by_days=14, order_by_is_estimate=False, status="healthy",
         reco_po=0, confirmed_incoming=0, expect="soon"),
    dict(sku="E", order_by="2026-12-30", order_by_days=57, order_by_is_estimate=False, status="overstocked",
         reco_po=0, confirmed_incoming=0, coverage=26.0, expect="hold"),
    dict(sku="F", order_by="2026-12-30", order_by_days=57, order_by_is_estimate=False, status="healthy",
         reco_po=0, confirmed_incoming=300, expect="hold"),
    dict(sku="G", order_by="2026-11-01", order_by_days=-2, order_by_is_estimate=True, status=None,
         reco_po=0, confirmed_incoming=0, expect="none"),       # estimate dates never count as overdue
    dict(sku="H", order_by="2026-11-30", order_by_days=27, order_by_is_estimate=False, status="healthy",
         reco_po=0, confirmed_incoming=0, expect="none"),
    dict(sku="I", order_by="2026-08-10", order_by_days=-5, order_by_is_estimate=False, status="overstocked",
         reco_po=0, confirmed_incoming=0, expect="now"),         # first match wins: order-by beats overstock
    dict(sku="J", expect="none"),                                # no data at all
]

SCRIPT = r"""
const fs = require('fs'), vm = require('vm');
const ctx = { console, Intl, Math, JSON, String, Number, Date, Object, Array, Promise };
vm.createContext(ctx);
const load = f => vm.runInContext(fs.readFileSync(f, 'utf8'), ctx, { filename: f });
load(process.argv[1]);   // core/format.js (with node -e, argv[1] is the first argument)
vm.runInContext("var replData = null; var PT = 'import';" +
  "function naiveWhy(k){return 'Simple average forecast'}", ctx);
load(process.argv[2]);   // core/decision.js
const rows = JSON.parse(process.argv[3]);
const out = rows.map(r => ({ sku: r.sku, bucket: vm.runInContext('bucketOf', ctx)(r),
  why: vm.runInContext('explainSku', ctx)(r, { lead_time_weeks: 8, overstock_weeks: 20 }) }));
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


def test_buckets_follow_the_approved_rules(result):
    for r in ROWS:
        assert result[r["sku"]]["bucket"] == r["expect"], r["sku"]


def test_every_sku_in_exactly_one_bucket(result):
    assert all(result[r["sku"]]["bucket"] in {"now", "soon", "hold", "none"} for r in ROWS)


def test_explanations_use_given_numbers_only(result):
    a = " ".join(result["A"]["why"])
    assert "week 1" in a and "85 days ago" in a and "2.238" in a
    b = " ".join(result["B"]["why"])
    assert "Recommended 0" in b and "week 10" in b          # order-by passed but stock covers the lead time
    assert "26.0 weeks" in " ".join(result["E"]["why"])
    assert "300" in " ".join(result["F"]["why"])


def test_fallback_when_data_is_missing(result):
    assert result["J"]["why"] == ["Not enough data to explain this recommendation."]
