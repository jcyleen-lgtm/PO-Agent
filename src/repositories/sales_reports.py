"""Read-only sales aggregations for the Overview and the weekly analysis.

`src` is "sales s" or "sales s JOIN products p ON s.sku = p.sku"; `tf` is the whitelisted product-type
fragment (empty for all types). Both are built by the caller from fixed strings only.
"""

from sqlalchemy import text


def db_counts(conn):
    """(products, sales rows, first sale, last sale, distinct sold SKUs)."""
    return conn.execute(text("""
            SELECT (SELECT COUNT(*) FROM products),(SELECT COUNT(*) FROM sales),
                   (SELECT MIN(invoice_date) FROM sales),(SELECT MAX(invoice_date) FROM sales),
                   (SELECT COUNT(DISTINCT sku) FROM sales)
        """)).fetchone()


def max_sale_row(conn):
    return conn.execute(text("SELECT MAX(invoice_date) FROM sales")).fetchone()


def period_kpis(conn, src, tf, start, end):
    """(units, revenue, distinct SKUs) in [start, end]."""
    return conn.execute(text(f"""
            SELECT COALESCE(SUM(s.quantity),0), COALESCE(SUM(s.amount),0), COUNT(DISTINCT s.sku)
            FROM {src} WHERE s.invoice_date >= :s AND s.invoice_date <= :e {tf}
        """), {"s": start, "e": end}).fetchone()


def trending_up_count(conn, src, tf, start, mid, end):
    """SKUs that sold more in the second half of the period than in the first (first half > 0)."""
    return conn.execute(text(f"""
                WITH fh AS (SELECT s.sku,SUM(s.quantity) q FROM {src} WHERE s.invoice_date>=:s AND s.invoice_date<:m {tf} GROUP BY s.sku),
                     sh AS (SELECT s.sku,SUM(s.quantity) q FROM {src} WHERE s.invoice_date>=:m AND s.invoice_date<=:e {tf} GROUP BY s.sku)
                SELECT COUNT(*) FROM sh JOIN fh ON sh.sku=fh.sku WHERE fh.q>0 AND sh.q>fh.q
            """), {"s": start, "m": mid, "e": end}).fetchone()


_TREND_BUCKET = {"day": "s.invoice_date::date", "week": "DATE_TRUNC('week',s.invoice_date)::date",
                 "month": "DATE_TRUNC('month',s.invoice_date)::date"}


def trend(conn, src, tf, agg, start, end):
    """[(bucket date, units, revenue)] per day / week / month."""
    tq = text(f"SELECT {_TREND_BUCKET[agg]} d,SUM(s.quantity) q,SUM(s.amount) r FROM {src} WHERE s.invoice_date>=:s AND s.invoice_date<=:e {tf} GROUP BY d ORDER BY d")
    return conn.execute(tq, {"s": start, "e": end}).fetchall()


def top_products(conn, tf, order_col, start, end):
    """Top 50 SKUs in the period by revenue ('r') or units ('q')."""
    assert order_col in ("r", "q")
    return conn.execute(text(f"""
            SELECT s.sku,p.name,SUM(s.quantity) q,SUM(s.amount) r
            FROM sales s JOIN products p ON s.sku=p.sku
            WHERE s.invoice_date>=:s AND s.invoice_date<=:e {tf}
            GROUP BY s.sku,p.name ORDER BY {order_col} DESC LIMIT 50
        """), {"s": start, "e": end}).fetchall()


def qty_by_sku(conn, skus, start, end):
    return {k: v for k, v in conn.execute(text("""
                SELECT sku, COALESCE(SUM(quantity),0) FROM sales
                WHERE sku = ANY(:k) AND invoice_date>=:s AND invoice_date<=:e GROUP BY sku
            """), {"k": skus, "s": start, "e": end}).fetchall()}


def first_sale_by_sku(conn, skus):
    return {k: v for k, v in conn.execute(text("""
                SELECT sku, MIN(invoice_date) FROM sales WHERE sku = ANY(:k) GROUP BY sku
            """), {"k": skus}).fetchall()}


def last_weeks(conn, tf, n):
    """The last n sales weeks (newest first) for the product type."""
    return conn.execute(text(f"""
            SELECT DISTINCT DATE_TRUNC('week', s.invoice_date)::date AS w
            FROM sales s JOIN products p ON s.sku = p.sku
            WHERE s.invoice_date IS NOT NULL {tf}
            ORDER BY w DESC LIMIT {int(n)}
        """)).fetchall()


def lifetime_by_sku(conn, tf):
    """[(sku, name, lifetime units, active weeks)]."""
    return conn.execute(text(f"""
            SELECT s.sku, MIN(p.name), SUM(s.quantity),
                   COUNT(DISTINCT DATE_TRUNC('week', s.invoice_date))
            FROM sales s JOIN products p ON s.sku = p.sku
            WHERE s.invoice_date IS NOT NULL {tf}
            GROUP BY s.sku ORDER BY s.sku
        """)).fetchall()


def weekly_qty_since(conn, tf, week0):
    """[(sku, week, units)] from week0 on."""
    return conn.execute(text(f"""
            SELECT s.sku, DATE_TRUNC('week', s.invoice_date)::date AS w, SUM(s.quantity)
            FROM sales s JOIN products p ON s.sku = p.sku
            WHERE s.invoice_date >= :w8 {tf}
            GROUP BY s.sku, w
        """), {"w8": week0}).fetchall()


def count_sales_weeks(conn, tf):
    return conn.execute(text(f"""
            SELECT COUNT(DISTINCT DATE_TRUNC('week', s.invoice_date))
            FROM sales s JOIN products p ON s.sku = p.sku
            WHERE s.invoice_date IS NOT NULL {tf}
        """)).scalar()
