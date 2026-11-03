"""Purchase-order data access: open lines for planning, master-list upsert."""

from psycopg2.extras import execute_values
from sqlalchemy import text

from src.core.database import get_engine, get_raw_conn
from src.core.ddl import ensure_po_columns



def _fetch_po_lines(conn, sku=None):
    """Outstanding PO lines (pending/partial, ordered > received). Cancelled/superseded never count."""
    where = "AND po.sku = :sku" if sku else ""
    rows = conn.execute(text(f"""
        SELECT po.id, po.sku, po.qty_ordered - COALESCE(po.qty_received, 0), po.order_date,
               po.expected_date, po.supplier, po.status, po.qty_ordered, COALESCE(po.qty_received, 0)
        FROM purchase_orders po
        WHERE po.status IN ('pending', 'partial') AND po.qty_ordered > COALESCE(po.qty_received, 0) {where}
        ORDER BY po.id
    """), {"sku": sku} if sku else {}).fetchall()
    out = {}
    for r in rows:
        out.setdefault(r[1], []).append({"id": r[0], "outstanding": int(r[2]), "order_date": r[3],
                                         "expected_date": r[4], "supplier": r[5], "status": r[6],
                                         "qty_ordered": int(r[7]), "qty_received": int(r[8])})
    return out


def _import_po_master(lines, warnings, filename):
    """Upsert by line key (PO Number | SKU | n-th line of that SKU in the PO). New lines are added, existing
    lines updated; the higher received quantity wins, so receipts entered in Stockwise are not lost on a
    re-upload. Old open lines without a PO number (previous upload format) are marked 'superseded' once."""
    engine = get_engine()
    with engine.begin() as c:
        ensure_po_columns(c)
    conn = get_raw_conn()
    conn.autocommit = False
    cur = conn.cursor()
    try:
        names = {}
        for l in lines:
            names.setdefault(l["sku"], l["description"] or l["sku"])
        execute_values(cur, "INSERT INTO products (sku, name) VALUES %s ON CONFLICT (sku) DO NOTHING",
                       list(names.items()), page_size=1000)
        cur.execute("SELECT line_key FROM purchase_orders WHERE line_key = ANY(%s)", ([l["line_key"] for l in lines],))
        existing = {r[0] for r in cur.fetchall()}
        cur.execute("""UPDATE purchase_orders SET status = 'superseded', updated_at = NOW()
                       WHERE line_key IS NULL AND status IN ('pending', 'partial')""")
        superseded = cur.rowcount
        execute_values(cur, """
            INSERT INTO purchase_orders (line_key, po_number, sku, description, qty_ordered, qty_received,
                                         order_date, expected_date, received_date, status, courier, notes)
            VALUES %s
            ON CONFLICT (line_key) WHERE line_key IS NOT NULL DO UPDATE SET
                po_number = EXCLUDED.po_number, description = EXCLUDED.description,
                qty_ordered = EXCLUDED.qty_ordered,
                qty_received = GREATEST(EXCLUDED.qty_received, purchase_orders.qty_received),
                order_date = EXCLUDED.order_date, expected_date = EXCLUDED.expected_date,
                received_date = COALESCE(EXCLUDED.received_date, purchase_orders.received_date),
                courier = COALESCE(EXCLUDED.courier, purchase_orders.courier),
                notes = COALESCE(EXCLUDED.notes, purchase_orders.notes),
                status = CASE WHEN purchase_orders.status IN ('cancelled', 'closed') THEN purchase_orders.status
                              WHEN GREATEST(EXCLUDED.qty_received, purchase_orders.qty_received) >= EXCLUDED.qty_ordered THEN 'completed'
                              WHEN GREATEST(EXCLUDED.qty_received, purchase_orders.qty_received) > 0 THEN 'partial'
                              ELSE 'pending' END,
                updated_at = NOW()
        """, [(l["line_key"], l["po_number"], l["sku"], l["description"], l["qty_ordered"], l["qty_received"],
               l["order_date"], l["expected_date"], l["received_date"], l["status"], l["courier"], l["notes"])
              for l in lines], page_size=1000)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()
    open_lines = [l for l in lines if l["qty_received"] < l["qty_ordered"]]
    return {
        "status": "success", "format": "po_master",
        "message": f"{len(lines)} baris PO dimuat ({len(lines) - len(existing)} baru, {len(existing)} diperbarui) · "
                   f"{len(open_lines)} baris belum lengkap ({sum(l['qty_ordered'] - l['qty_received'] for l in open_lines):,} unit)",
        "matched": len(lines), "new_lines": len(lines) - len(existing), "updated_lines": len(existing),
        "po_numbers": len({l["po_number"] for l in lines}), "open_lines": len(open_lines),
        "superseded_old_rows": superseded, "unmatched": 0, "unmatched_names": [], "warnings": warnings,
    }
