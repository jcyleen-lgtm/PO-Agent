"""Stock snapshot data access (stock_snapshots). Stock is summed per SKU and report date."""

from sqlalchemy import text


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
