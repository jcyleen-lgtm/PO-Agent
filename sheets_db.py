"""
sheets_db.py
Google Sheets sebagai database permanen PO Agent.
Handles read/write untuk Stok, Penjualan, PO Berjalan.
"""
import json
import os
import pandas as pd
import gspread
from google.oauth2.service_account import Credentials
from datetime import datetime

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

SPREADSHEET_ID = os.getenv("GOOGLE_SHEETS_ID", "1Yid0-NhwQTBj015xaLjymU8CxPJjLqifX5Zl_ODYbVo")

# Tab names di Google Sheets
TAB_STOK       = "Stok"
TAB_PENJUALAN  = "Penjualan"
TAB_PO         = "PO Berjalan"
TAB_HISTORY    = "History Run"


def get_client() -> gspread.Client:
    """Connect ke Google Sheets via service account."""
    # Coba dari env var dulu (GitHub Actions), lalu dari file lokal
    creds_json = os.getenv("GOOGLE_CREDENTIALS_JSON")
    if creds_json:
        creds_dict = json.loads(creds_json)
    else:
        # Lokal: baca dari file
        creds_path = os.getenv("GOOGLE_CREDENTIALS_PATH", "credentials.json")
        with open(creds_path) as f:
            creds_dict = json.load(f)

    creds = Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
    return gspread.authorize(creds)


def get_sheet(tab_name: str):
    """Ambil worksheet by name, buat kalau belum ada."""
    client = get_client()
    spreadsheet = client.open_by_key(SPREADSHEET_ID)

    try:
        return spreadsheet.worksheet(tab_name)
    except gspread.WorksheetNotFound:
        return spreadsheet.add_worksheet(title=tab_name, rows=5000, cols=30)


# ── WRITE — Upload data baru ke Sheets ────────────────────────────────────

def upload_stok(df: pd.DataFrame, tanggal: str = None):
    """
    Upload data stok ke tab 'Stok'.
    Setiap upload REPLACE data dengan tanggal tersebut.
    """
    if tanggal is None:
        tanggal = datetime.now().strftime("%Y-%m-%d")

    ws = get_sheet(TAB_STOK)

    # Baca data yang sudah ada
    existing = ws.get_all_records()
    df_existing = pd.DataFrame(existing) if existing else pd.DataFrame()

    # Hapus data dengan tanggal yang sama (replace)
    if not df_existing.empty and "Tanggal" in df_existing.columns:
        df_existing = df_existing[df_existing["Tanggal"] != tanggal]

    # Tambah kolom tanggal ke data baru
    df_new = df.copy()
    df_new.insert(0, "Tanggal", tanggal)

    # Gabungkan
    df_combined = pd.concat([df_existing, df_new], ignore_index=True)

    # Update sheet
    ws.clear()
    ws.update([df_combined.columns.tolist()] + df_combined.fillna("").values.tolist())
    print(f"   ✓ Stok {tanggal}: {len(df_new)} produk → Sheets")


def upload_penjualan(df: pd.DataFrame, periode: str):
    """
    Upload data penjualan ke tab 'Penjualan'.
    Periode = nama sheet dari Accurate (misal: '9/6 - 15/6' atau 'JUNE')
    Setiap periode disimpan sebagai kolom baru (append by periode).
    """
    ws = get_sheet(TAB_PENJUALAN)
    existing = ws.get_all_records()
    df_existing = pd.DataFrame(existing) if existing else pd.DataFrame()

    # Data baru: Product → Quantity untuk periode ini
    df_new = df[["Product", "Quantity"]].copy()
    df_new = df_new.rename(columns={"Quantity": periode})
    df_new = df_new.set_index("Product")

    if df_existing.empty:
        df_combined = df_new.reset_index()
    else:
        df_existing = df_existing.set_index("Product") if "Product" in df_existing.columns else df_existing
        # Drop kolom periode yang sama kalau sudah ada (replace)
        if periode in df_existing.columns:
            df_existing = df_existing.drop(columns=[periode])
        df_combined = df_existing.join(df_new, how="outer").fillna(0).reset_index()

    ws.clear()
    ws.update([df_combined.columns.tolist()] + df_combined.fillna("").values.tolist())
    print(f"   ✓ Penjualan [{periode}]: {len(df_new)} produk → Sheets")


def upload_po_berjalan(df: pd.DataFrame, tanggal: str = None):
    """Upload PO berjalan — replace setiap upload."""
    if tanggal is None:
        tanggal = datetime.now().strftime("%Y-%m-%d")

    ws = get_sheet(TAB_PO)
    df_new = df.copy()
    df_new.insert(0, "Tanggal Update", tanggal)

    ws.clear()
    ws.update([df_new.columns.tolist()] + df_new.fillna("").values.tolist())
    print(f"   ✓ PO Berjalan {tanggal}: {len(df_new)} PO → Sheets")


def log_history(run_type: str, ringkasan: dict):
    """Catat setiap run ke tab History."""
    ws = get_sheet(TAB_HISTORY)
    existing = ws.get_all_records()

    row = {
        "Timestamp":    datetime.now().strftime("%Y-%m-%d %H:%M"),
        "Tipe Run":     run_type,
        "Total Produk": ringkasan.get("total_produk", 0),
        "Perlu PO":     ringkasan.get("perlu_po", 0),
        "Urgent":       ringkasan.get("urgent", 0),
        "Normal":       ringkasan.get("normal", 0),
        "Skip":         ringkasan.get("skip_po_berjalan", 0),
    }

    if not existing:
        ws.update([list(row.keys()), list(row.values())])
    else:
        ws.append_row(list(row.values()))


# ── READ — Ambil data dari Sheets untuk analisis ──────────────────────────

def read_stok_latest() -> pd.DataFrame:
    """Ambil data stok terbaru (tanggal paling recent)."""
    ws = get_sheet(TAB_STOK)
    records = ws.get_all_records()
    if not records:
        return pd.DataFrame()

    df = pd.DataFrame(records)
    if "Tanggal" not in df.columns:
        return df

    # Ambil tanggal terbaru
    latest = df["Tanggal"].max()
    df_latest = df[df["Tanggal"] == latest].drop(columns=["Tanggal"])
    print(f"   ✓ Stok terbaru ({latest}): {len(df_latest)} produk ← Sheets")
    return df_latest


def read_penjualan_all() -> pd.DataFrame:
    """Ambil semua data penjualan (semua periode) dari Sheets."""
    ws = get_sheet(TAB_PENJUALAN)
    records = ws.get_all_records()
    if not records:
        return pd.DataFrame()

    df = pd.DataFrame(records)
    print(f"   ✓ Penjualan: {len(df)} produk, {len(df.columns)-1} periode ← Sheets")
    return df


def read_po_berjalan() -> pd.DataFrame:
    """Ambil PO berjalan terbaru dari Sheets."""
    ws = get_sheet(TAB_PO)
    records = ws.get_all_records()
    if not records:
        return pd.DataFrame()

    df = pd.DataFrame(records)
    if "Tanggal Update" in df.columns:
        df = df.drop(columns=["Tanggal Update"])
    print(f"   ✓ PO Berjalan: {len(df)} PO ← Sheets")
    return df


def get_penjualan_last_n_weeks(n: int = 8) -> pd.DataFrame:
    """
    Ambil data penjualan N minggu terakhir dari Sheets.
    Dipakai untuk hitung avg mingguan (exclude periode kosong).
    """
    df = read_penjualan_all()
    if df.empty:
        return df

    # Kolom selain Product = periode penjualan
    prod_col = "Product" if "Product" in df.columns else df.columns[0]
    period_cols = [c for c in df.columns if c != prod_col]

    # Ambil N periode terakhir
    last_n = period_cols[-n:] if len(period_cols) >= n else period_cols

    result = df[[prod_col] + last_n].copy()
    return result
