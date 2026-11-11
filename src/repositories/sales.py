"""Sales data access: table layout shared by the ETL and the food archive, and the sales reads used by
the inventory build. `type_filter` is the whitelisted fragment from repositories.products._type_filter."""

import csv
import io

import pandas as pd
from psycopg2.extras import execute_values
from sqlalchemy import text

from src.core.database import get_raw_conn


SALES_COLS = ("sku, invoice_date, quantity, unit, amount, cogs_accurate, gpao_accurate, "
              "gpao_pct_acc, week_label, month_label, quarter_label, year_val")


def last_sale_date(conn):
    return conn.execute(text("SELECT MAX(invoice_date) FROM sales")).scalar()


def recent_sales_weeks(conn, type_filter, n):
    """The last n sales weeks (Monday dates) that have sales of the product type, oldest first."""
    return sorted(r[0] for r in conn.execute(text(f"""
            SELECT DISTINCT DATE_TRUNC('week', s.invoice_date)::date AS w
            FROM sales s JOIN products p ON s.sku = p.sku
            WHERE s.invoice_date IS NOT NULL {type_filter}
            ORDER BY w DESC LIMIT :n
        """), {"n": n}).fetchall())


def sold_skus(conn, type_filter):
    """[(sku, name)] of every SKU of the product type that has ever sold, by SKU."""
    return conn.execute(text(f"""
            SELECT s.sku, MIN(p.name)
            FROM sales s JOIN products p ON s.sku = p.sku
            WHERE s.invoice_date IS NOT NULL {type_filter}
            GROUP BY s.sku ORDER BY s.sku
        """)).fetchall()


def weekly_sales_since(conn, type_filter, week0):
    """[(sku, week, qty, amount)] from week0 on."""
    return conn.execute(text(f"""
            SELECT s.sku, DATE_TRUNC('week', s.invoice_date)::date AS w, SUM(s.quantity), SUM(s.amount)
            FROM sales s JOIN products p ON s.sku = p.sku
            WHERE s.invoice_date >= :w0 {type_filter}
            GROUP BY s.sku, w
        """), {"w0": week0}).fetchall()


def avg_selling_price_12w(conn):
    """{sku: revenue / units} over the 12 weeks before the last sale date."""
    return {r[0]: float(r[1]) for r in conn.execute(text("""
            WITH mx AS (SELECT MAX(invoice_date) d FROM sales)
            SELECT sku, SUM(amount) / NULLIF(SUM(quantity), 0) FROM sales, mx
            WHERE invoice_date > mx.d - 84 AND quantity > 0 GROUP BY sku
        """)).fetchall() if r[1] is not None}


def import_revenue_ranking(conn):
    """[(sku, name, revenue, units, first sale, last sale)] of import SKUs, highest revenue first
    (the ranking the Pareto 80% cut is made on)."""
    return conn.execute(text("""
            SELECT s.sku, MIN(p.name), SUM(s.amount), SUM(s.quantity), MIN(s.invoice_date), MAX(s.invoice_date)
            FROM sales s JOIN products p ON p.sku = s.sku
            WHERE s.invoice_date IS NOT NULL AND COALESCE(p.product_type, 'import') = 'import'
            GROUP BY s.sku ORDER BY SUM(s.amount) DESC, s.sku
        """)).fetchall()


def weekly_for_sku(conn, sku, after):
    """[(week, units, revenue)] for one SKU after `after`."""
    return conn.execute(text("""
            SELECT DATE_TRUNC('week', invoice_date)::date w, SUM(quantity), SUM(amount)
            FROM sales WHERE sku=:s AND invoice_date > :a GROUP BY w ORDER BY w
        """), {"s": sku, "a": after}).fetchall()


def rows_in_range(conn, d1, d2):
    """Stored sales rows (incl. archived food rows) dated d1..d2: what an upload of that range replaces."""
    return int(conn.execute(text(
        "SELECT (SELECT COUNT(*) FROM sales WHERE invoice_date BETWEEN :a AND :b)"
        " + (SELECT COUNT(*) FROM sales_excluded WHERE reason = 'food' AND invoice_date BETWEEN :a AND :b)"),
        {"a": d1, "b": d2}).scalar() or 0)


def import_sales(prod_rows, sales, d1=None, d2=None):
    """Sales ETL write, one transaction:
    1. upsert products [(sku, name, product_type or None)] (type kept when None),
    2. COPY the cleaned rows into a temp staging table,
    3. food products' rows go to sales_excluded (reason 'food') and leave the stage,
    4. when the file's date range d1..d2 is given, delete the stored rows of that range first (sales and
       archived food rows), so an upload replaces its period instead of adding to it. Re-uploading a
       corrected export therefore fixes rows that were matched to the wrong SKU before,
    5. insert the rest into sales.
    `sales` is the DataFrame with the SALES_COLS columns. Returns (inserted, food rows archived, rows replaced)."""
    conn = get_raw_conn()
    conn.autocommit = False
    cur = conn.cursor()
    try:
        # One statement for all products (was one network round trip per product)
        execute_values(cur, """
                INSERT INTO products (sku, name, product_type) VALUES %s
                ON CONFLICT (sku) DO UPDATE SET name = EXCLUDED.name,
                    product_type = COALESCE(EXCLUDED.product_type, products.product_type),
                    updated_at = NOW()
            """, prod_rows, page_size=1000)
        # ── Append mode: load into temp table, then INSERT ... ON CONFLICT DO NOTHING ──
        cur.execute("""
                CREATE TEMP TABLE _sales_stage (LIKE sales INCLUDING DEFAULTS)
                ON COMMIT DROP
            """)
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
        cur.copy_from(buffer, '_sales_stage', sep='\t', null=r'\N',
                      columns=('sku','invoice_date','quantity','unit','amount',
                               'cogs_accurate','gpao_accurate','gpao_pct_acc',
                               'week_label','month_label','quarter_label','year_val'))
        # Food products never enter the sales table: their rows go to the archive (reason 'food').
        replaced = 0
        if d1 and d2:
            cur.execute("DELETE FROM sales WHERE invoice_date BETWEEN %s AND %s", (d1, d2))
            replaced = cur.rowcount
            cur.execute("DELETE FROM sales_excluded WHERE reason = 'food' AND invoice_date BETWEEN %s AND %s", (d1, d2))
            replaced += cur.rowcount
        food_list = [p_[0] for p_ in prod_rows if p_[2] == "food"]
        food_rows = 0
        if food_list:
            cur.execute("""
                    INSERT INTO sales_excluded (sku, invoice_date, quantity, unit, amount, cogs_accurate,
                        gpao_accurate, gpao_pct_acc, week_label, month_label, quarter_label, year_val, reason)
                    SELECT sku, invoice_date, quantity, unit, amount, cogs_accurate, gpao_accurate, gpao_pct_acc,
                           week_label, month_label, quarter_label, year_val, 'food'
                    FROM _sales_stage WHERE sku = ANY(%s)
                """, (food_list,))
            food_rows = cur.rowcount
            cur.execute("DELETE FROM _sales_stage WHERE sku = ANY(%s)", (food_list,))
        # Deduplicate: skip rows that already exist (same sku + date + qty + amount)
        cur.execute("""
                INSERT INTO sales (sku, invoice_date, quantity, unit, amount,
                                   cogs_accurate, gpao_accurate, gpao_pct_acc,
                                   week_label, month_label, quarter_label, year_val)
                SELECT s.sku, s.invoice_date, s.quantity, s.unit, s.amount,
                       s.cogs_accurate, s.gpao_accurate, s.gpao_pct_acc,
                       s.week_label, s.month_label, s.quarter_label, s.year_val
                FROM _sales_stage s
                WHERE NOT EXISTS (
                    SELECT 1 FROM sales e
                    WHERE e.sku = s.sku
                      AND e.invoice_date = s.invoice_date
                      AND e.quantity = s.quantity
                      AND e.amount = s.amount
                )
            """)
        inserted = cur.rowcount
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        cur.close()
        conn.close()
    return inserted, food_rows, replaced
