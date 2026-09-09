"""
Holt's Double Exponential Smoothing (DES) Forecasting Engine.
Per-SKU demand forecast with grid search for optimal α/β.
"""

import pandas as pd
import numpy as np
from datetime import datetime


def holts_des(series: list, alpha: float, beta: float, forecast_periods: int = 4) -> dict:
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
    fitted[0] = level + trend  # one-step-ahead forecast for t=0

    # Fit
    for t in range(1, n):
        forecast_t = level + trend  # one-step-ahead forecast
        fitted[t] = forecast_t

        prev_level = level
        level = alpha * series[t] + (1 - alpha) * (level + trend)
        trend = beta * (level - prev_level) + (1 - beta) * trend

    # Forecast future periods
    forecasts = []
    for m in range(1, forecast_periods + 1):
        forecasts.append(level + m * trend)

    # Calculate MAPE (skip first period)
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


def grid_search_holts(series: list, forecast_periods: int = 4) -> dict:
    """
    Grid search optimal α and β for Holt's DES.
    Tests 81 combinations (0.1 to 0.9 step 0.1).
    
    Returns best result with optimal parameters.
    """
    best = None
    best_mape = float('inf')

    for a in range(1, 10):  # 0.1 to 0.9
        for b in range(1, 10):
            alpha = a / 10
            beta = b / 10
            result = holts_des(series, alpha, beta, forecast_periods)
            if result and result["mape"] < best_mape:
                best_mape = result["mape"]
                best = {
                    "alpha": alpha,
                    "beta": beta,
                    **result,
                }

    return best


def run_forecast(engine, forecast_periods: int = 4, min_months: int = 4) -> dict:
    """
    Run Holt's DES forecast for all SKUs in the database.
    
    Args:
        engine: SQLAlchemy engine
        forecast_periods: months ahead to forecast
        min_months: minimum months of data required
    
    Returns:
        dict with summary stats and per-SKU results
    """
    from sqlalchemy import text

    # Get monthly sales per SKU
    query = """
        SELECT sku, 
               DATE_TRUNC('month', invoice_date) as month,
               SUM(quantity) as total_qty,
               SUM(amount) as total_amount,
               COUNT(*) as transaction_count
        FROM sales 
        WHERE invoice_date IS NOT NULL
        GROUP BY sku, DATE_TRUNC('month', invoice_date)
        ORDER BY sku, month
    """

    with engine.connect() as conn:
        df = pd.read_sql(text(query), conn)

    if df.empty:
        return {"error": "No sales data found", "results": []}

    df["month"] = pd.to_datetime(df["month"])

    # Get product names
    with engine.connect() as conn:
        products = pd.read_sql(text("SELECT sku, name FROM products"), conn)
    name_map = dict(zip(products["sku"], products["name"]))

    # Get all months in range for filling gaps
    all_months = pd.date_range(
        start=df["month"].min(),
        end=df["month"].max(),
        freq="MS"
    )

    results = []
    skipped = 0

    for sku in df["sku"].unique():
        sku_data = df[df["sku"] == sku].copy()

        # Fill missing months with 0
        sku_monthly = pd.DataFrame({"month": all_months})
        sku_monthly = sku_monthly.merge(
            sku_data[["month", "total_qty", "total_amount"]],
            on="month", how="left"
        ).fillna(0)

        series = sku_monthly["total_qty"].tolist()
        amounts = sku_monthly["total_amount"].tolist()
        months = sku_monthly["month"].dt.strftime("%Y-%m").tolist()

        # Skip SKUs with insufficient data
        if len(series) < min_months:
            skipped += 1
            continue

        # Skip SKUs with all zeros
        if sum(series) == 0:
            skipped += 1
            continue

        # Run grid search
        best = grid_search_holts(series, forecast_periods)

        if best is None:
            skipped += 1
            continue

        # Build forecast months
        last_month = sku_monthly["month"].max()
        forecast_months = pd.date_range(
            start=last_month + pd.DateOffset(months=1),
            periods=forecast_periods,
            freq="MS"
        ).strftime("%Y-%m").tolist()

        # Determine trend direction
        trend_dir = "up" if best["trend"] > 0.5 else ("down" if best["trend"] < -0.5 else "stable")

        # Calculate avg weekly demand (for restock)
        recent_3m = series[-3:] if len(series) >= 3 else series
        avg_monthly = sum(recent_3m) / len(recent_3m)
        avg_weekly = avg_monthly / 4

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
            "training_months": len(series),
            "actual_data": {
                "months": months,
                "quantities": [round(x, 1) for x in series],
                "amounts": [round(x, 0) for x in amounts],
            },
            "fitted_data": {
                "months": months,
                "quantities": [round(x, 1) if x is not None else None for x in best["fitted"]],
            },
            "forecast_data": {
                "months": forecast_months,
                "quantities": [max(0, round(x, 1)) for x in best["forecasts"]],
            },
        }
        results.append(result)

    # Sort by total sales volume (desc)
    results.sort(key=lambda x: sum(x["actual_data"]["quantities"]), reverse=True)

    # Summary stats
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
            "start": df["month"].min().strftime("%Y-%m"),
            "end": df["month"].max().strftime("%Y-%m"),
        },
        "calculated_at": datetime.now().isoformat(),
    }

    return {"summary": summary, "results": results}


def save_forecast_to_db(engine, forecast_data: dict):
    """Save forecast results to database."""
    from sqlalchemy import text
    import json

    results = forecast_data.get("results", [])
    if not results:
        return 0

    with engine.begin() as conn:
        # Clear old results
        conn.execute(text("TRUNCATE TABLE forecast_results RESTART IDENTITY"))

        for r in results:
            conn.execute(text("""
                INSERT INTO forecast_results (sku, alpha, beta, mape, forecast_data, training_months)
                VALUES (:sku, :alpha, :beta, :mape, :forecast_data, :training_months)
            """), {
                "sku": r["sku"],
                "alpha": r["alpha"],
                "beta": r["beta"],
                "mape": r["mape"],
                "forecast_data": json.dumps({
                    "trend": r["trend"],
                    "trend_direction": r["trend_direction"],
                    "level": r["level"],
                    "avg_weekly_demand": r["avg_weekly_demand"],
                    "actual": r["actual_data"],
                    "fitted": r["fitted_data"],
                    "forecast": r["forecast_data"],
                }),
                "training_months": r["training_months"],
            })

    return len(results)