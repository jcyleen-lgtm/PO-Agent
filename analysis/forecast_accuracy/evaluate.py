"""Forecast accuracy analysis for Stockwise (read-only, reproducible).

    python analysis/forecast_accuracy/evaluate.py \
        --weekly analysis/forecast_accuracy/data/weekly_2026-11-03.csv \
        --scope  analysis/forecast_accuracy/data/scope_2026-11-03.csv \
        --last-full-week 2026-10-19 --out analysis/forecast_accuracy/output

Inputs come from `export.sql` (read-only SELECTs). The model is the production code itself
(`forecast.holts_des`, `forecast.grid_search_holts`), so nothing here can drift from what the app does.
Nothing is written to the database.
"""

import argparse
import csv
import datetime as dt
import json
import os
import statistics as st
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
from forecast import grid_search_holts  # noqa: E402  (production model, unchanged)

H = 8              # holdout weeks, as in production (forecast.HOLDOUT_WEEKS)
MIN_TRAIN = 12     # as in production (forecast.MIN_TRAIN_WEEKS)
LOW_BUCKETS = [(1, 1), (2, 4), (5, 9), (10, 10**9)]


# ── metrics ────────────────────────────────────────────────────────────────
def mape(a, f):
    """Mean absolute percentage error over periods with actual > 0 (production rule)."""
    t = [abs(x - y) / x for x, y in zip(a, f) if x > 0]
    return 100 * sum(t) / len(t) if t else None


def wape(a, f):
    s = sum(a)
    return 100 * sum(abs(x - y) for x, y in zip(a, f)) / s if s > 0 else None


def bias(a, f):
    s = sum(a)
    return 100 * (sum(f) - sum(a)) / s if s > 0 else None


def cum_err(a, f):
    """Error of the total over the window (what a purchase quantity depends on)."""
    s = sum(a)
    return 100 * abs(sum(f) - s) / s if s > 0 else None


def mase(a, f, train):
    """MAE / in-sample MAE of the one-step naive forecast (Hyndman & Koehler 2006)."""
    d = [abs(train[i] - train[i - 1]) for i in range(1, len(train))]
    scale = sum(d) / len(d) if d else 0
    if scale == 0:
        return None
    return (sum(abs(x - y) for x, y in zip(a, f)) / len(a)) / scale


def demand_class(series):
    """Syntetos-Boylan-Croston (2005): ADI = periods / non-zero periods, CV² of non-zero sizes."""
    nz = [x for x in series if x > 0]
    if len(nz) < 2:
        return "insufficient", None, None
    adi = len(series) / len(nz)
    m = st.mean(nz)
    cv2 = (st.pstdev(nz) / m) ** 2 if m else 0
    if adi < 1.32:
        return ("smooth" if cv2 < 0.49 else "erratic"), adi, cv2
    return ("intermittent" if cv2 < 0.49 else "lumpy"), adi, cv2


def q(xs, p):
    xs = sorted(x for x in xs if x is not None)
    if not xs:
        return None
    k = (len(xs) - 1) * p
    lo, hi = int(k), min(int(k) + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


def r(x, n=1):
    return None if x is None else round(x, n)


# ── data ───────────────────────────────────────────────────────────────────
def load(weekly_path, scope_path, last_full_week):
    scope = {row["sku"]: row for row in csv.DictReader(open(scope_path, encoding="utf-8"))}
    data = {}
    for sku, f0, tot, s in csv.reader(open(weekly_path, encoding="utf-8")):
        qty = [float(x) for x in s.split()]
        assert sum(qty) == float(tot), f"checksum {sku}"
        start = dt.date.fromisoformat(f0)
        weeks = [start + dt.timedelta(weeks=i) for i in range(len(qty))]
        keep = [i for i, w in enumerate(weeks) if w <= last_full_week]
        data[sku] = {"series": [qty[i] for i in keep], "full_series": qty, "scope": scope[sku]}
    return data, scope


def blocks4(series):
    """Non-overlapping 4-week totals aligned to the end (≈ monthly); a leading partial block is dropped."""
    n = len(series) // 4
    s = series[len(series) - 4 * n:]
    return [sum(s[i * 4:(i + 1) * 4]) for i in range(n)]


# ── per-SKU evaluation ─────────────────────────────────────────────────────
def evaluate_sku(series):
    out = {"weeks": len(series), "units": sum(series), "zero_share": r(100 * series.count(0) / len(series))}
    out["class"], out["adi"], out["cv2"] = demand_class(series)
    fit = grid_search_holts(series)
    if fit is None:
        return out
    a, f = series[1:], fit["fitted"][1:]
    out.update(alpha=fit["alpha"], beta=fit["beta"], ins_mape=fit["mape"], ins_wape=wape(a, f),
               ins_mape_ge5=mape([x for x in a if x >= 5], [y for x, y in zip(a, f) if x >= 5]),
               neg_fitted=sum(1 for y in f if y < 0),
               ins_abs_err=sum(abs(x - y) for x, y in zip(a, f)), ins_units=sum(a))
    # contribution of low-volume weeks to the in-sample MAPE (sum of APE terms)
    terms = [(x, abs(x - y) / x) for x, y in zip(a, f) if x > 0]
    tot = sum(t for _, t in terms)
    out["ape_share"] = {f"{lo}-{hi if hi < 10**9 else '+'}": (100 * sum(t for x, t in terms if lo <= x <= hi) / tot
                                                              if tot else None) for lo, hi in LOW_BUCKETS}
    out["week_share"] = {f"{lo}-{hi if hi < 10**9 else '+'}": 100 * sum(1 for x, _ in terms if lo <= x <= hi) / len(terms)
                         for lo, hi in LOW_BUCKETS} if terms else None
    out["unit_share"] = {f"{lo}-{hi if hi < 10**9 else '+'}": (100 * sum(x for x, _ in terms if lo <= x <= hi) / sum(a)
                                                               if sum(a) else None) for lo, hi in LOW_BUCKETS}

    # 4-week (≈ monthly) in-sample, model refitted on 4-week totals
    b = blocks4(series)
    if len(b) >= 6:
        fb = grid_search_holts(b)
        out["m4_ins_mape"] = fb["mape"] if fb else None
        out["m4_ins_wape"] = wape(b[1:], fb["fitted"][1:]) if fb else None

    # Holdout: last H weeks, parameters chosen on train only (production definition)
    if len(series) >= H + MIN_TRAIN:
        train, test = series[:-H], series[-H:]
        ft = grid_search_holts(train, H)
        holt = ft["forecasts"][:H]
        naive_last = [train[-1]] * H
        ma4 = [sum(train[-4:]) / 4] * H
        hz = {}
        for name, fc in (("holt", holt), ("naive", naive_last), ("ma4", ma4)):
            hz[name] = {"mape": mape(test, fc), "wape": wape(test, fc), "bias": bias(test, fc),
                        "cum": cum_err(test, fc), "mase": mase(test, fc, train)}
        hz["holt"]["by_4w"] = wape([sum(test[:4]), sum(test[4:])], [sum(holt[:4]), sum(holt[4:])])
        out["holdout"] = hz
        out["test_units"] = sum(test)
        out["test_abs_err"] = {k: sum(abs(x - y) for x, y in zip(test, fc))
                               for k, fc in (("holt", holt), ("naive", naive_last), ("ma4", ma4))}
        out["test_sum_fc"] = {k: sum(fc) for k, fc in (("holt", holt), ("naive", naive_last), ("ma4", ma4))}
        # 4-week model, 2 blocks ahead (= the same 8 weeks)
        bt = blocks4(train)
        if len(bt) >= 6:
            fb = grid_search_holts(bt, 2)
            tb = [sum(test[:4]), sum(test[4:])]
            out["m4_holdout_wape"] = wape(tb, fb["forecasts"][:2]) if fb else None
            out["m4_holdout_mape"] = mape(tb, fb["forecasts"][:2]) if fb else None
        out["holdout_zero_forecast"] = sum(holt) == 0

    # Rolling origin (robustness): origins every 4 weeks, up to 6 origins, each forecasting H weeks
    ro = {"units": 0.0, "holt": 0.0, "naive": 0.0, "ma4": 0.0, "origins": 0, "holt_sum": 0.0, "zero_fc": 0}
    for k in range(6):
        end = len(series) - H - 4 * k
        if end < MIN_TRAIN:
            break
        train, test = series[:end], series[end:end + H]
        ft = grid_search_holts(train, H)
        fcs = {"holt": ft["forecasts"][:H], "naive": [train[-1]] * H, "ma4": [sum(train[-4:]) / 4] * H}
        ro["units"] += sum(test)
        for m, fc in fcs.items():
            ro[m] += sum(abs(x - y) for x, y in zip(test, fc))
        ro["holt_sum"] += sum(fcs["holt"])
        ro["zero_fc"] += sum(fcs["holt"]) == 0
        ro["origins"] += 1
    if ro["origins"]:
        out["rolling"] = ro

    # Production forecast for the next 12 weeks (full series): is it all zero?
    out["prod_fc_zero"] = sum(fit["forecasts"]) == 0
    out["prod_fc_vs_last8"] = (sum(fit["forecasts"][:8]) / sum(series[-8:]) if sum(series[-8:]) else None)
    return out


# ── summary ────────────────────────────────────────────────────────────────
def dist(xs):
    xs = [x for x in xs if x is not None]
    return {"n": len(xs), "median": r(q(xs, .5)), "p25": r(q(xs, .25)), "p75": r(q(xs, .75)), "p90": r(q(xs, .9))}


def summarise(res, scope):
    holt = {s: v for s, v in res.items() if scope[s]["method"] == "holt" and "ins_mape" in v}
    hold = {s: v for s, v in holt.items() if "holdout" in v}
    S = {"skus_total": len(scope), "skus_holt": len(holt), "skus_with_holdout": len(hold)}

    S["in_sample"] = {
        "weekly_mape": dist([v["ins_mape"] for v in holt.values()]),
        "weekly_wape": dist([v["ins_wape"] for v in holt.values()]),
        "weekly_mape_actual_ge5": dist([v["ins_mape_ge5"] for v in holt.values()]),
        "m4_mape": dist([v.get("m4_ins_mape") for v in holt.values()]),
        "m4_wape": dist([v.get("m4_ins_wape") for v in holt.values()]),
        "pooled_wape": r(100 * sum(v["ins_abs_err"] for v in holt.values()) / sum(v["ins_units"] for v in holt.values())),
        "skus_with_negative_fitted": sum(1 for v in holt.values() if v["neg_fitted"]),
    }
    keys = [f"{lo}-{hi if hi < 10**9 else '+'}" for lo, hi in LOW_BUCKETS]
    S["low_volume"] = {k: {"median_share_of_ape": r(q([v["ape_share"][k] for v in holt.values()], .5)),
                           "median_share_of_weeks": r(q([v["week_share"][k] for v in holt.values() if v["week_share"]], .5)),
                           "median_share_of_units": r(q([v["unit_share"][k] for v in holt.values()], .5))} for k in keys}
    S["zero_weeks_share"] = dist([v["zero_share"] for v in holt.values()])
    cls = {}
    for v in holt.values():
        cls.setdefault(v["class"], []).append(v)
    S["demand_class"] = {c: {"skus": len(vs), "median_ins_mape": r(q([v["ins_mape"] for v in vs], .5)),
                             "median_holdout_wape": r(q([v["holdout"]["holt"]["wape"] for v in vs if "holdout" in v], .5)),
                             "median_holdout_mase": r(q([v["holdout"]["holt"]["mase"] for v in vs if "holdout" in v], .5), 2)}
                         for c, vs in sorted(cls.items())}

    hz = {}
    for m in ("holt", "naive", "ma4"):
        hz[m] = {k: dist([v["holdout"][m][k] for v in hold.values()]) for k in ("mape", "wape", "bias", "cum", "mase")}
        tu = sum(v["test_units"] for v in hold.values())
        hz[m]["pooled_wape"] = r(100 * sum(v["test_abs_err"][m] for v in hold.values()) / tu)
        hz[m]["pooled_bias"] = r(100 * (sum(v["test_sum_fc"][m] for v in hold.values()) - tu) / tu)
    hz["holt"]["by_4w_wape"] = dist([v["holdout"]["holt"]["by_4w"] for v in hold.values()])
    hz["m4_model_wape"] = dist([v.get("m4_holdout_wape") for v in hold.values()])
    hz["holt_beats_naive_mase"] = sum(1 for v in hold.values()
                                      if v["holdout"]["holt"]["mase"] is not None and v["holdout"]["naive"]["mase"] is not None
                                      and v["holdout"]["holt"]["mase"] < v["holdout"]["naive"]["mase"])
    hz["holt_beats_ma4_wape"] = sum(1 for v in hold.values()
                                    if None not in (v["holdout"]["holt"]["wape"], v["holdout"]["ma4"]["wape"])
                                    and v["holdout"]["holt"]["wape"] < v["holdout"]["ma4"]["wape"])
    hz["mase_lt_1"] = sum(1 for v in hold.values() if (v["holdout"]["holt"]["mase"] or 9) < 1)
    hz["holt_zero_forecast_skus"] = sum(1 for v in hold.values() if v.get("holdout_zero_forecast"))
    S["holdout"] = hz

    rol = [v["rolling"] for v in holt.values() if "rolling" in v]
    U = sum(x["units"] for x in rol)
    S["rolling_origin"] = {"skus": len(rol), "forecasts": sum(x["origins"] for x in rol),
                           "pooled_wape": {m: r(100 * sum(x[m] for x in rol) / U) for m in ("holt", "naive", "ma4")},
                           "pooled_bias_holt": r(100 * (sum(x["holt_sum"] for x in rol) - U) / U),
                           "zero_forecast_share": r(100 * sum(x["zero_fc"] for x in rol) / sum(x["origins"] for x in rol)),
                           "skus_holt_best": sum(1 for x in rol if x["holt"] < min(x["naive"], x["ma4"]))}
    S["production_forecast"] = {
        "skus_all_zero_next12": sum(1 for v in holt.values() if v.get("prod_fc_zero")),
        "next8_vs_last8_ratio": dist([v.get("prod_fc_vs_last8") for v in holt.values()]),
    }

    # consistency: groups and the revenue-important SKUs
    def grp(pred):
        vs = [v for s, v in hold.items() if pred(scope[s])]
        tu = sum(v["test_units"] for v in vs)
        return {"skus": len(vs), "median_wape": r(q([v["holdout"]["holt"]["wape"] for v in vs], .5)),
                "median_mape": r(q([v["holdout"]["holt"]["mape"] for v in vs], .5)),
                "pooled_wape": r(100 * sum(v["test_abs_err"]["holt"] for v in vs) / tu) if tu else None}
    S["groups"] = {"pareto": grp(lambda x: x["pareto"] == "true"),
                   "hero_only": grp(lambda x: x["pareto"] != "true" and x["hero"] == "true"),
                   "both": grp(lambda x: x["pareto"] == "true" and x["hero"] == "true")}
    ranked = sorted(hold, key=lambda s: -float(scope[s]["revenue"]))
    top = ranked[:20]
    S["top20_revenue"] = [{"sku": s, "revenue": int(float(scope[s]["revenue"])), "class": hold[s]["class"],
                           "ins_mape": r(hold[s]["ins_mape"]), "hold_mape": r(hold[s]["holdout"]["holt"]["mape"]),
                           "hold_wape": r(hold[s]["holdout"]["holt"]["wape"]), "hold_cum": r(hold[s]["holdout"]["holt"]["cum"]),
                           "hold_bias": r(hold[s]["holdout"]["holt"]["bias"]), "mase": r(hold[s]["holdout"]["holt"]["mase"], 2),
                           "naive_mase": r(hold[s]["holdout"]["naive"]["mase"], 2)} for s in top]
    S["top20_share_of_test_units"] = r(100 * sum(hold[s]["test_units"] for s in top) / sum(v["test_units"] for v in hold.values()))
    S["top20_pooled_wape"] = r(100 * sum(hold[s]["test_abs_err"]["holt"] for s in top) / sum(hold[s]["test_units"] for s in top))
    S["rest_pooled_wape"] = r(100 * sum(hold[s]["test_abs_err"]["holt"] for s in ranked[20:]) /
                              sum(hold[s]["test_units"] for s in ranked[20:]))
    S["top20_wape_gt50"] = sum(1 for s in top if (hold[s]["holdout"]["holt"]["wape"] or 0) > 50)
    return S


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--weekly", required=True)
    ap.add_argument("--scope", required=True)
    ap.add_argument("--last-full-week", required=True, help="Monday of the last complete week to use")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    last = dt.date.fromisoformat(a.last_full_week)
    data, scope = load(a.weekly, a.scope, last)
    res = {s: evaluate_sku(d["series"]) for s, d in sorted(data.items())}
    S = summarise(res, scope)
    S["last_full_week"] = a.last_full_week
    os.makedirs(a.out, exist_ok=True)
    tag = a.last_full_week
    with open(os.path.join(a.out, f"summary_{tag}.json"), "w", encoding="utf-8") as fh:
        json.dump(S, fh, indent=1)
    with open(os.path.join(a.out, f"per_sku_{tag}.csv"), "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["sku", "pareto", "hero", "revenue", "class", "weeks", "zero_share", "alpha", "beta", "ins_mape",
                    "ins_wape", "m4_ins_mape", "hold_mape", "hold_wape", "hold_bias", "hold_cum", "hold_mase",
                    "naive_mase", "ma4_wape", "m4_hold_wape"])
        for s, v in res.items():
            h = v.get("holdout", {})
            w.writerow([s, scope[s]["pareto"], scope[s]["hero"], scope[s]["revenue"], v.get("class"), v["weeks"],
                        v["zero_share"], v.get("alpha"), v.get("beta"), r(v.get("ins_mape")), r(v.get("ins_wape")),
                        r(v.get("m4_ins_mape")), r(h.get("holt", {}).get("mape")), r(h.get("holt", {}).get("wape")),
                        r(h.get("holt", {}).get("bias")), r(h.get("holt", {}).get("cum")), r(h.get("holt", {}).get("mase"), 2),
                        r(h.get("naive", {}).get("mase"), 2), r(h.get("ma4", {}).get("wape")), r(v.get("m4_holdout_wape"))])
    print(json.dumps(S, indent=1))


if __name__ == "__main__":
    main()
