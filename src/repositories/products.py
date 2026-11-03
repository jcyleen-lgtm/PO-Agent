"""Product data access: names, Master SKU upsert, product-type filter, food list and food-sales archive."""

from psycopg2.extras import execute_values
from sqlalchemy import text

from src.core.database import get_raw_conn
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


def names_by_sku(conn):
    """{sku: name} for every real product (SKU '0' = unmatched placeholder is skipped)."""
    return {r[0]: r[1] for r in conn.execute(text("SELECT sku, name FROM products WHERE sku <> '0'")).fetchall()}


def insert_missing(conn, rows):
    """rows = [{"s": sku, "n": name}]; existing SKUs are left untouched."""
    conn.execute(text("INSERT INTO products (sku, name) VALUES (:s, :n) ON CONFLICT (sku) DO NOTHING"), rows)


def all_names(conn):
    return conn.execute(text("SELECT sku, name FROM products")).fetchall()


def name_of(conn, sku):
    return conn.execute(text("SELECT name FROM products WHERE sku=:s"), {"s": sku}).scalar()


def upsert_names(pairs):
    """Master SKU upload: [(sku, name)] inserted or renamed, one round trip per 1000 rows."""
    conn = get_raw_conn()
    cur = conn.cursor()
    try:
        execute_values(cur, """
                INSERT INTO products (sku, name) VALUES %s
                ON CONFLICT (sku) DO UPDATE SET name = EXCLUDED.name
            """, pairs, page_size=1000)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()


def replace_food_list(conn, entries):
    """entries = {code: name}; the stored food list is replaced in full."""
    conn.execute(text("DELETE FROM food_skus"))
    conn.execute(text("INSERT INTO food_skus (code, name) VALUES (:c, :n)"),
                 [{"c": c, "n": n} for c, n in entries.items()])


def set_product_types(conn, food_skus):
    """Every product becomes 'import' except the given SKUs, which become 'food'."""
    conn.execute(text("UPDATE products SET product_type = 'import' WHERE product_type IS DISTINCT FROM 'import'"))
    if food_skus:
        conn.execute(text("UPDATE products SET product_type = 'food' WHERE sku = ANY(:k)"), {"k": food_skus})


def lookup_rows_worst_first(conn):
    """[(sku, name)] for the sales-ETL name -> SKU lookup. Same name under several SKUs (old code without
    dots, AUTO- placeholder, current code): the preferred SKU must win, so rows are ordered worst -> best
    and the caller keeps the last one. Preference: in the latest stock snapshot > dotted Accurate code >
    digits only > AUTO-."""
    return conn.execute(text("""
                SELECT p.sku, p.name FROM products p
                WHERE p.sku != '0'
                ORDER BY
                    EXISTS (SELECT 1 FROM stock_snapshots s
                            WHERE s.sku = p.sku
                              AND s.snapshot_date = (SELECT MAX(snapshot_date) FROM stock_snapshots)),
                    CASE WHEN p.sku LIKE 'AUTO-%' THEN 0 WHEN p.sku NOT LIKE '%.%' THEN 1 ELSE 2 END,
                    p.sku
            """)).fetchall()
