"""
main.py — v3
2 mode run:
1. weekly   — analisis mingguan otomatis (rabu via GitHub Actions)
2. restock  — rekomendasi restock on-demand (dari web app)
3. upload   — upload data baru ke Google Sheets (dari web app)
"""
import os
import sys
from dotenv import load_dotenv

load_dotenv()


def check_setup():
    errors = []
    if not os.getenv("ANTHROPIC_API_KEY"):
        errors.append("ANTHROPIC_API_KEY belum diset")
    if not os.getenv("GOOGLE_CREDENTIALS_JSON") and not os.path.exists("credentials.json"):
        errors.append("Google credentials belum diset (GOOGLE_CREDENTIALS_JSON atau credentials.json)")
    if errors:
        print("\n⚠️  Masalah konfigurasi:")
        for e in errors:
            print(f"   ❌ {e}")
        sys.exit(1)


def run_weekly():
    """Mode weekly: analisis performa + stock alert (rabu otomatis)."""
    print("=" * 56)
    print("  📊  PO AGENT v3 — Weekly Analysis")
    print("=" * 56)

    from data_loader import load_from_sheets, calculate_all
    from agent import run_weekly_analysis
    from exporter import export_weekly
    from emailer import send_weekly_report
    from sheets_db import log_history

    KIRIM_EMAIL = os.getenv("KIRIM_EMAIL", "true").lower() == "true"

    # Load dari Sheets
    data    = load_from_sheets()
    metrics = calculate_all(data)

    if not metrics:
        print("❌ Data tidak cukup untuk analisis")
        sys.exit(1)

    # AI analysis
    weekly_result = run_weekly_analysis(metrics)

    # Print ringkasan
    ring = metrics.get("ringkasan", {})
    print(f"\n{'─'*56}")
    print(f"📊 WEEKLY ANALYSIS RESULT")
    print(f"   {weekly_result.get('ringkasan_minggu', '')}")
    if weekly_result.get("alert_best_seller"):
        print(f"\n   🚨 Best Seller kritis: {', '.join(weekly_result['alert_best_seller'])}")
    print(f"{'─'*56}")

    # Export Excel
    excel_path = export_weekly(weekly_result, metrics)

    # Kirim email
    if KIRIM_EMAIL:
        send_weekly_report(weekly_result, metrics, excel_path)
    else:
        print("📧 Email dilewati")

    # Log ke Sheets
    log_history("weekly", ring)

    print("\n✅ Weekly analysis selesai!")


def run_restock():
    """Mode restock: rekomendasi on-demand."""
    print("=" * 56)
    print("  📦  PO AGENT v3 — Restock Recommendations")
    print("=" * 56)

    from data_loader import load_from_sheets, calculate_all
    from agent import run_restock_analysis
    from exporter import export_restock
    from emailer import send_restock_report
    from sheets_db import log_history

    KIRIM_EMAIL = os.getenv("KIRIM_EMAIL", "true").lower() == "true"

    # Load dari Sheets
    data    = load_from_sheets()
    metrics = calculate_all(data)

    if not metrics:
        print("❌ Data tidak cukup untuk analisis")
        sys.exit(1)

    # AI restock analysis
    restock_result = run_restock_analysis(metrics)

    # Print ringkasan
    ring = metrics.get("ringkasan", {})
    reko = restock_result.get("rekomendasi_po", [])
    print(f"\n{'─'*56}")
    print(f"📦 RESTOCK RESULT")
    print(f"   {restock_result.get('analisis_singkat', '')}")
    urgent = [r for r in reko if r.get("prioritas") == "URGENT"]
    if urgent:
        print(f"\n   🚨 URGENT ({len(urgent)} produk):")
        for r in urgent[:5]:
            print(f"      • [{r.get('tier','-')}] {r['nama_produk']} — {r['qty_rekomendasi']} pcs")
    print(f"{'─'*56}")

    # Export Excel
    excel_path = export_restock(restock_result, metrics)

    # Kirim email
    if KIRIM_EMAIL:
        send_restock_report(restock_result, metrics, excel_path)
    else:
        print("📧 Email dilewati")

    # Log ke Sheets
    log_history("restock", ring)

    print("\n✅ Restock recommendations selesai!")


def run_upload():
    """
    Mode upload: upload file Excel baru ke Google Sheets.
    File path dari environment variable.
    """
    print("=" * 56)
    print("  📤  PO AGENT v3 — Upload Data ke Google Sheets")
    print("=" * 56)

    from data_loader import load_stok_from_file, load_penjualan_from_file, load_po_from_file
    from sheets_db import upload_stok, upload_penjualan, upload_po_berjalan

    stok_path = os.getenv("UPLOAD_STOK_PATH")
    penj_path = os.getenv("UPLOAD_PENJUALAN_PATH")
    po_path   = os.getenv("UPLOAD_PO_PATH")
    tanggal   = os.getenv("UPLOAD_TANGGAL")

    print("📂 Mengupload data ke Google Sheets...")

    if stok_path and os.path.exists(stok_path):
        df_stok = load_stok_from_file(stok_path)
        upload_stok(df_stok, tanggal)

    if penj_path and os.path.exists(penj_path):
        sheets = load_penjualan_from_file(penj_path)
        for periode, df in sheets.items():
            upload_penjualan(df, periode)

    if po_path and os.path.exists(po_path):
        df_po = load_po_from_file(po_path)
        upload_po_berjalan(df_po, tanggal)

    print("\n✅ Upload selesai! Data tersimpan di Google Sheets.")


def main():
    mode = os.getenv("RUN_MODE", "weekly").lower()

    check_setup()

    if mode == "weekly":
        run_weekly()
    elif mode == "restock":
        run_restock()
    elif mode == "upload":
        run_upload()
    else:
        print(f"❌ Mode tidak dikenal: {mode}")
        print("   Mode yang tersedia: weekly, restock, upload")
        sys.exit(1)


if __name__ == "__main__":
    main()
