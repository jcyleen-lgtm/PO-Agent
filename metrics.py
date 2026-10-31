"""
Stockwise — shared inventory metrics: unit cost, valuation, movement class, turnover, data freshness.
Calculated once here and reused by every endpoint.
"""

from datetime import date
from sqlalchemy import text


def unit_costs(conn, cfg, skus=None):
    """{sku: {'cost', 'source', 'verified'}}.
    1) master_cogs.hpp when present (official HPP) -> verified.
    2) sum(cogs_accurate) / sum(quantity) over the last N sales weeks. cogs_accurate is a line total
       (its ratio to amount is the same for qty 1 and qty > 5), so the ratio of sums is the weighted
       average unit cost. Verified only when finance has confirmed it (SW_COST_VERIFIED=true).
    SKUs with neither get no entry -> shown as 'No cost', never as 0."""
    out = {}
    params = {"w": cfg["cost_window_weeks"]}
    sku_filter = ""
    if skus is not None:
        sku_filter = "AND sku = ANY(:k)"
        params["k"] = list(skus)
    rows = conn.execute(text(f"""
        WITH mx AS (SELECT MAX(invoice_date) d FROM sales)
        SELECT sku, SUM(cogs_accurate), SUM(quantity)
        FROM sales, mx
        WHERE invoice_date > mx.d - (:w * 7) AND cogs_accurate IS NOT NULL AND cogs_accurate > 0
          AND quantity > 0 {sku_filter}
        GROUP BY sku
    """), params).fetchall()
    for sku, c, q in rows:
        if q and c:
            out[sku] = {"cost": round(float(c) / float(q), 2), "source": "cogs_accurate",
                        "verified": bool(cfg["cost_verified"])}
    try:
        for sku, hpp in conn.execute(text(
                f"SELECT sku, hpp FROM master_cogs WHERE hpp IS NOT NULL AND hpp > 0 {sku_filter}"),
                params).fetchall():
            out[sku] = {"cost": float(hpp), "source": "master_cogs", "verified": True}
    except Exception:
        conn.rollback()   # master_cogs may not exist on older databases
    return out


def movement_classes(avg_by_sku, cfg):
    """Rank SKUs by average weekly units. Top share Fast, next share Medium, rest Slow, 0 -> Non-moving."""
    moving = sorted((v, k) for k, v in avg_by_sku.items() if v and v > 0)
    moving.reverse()
    n = len(moving)
    fast_n = round(n * cfg["fast_share"])
    med_n = round(n * cfg["medium_share"])
    out = {k: "non_moving" for k, v in avg_by_sku.items() if not v or v <= 0}
    for i, (_, k) in enumerate(moving):
        out[k] = "fast" if i < fast_n else ("medium" if i < fast_n + med_n else "slow")
    return out


def freshness(stock_date, last_sale_date, cfg, today=None):
    today = today or date.today()
    age = (today - stock_date).days if stock_date else None
    level = None
    if age is not None:
        level = "fresh" if age <= cfg["stale_warn_days"] else ("stale" if age <= cfg["stale_block_days"] else "very_stale")
    return {
        "stock_date": str(stock_date) if stock_date else None,
        "stock_age_days": age, "stock_level": level,
        "last_sale_date": str(last_sale_date) if last_sale_date else None,
        "sales_age_days": (today - last_sale_date).days if last_sale_date else None,
        "warn_days": cfg["stale_warn_days"], "block_days": cfg["stale_block_days"],
    }


def inventory_turnover(conn, costs, product_skus=None):
    """Annualised turnover = (COGS in period x 52 / weeks) / average inventory value.
    Needs >= 2 stock snapshots; the period runs from the first to the last snapshot.
    Inventory value at each snapshot uses the same unit cost basis as Inventory Value."""
    counts = conn.execute(text(
        "SELECT snapshot_date, COUNT(DISTINCT sku) FROM stock_snapshots GROUP BY snapshot_date ORDER BY snapshot_date")).fetchall()
    # a partial upload (far fewer SKUs than the fullest snapshot) would distort the average -> skipped
    full = max((n for _, n in counts), default=0)
    dates = [d for d, n in counts if n >= 0.5 * full]
    skipped = len(counts) - len(dates)
    if len(dates) < 2:
        return {"available": False, "reason": "insufficient_snapshots", "snapshots": len(dates),
                "required": 2, "skipped_partial_snapshots": skipped}
    d0, d1 = dates[0], dates[-1]
    weeks = (d1 - d0).days / 7
    if weeks < 1:
        return {"available": False, "reason": "period_too_short", "snapshots": len(dates)}
    values = []
    for d in dates:
        v = 0.0
        for sku, q in conn.execute(text(
                "SELECT sku, SUM(quantity) FROM stock_snapshots WHERE snapshot_date = :d GROUP BY sku"),
                {"d": d}).fetchall():
            if product_skus is not None and sku not in product_skus:
                continue
            c = costs.get(sku)
            if c and q and q > 0:
                v += float(q) * c["cost"]
        values.append(v)
    avg_val = sum(values) / len(values)
    params = {"a": d0, "b": d1}
    cogs = 0.0
    for sku, c in conn.execute(text("""
            SELECT sku, SUM(cogs_accurate) FROM sales
            WHERE invoice_date > :a AND invoice_date <= :b AND cogs_accurate IS NOT NULL GROUP BY sku"""),
            params).fetchall():
        if product_skus is None or sku in product_skus:
            cogs += float(c or 0)
    if avg_val <= 0:
        return {"available": False, "reason": "no_inventory_value", "snapshots": len(dates)}
    return {"available": True, "turnover": round(cogs * 52 / weeks / avg_val, 2),
            "period_start": str(d0), "period_end": str(d1), "weeks": round(weeks, 1),
            "cogs": round(cogs), "avg_inventory_value": round(avg_val), "snapshots": len(dates),
            "skipped_partial_snapshots": skipped}
