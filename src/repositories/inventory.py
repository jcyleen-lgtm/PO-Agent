"""Stock snapshot data access (stock_snapshots): reads (stock is summed per SKU and report date) and the
snapshot upload."""

from psycopg2.extras import execute_values
from sqlalchemy import text

from src.core.database import get_raw_conn


def latest_snapshot_date(conn):
    return conn.execute(text("SELECT MAX(snapshot_date) FROM stock_snapshots")).scalar()


def snapshot_dates_desc(conn):
    return [r[0] for r in conn.execute(text(
        "SELECT DISTINCT snapshot_date FROM stock_snapshots ORDER BY snapshot_date DESC")).fetchall()]


def stock_by_sku(conn, snapshot_date):
    """{sku: quantity} for one report date (several warehouse rows are summed)."""
    return {r[0]: int(r[1]) for r in conn.execute(text(
        "SELECT sku, SUM(quantity) FROM stock_snapshots WHERE snapshot_date = :d GROUP BY sku"),
        {"d": snapshot_date}).fetchall()}


def latest_snapshot_stats(conn):
    """(latest report date, distinct SKUs over all snapshots) — as shown on the data status card."""
    return conn.execute(text("SELECT MAX(snapshot_date),COUNT(DISTINCT sku) FROM stock_snapshots")).fetchone()


def snapshot_dates_before(conn, d):
    return [r[0] for r in conn.execute(text(
        "SELECT DISTINCT snapshot_date FROM stock_snapshots WHERE snapshot_date < :d ORDER BY snapshot_date DESC"),
        {"d": d}).fetchall()]


def stock_for_sku(conn, sku, snapshot_date):
    return conn.execute(text("SELECT COALESCE(SUM(quantity),0) FROM stock_snapshots WHERE sku=:s AND snapshot_date=:d"),
                        {"s": sku, "d": snapshot_date}).scalar()


def stock_rows_after(conn, after):
    """[(report date, sku, quantity)] for every snapshot after `after`."""
    return conn.execute(text("""
            SELECT snapshot_date, sku, SUM(quantity) FROM stock_snapshots
            WHERE snapshot_date > :a GROUP BY snapshot_date, sku
        """), {"a": after}).fetchall()


def skus_in_snapshot(conn, snapshot_date):
    return {r[0] for r in conn.execute(text("SELECT sku FROM stock_snapshots WHERE snapshot_date = :d"),
                                       {"d": snapshot_date}).fetchall()}


def history_for_sku(conn, sku):
    """[(report date, quantity)] of one SKU, oldest first."""
    return conn.execute(text("""
            SELECT snapshot_date, SUM(quantity) FROM stock_snapshots WHERE sku=:s GROUP BY snapshot_date ORDER BY snapshot_date
        """), {"s": sku}).fetchall()


def replace_snapshot(snapshot_date, stock_skus, name_map_fn, rows):
    """Stock upload, one transaction: insert SKUs missing from products (name from the stock file, so the
    FK on stock_snapshots holds), delete the rows of that report date, insert the new rows.
    stock_skus = sorted stripped SKUs in the file; name_map_fn() -> {sku: name}; rows = [(sku, qty, date)]."""
    conn = get_raw_conn()
    cur = conn.cursor()
    conn.autocommit = False
    try:
        cur.execute("SELECT sku FROM products WHERE sku = ANY(%s)", (stock_skus,))
        existing_skus = {r[0] for r in cur.fetchall()}
        missing_skus = [sku for sku in stock_skus if sku not in existing_skus]
        if missing_skus:
            sku_name_map = name_map_fn()
            execute_values(cur, "INSERT INTO products (sku, name) VALUES %s ON CONFLICT DO NOTHING",
                           [(m, sku_name_map.get(m, m)) for m in missing_skus], page_size=1000)
        cur.execute("DELETE FROM stock_snapshots WHERE snapshot_date = %s", (snapshot_date,))
        execute_values(cur, "INSERT INTO stock_snapshots (sku, quantity, snapshot_date) VALUES %s ON CONFLICT DO NOTHING",
                       rows, page_size=1000)
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        cur.close()
        conn.close()


def sku_count_per_snapshot(conn):
    """[(report date, distinct SKUs)] oldest first."""
    return conn.execute(text(
        "SELECT snapshot_date, COUNT(DISTINCT sku) FROM stock_snapshots GROUP BY snapshot_date ORDER BY snapshot_date")).fetchall()
