"""Runtime DDL kept from app.py: additive, idempotent CREATE/ALTER for tables and columns that were
added after the original schema. Real schema changes go through migrations (docs/database.md §5);
these stay as fallbacks so an older database keeps working."""

from sqlalchemy import text



def ensure_hero_table(conn):
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS hero_skus (
            sku VARCHAR(50) PRIMARY KEY,
            group_name VARCHAR(160),
            status VARCHAR(60),
            source_code VARCHAR(160),
            product_name TEXT,
            uploaded_at TIMESTAMPTZ DEFAULT NOW()
        )"""))


def ensure_food_tables(conn):
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS food_skus (
            code     VARCHAR(80) PRIMARY KEY,
            name     TEXT,
            added_at TIMESTAMPTZ DEFAULT NOW()
        )"""))


def ensure_sales_excluded(conn):
    conn.execute(text(f"""
        CREATE TABLE IF NOT EXISTS sales_excluded (
            id BIGSERIAL PRIMARY KEY, sku VARCHAR(255), invoice_date DATE, quantity INTEGER, unit VARCHAR(20),
            amount NUMERIC, cogs_accurate NUMERIC, gpao_accurate NUMERIC, gpao_pct_acc NUMERIC,
            week_label VARCHAR(30), month_label VARCHAR(20), quarter_label VARCHAR(30), year_val SMALLINT,
            created_at TIMESTAMPTZ DEFAULT NOW(), excluded_at TIMESTAMPTZ DEFAULT NOW(), reason VARCHAR(40))"""))


_PO_COLS_READY = False


def ensure_po_columns(conn):
    """Additive, idempotent: extra PO fields used by the PO master import (existing rows untouched).
    ALTER TABLE takes an exclusive lock even when nothing changes, so it only runs when a column is
    actually missing (checked once per server instance)."""
    global _PO_COLS_READY
    if _PO_COLS_READY:
        return
    have = {r[0] for r in conn.execute(text(
        "SELECT column_name FROM information_schema.columns WHERE table_name = 'purchase_orders'"))}
    idx = conn.execute(text("SELECT 1 FROM pg_indexes WHERE indexname = 'ux_po_line_key'")).fetchone()
    if {"po_number", "line_key", "description", "courier"} <= have and idx:
        _PO_COLS_READY = True
        return
    conn.execute(text("""
        ALTER TABLE purchase_orders
            ADD COLUMN IF NOT EXISTS po_number VARCHAR(60),
            ADD COLUMN IF NOT EXISTS line_key VARCHAR(160),
            ADD COLUMN IF NOT EXISTS description TEXT,
            ADD COLUMN IF NOT EXISTS courier VARCHAR(60)"""))
    conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ux_po_line_key ON purchase_orders (line_key) WHERE line_key IS NOT NULL"))
    _PO_COLS_READY = True


def reset():
    """Forget the 'PO columns already checked' flag (tests rebuild the schema)."""
    global _PO_COLS_READY
    _PO_COLS_READY = False
