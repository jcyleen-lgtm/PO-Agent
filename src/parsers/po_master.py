"""Purchase-order file parsing: column mapping for the old PO template and the purchasing
'Master List' sheet (one row per PO line, history + ongoing). No database access."""

from datetime import date, datetime

import pandas as pd
from fastapi import HTTPException


PO_ALIASES = {
    "sku": {"sku", "kode", "kode item", "kode barang", "item code", "item_code", "item no", "no. barang"},
    "product_name": {"product", "product name", "nama", "nama barang", "nama item", "name", "item", "description", "deskripsi"},
    "qty_ordered": {"qty", "qty ordered", "qty_ordered", "quantity", "kuantitas", "jumlah", "total qty", "total_qty", "qty order"},
    "supplier": {"supplier", "vendor", "pemasok"},
    "order_date": {"order date", "order_date", "tanggal", "tanggal po", "tgl po", "po date", "tanggal pesanan"},
    "expected_date": {"expected date", "expected_date", "eta", "estimasi", "estimasi tiba", "tanggal kirim", "delivery date"},
    "po_number": {"po number", "po_number", "no po", "no. po", "nomor po", "po no", "po"},
    "source_status": {"status", "status po"},
}


# keyword fallback, checked in this order (dates before qty on purpose)
PO_KEYWORDS = [
    ("expected_date", ("expected", "eta", "estimasi", "delivery")),
    ("order_date", ("order date", "tanggal", "tgl", "date")),
    ("po_number", ("po number", "nomor po", "no po")),
    ("supplier", ("supplier", "vendor", "pemasok")),
    ("qty_ordered", ("qty", "quantity", "kuantitas", "jumlah")),
    ("sku", ("sku", "kode")),
    ("product_name", ("product", "nama", "name", "item", "description")),
]


def map_po_columns(columns):
    """Return ({original: target}, warnings). Each target is used once; unmapped columns are reported."""
    col_map, used, warnings = {}, set(), []
    norm = {c: " ".join(str(c).strip().lower().replace("_", " ").split()) for c in columns}
    for c, n in norm.items():
        for tgt, names in PO_ALIASES.items():
            if tgt not in used and (n in names or n.replace(" ", "_") in names):
                col_map[c] = tgt; used.add(tgt); break
    for c, n in norm.items():
        if c in col_map:
            continue
        for tgt, keys in PO_KEYWORDS:
            if tgt not in used and any(k in n for k in keys):
                col_map[c] = tgt; used.add(tgt); break
    unmapped = [str(c) for c in columns if c not in col_map]
    if unmapped:
        warnings.append(f"Kolom tidak dipakai: {', '.join(unmapped)}")
    return col_map, warnings


def _is_po_master(cols):
    n = {" ".join(str(c).lower().split()) for c in cols}
    return "po number" in n and "item" in n and ("received" in n or "remaining (pcs)" in n)


def _parse_po_master(df):
    """The purchasing team's 'Master List' sheet: one row per PO line, history + ongoing.
    Columns used: Tgl PO, PO Number, Item (= SKU), Description, QTY, Received, Tgl Terima Barang,
    Ekspedisi, Note, ETA Date. Returns (lines, warnings)."""
    norm = {" ".join(str(c).lower().split()): c for c in df.columns}
    g = lambda *names: next((norm[k] for k in names if k in norm), None)
    c_date, c_po, c_sku, c_desc = g("tgl po", "po date"), g("po number"), g("item", "item code", "sku"), g("description")
    c_qty, c_rec, c_rdate = g("qty"), g("received"), g("tgl terima barang", "received date")
    c_cour, c_note, c_eta = g("ekspedisi"), g("note"), g("eta date", "eta")
    if not (c_po and c_sku and c_qty):
        raise HTTPException(400, "Kolom PO Number, Item dan QTY wajib ada.")
    warnings, lines, seen = [], [], {}
    bad_dates = bad_qty = 0

    def to_date(v):
        if v is None or (isinstance(v, float) and pd.isna(v)) or str(v).strip() in ("", "-", "#REF!"):
            return None
        iso = isinstance(v, (datetime, date, pd.Timestamp)) or str(v).strip()[:4].isdigit()
        d = pd.to_datetime(v, errors="coerce", dayfirst=not iso)
        if pd.isna(d) or d.year < 2000 or d.year > 2100:
            return "bad"
        return d.date()

    for _, r in df.iterrows():
        sku = str(r[c_sku]).strip() if pd.notna(r[c_sku]) else ""
        po = str(r[c_po]).strip() if pd.notna(r[c_po]) else ""
        if not sku or sku.lower() in ("nan", "item") or not po:
            continue
        qty = pd.to_numeric(r[c_qty], errors="coerce")
        if pd.isna(qty) or qty <= 0:
            bad_qty += 1
            continue
        rec = pd.to_numeric(r[c_rec], errors="coerce") if c_rec else None
        rec = 0 if rec is None or pd.isna(rec) else max(0, int(rec))
        od, rd, eta = (to_date(r[c]) if c else None for c in (c_date, c_rdate, c_eta))
        for v in (od, rd, eta):
            bad_dates += v == "bad"
        od, rd, eta = (None if v == "bad" else v for v in (od, rd, eta))
        n = seen.get((po, sku), 0)
        seen[(po, sku)] = n + 1
        qty = int(qty)
        status = "completed" if rec >= qty else ("partial" if rec > 0 else "pending")
        lines.append({
            "line_key": f"{po}|{sku}|{n + 1}", "po_number": po, "sku": sku,
            "description": str(r[c_desc]).strip() if c_desc and pd.notna(r[c_desc]) else None,
            "qty_ordered": qty, "qty_received": rec, "order_date": od, "expected_date": eta,
            "received_date": rd if rec > 0 else None, "status": status,
            "courier": str(r[c_cour]).strip() if c_cour and pd.notna(r[c_cour]) and str(r[c_cour]).strip() not in ("-", "#REF!") else None,
            "notes": str(r[c_note]).strip() if c_note and pd.notna(r[c_note]) and str(r[c_note]).strip() not in ("-", "#REF!") else None,
        })
    if bad_dates:
        warnings.append(f"{bad_dates} tanggal tidak valid (mis. tahun 0206) dikosongkan — cek di sheet")
    if bad_qty:
        warnings.append(f"{bad_qty} baris tanpa QTY dilewati")
    over = sum(1 for l in lines if l["qty_received"] > l["qty_ordered"])
    if over:
        warnings.append(f"{over} baris diterima lebih dari dipesan (ditandai Over)")
    return lines, warnings
