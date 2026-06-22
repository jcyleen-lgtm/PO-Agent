"""
data_loader.py — v3
Baca data dari Google Sheets (database permanen) atau upload file baru.
Logika tier mengikuti Master List: Best Seller, Uprising, Slow Moving, Deadweight, Sin.
Safety stock tier-aware dengan multiplier berbeda per tier.
Exclude periode kosong dari rata-rata (anti-stockout bias).
"""
import pandas as pd
import numpy as np
from datetime import datetime


# ── Konstanta ──────────────────────────────────────────────────────────────
LEAD_TIME_WEEKS   = 8    # Lead time standar: 2 bulan
TIER_MULTIPLIER   = {
    "1. Best Seller":  1.5,
    "2. Uprising":     1.5,
    "3. Slow Moving":  1.0,
    "4. Deadweight":   1.0,
    "5. Sin":          0.75,
}


# ── Auto-detect & clean format Accurate ───────────────────────────────────

def _read_excel_auto(path: str, sheet_name=0) -> pd.DataFrame:
    """Auto-detect engine dari magic bytes, bukan ekstensi."""
    with open(path, "rb") as f:
        magic = f.read(8)
    if magic[:4] == b"\xd0\xcf\x11\xe0":
        return pd.read_excel(path, engine="xlrd", header=0, sheet_name=sheet_name)
    elif magic[:2] == b"PK":
        return pd.read_excel(path, engine="openpyxl", header=0, sheet_name=sheet_name)
    else:
        try:
            return pd.read_excel(path, engine="openpyxl", header=0, sheet_name=sheet_name)
        except Exception:
            return pd.read_excel(path, engine="xlrd", header=0, sheet_name=sheet_name)


def is_leaf_sku(sku: str, all_skus: set = None) -> bool:
    """Deteksi SKU produk individual (bukan grup/subtotal)."""
    if pd.isna(sku):
        return False
    sku = str(sku).strip()
    if "-" in sku and "." not in sku:
        return False
    parts = sku.replace("-", ".").split(".")
    if len(parts) < 3:
        return False
    if all_skus:
        prefix = sku + "."
        for other in all_skus:
            if str(other).startswith(prefix):
                return False
    return True


def clean_accurate_export(df: pd.DataFrame) -> pd.DataFrame:
    """
    Auto-clean export Accurate.
    Support format stok (SKU, Product, Quantity) dan penjualan (Product, QTY).
    """
    cols      = df.columns.tolist()
    col_lower = [str(c).lower() for c in cols]

    has_sku_col = any(
        "unnamed: 0" in c or (("sku" in c or "kode" in c) and "item" not in c)
        for c in col_lower
    )

    if not has_sku_col:
        # Format penjualan: Product | QTY
        rename_map = {}
        for col in cols:
            lower = str(col).lower()
            if "item" in lower and "desc" in lower:
                rename_map[col] = "Product"
            elif lower in ("product", "nama produk", "produk"):
                rename_map[col] = "Product"
            elif "qty" in lower or "quantity" in lower:
                rename_map[col] = "Quantity"
        df = df.rename(columns=rename_map)

        if "Product" not in df.columns:
            return pd.DataFrame(columns=["SKU", "Product", "Quantity"])

        df = df.dropna(subset=["Product"])
        df["Product"]  = df["Product"].astype(str).str.strip()
        df             = df[df["Product"] != ""]
        df["Quantity"] = pd.to_numeric(df.get("Quantity", 0), errors="coerce").fillna(0)
        df["SKU"]      = df["Product"]
        df             = df.drop_duplicates(subset=["Product"], keep="first")
        return df[["SKU", "Product", "Quantity"]].reset_index(drop=True)

    else:
        # Format stok: rename by position
        pos_rename = {}
        if len(cols) >= 1:
            pos_rename[cols[0]] = "SKU"
        if len(cols) >= 2:
            pos_rename[cols[1]] = "Product"
        if len(cols) >= 3:
            qty_col = next(
                (c for c in cols[2:] if "qty" in str(c).lower() or "quantity" in str(c).lower()),
                cols[2],
            )
            pos_rename[qty_col] = "Quantity"

        df = df.rename(columns=pos_rename)

        if "SKU" not in df.columns or "Quantity" not in df.columns:
            return pd.DataFrame(columns=["SKU", "Product", "Quantity"])

        df        = df.dropna(subset=["SKU"])
        df["SKU"] = df["SKU"].astype(str).str.strip()

        all_skus = set(df["SKU"].tolist())
        df       = df[df["SKU"].apply(lambda s: is_leaf_sku(s, all_skus))].copy()

        df["Quantity"] = pd.to_numeric(df["Quantity"], errors="coerce").fillna(0)
        df             = df.drop_duplicates(subset=["SKU"], keep="first")

        if "Product" not in df.columns:
            df["Product"] = df["SKU"]

        return df[["SKU", "Product", "Quantity"]].reset_index(drop=True)


# ── Load dari file Excel (saat upload) ────────────────────────────────────

def load_stok_from_file(path: str) -> pd.DataFrame:
    df = _read_excel_auto(path)
    df = clean_accurate_export(df)
    print(f"   ✓ Stok      : {len(df)} produk (setelah cleaning)")
    return df


def load_penjualan_from_file(path: str) -> dict:
    """
    Baca file penjualan multi-sheet dari Accurate.
    Return dict: {nama_sheet: df}
    """
    with open(path, "rb") as f:
        magic = f.read(8)
    engine = "xlrd" if magic[:4] == b"\xd0\xcf\x11\xe0" else "openpyxl"

    xl      = pd.ExcelFile(path, engine=engine)
    sheets  = {}
    for sheet_name in xl.sheet_names:
        df = xl.parse(sheet_name)
        df = clean_accurate_export(df)
        if len(df) > 0:
            sheets[sheet_name] = df
            print(f"   ✓ Penjualan [{sheet_name}]: {len(df)} produk")
    return sheets


def load_po_from_file(path: str) -> pd.DataFrame:
    """Baca file PO berjalan dari Accurate."""
    df = _read_excel_auto(path)

    col_map = {}
    for col in df.columns:
        lower = str(col).lower()
        if "sku" in lower or "kode" in lower or "item" in lower:
            col_map[col] = "SKU"
        elif "name" in lower or "product" in lower or "desc" in lower or "nama" in lower:
            col_map[col] = "Product"
        elif "qty" in lower or "quantity" in lower or "jumlah" in lower:
            col_map[col] = "Qty Dipesan"
        elif "supplier" in lower or "vendor" in lower:
            col_map[col] = "Supplier"

    df = df.rename(columns=col_map)

    for col in ["SKU", "Product", "Qty Dipesan"]:
        if col not in df.columns:
            df[col] = ""
    if "Supplier" not in df.columns:
        df["Supplier"] = "-"

    df = df.dropna(subset=["SKU"])
    df["SKU"]         = df["SKU"].astype(str).str.strip()
    df["Qty Dipesan"] = pd.to_numeric(df["Qty Dipesan"], errors="coerce").fillna(0)

    print(f"   ✓ PO berjalan: {len(df)} PO aktif")
    return df[["SKU", "Product", "Qty Dipesan", "Supplier"]]


# ── Hitung metrik dari data Sheets ────────────────────────────────────────

def calc_weekly_avg_exclude_zero(df_penjualan: pd.DataFrame) -> pd.DataFrame:
    """
    Hitung rata-rata mingguan per produk.
    EXCLUDE periode dengan qty = 0 (anti-stockout bias).
    Mengikuti logika L2M Average di file Best Seller lo.
    """
    prod_col     = "Product" if "Product" in df_penjualan.columns else df_penjualan.columns[0]
    period_cols  = [c for c in df_penjualan.columns if c != prod_col]

    if not period_cols:
        return pd.DataFrame(columns=[prod_col, "Avg_Mingguan", "Total_Terjual", "Periode_Aktif"])

    result = []
    for _, row in df_penjualan.iterrows():
        product = row[prod_col]
        qtys    = [float(row[p]) for p in period_cols if pd.notna(row[p])]

        # Exclude periode yang qty = 0
        qtys_nonzero = [q for q in qtys if q > 0]

        avg_mingguan  = round(sum(qtys_nonzero) / len(qtys_nonzero), 2) if qtys_nonzero else 0
        total_terjual = sum(qtys)
        periode_aktif = len(qtys_nonzero)

        result.append({
            prod_col:        product,
            "Avg_Mingguan":  avg_mingguan,
            "Total_Terjual": total_terjual,
            "Periode_Aktif": periode_aktif,
        })

    return pd.DataFrame(result)


def calc_tier_ai(df_avg: pd.DataFrame, df_penjualan: pd.DataFrame) -> pd.DataFrame:
    """
    Tentukan tier otomatis per produk mengikuti logika Master List.
    Hanya dari produk yang pernah terjual (qty > 0).
    """
    prod_col    = "Product" if "Product" in df_avg.columns else df_avg.columns[0]
    period_cols = [c for c in df_penjualan.columns if c != prod_col]

    df = df_avg.copy()

    # Percentile hanya dari produk yang pernah terjual
    sold      = df[df["Avg_Mingguan"] > 0]["Avg_Mingguan"]
    p80       = sold.quantile(0.80) if len(sold) > 0 else 999
    p30       = sold.quantile(0.30) if len(sold) > 0 else 1
    p80       = max(p80, 1)
    p30       = max(p30, 1)

    # Hitung tren: bandingkan 4 minggu terakhir vs 4 minggu sebelumnya
    if len(period_cols) >= 8:
        recent   = period_cols[-4:]
        previous = period_cols[-8:-4]
    elif len(period_cols) >= 4:
        recent   = period_cols[-2:]
        previous = period_cols[:-2]
    else:
        recent   = period_cols
        previous = []

    def get_avg_period(row, cols):
        if not cols:
            return 0
        vals = [float(row.get(c, 0) or 0) for c in cols]
        nonzero = [v for v in vals if v > 0]
        return sum(nonzero) / len(nonzero) if nonzero else 0

    # Join dengan data penjualan untuk hitung tren
    df_with_sales = df.merge(
        df_penjualan[[prod_col] + period_cols],
        on=prod_col,
        how="left"
    )

    tiers = []
    trens = []
    pcts  = []

    for _, row in df_with_sales.iterrows():
        avg      = row["Avg_Mingguan"]
        avg_rec  = get_avg_period(row, recent)
        avg_prev = get_avg_period(row, previous)

        # Hitung % perubahan
        if avg_prev > 0:
            pct = ((avg_rec - avg_prev) / avg_prev) * 100
        else:
            pct = None

        # Tentukan tren
        if pct is None:
            tren = "→ Baru"
        elif pct > 20:
            tren = "↑ Naik"
        elif pct < -20:
            tren = "↓ Turun"
        else:
            tren = "→ Stabil"

        # Tentukan tier
        if avg == 0 and row.get("Total_Terjual", 0) == 0:
            tier = "4. Deadweight"
        elif avg == 0 and avg_prev >= p80:
            tier = "5. Sin"
        elif avg == 0:
            tier = "3. Slow Moving"
        elif avg >= p80:
            tier = "1. Best Seller"
        elif tren == "↑ Naik" and (pct or 0) > 20:
            tier = "2. Uprising"
        elif avg_prev >= p80 and tren == "↓ Turun":
            tier = "5. Sin"
        elif avg <= p30:
            tier = "3. Slow Moving"
        else:
            tier = "3. Slow Moving"

        tiers.append(tier)
        trens.append(tren)
        pcts.append(round(pct, 1) if pct is not None else None)

    df["Tier"]       = tiers
    df["Tren"]       = trens
    df["Pct_Change"] = pcts

    return df[[prod_col, "Avg_Mingguan", "Total_Terjual", "Periode_Aktif", "Tier", "Tren", "Pct_Change"]]


def calculate_restock(df_stok, df_po, df_tier) -> dict:
    """
    Hitung rekomendasi restock berdasarkan:
    - Safety Stock = Avg Mingguan × 8 minggu × tier multiplier
    - Coverage = (Stok + Incoming) / Avg Mingguan
    - Perlu PO kalau Coverage < Safety Stock (dalam minggu)
    """
    prod_col = "Product" if "Product" in df_tier.columns else df_tier.columns[0]

    # Incoming dari PO berjalan
    po_sku_col  = "SKU" if "SKU" in df_po.columns else df_po.columns[0]
    po_prod_col = "Product" if "Product" in df_po.columns else df_po.columns[1]
    incoming    = df_po.groupby(po_prod_col)["Qty Dipesan"].sum().reset_index()
    incoming.columns = [prod_col, "Incoming"]
    products_with_po = set(df_po[po_prod_col].unique())

    # Gabungkan
    stok_col = "Product" if "Product" in df_stok.columns else df_stok.columns[1]
    m = df_stok.rename(columns={stok_col: prod_col, "Quantity": "Stok_Sekarang"})
    m = m.merge(df_tier, on=prod_col, how="left")
    m = m.merge(incoming, on=prod_col, how="left")

    m["Avg_Mingguan"]  = m["Avg_Mingguan"].fillna(0)
    m["Incoming"]      = m["Incoming"].fillna(0)
    m["Tier"]          = m["Tier"].fillna("3. Slow Moving")
    m["Tren"]          = m["Tren"].fillna("→ Stabil")
    m["Stok_Sekarang"] = pd.to_numeric(m["Stok_Sekarang"], errors="coerce").fillna(0)

    # Safety Stock = Avg × 8 × multiplier tier
    def get_multiplier(tier):
        return TIER_MULTIPLIER.get(str(tier), 1.0)

    m["Multiplier"]    = m["Tier"].apply(get_multiplier)
    m["Safety_Stock"]  = (m["Avg_Mingguan"] * LEAD_TIME_WEEKS * m["Multiplier"]).round(0)

    # Coverage = berapa minggu stok bertahan
    m["Proyeksi_Stok"] = m["Stok_Sekarang"] + m["Incoming"]
    m["Coverage"]      = (m["Proyeksi_Stok"] / m["Avg_Mingguan"].replace(0, np.nan)).round(1)

    # Qty Order = Safety Stock - Proyeksi Stok
    m["Qty_Order"]     = (m["Safety_Stock"] - m["Proyeksi_Stok"]).clip(lower=0).round(0)

    # Ada PO berjalan?
    m["Ada_PO"]        = m[prod_col].isin(products_with_po)

    # Prioritas
    def get_priority(row):
        if row["Ada_PO"]:
            return "SKIP"
        if row["Qty_Order"] <= 0:
            return "CUKUP"
        coverage  = row["Coverage"] if pd.notna(row["Coverage"]) else 999
        tier      = str(row["Tier"])
        if coverage <= 2 or (tier == "1. Best Seller" and coverage <= 4):
            return "URGENT"
        return "NORMAL"

    m["Prioritas"] = m.apply(get_priority, axis=1)

    to_order = (
        m[m["Prioritas"].isin(["URGENT", "NORMAL"])]
        .copy()
        .sort_values("Prioritas", key=lambda x: x.map({"URGENT": 0, "NORMAL": 1}))
    )

    ringkasan = {
        "total_produk":     len(m),
        "perlu_po":         len(to_order),
        "urgent":           len(to_order[to_order["Prioritas"] == "URGENT"]),
        "normal":           len(to_order[to_order["Prioritas"] == "NORMAL"]),
        "skip_po_berjalan": len(m[m["Ada_PO"]]),
        "stok_cukup":       len(m[m["Prioritas"] == "CUKUP"]),
        "total_nilai_po":   0,
    }

    print(f"\n📊 Hasil kalkulasi:")
    print(f"   Perlu di-PO : {ringkasan['perlu_po']} (URGENT: {ringkasan['urgent']}, NORMAL: {ringkasan['normal']})")
    print(f"   Skip        : {ringkasan['skip_po_berjalan']} | Cukup: {ringkasan['stok_cukup']}")

    return {
        "semua_produk": m,
        "perlu_po":     to_order,
        "ringkasan":    ringkasan,
        "tier_summary": m["Tier"].value_counts().to_dict(),
    }


# ── Main loader — dari Google Sheets ──────────────────────────────────────

def load_from_sheets() -> dict:
    """Ambil semua data dari Google Sheets untuk analisis."""
    from sheets_db import (
        read_stok_latest,
        read_penjualan_all,
        read_po_berjalan,
        get_penjualan_last_n_weeks,
    )

    print("📂 Membaca data dari Google Sheets...")
    df_stok      = read_stok_latest()
    df_penjualan = read_penjualan_all()
    df_po        = read_po_berjalan()
    df_last8     = get_penjualan_last_n_weeks(8)

    return {
        "stok":      df_stok,
        "penjualan": df_penjualan,
        "po":        df_po,
        "last8":     df_last8,
    }


def calculate_all(data: dict) -> dict:
    """
    Hitung semua metrik dari data yang sudah di-load.
    """
    df_stok  = data["stok"]
    df_last8 = data["last8"]
    df_po    = data["po"]

    if df_stok.empty:
        print("⚠️  Data stok kosong di Sheets")
        return {}

    # Hitung avg mingguan (dari 8 minggu terakhir, exclude zero)
    df_avg  = calc_weekly_avg_exclude_zero(df_last8)

    # Tier otomatis
    df_tier = calc_tier_ai(df_avg, df_last8)

    # Kalkulasi restock
    metrics = calculate_restock(df_stok, df_po, df_tier)
    metrics["tier_data"] = df_tier

    return metrics
