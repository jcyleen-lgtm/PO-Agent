"""Stockout (censored demand) rule of the Pareto ranking: a zero-sales run is credited only with evidence
that the item was out of stock; a SKU that simply did not sell keeps its zeros."""

from datetime import date, timedelta

import stockout

W0 = date(2026, 1, 5)                                   # a Monday
WEEKS = [W0 + timedelta(weeks=i) for i in range(16)]


def rev(pattern):
    return {w: v for w, v in zip(WEEKS, pattern) if v}


STEADY = [100, 100, 100, 100, 0, 0, 0, 100, 100, 100, 100, 100, 100, 100, 100, 100]


def test_po_receipt_and_resume_is_stockout():
    runs = stockout.detect_runs(WEEKS, rev(STEADY), receipt_weeks={WEEKS[6]})
    assert len(runs) == 1 and runs[0]["weeks"] == 3 and runs[0]["baseline"] == 100
    assert runs[0]["evidence"] == "po_receipt"


def test_zero_stock_snapshot_is_stockout_even_without_resume():
    pattern = [100] * 12 + [0, 0, 0, 0]                  # still out of stock at the end
    runs = stockout.detect_runs(WEEKS, rev(pattern), zero_stock_weeks={WEEKS[14]})
    assert len(runs) == 1 and runs[0]["evidence"] == "zero_stock" and runs[0]["weeks"] == 4


def test_no_evidence_stays_zero():
    assert stockout.detect_runs(WEEKS, rev(STEADY)) == []                       # gap, no receipt, no snapshot
    pattern = [100] * 12 + [0, 0, 0, 0]
    assert stockout.detect_runs(WEEKS, rev(pattern), receipt_weeks={WEEKS[13]}) == []   # never resumed


def test_slow_seller_and_short_gap_are_not_stockouts():
    slow = [100, 0, 0, 100, 0, 0, 0, 100, 0, 0, 100, 0, 0, 0, 100, 0]       # sells in < 3 of 4 baseline weeks
    assert stockout.detect_runs(WEEKS, rev(slow), receipt_weeks=set(WEEKS)) == []
    one = [100, 100, 100, 100, 0, 100, 100, 100, 100, 100, 100, 100, 100, 100, 100, 100]
    assert stockout.detect_runs(WEEKS, rev(one), receipt_weeks=set(WEEKS)) == []    # 1 week < MIN_RUN


def test_adjusted_revenue_rescues_a_best_seller_that_ran_out():
    start, last = WEEKS[4], WEEKS[-1]                   # 12-week window, 4 baseline weeks before it
    weekly = {
        "STAR": rev([1000] * 4 + [1000, 1000, 0, 0, 0, 0, 0, 0, 1000, 1000, 1000, 1000]),
        "MID": rev([0] * 4 + [500] * 12),
    }
    out = stockout.adjusted_revenue(weekly, {"STAR": {WEEKS[11]}}, {}, start, last)
    assert out["STAR"]["revenue"] == 6000 and out["STAR"]["stockout_weeks"] == 6
    assert out["STAR"]["adjusted"] == 12000                                     # 6 weeks × baseline 1000
    assert out["MID"]["adjusted"] == out["MID"]["revenue"] == 6000
    # Without the correction STAR ties MID; with it STAR ranks first.
    assert max(out, key=lambda s: out[s]["adjusted"]) == "STAR"
