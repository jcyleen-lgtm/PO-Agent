"""Purchase-order workflows: upload (master list or old template), list, summary, edits, receive-all."""

from datetime import date
import os
import uuid

from fastapi import HTTPException
import pandas as pd

from src.core.config import UPLOAD_DIR, get_settings
from src.core.database import get_engine
from src.core.ddl import ensure_po_columns
from src.core.state import master_sku
from src.parsers.name_match import group_skus, norm_name
from src.parsers.po_master import _is_po_master, _parse_po_master, map_po_columns
from src.repositories import products as product_repo, purchase_orders as po_repo
from src.repositories.purchase_orders import _import_po_master
from src.services.inventory_service import _stockout_rate
import replenishment as rp



async def po_upload(filename: str, content: bytes):
    """
    Upload ongoing PO dari Excel.
    Expected columns: SKU | Product Name | Qty Ordered | Supplier (optional) | Order Date (optional) | Expected Date (optional)
    Atau minimal: product_name + qty_ordered (akan di-lookup SKU dari master/products).
    """
    if not filename.endswith((".xls", ".xlsx")):
        raise HTTPException(400, "Format harus .xls/.xlsx")
    tmp = os.path.join(UPLOAD_DIR, f"po_{uuid.uuid4().hex[:8]}{os.path.splitext(filename)[1]}")
    with open(tmp, "wb") as f:
        f.write(content)
    try:
        ext = os.path.splitext(tmp)[1].lower()
        df = pd.read_excel(tmp, engine="xlrd" if ext == ".xls" else "openpyxl")
        df.columns = [str(c).strip() for c in df.columns]

        # Purchasing team's PO master sheet (history + ongoing, with receipts): dedicated importer
        if _is_po_master(df.columns):
            lines, warnings = _parse_po_master(df)
            if not lines:
                raise HTTPException(400, "Tidak ada baris PO yang valid di file.")
            res = _import_po_master(lines, warnings, filename)
            os.remove(tmp)
            return res

        # Column mapping: exact aliases first, then keyword rules in a fixed priority order.
        # Dates are matched BEFORE quantity, so "Order Date" can never become the qty column.
        col_map, warnings = map_po_columns(df.columns)
        df = df.rename(columns=col_map)

        if "qty_ordered" not in df.columns:
            raise HTTPException(400, f"Kolom qty tidak ditemukan. Kolom di file: {list(map(str, col_map.keys()) or df.columns)}")

        df["qty_ordered"] = pd.to_numeric(df["qty_ordered"], errors="coerce").fillna(0).astype(int)
        for dc in ("order_date", "expected_date"):
            if dc in df.columns:
                parsed = pd.to_datetime(df[dc], errors="coerce", dayfirst=True)
                bad = int((df[dc].notna() & parsed.isna()).sum())
                if bad:
                    warnings.append(f"{bad} nilai {dc} tidak bisa dibaca sebagai tanggal (dikosongkan)")
                df[dc] = parsed
        df = df[df["qty_ordered"] > 0].copy()

        if df.empty:
            raise HTTPException(400, "Tidak ada data PO valid (qty > 0)")

        # Resolve SKU: from sku column, products table, or master_sku fallback
        engine = get_engine()
        product_lookup = {}
        with engine.connect() as c:
            rows = product_repo.all_names(c)
        # Exact name match only (capitals / repeated spaces ignored); group codes are never a PO line.
        groups = group_skus([r[0] for r in rows] + list(master_sku.values()))
        for r in rows:
            if r[1] and r[0] not in groups:
                product_lookup[norm_name(r[1])] = r[0]
        master_lookup = {norm_name(k): v for k, v in master_sku.items() if v not in groups}

        if "sku" not in df.columns:
            df["sku"] = ""

        resolved = []
        unmatched = []
        new_products = []  # products to auto-insert from master_sku
        for _, row in df.iterrows():
            sku = str(row.get("sku", "")).strip()
            pname = str(row.get("product_name", "")).strip()

            # Try SKU first, then name lookup from products table
            if not sku or sku in ("", "nan", "0"):
                sku = product_lookup.get(norm_name(pname), "")

            # Fallback to master_sku if still no match
            if not sku and master_sku:
                sku = master_lookup.get(norm_name(pname), "")
                if sku:
                    new_products.append({"sku": sku, "name": pname})

            # If SKU column had a value but it's not in products, auto-insert it
            if sku and sku not in product_lookup.values():
                new_products.append({"sku": sku, "name": pname or sku})

            if sku:
                resolved.append({
                    "sku": sku,
                    "qty_ordered": int(row["qty_ordered"]),
                    "supplier": str(row.get("supplier", "")) if pd.notna(row.get("supplier")) else None,
                    "order_date": pd.to_datetime(row.get("order_date"), errors="coerce"),
                    "expected_date": pd.to_datetime(row.get("expected_date"), errors="coerce"),
                    # only cancelled when the source file itself says so
                    "status": "cancelled" if any(k in str(row.get("source_status", "")).lower() for k in ("cancel", "batal")) else "pending",
                })
            else:
                unmatched.append(pname)

        if not resolved:
            raise HTTPException(400, f"Tidak ada produk yang bisa di-match. Unmatched: {unmatched[:10]}")

        # Insert into purchase_orders — mark old pending as cancelled, then insert new
        po_repo.replace_open_lines(
            list({p_["sku"]: (p_["sku"], p_["name"]) for p_ in new_products}.values()),
            [(po["sku"], po["qty_ordered"], po["supplier"],
              po["order_date"].date() if pd.notna(po["order_date"]) else None,
              po["expected_date"].date() if pd.notna(po["expected_date"]) else None,
              po["status"]) for po in resolved])

        os.remove(tmp)
        return {
            "status": "success",
            "message": f"{len(resolved)} PO berhasil dimuat",
            "matched": len(resolved),
            "new_products": len(new_products),
            "unmatched": len(unmatched),
            "unmatched_names": unmatched[:20],
            "column_mapping": {str(k): v for k, v in col_map.items()},
            "warnings": warnings,
        }
    except HTTPException:
        raise
    except Exception as e:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise HTTPException(500, f"Gagal parse PO: {e}")


async def po_list(status: str = "open"):
    """PO lines with everything the PO & Supplier page shows. status = open | overdue | all | <status>.
    remaining = ordered − received (negative = over-received). age_days = days since PO date
    (until the receipt date for finished lines). aging = open line older than po_aging_warn_days."""
    cfg = get_settings()
    today = date.today()
    engine = get_engine()
    with engine.begin() as conn:
        ensure_po_columns(conn)
        rows = po_repo.list_lines(conn)
    data = []
    for r in rows:
        is_open = r[6] in ("pending", "partial") and r[3] > r[4]
        outstanding = max(0, int(r[3]) - int(r[4])) if is_open else 0
        eta, src = rp.po_eta({"expected_date": r[8], "order_date": r[7]}, cfg["lead_time_weeks"])
        overdue = bool(is_open and eta and eta < today)
        end = r[9] if (not is_open and r[9]) else today
        age = (end - r[7]).days if r[7] else None
        aging = bool(is_open and age is not None and age > cfg["po_aging_warn_days"])
        if status == "open" and not is_open:
            continue
        if status == "overdue" and not overdue:
            continue
        if status not in ("open", "overdue", "all") and r[6] != status:
            continue
        data.append({
            "id": r[0], "sku": r[1], "product": r[2], "qty_ordered": r[3], "qty_received": r[4],
            "remaining": int(r[3]) - int(r[4]), "outstanding": outstanding,
            "supplier": r[5], "status": r[6], "is_open": is_open,
            "order_date": str(r[7]) if r[7] else None, "expected_date": str(r[8]) if r[8] else None,
            "received_date": str(r[9]) if r[9] else None,
            "eta": str(eta) if eta else None, "eta_source": src, "overdue": overdue,
            "days_overdue": (today - eta).days if overdue else None,
            "age_days": age, "aging": aging, "po_number": r[10], "courier": r[11], "notes": r[12],
        })
    return {"count": len(data), "data": data, "aging_warn_days": cfg["po_aging_warn_days"]}


async def po_summary():
    """PO & Supplier page header + delivery performance. Performance figures are only returned when
    the underlying receipt data exists; otherwise `available: false` with the reason."""
    cfg = get_settings()
    today = date.today()
    engine = get_engine()
    with engine.begin() as conn:
        ensure_po_columns(conn)
        rows = po_repo.summary_rows(conn)
    open_rows = [r for r in rows if r[4] in ("pending", "partial") and r[2] > r[3]]
    aging = sum(1 for r in open_rows if r[5] and (today - r[5]).days > cfg["po_aging_warn_days"])
    overdue = unknown = estimated = 0
    for r in open_rows:
        eta, src = rp.po_eta({"expected_date": r[6], "order_date": r[5]}, cfg["lead_time_weeks"])
        unknown += src == "unknown"
        estimated += src == "estimated"
        overdue += bool(eta and eta < today)
    by_status = {}
    for r in rows:
        by_status[r[4]] = by_status.get(r[4], 0) + 1

    # Delivery performance (needs order dates + receipts)
    # Lines that are due: closed lines, plus partial lines whose ETA has passed (still-in-transit lines are excluded)
    def _due(r):
        if r[4] in ("received", "completed", "closed"):
            return True
        if r[4] in ("partial", "pending"):   # still open but already due: counts with what arrived so far
            eta, _ = rp.po_eta({"expected_date": r[6], "order_date": r[5]}, cfg["lead_time_weeks"])
            return bool(eta and eta < today)
        return False
    done = [r for r in rows if _due(r) and r[2] > 0]
    fulfil = {"available": False, "reason": "no_receipts", "required": "qty_received on received/partial POs"}
    if done and sum(r[3] for r in done) > 0:
        # fill rate: over-delivery on one line must not hide a shortage on another -> cap each line at ordered
        fulfil = {"available": True,
                  "rate_pct": round(sum(min(r[3], r[2]) for r in done) / sum(r[2] for r in done) * 100, 1),
                  "po_lines": len(done),
                  "short_lines": sum(1 for r in done if r[3] < r[2]),
                  "over_lines": sum(1 for r in done if r[3] > r[2])}
    lts = [((r[7] - r[5]).days, r[8] or "All suppliers (1 agent)") for r in rows
           if r[5] and r[7] and r[7] >= r[5] and r[4] in ("completed", "received", "partial", "closed")]
    lead = {"available": False, "reason": "no_order_and_receipt_dates",
            "required": "order_date and received_date on the same PO line", "deliveries": len(lts)}
    if lts:
        import statistics
        sup = {}
        for d, s_ in lts:
            sup.setdefault(s_, []).append(d)
        ds = sorted(d for d, _ in lts)
        lead = {"available": True, "avg_days": round(sum(ds) / len(ds), 1), "deliveries": len(lts),
                "median_days": round(statistics.median(ds), 1),
                "p90_days": ds[min(len(ds) - 1, int(round(0.9 * (len(ds) - 1))))],
                "by_supplier": [{"supplier": k, "deliveries": len(v), "avg_days": round(sum(v) / len(v), 1),
                                 "std_days": round(statistics.stdev(v), 1) if len(v) >= 3 else None}
                                for k, v in sorted(sup.items())]}
    return {
        "open_pos": len(open_rows),
        "open_qty_ordered": sum(r[2] for r in open_rows),
        "open_qty_received": sum(r[3] for r in open_rows),
        "outstanding_qty": sum(r[2] - r[3] for r in open_rows),
        "overdue": overdue, "eta_unknown": unknown, "eta_estimated": estimated,
        "aging": aging, "aging_warn_days": cfg["po_aging_warn_days"],
        "open_po_numbers": len({r[9] for r in open_rows if r[9]}),
        "by_status": by_status,
        "suppliers": len({r[8] for r in rows if r[8]}),
        "default_lead_time_weeks": cfg["lead_time_weeks"],
        "fulfillment": fulfil, "lead_time": lead,
        "stockout_rate": _stockout_rate(engine),
    }


async def po_delete(po_id: int):
    engine = get_engine()
    po_repo.delete_line(engine, po_id)
    return {"status": "deleted", "id": po_id}


async def po_update(po_id: int, body: dict = None):
    """Update a PO line. qty_received sets the status automatically (completed / partial / pending)
    and stamps the receipt date (today, or body.received_date) when goods are received for the first time."""
    if not body:
        raise HTTPException(400, "Body kosong")
    changes = []          # validated edits, in the order they are applied (see po_repo.update_line)
    if "qty_received" in body:
        try:
            q = int(body["qty_received"])
        except (TypeError, ValueError):
            raise HTTPException(400, "qty_received harus angka")
        if q < 0:
            raise HTTPException(400, "qty_received tidak boleh minus")
        changes.append(("qty_received", q))
        if "status" not in body:
            changes.append(("status_from_qty", None))
        rd = body.get("received_date")
        try:
            changes.append(("received_date_on_receipt", date.fromisoformat(rd) if rd else date.today()))
        except ValueError:
            raise HTTPException(400, "received_date harus YYYY-MM-DD")
    elif body.get("received_date"):
        # correct the receipt date of a line that already has goods in (used for actual lead time)
        try:
            changes.append(("received_date_fix", date.fromisoformat(body["received_date"])))
        except ValueError:
            raise HTTPException(400, "received_date harus YYYY-MM-DD")
    if "status" in body:
        st = body["status"]
        if st == "reopen":      # undo "close short": back to pending / partial from the received qty
            changes.append(("status_reopen", None))
        elif st in ("pending", "partial", "completed", "cancelled", "closed"):
            changes.append(("status", st))
        else:
            raise HTTPException(400, "status tidak dikenal")
    if "notes" in body:
        changes.append(("notes", (str(body["notes"]).strip() or None) if body["notes"] is not None else None))
    if not changes:
        raise HTTPException(400, "Tidak ada field yang di-update")
    row = po_repo.update_line(get_engine(), po_id, changes)
    if not row:
        raise HTTPException(404, "PO line tidak ditemukan")
    return {"status": "updated", "id": row[0], "qty_ordered": row[1], "qty_received": row[2],
            "line_status": row[3], "received_date": str(row[4]) if row[4] else None}


async def po_receive_all(body: dict = None):
    """Mark every open line of one PO number as fully received (today, or body.received_date)."""
    po = (body or {}).get("po_number")
    if not po:
        raise HTTPException(400, "po_number wajib")
    rd = (body or {}).get("received_date")
    engine = get_engine()
    with engine.begin() as conn:
        ensure_po_columns(conn)
        n = po_repo.receive_all(conn, po, date.fromisoformat(rd) if rd else date.today())
    return {"status": "updated", "po_number": po, "lines": n}
