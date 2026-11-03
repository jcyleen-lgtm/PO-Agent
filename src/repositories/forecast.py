"""Forecast results: read the saved forecast back from forecast_results."""

import json

from sqlalchemy import text

from forecast import _holdout_summary



def load_forecast_from_db(engine, sku=None):
    """Rebuild the latest forecast from the forecast_results table (written by save_forecast_to_db).
    Reading it back takes one query instead of re-running the Holt's DES grid search.
    Pass sku= to read a single product only."""
    where = "WHERE f.sku = :sku" if sku else ""
    with engine.connect() as conn:
        rows = conn.execute(text(f"""
            SELECT f.sku, COALESCE(p.name, f.sku) AS product_name, f.alpha, f.beta, f.mape,
                   f.forecast_data, f.training_months, f.calculated_at
            FROM forecast_results f
            LEFT JOIN products p ON p.sku = f.sku
            {where}
            ORDER BY f.id
        """), {"sku": sku} if sku else {}).fetchall()
    if not rows:
        return None

    results, calc_times = [], []
    for sku, name, alpha, beta, mape, fd, training, calc_at in rows:
        fd = fd if isinstance(fd, dict) else json.loads(fd or "{}")
        results.append({
            "sku": sku,
            "product_name": name,
            "alpha": float(alpha) if alpha is not None else None,
            "beta": float(beta) if beta is not None else None,
            "mape": float(mape) if mape is not None else 999,
            "trend": fd.get("trend", 0),
            "trend_direction": fd.get("trend_direction", "stable"),
            "level": fd.get("level", 0),
            "avg_weekly_demand": fd.get("avg_weekly_demand", 0),
            "training_weeks": training,
            "actual_data": fd.get("actual", {"weeks": [], "quantities": []}),
            "fitted_data": fd.get("fitted", {"weeks": [], "quantities": []}),
            "forecast_data": fd.get("forecast", {"weeks": [], "quantities": []}),
            "holdout": fd.get("holdout"),
            "method": fd.get("method", "holt"), "method_reason": fd.get("method_reason"),
            "scope": fd.get("scope"),
        })
        if calc_at:
            calc_times.append(calc_at)

    holt = [r for r in results if r.get("method", "holt") == "holt"]
    mapes = [r["mape"] for r in holt if r["mape"] < 999]
    summary = {
        "total_skus_forecasted": len(results),
        "holt_skus": len(holt), "naive_skus": len(results) - len(holt),
        "avg_mape": round(sum(mapes) / len(mapes), 2) if mapes else 0,
        "good_forecast_count": len([m for m in mapes if m < 25]),
        "moderate_forecast_count": len([m for m in mapes if 25 <= m < 50]),
        "poor_forecast_count": len([m for m in mapes if m >= 50]),
        "holdout": _holdout_summary(holt),
        "forecast_periods": len(results[0]["forecast_data"].get("quantities", [])),
        "calculated_at": (lambda c: c.isoformat() if hasattr(c, "isoformat") else str(c))(max(calc_times)) if calc_times else None,
        "source": "database",
    }
    return {"summary": summary, "results": results}
