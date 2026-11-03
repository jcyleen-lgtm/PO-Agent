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


def lines_for_sku(conn, sku):
    """Every PO line of one SKU (all statuses), newest first."""
    return conn.execute(text("""
            SELECT id, qty_ordered, COALESCE(qty_received,0), status, order_date, expected_date, received_date, supplier
            FROM purchase_orders WHERE sku=:s ORDER BY COALESCE(order_date, created_at::date) DESC, id DESC
        """), {"s": sku}).fetchall()


def replace_open_lines(new_products, lines):
    """Old PO template upload, one transaction: insert missing products, mark every open line
    'superseded' (NOT 'cancelled', so a replaced line can be told apart from a real cancellation and the
    history stays), insert the file's lines.
    new_products = [(sku, name)]; lines = [(sku, qty_ordered, supplier, order_date, expected_date, status)]."""
    conn = get_raw_conn()
    conn.autocommit = False
    cur = conn.cursor()
    try:
        if new_products:
            execute_values(cur, "INSERT INTO products (sku, name) VALUES %s ON CONFLICT (sku) DO NOTHING", new_products)
        cur.execute("UPDATE purchase_orders SET status = 'superseded', updated_at = NOW() WHERE status IN ('pending', 'partial')")
        execute_values(cur, """
                INSERT INTO purchase_orders (sku, qty_ordered, supplier, order_date, expected_date, status) VALUES %s
            """, lines)
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        cur.close()
        conn.close()


def list_lines(conn):
    """Every PO line except superseded ones, for the PO & Supplier table."""
    return conn.execute(text("""
            SELECT po.id, po.sku, COALESCE(p.name, po.description, po.sku), po.qty_ordered, COALESCE(po.qty_received, 0),
                   po.supplier, po.status, po.order_date, po.expected_date, po.received_date,
                   po.po_number, po.courier, po.notes
            FROM purchase_orders po LEFT JOIN products p ON p.sku = po.sku
            WHERE po.status <> 'superseded'
            ORDER BY po.order_date DESC NULLS LAST, po.po_number, po.id
        """)).fetchall()


def summary_rows(conn):
    return conn.execute(text("""
            SELECT id, sku, qty_ordered, COALESCE(qty_received,0), status, order_date, expected_date,
                   received_date, supplier, po_number
            FROM purchase_orders
        """)).fetchall()


def delete_line(engine, po_id):
    with engine.connect() as conn:
        conn.execute(text("DELETE FROM purchase_orders WHERE id = :id"), {"id": po_id})
        conn.commit()


def receive_all(conn, po_number, received_date):
    """Every open line of one PO number becomes fully received. Returns the number of lines changed."""
    return conn.execute(text("""
            UPDATE purchase_orders SET qty_received = qty_ordered, status = 'completed',
                   received_date = COALESCE(received_date, :rd), updated_at = NOW()
            WHERE po_number = :po AND status IN ('pending', 'partial')
        """), {"po": po_number, "rd": received_date}).rowcount


def update_line(engine, po_id, changes):
    """Apply validated edits to one PO line and return (id, qty_ordered, qty_received, status, received_date),
    or None when the id does not exist. `changes` = [(kind, value)] from the service, in order:

      qty_received              set the received quantity
      status_from_qty           status follows the quantity: completed when received >= ordered, partial when
                                > 0, else pending; cancelled/superseded stay, a closed line stays closed
      received_date_on_receipt  first receipt stamps this date (kept if already set); qty 0 clears it
      received_date_fix         correct the receipt date of a line that already has goods in
      status_reopen             undo close-short: status from the received quantity
      status                    set an explicit status
      notes                     set notes (None clears)
    """
    sets, params = [], {"id": po_id}
    for kind, value in changes:
        if kind == "qty_received":
            params["qty_received"] = value
            sets.append("qty_received = :qty_received")
        elif kind == "status_from_qty":
            sets.append("""status = CASE WHEN status IN ('cancelled','superseded') THEN status
                                         WHEN :qty_received >= qty_ordered THEN 'completed'
                                         WHEN status = 'closed' THEN 'closed'
                                         WHEN :qty_received > 0 THEN 'partial' ELSE 'pending' END""")
        elif kind == "received_date_on_receipt":
            params["rd"] = value
            sets.append("received_date = CASE WHEN :qty_received > 0 THEN COALESCE(received_date, :rd) ELSE NULL END")
        elif kind == "received_date_fix":
            params["rd"] = value
            sets.append("received_date = CASE WHEN COALESCE(qty_received, 0) > 0 THEN :rd ELSE received_date END")
        elif kind == "status_reopen":
            sets.append("status = CASE WHEN COALESCE(qty_received,0) >= qty_ordered THEN 'completed' "
                        "WHEN COALESCE(qty_received,0) > 0 THEN 'partial' ELSE 'pending' END")
        elif kind == "status":
            sets.append("status = :status")
            params["status"] = value
        elif kind == "notes":
            sets.append("notes = :notes")
            params["notes"] = value
        else:
            raise ValueError(f"unknown PO change {kind}")
    sets.append("updated_at = NOW()")
    with engine.connect() as conn:
        row = conn.execute(text(f"UPDATE purchase_orders SET {', '.join(sets)} WHERE id = :id "
                                "RETURNING id, qty_ordered, qty_received, status, received_date"), params).fetchone()
        conn.commit()
    return row
