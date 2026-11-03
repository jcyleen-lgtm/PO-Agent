"""
Stockwise — Inventory & Procurement Intelligence (formerly PO Agent)
FastAPI backend: ETL, inventory health, forecasting, replenishment planning, PO tracking.
Forecast runs automatically after ETL (no manual trigger).
Shared calculations live in replenishment.py (status, ROP, SS, reco, stockout, urgency) and
metrics.py (unit cost, valuation, movement class, turnover, freshness); config.py holds the parameters.
"""

from dotenv import load_dotenv
load_dotenv()

import os, io, uuid, csv, json, math, traceback
from datetime import datetime, date, timedelta

from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Query
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware

import pandas as pd
import numpy as np
from sqlalchemy import create_engine, text
import psycopg2
from psycopg2.extras import execute_values  # one round trip for many rows (Supabase is remote)

from etl import clean_sales_data, load_master_sku, load_product_lookup_from_db
from forecast import run_forecast, save_forecast_to_db, _holdout_summary
from config import settings as get_settings
import replenishment as rp
import metrics as mt
from auth import router as auth_router, require_login

DB_URL = os.getenv("SUPABASE_DB_URL")
UPLOAD_DIR = os.path.join("/tmp", "uploads")
MASTER_SKU_PATH = "MASTER_SKU.xlsx"
os.makedirs(UPLOAD_DIR, exist_ok=True)

app = FastAPI(title="Stockwise", version="4.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
app.middleware("http")(require_login)   # every /api/* route needs a login cookie (see auth.py)
app.include_router(auth_router)         # /api/auth/login, /api/auth/logout, /api/auth/me

master_sku = {}
if os.path.exists(MASTER_SKU_PATH):
    master_sku = load_master_sku(MASTER_SKU_PATH)
    print(f"[startup] Master SKU loaded: {len(master_sku)} entries")

processed_cache = {}
forecast_cache = {}


def get_engine():
    if not DB_URL:
        raise HTTPException(500, "SUPABASE_DB_URL not configured")
    # Force psycopg2 driver (Vercel's SQLAlchemy 2.x defaults to psycopg v3 which isn't installed)
    url = DB_URL
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+psycopg2://", 1)
    elif url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql+psycopg2://", 1)
    return create_engine(url)


def get_raw_conn():
    """psycopg2 connection built from the SAME parsed URL as get_engine().
    Passing the raw URL string to psycopg2 breaks when the password contains / # ? (libpq then reads
    part of the password as the port: 'invalid integer value ... for connection option "port"')."""
    if not DB_URL:
        raise HTTPException(500, "SUPABASE_DB_URL not configured")
    from sqlalchemy.engine import make_url
    u = make_url(DB_URL)
    kw = {k: (v[0] if isinstance(v, tuple) else v) for k, v in u.query.items()}
    if u.host and "host" not in kw:
        kw["host"] = u.host
    if u.port and "port" not in kw:
        kw["port"] = u.port
    if u.username:
        kw["user"] = u.username
    if u.password is not None:
        kw["password"] = u.password
    if u.database:
        kw["dbname"] = u.database
    return psycopg2.connect(**kw)


def _run_auto_forecast(engine):
    """Internal: run forecast + save to DB + cache. Called after ETL and on-demand."""
    try:
        result = run_forecast(engine, forecast_periods=12)
        if "error" not in result:
            saved = save_forecast_to_db(engine, result)
            forecast_cache["latest"] = result
            forecast_cache.pop("light", None); forecast_cache.pop("light_calc_at", None)
            print(f"[auto-forecast] {result['summary']['total_skus_forecasted']} SKUs forecasted, saved={saved}")
        else:
            print(f"[auto-forecast] skipped: {result['error']}")
    except Exception as e:
        print(f"[auto-forecast] error: {e}")
        traceback.print_exc()


def load_forecast_from_db(engine, sku=None):
    """Rebuild the latest forecast from the forecast_results table (written by save_forecast_to_db).
    Reading it back takes one query instead of re-running the Holt's DES grid search.
    Pass sku= to read a single product only."""
    where = "WHERE f.sku = :sku" if sku else ""
    with engine.connect() as conn:
        rows = conn.execute(text(f"""
            SELECT f.sku, COALESCE(p.name, f.sku) AS product_name, f.alpha, f.beta, f.mape,
                   f.forecast_data, f.training_months, f.calculated_at
            FROM forecast_results f
            LEFT JOIN products p ON p.sku = f.sku
            {where}
            ORDER BY f.id
        """), {"sku": sku} if sku else {}).fetchall()
    if not rows:
        return None

    results, calc_times = [], []
    for sku, name, alpha, beta, mape, fd, training, calc_at in rows:
        fd = fd if isinstance(fd, dict) else json.loads(fd or "{}")
        results.append({
            "sku": sku,
            "product_name": name,
            "alpha": float(alpha) if alpha is not None else None,
            "beta": float(beta) if beta is not None else None,
            "mape": float(mape) if mape is not None else 999,
            "trend": fd.get("trend", 0),
            "trend_direction": fd.get("trend_direction", "stable"),
            "level": fd.get("level", 0),
            "avg_weekly_demand": fd.get("avg_weekly_demand", 0),
            "training_weeks": training,
            "actual_data": fd.get("actual", {"weeks": [], "quantities": []}),
            "fitted_data": fd.get("fitted", {"weeks": [], "quantities": []}),
            "forecast_data": fd.get("forecast", {"weeks": [], "quantities": []}),
            "holdout": fd.get("holdout"),
            "method": fd.get("method", "holt"), "method_reason": fd.get("method_reason"),
            "scope": fd.get("scope"),
        })
        if calc_at:
            calc_times.append(calc_at)

    holt = [r for r in results if r.get("method", "holt") == "holt"]
    mapes = [r["mape"] for r in holt if r["mape"] < 999]
    summary = {
        "total_skus_forecasted": len(results),
        "holt_skus": len(holt), "naive_skus": len(results) - len(holt),
        "avg_mape": round(sum(mapes) / len(mapes), 2) if mapes else 0,
        "good_forecast_count": len([m for m in mapes if m < 25]),
        "moderate_forecast_count": len([m for m in mapes if 25 <= m < 50]),
        "poor_forecast_count": len([m for m in mapes if m >= 50]),
        "holdout": _holdout_summary(holt),
        "forecast_periods": len(results[0]["forecast_data"].get("quantities", [])),
        "calculated_at": (lambda c: c.isoformat() if hasattr(c, "isoformat") else str(c))(max(calc_times)) if calc_times else None,
        "source": "database",
    }
    return {"summary": summary, "results": results}


def get_forecast_light(engine):
    """Only the forecast fields the dashboard tables need (no full actual/fitted history).
    ~10x smaller than the full forecast, so it is cheap to load on a fresh Vercel instance.
    Also carries the relative trend and the holdout WMAPE so every page reads the same values."""
    def slim(r, actual8, holdout, ptype, method="holt", method_reason=None, scope=None):
        q = r["forecast_data"].get("quantities", [])
        tp, tc = rp.relative_trend(actual8, q)
        h = holdout or {}
        ha, hf = h.get("holdout_actual") or [], h.get("holdout_forecast") or []
        return {
            # same rounding as the "Forecast/Wk" column (avg of next 8 forecast weeks)
            "fc_avg_8w": round(sum(q[:8]) / min(len(q), 8), 1) if q else 0,
            "sku": r["sku"], "product_name": r.get("product_name", r["sku"]),
            "product_type": ptype or "import",
            "mape": r["mape"], "trend": r.get("trend", 0),
            "trend_direction": r.get("trend_direction", "stable"),   # legacy (beta-based), kept for comparison
            "trend_pct": tp, "trend_class": tc,
            "actual_8w": [round(float(x), 1) for x in (actual8 or [])[-8:]],
            "holdout_wmape": h.get("wmape"), "holdout_reason": h.get("reason") if h else "not_computed",
            "holdout_abs_err": round(sum(abs(a - f) for a, f in zip(ha, hf)), 1) if h.get("wmape") is not None else None,
            "holdout_actual_sum": round(sum(ha), 1) if h.get("wmape") is not None else None,
            "avg_weekly_demand": r.get("avg_weekly_demand", 0),
            "forecast_data": {"weeks": r["forecast_data"].get("weeks", []),
                              "quantities": q},
            "method": method or "holt", "method_reason": method_reason,
            "scope": scope or {"source": "pareto", "pareto": True, "hero": False},
        }
    if "light" in forecast_cache:
        return forecast_cache["light"]
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT f.sku, COALESCE(p.name, f.sku), f.mape,
                   f.forecast_data::jsonb -> 'forecast',
                   f.forecast_data::jsonb ->> 'trend',
                   f.forecast_data::jsonb ->> 'trend_direction',
                   f.forecast_data::jsonb ->> 'avg_weekly_demand',
                   f.forecast_data::jsonb -> 'actual' -> 'quantities',
                   f.forecast_data::jsonb -> 'holdout',
                   p.product_type,
                   f.forecast_data::jsonb ->> 'method',
                   f.forecast_data::jsonb ->> 'method_reason',
                   f.forecast_data::jsonb -> 'scope'
            FROM forecast_results f LEFT JOIN products p ON p.sku = f.sku
            ORDER BY f.id
        """)).fetchall()
    light = []
    for sku, name, mape, fc, trend, tdir, avg, actual, holdout, ptype, method, mreason, scope in rows:
        scope = scope if isinstance(scope, dict) or scope is None else json.loads(scope)
        fc = fc if isinstance(fc, dict) else json.loads(fc or "{}")
        actual = actual if isinstance(actual, list) else json.loads(actual or "[]")
        holdout = holdout if isinstance(holdout, dict) or holdout is None else json.loads(holdout)
        light.append(slim({
            "sku": sku, "product_name": name,
            "mape": float(mape) if mape is not None else 999,
            "trend": float(trend) if trend is not None else 0,
            "trend_direction": tdir or "stable",
            "avg_weekly_demand": float(avg) if avg is not None else 0,
            "forecast_data": fc or {"weeks": [], "quantities": []},
        }, actual[-8:], holdout, ptype, method, mreason, scope))
    if light:
        forecast_cache["light"] = light
    return light


def forecast_summary_fast(engine, product_type="all"):
    """Headline forecast numbers for the Overview, from the light forecast (one cached query)."""
    light = get_forecast_light(engine)
    if product_type in ("import", "food"):
        light = [r for r in light if r["product_type"] == product_type]
    if not light:
        return None
    holt = [r for r in light if r.get("method", "holt") == "holt"]
    mapes = [r["mape"] for r in holt if r["mape"] < 999]
    ev = [r for r in holt if r["holdout_wmape"] is not None]
    act = sum(r["holdout_actual_sum"] for r in ev)
    calc = forecast_cache.get("light_calc_at")
    if calc is None:
        try:
            with engine.connect() as conn:
                calc = conn.execute(text("SELECT MAX(calculated_at) FROM forecast_results")).scalar()
            calc = calc.isoformat() if calc else None
            forecast_cache["light_calc_at"] = calc
        except Exception:
            calc = None
    return {
        "total_forecasted": len(light),
        "avg_mape": round(sum(mapes) / len(mapes), 2) if mapes else 0,
        "mape_basis": "in_sample",
        "holdout_wmape": round(sum(r["holdout_abs_err"] for r in ev) / act * 100, 2) if act else None,
        "holdout_evaluated": len(ev), "holdout_not_evaluated": len(holt) - len(ev),
        "holt_skus": len(holt), "naive_skus": len(light) - len(holt),
        "hero_skus": sum(1 for r in light if (r.get("scope") or {}).get("hero")),
        "forecast_demand_8w": round(sum(sum(r["forecast_data"]["quantities"][:8]) for r in light)),
        "trending_up": len([r for r in light if r["trend_class"] == "increasing"]),
        "trending_down": len([r for r in light if r["trend_class"] == "decreasing"]),
        "trend_basis": "relative_8w_10pct",
        "calculated_at": calc,
    }


def get_latest_forecast(engine, compute_if_missing=True):
    """Forecast lookup order: memory cache → forecast_results table → full recompute (slow).
    On Vercel every new instance starts with an empty cache, so the table read avoids
    re-running the grid search on almost every page load."""
    if "latest" in forecast_cache:
        return forecast_cache["latest"]
    try:
        data = load_forecast_from_db(engine)
    except Exception as e:
        print(f"[forecast] could not read saved results: {e}")
        data = None
    if data:
        forecast_cache["latest"] = data
        return data
    if not compute_if_missing:
        return None
    data = run_forecast(engine, forecast_periods=12)
    if "error" not in data:
        try:
            save_forecast_to_db(engine, data)
        except Exception as e:
            print(f"[forecast] could not save results: {e}")
        forecast_cache["latest"] = data
    return data


# ── Hero SKU (company best-seller list) ───────────────────────

def ensure_hero_table(conn):
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS hero_skus (
            sku VARCHAR(50) PRIMARY KEY,
            group_name VARCHAR(160),
            status VARCHAR(60),
            source_code VARCHAR(160),
            product_name TEXT,
            uploaded_at TIMESTAMPTZ DEFAULT NOW()
        )"""))


def _hero_columns(raw):
    """Find the header row (within the first 10 rows) and the SKU / name / group / status columns."""
    for i in range(min(10, len(raw))):
        head = [" ".join(str(x).lower().split()) if pd.notna(x) else "" for x in raw.iloc[i].tolist()]
        sku_i = next((j for j, h in enumerate(head) if h in ("sku", "master sku", "kode", "item no", "item no.")
                      or h.endswith(" sku")), None)
        if sku_i is None:
            continue
        pick = lambda *keys: next((j for j, h in enumerate(head) if any(k == h or h.startswith(k) for k in keys)), None)
        return i, sku_i, pick("produk", "product", "nama", "name", "description"), pick("group", "grup"), pick("status")
    return None


@app.post("/api/hero-sku/upload")
async def upload_hero_sku(file: UploadFile = File(...)):
    """Company best-seller list (sheet HERO SKU / BEST SELLER SKU): SKU + optional PRODUK, GROUP, STATUS.
    Every SKU on it is analysed by Stockwise, on top of the Pareto 80% set. Replaces the stored list.
    Codes are matched to Accurate SKUs: exact, else ignoring dots (299028001 = 299.028.001);
    a bundle 'A+B' becomes its component SKUs."""
    if not file.filename.lower().endswith((".xls", ".xlsx")):
        raise HTTPException(400, "File harus .xls atau .xlsx")
    content = await file.read()
    ext = os.path.splitext(file.filename)[1].lower()
    try:
        raw = pd.read_excel(io.BytesIO(content), header=None, dtype=str, engine="xlrd" if ext == ".xls" else "openpyxl")
    except Exception as e:
        raise HTTPException(400, f"Gagal membaca file: {e}")
    cols = _hero_columns(raw)
    if not cols:
        raise HTTPException(400, "Kolom SKU tidak ditemukan. Baris judul harus berisi 'SKU' (atau 'MASTER SKU'); "
                                 "kolom lain opsional: PRODUK, GROUP, STATUS.")
    hdr, c_sku, c_name, c_group, c_status = cols
    val = lambda r, c: (str(r[c]).strip() if c is not None and c < len(r) and pd.notna(r[c]) and str(r[c]).strip() else None)

    engine = get_engine()
    with engine.begin() as conn:
        ensure_hero_table(conn)
        prods = {r[0]: r[1] for r in conn.execute(text("SELECT sku, name FROM products WHERE sku <> '0'")).fetchall()}
        snap = conn.execute(text("SELECT MAX(snapshot_date) FROM stock_snapshots")).scalar()
        in_stock = {r[0] for r in conn.execute(text("SELECT sku FROM stock_snapshots WHERE snapshot_date = :d"),
                                               {"d": snap}).fetchall()} if snap else set()
        nodot = {}
        for sku in prods:
            nodot.setdefault(sku.replace(".", ""), []).append(sku)

        def resolve(code):
            if code in prods:
                return code, False
            cands = nodot.get(code.replace(".", ""), [])
            if not cands:
                return None, False
            # same digits under several codes: prefer the one in the latest stock, then the dotted Accurate code
            cands.sort(key=lambda s_: (s_ in in_stock, "." in s_), reverse=True)
            return cands[0], True

        saved, unmatched, normalized, bundles, dupes, new_products = {}, [], [], 0, 0, []
        for r in raw.iloc[hdr + 1:].itertuples(index=False):
            code = val(r, c_sku)
            if not code or not code[0].isdigit():
                continue
            parts = [p_.strip() for p_ in code.split("+") if p_.strip()]
            bundles += len(parts) > 1
            for part in parts:
                sku, fixed = resolve(part)
                if sku is None:
                    name = val(r, c_name) if len(parts) == 1 else None
                    if name and "." in part:          # a valid-looking new code with a name: create the product
                        sku = part
                        new_products.append({"s": sku, "n": name})
                        prods[sku] = name
                    else:
                        unmatched.append(part)
                        continue
                if fixed:
                    normalized.append(f"{part} → {sku}")
                if sku in saved:
                    dupes += 1
                    continue
                saved[sku] = {"sku": sku, "g": val(r, c_group), "st": val(r, c_status), "src": code,
                              "n": val(r, c_name) or prods.get(sku)}
        if not saved:
            raise HTTPException(400, "Tidak ada SKU yang bisa dicocokkan dengan data produk.")
        if new_products:
            conn.execute(text("INSERT INTO products (sku, name) VALUES (:s, :n) ON CONFLICT (sku) DO NOTHING"), new_products)
        conn.execute(text("DELETE FROM hero_skus"))
        conn.execute(text("""INSERT INTO hero_skus (sku, group_name, status, source_code, product_name)
                             VALUES (:sku, :g, :st, :src, :n)"""), list(saved.values()))
    forecast_cache.clear()
    return {
        "status": "success", "skus_saved": len(saved), "bundles_split": bundles,
        "normalized": normalized[:30], "normalized_count": len(normalized),
        "duplicates_skipped": dupes, "unmatched": unmatched[:30], "unmatched_count": len(unmatched),
        "new_products": len(new_products),
        "groups": len({v["g"] for v in saved.values() if v["g"]}),
        "message": f"{len(saved)} SKU Hero disimpan. Jalankan Refresh forecast supaya semuanya masuk analisis.",
    }


@app.get("/api/bestseller/scope")
async def bestseller_scope():
    """How the best-seller set is formed: Pareto 80% of import revenue (same rule as the forecast run)
    plus the company Hero SKU list. Returns the Pareto curve, the counts and one row per SKU in scope."""
    engine = get_engine()
    with engine.begin() as conn:
        ensure_hero_table(conn)
        sales = conn.execute(text("""
            SELECT s.sku, MIN(p.name), SUM(s.amount), SUM(s.quantity), MIN(s.invoice_date), MAX(s.invoice_date)
            FROM sales s JOIN products p ON p.sku = s.sku
            WHERE s.invoice_date IS NOT NULL AND COALESCE(p.product_type, 'import') = 'import'
            GROUP BY s.sku ORDER BY SUM(s.amount) DESC, s.sku
        """)).fetchall()
        hero = {r[0]: {"group": r[1], "status": r[2], "name": r[3], "src": r[4]} for r in conn.execute(text(
            "SELECT h.sku, h.group_name, h.status, COALESCE(p.name, h.product_name), h.source_code "
            "FROM hero_skus h LEFT JOIN products p ON p.sku = h.sku")).fetchall()}
    try:
        fc = {r["sku"]: r for r in get_forecast_light(engine)}
    except Exception:
        fc = {}
    total = float(sum(float(r[2] or 0) for r in sales)) or 0.0
    rows, cum, prev, n_pareto, curve = {}, 0.0, 0.0, 0, []
    step = max(1, len(sales) // 300)
    for i, (sku, name, rev, qty, first, last) in enumerate(sales, start=1):
        rev = float(rev or 0)
        prev, cum = cum, cum + rev
        in_p = total > 0 and prev < 0.8 * total
        n_pareto += in_p
        if i % step == 0 or i == len(sales) or in_p and (cum >= 0.8 * total):
            curve.append({"rank": i, "cum_pct": round(cum / total * 100, 2) if total else 0})
        if in_p or sku in hero:
            rows[sku] = {"sku": sku, "product_name": name, "rank": i, "revenue": round(rev), "qty": int(qty or 0),
                         "share_pct": round(rev / total * 100, 3) if total else 0,
                         "cum_pct": round(cum / total * 100, 2) if total else 0,
                         "first_sale": str(first) if first else None, "last_sale": str(last) if last else None,
                         "pareto": bool(in_p)}
    for sku, h in hero.items():
        rows.setdefault(sku, {"sku": sku, "product_name": h["name"], "rank": None, "revenue": 0, "qty": 0,
                              "share_pct": 0, "cum_pct": None, "first_sale": None, "last_sale": None, "pareto": False})
    last = max((r[5] for r in sales if r[5]), default=None)
    out = []
    for sku, r in rows.items():
        h, f = hero.get(sku), fc.get(sku)
        r.update({"hero": h is not None, "group": h["group"] if h else None, "status": h["status"] if h else None,
                  "source_code": h["src"] if h else None,
                  "source": "both" if (r["pareto"] and h) else ("hero" if h else "pareto"),
                  "in_system": f is not None,
                  "method": f.get("method", "holt") if f else None,
                  "method_reason": f.get("method_reason") if f else None})
        if not f and r["pareto"] and not h:
            # Pareto SKU left out of the forecast run: no sale in the last 8 weeks, or too little history
            stale = last and r["last_sale"] and (last - date.fromisoformat(r["last_sale"])).days > 56
            r["method_reason"] = "inactive" if stale else "too_few_weeks"
        out.append(r)
    out.sort(key=lambda r: (r["rank"] is None, r["rank"] or 0, r["sku"]))
    cut = min((r["revenue"] for r in out if r["pareto"]), default=None)
    first = min((r[4] for r in sales if r[4]), default=None)
    last = max((r[5] for r in sales if r[5]), default=None)
    pareto_rev = sum(r["revenue"] for r in out if r["pareto"])
    hero_rev = sum(r["revenue"] for r in out if r["hero"])
    groups = {}
    for r in out:
        if r["hero"]:
            g = groups.setdefault(r["group"] or "(tanpa group)", {"group": r["group"] or "(tanpa group)", "status": r["status"],
                                                                   "skus": 0, "in_pareto": 0, "revenue": 0})
            g["skus"] += 1
            g["in_pareto"] += r["pareto"]
            g["revenue"] += r["revenue"]
    return {
        "period": {"start": str(first) if first else None, "end": str(last) if last else None},
        "import_revenue": round(total), "skus_sold": len(sales),
        "pareto": {"skus": n_pareto, "share_of_skus_pct": round(n_pareto / len(sales) * 100, 1) if sales else 0,
                   "revenue_pct": round(pareto_rev / total * 100, 1) if total else 0, "cut_revenue": cut},
        "hero": {"skus": len(hero), "revenue_pct": round(hero_rev / total * 100, 1) if total else 0,
                 "groups": len(groups)},
        "counts": {"both": sum(r["source"] == "both" for r in out), "pareto_only": sum(r["source"] == "pareto" for r in out),
                   "hero_only": sum(r["source"] == "hero" for r in out), "in_scope": len(out),
                   "in_system": sum(r["in_system"] for r in out),
                   "naive": sum(r["method"] == "naive" for r in out)},
        # a Hero SKU that is not in the forecast yet = the list was uploaded after the last Refresh forecast
        "forecast_stale": any(r["hero"] and not r["in_system"] for r in out),
        "curve": curve, "groups": sorted(groups.values(), key=lambda g: -g["revenue"]), "rows": out,
    }


# ── Page Routes ───────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def dashboard_page():
    with open("templates/dashboard.html", "r", encoding="utf-8") as f:
        return f.read()

## ETL is now a tab inside dashboard.html — no separate page needed


# ── ETL API ───────────────────────────────────────────────────

def read_sku_name_sheet(content: bytes, filename: str):
    """Read a 2-column SKU list (col A = SKU, col B = name) from .xls/.xlsx, with or without a header row.
    A header row is recognised by 'sku' in column A; a stock export's extra Quantity column is ignored."""
    ext = os.path.splitext(filename)[1].lower()
    raw = pd.read_excel(io.BytesIO(content), header=None, dtype=str, engine="xlrd" if ext == ".xls" else "openpyxl")
    if raw.empty or raw.shape[1] < 2:
        raise HTTPException(400, "File harus punya minimal 2 kolom: kolom A = SKU, kolom B = nama produk.")
    head = [str(x).strip().lower() for x in raw.iloc[0].tolist()]
    sku_i, name_i, start = 0, 1, 0
    if any("sku" in h or "item no" in h or "kode" in h for h in head):
        sku_i = next(i for i, h in enumerate(head) if "sku" in h or "item no" in h or "kode" in h)
        name_i = next((i for i, h in enumerate(head) if any(k in h for k in ("desc", "name", "nama", "product"))),
                      1 if sku_i == 0 else 0)
        start = 1
    rows = {}
    for sku, name in zip(raw.iloc[start:, sku_i], raw.iloc[start:, name_i]):
        sku = "" if pd.isna(sku) else str(sku).strip()
        if not sku or sku.lower() in ("nan", "sku") or not sku[0].isalnum():
            continue
        name = "" if pd.isna(name) else str(name).strip()
        rows[sku] = name or sku
    return rows


@app.post("/api/master-sku/upload")
async def upload_master_sku(file: UploadFile = File(...)):
    """Master SKU list -> products table (the sales ETL resolves names to SKUs from products).
    Kept in the database, not on disk: Vercel's file system is read-only and every instance needs the list."""
    global master_sku
    if not file.filename.lower().endswith((".xls", ".xlsx")):
        raise HTTPException(400, "Format file harus .xls atau .xlsx")
    content = await file.read()
    try:
        products_by_sku = read_sku_name_sheet(content, file.filename)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(400, f"Gagal membaca Master SKU: {e}")
    if not products_by_sku:
        raise HTTPException(400, "Tidak ada SKU yang terbaca. Format: kolom A = SKU, kolom B = nama produk.")
    try:
        # this instance's in-memory mapping (description -> SKU), used next to the products table
        master_sku = {**master_sku, **{n.lower(): k for k, n in products_by_sku.items() if n}}

        conn = get_raw_conn()
        cur = conn.cursor()
        try:
            execute_values(cur, """
                INSERT INTO products (sku, name) VALUES %s
                ON CONFLICT (sku) DO UPDATE SET name = EXCLUDED.name
            """, list(products_by_sku.items()), page_size=1000)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            cur.close()
            conn.close()

        return {
            "status": "success",
            "entries": len(products_by_sku),
            "products_synced": len(products_by_sku),
            "message": f"Master SKU berhasil di-upload dan {len(products_by_sku)} SKU disinkronkan ke database.",
        }
    except Exception as e:
        raise HTTPException(500, f"Gagal memproses Master SKU: {e}")


# ── Stock status (one definition for the whole dashboard) ──────
# Coverage = (stock + open PO) / average weekly sales of the last 4 weeks.
def _type_filter(product_type):
    return f"AND COALESCE(p.product_type, 'import') = '{product_type}'" if product_type in ("import", "food") else ""


def coverage_status(cov, stock=0):
    """Kept for old callers; the rule itself lives in replenishment.stock_status (3 statuses only)."""
    return rp.stock_status(cov, stock, get_settings())


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


def build_inventory(engine, product_type="import", scope="all", today=None):
    """One pass that every inventory/replenishment page reads from, so a SKU shows the same
    coverage, status, cost, reco and urgency everywhere."""
    cfg = get_settings()
    today = today or date.today()
    tf = _type_filter(product_type)
    n_hist = max(12, cfg["movement_window_weeks"])
    with engine.connect() as conn:
        weeks = sorted(r[0] for r in conn.execute(text(f"""
            SELECT DISTINCT DATE_TRUNC('week', s.invoice_date)::date AS w
            FROM sales s JOIN products p ON s.sku = p.sku
            WHERE s.invoice_date IS NOT NULL {tf}
            ORDER BY w DESC LIMIT :n
        """), {"n": n_hist}).fetchall())
        last_sale = conn.execute(text("SELECT MAX(invoice_date) FROM sales")).scalar()
        snap_dates = [r[0] for r in conn.execute(text(
            "SELECT DISTINCT snapshot_date FROM stock_snapshots ORDER BY snapshot_date DESC")).fetchall()]
        snap_date = snap_dates[0] if snap_dates else None
        stk = {r[0]: int(r[1]) for r in conn.execute(text(
            "SELECT sku, SUM(quantity) FROM stock_snapshots WHERE snapshot_date = :d GROUP BY sku"),
            {"d": snap_date}).fetchall()} if snap_date else {}
        po_lines = _fetch_po_lines(conn)
        if not weeks:
            return {"rows": [], "summary": {}, "freshness": mt.freshness(snap_date, last_sale, cfg, today),
                    "stock_date": str(snap_date) if snap_date else None, "has_stock": bool(stk),
                    "snapshot_count": len(snap_dates), "settings": cfg}
        r4 = weeks[-4:]
        rmov = weeks[-cfg["movement_window_weeks"]:]
        sku_rows = conn.execute(text(f"""
            SELECT s.sku, MIN(p.name)
            FROM sales s JOIN products p ON s.sku = p.sku
            WHERE s.invoice_date IS NOT NULL {tf}
            GROUP BY s.sku ORDER BY s.sku
        """)).fetchall()
        recent = conn.execute(text(f"""
            SELECT s.sku, DATE_TRUNC('week', s.invoice_date)::date AS w, SUM(s.quantity), SUM(s.amount)
            FROM sales s JOIN products p ON s.sku = p.sku
            WHERE s.invoice_date >= :w0 {tf}
            GROUP BY s.sku, w
        """), {"w0": weeks[0]}).fetchall()
        costs = mt.unit_costs(conn, cfg)

    qty_by, rev_by = {}, {}
    for sku, w, q, a in recent:
        qty_by.setdefault(sku, {})[w] = int(q or 0)
        rev_by.setdefault(sku, {})[w] = float(a or 0)

    avg_mov = {sku: sum(qty_by.get(sku, {}).get(w, 0) for w in rmov) / max(len(rmov), 1) for sku, _ in sku_rows}
    movement = mt.movement_classes(avg_mov, cfg)

    try:
        fc_map = {r["sku"]: r for r in get_forecast_light(engine)}
    except Exception:
        fc_map = {}
    if scope == "bestseller" and fc_map:
        # every SKU in the best-seller scope, including company-list SKUs that have no sales yet
        sku_rows = sorted((s, r.get("product_name") or s) for s, r in fc_map.items()
                          if product_type == "all" or r.get("product_type", "import") == product_type)

    rows = []
    for sku, name in sku_rows:
        wmap, rmap = qty_by.get(sku, {}), rev_by.get(sku, {})
        l4w_qty = sum(wmap.get(w, 0) for w in r4)
        l4w_rev = sum(rmap.get(w, 0) for w in r4)
        avg_l4w = round(l4w_qty / max(len(r4), 1), 1)
        cs = stk.get(sku, 0)
        lines = po_lines.get(sku, [])
        op = sum(l["outstanding"] for l in lines)
        available = cs + op
        cov = rp.coverage_weeks(cs, op, avg_l4w)
        status = rp.stock_status(cov, cs, cfg)
        c = costs.get(sku)
        ovq = rp.overstock_qty(available, avg_l4w, cfg) if status == "overstocked" else 0
        row = {
            "sku": sku, "product_name": name,
            "l4w_qty": l4w_qty, "l4w_revenue": round(l4w_rev), "avg_l4w": avg_l4w,
            "current_stock": cs, "out_of_stock": cs <= 0,
            "ongoing_po": op, "available": available,
            "coverage": cov, "days_of_inventory": round(cov * 7) if cov is not None else None,
            "status": status,
            "movement": movement.get(sku, "non_moving"), "avg_weekly_12w": round(avg_mov.get(sku, 0), 1),
            "unit_cost": c["cost"] if c else None,
            "cost_source": c["source"] if c else None,
            "cost_verified": c["verified"] if c else False,
            "inventory_value": round(max(cs, 0) * c["cost"]) if c else None,
            "overstock_qty": ovq,
            "overstock_value": round(ovq * c["cost"]) if (c and ovq) else (0 if not ovq else None),
            "forecasted": sku in fc_map,
            # forecast-based fields (None when the SKU is not a forecasted best seller)
            "fc_avg_weekly": None, "mape": None, "holdout_wmape": None,
            "trend_pct": None, "trend_class": None, "trend_direction": None,
            "lt_demand_8w": None, "safety_stock": None, "rop": None,
            "inventory_position": None, "confirmed_incoming": None, "unconfirmed_incoming": None,
            "reco_po": None, "reco_order_up_to": None, "reco_if_unconfirmed_arrives": None,
            "stockout_week": None, "stockout_week_if_unconfirmed_arrives": None,
            "urgency": None, "slack_weeks": None, "needs_po_check": False, "est_purchase_cost": None,
            "order_by": None, "order_by_days": None, "order_by_is_estimate": None,
            "scope_source": None, "hero_group": None, "hero_status": None, "pareto_rank": None,
            "fc_method": None, "fc_method_reason": None,
        }
        fc = fc_map.get(sku)
        if fc:
            sc = fc.get("scope") or {}
            row.update({"scope_source": sc.get("source"), "hero_group": sc.get("group"),
                        "hero_status": sc.get("status"), "pareto_rank": sc.get("rank"),
                        "fc_method": fc.get("method", "holt"), "fc_method_reason": fc.get("method_reason")})
        if fc:
            fq = fc["forecast_data"]["quantities"]
            plan = rp.plan_sku(cs, lines, fq, fc["forecast_data"].get("weeks", []), cfg, today=today)
            row.update({
                "fc_avg_weekly": fc["fc_avg_8w"], "mape": fc["mape"], "holdout_wmape": fc["holdout_wmape"],
                "trend_pct": fc["trend_pct"], "trend_class": fc["trend_class"],
                "trend_direction": fc["trend_direction"],
                "lt_demand_8w": plan["lt_demand"], "safety_stock": plan["safety_stock"], "rop": plan["rop"],
                "inventory_position": plan["inventory_position"],
                "confirmed_incoming": plan["confirmed_incoming"],
                "unconfirmed_incoming": plan["unconfirmed_incoming"],
                "reco_po": plan["reco_qty"], "reco_order_up_to": plan["reco_order_up_to"],
                "reco_if_unconfirmed_arrives": plan["reco_if_unconfirmed_arrives"],
                "stockout_week": plan["stockout_week"],
                "stockout_week_if_unconfirmed_arrives": plan["stockout_week_if_unconfirmed_arrives"],
                "urgency": plan["urgency"], "slack_weeks": plan["slack_weeks"],
                "order_by": plan["order_by"], "order_by_days": plan["order_by_days"],
                "order_by_is_estimate": plan["order_by_is_estimate"],
                "needs_po_check": plan["needs_po_check"],
                "est_purchase_cost": round(plan["reco_qty"] * c["cost"]) if (c and plan["reco_qty"]) else (0 if not plan["reco_qty"] else None),
                "flags": plan["flags"],
            })
        rows.append(row)

    with_cost = [r for r in rows if r["unit_cost"] is not None]
    summary = {
        "total": len(rows),
        "understocked": sum(r["status"] == "understocked" for r in rows),
        "healthy": sum(r["status"] == "healthy" for r in rows),
        "overstocked": sum(r["status"] == "overstocked" for r in rows),
        "unclassified": sum(r["status"] is None for r in rows),   # no sales and no stock: not a status
        "out_of_stock": sum(r["out_of_stock"] for r in rows),
        "stock_on_hand": sum(max(r["current_stock"], 0) for r in rows),
        "negative_stock_skus": sum(r["current_stock"] < 0 for r in rows),
        "inventory_value": sum(r["inventory_value"] for r in with_cost),
        "valued_skus": len(with_cost), "no_cost_skus": len(rows) - len(with_cost),
        "cost_verified": bool(with_cost) and all(r["cost_verified"] for r in with_cost),
        "overstock_qty": sum(r["overstock_qty"] for r in rows),
        "overstock_value": sum(r["overstock_value"] or 0 for r in rows),
        "forecasted": sum(r["forecasted"] for r in rows),
        "reorder_skus": sum(1 for r in rows if (r["reco_po"] or 0) > 0),
        "reorder_qty": sum(r["reco_po"] or 0 for r in rows),
        "est_purchase_cost": sum(r["est_purchase_cost"] or 0 for r in rows),
        "urgency": {u: sum(r["urgency"] == u for r in rows) for u in ("critical", "high", "medium", "low")},
        "needs_po_check": sum(r["needs_po_check"] for r in rows),
        "movement": {m: sum(r["movement"] == m for r in rows) for m in ("fast", "medium", "slow", "non_moving")},
        # legacy key (old dashboards): there is no 4th status any more
        "no_sales": 0,
    }
    return {
        "rows": rows, "summary": summary,
        "freshness": mt.freshness(snap_date, last_sale, cfg, today),
        "stock_date": str(snap_date) if snap_date else None, "has_stock": bool(stk),
        "snapshot_count": len(snap_dates), "sales_weeks": [str(w) for w in r4],
        "settings": {k: cfg[k] for k in ("lead_time_weeks", "review_period_days", "ss_pct", "ss_basis_weeks",
                                         "understock_weeks", "overstock_weeks", "cost_verified",
                                         "stale_warn_days", "stale_block_days")},
    }


# ── Food / import classification ──────────────────────────────
# The food list is stored in food_skus. Products matching it get product_type='food',
# everything else 'import'. The dashboard filters on product_type (default: import).
# New sales uploads are tagged with the same rules.

SALES_COLS = ("sku, invoice_date, quantity, unit, amount, cogs_accurate, gpao_accurate, "
              "gpao_pct_acc, week_label, month_label, quarter_label, year_val")


def _norm_name(name):
    return " ".join(str(name or "").lower().split())


def ensure_food_tables(conn):
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS food_skus (
            code     VARCHAR(80) PRIMARY KEY,
            name     TEXT,
            added_at TIMESTAMPTZ DEFAULT NOW()
        )"""))


def ensure_sales_excluded(conn):
    conn.execute(text(f"""
        CREATE TABLE IF NOT EXISTS sales_excluded (
            id BIGSERIAL PRIMARY KEY, sku VARCHAR(255), invoice_date DATE, quantity INTEGER, unit VARCHAR(20),
            amount NUMERIC, cogs_accurate NUMERIC, gpao_accurate NUMERIC, gpao_pct_acc NUMERIC,
            week_label VARCHAR(30), month_label VARCHAR(20), quarter_label VARCHAR(30), year_val SMALLINT,
            created_at TIMESTAMPTZ DEFAULT NOW(), excluded_at TIMESTAMPTZ DEFAULT NOW(), reason VARCHAR(40))"""))


def move_food_sales(conn, food_skus):
    """Food sales leave the system (archived with reason 'food'); sales of products that are no longer
    on the food list come back. Returns (archived, restored) row counts."""
    ensure_sales_excluded(conn)
    restored = conn.execute(text(f"""
        INSERT INTO sales ({SALES_COLS})
        SELECT {SALES_COLS} FROM sales_excluded x
        WHERE x.reason = 'food' AND NOT (x.sku = ANY(:k)) AND NOT EXISTS (
            SELECT 1 FROM sales e WHERE e.sku = x.sku AND e.invoice_date = x.invoice_date
              AND e.quantity = x.quantity AND e.amount = x.amount)
    """), {"k": list(food_skus)}).rowcount
    conn.execute(text("DELETE FROM sales_excluded WHERE reason = 'food' AND NOT (sku = ANY(:k))"), {"k": list(food_skus)})
    archived = 0
    if food_skus:
        archived = conn.execute(text(f"""
            WITH m AS (DELETE FROM sales WHERE sku = ANY(:k) RETURNING {SALES_COLS})
            INSERT INTO sales_excluded ({SALES_COLS}, reason) SELECT {SALES_COLS}, 'food' FROM m
        """), {"k": list(food_skus)}).rowcount
    return archived, restored


def load_food_rules(conn):
    """Return (codes, normalized_names). Empty sets if no food list was uploaded yet."""
    exists = conn.execute(text("SELECT to_regclass('public.food_skus')")).scalar()
    if not exists:
        return set(), set()
    rows = conn.execute(text("SELECT code, name FROM food_skus")).fetchall()
    return {r[0] for r in rows}, {_norm_name(r[1]) for r in rows if r[1]}


def is_food(sku, name, codes, names):
    """Food if the SKU equals a listed code or sits under one (299.025 covers 299.025.xx...),
    or if the product name matches a listed name exactly (catches AUTO-xxxx SKUs)."""
    sku = str(sku or "").strip()
    parts = sku.split(".")
    if any(".".join(parts[:i]) in codes for i in range(1, len(parts) + 1)):
        return True
    return _norm_name(name) in names


@app.post("/api/food-sku/upload")
async def upload_food_sku(file: UploadFile = File(...)):
    """Upload the food list (Excel: col A = SKU/code, col B = product name).
    Replaces the stored list, re-tags every product as food or import, and takes food sales OUT of the
    system: they are moved to sales_excluded (reason 'food'), so no page, forecast or KPI uses them.
    A product removed from the list gets its archived sales back."""
    if not file.filename.lower().endswith((".xls", ".xlsx")):
        raise HTTPException(400, "File harus .xls atau .xlsx")
    content = await file.read()
    try:
        df = pd.read_excel(io.BytesIO(content), header=None, dtype=str)
    except Exception as e:
        raise HTTPException(400, f"Gagal membaca file: {e}")

    entries = {}
    for row in df.itertuples(index=False):
        code = str(row[0]).strip() if len(row) > 0 and pd.notna(row[0]) else ""
        name = str(row[1]).strip() if len(row) > 1 and pd.notna(row[1]) else ""
        if not code or not code[0].isdigit():  # skips the header row ("Item No.") and blanks
            continue
        entries[code] = name
    if not entries:
        raise HTTPException(400, "Tidak ada SKU di kolom A. Format: kolom A = SKU, kolom B = nama produk.")

    engine = get_engine()
    with engine.begin() as conn:
        ensure_food_tables(conn)
        conn.execute(text("DELETE FROM food_skus"))
        conn.execute(text("INSERT INTO food_skus (code, name) VALUES (:c, :n)"),
                     [{"c": c, "n": n} for c, n in entries.items()])
        codes, names = set(entries), {_norm_name(n) for n in entries.values() if n}

        products = conn.execute(text("SELECT sku, name FROM products")).fetchall()
        food = [sku for sku, name in products if is_food(sku, name, codes, names)]
        food_set = set(food)
        auto_matched = sorted({name for sku, name in products
                               if sku in food_set and str(sku).startswith("AUTO-")})

        conn.execute(text("UPDATE products SET product_type = 'import' WHERE product_type IS DISTINCT FROM 'import'"))
        if food:
            conn.execute(text("UPDATE products SET product_type = 'food' WHERE sku = ANY(:k)"), {"k": food})

        archived, restored = move_food_sales(conn, food)

    forecast_cache.clear()
    return {
        "status": "success",
        "food_skus_in_file": len(entries),
        "products_marked_food": len(food),
        "products_marked_import": len(products) - len(food),
        "sales_rows_restored": restored,
        "sales_rows_archived": archived,
        "auto_sku_matched_by_name": auto_matched[:20],
        "auto_sku_matched_count": len(auto_matched),
    }


@app.post("/api/etl/upload")
async def etl_upload(file: UploadFile = File(...)):
    if not file.filename.endswith((".xls", ".xlsx")):
        raise HTTPException(400, "Format file harus .xls atau .xlsx")
    file_id = str(uuid.uuid4())[:8]
    ext = os.path.splitext(file.filename)[1]
    save_path = os.path.join(UPLOAD_DIR, f"{file_id}{ext}")
    content = await file.read()
    with open(save_path, "wb") as f:
        f.write(content)
    # Load DB product lookup for SKU resolution (primary source)
    try:
        db_lookup = load_product_lookup_from_db(get_engine())
    except Exception:
        db_lookup = {}
    result = clean_sales_data(save_path, master_sku=master_sku, db_product_lookup=db_lookup)
    if result["errors"]:
        os.remove(save_path)
        raise HTTPException(400, detail={"errors": result["errors"]})
    df = result["df"]
    processed_cache[file_id] = {
        "df": df, "filepath": save_path,
        "filename": file.filename, "created_at": datetime.now().isoformat(),
    }
    preview_rows = df.head(20).copy()
    for col in preview_rows.columns:
        if preview_rows[col].dtype == "datetime64[ns]":
            preview_rows[col] = preview_rows[col].dt.strftime("%Y-%m-%d")
    preview_rows = preview_rows.fillna("").to_dict("records")
    return {
        "file_id": file_id, "filename": file.filename,
        "stats": result["stats"], "preview": preview_rows,
        "columns": list(df.columns),
    }


@app.post("/api/etl/confirm/{file_id}")
async def etl_confirm(file_id: str):
    if file_id not in processed_cache:
        raise HTTPException(404, "Data tidak ditemukan. Upload ulang.")
    cached = processed_cache[file_id]
    df = cached["df"]
    try:
        # Tag products as food/import with the stored food list (if one was uploaded).
        with get_engine().begin() as c:
            food_codes, food_names = load_food_rules(c)
            if food_codes or food_names:
                ensure_sales_excluded(c)
        has_food_rules = bool(food_codes or food_names)
        sales = df.copy()
        # Newer Accurate exports carry both "Invoice Date" and "Date". Renaming "date" too would create two
        # "invoice_date" columns ("cannot assemble with duplicate keys"), so the Invoice Date column wins.
        if "invoice_date" in sales.columns and "date" in sales.columns:
            sales = sales.drop(columns=["date"])
        sales = sales.rename(columns={
            "SKU": "sku", "date": "invoice_date", "quantity": "quantity",
            "amount": "amount", "COGS": "cogs_accurate", "GPAO": "gpao_accurate",
            "GPAO (%)": "gpao_pct_acc", "week": "week_label", "month": "month_label",
            "quarter": "quarter_label", "year": "year_val",
        })
        dup = sales.columns[sales.columns.duplicated()].tolist()
        if dup:  # any other repeated header: keep the first occurrence
            print(f"[etl_confirm] duplicate columns dropped: {dup}")
            sales = sales.loc[:, ~sales.columns.duplicated()]
        sales["sku"] = sales["sku"].astype(str)
        if "invoice_date" in sales.columns:
            sales["invoice_date"] = pd.to_datetime(sales["invoice_date"]).dt.date
        for col in ["cogs_accurate", "unit"]:
            if col not in sales.columns:
                sales[col] = None
        if "gpao_pct_acc" in sales.columns:
            def parse_pct(val):
                if pd.isna(val): return None
                if isinstance(val, str):
                    return float(val.replace("%", "").strip()) / 100 if "%" in val else float(val)
                return float(val)
            sales["gpao_pct_acc"] = sales["gpao_pct_acc"].apply(parse_pct)
        keep_cols = ["sku", "invoice_date", "quantity", "unit", "amount",
                     "cogs_accurate", "gpao_accurate", "gpao_pct_acc",
                     "week_label", "month_label", "quarter_label", "year_val"]
        for col in keep_cols:
            if col not in sales.columns:
                sales[col] = None
        sales = sales[keep_cols]
        products = df[["SKU", "product_name"]].drop_duplicates(subset=["SKU"])
        products = products.groupby("SKU").first().reset_index()
        conn = get_raw_conn()
        conn.autocommit = False
        cur = conn.cursor()
        try:
            food_tagged = 0
            prod_rows = []
            for sku_, pname in zip(products["SKU"], products["product_name"]):
                ptype = None
                if has_food_rules:
                    ptype = "food" if is_food(sku_, pname, food_codes, food_names) else "import"
                    food_tagged += ptype == "food"
                prod_rows.append((str(sku_), pname, ptype))
            # One statement for all products (was one network round trip per product)
            execute_values(cur, """
                INSERT INTO products (sku, name, product_type) VALUES %s
                ON CONFLICT (sku) DO UPDATE SET name = EXCLUDED.name,
                    product_type = COALESCE(EXCLUDED.product_type, products.product_type),
                    updated_at = NOW()
            """, prod_rows, page_size=1000)
            # ── Append mode: load into temp table, then INSERT ... ON CONFLICT DO NOTHING ──
            cur.execute("""
                CREATE TEMP TABLE _sales_stage (LIKE sales INCLUDING DEFAULTS)
                ON COMMIT DROP
            """)
            buffer = io.StringIO()
            writer = csv.writer(buffer, delimiter='\t', quoting=csv.QUOTE_MINIMAL)
            for _, row in sales.iterrows():
                writer.writerow([
                    str(row["sku"]),
                    str(row["invoice_date"]) if pd.notna(row["invoice_date"]) else r'\N',
                    int(row["quantity"]) if pd.notna(row["quantity"]) else r'\N',
                    str(row["unit"]) if pd.notna(row["unit"]) else r'\N',
                    float(row["amount"]) if pd.notna(row["amount"]) else r'\N',
                    float(row["cogs_accurate"]) if pd.notna(row["cogs_accurate"]) else r'\N',
                    float(row["gpao_accurate"]) if pd.notna(row["gpao_accurate"]) else r'\N',
                    float(row["gpao_pct_acc"]) if pd.notna(row["gpao_pct_acc"]) else r'\N',
                    str(row["week_label"]) if pd.notna(row["week_label"]) else r'\N',
                    str(row["month_label"]) if pd.notna(row["month_label"]) else r'\N',
                    str(row["quarter_label"]) if pd.notna(row["quarter_label"]) else r'\N',
                    int(row["year_val"]) if pd.notna(row["year_val"]) else r'\N',
                ])
            buffer.seek(0)
            cur.copy_from(buffer, '_sales_stage', sep='\t', null=r'\N',
                          columns=('sku','invoice_date','quantity','unit','amount',
                                   'cogs_accurate','gpao_accurate','gpao_pct_acc',
                                   'week_label','month_label','quarter_label','year_val'))
            # Food products never enter the sales table: their rows go to the archive (reason 'food').
            food_list = [p_[0] for p_ in prod_rows if p_[2] == "food"]
            food_rows = 0
            if food_list:
                cur.execute("""
                    INSERT INTO sales_excluded (sku, invoice_date, quantity, unit, amount, cogs_accurate,
                        gpao_accurate, gpao_pct_acc, week_label, month_label, quarter_label, year_val, reason)
                    SELECT sku, invoice_date, quantity, unit, amount, cogs_accurate, gpao_accurate, gpao_pct_acc,
                           week_label, month_label, quarter_label, year_val, 'food'
                    FROM _sales_stage WHERE sku = ANY(%s)
                """, (food_list,))
                food_rows = cur.rowcount
                cur.execute("DELETE FROM _sales_stage WHERE sku = ANY(%s)", (food_list,))
            # Deduplicate: skip rows that already exist (same sku + date + qty + amount)
            cur.execute("""
                INSERT INTO sales (sku, invoice_date, quantity, unit, amount,
                                   cogs_accurate, gpao_accurate, gpao_pct_acc,
                                   week_label, month_label, quarter_label, year_val)
                SELECT s.sku, s.invoice_date, s.quantity, s.unit, s.amount,
                       s.cogs_accurate, s.gpao_accurate, s.gpao_pct_acc,
                       s.week_label, s.month_label, s.quarter_label, s.year_val
                FROM _sales_stage s
                WHERE NOT EXISTS (
                    SELECT 1 FROM sales e
                    WHERE e.sku = s.sku
                      AND e.invoice_date = s.invoice_date
                      AND e.quantity = s.quantity
                      AND e.amount = s.amount
                )
            """)
            inserted = cur.rowcount
            skipped = len(sales) - inserted - food_rows
            conn.commit()
            total = inserted
        except Exception as e:
            conn.rollback()
            raise e
        finally:
            cur.close()
            conn.close()
        engine = get_engine()
        with engine.connect() as c:
            r = c.execute(text("""
                SELECT (SELECT COUNT(*) FROM products),(SELECT COUNT(*) FROM sales),
                       (SELECT MIN(invoice_date) FROM sales),(SELECT MAX(invoice_date) FROM sales),
                       (SELECT COUNT(DISTINCT sku) FROM sales)
            """))
            row = r.fetchone()
        if os.path.exists(cached["filepath"]):
            os.remove(cached["filepath"])
        del processed_cache[file_id]

        # ── AUTO-FORECAST after ETL ──
        _run_auto_forecast(engine)

        return {
            "status": "success",
            "message": f"{total:,} baris baru ditambahkan ({skipped:,} duplikat di-skip)"
                       + (f" · {food_rows:,} baris penjualan food dikeluarkan ({food_tagged:,} produk food)" if food_rows else ""),
            "food_rows_excluded": food_rows,
            "food_products_tagged": food_tagged,
            "database": {"products": row[0], "sales": row[1],
                         "date_range": f"{row[2]} s/d {row[3]}", "unique_skus": row[4]},
            "forecast_status": "auto-run complete" if "latest" in forecast_cache else "skipped",
        }
    except Exception as e:
        raise HTTPException(500, f"Gagal memuat data: {str(e)}")


# ── Stock Upload ──────────────────────────────────────────────

@app.post("/api/stock/upload")
async def stock_upload(file: UploadFile = File(...), report_date: str = Form(None)):
    """report_date = the date of the stock report (YYYY-MM-DD). Defaults to today when not given.
    The upload time is kept separately in created_at."""
    try:
        snap_day = date.fromisoformat(report_date) if report_date else date.today()
    except ValueError:
        raise HTTPException(400, "report_date harus format YYYY-MM-DD")
    if snap_day > date.today():
        raise HTTPException(400, "Tanggal laporan stok tidak boleh di masa depan")
    if not file.filename.endswith((".xls", ".xlsx")):
        raise HTTPException(400, "Format harus .xls/.xlsx")
    content = await file.read()
    tmp = os.path.join(UPLOAD_DIR, f"stok_{uuid.uuid4().hex[:8]}{os.path.splitext(file.filename)[1]}")
    with open(tmp, "wb") as f:
        f.write(content)
    try:
        ext = os.path.splitext(tmp)[1].lower()
        raw = pd.read_excel(tmp, engine="xlrd" if ext == ".xls" else "openpyxl", header=None)
        raw.columns = range(len(raw.columns))
        raw = raw.dropna(subset=[0])
        raw[0] = raw[0].astype(str).str.strip()
        # Parse Indonesian number format: "312.507,92" -> 312507.92
        def parse_id_qty(val):
            if pd.isna(val): return 0
            s = str(val).strip()
            if s in ("", "-", "QTY"): return 0
            s = s.replace(".", "").replace(",", ".")
            try: return int(float(s))
            except: return 0
        raw[2] = raw[2].apply(parse_id_qty)
        df = raw[raw[0].apply(lambda s: len(s.replace("-",".").split(".")) >= 3)].copy()
        df = df.rename(columns={0:"sku", 1:"product", 2:"quantity"})
        df["quantity"] = df["quantity"].astype(int)
        today = snap_day
        conn = get_raw_conn()
        cur = conn.cursor()
        conn.autocommit = False
        try:
            # Auto-insert missing SKUs into products table so stock upload
            # doesn't fail on FK constraint. Product name taken from stock file.
            stock_skus = sorted(set(df["sku"].astype(str).str.strip()))
            cur.execute("SELECT sku FROM products WHERE sku = ANY(%s)", (stock_skus,))
            existing_skus = {r[0] for r in cur.fetchall()}
            missing_skus = [sku for sku in stock_skus if sku not in existing_skus]
            if missing_skus:
                # Build SKU -> product name mapping from the stock file
                sku_name_map = df.drop_duplicates(subset=["sku"]).set_index("sku")["product"].to_dict()
                execute_values(cur, "INSERT INTO products (sku, name) VALUES %s ON CONFLICT DO NOTHING",
                               [(m, sku_name_map.get(m, m)) for m in missing_skus], page_size=1000)

            cur.execute("DELETE FROM stock_snapshots WHERE snapshot_date = %s", (today,))
            execute_values(cur, "INSERT INTO stock_snapshots (sku, quantity, snapshot_date) VALUES %s ON CONFLICT DO NOTHING",
                           [(str(s_), int(q_), today) for s_, q_ in zip(df["sku"], df["quantity"])], page_size=1000)
            conn.commit()
            saved = len(df)
        except Exception as e:
            conn.rollback()
            raise e
        finally:
            cur.close()
            conn.close()
        os.remove(tmp)
        return {"status":"success","message":f"{saved} SKU stock saved for {today}","date":str(today),"total_skus":saved}
    except Exception as e:
        if os.path.exists(tmp): os.remove(tmp)
        raise HTTPException(500, f"Gagal parse: {e}")


# ══════════════════════════════════════════════════════════════
# PURCHASE ORDER APIs
# ══════════════════════════════════════════════════════════════

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


_PO_COLS_READY = False


def ensure_po_columns(conn):
    """Additive, idempotent: extra PO fields used by the PO master import (existing rows untouched).
    ALTER TABLE takes an exclusive lock even when nothing changes, so it only runs when a column is
    actually missing (checked once per server instance)."""
    global _PO_COLS_READY
    if _PO_COLS_READY:
        return
    have = {r[0] for r in conn.execute(text(
        "SELECT column_name FROM information_schema.columns WHERE table_name = 'purchase_orders'"))}
    idx = conn.execute(text("SELECT 1 FROM pg_indexes WHERE indexname = 'ux_po_line_key'")).fetchone()
    if {"po_number", "line_key", "description", "courier"} <= have and idx:
        _PO_COLS_READY = True
        return
    conn.execute(text("""
        ALTER TABLE purchase_orders
            ADD COLUMN IF NOT EXISTS po_number VARCHAR(60),
            ADD COLUMN IF NOT EXISTS line_key VARCHAR(160),
            ADD COLUMN IF NOT EXISTS description TEXT,
            ADD COLUMN IF NOT EXISTS courier VARCHAR(60)"""))
    conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ux_po_line_key ON purchase_orders (line_key) WHERE line_key IS NOT NULL"))
    _PO_COLS_READY = True


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


@app.post("/api/po/upload")
async def po_upload(file: UploadFile = File(...)):
    """
    Upload ongoing PO dari Excel.
    Expected columns: SKU | Product Name | Qty Ordered | Supplier (optional) | Order Date (optional) | Expected Date (optional)
    Atau minimal: product_name + qty_ordered (akan di-lookup SKU dari master/products).
    """
    if not file.filename.endswith((".xls", ".xlsx")):
        raise HTTPException(400, "Format harus .xls/.xlsx")
    content = await file.read()
    tmp = os.path.join(UPLOAD_DIR, f"po_{uuid.uuid4().hex[:8]}{os.path.splitext(file.filename)[1]}")
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
            res = _import_po_master(lines, warnings, file.filename)
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
            rows = c.execute(text("SELECT sku, name FROM products")).fetchall()
            for r in rows:
                product_lookup[r[1].strip().lower()] = r[0]

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
                sku = product_lookup.get(pname.lower(), "")
                # Partial match fallback (products table)
                if not sku:
                    for db_name, db_sku in product_lookup.items():
                        if db_name in pname.lower() or pname.lower() in db_name:
                            sku = db_sku
                            break

            # Fallback to master_sku if still no match
            if not sku and master_sku:
                sku = master_sku.get(pname.lower(), "")
                if not sku:
                    for desc_key, sku_val in master_sku.items():
                        if desc_key in pname.lower() or pname.lower() in desc_key:
                            sku = sku_val
                            break
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
        conn = get_raw_conn()
        conn.autocommit = False
        cur = conn.cursor()
        try:
            # Auto-insert new products found via master_sku
            if new_products:
                execute_values(cur, "INSERT INTO products (sku, name) VALUES %s ON CONFLICT (sku) DO NOTHING",
                               list({p_["sku"]: (p_["sku"], p_["name"]) for p_ in new_products}.values()))
            # The file is the new list of open POs. Previously open lines are marked 'superseded'
            # (NOT 'cancelled'), so a replaced line can be told apart from a real cancellation and
            # the history stays. Upsert by PO number arrives with the P2 migration (po_number column).
            cur.execute("UPDATE purchase_orders SET status = 'superseded', updated_at = NOW() WHERE status IN ('pending', 'partial')")
            execute_values(cur, """
                INSERT INTO purchase_orders (sku, qty_ordered, supplier, order_date, expected_date, status) VALUES %s
            """, [(po["sku"], po["qty_ordered"], po["supplier"],
                   po["order_date"].date() if pd.notna(po["order_date"]) else None,
                   po["expected_date"].date() if pd.notna(po["expected_date"]) else None,
                   po["status"]) for po in resolved])
            conn.commit()
        except Exception as e:
            conn.rollback()
            raise e
        finally:
            cur.close()
            conn.close()

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


@app.get("/api/po/list")
async def po_list(status: str = "open"):
    """PO lines with everything the PO & Supplier page shows. status = open | overdue | all | <status>.
    remaining = ordered − received (negative = over-received). age_days = days since PO date
    (until the receipt date for finished lines). aging = open line older than po_aging_warn_days."""
    cfg = get_settings()
    today = date.today()
    engine = get_engine()
    with engine.begin() as conn:
        ensure_po_columns(conn)
        rows = conn.execute(text("""
            SELECT po.id, po.sku, COALESCE(p.name, po.description, po.sku), po.qty_ordered, COALESCE(po.qty_received, 0),
                   po.supplier, po.status, po.order_date, po.expected_date, po.received_date,
                   po.po_number, po.courier, po.notes
            FROM purchase_orders po LEFT JOIN products p ON p.sku = po.sku
            WHERE po.status <> 'superseded'
            ORDER BY po.order_date DESC NULLS LAST, po.po_number, po.id
        """)).fetchall()
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


@app.get("/api/po/summary")
async def po_summary():
    """PO & Supplier page header + delivery performance. Performance figures are only returned when
    the underlying receipt data exists; otherwise `available: false` with the reason."""
    cfg = get_settings()
    today = date.today()
    engine = get_engine()
    with engine.begin() as conn:
        ensure_po_columns(conn)
        rows = conn.execute(text("""
            SELECT id, sku, qty_ordered, COALESCE(qty_received,0), status, order_date, expected_date,
                   received_date, supplier, po_number
            FROM purchase_orders
        """)).fetchall()
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


def _stockout_rate(engine, min_snapshots=4, weeks=12):
    """SKU-weeks with stock <= 0 / SKU-weeks with stock data, forecasted best sellers, last `weeks` weeks.
    Not reported until there are at least `min_snapshots` snapshots in that window."""
    try:
        best = {r["sku"] for r in get_forecast_light(engine)}
    except Exception:
        best = set()
    with engine.connect() as conn:
        mx = conn.execute(text("SELECT MAX(snapshot_date) FROM stock_snapshots")).scalar()
        if not mx:
            return {"available": False, "reason": "no_snapshots", "snapshots": 0, "required": min_snapshots}
        rows = conn.execute(text("""
            SELECT snapshot_date, sku, SUM(quantity) FROM stock_snapshots
            WHERE snapshot_date > :a GROUP BY snapshot_date, sku
        """), {"a": mx - timedelta(weeks=weeks)}).fetchall()
    dates = {r[0] for r in rows}
    if len(dates) < min_snapshots:
        return {"available": False, "reason": "needs_weekly_stock_snapshots", "snapshots": len(dates),
                "required": min_snapshots}
    obs = [r for r in rows if not best or r[1] in best]
    if not obs:
        return {"available": False, "reason": "no_best_seller_stock_rows", "snapshots": len(dates)}
    oos = sum(1 for r in obs if (r[2] or 0) <= 0)
    return {"available": True, "rate_pct": round(oos / len(obs) * 100, 1), "sku_weeks": len(obs),
            "stockout_sku_weeks": oos, "snapshots": len(dates), "weeks": weeks}


@app.delete("/api/po/{po_id}")
async def po_delete(po_id: int):
    engine = get_engine()
    with engine.connect() as conn:
        conn.execute(text("DELETE FROM purchase_orders WHERE id = :id"), {"id": po_id})
        conn.commit()
    return {"status": "deleted", "id": po_id}


@app.patch("/api/po/{po_id}")
async def po_update(po_id: int, body: dict = None):
    """Update a PO line. qty_received sets the status automatically (completed / partial / pending)
    and stamps the receipt date (today, or body.received_date) when goods are received for the first time."""
    if not body:
        raise HTTPException(400, "Body kosong")
    sets = []
    params = {"id": po_id}
    if "qty_received" in body:
        try:
            q = int(body["qty_received"])
        except (TypeError, ValueError):
            raise HTTPException(400, "qty_received harus angka")
        if q < 0:
            raise HTTPException(400, "qty_received tidak boleh minus")
        params["qty_received"] = q
        sets.append("qty_received = :qty_received")
        if "status" not in body:
            sets.append("""status = CASE WHEN status IN ('cancelled','superseded') THEN status
                                         WHEN :qty_received >= qty_ordered THEN 'completed'
                                         WHEN status = 'closed' THEN 'closed'
                                         WHEN :qty_received > 0 THEN 'partial' ELSE 'pending' END""")
        rd = body.get("received_date")
        try:
            params["rd"] = date.fromisoformat(rd) if rd else date.today()
        except ValueError:
            raise HTTPException(400, "received_date harus YYYY-MM-DD")
        sets.append("received_date = CASE WHEN :qty_received > 0 THEN COALESCE(received_date, :rd) ELSE NULL END")
    elif body.get("received_date"):
        # correct the receipt date of a line that already has goods in (used for actual lead time)
        try:
            params["rd"] = date.fromisoformat(body["received_date"])
        except ValueError:
            raise HTTPException(400, "received_date harus YYYY-MM-DD")
        sets.append("received_date = CASE WHEN COALESCE(qty_received, 0) > 0 THEN :rd ELSE received_date END")
    if "status" in body:
        st = body["status"]
        if st == "reopen":      # undo "close short": back to pending / partial from the received qty
            sets.append("status = CASE WHEN COALESCE(qty_received,0) >= qty_ordered THEN 'completed' "
                        "WHEN COALESCE(qty_received,0) > 0 THEN 'partial' ELSE 'pending' END")
        elif st in ("pending", "partial", "completed", "cancelled", "closed"):
            sets.append("status = :status")
            params["status"] = st
        else:
            raise HTTPException(400, "status tidak dikenal")
    if "notes" in body:
        sets.append("notes = :notes")
        params["notes"] = (str(body["notes"]).strip() or None) if body["notes"] is not None else None
    if not sets:
        raise HTTPException(400, "Tidak ada field yang di-update")
    sets.append("updated_at = NOW()")
    engine = get_engine()
    with engine.connect() as conn:
        row = conn.execute(text(f"UPDATE purchase_orders SET {', '.join(sets)} WHERE id = :id "
                                "RETURNING id, qty_ordered, qty_received, status, received_date"), params).fetchone()
        conn.commit()
    if not row:
        raise HTTPException(404, "PO line tidak ditemukan")
    return {"status": "updated", "id": row[0], "qty_ordered": row[1], "qty_received": row[2],
            "line_status": row[3], "received_date": str(row[4]) if row[4] else None}


@app.post("/api/po/receive-all")
async def po_receive_all(body: dict = None):
    """Mark every open line of one PO number as fully received (today, or body.received_date)."""
    po = (body or {}).get("po_number")
    if not po:
        raise HTTPException(400, "po_number wajib")
    rd = (body or {}).get("received_date")
    engine = get_engine()
    with engine.begin() as conn:
        ensure_po_columns(conn)
        n = conn.execute(text("""
            UPDATE purchase_orders SET qty_received = qty_ordered, status = 'completed',
                   received_date = COALESCE(received_date, :rd), updated_at = NOW()
            WHERE po_number = :po AND status IN ('pending', 'partial')
        """), {"po": po, "rd": date.fromisoformat(rd) if rd else date.today()}).rowcount
    return {"status": "updated", "po_number": po, "lines": n}


# ══════════════════════════════════════════════════════════════
# DASHBOARD APIs
# ══════════════════════════════════════════════════════════════

@app.get("/api/db/stats")
async def db_stats():
    engine = get_engine()
    with engine.connect() as conn:
        row = conn.execute(text("""
            SELECT (SELECT COUNT(*) FROM products),(SELECT COUNT(*) FROM sales),
                   (SELECT MIN(invoice_date) FROM sales),(SELECT MAX(invoice_date) FROM sales),
                   (SELECT COUNT(DISTINCT sku) FROM sales)
        """)).fetchone()
        fc = 0
        try:
            fc = conn.execute(text("SELECT COUNT(*) FROM forecast_results")).fetchone()[0]
        except Exception:
            pass
        snap_date, snap_skus = None, 0
        try:
            snap = conn.execute(text("SELECT MAX(snapshot_date),COUNT(DISTINCT sku) FROM stock_snapshots")).fetchone()
            snap_date, snap_skus = snap[0], snap[1] or 0
        except Exception:
            pass
    return {
        "products": row[0], "sales": row[1],
        "date_range": f"{row[2]} s/d {row[3]}" if row[2] else "Belum ada data",
        "last_update": str(row[3]) if row[3] else None,
        "unique_skus": row[4], "forecast_entries": fc,
        "stock_date": str(snap_date) if snap_date else None, "stock_skus": snap_skus,
    }


@app.get("/api/overview")
async def overview_summary(
    start: str = None, end: str = None,
    preset: str = "l4w",
    top_sort: str = "revenue",
    product_type: str = "all"
):
    engine = get_engine()
    with engine.connect() as conn:
        mx = conn.execute(text("SELECT MAX(invoice_date) FROM sales")).fetchone()
        max_date = mx[0] if mx and mx[0] else None
        if not max_date:
            return {"error":"No sales data","kpis":{},"comparison":{},"trend":[],"top_products":[],"period":{},"forecast_summary":None}

        if start and end:
            dt_end = date.fromisoformat(end)
            dt_start = date.fromisoformat(start)
        else:
            dt_end = max_date
            preset_days = {"l7d":7,"l4w":28,"l8w":56,"l12w":84}
            if preset == "this_month":
                dt_start = dt_end.replace(day=1)
            elif preset == "last_month":
                first_this = dt_end.replace(day=1)
                dt_start = (first_this - timedelta(days=1)).replace(day=1)
                dt_end = first_this - timedelta(days=1)
            else:
                days = preset_days.get(preset, 28)
                dt_start = dt_end - timedelta(days=days - 1)

        period_days = (dt_end - dt_start).days + 1
        period_weeks = max(period_days / 7, 1)

        # Product type filter (import / food / all) applies to every figure below.
        tf = f"AND COALESCE(p.product_type, 'import') = '{product_type}'" if product_type in ("import", "food") else ""
        SRC = "sales s JOIN products p ON s.sku = p.sku" if tf else "sales s"
        prev_end = dt_start - timedelta(days=1)
        prev_start = prev_end - timedelta(days=period_days - 1)

        # KPIs
        kpi = conn.execute(text(f"""
            SELECT COALESCE(SUM(s.quantity),0), COALESCE(SUM(s.amount),0), COUNT(DISTINCT s.sku)
            FROM {SRC} WHERE s.invoice_date >= :s AND s.invoice_date <= :e {tf}
        """), {"s": dt_start, "e": dt_end}).fetchone()
        total_sales = int(kpi[0]); revenue = float(kpi[1]); active = int(kpi[2])
        avg_weekly = round(total_sales / period_weeks)

        # Trending up
        mid = dt_start + timedelta(days=period_days // 2)
        trending = (0,)
        try:
            trending = conn.execute(text(f"""
                WITH fh AS (SELECT s.sku,SUM(s.quantity) q FROM {SRC} WHERE s.invoice_date>=:s AND s.invoice_date<:m {tf} GROUP BY s.sku),
                     sh AS (SELECT s.sku,SUM(s.quantity) q FROM {SRC} WHERE s.invoice_date>=:m AND s.invoice_date<=:e {tf} GROUP BY s.sku)
                SELECT COUNT(*) FROM sh JOIN fh ON sh.sku=fh.sku WHERE fh.q>0 AND sh.q>fh.q
            """), {"s": dt_start, "m": mid, "e": dt_end}).fetchone()
        except Exception:
            trending = (0,)

        # Previous period KPIs
        prev = conn.execute(text(f"""
            SELECT COALESCE(SUM(s.quantity),0), COALESCE(SUM(s.amount),0), COUNT(DISTINCT s.sku)
            FROM {SRC} WHERE s.invoice_date >= :s AND s.invoice_date <= :e {tf}
        """), {"s": prev_start, "e": prev_end}).fetchone()
        p_sales = int(prev[0]); p_rev = float(prev[1]); p_active = int(prev[2])
        p_avg = round(p_sales / period_weeks) if period_weeks else 0

        def pchg(c, p):
            return round((c - p) / p * 100, 1) if p else None

        # Trend chart
        if period_days <= 14:
            agg = "day"
            tq = text(f"SELECT s.invoice_date::date d,SUM(s.quantity) q,SUM(s.amount) r FROM {SRC} WHERE s.invoice_date>=:s AND s.invoice_date<=:e {tf} GROUP BY d ORDER BY d")
        elif period_days <= 90:
            agg = "week"
            tq = text(f"SELECT DATE_TRUNC('week',s.invoice_date)::date d,SUM(s.quantity) q,SUM(s.amount) r FROM {SRC} WHERE s.invoice_date>=:s AND s.invoice_date<=:e {tf} GROUP BY d ORDER BY d")
        else:
            agg = "month"
            tq = text(f"SELECT DATE_TRUNC('month',s.invoice_date)::date d,SUM(s.quantity) q,SUM(s.amount) r FROM {SRC} WHERE s.invoice_date>=:s AND s.invoice_date<=:e {tf} GROUP BY d ORDER BY d")
        trend = conn.execute(tq, {"s": dt_start, "e": dt_end}).fetchall()

        # Top products
        order_col = "r" if top_sort == "revenue" else "q"
        top_type_filter = tf
        top_all = conn.execute(text(f"""
            SELECT s.sku,p.name,SUM(s.quantity) q,SUM(s.amount) r
            FROM sales s JOIN products p ON s.sku=p.sku
            WHERE s.invoice_date>=:s AND s.invoice_date<=:e {top_type_filter}
            GROUP BY s.sku,p.name ORDER BY {order_col} DESC LIMIT 50
        """), {"s": dt_start, "e": dt_end}).fetchall()
        # Previous-period qty and first sale date for all top SKUs in 2 queries
        # (was 2 queries per SKU = ~100 round trips to Supabase).
        top_skus = [r[0] for r in top_all]
        prev_map, first_map = {}, {}
        if top_skus:
            prev_map = {k: v for k, v in conn.execute(text("""
                SELECT sku, COALESCE(SUM(quantity),0) FROM sales
                WHERE sku = ANY(:k) AND invoice_date>=:s AND invoice_date<=:e GROUP BY sku
            """), {"k": top_skus, "s": prev_start, "e": prev_end}).fetchall()}
            first_map = {k: v for k, v in conn.execute(text("""
                SELECT sku, MIN(invoice_date) FROM sales WHERE sku = ANY(:k) GROUP BY sku
            """), {"k": top_skus}).fetchall()}
        top_g = []
        for r in top_all:
            prev_qty = int(prev_map.get(r[0], 0))
            curr_qty = int(r[2])
            first_seen = first_map.get(r[0])
            is_new = first_seen is not None and first_seen >= dt_start
            if prev_qty > 0:
                growth = round((curr_qty - prev_qty) / prev_qty * 100, 1)
            elif is_new:
                growth = None
            else:
                growth = None
            top_g.append({"sku":r[0],"name":r[1],"qty":curr_qty,"revenue":float(r[3]),
                          "growth":growth,"is_new":is_new,"prev_qty":prev_qty})
        if top_sort == "growth":
            has_g = [x for x in top_g if x["growth"] is not None]
            no_g = [x for x in top_g if x["growth"] is None]
            has_g.sort(key=lambda x: x["growth"], reverse=True)
            top_g = has_g + no_g
        top_g = top_g[:10]

    # Forecast summary (memory cache, else one SQL aggregate)
    try:
        fc_summary = forecast_summary_fast(engine, product_type)
    except Exception as e:
        print(f"[overview] forecast summary skipped: {e}")
        fc_summary = None

    return {
        "period": {"start":str(dt_start),"end":str(dt_end),"days":period_days,"aggregation":agg},
        "prev_period": {"start":str(prev_start),"end":str(prev_end)},
        "kpis": {"total_sales":total_sales,"revenue":revenue,"avg_weekly_sales":avg_weekly,"active_products":active,"trending_up":trending[0] if trending else 0},
        "comparison": {"total_sales":pchg(total_sales,p_sales),"revenue":pchg(revenue,p_rev),"avg_weekly_sales":pchg(avg_weekly,p_avg),"active_products":pchg(active,p_active)},
        "trend": [{"date":str(r[0]),"quantity":int(r[1]),"revenue":float(r[2])} for r in trend],
        "top_products": top_g,
        "forecast_summary": fc_summary,
    }


@app.get("/api/best-sellers-at-risk")
async def best_sellers_at_risk(product_type: str = "all", limit: int = 50):
    """Understocked best sellers (Pareto set), same numbers as the inventory pages.
    The stock comparison vs ~8 weeks ago is only returned when a second, older snapshot exists."""
    engine = get_engine()
    inv = build_inventory(engine, product_type, scope="bestseller")
    best_only = any(r["forecasted"] for r in inv["rows"])
    res = [r for r in inv["rows"] if r["status"] == "understocked" and r["l4w_qty"] > 0]

    snap_now = date.fromisoformat(inv["stock_date"]) if inv["stock_date"] else None
    snap_prev, prev_stk = None, {}
    if snap_now and inv["snapshot_count"] >= 2:
        with engine.connect() as conn:
            dates = [r[0] for r in conn.execute(text(
                "SELECT DISTINCT snapshot_date FROM stock_snapshots WHERE snapshot_date < :d ORDER BY snapshot_date DESC"),
                {"d": snap_now}).fetchall()]
            older = [d for d in dates if (snap_now - d).days >= 49]
            snap_prev = older[0] if older else (dates[-1] if dates else None)
            if snap_prev:
                prev_stk = {r[0]: int(r[1]) for r in conn.execute(text(
                    "SELECT sku, SUM(quantity) FROM stock_snapshots WHERE snapshot_date = :d GROUP BY sku"),
                    {"d": snap_prev}).fetchall()}
    results = []
    for r in res:
        ps = prev_stk.get(r["sku"]) if snap_prev else None
        results.append({"sku": r["sku"], "name": r["product_name"], "l4w_qty": r["l4w_qty"],
                        "revenue": r["l4w_revenue"], "avg_weekly": r["avg_l4w"],
                        "current_stock": r["current_stock"], "ongoing_po": r["ongoing_po"],
                        "coverage": r["coverage"], "days_of_inventory": r["days_of_inventory"],
                        "status": r["status"], "urgency": r["urgency"], "slack_weeks": r["slack_weeks"],
                        "stockout_week": r["stockout_week"], "reco_po": r["reco_po"],
                        "needs_po_check": r["needs_po_check"],
                        "prev_stock": ps,
                        "stock_change_pct": round((r["current_stock"] - ps) / ps * 100, 1) if ps else None})
    results.sort(key=lambda x: x["revenue"], reverse=True)
    return {
        "results": results[:limit],
        "total_understocked": len(results),
        "stock_now_date": inv["stock_date"],
        "stock_prev_date": str(snap_prev) if snap_prev else None,
        "weeks_between": round((snap_now - snap_prev).days / 7, 1) if snap_prev else None,
        "comparison_available": snap_prev is not None,
        "best_sellers_only": best_only,
        "freshness": inv["freshness"],
    }


@app.get("/api/weekly-analysis")
async def weekly_analysis(product_type: str = "import", scope: str = "all"):
    """scope=bestseller -> only SKUs in the Pareto 80% set (the ones that have a forecast)."""
    engine = get_engine()
    type_filter = ""
    if product_type in ("import", "food"):
        type_filter = f"AND COALESCE(p.product_type, 'import') = '{product_type}'"
    with engine.connect() as conn:
        # Only the last 8 weeks are needed week-by-week; lifetime figures come pre-aggregated.
        week_rows = conn.execute(text(f"""
            SELECT DISTINCT DATE_TRUNC('week', s.invoice_date)::date AS w
            FROM sales s JOIN products p ON s.sku = p.sku
            WHERE s.invoice_date IS NOT NULL {type_filter}
            ORDER BY w DESC LIMIT 8
        """)).fetchall()
        if not week_rows:
            return {"results": [], "week_labels": {}}
        spark_w = sorted(r[0] for r in week_rows)
        totals = conn.execute(text(f"""
            SELECT s.sku, MIN(p.name), SUM(s.quantity),
                   COUNT(DISTINCT DATE_TRUNC('week', s.invoice_date))
            FROM sales s JOIN products p ON s.sku = p.sku
            WHERE s.invoice_date IS NOT NULL {type_filter}
            GROUP BY s.sku ORDER BY s.sku
        """)).fetchall()
        recent = conn.execute(text(f"""
            SELECT s.sku, DATE_TRUNC('week', s.invoice_date)::date AS w, SUM(s.quantity)
            FROM sales s JOIN products p ON s.sku = p.sku
            WHERE s.invoice_date >= :w8 {type_filter}
            GROUP BY s.sku, w
        """), {"w8": spark_w[0]}).fetchall()
        n_weeks = conn.execute(text(f"""
            SELECT COUNT(DISTINCT DATE_TRUNC('week', s.invoice_date))
            FROM sales s JOIN products p ON s.sku = p.sku
            WHERE s.invoice_date IS NOT NULL {type_filter}
        """)).scalar()

    if scope == "bestseller":
        try:
            best = {r["sku"] for r in get_forecast_light(engine)}
        except Exception:
            best = set()
        totals = [t for t in totals if t[0] in best]

    weekly = {}
    for sku, w, q in recent:
        weekly.setdefault(sku, {})[w] = q
    recent_4 = spark_w[-4:]
    last_2 = spark_w[-2:]

    results = []
    for sku, name, lifetime_q, active_weeks in totals:
        wmap = weekly.get(sku, {})

        w1 = int(wmap.get(last_2[-1], 0)) if len(last_2) >= 1 else 0
        w2 = int(wmap.get(last_2[-2], 0)) if len(last_2) >= 2 else 0
        change = w1 - w2
        growth = round((change / w2) * 100, 1) if w2 > 0 else (100.0 if w1 > 0 else 0.0)

        l4w = sum(int(wmap.get(w, 0)) for w in recent_4)
        avg_l4w = round(l4w / max(len(recent_4), 1), 1)
        lifetime = int(lifetime_q or 0)
        active = int(active_weeks)
        avg_lt = math.ceil(lifetime / active) if active > 0 else 0
        diff = round(avg_l4w - avg_lt, 1)
        diff_pct = round((avg_l4w / avg_lt) * 100, 1) if avg_lt > 0 else 0.0

        trend = "up" if growth > 20 else ("down" if growth < -20 else "stable")
        spark = [int(wmap.get(w, 0)) for w in spark_w]

        results.append({
            "sku": sku, "product_name": name, "trend": trend,
            "w1": w1, "w2": w2, "change": change, "growth": growth,
            "avg_l4w": avg_l4w, "avg_lt": avg_lt,
            "l4w_vs_lt": diff, "l4w_vs_lt_pct": diff_pct,
            "lifetime": lifetime, "l4w": l4w, "sparkline": spark,
        })

    results.sort(key=lambda x: x["l4w"], reverse=True)
    return {
        "results": results,
        "week_labels": {"w1": str(last_2[-1]) if last_2 else "", "w2": str(last_2[-2]) if len(last_2) >= 2 else ""},
        "total_weeks": int(n_weeks or 0), "total_skus": len(results),
    }


@app.get("/api/coverage")
async def coverage_analysis():
    """DEPRECATED: not used by the dashboard (full sales scan). Kept for any outside caller;
    use /api/inventory instead."""
    engine = get_engine()
    with engine.connect() as conn:
        wdf = pd.read_sql(text("""
            SELECT s.sku, p.name as product_name,
                   DATE_TRUNC('week', s.invoice_date)::date as week_start,
                   SUM(s.quantity) as qty
            FROM sales s JOIN products p ON s.sku = p.sku
            WHERE s.invoice_date IS NOT NULL
            GROUP BY s.sku, p.name, DATE_TRUNC('week', s.invoice_date)::date
        """), conn)
        try:
            stk = pd.read_sql(text("""
                SELECT sku, quantity as current_stock FROM stock_snapshots
                WHERE snapshot_date = (SELECT MAX(snapshot_date) FROM stock_snapshots)
            """), conn)
        except Exception:
            stk = pd.DataFrame(columns=["sku", "current_stock"])
        try:
            snap = conn.execute(text("SELECT MAX(snapshot_date) FROM stock_snapshots")).fetchone()
        except Exception:
            snap = (None,)
        try:
            po = pd.read_sql(text("""
                SELECT sku, SUM(qty_ordered-qty_received) as ongoing
                FROM purchase_orders WHERE status IN ('pending','partial') GROUP BY sku
            """), conn)
        except Exception:
            po = pd.DataFrame(columns=["sku", "ongoing"])

    if wdf.empty:
        return {"results": [], "stock_date": None, "has_stock": False}

    all_weeks = sorted(wdf["week_start"].unique())
    r4 = all_weeks[-4:] if len(all_weeks) >= 4 else all_weeks

    results = []
    for sku in wdf["sku"].unique():
        sd = wdf[wdf["sku"] == sku]
        name = sd.iloc[0]["product_name"]
        wmap = dict(zip(sd["week_start"], sd["qty"]))
        l4w = sum(int(wmap.get(w, 0)) for w in r4)
        avg = round(l4w / max(len(r4), 1), 1)

        sr = stk[stk["sku"] == sku]
        cs = int(sr.iloc[0]["current_stock"]) if len(sr) > 0 else 0
        pr = po[po["sku"] == sku]
        op = int(pr.iloc[0]["ongoing"]) if len(pr) > 0 else 0

        cov = round((cs + op) / avg, 1) if avg > 0 else None
        st = coverage_status(cov, cs)  # same 3-status rule as every other page

        results.append({"sku":sku,"product_name":name,"l4w":l4w,"avg_l4w":avg,
                        "current_stock":cs,"ongoing_po":op,"coverage":cov,
                        "status":st,"weeks_oos":None})

    results.sort(key=lambda x: x["coverage"] if x["coverage"] is not None else 9999)
    return {
        "results": results,
        "stock_date": str(snap[0]) if snap and snap[0] else None,
        "has_stock": len(stk) > 0, "total_skus": len(results),
    }


# ── Inventory ─────────────────────────────────────────────────

@app.get("/api/inventory")
async def inventory_analysis(sort: str = "priority", product_type: str = "import", scope: str = "all",
                             summary_only: bool = False):
    """Inventory health per SKU (coverage, status, days of inventory, value, overstock, movement)
    plus the forecast-based replenishment fields. product_type: import (default) | food | all."""
    engine = get_engine()
    inv = build_inventory(engine, product_type, scope)
    results = inv["rows"]
    status_order = {"understocked": 0, "healthy": 1, "overstocked": 2, None: 3}
    if sort == "priority":
        results.sort(key=lambda x: (status_order.get(x["status"], 9), -(x["l4w_revenue"])))
    elif sort == "revenue":
        results.sort(key=lambda x: -x["l4w_revenue"])
    elif sort == "coverage":
        results.sort(key=lambda x: x["coverage"] if x["coverage"] is not None else 9999)
    elif sort == "stockout":
        results.sort(key=lambda x: (x["stockout_week"] if x["stockout_week"] else 999, -(x["l4w_revenue"])))
    elif sort == "value":
        results.sort(key=lambda x: -(x["inventory_value"] or 0))
    out = {k: v for k, v in inv.items() if k != "rows"}
    if not summary_only:
        out["results"] = results
    return out


@app.get("/api/inventory/summary")
async def inventory_summary(product_type: str = "import", scope: str = "all"):
    """Overview: totals, status distribution, value, freshness, turnover availability."""
    engine = get_engine()
    inv = build_inventory(engine, product_type, scope)
    cfg = get_settings()
    try:
        with engine.connect() as conn:
            costs = mt.unit_costs(conn, cfg)
            turnover = mt.inventory_turnover(conn, costs, {r["sku"] for r in inv["rows"]})
    except Exception as e:
        turnover = {"available": False, "reason": f"error: {e}"}
    top_reorder = sorted([r for r in inv["rows"] if (r["reco_po"] or 0) > 0],
                         key=lambda r: (r["order_by"] or "9999-12-31", -(r["l4w_revenue"])))[:5]
    out = {k: v for k, v in inv.items() if k != "rows"}
    out["turnover"] = turnover
    out["top_reorder"] = [{k: r[k] for k in ("sku", "product_name", "urgency", "reco_po", "stockout_week",
                                             "est_purchase_cost", "needs_po_check", "order_by", "order_by_days",
                                             "order_by_is_estimate", "status")} for r in top_reorder]
    out["order_soon"] = sum(1 for r in inv["rows"] if r["order_by_days"] is not None
                            and not r["order_by_is_estimate"] and r["order_by_days"] <= 14)
    out["order_overdue"] = sum(1 for r in inv["rows"] if r["order_by_days"] is not None
                               and not r["order_by_is_estimate"] and r["order_by_days"] < 0)
    return out


# ── Forecast API ──────────────────────────────────────────────

@app.post("/api/forecast/run")
async def forecast_run():
    """On-demand forecast refresh. Also called automatically after ETL."""
    engine = get_engine()
    result = run_forecast(engine, forecast_periods=12)
    if "error" in result:
        raise HTTPException(400, result["error"])
    saved = save_forecast_to_db(engine, result)
    forecast_cache["latest"] = result
    forecast_cache.pop("light", None); forecast_cache.pop("light_calc_at", None)
    return {"status": "success", "summary": result["summary"], "saved_to_db": saved}


@app.get("/api/forecast/results")
async def forecast_results(limit: int = 200, sort: str = "volume", fields: str = "full"):
    """fields=light returns only what the dashboard tables need (~10x smaller payload)."""
    engine = get_engine()
    if fields == "light":
        results = get_forecast_light(engine)
        if not results:  # nothing saved yet -> compute once (slow path)
            results = get_forecast_light(engine) if get_latest_forecast(engine).get("results") else []
        data = {"summary": forecast_summary_fast(engine) or {}}
    else:
        data = get_latest_forecast(engine)
        results = data.get("results", [])
    if sort == "mape":
        results = sorted(results, key=lambda x: x["mape"])
    elif sort == "trend":
        results = sorted(results, key=lambda x: abs(x["trend"]), reverse=True)
    return {"summary": data.get("summary", {}), "results": results[:limit], "total": len(results)}


@app.get("/api/forecast/sku/{sku}")
async def forecast_sku(sku: str):
    engine = get_engine()
    match = None
    if "latest" in forecast_cache:
        match = next((r for r in forecast_cache["latest"].get("results", []) if r["sku"] == sku), None)
    else:
        # Read just this SKU's saved forecast instead of loading every SKU.
        try:
            one = load_forecast_from_db(engine, sku=sku)
            match = one["results"][0] if one else None
        except Exception as e:
            print(f"[forecast_sku] DB read failed: {e}")
        if match is None and not forecast_cache.get("light"):
            results = get_latest_forecast(engine).get("results", [])
            match = next((r for r in results if r["sku"] == sku), None)
    if not match:
        raise HTTPException(404, f"SKU {sku} tidak ditemukan")
    return match


# ── Replenishment API ─────────────────────────────────────────

@app.get("/api/replenishment")
async def replenishment(product_type: str = "import", only_reorder: bool = False, urgency: str = "all"):
    """Purchase recommendations for forecasted best sellers. The system recommends; purchasing decides
    and places orders. Primary qty = thesis formula; order-up-to is returned as a comparison."""
    engine = get_engine()
    inv = build_inventory(engine, product_type, scope="bestseller")
    rows = [r for r in inv["rows"] if r["forecasted"]]
    if only_reorder:
        rows = [r for r in rows if (r["reco_po"] or 0) > 0]
    if urgency in ("critical", "high", "medium", "low"):
        rows = [r for r in rows if r["urgency"] == urgency]
    rows.sort(key=lambda r: (r["order_by"] or "9999-12-31", rp.URGENCY_ORDER.get(r["urgency"], 9), -(r["l4w_revenue"])))
    s = inv["summary"]
    return {
        "results": rows,
        "summary": {"forecasted": s["forecasted"], "reorder_skus": s["reorder_skus"], "reorder_qty": s["reorder_qty"],
                    "reorder_qty_order_up_to": sum(r["reco_order_up_to"] or 0 for r in rows),
                    "est_purchase_cost": s["est_purchase_cost"], "cost_verified": s["cost_verified"],
                    "urgency": s["urgency"], "needs_po_check": s["needs_po_check"]},
        "freshness": inv["freshness"], "settings": inv["settings"], "stock_date": inv["stock_date"],
    }


@app.get("/api/forecast/plan")
async def forecast_plan(product_type: str = "import"):
    """Demand Plan page: per forecasted SKU, the weekly forecast, the 8 holdout weeks (plan from a model
    trained without them vs actual) and the model parameters behind every number. Months are built in the browser."""
    engine = get_engine()
    tf = _type_filter(product_type)
    with engine.connect() as conn:
        rows = conn.execute(text(f"""
            SELECT f.sku, COALESCE(p.name, f.sku), f.alpha, f.beta, f.mape, f.training_months,
                   f.forecast_data::jsonb -> 'forecast',
                   f.forecast_data::jsonb -> 'actual',
                   f.forecast_data::jsonb -> 'holdout',
                   f.forecast_data::jsonb ->> 'level', f.forecast_data::jsonb ->> 'trend',
                   f.calculated_at
            FROM forecast_results f LEFT JOIN products p ON p.sku = f.sku
            WHERE 1=1 {tf}
            ORDER BY f.id
        """)).fetchall()
        price = {r[0]: float(r[1]) for r in conn.execute(text("""
            WITH mx AS (SELECT MAX(invoice_date) d FROM sales)
            SELECT sku, SUM(amount) / NULLIF(SUM(quantity), 0) FROM sales, mx
            WHERE invoice_date > mx.d - 84 AND quantity > 0 GROUP BY sku
        """)).fetchall() if r[1] is not None}
    light = {r["sku"]: r for r in get_forecast_light(engine)}
    out, calc = [], None
    for sku, name, a, b, mape, tw, fc, act, ho, level, trend, calc_at in rows:
        fc = fc if isinstance(fc, dict) else json.loads(fc or "{}")
        act = act if isinstance(act, dict) else json.loads(act or "{}")
        ho = ho if (isinstance(ho, dict) or ho is None) else json.loads(ho)
        aw, aq = act.get("weeks", []), act.get("quantities", [])
        h = ho or {}
        hw = aw[-len(h["holdout_actual"]):] if h.get("holdout_actual") else []
        lf = light.get(sku, {})
        calc = calc_at or calc
        out.append({
            "sku": sku, "product_name": name,
            "forecast": {"weeks": fc.get("weeks", []), "quantities": fc.get("quantities", [])},
            "holdout": {"weeks": hw, "actual": h.get("holdout_actual"), "forecast": h.get("holdout_forecast"),
                        "wmape": h.get("wmape"), "alpha": h.get("alpha"), "beta": h.get("beta"),
                        "train_weeks": h.get("train_weeks"), "reason": h.get("reason") if ho else "not_computed"},
            "model": {"alpha": float(a) if a is not None else None, "beta": float(b) if b is not None else None,
                      "mape": float(mape) if mape is not None else None, "training_weeks": tw,
                      "level": float(level) if level is not None else None,
                      "trend": float(trend) if trend is not None else None,
                      "history_start": aw[0] if aw else None, "history_end": aw[-1] if aw else None},
            "trend_pct": lf.get("trend_pct"), "trend_class": lf.get("trend_class"),
            "avg_price": round(price[sku]) if sku in price else None,
            "l4w_avg": round(sum(aq[-4:]) / max(len(aq[-4:]), 1), 1) if aq else None,
        })
    return {"results": out, "calculated_at": calc.isoformat() if hasattr(calc, "isoformat") else calc,
            "holdout_weeks": 8, "horizon_weeks": 12}


@app.get("/api/replenishment/sku/{sku}")
async def replenishment_sku(sku: str):
    """Week-by-week projection for one SKU + PO lines + safety-stock comparisons (simulation only)."""
    engine = get_engine()
    cfg = get_settings()
    fc = next((r for r in get_forecast_light(engine) if r["sku"] == sku), None)
    if not fc:
        raise HTTPException(404, f"SKU {sku} has no forecast (not a forecasted best seller)")
    with engine.connect() as conn:
        snap = conn.execute(text("SELECT MAX(snapshot_date) FROM stock_snapshots")).scalar()
        cs = conn.execute(text("SELECT COALESCE(SUM(quantity),0) FROM stock_snapshots WHERE sku=:s AND snapshot_date=:d"),
                          {"s": sku, "d": snap}).scalar() if snap else 0
        lines = _fetch_po_lines(conn, sku).get(sku, [])
        cost = mt.unit_costs(conn, cfg, [sku]).get(sku)
        last_sale = conn.execute(text("SELECT MAX(invoice_date) FROM sales")).scalar()
    fq, fw = fc["forecast_data"]["quantities"], fc["forecast_data"].get("weeks", [])
    plan = rp.plan_sku(int(cs or 0), lines, fq, fw, cfg)
    scen = []
    for pct in (0.05, 0.10, 0.15):
        p2 = rp.plan_sku(int(cs or 0), lines, fq, fw, {**cfg, "ss_pct": pct})
        scen.append({"ss_pct": pct, "safety_stock": p2["safety_stock"], "reco_qty": p2["reco_qty"],
                     "stockout_week": p2["stockout_week"], "is_default": abs(pct - cfg["ss_pct"]) < 1e-9})
    hist = None
    try:
        one = load_forecast_from_db(engine, sku=sku)
        hist = one["results"][0]["actual_data"]["quantities"] if one else None
    except Exception:
        pass
    stat_ss = rp.statistical_safety_stock(hist, plan["lead_time_weeks"])
    return {"sku": sku, "product_name": fc["product_name"], "current_stock": int(cs or 0),
            "stock_date": str(snap) if snap else None, "unit_cost": cost,
            "forecast": {"weeks": fw, "quantities": fq, "fc_avg_8w": fc["fc_avg_8w"],
                         "trend_pct": fc["trend_pct"], "trend_class": fc["trend_class"]},
            "plan": plan,
            "safety_stock_comparison": {
                "policy": {"method": f"{round(cfg['ss_pct']*100)}% x forecast {cfg['ss_basis_weeks']} weeks",
                           "value": plan["safety_stock"], "used_in_recommendation": True},
                "statistical": {"method": "z x sigma_d x sqrt(L), z=1.65 (~95% service level), sigma_d = std dev of weekly demand, last 26 weeks",
                                "value": stat_ss, "used_in_recommendation": False,
                                "note": None if stat_ss is not None else "needs at least 8 weeks of history"},
                "sensitivity": scen},
            "freshness": mt.freshness(snap, last_sale, cfg)}


@app.get("/api/product/{sku}/history")
async def product_history(sku: str, weeks: int = 52):
    """Product Analysis drill-down: weekly sales, stock snapshots and PO lines for one SKU."""
    engine = get_engine()
    with engine.connect() as conn:
        name = conn.execute(text("SELECT name FROM products WHERE sku=:s"), {"s": sku}).scalar()
        if name is None:
            raise HTTPException(404, f"SKU {sku} not found")
        mx = conn.execute(text("SELECT MAX(invoice_date) FROM sales")).scalar()
        sales = conn.execute(text("""
            SELECT DATE_TRUNC('week', invoice_date)::date w, SUM(quantity), SUM(amount)
            FROM sales WHERE sku=:s AND invoice_date > :a GROUP BY w ORDER BY w
        """), {"s": sku, "a": (mx - timedelta(weeks=weeks)) if mx else date(1900, 1, 1)}).fetchall()
        snaps = conn.execute(text("""
            SELECT snapshot_date, SUM(quantity) FROM stock_snapshots WHERE sku=:s GROUP BY snapshot_date ORDER BY snapshot_date
        """), {"s": sku}).fetchall()
        pos = conn.execute(text("""
            SELECT id, qty_ordered, COALESCE(qty_received,0), status, order_date, expected_date, received_date, supplier
            FROM purchase_orders WHERE sku=:s ORDER BY COALESCE(order_date, created_at::date) DESC, id DESC
        """), {"s": sku}).fetchall()
    return {"sku": sku, "product_name": name,
            "weekly_sales": [{"week": str(w), "qty": int(q or 0), "revenue": float(a or 0)} for w, q, a in sales],
            "stock_snapshots": [{"date": str(d), "qty": int(q)} for d, q in snaps],
            "stock_movement_available": len(snaps) >= 2,
            "purchase_orders": [{"id": r[0], "qty_ordered": r[1], "qty_received": r[2], "status": r[3],
                                 "order_date": str(r[4]) if r[4] else None, "expected_date": str(r[5]) if r[5] else None,
                                 "received_date": str(r[6]) if r[6] else None, "supplier": r[7]} for r in pos]}


@app.get("/api/settings")
async def get_planning_settings():
    """Planning parameters in use (read-only here; set via environment variables for now)."""
    return get_settings()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)