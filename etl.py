"""
ETL Module — membersihkan data mentah Accurate menjadi format siap-load.
Logic diambil dari ETL_Penjualan_ACCURATE.ipynb.
"""

import pandas as pd
import numpy as np
import re
import os


def clean_sales_data(filepath: str) -> dict:
    """
    Membersihkan file Excel mentah dari Accurate.
    
    Returns dict:
        - df: DataFrame bersih
        - stats: dict statistik proses ETL
        - errors: list error messages (jika ada)
    """
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

    is_footer = df.apply(
        lambda row: row.astype(str).str.contains(
            r"ACCURATE Accounting|Printed on|Page \d+ of \d+", na=False
        ).any(), axis=1
    )

    drop_mask = is_header | is_footer | df.index.isin(pre_header_idxs)
    # Juga drop baris sebelum header pertama (metadata)
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

    # Forward fill nama produk
    df["product_raw"] = np.where(is_product_header, df[inv_col], np.nan)
    df["product_raw"] = df["product_raw"].ffill()

    # ── Step 6: Extract SKU dari nama produk ──────────────────
    # Format: "299.067.002.001.01 - Sarung Pencuci AC Uk.80x80 CM"
    def extract_sku_name(raw_name):
        if pd.isna(raw_name):
            return "0", str(raw_name)
        s = str(raw_name).strip()
        # Pattern: SKU (angka+titik) diikuti " - " lalu nama produk
        match = re.match(r"^([\d.]+)\s*[-–]\s*(.+)$", s)
        if match:
            return match.group(1).strip(), match.group(2).strip()
        # Fallback: coba split by " - "
        if " - " in s:
            parts = s.split(" - ", 1)
            return parts[0].strip(), parts[1].strip()
        return "0", s

    sku_names = df["product_raw"].apply(extract_sku_name)
    df["SKU"] = sku_names.apply(lambda x: x[0])
    df["product_name"] = sku_names.apply(lambda x: x[1])

    # ── Step 7: Hanya simpan baris transaksi ──────────────────
    before_rows = len(df)
    df = df[is_transaction].reset_index(drop=True)
    stats["non_transaction_removed"] = before_rows - len(df)

    # ── Step 8: Bangun DataFrame final ────────────────────────
    final_cols = {"SKU": df["SKU"], "product_name": df["product_name"]}
    for col_pos, col_name in sorted(col_map.items()):
        if col_pos in df.columns and col_name not in ("invoice_date",):
            # invoice_date sudah dipakai untuk forward fill, ambil dari kolom asli
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
            # Fallback: coba format lain
            mask = df[dc].isna()
            if mask.any():
                df.loc[mask, dc] = pd.to_datetime(
                    df.loc[mask, dc.replace("_", " ")].astype(str) if dc != "date" else df.loc[mask, "date"],
                    errors="coerce"
                )

    if "year" in df.columns:
        df["year"] = pd.to_numeric(df["year"], errors="coerce").astype("Int64")

    # Bersihkan string columns
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

    # Hitung GPAO (%) jika ada
    if "GPAO" in df.columns and "amount" in df.columns:
        df["GPAO (%)"] = np.where(
            df["amount"] != 0,
            df["GPAO"] / df["amount"],
            0
        )

    # Drop kolom yang gak perlu
    drop_cols = ["invoice_date", "description"]
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
