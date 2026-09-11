"""
PO Agent — Web Server v2
FastAPI backend: ETL, Weekly Analysis, Coverage, Forecasting, Dashboard.
"""

from dotenv import load_dotenv
load_dotenv()

import os, io, uuid, csv, json, math
from datetime import datetime, date, timedelta

from fastapi import FastAPI, UploadFile, File, HTTPException, Query
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware

import pandas as pd
import numpy as np
from sqlalchemy import create_engine, text
import psycopg2

from etl import clean_sales_data, load_master_sku
from forecast import run_forecast, save_forecast_to_db

DB_URL = os.getenv("SUPABASE_DB_URL")
UPLOAD_DIR = "uploads"
MASTER_SKU_PATH = "MASTER_SKU.xlsx"
os.makedirs(UPLOAD_DIR, exist_ok=True)

app = FastAPI(title="PO Agent", version="2.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

master_sku = {}
if os.path.exists(MASTER_SKU_PATH):
    master_sku = load_master_sku(MASTER_SKU_PATH)
    print(f"[startup] Master SKU loaded: {len(master_sku)} entries")

processed_cache = {}
forecast_cache = {}


def get_engine():
    if not DB_URL:
        raise HTTPException(500, "SUPABASE_DB_URL not configured")
    return create_engine(DB_URL)


def get_raw_conn():
    if not DB_URL:
        raise HTTPException(500, "SUPABASE_DB_URL not configured")
    return psycopg2.connect(DB_URL)


# ── Page Routes ───────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def dashboard_page():
    with open("templates/dashboard.html", "r", encoding="utf-8") as f:
        return f.read()

@app.get("/etl", response_class=HTMLResponse)
async def etl_page():
    with open("templates/index.html", "r", encoding="utf-8") as f:
        return f.read()


# ── ETL API ───────────────────────────────────────────────────

@app.post("/api/master-sku/upload")
async def upload_master_sku(file: UploadFile = File(...)):
    global master_sku
    content = await file.read()
    with open(MASTER_SKU_PATH, "wb") as f:
        f.write(content)
    master_sku = load_master_sku(MASTER_SKU_PATH)
    return {"status": "success", "entries": len(master_sku)}


@app.post("/api/etl/upload")
async def etl_upload(file: UploadFile = File(...)):
    if not file.filename.endswith((".xls", ".xlsx")):
        raise HTTPException(400, "Format file harus .xls atau .xlsx")
    file_id = str(uuid.uuid4())[:8]
    ext = os.path.splitext(file.filename)[1]
    save_path = os.path.join(UPLOAD_DIR, f"{file_id}{ext}")
    content = await file.read()
    with open(save_path, "wb") as f:
        f.write(content)
    result = clean_sales_data(save_path, master_sku=master_sku)
    if result["errors"]:
        os.remove(save_path)
        raise HTTPException(400, detail={"errors": result["errors"]})
    df = result["df"]
    processed_cache[file_id] = {
        "df": df, "filepath": save_path,
        "filename": file.filename, "created_at": datetime.now().isoformat(),
    }
    preview_rows = df.head(20).copy()
    for col in preview_rows.columns:
        if preview_rows[col].dtype == "datetime64[ns]":
            preview_rows[col] = preview_rows[col].dt.strftime("%Y-%m-%d")
    preview_rows = preview_rows.fillna("").to_dict("records")
    return {
        "file_id": file_id, "filename": file.filename,
        "stats": result["stats"], "preview": preview_rows,
        "columns": list(df.columns),
    }


@app.post("/api/etl/confirm/{file_id}")
async def etl_confirm(file_id: str):
    if file_id not in processed_cache:
        raise HTTPException(404, "Data tidak ditemukan. Upload ulang.")
    cached = processed_cache[file_id]
    df = cached["df"]
    try:
        sales = df.copy()
        sales = sales.rename(columns={
            "SKU": "sku", "date": "invoice_date", "quantity": "quantity",
            "amount": "amount", "COGS": "cogs_accurate", "GPAO": "gpao_accurate",
            "GPAO (%)": "gpao_pct_acc", "week": "week_label", "month": "month_label",
            "quarter": "quarter_label", "year": "year_val",
        })
        sales["sku"] = sales["sku"].astype(str)
        if "invoice_date" in sales.columns:
            sales["invoice_date"] = pd.to_datetime(sales["invoice_date"]).dt.date
        for col in ["cogs_accurate", "unit"]:
            if col not in sales.columns:
                sales[col] = None
        if "gpao_pct_acc" in sales.columns:
            def parse_pct(val):
                if pd.isna(val): return None
                if isinstance(val, str):
                    return float(val.replace("%", "").strip()) / 100 if "%" in val else float(val)
                return float(val)
            sales["gpao_pct_acc"] = sales["gpao_pct_acc"].apply(parse_pct)
        keep_cols = ["sku", "invoice_date", "quantity", "unit", "amount",
                     "cogs_accurate", "gpao_accurate", "gpao_pct_acc",
                     "week_label", "month_label", "quarter_label", "year_val"]
        for col in keep_cols:
            if col not in sales.columns:
                sales[col] = None
        sales = sales[keep_cols]
        products = df[["SKU", "product_name"]].drop_duplicates(subset=["SKU"])
        products = products.groupby("SKU").first().reset_index()
        conn = get_raw_conn()
        conn.autocommit = False
        cur = conn.cursor()
        try:
            for _, row in products.iterrows():
                cur.execute("""
                    INSERT INTO products (sku, name) VALUES (%s, %s)
                    ON CONFLICT (sku) DO UPDATE SET name = EXCLUDED.name, updated_at = NOW()
                """, (str(row["SKU"]), row["product_name"]))
            cur.execute("TRUNCATE TABLE sales RESTART IDENTITY")
            buffer = io.StringIO()
            writer = csv.writer(buffer, delimiter='\t', quoting=csv.QUOTE_MINIMAL)
            for _, row in sales.iterrows():
                writer.writerow([
                    str(row["sku"]),
                    str(row["invoice_date"]) if pd.notna(row["invoice_date"]) else r'\N',
                    int(row["quantity"]) if pd.notna(row["quantity"]) else r'\N',
                    str(row["unit"]) if pd.notna(row["unit"]) else r'\N',
                    float(row["amount"]) if pd.notna(row["amount"]) else r'\N',
                    float(row["cogs_accurate"]) if pd.notna(row["cogs_accurate"]) else r'\N',
                    float(row["gpao_accurate"]) if pd.notna(row["gpao_accurate"]) else r'\N',
                    float(row["gpao_pct_acc"]) if pd.notna(row["gpao_pct_acc"]) else r'\N',
                    str(row["week_label"]) if pd.notna(row["week_label"]) else r'\N',
                    str(row["month_label"]) if pd.notna(row["month_label"]) else r'\N',
                    str(row["quarter_label"]) if pd.notna(row["quarter_label"]) else r'\N',
                    int(row["year_val"]) if pd.notna(row["year_val"]) else r'\N',
                ])
            buffer.seek(0)
            cur.copy_from(buffer, 'sales', sep='\t', null=r'\N',
                          columns=('sku','invoice_date','quantity','unit','amount',
                                   'cogs_accurate','gpao_accurate','gpao_pct_acc',
                                   'week_label','month_label','quarter_label','year_val'))
            conn.commit()
            total = len(sales)
        except Exception as e:
            conn.rollback()
            raise e
        finally:
            cur.close()
            conn.close()
        engine = get_engine()
        with engine.connect() as c:
            r = c.execute(text("""
                SELECT (SELECT COUNT(*) FROM products),(SELECT COUNT(*) FROM sales),
                       (SELECT MIN(invoice_date) FROM sales),(SELECT MAX(invoice_date) FROM sales),
                       (SELECT COUNT(DISTINCT sku) FROM sales)
            """))
            row = r.fetchone()
        if os.path.exists(cached["filepath"]):
            os.remove(cached["filepath"])
        del processed_cache[file_id]
        return {
            "status": "success",
            "message": f"{total:,} baris berhasil dimuat ke database",
            "database": {"products": row[0], "sales": row[1],
                         "date_range": f"{row[2]} s/d {row[3]}", "unique_skus": row[4]},
        }
    except Exception as e:
        raise HTTPException(500, f"Gagal memuat data: {str(e)}")


# ── Stock Upload ──────────────────────────────────────────────

@app.post("/api/stock/upload")
async def stock_upload(file: UploadFile = File(...)):
    if not file.filename.endswith((".xls", ".xlsx")):
        raise HTTPException(400, "Format harus .xls/.xlsx")
    content = await file.read()
    tmp = os.path.join(UPLOAD_DIR, f"stok_{uuid.uuid4().hex[:8]}{os.path.splitext(file.filename)[1]}")
    with open(tmp, "wb") as f:
        f.write(content)
    try:
        ext = os.path.splitext(tmp)[1].lower()
        raw = pd.read_excel(tmp, engine="xlrd" if ext == ".xls" else "openpyxl", header=None)
        raw.columns = range(len(raw.columns))
        raw = raw.dropna(subset=[0])
        raw[0] = raw[0].astype(str).str.strip()
        raw[2] = pd.to_numeric(raw[2], errors="coerce").fillna(0)
        df = raw[raw[0].apply(lambda s: len(s.replace("-",".").split(".")) >= 3)].copy()
        df = df.rename(columns={0:"sku", 1:"product", 2:"quantity"})
        df["quantity"] = df["quantity"].astype(int)
        today = date.today()
        conn = get_raw_conn()
        cur = conn.cursor()
        conn.autocommit = False
        try:
            cur.execute("DELETE FROM stock_snapshots WHERE snapshot_date = %s", (today,))
            for _, row in df.iterrows():
                cur.execute("INSERT INTO stock_snapshots (sku, quantity, snapshot_date) VALUES (%s,%s,%s) ON CONFLICT DO NOTHING",
                            (row["sku"], row["quantity"], today))
            conn.commit()
            saved = len(df)
        except Exception as e:
            conn.rollback()
            raise e
        finally:
            cur.close()
            conn.close()
        os.remove(tmp)
        return {"status":"success","message":f"{saved} SKU stock saved for {today}","date":str(today),"total_skus":saved}
    except Exception as e:
        if os.path.exists(tmp): os.remove(tmp)
        raise HTTPException(500, f"Gagal parse: {e}")


# ══════════════════════════════════════════════════════════════
# DASHBOARD APIs
# ══════════════════════════════════════════════════════════════

@app.get("/api/db/stats")
async def db_stats():
    engine = get_engine()
    with engine.connect() as conn:
        row = conn.execute(text("""
            SELECT (SELECT COUNT(*) FROM products),(SELECT COUNT(*) FROM sales),
                   (SELECT MIN(invoice_date) FROM sales),(SELECT MAX(invoice_date) FROM sales),
                   (SELECT COUNT(DISTINCT sku) FROM sales)
        """)).fetchone()
        fc = 0
        try:
            fc = conn.execute(text("SELECT COUNT(*) FROM forecast_results")).fetchone()[0]
        except Exception:
            pass
        snap_date, snap_skus = None, 0
        try:
            snap = conn.execute(text("SELECT MAX(snapshot_date),COUNT(DISTINCT sku) FROM stock_snapshots")).fetchone()
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


@app.get("/api/overview")
async def overview_summary(
    start: str = None, end: str = None,
    preset: str = "l4w"
):
    engine = get_engine()
    with engine.connect() as conn:
        mx = conn.execute(text("SELECT MAX(invoice_date) FROM sales")).fetchone()
        max_date = mx[0] if mx and mx[0] else None
        if not max_date:
            return {"error":"No sales data","kpis":{},"comparison":{},"trend":[],"top_products":[],"period":{}}

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
        prev_end = dt_start - timedelta(days=1)
        prev_start = prev_end - timedelta(days=period_days - 1)

        # KPIs
        kpi = conn.execute(text("""
            SELECT COALESCE(SUM(quantity),0), COALESCE(SUM(amount),0), COUNT(DISTINCT sku)
            FROM sales WHERE invoice_date >= :s AND invoice_date <= :e
        """), {"s": dt_start, "e": dt_end}).fetchone()
        total_sales = int(kpi[0]); revenue = float(kpi[1]); active = int(kpi[2])
        avg_weekly = round(total_sales / period_weeks)

        # Trending up
        mid = dt_start + timedelta(days=period_days // 2)
        trending = (0,)
        try:
            trending = conn.execute(text("""
                WITH fh AS (SELECT sku,SUM(quantity) q FROM sales WHERE invoice_date>=:s AND invoice_date<:m GROUP BY sku),
                     sh AS (SELECT sku,SUM(quantity) q FROM sales WHERE invoice_date>=:m AND invoice_date<=:e GROUP BY sku)
                SELECT COUNT(*) FROM sh JOIN fh ON sh.sku=fh.sku WHERE fh.q>0 AND sh.q>fh.q
            """), {"s": dt_start, "m": mid, "e": dt_end}).fetchone()
        except Exception:
            trending = (0,)

        # Previous period KPIs
        prev = conn.execute(text("""
            SELECT COALESCE(SUM(quantity),0), COALESCE(SUM(amount),0), COUNT(DISTINCT sku)
            FROM sales WHERE invoice_date >= :s AND invoice_date <= :e
        """), {"s": prev_start, "e": prev_end}).fetchone()
        p_sales = int(prev[0]); p_rev = float(prev[1]); p_active = int(prev[2])
        p_avg = round(p_sales / period_weeks) if period_weeks else 0

        def pchg(c, p):
            return round((c - p) / p * 100, 1) if p else None

        # Trend chart (auto-aggregate)
        if period_days <= 14:
            agg = "day"
            tq = text("SELECT invoice_date::date d,SUM(quantity) q,SUM(amount) r FROM sales WHERE invoice_date>=:s AND invoice_date<=:e GROUP BY d ORDER BY d")
        elif period_days <= 90:
            agg = "week"
            tq = text("SELECT DATE_TRUNC('week',invoice_date)::date d,SUM(quantity) q,SUM(amount) r FROM sales WHERE invoice_date>=:s AND invoice_date<=:e GROUP BY d ORDER BY d")
        else:
            agg = "month"
            tq = text("SELECT DATE_TRUNC('month',invoice_date)::date d,SUM(quantity) q,SUM(amount) r FROM sales WHERE invoice_date>=:s AND invoice_date<=:e GROUP BY d ORDER BY d")
        trend = conn.execute(tq, {"s": dt_start, "e": dt_end}).fetchall()

        # Top 10 with growth
        top10 = conn.execute(text("""
            SELECT s.sku,p.name,SUM(s.quantity) q,SUM(s.amount) r
            FROM sales s JOIN products p ON s.sku=p.sku
            WHERE s.invoice_date>=:s AND s.invoice_date<=:e
            GROUP BY s.sku,p.name ORDER BY q DESC LIMIT 10
        """), {"s": dt_start, "e": dt_end}).fetchall()
        top_g = []
        for r in top10:
            pq = conn.execute(text("SELECT COALESCE(SUM(quantity),0) FROM sales WHERE sku=:k AND invoice_date>=:s AND invoice_date<=:e"),
                              {"k": r[0], "s": prev_start, "e": prev_end}).fetchone()
            top_g.append({"sku":r[0],"name":r[1],"qty":int(r[2]),"revenue":float(r[3]),"growth":pchg(int(r[2]),int(pq[0]))})

    return {
        "period": {"start":str(dt_start),"end":str(dt_end),"days":period_days,"aggregation":agg},
        "prev_period": {"start":str(prev_start),"end":str(prev_end)},
        "kpis": {"total_sales":total_sales,"revenue":revenue,"avg_weekly_sales":avg_weekly,"active_products":active,"trending_up":trending[0] if trending else 0},
        "comparison": {"total_sales":pchg(total_sales,p_sales),"revenue":pchg(revenue,p_rev),"avg_weekly_sales":pchg(avg_weekly,p_avg),"active_products":pchg(active,p_active)},
        "trend": [{"date":str(r[0]),"quantity":int(r[1]),"revenue":float(r[2])} for r in trend],
        "top_products": top_g,
    }


@app.get("/api/weekly-analysis")
async def weekly_analysis():
    engine = get_engine()
    with engine.connect() as conn:
        df = pd.read_sql(text("""
            SELECT s.sku, p.name as product_name,
                   DATE_TRUNC('week', s.invoice_date)::date as week_start,
                   SUM(s.quantity) as qty
            FROM sales s JOIN products p ON s.sku = p.sku
            WHERE s.invoice_date IS NOT NULL
            GROUP BY s.sku, p.name, DATE_TRUNC('week', s.invoice_date)::date
            ORDER BY s.sku, week_start
        """), conn)
    if df.empty:
        return {"results": [], "week_labels": {}}

    all_weeks = sorted(df["week_start"].unique())
    recent_4 = all_weeks[-4:] if len(all_weeks) >= 4 else all_weeks
    last_2 = all_weeks[-2:] if len(all_weeks) >= 2 else all_weeks

    results = []
    for sku in df["sku"].unique():
        sd = df[df["sku"] == sku]
        name = sd.iloc[0]["product_name"]
        wmap = dict(zip(sd["week_start"], sd["qty"]))

        w1 = int(wmap.get(last_2[-1], 0)) if len(last_2) >= 1 else 0
        w2 = int(wmap.get(last_2[-2], 0)) if len(last_2) >= 2 else 0
        change = w1 - w2
        growth = round((change / w2) * 100, 1) if w2 > 0 else (100.0 if w1 > 0 else 0.0)

        l4w = sum(int(wmap.get(w, 0)) for w in recent_4)
        avg_l4w = round(l4w / max(len(recent_4), 1), 1)
        lifetime = int(sd["qty"].sum())
        active = len(sd)
        avg_lt = math.ceil(lifetime / active) if active > 0 else 0
        diff = round(avg_l4w - avg_lt, 1)
        diff_pct = round((avg_l4w / avg_lt) * 100, 1) if avg_lt > 0 else 0.0

        trend = "up" if growth > 20 else ("down" if growth < -20 else "stable")
        spark_w = all_weeks[-8:] if len(all_weeks) >= 8 else all_weeks
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
        "total_weeks": len(all_weeks), "total_skus": len(results),
    }


@app.get("/api/coverage")
async def coverage_analysis():
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
        st = "understocked" if cov is not None and cov < 8 else ("healthy" if cov is not None and cov <= 20 else ("overstocked" if cov is not None else "no_sales"))

        oos = 0
        for w in reversed(all_weeks):
            if wmap.get(w, 0) == 0:
                oos += 1
            else:
                break

        results.append({"sku":sku,"product_name":name,"l4w":l4w,"avg_l4w":avg,
                        "current_stock":cs,"ongoing_po":op,"coverage":cov,
                        "status":st,"weeks_oos":oos})

    results.sort(key=lambda x: x["coverage"] if x["coverage"] is not None else 9999)
    return {
        "results": results,
        "stock_date": str(snap[0]) if snap and snap[0] else None,
        "has_stock": len(stk) > 0, "total_skus": len(results),
    }


# ── Forecast API ──────────────────────────────────────────────

@app.post("/api/forecast/run")
async def forecast_run(periods: int = 2):
    engine = get_engine()
    result = run_forecast(engine, forecast_periods=periods)
    if "error" in result:
        raise HTTPException(400, result["error"])
    saved = save_forecast_to_db(engine, result)
    forecast_cache["latest"] = result
    return {"status": "success", "summary": result["summary"], "saved_to_db": saved}


@app.get("/api/forecast/results")
async def forecast_results(limit: int = 100, sort: str = "volume"):
    if "latest" in forecast_cache:
        data = forecast_cache["latest"]
    else:
        engine = get_engine()
        data = run_forecast(engine, forecast_periods=2)
        forecast_cache["latest"] = data
    results = data.get("results", [])
    if sort == "mape":
        results.sort(key=lambda x: x["mape"])
    elif sort == "trend":
        results.sort(key=lambda x: abs(x["trend"]), reverse=True)
    return {"summary": data.get("summary", {}), "results": results[:limit], "total": len(results)}


@app.get("/api/forecast/sku/{sku}")
async def forecast_sku(sku: str):
    if "latest" not in forecast_cache:
        engine = get_engine()
        forecast_cache["latest"] = run_forecast(engine, forecast_periods=2)
    results = forecast_cache["latest"].get("results", [])
    match = next((r for r in results if r["sku"] == sku), None)
    if not match:
        raise HTTPException(404, f"SKU {sku} tidak ditemukan")
    return match


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)