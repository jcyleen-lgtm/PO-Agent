"""Sales data access: table layout shared by the ETL and the food archive, and the sales reads used by
the inventory build. `type_filter` is the whitelisted fragment from repositories.products._type_filter."""

from sqlalchemy import text


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
