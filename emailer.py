"""
emailer.py — v3
2 template email:
1. weekly_report  — rabu otomatis (performance analysis + stock alert)
2. restock_report — on-demand (rekomendasi restock lengkap)
"""
import os
import smtplib
from datetime import datetime, timedelta
from email import encoders
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from dotenv import load_dotenv

load_dotenv()


def _get_recipients() -> list:
    recipient = os.getenv("EMAIL_RECIPIENT", "")
    return [r.strip() for r in recipient.split(",") if r.strip()]


def _send(msg: MIMEMultipart, recipients: list) -> bool:
    sender   = os.getenv("EMAIL_SENDER")
    password = os.getenv("EMAIL_PASSWORD")

    if not all([sender, password, recipients]):
        print("⚠️  Konfigurasi email tidak lengkap.")
        return False

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(sender, password)
            server.sendmail(sender, recipients, msg.as_string())
        print(f"📧 Email terkirim ke: {', '.join(recipients)}")
        return True
    except smtplib.SMTPAuthenticationError:
        print("❌ Gagal login Gmail. Pastikan pakai App Password.")
        return False
    except Exception as e:
        print(f"❌ Gagal kirim email: {e}")
        return False


def _attach_excel(msg: MIMEMultipart, excel_path: str):
    if excel_path and os.path.exists(excel_path):
        with open(excel_path, "rb") as f:
            att = MIMEBase("application", "octet-stream")
            att.set_payload(f.read())
            encoders.encode_base64(att)
            att.add_header("Content-Disposition", f'attachment; filename="{os.path.basename(excel_path)}"')
            msg.attach(att)


# ── Template 1: Weekly Report (Rabu otomatis) ─────────────────────────────

def send_weekly_report(
    weekly_result: dict,
    metrics: dict,
    excel_path: str = None,
) -> bool:
    sender     = os.getenv("EMAIL_SENDER")
    recipients = _get_recipients()
    ring       = metrics.get("ringkasan", {})
    tier_dist  = metrics.get("tier_summary", {})

    now         = datetime.now()
    week_start  = (now - timedelta(days=now.weekday() + 1)).strftime("%d %b")
    week_end    = now.strftime("%d %b %Y")

    subject = (
        f"[PO Agent] Weekly Report — {week_start} s/d {week_end} | "
        f"{'🚨 ' + str(ring.get('urgent',0)) + ' URGENT' if ring.get('urgent',0) > 0 else '✅ Stok Aman'}"
    )

    # Alert Best Seller
    bs_alert_html = ""
    bs_list = weekly_result.get("alert_best_seller", [])
    if bs_list:
        items = "".join(f"<li><b>{p}</b></li>" for p in bs_list)
        bs_alert_html = f"""
        <div style="background:#FFE2E2;padding:14px 16px;border-radius:8px;
                    margin-bottom:14px;border-left:4px solid #CC0000;">
          <b>🚨 ALERT — Best Seller Stok Kritis:</b>
          <ul style="margin:8px 0 0;padding-left:18px">{items}</ul>
        </div>"""

    # Tier distribution
    tier_colors = {
        "1. Best Seller": "#15803D", "2. Uprising": "#1D4ED8",
        "3. Slow Moving": "#92400E", "4. Deadweight": "#6B7280", "5. Sin": "#9F1239",
    }
    tier_parts = []
    for k, v in tier_dist.items():
        color = tier_colors.get(k, "#333")
        tier_parts.append(f"<span style='color:{color};font-weight:600'>{k}: {v}</span>")
    tier_html = " &nbsp;|&nbsp; ".join(tier_parts)

    def make_list(items):
        if not items:
            return "<li style='color:#888'>Tidak ada</li>"
        return "".join(f"<li>{i}</li>" for i in items)

    html = f"""
<html><body style="font-family:Arial,sans-serif;color:#333;max-width:700px;">
<div style="background:#1E3A5F;padding:18px 20px;border-radius:8px 8px 0 0;">
  <h2 style="color:#fff;margin:0;font-size:18px;">📊 Weekly Purchasing Report</h2>
  <p style="color:#aac;margin:4px 0 0;font-size:13px;">
    {week_start} s/d {week_end} &nbsp;·&nbsp; Dikirim otomatis setiap Rabu
  </p>
</div>
<div style="background:#f8f9fa;padding:18px;border:1px solid #dee2e6;
            border-top:none;border-radius:0 0 8px 8px;">

  <!-- Ringkasan stats -->
  <div style="background:#fff;padding:14px 16px;border-radius:8px;margin-bottom:14px;
              border-left:4px solid #1E3A5F;">
    <b>📦 Ringkasan Stok Minggu Ini</b>
    <table style="width:100%;margin-top:10px;font-size:14px;">
      <tr>
        <td>Total SKU:</td><td><b>{ring.get('total_produk',0)}</b></td>
        <td>Perlu di-PO:</td><td><b>{ring.get('perlu_po',0)} produk</b></td>
      </tr>
      <tr>
        <td style="color:#CC0000">URGENT:</td>
        <td style="color:#CC0000"><b>{ring.get('urgent',0)} produk</b></td>
        <td>NORMAL:</td><td><b>{ring.get('normal',0)} produk</b></td>
      </tr>
      <tr>
        <td>Stok Aman:</td><td><b>{ring.get('stok_cukup',0)}</b></td>
        <td>Skip (PO aktif):</td><td><b>{ring.get('skip_po_berjalan',0)}</b></td>
      </tr>
    </table>
    <div style="margin-top:10px;font-size:12px">{tier_html}</div>
  </div>

  <!-- Analisis AI -->
  <div style="background:#fff;padding:14px 16px;border-radius:8px;margin-bottom:14px;">
    <b>🔍 Analisis Minggu Ini</b>
    <p style="margin:8px 0 0;font-size:14px;font-style:italic;line-height:1.6">
      {weekly_result.get('ringkasan_minggu', '')}
    </p>
  </div>

  <!-- Alert Best Seller -->
  {bs_alert_html}

  <!-- Highlight positif -->
  <div style="background:#DCFCE7;padding:14px 16px;border-radius:8px;
              margin-bottom:14px;border-left:4px solid #15803D;">
    <b>✅ Highlight Positif:</b>
    <ul style="margin:8px 0 0;padding-left:18px;font-size:13px">
      {make_list(weekly_result.get('highlight_positif', []))}
    </ul>
  </div>

  <!-- Highlight negatif -->
  <div style="background:#FEF3C7;padding:14px 16px;border-radius:8px;
              margin-bottom:14px;border-left:4px solid #D97706;">
    <b>⚠️ Perlu Diwaspadai:</b>
    <ul style="margin:8px 0 0;padding-left:18px;font-size:13px">
      {make_list(weekly_result.get('highlight_negatif', []))}
    </ul>
  </div>

  <!-- Uprising watch -->
  {"" if not weekly_result.get('produk_uprising_watch') else f'''
  <div style="background:#DBEAFE;padding:14px 16px;border-radius:8px;
              margin-bottom:14px;border-left:4px solid #1D4ED8;">
    <b>📈 Uprising — Demand Naik:</b>
    <ul style="margin:8px 0 0;padding-left:18px;font-size:13px">
      {"".join(f"<li>{p}</li>" for p in weekly_result.get("produk_uprising_watch",[]))}
    </ul>
  </div>'''}

  <!-- Sin watch -->
  {"" if not weekly_result.get('produk_sin_watch') else f'''
  <div style="background:#FFE4E6;padding:14px 16px;border-radius:8px;
              margin-bottom:14px;border-left:4px solid #9F1239;">
    <b>⚠️ Sin — Perlu Evaluasi:</b>
    <ul style="margin:8px 0 0;padding-left:18px;font-size:13px">
      {"".join(f"<li>{p}</li>" for p in weekly_result.get("produk_sin_watch",[]))}
    </ul>
  </div>'''}

  <!-- Rekomendasi tindakan -->
  <div style="background:#fff;padding:14px 16px;border-radius:8px;margin-bottom:14px;">
    <b>📋 Rekomendasi Tindakan Minggu Ini:</b>
    <ul style="margin:8px 0 0;padding-left:18px;font-size:13px">
      {make_list(weekly_result.get('rekomendasi_tindakan', []))}
    </ul>
  </div>

  <!-- Saran utama -->
  <div style="background:#E8F4E8;padding:12px 16px;border-radius:8px;
              border-left:4px solid #1D9E75;font-size:14px;margin-bottom:14px;">
    <b>💡 Saran Utama:</b> {weekly_result.get('saran_utama', '')}
  </div>

  <p style="color:#888;font-size:12px;">
    📎 Detail lengkap terlampir di file Excel.<br>
    Untuk minta rekomendasi restock, buka dashboard PO Agent dan klik "Restock Recommendations".
  </p>
</div>
</body></html>"""

    msg             = MIMEMultipart("alternative")
    msg["Subject"]  = subject
    msg["From"]     = sender
    msg["To"]       = ", ".join(recipients)
    msg.attach(MIMEText(html, "html"))
    _attach_excel(msg, excel_path)

    return _send(msg, recipients)


# ── Template 2: Restock Report (On-demand) ────────────────────────────────

def send_restock_report(
    restock_result: dict,
    metrics: dict,
    excel_path: str = None,
) -> bool:
    sender     = os.getenv("EMAIL_SENDER")
    recipients = _get_recipients()
    ring       = metrics.get("ringkasan", {})

    subject = (
        f"[PO Agent] Restock Recommendations — "
        f"{datetime.now().strftime('%d %b %Y %H:%M')} | "
        f"{ring.get('perlu_po',0)} produk perlu dipesan"
    )

    reko    = restock_result.get("rekomendasi_po", [])
    urgent  = [r for r in reko if r.get("prioritas") == "URGENT"]
    normal  = [r for r in reko if r.get("prioritas") == "NORMAL"]
    skipped = restock_result.get("produk_dilewati", [])

    tier_colors = {
        "1. Best Seller": "#15803D", "2. Uprising": "#1D4ED8",
        "3. Slow Moving": "#92400E", "4. Deadweight": "#6B7280", "5. Sin": "#9F1239",
    }

    def make_po_list(items):
        if not items:
            return ""
        rows = ""
        for item in items:
            tier   = item.get("tier", "-")
            color  = tier_colors.get(tier, "#333")
            rows += f"""
            <tr style="border-bottom:1px solid #f0f0f0">
              <td style="padding:8px 4px"><b>{item.get('nama_produk','')}</b></td>
              <td style="padding:8px 4px;color:{color};font-weight:600;font-size:12px">{tier}</td>
              <td style="padding:8px 4px">{item.get('tren','-')}</td>
              <td style="padding:8px 4px">{item.get('coverage_sekarang','-')}</td>
              <td style="padding:8px 4px"><b>{item.get('qty_rekomendasi',0):,} pcs</b></td>
              <td style="padding:8px 4px;font-size:12px;color:#666">{item.get('estimasi_tiba','-')}</td>
            </tr>"""
        return f"""
        <table style="width:100%;border-collapse:collapse;font-size:13px">
          <tr style="background:#f8f9fa;font-size:11px;font-weight:600;color:#666;text-transform:uppercase">
            <th style="padding:8px 4px;text-align:left">Produk</th>
            <th style="padding:8px 4px;text-align:left">Tier</th>
            <th style="padding:8px 4px;text-align:left">Tren</th>
            <th style="padding:8px 4px;text-align:left">Coverage</th>
            <th style="padding:8px 4px;text-align:left">Qty Order</th>
            <th style="padding:8px 4px;text-align:left">Est. Tiba</th>
          </tr>
          {rows}
        </table>"""

    insight     = restock_result.get("insight_tier", {})
    insight_html = ""
    insight_map  = {
        "best_seller_kritis":   ("🔴", "#DC2626", "Best Seller Stok Kritis"),
        "uprising_order_lebih": ("🔵", "#2563EB", "Uprising — Order Lebih dari Biasanya"),
        "sin_hati_hati":        ("⚠️", "#D97706", "Sin — Hati-hati Over-order"),
    }
    for key, (icon, color, label) in insight_map.items():
        items = insight.get(key, [])
        if items:
            insight_html += f"""
            <div style="margin-bottom:8px">
              <b style="color:{color}">{icon} {label}:</b>
              <span style="font-size:13px"> {', '.join(items)}</span>
            </div>"""

    html = f"""
<html><body style="font-family:Arial,sans-serif;color:#333;max-width:700px;">
<div style="background:#1E3A5F;padding:18px 20px;border-radius:8px 8px 0 0;">
  <h2 style="color:#fff;margin:0;font-size:18px;">📦 Restock Recommendations</h2>
  <p style="color:#aac;margin:4px 0 0;font-size:13px;">
    {datetime.now().strftime('%A, %d %B %Y — %H:%M')} &nbsp;·&nbsp; On-demand request
  </p>
</div>
<div style="background:#f8f9fa;padding:18px;border:1px solid #dee2e6;
            border-top:none;border-radius:0 0 8px 8px;">

  <div style="background:#fff;padding:14px 16px;border-radius:8px;margin-bottom:14px;
              border-left:4px solid #1E3A5F;">
    <b>📊 Ringkasan</b>
    <table style="width:100%;margin-top:8px;font-size:14px">
      <tr>
        <td>Total SKU:</td><td><b>{ring.get('total_produk',0)}</b></td>
        <td>Perlu di-PO:</td><td><b>{ring.get('perlu_po',0)} produk</b></td>
      </tr>
      <tr>
        <td style="color:#CC0000">URGENT:</td>
        <td style="color:#CC0000"><b>{ring.get('urgent',0)}</b></td>
        <td>NORMAL:</td><td><b>{ring.get('normal',0)}</b></td>
      </tr>
    </table>
  </div>

  <div style="background:#fff;padding:14px 16px;border-radius:8px;margin-bottom:14px;">
    <b>🔍 Analisis AI:</b>
    <p style="margin:8px 0 0;font-size:14px;font-style:italic">
      {restock_result.get('analisis_singkat','')}
    </p>
  </div>

  {"" if not urgent else f'''
  <div style="background:#FFE2E2;padding:14px 16px;border-radius:8px;
              margin-bottom:14px;border-left:4px solid #CC0000;">
    <b>🚨 URGENT ({len(urgent)} produk):</b>
    <div style="margin-top:10px">{make_po_list(urgent)}</div>
  </div>'''}

  {"" if not normal else f'''
  <div style="background:#FFF9E2;padding:14px 16px;border-radius:8px;
              margin-bottom:14px;border-left:4px solid #886600;">
    <b>📋 NORMAL ({len(normal)} produk):</b>
    <div style="margin-top:10px">{make_po_list(normal)}</div>
  </div>'''}

  {"" if not insight_html else f'''
  <div style="background:#fff;padding:14px 16px;border-radius:8px;margin-bottom:14px;">
    <b>🎯 Insight Tier:</b>
    <div style="margin-top:10px">{insight_html}</div>
  </div>'''}

  {"" if not skipped else f'''
  <div style="background:#F3F4F6;padding:14px 16px;border-radius:8px;margin-bottom:14px;">
    <b>⏭ Dilewati — PO Berjalan ({len(skipped)} produk):</b>
    <p style="font-size:12px;color:#666;margin:6px 0 0">
      {", ".join(skipped[:15])}{"..." if len(skipped) > 15 else ""}
    </p>
  </div>'''}

  <div style="background:#E8F4E8;padding:12px 16px;border-radius:8px;
              border-left:4px solid #1D9E75;font-size:14px;margin-bottom:14px;">
    <b>💡 Saran:</b> {restock_result.get('saran_tindakan','')}
  </div>

  <p style="color:#888;font-size:12px;">
    📎 Detail lengkap terlampir di file Excel.<br>
    Lead time semua produk: 8 minggu (2 bulan).
  </p>
</div>
</body></html>"""

    msg             = MIMEMultipart("alternative")
    msg["Subject"]  = subject
    msg["From"]     = sender
    msg["To"]       = ", ".join(recipients)
    msg.attach(MIMEText(html, "html"))
    _attach_excel(msg, excel_path)

    return _send(msg, recipients)
