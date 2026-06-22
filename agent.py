"""
agent.py — v3
2 mode:
1. weekly_analysis  — analisis performa produk mingguan (rabu otomatis)
2. restock_ondemand — rekomendasi restock on-demand dari web app
"""
import anthropic
import json
import os
import pandas as pd
from dotenv import load_dotenv

load_dotenv()


def _call_claude(prompt: str, system: str) -> dict:
    """Helper: panggil Claude API dan parse JSON response."""
    client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=4096,
        system=system,
        messages=[{"role": "user", "content": prompt}],
    )

    raw = response.content[0].text.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    raw = raw.strip()

    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"   ⚠️  Gagal parse JSON: {e}")
        return {}


# ── Mode 1: Weekly Analysis (Rabu otomatis) ───────────────────────────────

def run_weekly_analysis(metrics: dict) -> dict:
    """
    Analisis performa produk mingguan:
    - Produk yang naik/turun signifikan
    - Update tier
    - Alert Best Seller stok kritis
    """
    tier_data  = metrics.get("tier_data", pd.DataFrame())
    ringkasan  = metrics.get("ringkasan", {})
    semua      = metrics.get("semua_produk", pd.DataFrame())

    # Best Seller stok kritis
    bs_kritis = []
    if not semua.empty:
        prod_col = "Product" if "Product" in semua.columns else semua.columns[0]
        bs = semua[
            (semua["Tier"] == "1. Best Seller") &
            (semua["Prioritas"].isin(["URGENT", "NORMAL"]))
        ]
        bs_kritis = bs[prod_col].tolist()[:10]

    # Produk naik dan turun signifikan
    naik = turun = uprising_list = sin_list = []
    if not tier_data.empty:
        prod_col = "Product" if "Product" in tier_data.columns else tier_data.columns[0]
        naik     = tier_data[tier_data["Tren"] == "↑ Naik"][prod_col].tolist()[:10]
        turun    = tier_data[tier_data["Tren"] == "↓ Turun"][prod_col].tolist()[:10]
        uprising_list = tier_data[tier_data["Tier"] == "2. Uprising"][prod_col].tolist()[:10]
        sin_list      = tier_data[tier_data["Tier"] == "5. Sin"][prod_col].tolist()[:10]

    # Distribusi tier
    tier_dist = metrics.get("tier_summary", {})

    print("\n🤖 Menjalankan Weekly Analysis AI...")

    prompt = f"""Kamu adalah purchasing manager berpengalaman di perusahaan retail/distribusi Indonesia.
Analisis performa produk minggu ini dan berikan insight yang actionable.

RINGKASAN MINGGU INI:
- Total SKU aktif     : {ringkasan.get('total_produk', 0)}
- Perlu di-PO         : {ringkasan.get('perlu_po', 0)} produk
- URGENT              : {ringkasan.get('urgent', 0)} produk
- NORMAL              : {ringkasan.get('normal', 0)} produk
- Stok cukup          : {ringkasan.get('stok_cukup', 0)} produk
- Skip (PO berjalan)  : {ringkasan.get('skip_po_berjalan', 0)} produk

DISTRIBUSI TIER:
{json.dumps(tier_dist, ensure_ascii=False)}

PRODUK BEST SELLER STOK KRITIS (coverage rendah):
{', '.join(bs_kritis) if bs_kritis else 'Tidak ada'}

PRODUK TREN NAIK MINGGU INI:
{', '.join(naik) if naik else 'Tidak ada'}

PRODUK TREN TURUN MINGGU INI:
{', '.join(turun) if turun else 'Tidak ada'}

PRODUK UPRISING (potensial naik terus):
{', '.join(uprising_list) if uprising_list else 'Tidak ada'}

PRODUK SIN (pernah bagus, sekarang drop):
{', '.join(sin_list) if sin_list else 'Tidak ada'}

Balas HANYA dengan JSON valid berikut:
{{
  "ringkasan_minggu": "2-3 kalimat ringkasan kondisi stok dan performa minggu ini",
  "highlight_positif": ["produk/tren bagus minggu ini"],
  "highlight_negatif": ["produk/tren yang perlu diwaspadai"],
  "alert_best_seller": ["nama produk Best Seller yang stok kritis — perlu segera di-order"],
  "produk_uprising_watch": ["produk Uprising yang perlu diperhatikan — demand naik"],
  "produk_sin_watch": ["produk Sin yang perlu dievaluasi — pernah bagus tapi drop"],
  "rekomendasi_tindakan": ["list aksi yang perlu dilakukan tim purchasing minggu ini"],
  "saran_utama": "1 kalimat saran paling penting untuk purchasing manager hari ini"
}}"""

    system = (
        "Kamu adalah AI purchasing analyst untuk retail/distribusi Indonesia. "
        "Fokus pada insight actionable yang langsung bisa ditindaklanjuti tim purchasing. "
        "Gunakan Bahasa Indonesia. Jawab HANYA dalam JSON valid."
    )

    result = _call_claude(prompt, system)
    print("   ✓ Weekly analysis selesai")
    return result


# ── Mode 2: Restock On-Demand ─────────────────────────────────────────────

def run_restock_analysis(metrics: dict) -> dict:
    """
    Rekomendasi restock lengkap dengan analisis AI per produk.
    Dijalankan on-demand dari web app.
    """
    to_order  = metrics.get("perlu_po", pd.DataFrame())
    ringkasan = metrics.get("ringkasan", {})
    semua     = metrics.get("semua_produk", pd.DataFrame())

    if to_order.empty:
        return {
            "analisis_singkat": "Semua stok dalam kondisi aman. Tidak ada produk yang perlu di-PO saat ini.",
            "rekomendasi_po":   [],
            "produk_dilewati":  [],
            "insight_tier":     {},
            "total_estimasi_nilai": 0,
            "saran_tindakan":   "Tidak ada aksi purchasing yang diperlukan.",
        }

    # Format produk untuk prompt
    prod_col = "Product" if "Product" in to_order.columns else to_order.columns[0]
    produk_lines = []
    for _, r in to_order.iterrows():
        coverage = r.get("Coverage", "?")
        coverage_str = f"{coverage:.1f} minggu" if pd.notna(coverage) else "tidak diketahui"
        produk_lines.append(
            f"- {r[prod_col]} | "
            f"Prioritas: {r['Prioritas']} | Tier: {r.get('Tier', '-')} | "
            f"Tren: {r.get('Tren', '-')} | "
            f"Stok: {int(r.get('Stok_Sekarang', 0))} | "
            f"Coverage: {coverage_str} | "
            f"Safety Stock: {int(r.get('Safety_Stock', 0))} minggu | "
            f"Qty Order: {int(r.get('Qty_Order', 0))}"
        )

    # Produk yang skip (PO berjalan)
    skip_list = []
    if not semua.empty:
        skip_df = semua[semua["Ada_PO"] == True]
        skip_list = skip_df[prod_col].tolist()[:20]
        if len(semua[semua["Ada_PO"] == True]) > 20:
            skip_list.append(f"...dan {len(semua[semua['Ada_PO']==True])-20} produk lainnya")

    print("\n🤖 Menjalankan Restock Analysis AI...")

    prompt = f"""Kamu adalah purchasing manager berpengalaman di perusahaan retail/distribusi Indonesia.
Buat rekomendasi Purchase Order yang detail dan akurat.

TANGGAL    : {pd.Timestamp.now().strftime('%d %B %Y')}
LEAD TIME  : 8 minggu (2 bulan) untuk semua produk

RINGKASAN:
- Total SKU  : {ringkasan.get('total_produk', 0)}
- Perlu PO   : {ringkasan.get('perlu_po', 0)}
- URGENT     : {ringkasan.get('urgent', 0)}
- NORMAL     : {ringkasan.get('normal', 0)}
- Skip       : {ringkasan.get('skip_po_berjalan', 0)}

PRODUK YANG PERLU DI-PO:
{chr(10).join(produk_lines)}

PRODUK DILEWATI (PO berjalan):
{chr(10).join(['- ' + p for p in skip_list]) if skip_list else 'Tidak ada'}

CATATAN TIER MULTIPLIER:
- Best Seller / Uprising → Safety Stock 12 minggu (lebih cepat habis)
- Slow Moving / Deadweight → 8 minggu
- Sin → 6 minggu (hati-hati over-order)

Balas HANYA dengan JSON valid berikut:
{{
  "analisis_singkat": "2-3 kalimat ringkasan kondisi dan urgensi",
  "catatan_penting": ["poin penting untuk purchasing manager"],
  "rekomendasi_po": [
    {{
      "nama_produk": "...",
      "prioritas": "URGENT atau NORMAL",
      "tier": "tier produk",
      "tren": "tren produk",
      "coverage_sekarang": "X minggu",
      "qty_rekomendasi": 0,
      "alasan": "alasan singkat kenapa qty segini dan urgensinya",
      "estimasi_tiba": "estimasi tiba berdasarkan lead time 8 minggu"
    }}
  ],
  "produk_dilewati": ["nama produk 1"],
  "insight_tier": {{
    "best_seller_kritis": ["produk BS stok kritis"],
    "uprising_order_lebih": ["produk Uprising yang perlu order lebih dari biasanya"],
    "sin_hati_hati": ["produk Sin yang perlu hati-hati jangan over-order"]
  }},
  "total_estimasi_nilai": 0,
  "saran_tindakan": "saran prioritas hari ini"
}}"""

    system = (
        "Kamu adalah AI purchasing agent untuk retail/distribusi Indonesia. "
        "Berikan rekomendasi restock yang detail, akurat, dan actionable. "
        "Gunakan Bahasa Indonesia. Jawab HANYA dalam JSON valid."
    )

    result = _call_claude(prompt, system)
    print("   ✓ Restock analysis selesai")
    return result
