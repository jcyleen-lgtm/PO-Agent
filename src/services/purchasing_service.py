"""Purchasing decisions: replenishment list, per-SKU plan detail, demand plan, planning settings."""

import json

from fastapi import HTTPException

from src.core.config import get_settings
from src.core.database import get_engine
from src.repositories.forecast import load_forecast_from_db
from src.repositories import forecast as forecast_repo, inventory as stock_repo, sales as sales_repo
from src.repositories.products import _type_filter
from src.repositories.purchase_orders import _fetch_po_lines
from src.services.forecast_service import get_forecast_light
from src.services.inventory_service import build_inventory
import metrics as mt
import replenishment as rp



async def replenishment(product_type: str = "import", only_reorder: bool = False, urgency: str = "all"):
    """Purchase recommendations for forecasted best sellers. The system recommends; purchasing decides
    and places orders. Primary qty = thesis formula; order-up-to is returned as a comparison."""
    engine = get_engine()
    inv = build_inventory(engine, product_type, scope="bestseller")
    rows = [r for r in inv["rows"] if r["forecasted"]]
    if only_reorder:
        rows = [r for r in rows if (r["reco_po"] or 0) > 0]
    if urgency in ("critical", "high", "medium", "low"):
        rows = [r for r in rows if r["urgency"] == urgency]
    rows.sort(key=lambda r: (r["order_by"] or "9999-12-31", rp.URGENCY_ORDER.get(r["urgency"], 9), -(r["l4w_revenue"])))
    s = inv["summary"]
    return {
        "results": rows,
        "summary": {"forecasted": s["forecasted"], "reorder_skus": s["reorder_skus"], "reorder_qty": s["reorder_qty"],
                    "reorder_qty_order_up_to": sum(r["reco_order_up_to"] or 0 for r in rows),
                    "est_purchase_cost": s["est_purchase_cost"], "cost_verified": s["cost_verified"],
                    "urgency": s["urgency"], "needs_po_check": s["needs_po_check"]},
        "freshness": inv["freshness"], "settings": inv["settings"], "stock_date": inv["stock_date"],
    }


async def forecast_plan(product_type: str = "import"):
    """Demand Plan page: per forecasted SKU, the weekly forecast, the 8 holdout weeks (plan from a model
    trained without them vs actual) and the model parameters behind every number. Months are built in the browser."""
    engine = get_engine()
    tf = _type_filter(product_type)
    with engine.connect() as conn:
        rows = forecast_repo.plan_rows(conn, tf)
        price = sales_repo.avg_selling_price_12w(conn)
    light = {r["sku"]: r for r in get_forecast_light(engine)}
    out, calc = [], None
    for sku, name, a, b, mape, tw, fc, act, ho, level, trend, calc_at in rows:
        fc = fc if isinstance(fc, dict) else json.loads(fc or "{}")
        act = act if isinstance(act, dict) else json.loads(act or "{}")
        ho = ho if (isinstance(ho, dict) or ho is None) else json.loads(ho)
        aw, aq = act.get("weeks", []), act.get("quantities", [])
        h = ho or {}
        hw = aw[-len(h["holdout_actual"]):] if h.get("holdout_actual") else []
        lf = light.get(sku, {})
        calc = calc_at or calc
        out.append({
            "sku": sku, "product_name": name,
            "forecast": {"weeks": fc.get("weeks", []), "quantities": fc.get("quantities", [])},
            "holdout": {"weeks": hw, "actual": h.get("holdout_actual"), "forecast": h.get("holdout_forecast"),
                        "wmape": h.get("wmape"), "alpha": h.get("alpha"), "beta": h.get("beta"),
                        "train_weeks": h.get("train_weeks"), "reason": h.get("reason") if ho else "not_computed"},
            "model": {"alpha": float(a) if a is not None else None, "beta": float(b) if b is not None else None,
                      "mape": float(mape) if mape is not None else None, "training_weeks": tw,
                      "level": float(level) if level is not None else None,
                      "trend": float(trend) if trend is not None else None,
                      "history_start": aw[0] if aw else None, "history_end": aw[-1] if aw else None},
            "trend_pct": lf.get("trend_pct"), "trend_class": lf.get("trend_class"),
            "avg_price": round(price[sku]) if sku in price else None,
            "l4w_avg": round(sum(aq[-4:]) / max(len(aq[-4:]), 1), 1) if aq else None,
        })
    return {"results": out, "calculated_at": calc.isoformat() if hasattr(calc, "isoformat") else calc,
            "holdout_weeks": 8, "horizon_weeks": 12}


async def replenishment_sku(sku: str):
    """Week-by-week projection for one SKU + PO lines + safety-stock comparisons (simulation only)."""
    engine = get_engine()
    cfg = get_settings()
    fc = next((r for r in get_forecast_light(engine) if r["sku"] == sku), None)
    if not fc:
        raise HTTPException(404, f"SKU {sku} has no forecast (not a forecasted best seller)")
    with engine.connect() as conn:
        snap = stock_repo.latest_snapshot_date(conn)
        cs = stock_repo.stock_for_sku(conn, sku, snap) if snap else 0
        lines = _fetch_po_lines(conn, sku).get(sku, [])
        cost = mt.unit_costs(conn, cfg, [sku]).get(sku)
        last_sale = sales_repo.last_sale_date(conn)
    fq, fw = fc["forecast_data"]["quantities"], fc["forecast_data"].get("weeks", [])
    plan = rp.plan_sku(int(cs or 0), lines, fq, fw, cfg)
    scen = []
    for pct in (0.05, 0.10, 0.15):
        p2 = rp.plan_sku(int(cs or 0), lines, fq, fw, {**cfg, "ss_pct": pct})
        scen.append({"ss_pct": pct, "safety_stock": p2["safety_stock"], "reco_qty": p2["reco_qty"],
                     "stockout_week": p2["stockout_week"], "is_default": abs(pct - cfg["ss_pct"]) < 1e-9})
    hist = None
    try:
        one = load_forecast_from_db(engine, sku=sku)
        hist = one["results"][0]["actual_data"]["quantities"] if one else None
    except Exception:
        pass
    stat_ss = rp.statistical_safety_stock(hist, plan["lead_time_weeks"])
    return {"sku": sku, "product_name": fc["product_name"], "current_stock": int(cs or 0),
            "stock_date": str(snap) if snap else None, "unit_cost": cost,
            "forecast": {"weeks": fw, "quantities": fq, "fc_avg_8w": fc["fc_avg_8w"],
                         "trend_pct": fc["trend_pct"], "trend_class": fc["trend_class"]},
            "plan": plan,
            "safety_stock_comparison": {
                "policy": {"method": f"{round(cfg['ss_pct']*100)}% x forecast {cfg['ss_basis_weeks']} weeks",
                           "value": plan["safety_stock"], "used_in_recommendation": True},
                "statistical": {"method": "z x sigma_d x sqrt(L), z=1.65 (~95% service level), sigma_d = std dev of weekly demand, last 26 weeks",
                                "value": stat_ss, "used_in_recommendation": False,
                                "note": None if stat_ss is not None else "needs at least 8 weeks of history"},
                "sensitivity": scen},
            "freshness": mt.freshness(snap, last_sale, cfg)}


async def get_planning_settings():
    """Planning parameters in use (read-only here; set via environment variables for now)."""
    return get_settings()
