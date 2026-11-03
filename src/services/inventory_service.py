"""Inventory aggregation: build_inventory (single source for status, coverage, cost and plan per SKU) and the inventory endpoints."""

from datetime import date, timedelta

from sqlalchemy import text
import pandas as pd

from src.core.config import get_settings
from src.core.database import get_engine
from src.repositories import inventory as stock_repo, sales as sales_repo
from src.repositories.products import _type_filter
from src.repositories.purchase_orders import _fetch_po_lines
from src.services.forecast_service import get_forecast_light
import metrics as mt
import replenishment as rp



def coverage_status(cov, stock=0):
    """Kept for old callers; the rule itself lives in replenishment.stock_status (3 statuses only)."""
    return rp.stock_status(cov, stock, get_settings())


def build_inventory(engine, product_type="import", scope="all", today=None):
    """One pass that every inventory/replenishment page reads from, so a SKU shows the same
    coverage, status, cost, reco and urgency everywhere."""
    cfg = get_settings()
    today = today or date.today()
    tf = _type_filter(product_type)
    n_hist = max(12, cfg["movement_window_weeks"])
    with engine.connect() as conn:
        weeks = sales_repo.recent_sales_weeks(conn, tf, n_hist)
        last_sale = sales_repo.last_sale_date(conn)
        snap_dates = stock_repo.snapshot_dates_desc(conn)
        snap_date = snap_dates[0] if snap_dates else None
        stk = stock_repo.stock_by_sku(conn, snap_date) if snap_date else {}
        po_lines = _fetch_po_lines(conn)
        if not weeks:
            return {"rows": [], "summary": {}, "freshness": mt.freshness(snap_date, last_sale, cfg, today),
                    "stock_date": str(snap_date) if snap_date else None, "has_stock": bool(stk),
                    "snapshot_count": len(snap_dates), "settings": cfg}
        r4 = weeks[-4:]
        rmov = weeks[-cfg["movement_window_weeks"]:]
        sku_rows = sales_repo.sold_skus(conn, tf)
        recent = sales_repo.weekly_sales_since(conn, tf, weeks[0])
        costs = mt.unit_costs(conn, cfg)

    qty_by, rev_by = {}, {}
    for sku, w, q, a in recent:
        qty_by.setdefault(sku, {})[w] = int(q or 0)
        rev_by.setdefault(sku, {})[w] = float(a or 0)

    avg_mov = {sku: sum(qty_by.get(sku, {}).get(w, 0) for w in rmov) / max(len(rmov), 1) for sku, _ in sku_rows}
    movement = mt.movement_classes(avg_mov, cfg)

    try:
        fc_map = {r["sku"]: r for r in get_forecast_light(engine)}
    except Exception:
        fc_map = {}
    if scope == "bestseller" and fc_map:
        # every SKU in the best-seller scope, including company-list SKUs that have no sales yet
        sku_rows = sorted((s, r.get("product_name") or s) for s, r in fc_map.items()
                          if product_type == "all" or r.get("product_type", "import") == product_type)

    rows = []
    for sku, name in sku_rows:
        wmap, rmap = qty_by.get(sku, {}), rev_by.get(sku, {})
        l4w_qty = sum(wmap.get(w, 0) for w in r4)
        l4w_rev = sum(rmap.get(w, 0) for w in r4)
        avg_l4w = round(l4w_qty / max(len(r4), 1), 1)
        cs = stk.get(sku, 0)
        lines = po_lines.get(sku, [])
        op = sum(l["outstanding"] for l in lines)
        available = cs + op
        cov = rp.coverage_weeks(cs, op, avg_l4w)
        status = rp.stock_status(cov, cs, cfg)
        c = costs.get(sku)
        ovq = rp.overstock_qty(available, avg_l4w, cfg) if status == "overstocked" else 0
        row = {
            "sku": sku, "product_name": name,
            "l4w_qty": l4w_qty, "l4w_revenue": round(l4w_rev), "avg_l4w": avg_l4w,
            "current_stock": cs, "out_of_stock": cs <= 0,
            "ongoing_po": op, "available": available,
            "coverage": cov, "days_of_inventory": round(cov * 7) if cov is not None else None,
            "status": status,
            "movement": movement.get(sku, "non_moving"), "avg_weekly_12w": round(avg_mov.get(sku, 0), 1),
            "unit_cost": c["cost"] if c else None,
            "cost_source": c["source"] if c else None,
            "cost_verified": c["verified"] if c else False,
            "inventory_value": round(max(cs, 0) * c["cost"]) if c else None,
            "overstock_qty": ovq,
            "overstock_value": round(ovq * c["cost"]) if (c and ovq) else (0 if not ovq else None),
            "forecasted": sku in fc_map,
            # forecast-based fields (None when the SKU is not a forecasted best seller)
            "fc_avg_weekly": None, "mape": None, "holdout_wmape": None,
            "trend_pct": None, "trend_class": None, "trend_direction": None,
            "lt_demand_8w": None, "safety_stock": None, "rop": None,
            "inventory_position": None, "confirmed_incoming": None, "unconfirmed_incoming": None,
            "reco_po": None, "reco_order_up_to": None, "reco_if_unconfirmed_arrives": None,
            "stockout_week": None, "stockout_week_if_unconfirmed_arrives": None,
            "urgency": None, "slack_weeks": None, "needs_po_check": False, "est_purchase_cost": None,
            "order_by": None, "order_by_days": None, "order_by_is_estimate": None,
            "scope_source": None, "hero_group": None, "hero_status": None, "pareto_rank": None,
            "fc_method": None, "fc_method_reason": None,
        }
        fc = fc_map.get(sku)
        if fc:
            sc = fc.get("scope") or {}
            row.update({"scope_source": sc.get("source"), "hero_group": sc.get("group"),
                        "hero_status": sc.get("status"), "pareto_rank": sc.get("rank"),
                        "fc_method": fc.get("method", "holt"), "fc_method_reason": fc.get("method_reason")})
        if fc:
            fq = fc["forecast_data"]["quantities"]
            plan = rp.plan_sku(cs, lines, fq, fc["forecast_data"].get("weeks", []), cfg, today=today)
            row.update({
                "fc_avg_weekly": fc["fc_avg_8w"], "mape": fc["mape"], "holdout_wmape": fc["holdout_wmape"],
                "trend_pct": fc["trend_pct"], "trend_class": fc["trend_class"],
                "trend_direction": fc["trend_direction"],
                "lt_demand_8w": plan["lt_demand"], "safety_stock": plan["safety_stock"], "rop": plan["rop"],
                "inventory_position": plan["inventory_position"],
                "confirmed_incoming": plan["confirmed_incoming"],
                "unconfirmed_incoming": plan["unconfirmed_incoming"],
                "reco_po": plan["reco_qty"], "reco_order_up_to": plan["reco_order_up_to"],
                "reco_if_unconfirmed_arrives": plan["reco_if_unconfirmed_arrives"],
                "stockout_week": plan["stockout_week"],
                "stockout_week_if_unconfirmed_arrives": plan["stockout_week_if_unconfirmed_arrives"],
                "urgency": plan["urgency"], "slack_weeks": plan["slack_weeks"],
                "order_by": plan["order_by"], "order_by_days": plan["order_by_days"],
                "order_by_is_estimate": plan["order_by_is_estimate"],
                "needs_po_check": plan["needs_po_check"],
                "est_purchase_cost": round(plan["reco_qty"] * c["cost"]) if (c and plan["reco_qty"]) else (0 if not plan["reco_qty"] else None),
                "flags": plan["flags"],
            })
        rows.append(row)

    with_cost = [r for r in rows if r["unit_cost"] is not None]
    summary = {
        "total": len(rows),
        "understocked": sum(r["status"] == "understocked" for r in rows),
        "healthy": sum(r["status"] == "healthy" for r in rows),
        "overstocked": sum(r["status"] == "overstocked" for r in rows),
        "unclassified": sum(r["status"] is None for r in rows),   # no sales and no stock: not a status
        "out_of_stock": sum(r["out_of_stock"] for r in rows),
        "stock_on_hand": sum(max(r["current_stock"], 0) for r in rows),
        "negative_stock_skus": sum(r["current_stock"] < 0 for r in rows),
        "inventory_value": sum(r["inventory_value"] for r in with_cost),
        "valued_skus": len(with_cost), "no_cost_skus": len(rows) - len(with_cost),
        "cost_verified": bool(with_cost) and all(r["cost_verified"] for r in with_cost),
        "overstock_qty": sum(r["overstock_qty"] for r in rows),
        "overstock_value": sum(r["overstock_value"] or 0 for r in rows),
        "forecasted": sum(r["forecasted"] for r in rows),
        "reorder_skus": sum(1 for r in rows if (r["reco_po"] or 0) > 0),
        "reorder_qty": sum(r["reco_po"] or 0 for r in rows),
        "est_purchase_cost": sum(r["est_purchase_cost"] or 0 for r in rows),
        "urgency": {u: sum(r["urgency"] == u for r in rows) for u in ("critical", "high", "medium", "low")},
        "needs_po_check": sum(r["needs_po_check"] for r in rows),
        "movement": {m: sum(r["movement"] == m for r in rows) for m in ("fast", "medium", "slow", "non_moving")},
        # legacy key (old dashboards): there is no 4th status any more
        "no_sales": 0,
    }
    return {
        "rows": rows, "summary": summary,
        "freshness": mt.freshness(snap_date, last_sale, cfg, today),
        "stock_date": str(snap_date) if snap_date else None, "has_stock": bool(stk),
        "snapshot_count": len(snap_dates), "sales_weeks": [str(w) for w in r4],
        "settings": {k: cfg[k] for k in ("lead_time_weeks", "review_period_days", "ss_pct", "ss_basis_weeks",
                                         "understock_weeks", "overstock_weeks", "cost_verified",
                                         "stale_warn_days", "stale_block_days")},
    }


async def best_sellers_at_risk(product_type: str = "all", limit: int = 50):
    """Understocked best sellers (Pareto set), same numbers as the inventory pages.
    The stock comparison vs ~8 weeks ago is only returned when a second, older snapshot exists."""
    engine = get_engine()
    inv = build_inventory(engine, product_type, scope="bestseller")
    best_only = any(r["forecasted"] for r in inv["rows"])
    res = [r for r in inv["rows"] if r["status"] == "understocked" and r["l4w_qty"] > 0]

    snap_now = date.fromisoformat(inv["stock_date"]) if inv["stock_date"] else None
    snap_prev, prev_stk = None, {}
    if snap_now and inv["snapshot_count"] >= 2:
        with engine.connect() as conn:
            dates = [r[0] for r in conn.execute(text(
                "SELECT DISTINCT snapshot_date FROM stock_snapshots WHERE snapshot_date < :d ORDER BY snapshot_date DESC"),
                {"d": snap_now}).fetchall()]
            older = [d for d in dates if (snap_now - d).days >= 49]
            snap_prev = older[0] if older else (dates[-1] if dates else None)
            if snap_prev:
                prev_stk = {r[0]: int(r[1]) for r in conn.execute(text(
                    "SELECT sku, SUM(quantity) FROM stock_snapshots WHERE snapshot_date = :d GROUP BY sku"),
                    {"d": snap_prev}).fetchall()}
    results = []
    for r in res:
        ps = prev_stk.get(r["sku"]) if snap_prev else None
        results.append({"sku": r["sku"], "name": r["product_name"], "l4w_qty": r["l4w_qty"],
                        "revenue": r["l4w_revenue"], "avg_weekly": r["avg_l4w"],
                        "current_stock": r["current_stock"], "ongoing_po": r["ongoing_po"],
                        "coverage": r["coverage"], "days_of_inventory": r["days_of_inventory"],
                        "status": r["status"], "urgency": r["urgency"], "slack_weeks": r["slack_weeks"],
                        "stockout_week": r["stockout_week"], "reco_po": r["reco_po"],
                        "needs_po_check": r["needs_po_check"],
                        "prev_stock": ps,
                        "stock_change_pct": round((r["current_stock"] - ps) / ps * 100, 1) if ps else None})
    results.sort(key=lambda x: x["revenue"], reverse=True)
    return {
        "results": results[:limit],
        "total_understocked": len(results),
        "stock_now_date": inv["stock_date"],
        "stock_prev_date": str(snap_prev) if snap_prev else None,
        "weeks_between": round((snap_now - snap_prev).days / 7, 1) if snap_prev else None,
        "comparison_available": snap_prev is not None,
        "best_sellers_only": best_only,
        "freshness": inv["freshness"],
    }


async def coverage_analysis():
    """DEPRECATED: not used by the dashboard (full sales scan). Kept for any outside caller;
    use /api/inventory instead."""
    engine = get_engine()
    with engine.connect() as conn:
        wdf = pd.read_sql(text("""
            SELECT s.sku, p.name as product_name,
                   DATE_TRUNC('week', s.invoice_date)::date as week_start,
                   SUM(s.quantity) as qty
            FROM sales s JOIN products p ON s.sku = p.sku
            WHERE s.invoice_date IS NOT NULL
            GROUP BY s.sku, p.name, DATE_TRUNC('week', s.invoice_date)::date
        """), conn)
        try:
            stk = pd.read_sql(text("""
                SELECT sku, quantity as current_stock FROM stock_snapshots
                WHERE snapshot_date = (SELECT MAX(snapshot_date) FROM stock_snapshots)
            """), conn)
        except Exception:
            stk = pd.DataFrame(columns=["sku", "current_stock"])
        try:
            snap = conn.execute(text("SELECT MAX(snapshot_date) FROM stock_snapshots")).fetchone()
        except Exception:
            snap = (None,)
        try:
            po = pd.read_sql(text("""
                SELECT sku, SUM(qty_ordered-qty_received) as ongoing
                FROM purchase_orders WHERE status IN ('pending','partial') GROUP BY sku
            """), conn)
        except Exception:
            po = pd.DataFrame(columns=["sku", "ongoing"])

    if wdf.empty:
        return {"results": [], "stock_date": None, "has_stock": False}

    all_weeks = sorted(wdf["week_start"].unique())
    r4 = all_weeks[-4:] if len(all_weeks) >= 4 else all_weeks

    results = []
    for sku in wdf["sku"].unique():
        sd = wdf[wdf["sku"] == sku]
        name = sd.iloc[0]["product_name"]
        wmap = dict(zip(sd["week_start"], sd["qty"]))
        l4w = sum(int(wmap.get(w, 0)) for w in r4)
        avg = round(l4w / max(len(r4), 1), 1)

        sr = stk[stk["sku"] == sku]
        cs = int(sr.iloc[0]["current_stock"]) if len(sr) > 0 else 0
        pr = po[po["sku"] == sku]
        op = int(pr.iloc[0]["ongoing"]) if len(pr) > 0 else 0

        cov = round((cs + op) / avg, 1) if avg > 0 else None
        st = coverage_status(cov, cs)  # same 3-status rule as every other page

        results.append({"sku":sku,"product_name":name,"l4w":l4w,"avg_l4w":avg,
                        "current_stock":cs,"ongoing_po":op,"coverage":cov,
                        "status":st,"weeks_oos":None})

    results.sort(key=lambda x: x["coverage"] if x["coverage"] is not None else 9999)
    return {
        "results": results,
        "stock_date": str(snap[0]) if snap and snap[0] else None,
        "has_stock": len(stk) > 0, "total_skus": len(results),
    }


async def inventory_analysis(sort: str = "priority", product_type: str = "import", scope: str = "all",
                             summary_only: bool = False):
    """Inventory health per SKU (coverage, status, days of inventory, value, overstock, movement)
    plus the forecast-based replenishment fields. product_type: import (default) | food | all."""
    engine = get_engine()
    inv = build_inventory(engine, product_type, scope)
    results = inv["rows"]
    status_order = {"understocked": 0, "healthy": 1, "overstocked": 2, None: 3}
    if sort == "priority":
        results.sort(key=lambda x: (status_order.get(x["status"], 9), -(x["l4w_revenue"])))
    elif sort == "revenue":
        results.sort(key=lambda x: -x["l4w_revenue"])
    elif sort == "coverage":
        results.sort(key=lambda x: x["coverage"] if x["coverage"] is not None else 9999)
    elif sort == "stockout":
        results.sort(key=lambda x: (x["stockout_week"] if x["stockout_week"] else 999, -(x["l4w_revenue"])))
    elif sort == "value":
        results.sort(key=lambda x: -(x["inventory_value"] or 0))
    out = {k: v for k, v in inv.items() if k != "rows"}
    if not summary_only:
        out["results"] = results
    return out


async def inventory_summary(product_type: str = "import", scope: str = "all"):
    """Overview: totals, status distribution, value, freshness, turnover availability."""
    engine = get_engine()
    inv = build_inventory(engine, product_type, scope)
    cfg = get_settings()
    try:
        with engine.connect() as conn:
            costs = mt.unit_costs(conn, cfg)
            turnover = mt.inventory_turnover(conn, costs, {r["sku"] for r in inv["rows"]})
    except Exception as e:
        turnover = {"available": False, "reason": f"error: {e}"}
    top_reorder = sorted([r for r in inv["rows"] if (r["reco_po"] or 0) > 0],
                         key=lambda r: (r["order_by"] or "9999-12-31", -(r["l4w_revenue"])))[:5]
    out = {k: v for k, v in inv.items() if k != "rows"}
    out["turnover"] = turnover
    out["top_reorder"] = [{k: r[k] for k in ("sku", "product_name", "urgency", "reco_po", "stockout_week",
                                             "est_purchase_cost", "needs_po_check", "order_by", "order_by_days",
                                             "order_by_is_estimate", "status")} for r in top_reorder]
    out["order_soon"] = sum(1 for r in inv["rows"] if r["order_by_days"] is not None
                            and not r["order_by_is_estimate"] and r["order_by_days"] <= 14)
    out["order_overdue"] = sum(1 for r in inv["rows"] if r["order_by_days"] is not None
                               and not r["order_by_is_estimate"] and r["order_by_days"] < 0)
    return out


def _stockout_rate(engine, min_snapshots=4, weeks=12):
    """SKU-weeks with stock <= 0 / SKU-weeks with stock data, forecasted best sellers, last `weeks` weeks.
    Not reported until there are at least `min_snapshots` snapshots in that window."""
    try:
        best = {r["sku"] for r in get_forecast_light(engine)}
    except Exception:
        best = set()
    with engine.connect() as conn:
        mx = stock_repo.latest_snapshot_date(conn)
        if not mx:
            return {"available": False, "reason": "no_snapshots", "snapshots": 0, "required": min_snapshots}
        rows = conn.execute(text("""
            SELECT snapshot_date, sku, SUM(quantity) FROM stock_snapshots
            WHERE snapshot_date > :a GROUP BY snapshot_date, sku
        """), {"a": mx - timedelta(weeks=weeks)}).fetchall()
    dates = {r[0] for r in rows}
    if len(dates) < min_snapshots:
        return {"available": False, "reason": "needs_weekly_stock_snapshots", "snapshots": len(dates),
                "required": min_snapshots}
    obs = [r for r in rows if not best or r[1] in best]
    if not obs:
        return {"available": False, "reason": "no_best_seller_stock_rows", "snapshots": len(dates)}
    oos = sum(1 for r in obs if (r[2] or 0) <= 0)
    return {"available": True, "rate_pct": round(oos / len(obs) * 100, 1), "sku_weeks": len(obs),
            "stockout_sku_weeks": oos, "snapshots": len(dates), "weeks": weeks}
