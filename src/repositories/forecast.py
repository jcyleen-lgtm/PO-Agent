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


def light_rows(conn):
    """One row per forecast SKU with only the fields the dashboard tables need (JSON paths, no history)."""
    return conn.execute(text("""
            SELECT f.sku, COALESCE(p.name, f.sku), f.mape,
                   f.forecast_data::jsonb -> 'forecast',
                   f.forecast_data::jsonb ->> 'trend',
                   f.forecast_data::jsonb ->> 'trend_direction',
                   f.forecast_data::jsonb ->> 'avg_weekly_demand',
                   f.forecast_data::jsonb -> 'actual' -> 'quantities',
                   f.forecast_data::jsonb -> 'holdout',
                   p.product_type,
                   f.forecast_data::jsonb ->> 'method',
                   f.forecast_data::jsonb ->> 'method_reason',
                   f.forecast_data::jsonb -> 'scope'
            FROM forecast_results f LEFT JOIN products p ON p.sku = f.sku
            ORDER BY f.id
        """)).fetchall()


def max_calculated_at(conn):
    return conn.execute(text("SELECT MAX(calculated_at) FROM forecast_results")).scalar()


def count_rows(conn):
    return conn.execute(text("SELECT COUNT(*) FROM forecast_results")).fetchone()[0]


def plan_rows(conn, type_filter):
    """Forecast rows with model parameters and holdout for the Demand Plan page."""
    return conn.execute(text(f"""
            SELECT f.sku, COALESCE(p.name, f.sku), f.alpha, f.beta, f.mape, f.training_months,
                   f.forecast_data::jsonb -> 'forecast',
                   f.forecast_data::jsonb -> 'actual',
                   f.forecast_data::jsonb -> 'holdout',
                   f.forecast_data::jsonb ->> 'level', f.forecast_data::jsonb ->> 'trend',
                   f.calculated_at
            FROM forecast_results f LEFT JOIN products p ON p.sku = f.sku
            WHERE 1=1 {type_filter}
            ORDER BY f.id
        """)).fetchall()


def weekly_import_sales_df(conn):
    """DataFrame: sku, week_start, total_qty, total_amount, transaction_count for import products."""
    import pandas as pd
    return pd.read_sql(text("""
        SELECT s.sku,
               DATE_TRUNC('week', s.invoice_date)::date AS week_start,
               SUM(s.quantity) AS total_qty,
               SUM(s.amount) AS total_amount,
               COUNT(*) AS transaction_count
        FROM sales s
        JOIN products p ON p.sku = s.sku
        WHERE s.invoice_date IS NOT NULL
          AND COALESCE(p.product_type, 'import') = 'import'
        GROUP BY s.sku, DATE_TRUNC('week', s.invoice_date)::date
        ORDER BY s.sku, week_start
    """), conn)


def product_names_df(conn):
    import pandas as pd
    return pd.read_sql(text("SELECT sku, name FROM products"), conn)


def replace_all(engine, rows):
    """Truncate forecast_results and insert the new rows in one executemany (one transaction)."""
    with engine.begin() as conn:
        conn.execute(text("TRUNCATE TABLE forecast_results RESTART IDENTITY"))
        conn.execute(
            text(
                """INSERT INTO forecast_results (sku, alpha, beta, mape, forecast_data, training_months)
                   VALUES (:sku, :alpha, :beta, :mape, :forecast_data, :training_months)"""
            ),
            rows,
        )
