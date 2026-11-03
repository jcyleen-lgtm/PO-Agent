"""Product classification data access: product-type filter, food rules, food-sales archive."""

from sqlalchemy import text

from src.core.ddl import ensure_sales_excluded
from src.parsers.sku_lists import _norm_name
from src.repositories.sales import SALES_COLS



# ── Stock status (one definition for the whole dashboard) ──────
# Coverage = (stock + open PO) / average weekly sales of the last 4 weeks.
def _type_filter(product_type):
    return f"AND COALESCE(p.product_type, 'import') = '{product_type}'" if product_type in ("import", "food") else ""


def load_food_rules(conn):
    """Return (codes, normalized_names). Empty sets if no food list was uploaded yet."""
    exists = conn.execute(text("SELECT to_regclass('public.food_skus')")).scalar()
    if not exists:
        return set(), set()
    rows = conn.execute(text("SELECT code, name FROM food_skus")).fetchall()
    return {r[0] for r in rows}, {_norm_name(r[1]) for r in rows if r[1]}


def move_food_sales(conn, food_skus):
    """Food sales leave the system (archived with reason 'food'); sales of products that are no longer
    on the food list come back. Returns (archived, restored) row counts."""
    ensure_sales_excluded(conn)
    restored = conn.execute(text(f"""
        INSERT INTO sales ({SALES_COLS})
        SELECT {SALES_COLS} FROM sales_excluded x
        WHERE x.reason = 'food' AND NOT (x.sku = ANY(:k)) AND NOT EXISTS (
            SELECT 1 FROM sales e WHERE e.sku = x.sku AND e.invoice_date = x.invoice_date
              AND e.quantity = x.quantity AND e.amount = x.amount)
    """), {"k": list(food_skus)}).rowcount
    conn.execute(text("DELETE FROM sales_excluded WHERE reason = 'food' AND NOT (sku = ANY(:k))"), {"k": list(food_skus)})
    archived = 0
    if food_skus:
        archived = conn.execute(text(f"""
            WITH m AS (DELETE FROM sales WHERE sku = ANY(:k) RETURNING {SALES_COLS})
            INSERT INTO sales_excluded ({SALES_COLS}, reason) SELECT {SALES_COLS}, 'food' FROM m
        """), {"k": list(food_skus)}).rowcount
    return archived, restored
