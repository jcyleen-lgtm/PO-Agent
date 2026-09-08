"""
PO Agent — Migration Script
Ingests sales data (87k+ rows) and Master COGS into PostgreSQL (Supabase).

Usage:
    1. Set SUPABASE_DB_URL environment variable
    2. Run: python migrate.py

Requires: pip install pandas openpyxl psycopg2-binary sqlalchemy
"""

from dotenv import load_dotenv
load_dotenv()

import os
import sys
import pandas as pd
import numpy as np
from sqlalchemy import create_engine, text
from datetime import datetime

# ============================================================
# CONFIG
# ============================================================

DB_URL = os.getenv("SUPABASE_DB_URL")
# Format: postgresql://postgres.[project]:[password]@aws-0-[region].pooler.supabase.com:6543/postgres

SALES_FILE = "SALES_2025-2026.xlsx"
MARGIN_FILE = "MARGIN_IMPOR.xlsx"

# ============================================================
# HELPERS
# ============================================================

def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")

def connect_db():
    if not DB_URL:
        print("ERROR: Set SUPABASE_DB_URL environment variable.")
        print("Format: postgresql://postgres.[project]:[password]@aws-0-[region].pooler.supabase.com:6543/postgres")
        sys.exit(1)
    engine = create_engine(DB_URL, echo=False)
    # Test connection
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))
    log("Connected to database.")
    return engine

def run_schema(engine):
    """Execute schema.sql to create all tables."""
    schema_path = os.path.join(os.path.dirname(__file__), "schema.sql")
    with open(schema_path) as f:
        sql = f.read()
    # Make idempotent
    sql = sql.replace("CREATE TABLE ", "CREATE TABLE IF NOT EXISTS ")
    sql = sql.replace("CREATE INDEX ", "CREATE INDEX IF NOT EXISTS ")
    sql = sql.replace("INSERT INTO marketplace_fees", "INSERT INTO marketplace_fees")
    with engine.begin() as conn:
        conn.execute(text(sql))
    log("Schema created/updated.")

# ============================================================
# INGEST: SALES DATA
# ============================================================

def ingest_sales(engine, filepath):
    """Load sales Excel and insert into products + sales tables."""
    log(f"Reading {filepath}...")
    df = pd.read_excel(filepath, engine="openpyxl")
    log(f"  Loaded {len(df):,} rows, {df['SKU'].nunique()} unique SKUs.")

    # ---- Step 1: Upsert products ----
    log("  Upserting products...")
    products = df[["SKU", "product_name"]].drop_duplicates(subset=["SKU"])
    # For SKU "0" (dummy), keep all unique product names as separate entries
    # by appending a suffix. But for now, take the first name per SKU.
    products = products.groupby("SKU").first().reset_index()
    products.columns = ["sku", "name"]

    with engine.begin() as conn:
        for _, row in products.iterrows():
            conn.execute(text("""
                INSERT INTO products (sku, name)
                VALUES (:sku, :name)
                ON CONFLICT (sku) DO UPDATE SET
                    name = EXCLUDED.name,
                    updated_at = NOW()
            """), {"sku": str(row["sku"]), "name": row["name"]})
    log(f"  {len(products)} products upserted.")

    # ---- Step 2: Insert sales ----
    log("  Preparing sales data...")
    sales = df.rename(columns={
        "SKU": "sku",
        "product_name": "_drop",
        "date": "invoice_date",
        "quantity": "quantity",
        "amount": "amount",
        "GPAO": "gpao_accurate",
        "GPAO (%)": "gpao_pct_acc",
        "week": "week_label",
        "month": "month_label",
        "quarter": "quarter_label",
        "year": "year_val",
    }).drop(columns=["_drop"], errors="ignore")

    # Clean types
    sales["sku"] = sales["sku"].astype(str)
    sales["invoice_date"] = pd.to_datetime(sales["invoice_date"]).dt.date
    sales["quantity"] = sales["quantity"].astype(int)
    sales["amount"] = pd.to_numeric(sales["amount"], errors="coerce").fillna(0)
    sales["cogs_accurate"] = None
    sales["unit"] = None
    sales["gpao_accurate"] = pd.to_numeric(sales["gpao_accurate"], errors="coerce")

    # Handle GPAO (%) which may be string like "4.17%" or numeric
    def parse_gpao_pct(val):
        if pd.isna(val):
            return None
        if isinstance(val, str):
            return float(val.replace("%", "").strip()) / 100 if "%" in val else float(val)
        return float(val)

    sales["gpao_pct_acc"] = sales["gpao_pct_acc"].apply(parse_gpao_pct)

    # Select only needed columns
    cols = [
        "sku", "invoice_date", "quantity", "unit", "amount",
        "cogs_accurate", "gpao_accurate", "gpao_pct_acc",
        "week_label", "month_label", "quarter_label", "year_val"
    ]
    sales = sales[cols]

    # Replace NaN with None for SQL
    sales = sales.where(pd.notna(sales), None)

    # Batch insert
    log(f"  Inserting {len(sales):,} sales rows (batch)...")
    BATCH_SIZE = 5000
    total = 0
    with engine.begin() as conn:
        # Clear existing sales to prevent duplicates on re-run
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
            log(f"    {total:,} / {len(sales):,} inserted...")

    log(f"  Sales ingestion complete: {total:,} rows.")

# ============================================================
# INGEST: MASTER COGS
# ============================================================

def ingest_master_cogs(engine, filepath):
    """Load Master COGS sheet and insert into master_cogs + products."""
    log(f"Reading Master COGS from {filepath}...")
    df = pd.read_excel(filepath, sheet_name="Master COGS", engine="openpyxl")
    log(f"  Loaded {len(df)} rows.")

    df = df.rename(columns={
        "SKU": "sku",
        "PRODUCT": "name",
        "GROUP": "grp",
        "CAT": "category",
        "CBP": "hpp",
        "SF": "shipping_fee",
        "COGS": "_cogs_check",  # we calculate this, but keep for validation
    })

    df["sku"] = df["sku"].astype(str).str.strip()
    df["hpp"] = pd.to_numeric(df["hpp"], errors="coerce").fillna(0)
    df["shipping_fee"] = pd.to_numeric(df["shipping_fee"], errors="coerce").fillna(0)
    df["category"] = pd.to_numeric(df["category"], errors="coerce")

    with engine.begin() as conn:
        for _, row in df.iterrows():
            sku = row["sku"]
            if not sku or sku == "nan":
                continue

            # Upsert product with group and category
            conn.execute(text("""
                INSERT INTO products (sku, name, grp, category)
                VALUES (:sku, :name, :grp, :cat)
                ON CONFLICT (sku) DO UPDATE SET
                    grp = COALESCE(EXCLUDED.grp, products.grp),
                    category = COALESCE(EXCLUDED.category, products.category),
                    updated_at = NOW()
            """), {
                "sku": sku,
                "name": row.get("name", ""),
                "grp": row.get("grp"),
                "cat": int(row["category"]) if pd.notna(row["category"]) else None,
            })

            # Upsert master_cogs
            conn.execute(text("""
                INSERT INTO master_cogs (sku, hpp, shipping_fee)
                VALUES (:sku, :hpp, :sf)
                ON CONFLICT (sku) DO UPDATE SET
                    hpp = EXCLUDED.hpp,
                    shipping_fee = EXCLUDED.shipping_fee,
                    updated_at = NOW()
            """), {
                "sku": sku,
                "hpp": float(row["hpp"]),
                "sf": float(row["shipping_fee"]),
            })

    log(f"  Master COGS upserted: {len(df)} SKUs.")

# ============================================================
# INGEST: SELLING PRICES (Shopee + TikTok)
# ============================================================

def ingest_selling_prices(engine, filepath):
    """Load Shopee and TikTok sheets for selling prices."""

    sheets = {
        "shopee": "Shopee",
        "tiktok": "TikTok",
    }

    for marketplace, sheet_name in sheets.items():
        log(f"Reading selling prices from sheet '{sheet_name}'...")
        try:
            df = pd.read_excel(filepath, sheet_name=sheet_name, engine="openpyxl")
        except ValueError:
            log(f"  Sheet '{sheet_name}' not found, skipping.")
            continue

        df = df.rename(columns={
            "SKU": "sku",
            "SP": "selling_price",
            "MQ": "min_qty",
        })

        df["sku"] = df["sku"].astype(str).str.strip()
        df["selling_price"] = pd.to_numeric(df["selling_price"], errors="coerce")
        df["min_qty"] = pd.to_numeric(df["min_qty"], errors="coerce").fillna(1).astype(int)

        # Filter valid rows
        df = df[df["sku"].notna() & (df["sku"] != "nan") & df["selling_price"].notna()]

        with engine.begin() as conn:
            for _, row in df.iterrows():
                conn.execute(text("""
                    INSERT INTO selling_prices (sku, marketplace, selling_price, min_qty)
                    VALUES (:sku, :mp, :sp, :mq)
                    ON CONFLICT (sku, marketplace) DO UPDATE SET
                        selling_price = EXCLUDED.selling_price,
                        min_qty = EXCLUDED.min_qty,
                        updated_at = NOW()
                """), {
                    "sku": row["sku"],
                    "mp": marketplace,
                    "sp": float(row["selling_price"]),
                    "mq": int(row["min_qty"]),
                })

        log(f"  {marketplace}: {len(df)} selling prices upserted.")

# ============================================================
# RECALCULATE ALL MARGINS
# ============================================================

def recalculate_margins(engine):
    """Call the calc_margin_global() function to recalculate all margins."""
    log("Recalculating all margins...")
    with engine.begin() as conn:
        result = conn.execute(text("SELECT calc_margin_global()"))
        count = result.scalar()
    log(f"  {count} margin entries calculated.")

# ============================================================
# VALIDATION
# ============================================================

def validate(engine):
    """Print summary stats to verify ingestion."""
    log("=" * 50)
    log("VALIDATION SUMMARY")
    log("=" * 50)

    queries = {
        "Products":             "SELECT COUNT(*) FROM products",
        "Sales rows":           "SELECT COUNT(*) FROM sales",
        "Sales date range":     "SELECT MIN(invoice_date) || ' to ' || MAX(invoice_date) FROM sales",
        "Unique SKUs in sales": "SELECT COUNT(DISTINCT sku) FROM sales",
        "Master COGS entries":  "SELECT COUNT(*) FROM master_cogs",
        "Selling prices":       "SELECT marketplace, COUNT(*) FROM selling_prices GROUP BY marketplace",
        "Margin results":       "SELECT marketplace, COUNT(*) FROM margin_results GROUP BY marketplace",
        "Marketplace fees":     "SELECT marketplace, admin_fee_pct, ads_pct FROM marketplace_fees",
    }

    with engine.connect() as conn:
        for label, query in queries.items():
            try:
                result = conn.execute(text(query))
                rows = result.fetchall()
                if len(rows) == 1 and len(rows[0]) == 1:
                    log(f"  {label}: {rows[0][0]}")
                else:
                    log(f"  {label}:")
                    for row in rows:
                        log(f"    {row}")
            except Exception as e:
                log(f"  {label}: ERROR — {e}")

# ============================================================
# MAIN
# ============================================================

def main():
    log("PO Agent — Migration Script")
    log("=" * 50)

    engine = connect_db()

    # Step 1: Create schema
    run_schema(engine)

    # Step 2: Ingest sales data
    if os.path.exists(SALES_FILE):
        ingest_sales(engine, SALES_FILE)
    else:
        log(f"WARNING: {SALES_FILE} not found, skipping sales ingestion.")

    # Step 3: Ingest Master COGS
    if os.path.exists(MARGIN_FILE):
        ingest_master_cogs(engine, MARGIN_FILE)
        ingest_selling_prices(engine, MARGIN_FILE)
    else:
        log(f"WARNING: {MARGIN_FILE} not found, skipping COGS/prices ingestion.")

    # Step 4: Recalculate margins
    recalculate_margins(engine)

    # Step 5: Validate
    validate(engine)

    log("=" * 50)
    log("Migration complete!")

if __name__ == "__main__":
    main()
