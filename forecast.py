"""
Holt's Double Exponential Smoothing (DES) Forecasting Engine — Weekly.
Per-SKU demand forecast with grid search for optimal α/β.
"""

import pandas as pd
from datetime import datetime


def load_hero_skus(engine) -> dict:
    """Company best-seller list (Hero SKU), uploaded on the Upload Data page.
    {sku: {"group", "status"}}; empty when the table does not exist yet."""
    from src.core.config import INCLUDE_HERO_SKUS
    from src.repositories import hero as hero_repo
    if not INCLUDE_HERO_SKUS:
        return {}
    try:
        with engine.connect() as conn:
            rows = hero_repo.import_skus(conn)
        return {r[0]: {"group": r[1], "status": r[2]} for r in rows}
    except Exception:
        return {}


def holts_des(series: list, alpha: float, beta: float, forecast_periods: int = 12) -> dict:
    """
    Run Holt's DES on a time series.

    Args:
        series: list of numeric values (chronological)
        alpha: smoothing factor for level (0-1)
        beta: smoothing factor for trend (0-1)
        forecast_periods: how many periods ahead to forecast

    Returns:
        dict with fitted values, forecasts, and error metrics
    """
    n = len(series)
    if n < 3:
        return None

    # Initialize
    level = series[0]
    trend = series[1] - series[0]

    fitted = [None] * n
    fitted[0] = level + trend

    # Fit
    for t in range(1, n):
        forecast_t = level + trend
        fitted[t] = forecast_t

        prev_level = level
        level = alpha * series[t] + (1 - alpha) * (level + trend)
        trend = beta * (level - prev_level) + (1 - beta) * trend

    # Forecast future periods (clamped)
    max_forecast = max(series) * 2.5 if series else 1
    forecasts = []
    for m in range(1, forecast_periods + 1):
        fc = level + m * trend
        fc = max(0, min(fc, max_forecast))
        forecasts.append(fc)

    # MAPE (skip first period)
    errors = []
    for t in range(1, n):
        if series[t] != 0 and fitted[t] is not None:
            errors.append(abs((series[t] - fitted[t]) / series[t]))

    mape = (sum(errors) / len(errors) * 100) if errors else 999

    # MAE of the one-step fitted values (skip first period; zero weeks count too). This is the
    # parameter-selection criterion: unlike MAPE it is not blown up by weeks with very small sales.
    abs_err = [abs(series[t] - fitted[t]) for t in range(1, n)]
    mae = sum(abs_err) / len(abs_err)

    return {
        "fitted": fitted,
        "forecasts": forecasts,
        "level": level,
        "trend": trend,
        "mape": round(mape, 4),
        "mae": mae,
    }


ALPHA_GRID = [a / 10 for a in range(1, 10)]   # 0.1 – 0.9
BETA_GRID = [0.1]                             # fixed: the sales show no lasting trend (see analysis/)


def grid_search_holts(series: list, forecast_periods: int = 12) -> dict:
    """
    Grid search for Holt's DES parameters.
    α: 0.1–0.9 (9 values), β fixed at 0.1; the pair with the lowest in-sample MAE wins
    (ties keep the smaller α). MAPE is still computed and reported, but no longer chooses α/β:
    a rolling-origin test over 6 periods x 8 weeks showed min-MAPE selection with β up to 0.9
    projects week-to-week noise as trend (pooled WMAPE 102.0% vs 86.1% for this rule).
    """
    best = None
    best_mae = float("inf")

    for alpha in ALPHA_GRID:
        for beta in BETA_GRID:
            result = holts_des(series, alpha, beta, forecast_periods)
            if result and result["mae"] < best_mae:
                best_mae = result["mae"]
                best = {"alpha": alpha, "beta": beta, **result}

    return best


HOLDOUT_WEEKS = 8
MIN_TRAIN_WEEKS = 12


def holdout_wmape(series: list, holdout: int = HOLDOUT_WEEKS, min_train: int = MIN_TRAIN_WEEKS) -> dict:
    """Out-of-sample accuracy without data leakage.
    1) Split chronologically: train = all weeks except the last `holdout`, test = the last `holdout`.
    2) Choose alpha/beta with the same grid search (grid_search_holts), on the TRAIN weeks only.
    3) Forecast `holdout` weeks ahead from the end of train (no refit on test actuals).
    4) WMAPE = sum|actual - forecast| / sum(actual) x 100 (defined when test weeks have any sales).
    The in-sample MAPE stays the thesis metric; this one is reported next to it, never instead of it."""
    n = len(series)
    if n < holdout + min_train:
        return {"wmape": None, "reason": "insufficient_history", "train_weeks": max(0, n - holdout),
                "required_weeks": holdout + min_train}
    train, test = series[:-holdout], series[-holdout:]
    best = grid_search_holts(train, holdout)
    if best is None:
        return {"wmape": None, "reason": "fit_failed"}
    preds = best["forecasts"][:holdout]
    total = sum(test)
    if total <= 0:
        return {"wmape": None, "reason": "no_sales_in_holdout", "train_weeks": len(train)}
    err = sum(abs(a - f) for a, f in zip(test, preds))
    return {"wmape": round(err / total * 100, 2), "alpha": best["alpha"], "beta": best["beta"],
            "train_weeks": len(train), "holdout_weeks": holdout,
            "holdout_actual": [round(x, 1) for x in test], "holdout_forecast": [round(x, 1) for x in preds]}


def run_forecast(engine, forecast_periods: int = 12, min_weeks: int = 4) -> dict:
    """
    Run Holt's DES forecast for all SKUs — WEEKLY aggregation.

    Args:
        engine: SQLAlchemy engine
        forecast_periods: weeks ahead to forecast (default 12)
        min_weeks: minimum weeks of data required

    Returns:
        dict with summary stats and per-SKU results
    """

    from src.repositories import forecast as forecast_repo
    # Weekly sales per SKU — research scope is IMPORT products only (food is excluded),
    # so the Pareto 80/20 below is computed on import revenue.
    with engine.connect() as conn:
        df = forecast_repo.weekly_import_sales_df(conn)

    if df.empty:
        return {"error": "No sales data found", "results": []}

    df["week_start"] = pd.to_datetime(df["week_start"])

    # ── Best-seller scope = Pareto 80/20 ∪ company Hero SKU list ─────
    # Pareto: SKUs that together make up the top 80% of import revenue (rank by revenue).
    sku_revenue = df.groupby("sku")["total_amount"].sum().sort_values(ascending=False)
    total_revenue = sku_revenue.sum()
    cumulative = sku_revenue.cumsum()
    pareto_skus = set(cumulative[cumulative <= total_revenue * 0.80].index)
    # Always include the SKU that crosses the 80% threshold
    if len(pareto_skus) < len(sku_revenue):
        boundary_sku = cumulative[cumulative > total_revenue * 0.80].index[0]
        pareto_skus.add(boundary_sku)
    rank = {s: i + 1 for i, s in enumerate(sku_revenue.index)}
    share = (sku_revenue / total_revenue * 100) if total_revenue else sku_revenue * 0
    cum_share = (cumulative / total_revenue * 100) if total_revenue else cumulative * 0

    # Company list: always analysed, even when the SKU is outside the Pareto set.
    hero = load_hero_skus(engine)
    scope_skus = pareto_skus | set(hero)

    def scope_info(sku):
        h = hero.get(sku) or {}
        return {"pareto": sku in pareto_skus, "hero": sku in hero,
                "source": "both" if (sku in pareto_skus and sku in hero) else ("hero" if sku in hero else "pareto"),
                "rank": rank.get(sku), "revenue": round(float(sku_revenue.get(sku, 0))),
                "share_pct": round(float(share.get(sku, 0)), 2), "cum_share_pct": round(float(cum_share.get(sku, 0)), 1),
                "group": h.get("group"), "status": h.get("status")}

    pareto_excluded = len(sku_revenue) - len(pareto_skus)
    df = df[df["sku"].isin(scope_skus)]

    # Product names
    with engine.connect() as conn:
        products = forecast_repo.product_names_df(conn)
    name_map = dict(zip(products["sku"], products["name"]))

    # All weeks in range
    all_weeks = pd.date_range(
        start=df["week_start"].min(),
        end=df["week_start"].max(),
        freq="W-MON",
    )
    # If data doesn't align to Monday, use the actual min as start
    if len(all_weeks) == 0:
        all_weeks = pd.date_range(
            start=df["week_start"].min(),
            end=df["week_start"].max(),
            freq="7D",
        )

    results = []
    skipped = 0
    skip_reasons = {"too_few_weeks": 0, "no_sales": 0, "inactive": 0, "forecast_fail": 0}
    naive_reasons = {}

    # Recency threshold: skip SKUs with no sales in last 8 weeks
    max_week = df["week_start"].max()
    recency_cutoff = max_week - pd.DateOffset(weeks=8)
    horizon_weeks = pd.date_range(start=max_week + pd.DateOffset(weeks=1), periods=forecast_periods,
                                  freq="W-MON").strftime("%Y-%m-%d").tolist()

    def naive(sku, sku_data, reason):
        """Hero SKU that Holt's DES cannot model (too little / no recent history): flat forecast at the
        average of its last 4 weeks, clearly marked, so the SKU is still on every page."""
        if sku_data is not None and not sku_data.empty:
            wk = pd.date_range(start=sku_data["week_start"].min(), end=max_week, freq="W-MON")
            sw = pd.DataFrame({"week_start": wk}).merge(
                sku_data[["week_start", "total_qty", "total_amount"]], on="week_start", how="left").fillna(0)
            series, amounts = sw["total_qty"].tolist(), sw["total_amount"].tolist()
            weeks = sw["week_start"].dt.strftime("%Y-%m-%d").tolist()
        else:
            series, amounts, weeks = [], [], []
        last4 = series[-4:]
        avg = sum(last4) / len(last4) if last4 else 0.0
        naive_reasons[reason] = naive_reasons.get(reason, 0) + 1
        return {
            "sku": sku, "product_name": name_map.get(sku, sku),
            "alpha": None, "beta": None, "mape": 999, "trend": 0, "trend_direction": "stable",
            "level": round(avg, 2), "avg_weekly_demand": round(avg, 1), "training_weeks": len(series),
            "actual_data": {"weeks": weeks, "quantities": [round(x, 1) for x in series],
                            "amounts": [round(x, 0) for x in amounts]},
            "fitted_data": {"weeks": [], "quantities": []},
            "forecast_data": {"weeks": horizon_weeks, "quantities": [round(avg, 1)] * forecast_periods},
            "holdout": {"wmape": None, "reason": "naive_" + reason},
            "method": "naive", "method_reason": reason, "scope": scope_info(sku),
        }

    # Hero SKUs with no import sales at all still get a row (forecast 0)
    for sku in sorted(set(hero) - set(df["sku"].unique())):
        results.append(naive(sku, None, "no_sales"))

    for sku in df["sku"].unique():
        sku_data = df[df["sku"] == sku].copy()
        is_hero = sku in hero

        # Skip SKU "0" (unmatched products)
        if str(sku).strip() == "0":
            skipped += 1
            skip_reasons["no_sales"] += 1
            continue

        # Recency check: must have at least 1 sale in last 8 weeks
        recent_sales = sku_data[sku_data["week_start"] >= recency_cutoff]
        if recent_sales.empty:
            if is_hero:
                results.append(naive(sku, sku_data, "inactive"))
                continue
            skipped += 1
            skip_reasons["inactive"] += 1
            continue

        # Activity check: must have sales in at least 3 of last 12 weeks
        last_12w_cutoff = max_week - pd.DateOffset(weeks=12)
        last_12w_sales = sku_data[sku_data["week_start"] >= last_12w_cutoff]
        if len(last_12w_sales) < 3:
            if is_hero:
                results.append(naive(sku, sku_data, "inactive"))
                continue
            skipped += 1
            skip_reasons["inactive"] += 1
            continue

        # Zero-fill ONLY from first sale date of this SKU, not global start
        sku_first_sale = sku_data["week_start"].min()
        sku_weeks = all_weeks[all_weeks >= sku_first_sale]

        sku_weekly = pd.DataFrame({"week_start": sku_weeks})
        sku_weekly = sku_weekly.merge(
            sku_data[["week_start", "total_qty", "total_amount"]],
            on="week_start",
            how="left",
        ).fillna(0)

        series = sku_weekly["total_qty"].tolist()
        amounts = sku_weekly["total_amount"].tolist()
        weeks = sku_weekly["week_start"].dt.strftime("%Y-%m-%d").tolist()

        if len(series) < min_weeks:
            if is_hero:
                results.append(naive(sku, sku_data, "too_few_weeks"))
                continue
            skipped += 1
            skip_reasons["too_few_weeks"] += 1
            continue

        if sum(series) == 0:
            if is_hero:
                results.append(naive(sku, sku_data, "no_sales"))
                continue
            skipped += 1
            skip_reasons["no_sales"] += 1
            continue

        # Grid search
        best = grid_search_holts(series, forecast_periods)
        if best is None:
            if is_hero:
                results.append(naive(sku, sku_data, "forecast_fail"))
                continue
            skipped += 1
            skip_reasons["forecast_fail"] += 1
            continue

        # Forecast week labels
        last_week = sku_weekly["week_start"].max()
        forecast_weeks = pd.date_range(
            start=last_week + pd.DateOffset(weeks=1),
            periods=forecast_periods,
            freq="W-MON",
        ).strftime("%Y-%m-%d").tolist()

        # Trend direction
        trend_dir = (
            "up" if best["trend"] > 1 else ("down" if best["trend"] < -1 else "stable")
        )

        # Avg weekly demand (recent 4 weeks)
        recent_4w = series[-4:] if len(series) >= 4 else series
        avg_weekly = sum(recent_4w) / len(recent_4w)

        result = {
            "sku": sku,
            "product_name": name_map.get(sku, sku),
            "alpha": best["alpha"],
            "beta": best["beta"],
            "mape": best["mape"],
            "trend": round(best["trend"], 2),
            "trend_direction": trend_dir,
            "level": round(best["level"], 2),
            "avg_weekly_demand": round(avg_weekly, 1),
            "training_weeks": len(series),
            "actual_data": {
                "weeks": weeks,
                "quantities": [round(x, 1) for x in series],
                "amounts": [round(x, 0) for x in amounts],
            },
            "fitted_data": {
                "weeks": weeks,
                "quantities": [
                    round(x, 1) if x is not None else None for x in best["fitted"]
                ],
            },
            "forecast_data": {
                "weeks": forecast_weeks,
                "quantities": [max(0, round(x, 1)) for x in best["forecasts"]],
            },
            "holdout": holdout_wmape(series),
            "method": "holt", "method_reason": None, "scope": scope_info(sku),
        }
        results.append(result)

    # Sort by total sales volume desc
    results.sort(key=lambda x: sum(x["actual_data"]["quantities"]), reverse=True)

    # Summary (MAPE / WMAPE are Holt's DES metrics: the naive fallback rows are left out)
    holt = [r for r in results if r.get("method", "holt") == "holt"]
    mapes = [r["mape"] for r in holt if r["mape"] < 999]

    summary = {
        "total_skus_forecasted": len(results),
        "total_skus_skipped": skipped,
        "pareto_skus_included": len(pareto_skus),
        "pareto_skus_excluded": pareto_excluded,
        "hero_skus": len(hero), "hero_outside_pareto": len(set(hero) - pareto_skus),
        "holt_skus": len(holt), "naive_skus": len(results) - len(holt), "naive_reasons": naive_reasons,
        "skip_reasons": skip_reasons,
        "avg_mape": round(sum(mapes) / len(mapes), 2) if mapes else 0,
        "good_forecast_count": len([m for m in mapes if m < 25]),
        "moderate_forecast_count": len([m for m in mapes if 25 <= m < 50]),
        "poor_forecast_count": len([m for m in mapes if m >= 50]),
        "holdout": _holdout_summary(holt),
        "forecast_periods": forecast_periods,
        "data_range": {
            "start": df["week_start"].min().strftime("%Y-%m-%d"),
            "end": df["week_start"].max().strftime("%Y-%m-%d"),
        },
        "calculated_at": datetime.now().isoformat(),
    }

    return {"summary": summary, "results": results}


def _holdout_summary(results):
    """Portfolio WMAPE over all evaluable SKUs = total abs error / total actual (volume-weighted)."""
    err = act = 0.0
    n = 0
    for r in results:
        h = r.get("holdout") or {}
        if h.get("wmape") is None:
            continue
        n += 1
        err += sum(abs(a - f) for a, f in zip(h["holdout_actual"], h["holdout_forecast"]))
        act += sum(h["holdout_actual"])
    return {"wmape": round(err / act * 100, 2) if act else None, "evaluated_skus": n,
            "not_evaluated": len(results) - n, "holdout_weeks": HOLDOUT_WEEKS}


def save_forecast_to_db(engine, forecast_data: dict):
    """Save forecast results to database (the whole table is replaced)."""
    import json
    from src.repositories import forecast as forecast_repo

    results = forecast_data.get("results", [])
    if not results:
        return 0

    forecast_repo.replace_all(engine, [
        {
            "sku": r["sku"],
            "alpha": r["alpha"],
            "beta": r["beta"],
            "mape": r["mape"],
            "forecast_data": json.dumps(
                {
                    "trend": r["trend"],
                    "trend_direction": r["trend_direction"],
                    "level": r["level"],
                    "avg_weekly_demand": r["avg_weekly_demand"],
                    "actual": r["actual_data"],
                    "fitted": r["fitted_data"],
                    "forecast": r["forecast_data"],
                    "holdout": r.get("holdout"),
                    "method": r.get("method", "holt"),
                    "method_reason": r.get("method_reason"),
                    "scope": r.get("scope"),
                }
            ),
            "training_months": r["training_weeks"],
        }
        for r in results
    ])
    return len(results)