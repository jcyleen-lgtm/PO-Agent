"""Company Hero SKU list (hero_skus): replaced in full on every upload."""

from sqlalchemy import text


def replace_list(conn, rows):
    """rows = [{"sku", "g" (group), "st" (status), "src" (code as written), "n" (name)}]."""
    conn.execute(text("DELETE FROM hero_skus"))
    conn.execute(text("""INSERT INTO hero_skus (sku, group_name, status, source_code, product_name)
                             VALUES (:sku, :g, :st, :src, :n)"""), rows)


def list_with_names(conn):
    """{sku: {"group", "status", "name", "src"}} with the product name from products when known."""
    return {r[0]: {"group": r[1], "status": r[2], "name": r[3], "src": r[4]} for r in conn.execute(text(
        "SELECT h.sku, h.group_name, h.status, COALESCE(p.name, h.product_name), h.source_code "
        "FROM hero_skus h LEFT JOIN products p ON p.sku = h.sku")).fetchall()}


def import_skus(conn):
    """[(sku, group, status)] of listed SKUs that exist as import products (forecast scope)."""
    return conn.execute(text(
        "SELECT h.sku, h.group_name, h.status FROM hero_skus h JOIN products p ON p.sku = h.sku "
        "WHERE COALESCE(p.product_type, 'import') = 'import'")).fetchall()
