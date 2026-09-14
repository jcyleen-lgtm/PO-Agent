"""
Holt's Double Exponential Smoothing (DES) Forecasting Engine — Weekly.
Per-SKU demand forecast with grid search for optimal α/β.
Replenishment calculation with lead-time demand + safety stock.
"""

import pandas as pd
import numpy as np
from datetime import datetime


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

    return {
        "fitted": fitted,
        "forecasts": forecasts,
        "level": level,
        "trend": trend,
        "mape": round(mape, 4),
    }


def grid_search_holts(series: list, forecast_periods: int = 12) -> dict:
    """
    Grid search optimal α and β for Holt's DES.
    α: 0.1–0.9, β: 0.1–0.3 (cap beta to prevent trend explosion on weekly data).
    """
    best = None
    best_mape = float("inf")

    for a in range(1, 10):
        for b in range(1, 4):
            alpha = a / 10
            beta = b / 10
            result = holts_des(series, alpha, beta, forecast_periods)
            if result and result["mape"] < best_mape:
                best_mape = result["mape"]
                best = {"alpha": alpha, "beta": beta, **result}

    return best


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
    from sqlalchemy import text

    # Weekly sales per SKU
    query = """
        SELECT sku,
               DATE_TRUNC('week', invoice_date)::date AS week_start,
               SUM(quantity) AS total_qty,
               SUM(amount) AS total_amount,
               COUNT(*) AS transaction_count
        FROM sales
        WHERE invoice_date IS NOT NULL
        GROUP BY sku, DATE_TRUNC('week', invoice_date)::date
        ORDER BY sku, week_start
    """

    with engine.connect() as conn:
        df = pd.read_sql(text(query), conn)

    if df.empty:
        return {"error": "No sales data found", "results": []}

    df["week_start"] = pd.to_datetime(df["week_start"])

    # Product names
    with engine.connect() as conn:
        products = pd.read_sql(text("SELECT sku, name FROM products"), conn)
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

    for sku in df["sku"].unique():
        sku_data = df[df["sku"] == sku].copy()

        # Fill missing weeks with 0
        sku_weekly = pd.DataFrame({"week_start": all_weeks})
        sku_weekly = sku_weekly.merge(
            sku_data[["week_start", "total_qty", "total_amount"]],
            on="week_start",
            how="left",
        ).fillna(0)

        series = sku_weekly["total_qty"].tolist()
        amounts = sku_weekly["total_amount"].tolist()
        weeks = sku_weekly["week_start"].dt.strftime("%Y-%m-%d").tolist()

        if len(series) < min_weeks:
            skipped += 1
            continue

        if sum(series) == 0:
            skipped += 1
            continue

        # Grid search
        best = grid_search_holts(series, forecast_periods)
        if best is None:
            skipped += 1
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
        }
        results.append(result)

    # Sort by total sales volume desc
    results.sort(key=lambda x: sum(x["actual_data"]["quantities"]), reverse=True)

    # Summary
    mapes = [r["mape"] for r in results if r["mape"] < 999]

    summary = {
        "total_skus_forecasted": len(results),
        "total_skus_skipped": skipped,
        "avg_mape": round(sum(mapes) / len(mapes), 2) if mapes else 0,
        "good_forecast_count": len([m for m in mapes if m < 25]),
        "moderate_forecast_count": len([m for m in mapes if 25 <= m < 50]),
        "poor_forecast_count": len([m for m in mapes if m >= 50]),
        "forecast_periods": forecast_periods,
        "data_range": {
            "start": df["week_start"].min().strftime("%Y-%m-%d"),
            "end": df["week_start"].max().strftime("%Y-%m-%d"),
        },
        "calculated_at": datetime.now().isoformat(),
    }

    return {"summary": summary, "results": results}


def calculate_replenishment(engine, forecast_data: dict) -> dict:
    """
    Calculate replenishment recommendations using Holt's DES forecast output.

    Flow: Forecast → Lead Time Demand → Safety Stock → Available → Recommended PO
    Lead time: 6 weeks (optimistic) / 8 weeks (conservative)
    Safety stock: 10% of lead time demand
    """
    from sqlalchemy import text

    results_list = forecast_data.get("results", [])
    if not results_list:
        return {"results": [], "summary": {}}

    # Get current stock
    with engine.connect() as conn:
        try:
            stk_rows = conn.execute(
                text(
                    """SELECT sku, quantity FROM stock_snapshots
                       WHERE snapshot_date = (SELECT MAX(snapshot_date) FROM stock_snapshots)"""
                )
            ).fetchall()
            stock_map = {r[0]: int(r[1]) for r in stk_rows}
        except Exception:
            stock_map = {}

        try:
            po_rows = conn.execute(
                text(
                    """SELECT sku, SUM(qty_ordered - qty_received) FROM purchase_orders
                       WHERE status IN ('pending','partial') GROUP BY sku"""
                )
            ).fetchall()
            po_map = {r[0]: int(r[1]) for r in po_rows}
        except Exception:
            po_map = {}

        try:
            snap = conn.execute(
                text("SELECT MAX(snapshot_date) FROM stock_snapshots")
            ).fetchone()
            stock_date = str(snap[0]) if snap and snap[0] else None
        except Exception:
            stock_date = None

    replenishment = []

    for r in results_list:
        sku = r["sku"]
        fc_qty = r["forecast_data"]["quantities"]  # 12-week forecast

        # Lead time demand
        lt_6w = sum(fc_qty[:6]) if len(fc_qty) >= 6 else sum(fc_qty)
        lt_8w = sum(fc_qty[:8]) if len(fc_qty) >= 8 else sum(fc_qty)

        # Safety stock (10%)
        ss_6w = round(lt_6w * 0.10, 1)
        ss_8w = round(lt_8w * 0.10, 1)

        # Available stock
        current_stock = stock_map.get(sku, 0)
        ongoing_po = po_map.get(sku, 0)
        available = current_stock + ongoing_po

        # Recommended PO (conservative = 8w lead time)
        req_opt = lt_6w + ss_6w
        req_con = lt_8w + ss_8w
        reco_opt = max(0, round(req_opt - available))
        reco_con = max(0, round(req_con - available))

        # Forecast avg weekly (from forecast, not actual)
        fc_avg_weekly = round(sum(fc_qty[:8]) / min(len(fc_qty), 8), 1) if fc_qty else 0

        # Projected stockout week
        stockout_week = None
        cumulative = 0
        for w_idx, wf in enumerate(fc_qty):
            cumulative += wf
            if cumulative >= available:
                stockout_week = w_idx + 1
                break

        # Status
        if stockout_week is not None and stockout_week <= 6:
            status = "critical"
        elif reco_con > 0:
            status = "understock"
        elif available > req_con * 2:
            status = "overstock"
        else:
            status = "healthy"

        replenishment.append(
            {
                "sku": sku,
                "product_name": r["product_name"],
                "current_stock": current_stock,
                "ongoing_po": ongoing_po,
                "available": available,
                "fc_avg_weekly": fc_avg_weekly,
                "mape": r["mape"],
                "trend_direction": r["trend_direction"],
                # Optimistic (6w lead time)
                "lt_demand_6w": round(lt_6w, 1),
                "safety_stock_6w": ss_6w,
                "reco_po_6w": reco_opt,
                # Conservative (8w lead time)
                "lt_demand_8w": round(lt_8w, 1),
                "safety_stock_8w": ss_8w,
                "reco_po_8w": reco_con,
                # Projected
                "stockout_week": stockout_week,
                "status": status,
            }
        )

    # Sort: critical first, then understock, then by stockout week
    status_order = {"critical": 0, "understock": 1, "healthy": 2, "overstock": 3}
    replenishment.sort(
        key=lambda x: (
            status_order.get(x["status"], 9),
            x["stockout_week"] if x["stockout_week"] else 999,
        )
    )

    # Summary
    summary = {
        "total": len(replenishment),
        "critical": len([r for r in replenishment if r["status"] == "critical"]),
        "understock": len([r for r in replenishment if r["status"] == "understock"]),
        "healthy": len([r for r in replenishment if r["status"] == "healthy"]),
        "overstock": len([r for r in replenishment if r["status"] == "overstock"]),
        "stock_date": stock_date,
        "has_stock": len(stock_map) > 0,
    }

    return {"results": replenishment, "summary": summary}


def save_forecast_to_db(engine, forecast_data: dict):
    """Save forecast results to database."""
    from sqlalchemy import text
    import json

    results = forecast_data.get("results", [])
    if not results:
        return 0

    with engine.begin() as conn:
        conn.execute(text("TRUNCATE TABLE forecast_results RESTART IDENTITY"))

        for r in results:
            conn.execute(
                text(
                    """INSERT INTO forecast_results (sku, alpha, beta, mape, forecast_data, training_months)
                       VALUES (:sku, :alpha, :beta, :mape, :forecast_data, :training_months)"""
                ),
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
                        }
                    ),
                    "training_months": r["training_weeks"],
                },
            )

    return len(results)