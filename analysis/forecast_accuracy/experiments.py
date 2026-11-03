"""Offline what-if comparison on the same rolling origins (nothing in production changes).

    python analysis/forecast_accuracy/experiments.py --weekly ... --scope ... --last-full-week 2026-10-19

Variants of Holt's DES differ only in how alpha/beta are chosen; benchmarks are naive (last value) and
MA4 (mean of last 4 weeks — the production fallback). Error is reported two ways:
  weekly WAPE   = Σ|weekly error| / Σ actual          (week-by-week accuracy)
  8-week error  = Σ|Σ8 forecast − Σ8 actual| / Σ actual (accuracy of the lead-time total a purchase uses)
"""

import argparse
import csv
import datetime as dt
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
from forecast import holts_des  # noqa: E402

H, MIN_TRAIN, ORIGINS, STEP = 8, 12, 6, 4


def grid(series, betas, crit):
    best, best_v = None, float("inf")
    for a in range(1, 10):
        for b in betas:
            res = holts_des(series, a / 10, b / 10, H)
            if not res:
                continue
            act, fit = series[1:], res["fitted"][1:]
            if crit == "mape":
                v = res["mape"]
            else:  # mae
                v = sum(abs(x - y) for x, y in zip(act, fit)) / len(act)
            if v < best_v:
                best_v, best = v, res
    return best["forecasts"][:H]


def holt_4w(t):
    """Holt's DES on non-overlapping 4-week totals (≈ monthly); 2 blocks ahead, spread evenly per week."""
    n = len(t) // 4
    b = [sum(t[len(t) - 4 * n:][i * 4:(i + 1) * 4]) for i in range(n)]
    best, best_v = None, float("inf")
    for a in range(1, 10):
        for be in range(1, 10):
            res = holts_des(b, a / 10, be / 10, 2)
            if res and res["mape"] < best_v:
                best_v, best = res["mape"], res
    f = best["forecasts"][:2]
    return [f[0] / 4] * 4 + [f[1] / 4] * 4


VARIANTS = {
    "holt_prod (81, min MAPE)": lambda t: grid(t, range(1, 10), "mape"),
    "holt_81_min_MAE": lambda t: grid(t, range(1, 10), "mae"),
    "holt_beta<=0.3_min_MAPE": lambda t: grid(t, range(1, 4), "mape"),
    "holt_beta<=0.3_min_MAE": lambda t: grid(t, range(1, 4), "mae"),
    "holt_4week_series": holt_4w,
    "naive_last": lambda t: [t[-1]] * H,
    "ma4": lambda t: [sum(t[-4:]) / 4] * H,
    "ma8": lambda t: [sum(t[-8:]) / 8] * H,
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--weekly", required=True)
    ap.add_argument("--scope", required=True)
    ap.add_argument("--last-full-week", required=True)
    a = ap.parse_args()
    last = dt.date.fromisoformat(a.last_full_week)
    scope = {r["sku"]: r for r in csv.DictReader(open(a.scope, encoding="utf-8"))}
    acc = {k: {"abs": 0.0, "cum": 0.0, "sum": 0.0, "zero": 0, "best": 0} for k in VARIANTS}
    units, n_fc, skus = 0.0, 0, 0
    for sku, f0, tot, s in csv.reader(open(a.weekly, encoding="utf-8")):
        if scope[sku]["method"] != "holt":
            continue
        q = [float(x) for x in s.split()]
        start = dt.date.fromisoformat(f0)
        q = [x for i, x in enumerate(q) if start + dt.timedelta(weeks=i) <= last]
        per = {k: 0.0 for k in VARIANTS}
        used = False
        for k_o in range(ORIGINS):
            end = len(q) - H - STEP * k_o
            if end < MIN_TRAIN:
                break
            used = True
            train, test = q[:end], q[end:end + H]
            units += sum(test)
            n_fc += 1
            for k, fn in VARIANTS.items():
                fc = fn(train)
                e = sum(abs(x - y) for x, y in zip(test, fc))
                acc[k]["abs"] += e
                per[k] += e
                acc[k]["cum"] += abs(sum(fc) - sum(test))
                acc[k]["sum"] += sum(fc)
                acc[k]["zero"] += sum(fc) == 0
        if used:
            skus += 1
            acc[min(per, key=per.get)]["best"] += 1
    print(f"SKUs {skus}, forecasts {n_fc}, actual units {units:.0f}, last full week {a.last_full_week}")
    print(f"{'variant':28} {'weekly WAPE':>11} {'8-wk err':>9} {'bias':>7} {'zero fc':>8} {'best SKUs':>9}")
    for k, v in acc.items():
        print(f"{k:28} {100 * v['abs'] / units:10.1f}% {100 * v['cum'] / units:8.1f}% "
              f"{100 * (v['sum'] - units) / units:+6.1f}% {100 * v['zero'] / n_fc:7.1f}% {v['best']:9d}")


if __name__ == "__main__":
    main()
