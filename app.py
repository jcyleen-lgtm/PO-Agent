"""
PO Agent — Web Server
FastAPI backend for ETL, Forecasting, and Dashboard.
"""

from dotenv import load_dotenv
load_dotenv()

import os
import io
import uuid
import csv
from datetime import datetime

from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware

import pandas as pd
import numpy as np
from sqlalchemy import create_engine, text
import psycopg2

from etl import clean_sales_data, load_master_sku
from forecast import run_forecast, save_forecast_to_db

# ── Config ────────────────────────────────────────────────────
DB_URL = os.getenv("SUPABASE_DB_URL")
UPLOAD_DIR = "uploads"
MASTER_SKU_PATH = "MASTER_SKU.xlsx"
os.makedirs(UPLOAD_DIR, exist_ok=True)

app = FastAPI(title="PO Agent", version="1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

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
                          columns=('sku', 'invoice_date', 'quantity', 'unit', 'amount',
                                   'cogs_accurate', 'gpao_accurate', 'gpao_pct_acc',
                                   'week_label', 'month_label', 'quarter_label', 'year_val'))
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
                SELECT (SELECT COUNT(*) FROM products), (SELECT COUNT(*) FROM sales),
                       (SELECT MIN(invoice_date) FROM sales), (SELECT MAX(invoice_date) FROM sales),
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


# ── Forecast API ──────────────────────────────────────────────

@app.post("/api/forecast/run")
async def forecast_run(periods: int = 4):
    """Run Holt's DES forecast for all SKUs."""
    engine = get_engine()
    result = run_forecast(engine, forecast_periods=periods)
    
    if "error" in result:
        raise HTTPException(400, result["error"])
    
    # Save to DB
    saved = save_forecast_to_db(engine, result)
    
    # Cache for quick access
    forecast_cache["latest"] = result
    
    return {
        "status": "success",
        "summary": result["summary"],
        "saved_to_db": saved,
    }


@app.get("/api/forecast/results")
async def forecast_results(limit: int = 50, sort: str = "volume"):
    """Get forecast results. Sort by: volume, mape, trend."""
    # Try cache first
    if "latest" in forecast_cache:
        data = forecast_cache["latest"]
    else:
        # Load from DB
        engine = get_engine()
        data = run_forecast(engine)
        forecast_cache["latest"] = data
    
    results = data.get("results", [])
    
    if sort == "mape":
        results.sort(key=lambda x: x["mape"])
    elif sort == "trend":
        results.sort(key=lambda x: abs(x["trend"]), reverse=True)
    
    return {
        "summary": data.get("summary", {}),
        "results": results[:limit],
        "total": len(results),
    }


@app.get("/api/forecast/sku/{sku}")
async def forecast_sku(sku: str):
    """Get detailed forecast for one SKU."""
    if "latest" not in forecast_cache:
        engine = get_engine()
        forecast_cache["latest"] = run_forecast(engine)
    
    results = forecast_cache["latest"].get("results", [])
    match = next((r for r in results if r["sku"] == sku), None)
    
    if not match:
        raise HTTPException(404, f"SKU {sku} tidak ditemukan dalam forecast")
    
    return match


# ── Dashboard API ─────────────────────────────────────────────

@app.get("/api/db/stats")
async def db_stats():
    engine = get_engine()
    with engine.connect() as conn:
        result = conn.execute(text("""
            SELECT
                (SELECT COUNT(*) FROM products),
                (SELECT COUNT(*) FROM sales),
                (SELECT MIN(invoice_date) FROM sales),
                (SELECT MAX(invoice_date) FROM sales),
                (SELECT COUNT(DISTINCT sku) FROM sales),
                (SELECT COUNT(*) FROM master_cogs),
                (SELECT COUNT(*) FROM margin_results),
                (SELECT COUNT(*) FROM forecast_results)
        """))
        row = result.fetchone()
    return {
        "products": row[0], "sales": row[1],
        "date_range": f"{row[2]} s/d {row[3]}" if row[2] else "Belum ada data",
        "unique_skus": row[4], "cogs_entries": row[5],
        "margin_entries": row[6], "forecast_entries": row[7],
    }


@app.get("/api/dashboard/summary")
async def dashboard_summary():
    """Dashboard KPI summary."""
    engine = get_engine()
    
    with engine.connect() as conn:
        # Sales stats
        sales = conn.execute(text("""
            SELECT COUNT(*) as total_txn,
                   COUNT(DISTINCT sku) as unique_skus,
                   SUM(amount) as total_revenue,
                   MIN(invoice_date) as date_min,
                   MAX(invoice_date) as date_max
            FROM sales
        """)).fetchone()
        
        # Monthly trend (last 6 months)
        monthly = conn.execute(text("""
            SELECT DATE_TRUNC('month', invoice_date) as month,
                   SUM(quantity) as qty, SUM(amount) as revenue,
                   COUNT(DISTINCT sku) as active_skus
            FROM sales
            WHERE invoice_date >= (SELECT MAX(invoice_date) - INTERVAL '6 months' FROM sales)
            GROUP BY month ORDER BY month
        """)).fetchall()
        
        # Top 10 products by revenue
        top = conn.execute(text("""
            SELECT s.sku, p.name, SUM(s.quantity) as total_qty,
                   SUM(s.amount) as total_revenue, COUNT(*) as txn_count
            FROM sales s JOIN products p ON s.sku = p.sku
            GROUP BY s.sku, p.name
            ORDER BY total_revenue DESC LIMIT 10
        """)).fetchall()
        
        # Forecast summary
        fc = conn.execute(text("""
            SELECT COUNT(*) as total, AVG(mape) as avg_mape
            FROM forecast_results
        """)).fetchone()
    
    return {
        "sales": {
            "total_transactions": sales[0],
            "unique_skus": sales[1],
            "total_revenue": float(sales[2]) if sales[2] else 0,
            "date_range": f"{sales[3]} s/d {sales[4]}" if sales[3] else "-",
        },
        "monthly_trend": [{
            "month": str(r[0].strftime("%Y-%m")),
            "quantity": int(r[1]),
            "revenue": float(r[2]),
            "active_skus": int(r[3]),
        } for r in monthly],
        "top_products": [{
            "sku": r[0], "name": r[1], "qty": int(r[2]),
            "revenue": float(r[3]), "transactions": int(r[4]),
        } for r in top],
        "forecast": {
            "total_forecasted": fc[0] if fc[0] else 0,
            "avg_mape": round(float(fc[1]), 2) if fc[1] else 0,
        },
    }


# ── Run ───────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)