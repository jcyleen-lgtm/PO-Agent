"""
PO Agent — Web Server
FastAPI backend for ETL upload, data preview, and database loading.
"""

from dotenv import load_dotenv
load_dotenv()

import os
import uuid
import json
from datetime import datetime

from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware

import pandas as pd
import numpy as np
from sqlalchemy import create_engine, text

from etl import clean_sales_data, load_master_sku

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

# Load master SKU mapping at startup
master_sku = load_master_sku(MASTER_SKU_PATH)
print(f"[startup] Master SKU loaded: {len(master_sku)} entries")

# Store processed data temporarily
processed_cache = {}


def get_engine():
    if not DB_URL:
        raise HTTPException(500, "SUPABASE_DB_URL not configured")
    return create_engine(DB_URL)


# ── API Routes ────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def index():
    with open("templates/index.html", "r", encoding="utf-8") as f:
        return f.read()


@app.post("/api/master-sku/upload")
async def upload_master_sku(file: UploadFile = File(...)):
    """Upload/update Master SKU file."""
    global master_sku
    content = await file.read()
    with open(MASTER_SKU_PATH, "wb") as f:
        f.write(content)
    master_sku = load_master_sku(MASTER_SKU_PATH)
    return {"status": "success", "entries": len(master_sku)}


@app.post("/api/etl/upload")
async def etl_upload(file: UploadFile = File(...)):
    """Upload raw Accurate Excel, run ETL, return preview."""

    if not file.filename.endswith((".xls", ".xlsx")):
        raise HTTPException(400, "Format file harus .xls atau .xlsx")

    file_id = str(uuid.uuid4())[:8]
    ext = os.path.splitext(file.filename)[1]
    save_path = os.path.join(UPLOAD_DIR, f"{file_id}{ext}")

    content = await file.read()
    with open(save_path, "wb") as f:
        f.write(content)

    # Run ETL with master SKU lookup
    result = clean_sales_data(save_path, master_sku=master_sku)

    if result["errors"]:
        os.remove(save_path)
        raise HTTPException(400, detail={"errors": result["errors"]})

    df = result["df"]

    processed_cache[file_id] = {
        "df": df,
        "filepath": save_path,
        "filename": file.filename,
        "created_at": datetime.now().isoformat(),
    }

    preview_rows = df.head(20).copy()
    for col in preview_rows.columns:
        if preview_rows[col].dtype == "datetime64[ns]":
            preview_rows[col] = preview_rows[col].dt.strftime("%Y-%m-%d")
    preview_rows = preview_rows.fillna("").to_dict("records")

    return {
        "file_id": file_id,
        "filename": file.filename,
        "stats": result["stats"],
        "preview": preview_rows,
        "columns": list(df.columns),
    }


@app.post("/api/etl/confirm/{file_id}")
async def etl_confirm(file_id: str):
    """Confirm and load cleaned data into PostgreSQL."""

    if file_id not in processed_cache:
        raise HTTPException(404, "Data tidak ditemukan. Upload ulang.")

    cached = processed_cache[file_id]
    df = cached["df"]
    engine = get_engine()

    try:
        # ── Upsert products ──────────────────────────────────
        products = df[["SKU", "product_name"]].drop_duplicates(subset=["SKU"])
        products = products.groupby("SKU").first().reset_index()

        with engine.begin() as conn:
            for _, row in products.iterrows():
                conn.execute(text("""
                    INSERT INTO products (sku, name)
                    VALUES (:sku, :name)
                    ON CONFLICT (sku) DO UPDATE SET
                        name = EXCLUDED.name,
                        updated_at = NOW()
                """), {"sku": str(row["SKU"]), "name": row["product_name"]})

        # ── Prepare sales data ───────────────────────────────
        sales = df.copy()
        sales = sales.rename(columns={
            "SKU": "sku",
            "date": "invoice_date",
            "quantity": "quantity",
            "amount": "amount",
            "COGS": "cogs_accurate",
            "GPAO": "gpao_accurate",
            "GPAO (%)": "gpao_pct_acc",
            "week": "week_label",
            "month": "month_label",
            "quarter": "quarter_label",
            "year": "year_val",
        })

        sales["sku"] = sales["sku"].astype(str)

        if "invoice_date" in sales.columns:
            sales["invoice_date"] = pd.to_datetime(sales["invoice_date"]).dt.date

        for col in ["cogs_accurate", "unit"]:
            if col not in sales.columns:
                sales[col] = None

        if "gpao_pct_acc" in sales.columns:
            def parse_gpao_pct(val):
                if pd.isna(val):
                    return None
                if isinstance(val, str):
                    return float(val.replace("%", "").strip()) / 100 if "%" in val else float(val)
                return float(val)
            sales["gpao_pct_acc"] = sales["gpao_pct_acc"].apply(parse_gpao_pct)

        keep_cols = [
            "sku", "invoice_date", "quantity", "unit", "amount",
            "cogs_accurate", "gpao_accurate", "gpao_pct_acc",
            "week_label", "month_label", "quarter_label", "year_val"
        ]
        for col in keep_cols:
            if col not in sales.columns:
                sales[col] = None
        sales = sales[keep_cols]
        sales = sales.where(pd.notna(sales), None)

        # ── Insert to database ───────────────────────────────
        BATCH_SIZE = 5000
        total = 0

        with engine.begin() as conn:
            conn.execute(text("TRUNCATE TABLE sales RESTART IDENTITY"))

            for i in range(0, len(sales), BATCH_SIZE):
                batch = sales.iloc[i:i + BATCH_SIZE]
                records = batch.to_dict("records")
                conn.execute(text("""
                    INSERT INTO sales (
                        sku, invoice_date, quantity, unit, amount,
                        cogs_accurate, gpao_accurate, gpao_pct_acc,
                        week_label, month_label, quarter_label, year_val
                    ) VALUES (
                        :sku, :invoice_date, :quantity, :unit, :amount,
                        :cogs_accurate, :gpao_accurate, :gpao_pct_acc,
                        :week_label, :month_label, :quarter_label, :year_val
                    )
                """), records)
                total += len(batch)

        # ── Validate ─────────────────────────────────────────
        with engine.connect() as conn:
            result = conn.execute(text("""
                SELECT
                    (SELECT COUNT(*) FROM products) as products,
                    (SELECT COUNT(*) FROM sales) as sales,
                    (SELECT MIN(invoice_date) FROM sales) as date_min,
                    (SELECT MAX(invoice_date) FROM sales) as date_max,
                    (SELECT COUNT(DISTINCT sku) FROM sales) as unique_skus
            """))
            row = result.fetchone()

        if os.path.exists(cached["filepath"]):
            os.remove(cached["filepath"])
        del processed_cache[file_id]

        return {
            "status": "success",
            "message": f"{total:,} baris berhasil dimuat ke database",
            "database": {
                "products": row[0],
                "sales": row[1],
                "date_range": f"{row[2]} s/d {row[3]}",
                "unique_skus": row[4],
            }
        }

    except Exception as e:
        raise HTTPException(500, f"Gagal memuat data: {str(e)}")


@app.get("/api/db/stats")
async def db_stats():
    """Get current database statistics."""
    engine = get_engine()
    with engine.connect() as conn:
        result = conn.execute(text("""
            SELECT
                (SELECT COUNT(*) FROM products) as products,
                (SELECT COUNT(*) FROM sales) as sales,
                (SELECT MIN(invoice_date) FROM sales) as date_min,
                (SELECT MAX(invoice_date) FROM sales) as date_max,
                (SELECT COUNT(DISTINCT sku) FROM sales) as unique_skus,
                (SELECT COUNT(*) FROM master_cogs) as cogs_entries,
                (SELECT COUNT(*) FROM margin_results) as margin_entries
        """))
        row = result.fetchone()

    return {
        "products": row[0],
        "sales": row[1],
        "date_range": f"{row[2]} s/d {row[3]}" if row[2] else "Belum ada data",
        "unique_skus": row[4],
        "cogs_entries": row[5],
        "margin_entries": row[6],
    }


# ── Run ───────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
