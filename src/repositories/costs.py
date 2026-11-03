"""Cost data access: Accurate COGS from sales and the verified HPP table (master_cogs)."""

from sqlalchemy import text


def _sku_filter(window_weeks, skus):
    params = {"w": window_weeks}
    sku_filter = ""
    if skus is not None:
        sku_filter = "AND sku = ANY(:k)"
        params["k"] = list(skus)
    return sku_filter, params


def cogs_and_qty_by_sku(conn, window_weeks, skus=None):
    """[(sku, sum cogs_accurate, sum quantity)] over the last `window_weeks` weeks before the last sale."""
    sku_filter, params = _sku_filter(window_weeks, skus)
    return conn.execute(text(f"""
        WITH mx AS (SELECT MAX(invoice_date) d FROM sales)
        SELECT sku, SUM(cogs_accurate), SUM(quantity)
        FROM sales, mx
        WHERE invoice_date > mx.d - (:w * 7) AND cogs_accurate IS NOT NULL AND cogs_accurate > 0
          AND quantity > 0 {sku_filter}
        GROUP BY sku
    """), params).fetchall()


def master_hpp(conn, window_weeks, skus=None):
    """[(sku, hpp)] from master_cogs (raises if the table does not exist; caller rolls back)."""
    sku_filter, params = _sku_filter(window_weeks, skus)
    return conn.execute(text(
        f"SELECT sku, hpp FROM master_cogs WHERE hpp IS NOT NULL AND hpp > 0 {sku_filter}"),
        params).fetchall()


def cogs_between(conn, after, until):
    """[(sku, sum cogs_accurate)] for after < invoice_date <= until."""
    return conn.execute(text("""
            SELECT sku, SUM(cogs_accurate) FROM sales
            WHERE invoice_date > :a AND invoice_date <= :b AND cogs_accurate IS NOT NULL GROUP BY sku"""),
            {"a": after, "b": until}).fetchall()
