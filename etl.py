"""
ETL Module — membersihkan data mentah Accurate menjadi format siap-load.
Logic diambil dari ETL_Penjualan_ACCURATE.ipynb.
Includes Master SKU lookup for products without numeric SKU prefix.
Supports DB-based product lookup as primary SKU resolver.
"""

import pandas as pd
import numpy as np
import re
import os
import hashlib

from src.parsers.name_match import group_skus, norm_name


def load_master_sku(filepath: str = None) -> dict:
    """Load master SKU mapping from Excel. Returns {description_lower: sku}."""
    if filepath and os.path.exists(filepath):
        df = pd.read_excel(filepath, engine="openpyxl")
        df.columns = [c.strip() for c in df.columns]
        sku_col = next((c for c in df.columns if 'sku' in c.lower()), None)
        desc_col = next((c for c in df.columns if 'desc' in c.lower() or 'name' in c.lower() or 'product' in c.lower()), None)
        if sku_col and desc_col:
            mapping = {}
            for _, row in df.iterrows():
                if pd.notna(row[sku_col]) and pd.notna(row[desc_col]):
                    mapping[str(row[desc_col]).strip().lower()] = str(row[sku_col]).strip()
            return mapping
    return {}


def load_product_lookup_from_db(engine) -> dict:
    """Load {product_name_lower: sku} from products table in DB.
    This is the primary SKU resolver since Accurate exports use plain
    product names without SKU prefixes."""
    try:
        from src.repositories import products as product_repo
        with engine.connect() as conn:
            rows = product_repo.lookup_rows_worst_first(conn)
            mapping = NameLookup()
            for r in rows:
                if r[1]:
                    name, sku = str(r[1]).strip(), str(r[0]).strip()
                    mapping[name.lower()] = sku
                    mapping.exact[name] = sku
            return mapping
    except Exception:
        return {}


class NameLookup(dict):
    """{name_lower: sku} plus `.exact` {name as written: sku}. Accurate sometimes has two items whose
    names differ only in capitals (e.g. a parent code and its variant); the exact spelling decides first."""
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.exact = {}


def clean_sales_data(filepath: str, master_sku: dict = None, db_product_lookup: dict = None) -> dict:
    """
    Membersihkan file Excel mentah dari Accurate.

    Args:
        filepath: path ke file Excel mentah
        master_sku: dict {description_lower: sku} untuk lookup dari file
        db_product_lookup: dict {product_name_lower: sku} dari DB products table

    Returns dict:
        - df: DataFrame bersih
        - stats: dict statistik proses ETL
        - errors: list error messages (jika ada)
    """
    if master_sku is None:
        master_sku = {}
    if db_product_lookup is None:
        db_product_lookup = {}

    stats = {}
    errors = []

    # ── Step 1: Baca file ─────────────────────────────────────
    ext = os.path.splitext(filepath)[1].lower()
    engine = "xlrd" if ext == ".xls" else "openpyxl"

    try:
        raw = pd.read_excel(filepath, engine=engine, header=None)
    except Exception as e:
        return {"df": None, "stats": {}, "errors": [f"Gagal membaca file: {e}"]}

    stats["raw_rows"] = len(raw)
    stats["raw_cols"] = len(raw.columns)

    df = raw.copy()

    # ── Step 2: Hapus kolom kosong ────────────────────────────
    before_cols = len(df.columns)
    df = df.dropna(axis=1, how="all")
    df.columns = range(len(df.columns))
    stats["cols_dropped"] = before_cols - len(df.columns)

    # ── Step 3: Cari header row ───────────────────────────────
    header_idx = None
    for idx, row in df.iterrows():
        if row.astype(str).str.strip().eq("Invoice Date").any():
            header_idx = idx
            break

    if header_idx is None:
        return {"df": None, "stats": stats, "errors": ["Header 'Invoice Date' tidak ditemukan. Pastikan file dari Accurate."]}

    stats["header_row"] = int(header_idx)

    # Mapping kolom
    header_row = df.iloc[header_idx]
    pre_header = df.iloc[header_idx - 1] if header_idx > 0 else pd.Series(dtype=object)

    name_to_target = {
        "Invoice Date": "invoice_date",
        "Description": "description",
        "Quantity": "quantity",
        "Amount": "amount",
        "COGS Amount": "cogs",
        "Gross Profit": "gross_profit",
        "Date": "date",
        "Week": "week",
        "Month": "month",
        "Quarter": "quarter",
        "Year": "year",
    }

    col_map = {}
    for col_pos in df.columns:
        val = str(header_row[col_pos]).strip() if pd.notna(header_row[col_pos]) else ""
        if val in name_to_target:
            col_map[col_pos] = name_to_target[val]

    # Cari kolom unit dari pre-header
    for col_pos in df.columns:
        if col_pos < len(pre_header) and pd.notna(pre_header.get(col_pos)):
            if "Item Unit" in str(pre_header[col_pos]):
                col_map[col_pos] = "unit"

    if "invoice_date" not in col_map.values():
        return {"df": None, "stats": stats, "errors": ["Kolom 'Invoice Date' tidak ditemukan."]}

    inv_col = next(k for k, v in col_map.items() if v == "invoice_date")
    qty_col = next((k for k, v in col_map.items() if v == "quantity"), None)

    if qty_col is None:
        return {"df": None, "stats": stats, "errors": ["Kolom 'Quantity' tidak ditemukan."]}

    stats["columns_found"] = list(col_map.values())

    # ── Step 4: Hapus header berulang + footer ────────────────
    before_rows = len(df)

    is_header = df[inv_col].astype(str).str.strip() == "Invoice Date"

    pre_header_idxs = set()
    for i in df.index[is_header]:
        if i - 1 in df.index:
            pre_header_idxs.add(i - 1)

    # Column-wise (vectorised) instead of row-by-row apply: same result, ~10x faster on big exports.
    footer_pat = r"ACCURATE Accounting|Printed on|Page \d+ of \d+"
    is_footer = pd.Series(False, index=df.index)
    for c in df.columns:
        is_footer |= df[c].astype(str).str.contains(footer_pat, na=False, regex=True)

    drop_mask = is_header | is_footer | df.index.isin(pre_header_idxs)
    drop_mask = drop_mask | (df.index <= header_idx)

    df = df[~drop_mask].reset_index(drop=True)
    stats["junk_rows_removed"] = before_rows - len(df)

    # ── Step 5: Identifikasi & forward fill product name ─────
    is_product_header = df[inv_col].notna() & df[qty_col].isna()
    is_subtotal = df[inv_col].isna() & df[qty_col].notna()
    is_transaction = ~is_product_header & ~is_subtotal & df[inv_col].notna()

    stats["product_headers"] = int(is_product_header.sum())
    stats["subtotal_rows"] = int(is_subtotal.sum())
    stats["transaction_rows"] = int(is_transaction.sum())

    df["product_raw"] = np.where(is_product_header, df[inv_col], np.nan)
    df["product_raw"] = df["product_raw"].ffill()

    # ── Step 6: Extract SKU dari nama produk ──────────────────
    # Merge all lookup sources: DB products (primary) > master_sku file (fallback)
    # DB lookup takes priority because it's the most up-to-date source
    combined_lookup = {}
    combined_lookup.update(master_sku)        # file-based (lower priority)
    combined_lookup.update(db_product_lookup)  # DB-based (higher priority)
    exact_lookup = getattr(db_product_lookup, "exact", {}) or {}
    # Group / category codes (a code with codes under it, e.g. 299.052.02.011 "Tang") are never sold:
    # keep them out of the lookup so no sales row can land on a category.
    groups = group_skus(set(combined_lookup.values()) | set(exact_lookup.values()))
    exact_lookup = {k: v for k, v in exact_lookup.items() if v not in groups}
    norm_lookup = {norm_name(k): v for k, v in combined_lookup.items() if v not in groups}
    stats["group_codes_excluded"] = len(groups)

    # Auto-generate stable SKUs for unmatched products
    # Uses hash of product name so re-uploads produce the same SKU
    auto_sku_cache = {}

    def _generate_sku(product_name: str) -> str:
        """Generate a deterministic SKU from product name: AUTO-<8hex>"""
        h = hashlib.md5(product_name.strip().lower().encode()).hexdigest()[:8].upper()
        return f"AUTO-{h}"

    def extract_sku_name(raw_name):
        if pd.isna(raw_name):
            return "0", str(raw_name)
        s = str(raw_name).strip()
        # Pattern 1: numeric SKU (digits+dots) followed by separator then name
        match = re.match(r"^([\d.]+)\s*[-–]\s*(.+)$", s)
        if match:
            return match.group(1).strip(), match.group(2).strip()
        # Pattern 2a: same spelling incl. capitals (DB products)
        if s in exact_lookup:
            return exact_lookup[s], s
        # Pattern 2b: same name ignoring capitals and repeated spaces (DB + master file).
        # No substring fallback: it matched "Sarung tangan ..." to the category "Tang".
        lookup_key = norm_name(s)
        if lookup_key in norm_lookup:
            return norm_lookup[lookup_key], s
        # Pattern 3: unknown name -> stable AUTO- code, listed in stats["unmatched_names"]
        if lookup_key not in auto_sku_cache:
            auto_sku_cache[lookup_key] = _generate_sku(s)
        return auto_sku_cache[lookup_key], s

    sku_names = df["product_raw"].apply(extract_sku_name)
    df["SKU"] = sku_names.apply(lambda x: x[0])
    df["product_name"] = sku_names.apply(lambda x: x[1])

    # Count SKU resolution stats
    is_auto = df["SKU"].str.startswith("AUTO-")
    stats["sku_from_lookup"] = int((~is_auto & (df["SKU"] != "0")).sum())
    stats["sku_auto_generated"] = int(is_auto.sum())
    stats["sku_unmatched"] = int((df["SKU"] == "0").sum())
    stats["auto_sku_products"] = len(auto_sku_cache)
    stats["unmatched_names"] = sorted(df.loc[is_auto & is_transaction, "product_name"].dropna().astype(str).unique().tolist())

    # ── Step 7: Hanya simpan baris transaksi ──────────────────
    before_rows = len(df)
    df = df[is_transaction].reset_index(drop=True)
    stats["non_transaction_removed"] = before_rows - len(df)

    # ── Step 8: Bangun DataFrame final ────────────────────────
    final_cols = {"SKU": df["SKU"], "product_name": df["product_name"]}
    for col_pos, col_name in sorted(col_map.items()):
        if col_pos in df.columns and col_name not in ("invoice_date",):
            final_cols[col_name] = df[col_pos]
        elif col_name == "invoice_date":
            final_cols[col_name] = df[col_pos]

    df = pd.DataFrame(final_cols)

    # ── Step 9: Parse angka format Indonesia ──────────────────
    def parse_id_number(val):
        if pd.isna(val):
            return np.nan
        s = str(val).strip()
        if s in ("", "-"):
            return np.nan
        s = s.replace(".", "").replace(",", ".")
        try:
            return float(s)
        except ValueError:
            return np.nan

    numeric_cols = ["quantity", "amount", "cogs", "gross_profit"]
    for col in numeric_cols:
        if col in df.columns:
            df[col] = df[col].apply(parse_id_number)

    # ── Step 10: Parse tanggal ────────────────────────────────
    for dc in ["invoice_date", "date"]:
        if dc in df.columns:
            df[dc] = pd.to_datetime(df[dc], format="%d %b %Y", errors="coerce")
            mask = df[dc].isna()
            if mask.any():
                df.loc[mask, dc] = pd.to_datetime(df.loc[mask, dc], errors="coerce")

    if "year" in df.columns:
        df["year"] = pd.to_numeric(df["year"], errors="coerce").astype("Int64")

    for sc in ["product_name", "description", "unit", "week", "month", "quarter"]:
        if sc in df.columns:
            df[sc] = df[sc].astype(str).replace({"nan": np.nan, "None": np.nan})
            df[sc] = df[sc].str.strip()

    # ── Step 11: Rename untuk konsistensi ─────────────────────
    rename_map = {}
    if "gross_profit" in df.columns:
        rename_map["gross_profit"] = "GPAO"
    if "cogs" in df.columns:
        rename_map["cogs"] = "COGS"
    df = df.rename(columns=rename_map)

    if "GPAO" in df.columns and "amount" in df.columns:
        df["GPAO (%)"] = np.where(df["amount"] != 0, df["GPAO"] / df["amount"], 0)

    # ── Step 11b: Filter negative quantity (returns dari Accurate) ──
    if "quantity" in df.columns:
        neg_mask = df["quantity"] < 0
        stats["negative_qty_removed"] = int(neg_mask.sum())
        df = df[~neg_mask].reset_index(drop=True)

    drop_cols = ["description"]  # KEEP invoice_date for downstream date handling
    df = df.drop(columns=[c for c in drop_cols if c in df.columns], errors="ignore")

    # ── Step 12: Final stats ──────────────────────────────────
    stats["clean_rows"] = len(df)
    stats["clean_cols"] = len(df.columns)
    stats["unique_products"] = int(df["product_name"].nunique())
    stats["unique_skus"] = int(df["SKU"].nunique())

    if "date" in df.columns:
        valid_dates = df["date"].dropna()
        if len(valid_dates) > 0:
            stats["date_min"] = str(valid_dates.min().date())
            stats["date_max"] = str(valid_dates.max().date())

    if "amount" in df.columns:
        stats["total_revenue"] = float(df["amount"].sum())

    stats["final_columns"] = list(df.columns)

    return {"df": df, "stats": stats, "errors": errors}