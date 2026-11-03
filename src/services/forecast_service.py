"""Forecast orchestration: run + save, cached reads (full and light), headline numbers, forecast endpoints."""

import json
import traceback

from fastapi import HTTPException
from sqlalchemy import text

from forecast import run_forecast, save_forecast_to_db
from src.core.database import get_engine
from src.core.state import forecast_cache
from src.repositories.forecast import load_forecast_from_db
import replenishment as rp



def _run_auto_forecast(engine):
    """Internal: run forecast + save to DB + cache. Called after ETL and on-demand."""
    try:
        result = run_forecast(engine, forecast_periods=12)
        if "error" not in result:
            saved = save_forecast_to_db(engine, result)
            forecast_cache["latest"] = result
            forecast_cache.pop("light", None); forecast_cache.pop("light_calc_at", None)
            print(f"[auto-forecast] {result['summary']['total_skus_forecasted']} SKUs forecasted, saved={saved}")
        else:
            print(f"[auto-forecast] skipped: {result['error']}")
    except Exception as e:
        print(f"[auto-forecast] error: {e}")
        traceback.print_exc()


def get_forecast_light(engine):
    """Only the forecast fields the dashboard tables need (no full actual/fitted history).
    ~10x smaller than the full forecast, so it is cheap to load on a fresh Vercel instance.
    Also carries the relative trend and the holdout WMAPE so every page reads the same values."""
    def slim(r, actual8, holdout, ptype, method="holt", method_reason=None, scope=None):
        q = r["forecast_data"].get("quantities", [])
        tp, tc = rp.relative_trend(actual8, q)
        h = holdout or {}
        ha, hf = h.get("holdout_actual") or [], h.get("holdout_forecast") or []
        return {
            # same rounding as the "Forecast/Wk" column (avg of next 8 forecast weeks)
            "fc_avg_8w": round(sum(q[:8]) / min(len(q), 8), 1) if q else 0,
            "sku": r["sku"], "product_name": r.get("product_name", r["sku"]),
            "product_type": ptype or "import",
            "mape": r["mape"], "trend": r.get("trend", 0),
            "trend_direction": r.get("trend_direction", "stable"),   # legacy (beta-based), kept for comparison
            "trend_pct": tp, "trend_class": tc,
            "actual_8w": [round(float(x), 1) for x in (actual8 or [])[-8:]],
            "holdout_wmape": h.get("wmape"), "holdout_reason": h.get("reason") if h else "not_computed",
            "holdout_abs_err": round(sum(abs(a - f) for a, f in zip(ha, hf)), 1) if h.get("wmape") is not None else None,
            "holdout_actual_sum": round(sum(ha), 1) if h.get("wmape") is not None else None,
            "avg_weekly_demand": r.get("avg_weekly_demand", 0),
            "forecast_data": {"weeks": r["forecast_data"].get("weeks", []),
                              "quantities": q},
            "method": method or "holt", "method_reason": method_reason,
            "scope": scope or {"source": "pareto", "pareto": True, "hero": False},
        }
    if "light" in forecast_cache:
        return forecast_cache["light"]
    with engine.connect() as conn:
        rows = conn.execute(text("""
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
    light = []
    for sku, name, mape, fc, trend, tdir, avg, actual, holdout, ptype, method, mreason, scope in rows:
        scope = scope if isinstance(scope, dict) or scope is None else json.loads(scope)
        fc = fc if isinstance(fc, dict) else json.loads(fc or "{}")
        actual = actual if isinstance(actual, list) else json.loads(actual or "[]")
        holdout = holdout if isinstance(holdout, dict) or holdout is None else json.loads(holdout)
        light.append(slim({
            "sku": sku, "product_name": name,
            "mape": float(mape) if mape is not None else 999,
            "trend": float(trend) if trend is not None else 0,
            "trend_direction": tdir or "stable",
            "avg_weekly_demand": float(avg) if avg is not None else 0,
            "forecast_data": fc or {"weeks": [], "quantities": []},
        }, actual[-8:], holdout, ptype, method, mreason, scope))
    if light:
        forecast_cache["light"] = light
    return light


def forecast_summary_fast(engine, product_type="all"):
    """Headline forecast numbers for the Overview, from the light forecast (one cached query)."""
    light = get_forecast_light(engine)
    if product_type in ("import", "food"):
        light = [r for r in light if r["product_type"] == product_type]
    if not light:
        return None
    holt = [r for r in light if r.get("method", "holt") == "holt"]
    mapes = [r["mape"] for r in holt if r["mape"] < 999]
    ev = [r for r in holt if r["holdout_wmape"] is not None]
    act = sum(r["holdout_actual_sum"] for r in ev)
    calc = forecast_cache.get("light_calc_at")
    if calc is None:
        try:
            with engine.connect() as conn:
                calc = conn.execute(text("SELECT MAX(calculated_at) FROM forecast_results")).scalar()
            calc = calc.isoformat() if calc else None
            forecast_cache["light_calc_at"] = calc
        except Exception:
            calc = None
    return {
        "total_forecasted": len(light),
        "avg_mape": round(sum(mapes) / len(mapes), 2) if mapes else 0,
        "mape_basis": "in_sample",
        "holdout_wmape": round(sum(r["holdout_abs_err"] for r in ev) / act * 100, 2) if act else None,
        "holdout_evaluated": len(ev), "holdout_not_evaluated": len(holt) - len(ev),
        "holt_skus": len(holt), "naive_skus": len(light) - len(holt),
        "hero_skus": sum(1 for r in light if (r.get("scope") or {}).get("hero")),
        "forecast_demand_8w": round(sum(sum(r["forecast_data"]["quantities"][:8]) for r in light)),
        "trending_up": len([r for r in light if r["trend_class"] == "increasing"]),
        "trending_down": len([r for r in light if r["trend_class"] == "decreasing"]),
        "trend_basis": "relative_8w_10pct",
        "calculated_at": calc,
    }


def get_latest_forecast(engine, compute_if_missing=True):
    """Forecast lookup order: memory cache → forecast_results table → full recompute (slow).
    On Vercel every new instance starts with an empty cache, so the table read avoids
    re-running the grid search on almost every page load."""
    if "latest" in forecast_cache:
        return forecast_cache["latest"]
    try:
        data = load_forecast_from_db(engine)
    except Exception as e:
        print(f"[forecast] could not read saved results: {e}")
        data = None
    if data:
        forecast_cache["latest"] = data
        return data
    if not compute_if_missing:
        return None
    data = run_forecast(engine, forecast_periods=12)
    if "error" not in data:
        try:
            save_forecast_to_db(engine, data)
        except Exception as e:
            print(f"[forecast] could not save results: {e}")
        forecast_cache["latest"] = data
    return data


async def forecast_run():
    """On-demand forecast refresh. Also called automatically after ETL."""
    engine = get_engine()
    result = run_forecast(engine, forecast_periods=12)
    if "error" in result:
        raise HTTPException(400, result["error"])
    saved = save_forecast_to_db(engine, result)
    forecast_cache["latest"] = result
    forecast_cache.pop("light", None); forecast_cache.pop("light_calc_at", None)
    return {"status": "success", "summary": result["summary"], "saved_to_db": saved}


async def forecast_results(limit: int = 200, sort: str = "volume", fields: str = "full"):
    """fields=light returns only what the dashboard tables need (~10x smaller payload)."""
    engine = get_engine()
    if fields == "light":
        results = get_forecast_light(engine)
        if not results:  # nothing saved yet -> compute once (slow path)
            results = get_forecast_light(engine) if get_latest_forecast(engine).get("results") else []
        data = {"summary": forecast_summary_fast(engine) or {}}
    else:
        data = get_latest_forecast(engine)
        results = data.get("results", [])
    if sort == "mape":
        results = sorted(results, key=lambda x: x["mape"])
    elif sort == "trend":
        results = sorted(results, key=lambda x: abs(x["trend"]), reverse=True)
    return {"summary": data.get("summary", {}), "results": results[:limit], "total": len(results)}


async def forecast_sku(sku: str):
    engine = get_engine()
    match = None
    if "latest" in forecast_cache:
        match = next((r for r in forecast_cache["latest"].get("results", []) if r["sku"] == sku), None)
    else:
        # Read just this SKU's saved forecast instead of loading every SKU.
        try:
            one = load_forecast_from_db(engine, sku=sku)
            match = one["results"][0] if one else None
        except Exception as e:
            print(f"[forecast_sku] DB read failed: {e}")
        if match is None and not forecast_cache.get("light"):
            results = get_latest_forecast(engine).get("results", [])
            match = next((r for r in results if r["sku"] == sku), None)
    if not match:
        raise HTTPException(404, f"SKU {sku} tidak ditemukan")
    return match
