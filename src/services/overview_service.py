"""Overview and analysis: database stats, overview KPIs and trends, weekly analysis."""

from datetime import date, timedelta
import math

from src.core.logging import get_logger
from src.core.database import get_engine
from src.repositories import forecast as forecast_repo, inventory as stock_repo, sales_reports as reports
from src.services.forecast_service import forecast_summary_fast, get_forecast_light


log = get_logger("overview")


async def db_stats():
    engine = get_engine()
    with engine.connect() as conn:
        row = reports.db_counts(conn)
        fc = 0
        try:
            fc = forecast_repo.count_rows(conn)
        except Exception:
            pass
        snap_date, snap_skus = None, 0
        try:
            snap = stock_repo.latest_snapshot_stats(conn)
            snap_date, snap_skus = snap[0], snap[1] or 0
        except Exception:
            pass
    return {
        "products": row[0], "sales": row[1],
        "date_range": f"{row[2]} s/d {row[3]}" if row[2] else "Belum ada data",
        "last_update": str(row[3]) if row[3] else None,
        "unique_skus": row[4], "forecast_entries": fc,
        "stock_date": str(snap_date) if snap_date else None, "stock_skus": snap_skus,
    }


async def overview_summary(
    start: str = None, end: str = None,
    preset: str = "l4w",
    top_sort: str = "revenue",
    product_type: str = "all"
):
    engine = get_engine()
    with engine.connect() as conn:
        mx = reports.max_sale_row(conn)
        max_date = mx[0] if mx and mx[0] else None
        if not max_date:
            return {"error":"No sales data","kpis":{},"comparison":{},"trend":[],"top_products":[],"period":{},"forecast_summary":None}

        if start and end:
            dt_end = date.fromisoformat(end)
            dt_start = date.fromisoformat(start)
        else:
            dt_end = max_date
            preset_days = {"l7d":7,"l4w":28,"l8w":56,"l12w":84}
            if preset == "this_month":
                dt_start = dt_end.replace(day=1)
            elif preset == "last_month":
                first_this = dt_end.replace(day=1)
                dt_start = (first_this - timedelta(days=1)).replace(day=1)
                dt_end = first_this - timedelta(days=1)
            else:
                days = preset_days.get(preset, 28)
                dt_start = dt_end - timedelta(days=days - 1)

        period_days = (dt_end - dt_start).days + 1
        period_weeks = max(period_days / 7, 1)

        # Product type filter (import / food / all) applies to every figure below.
        tf = f"AND COALESCE(p.product_type, 'import') = '{product_type}'" if product_type in ("import", "food") else ""
        SRC = "sales s JOIN products p ON s.sku = p.sku" if tf else "sales s"
        prev_end = dt_start - timedelta(days=1)
        prev_start = prev_end - timedelta(days=period_days - 1)

        # KPIs
        kpi = reports.period_kpis(conn, SRC, tf, dt_start, dt_end)
        total_sales = int(kpi[0]); revenue = float(kpi[1]); active = int(kpi[2])
        avg_weekly = round(total_sales / period_weeks)

        # Trending up
        mid = dt_start + timedelta(days=period_days // 2)
        trending = (0,)
        try:
            trending = reports.trending_up_count(conn, SRC, tf, dt_start, mid, dt_end)
        except Exception:
            trending = (0,)

        # Previous period KPIs
        prev = reports.period_kpis(conn, SRC, tf, prev_start, prev_end)
        p_sales = int(prev[0]); p_rev = float(prev[1]); p_active = int(prev[2])
        p_avg = round(p_sales / period_weeks) if period_weeks else 0

        def pchg(c, p):
            return round((c - p) / p * 100, 1) if p else None

        # Trend chart
        if period_days <= 14:
            agg = "day"
        elif period_days <= 90:
            agg = "week"
        else:
            agg = "month"
        trend = reports.trend(conn, SRC, tf, agg, dt_start, dt_end)

        # Top products
        order_col = "r" if top_sort == "revenue" else "q"
        top_type_filter = tf
        top_all = reports.top_products(conn, top_type_filter, order_col, dt_start, dt_end)
        # Previous-period qty and first sale date for all top SKUs in 2 queries
        # (was 2 queries per SKU = ~100 round trips to Supabase).
        top_skus = [r[0] for r in top_all]
        prev_map, first_map = {}, {}
        if top_skus:
            prev_map = reports.qty_by_sku(conn, top_skus, prev_start, prev_end)
            first_map = reports.first_sale_by_sku(conn, top_skus)
        top_g = []
        for r in top_all:
            prev_qty = int(prev_map.get(r[0], 0))
            curr_qty = int(r[2])
            first_seen = first_map.get(r[0])
            is_new = first_seen is not None and first_seen >= dt_start
            if prev_qty > 0:
                growth = round((curr_qty - prev_qty) / prev_qty * 100, 1)
            elif is_new:
                growth = None
            else:
                growth = None
            top_g.append({"sku":r[0],"name":r[1],"qty":curr_qty,"revenue":float(r[3]),
                          "growth":growth,"is_new":is_new,"prev_qty":prev_qty})
        if top_sort == "growth":
            has_g = [x for x in top_g if x["growth"] is not None]
            no_g = [x for x in top_g if x["growth"] is None]
            has_g.sort(key=lambda x: x["growth"], reverse=True)
            top_g = has_g + no_g
        top_g = top_g[:10]

    # Forecast summary (memory cache, else one SQL aggregate)
    try:
        fc_summary = forecast_summary_fast(engine, product_type)
    except Exception as e:
        log.warning(f"forecast summary skipped: {e}")
        fc_summary = None

    return {
        "period": {"start":str(dt_start),"end":str(dt_end),"days":period_days,"aggregation":agg},
        "prev_period": {"start":str(prev_start),"end":str(prev_end)},
        "kpis": {"total_sales":total_sales,"revenue":revenue,"avg_weekly_sales":avg_weekly,"active_products":active,"trending_up":trending[0] if trending else 0},
        "comparison": {"total_sales":pchg(total_sales,p_sales),"revenue":pchg(revenue,p_rev),"avg_weekly_sales":pchg(avg_weekly,p_avg),"active_products":pchg(active,p_active)},
        "trend": [{"date":str(r[0]),"quantity":int(r[1]),"revenue":float(r[2])} for r in trend],
        "top_products": top_g,
        "forecast_summary": fc_summary,
    }


async def weekly_analysis(product_type: str = "import", scope: str = "all"):
    """scope=bestseller -> only SKUs in the Pareto 80% set (the ones that have a forecast)."""
    engine = get_engine()
    type_filter = ""
    if product_type in ("import", "food"):
        type_filter = f"AND COALESCE(p.product_type, 'import') = '{product_type}'"
    with engine.connect() as conn:
        # Only the last 8 weeks are needed week-by-week; lifetime figures come pre-aggregated.
        week_rows = reports.last_weeks(conn, type_filter, 8)
        if not week_rows:
            return {"results": [], "week_labels": {}}
        spark_w = sorted(r[0] for r in week_rows)
        totals = reports.lifetime_by_sku(conn, type_filter)
        recent = reports.weekly_qty_since(conn, type_filter, spark_w[0])
        n_weeks = reports.count_sales_weeks(conn, type_filter)

    if scope == "bestseller":
        try:
            best = {r["sku"] for r in get_forecast_light(engine)}
        except Exception:
            best = set()
        totals = [t for t in totals if t[0] in best]

    weekly = {}
    for sku, w, q in recent:
        weekly.setdefault(sku, {})[w] = q
    recent_4 = spark_w[-4:]
    last_2 = spark_w[-2:]

    results = []
    for sku, name, lifetime_q, active_weeks in totals:
        wmap = weekly.get(sku, {})

        w1 = int(wmap.get(last_2[-1], 0)) if len(last_2) >= 1 else 0
        w2 = int(wmap.get(last_2[-2], 0)) if len(last_2) >= 2 else 0
        change = w1 - w2
        growth = round((change / w2) * 100, 1) if w2 > 0 else (100.0 if w1 > 0 else 0.0)

        l4w = sum(int(wmap.get(w, 0)) for w in recent_4)
        avg_l4w = round(l4w / max(len(recent_4), 1), 1)
        lifetime = int(lifetime_q or 0)
        active = int(active_weeks)
        avg_lt = math.ceil(lifetime / active) if active > 0 else 0
        diff = round(avg_l4w - avg_lt, 1)
        diff_pct = round((avg_l4w / avg_lt) * 100, 1) if avg_lt > 0 else 0.0

        trend = "up" if growth > 20 else ("down" if growth < -20 else "stable")
        spark = [int(wmap.get(w, 0)) for w in spark_w]

        results.append({
            "sku": sku, "product_name": name, "trend": trend,
            "w1": w1, "w2": w2, "change": change, "growth": growth,
            "avg_l4w": avg_l4w, "avg_lt": avg_lt,
            "l4w_vs_lt": diff, "l4w_vs_lt_pct": diff_pct,
            "lifetime": lifetime, "l4w": l4w, "sparkline": spark,
        })

    results.sort(key=lambda x: x["l4w"], reverse=True)
    return {
        "results": results,
        "week_labels": {"w1": str(last_2[-1]) if last_2 else "", "w2": str(last_2[-2]) if len(last_2) >= 2 else ""},
        "total_weeks": int(n_weeks or 0), "total_skus": len(results),
    }
