"""
PO Agent — Web Server v3
FastAPI backend: ETL, Weekly Analysis, Coverage, Forecasting, Replenishment.
Forecast runs automatically after ETL (no manual trigger).
"""

from dotenv import load_dotenv
load_dotenv()

import os, io, uuid, csv, json, math, traceback
from datetime import datetime, date, timedelta

from fastapi import FastAPI, UploadFile, File, HTTPException, Query
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware

import pandas as pd
import numpy as np
from sqlalchemy import create_engine, text
import psycopg2

from etl import clean_sales_data, load_master_sku, load_product_lookup_from_db
from forecast import run_forecast, save_forecast_to_db, calculate_replenishment
from auth import router as auth_router, require_login

DB_URL = os.getenv("SUPABASE_DB_URL")
UPLOAD_DIR = os.path.join("/tmp", "uploads")
MASTER_SKU_PATH = "MASTER_SKU.xlsx"
os.makedirs(UPLOAD_DIR, exist_ok=True)

app = FastAPI(title="PO Agent", version="3.0")
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
    if not DB_URL:
        raise HTTPException(500, "SUPABASE_DB_URL not configured")
    return psycopg2.connect(DB_URL)


def _run_auto_forecast(engine):
    """Internal: run forecast + save to DB + cache. Called after ETL and on-demand."""
    try:
        result = run_forecast(engine, forecast_periods=12)
        if "error" not in result:
            saved = save_forecast_to_db(engine, result)
            forecast_cache["latest"] = result
            forecast_cache.pop("light", None)
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
        })
        if calc_at:
            calc_times.append(calc_at)

    mapes = [r["mape"] for r in results if r["mape"] < 999]
    summary = {
        "total_skus_forecasted": len(results),
        "avg_mape": round(sum(mapes) / len(mapes), 2) if mapes else 0,
        "good_forecast_count": len([m for m in mapes if m < 25]),
        "moderate_forecast_count": len([m for m in mapes if 25 <= m < 50]),
        "poor_forecast_count": len([m for m in mapes if m >= 50]),
        "forecast_periods": len(results[0]["forecast_data"].get("quantities", [])),
        "calculated_at": (lambda c: c.isoformat() if hasattr(c, "isoformat") else str(c))(max(calc_times)) if calc_times else None,
        "source": "database",
    }
    return {"summary": summary, "results": results}


def get_forecast_light(engine):
    """Only the forecast fields the dashboard tables need (no actual/fitted history).
    ~10x smaller than the full forecast, so it is cheap to load on a fresh Vercel instance."""
    def slim(r):
        q = r["forecast_data"].get("quantities", [])
        return {
            # same rounding as the Inventory "Forecast/Wk" column (avg of next 8 forecast weeks)
            "fc_avg_8w": round(sum(q[:8]) / min(len(q), 8), 1) if q else 0,
            "sku": r["sku"], "product_name": r.get("product_name", r["sku"]),
            "mape": r["mape"], "trend": r.get("trend", 0),
            "trend_direction": r.get("trend_direction", "stable"),
            "avg_weekly_demand": r.get("avg_weekly_demand", 0),
            "forecast_data": {"weeks": r["forecast_data"].get("weeks", []),
                              "quantities": r["forecast_data"].get("quantities", [])},
        }
    if "latest" in forecast_cache:
        return [slim(r) for r in forecast_cache["latest"].get("results", [])]
    if "light" in forecast_cache:
        return forecast_cache["light"]
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT f.sku, COALESCE(p.name, f.sku), f.mape,
                   f.forecast_data::jsonb -> 'forecast',
                   f.forecast_data::jsonb ->> 'trend',
                   f.forecast_data::jsonb ->> 'trend_direction',
                   f.forecast_data::jsonb ->> 'avg_weekly_demand'
            FROM forecast_results f LEFT JOIN products p ON p.sku = f.sku
            ORDER BY f.id
        """)).fetchall()
    light = []
    for sku, name, mape, fc, trend, tdir, avg in rows:
        fc = fc if isinstance(fc, dict) else json.loads(fc or "{}")
        light.append(slim({
            "sku": sku, "product_name": name,
            "mape": float(mape) if mape is not None else 999,
            "trend": float(trend) if trend is not None else 0,
            "trend_direction": tdir or "stable",
            "avg_weekly_demand": float(avg) if avg is not None else 0,
            "forecast_data": fc or {"weeks": [], "quantities": []},
        }))
    if light:
        forecast_cache["light"] = light
    return light


def forecast_summary_fast(engine, product_type="all"):
    """The 4 headline forecast numbers for the Overview, computed in SQL so the
    full forecast table never has to be downloaded just for them."""
    tf = f"WHERE COALESCE(p.product_type, 'import') = '{product_type}'" if product_type in ("import", "food") else ""
    fc = forecast_cache.get("latest")
    if fc and fc.get("results") and not tf:
        res = fc["results"]
        return {
            "total_forecasted": len(res),
            "avg_mape": fc["summary"].get("avg_mape", 0),
            "forecast_demand_8w": round(sum(sum(r["forecast_data"]["quantities"][:8]) for r in res)),
            "trending_up": len([r for r in res if r["trend_direction"] == "up"]),
            "trending_down": len([r for r in res if r["trend_direction"] == "down"]),
            "calculated_at": fc["summary"].get("calculated_at"),
        }
    with engine.connect() as conn:
        row = conn.execute(text(f"""
            SELECT COUNT(*),
                   ROUND(AVG(f.mape) FILTER (WHERE f.mape < 999), 2),
                   COALESCE(SUM((
                       SELECT SUM(q::numeric)
                       FROM jsonb_array_elements_text(f.forecast_data::jsonb -> 'forecast' -> 'quantities')
                            WITH ORDINALITY AS t(q, i)
                       WHERE i <= 8
                   )), 0),
                   COUNT(*) FILTER (WHERE f.forecast_data::jsonb ->> 'trend_direction' = 'up'),
                   COUNT(*) FILTER (WHERE f.forecast_data::jsonb ->> 'trend_direction' = 'down'),
                   MAX(f.calculated_at)
            FROM forecast_results f
            LEFT JOIN products p ON p.sku = f.sku
            {tf}
        """)).fetchone()
    if not row or not row[0]:
        return None
    return {
        "total_forecasted": int(row[0]),
        "avg_mape": float(row[1]) if row[1] is not None else 0,
        "forecast_demand_8w": round(float(row[2])),
        "trending_up": int(row[3]),
        "trending_down": int(row[4]),
        "calculated_at": row[5].isoformat() if row[5] else None,
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


# ── Page Routes ───────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def dashboard_page():
    with open("templates/dashboard.html", "r", encoding="utf-8") as f:
        return f.read()

## ETL is now a tab inside dashboard.html — no separate page needed


# ── ETL API ───────────────────────────────────────────────────

@app.post("/api/master-sku/upload")
async def upload_master_sku(file: UploadFile = File(...)):
    global master_sku
    if not file.filename.endswith((".xls", ".xlsx")):
        raise HTTPException(400, "Format file harus .xls atau .xlsx")

    content = await file.read()
    with open(MASTER_SKU_PATH, "wb") as f:
        f.write(content)

    try:
        # Keep the existing description -> SKU mapping used by the sales ETL.
        master_sku = load_master_sku(MASTER_SKU_PATH)

        # IMPORTANT: stock_snapshots.sku has a foreign key to products.sku.
        # Therefore every SKU from the uploaded Master SKU must also exist in
        # products, even if that SKU has never appeared in sales yet.
        master_df = pd.read_excel(MASTER_SKU_PATH, engine="openpyxl")
        master_df.columns = [str(c).strip() for c in master_df.columns]
        sku_col = next((c for c in master_df.columns if "sku" in c.lower()), None)
        name_col = next((c for c in master_df.columns
                         if "desc" in c.lower() or "name" in c.lower() or "product" in c.lower()), None)

        if not sku_col or not name_col:
            raise ValueError("Kolom SKU dan Description/Name/Product tidak ditemukan di Master SKU")

        rows = []
        for _, row in master_df.iterrows():
            if pd.isna(row[sku_col]):
                continue
            sku = str(row[sku_col]).strip()
            if not sku or sku.lower() == "nan":
                continue
            name = "" if pd.isna(row[name_col]) else str(row[name_col]).strip()
            rows.append((sku, name or sku))

        # Deduplicate by SKU before upsert.
        products_by_sku = {}
        for sku, name in rows:
            products_by_sku[sku] = name

        conn = get_raw_conn()
        cur = conn.cursor()
        try:
            for sku, name in products_by_sku.items():
                cur.execute(
                    """
                    INSERT INTO products (sku, name)
                    VALUES (%s, %s)
                    ON CONFLICT (sku) DO UPDATE SET name = EXCLUDED.name
                    """,
                    (sku, name),
                )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            cur.close()
            conn.close()

        return {
            "status": "success",
            "entries": len(master_sku),
            "products_synced": len(products_by_sku),
            "message": f"Master SKU berhasil di-upload dan {len(products_by_sku)} SKU disinkronkan ke database.",
        }
    except Exception as e:
        raise HTTPException(500, f"Gagal memproses Master SKU: {e}")


# ── Stock status (one definition for the whole dashboard) ──────
# Coverage = (stock + open PO) / average weekly sales of the last 4 weeks.
UNDERSTOCK_WEEKS = 8    # below this: understocked (matches the 8-week lead time)
OVERSTOCK_WEEKS = 20    # above this: overstocked


def coverage_status(cov):
    if cov is None:
        return "no_sales"
    if cov < UNDERSTOCK_WEEKS:
        return "understocked"
    if cov <= OVERSTOCK_WEEKS:
        return "healthy"
    return "overstocked"


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
    Replaces the stored list and re-tags every product as food or import.
    Sales data is not touched (any rows archived by an earlier version are restored)."""
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

        # Undo the earlier "exclude food" behaviour: move archived food sales back (skip exact duplicates).
        restored = 0
        if conn.execute(text("SELECT to_regclass('public.sales_excluded')")).scalar():
            restored = conn.execute(text(f"""
                INSERT INTO sales ({SALES_COLS})
                SELECT {SALES_COLS} FROM sales_excluded x
                WHERE x.reason = 'food' AND NOT EXISTS (
                    SELECT 1 FROM sales e WHERE e.sku = x.sku AND e.invoice_date = x.invoice_date
                      AND e.quantity = x.quantity AND e.amount = x.amount)
            """)).rowcount
            conn.execute(text("DELETE FROM sales_excluded WHERE reason = 'food'"))

    forecast_cache.clear()
    return {
        "status": "success",
        "food_skus_in_file": len(entries),
        "products_marked_food": len(food),
        "products_marked_import": len(products) - len(food),
        "sales_rows_restored": restored,
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
        with get_engine().connect() as c:
            food_codes, food_names = load_food_rules(c)
        has_food_rules = bool(food_codes or food_names)
        sales = df.copy()
        sales = sales.rename(columns={
            "SKU": "sku", "date": "invoice_date", "quantity": "quantity",
            "amount": "amount", "COGS": "cogs_accurate", "GPAO": "gpao_accurate",
            "GPAO (%)": "gpao_pct_acc", "week": "week_label", "month": "month_label",
            "quarter": "quarter_label", "year": "year_val",
        })
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
            for _, row in products.iterrows():
                ptype = None
                if has_food_rules:
                    ptype = "food" if is_food(row["SKU"], row["product_name"], food_codes, food_names) else "import"
                    food_tagged += ptype == "food"
                cur.execute("""
                    INSERT INTO products (sku, name, product_type) VALUES (%s, %s, %s)
                    ON CONFLICT (sku) DO UPDATE SET name = EXCLUDED.name,
                        product_type = COALESCE(EXCLUDED.product_type, products.product_type),
                        updated_at = NOW()
                """, (str(row["SKU"]), row["product_name"], ptype))
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
            skipped = len(sales) - inserted
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
                       + (f" · {food_tagged:,} produk ditandai food" if food_tagged else ""),
            "food_products_tagged": food_tagged,
            "database": {"products": row[0], "sales": row[1],
                         "date_range": f"{row[2]} s/d {row[3]}", "unique_skus": row[4]},
            "forecast_status": "auto-run complete" if "latest" in forecast_cache else "skipped",
        }
    except Exception as e:
        raise HTTPException(500, f"Gagal memuat data: {str(e)}")


# ── Stock Upload ──────────────────────────────────────────────

@app.post("/api/stock/upload")
async def stock_upload(file: UploadFile = File(...)):
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
        today = date.today()
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
                for msku in missing_skus:
                    pname = sku_name_map.get(msku, msku)
                    cur.execute(
                        "INSERT INTO products (sku, name) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                        (msku, pname)
                    )

            cur.execute("DELETE FROM stock_snapshots WHERE snapshot_date = %s", (today,))
            for _, row in df.iterrows():
                cur.execute("INSERT INTO stock_snapshots (sku, quantity, snapshot_date) VALUES (%s,%s,%s) ON CONFLICT DO NOTHING",
                            (row["sku"], row["quantity"], today))
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

        # Flexible column mapping
        col_map = {}
        for c in df.columns:
            cl = c.lower()
            if cl in ("sku", "kode", "item code", "item_code", "kode item"):
                col_map[c] = "sku"
            elif any(k in cl for k in ("product", "nama", "name", "item", "description")):
                col_map[c] = "product_name"
            elif any(k in cl for k in ("qty ordered", "qty_ordered", "quantity", "total qty", "total_qty", "qty", "jumlah", "order")):
                col_map[c] = "qty_ordered"
            elif any(k in cl for k in ("supplier", "vendor")):
                col_map[c] = "supplier"
            elif any(k in cl for k in ("order date", "order_date", "tanggal")):
                col_map[c] = "order_date"
            elif any(k in cl for k in ("expected", "eta", "estimasi")):
                col_map[c] = "expected_date"
        df = df.rename(columns=col_map)

        if "qty_ordered" not in df.columns:
            raise HTTPException(400, "Kolom qty/quantity tidak ditemukan")

        df["qty_ordered"] = pd.to_numeric(df["qty_ordered"], errors="coerce").fillna(0).astype(int)
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
            for np in new_products:
                cur.execute("""
                    INSERT INTO products (sku, name) VALUES (%s, %s)
                    ON CONFLICT (sku) DO NOTHING
                """, (np["sku"], np["name"]))
            # Cancel all existing pending POs (fresh upload = fresh state)
            cur.execute("UPDATE purchase_orders SET status = 'cancelled', updated_at = NOW() WHERE status IN ('pending', 'partial')")
            for po in resolved:
                cur.execute("""
                    INSERT INTO purchase_orders (sku, qty_ordered, supplier, order_date, expected_date, status)
                    VALUES (%s, %s, %s, %s, %s, 'pending')
                """, (
                    po["sku"], po["qty_ordered"], po["supplier"],
                    po["order_date"].date() if pd.notna(po["order_date"]) else None,
                    po["expected_date"].date() if pd.notna(po["expected_date"]) else None,
                ))
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
        }
    except HTTPException:
        raise
    except Exception as e:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise HTTPException(500, f"Gagal parse PO: {e}")


@app.get("/api/po/list")
async def po_list(status: str = "pending"):
    """List ongoing POs. status = pending | partial | received | cancelled | all"""
    engine = get_engine()
    with engine.connect() as conn:
        if status == "all":
            rows = conn.execute(text("""
                SELECT po.id, po.sku, p.name, po.qty_ordered, po.qty_received,
                       po.supplier, po.status, po.order_date, po.expected_date
                FROM purchase_orders po
                LEFT JOIN products p ON p.sku = po.sku
                ORDER BY po.created_at DESC
            """)).fetchall()
        else:
            rows = conn.execute(text("""
                SELECT po.id, po.sku, p.name, po.qty_ordered, po.qty_received,
                       po.supplier, po.status, po.order_date, po.expected_date
                FROM purchase_orders po
                LEFT JOIN products p ON p.sku = po.sku
                WHERE po.status = :status
                ORDER BY po.created_at DESC
            """), {"status": status}).fetchall()

    return {
        "count": len(rows),
        "data": [
            {
                "id": r[0], "sku": r[1], "product": r[2],
                "qty_ordered": r[3], "qty_received": r[4],
                "supplier": r[5], "status": r[6],
                "order_date": str(r[7]) if r[7] else None,
                "expected_date": str(r[8]) if r[8] else None,
            }
            for r in rows
        ],
    }


@app.delete("/api/po/{po_id}")
async def po_delete(po_id: int):
    engine = get_engine()
    with engine.connect() as conn:
        conn.execute(text("DELETE FROM purchase_orders WHERE id = :id"), {"id": po_id})
        conn.commit()
    return {"status": "deleted", "id": po_id}


@app.patch("/api/po/{po_id}")
async def po_update(po_id: int, body: dict = None):
    """Update PO status or qty_received."""
    if not body:
        raise HTTPException(400, "Body kosong")
    sets = []
    params = {"id": po_id}
    if "status" in body:
        sets.append("status = :status")
        params["status"] = body["status"]
    if "qty_received" in body:
        sets.append("qty_received = :qty_received")
        params["qty_received"] = int(body["qty_received"])
    if not sets:
        raise HTTPException(400, "Tidak ada field yang di-update")
    sets.append("updated_at = NOW()")
    engine = get_engine()
    with engine.connect() as conn:
        conn.execute(text(f"UPDATE purchase_orders SET {', '.join(sets)} WHERE id = :id"), params)
        conn.commit()
    return {"status": "updated", "id": po_id}


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
    """Understocked best sellers (Pareto set) with the stock trend vs ~8 weeks ago.
    The dashboard picks the top 10 by urgency or by revenue."""
    engine = get_engine()
    type_filter = ""
    if product_type in ("import", "food"):
        type_filter = f"AND COALESCE(p.product_type, 'import') = '{product_type}'"
    with engine.connect() as conn:
        # Same "last 4 weeks" window as /api/inventory (last 4 sales weeks), so coverage
        # and status are identical for a SKU on both pages.
        r4 = sorted(r[0] for r in conn.execute(text(f"""
            SELECT DISTINCT DATE_TRUNC('week', s.invoice_date)::date AS w
            FROM sales s JOIN products p ON s.sku = p.sku
            WHERE s.invoice_date IS NOT NULL {type_filter}
            ORDER BY w DESC LIMIT 4
        """)).fetchall())
        if not r4:
            return {"results": []}
        n_weeks = len(r4)

        sales = conn.execute(text(f"""
            SELECT s.sku, p.name, SUM(s.quantity) qty, SUM(s.amount) rev
            FROM sales s JOIN products p ON s.sku = p.sku
            WHERE s.invoice_date >= :s {type_filter} GROUP BY s.sku, p.name
        """), {"s": r4[0]}).fetchall()

        # Current stock = latest snapshot. Previous = latest snapshot at least 7 weeks older
        # (target ~8 weeks); if none that old exists, the oldest snapshot available.
        stk, prev_stk, snap_now, snap_prev = {}, {}, None, None
        try:
            dates = [r[0] for r in conn.execute(text(
                "SELECT DISTINCT snapshot_date FROM stock_snapshots ORDER BY snapshot_date DESC")).fetchall()]
            if dates:
                snap_now = dates[0]
                older = [d for d in dates[1:] if (snap_now - d).days >= 49]
                snap_prev = older[0] if older else (dates[-1] if len(dates) > 1 else None)
                stk = {r[0]: int(r[1]) for r in conn.execute(text(
                    "SELECT sku, quantity FROM stock_snapshots WHERE snapshot_date = :d"), {"d": snap_now}).fetchall()}
                if snap_prev:
                    prev_stk = {r[0]: int(r[1]) for r in conn.execute(text(
                        "SELECT sku, quantity FROM stock_snapshots WHERE snapshot_date = :d"), {"d": snap_prev}).fetchall()}
        except Exception:
            pass
        try:
            po = {r[0]: int(r[1]) for r in conn.execute(text("""
                SELECT sku, SUM(qty_ordered-qty_received) FROM purchase_orders
                WHERE status IN ('pending','partial') GROUP BY sku
            """)).fetchall()}
        except Exception:
            po = {}

    # Best sellers only (Pareto 80% set = SKUs with a forecast); all SKUs if no forecast exists yet.
    try:
        best = {r["sku"] for r in get_forecast_light(engine)}
    except Exception:
        best = set()

    weeks_between = round((snap_now - snap_prev).days / 7, 1) if snap_now and snap_prev else None
    results = []
    for r in sales:
        sku, name, qty, rev = r[0], r[1], int(r[2]), float(r[3])
        if qty <= 0 or (best and sku not in best):
            continue
        avg_w = round(qty / n_weeks, 1)
        cs = stk.get(sku, 0)
        op = po.get(sku, 0)
        cov = round((cs + op) / avg_w, 1) if avg_w > 0 else None
        st = coverage_status(cov)
        if st != "understocked":
            continue
        ps = prev_stk.get(sku) if snap_prev else None
        change = round((cs - ps) / ps * 100, 1) if ps else None
        results.append({"sku": sku, "name": name, "l4w_qty": qty, "revenue": rev,
                        "avg_weekly": avg_w, "current_stock": cs, "ongoing_po": op,
                        "coverage": cov, "status": st,
                        "prev_stock": ps, "stock_change_pct": change})

    results.sort(key=lambda x: x["revenue"], reverse=True)
    return {
        "results": results[:limit],
        "total_understocked": len(results),
        "stock_now_date": str(snap_now) if snap_now else None,
        "stock_prev_date": str(snap_prev) if snap_prev else None,
        "weeks_between": weeks_between,
        "best_sellers_only": bool(best),
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
        st = "understocked" if cov is not None and cov < 8 else ("healthy" if cov is not None and cov <= 20 else ("overstocked" if cov is not None else "no_sales"))

        results.append({"sku":sku,"product_name":name,"l4w":l4w,"avg_l4w":avg,
                        "current_stock":cs,"ongoing_po":op,"coverage":cov,
                        "status":st,"weeks_oos":None})

    results.sort(key=lambda x: x["coverage"] if x["coverage"] is not None else 9999)
    return {
        "results": results,
        "stock_date": str(snap[0]) if snap and snap[0] else None,
        "has_stock": len(stk) > 0, "total_skus": len(results),
    }


# ── Inventory (merged Coverage + Replenishment) ──────────────

@app.get("/api/inventory")
async def inventory_analysis(sort: str = "priority", product_type: str = "import", scope: str = "all"):
    """
    Merged Coverage + Replenishment in one endpoint.
    Coverage = Avg L4W basis. Replenishment = Holt's DES basis.
    product_type: 'import' (default), 'food', or 'all'
    """
    engine = get_engine()

    type_filter = ""
    if product_type in ("import", "food"):
        type_filter = f"AND COALESCE(p.product_type, 'import') = '{product_type}'"

    with engine.connect() as conn:
        # Only the last 4 sales weeks are used below, so only those are fetched week-by-week.
        r4 = sorted(r[0] for r in conn.execute(text(f"""
            SELECT DISTINCT DATE_TRUNC('week', s.invoice_date)::date AS w
            FROM sales s JOIN products p ON s.sku = p.sku
            WHERE s.invoice_date IS NOT NULL {type_filter}
            ORDER BY w DESC LIMIT 4
        """)).fetchall())
        sku_rows = conn.execute(text(f"""
            SELECT s.sku, MIN(p.name)
            FROM sales s JOIN products p ON s.sku = p.sku
            WHERE s.invoice_date IS NOT NULL {type_filter}
            GROUP BY s.sku ORDER BY s.sku
        """)).fetchall() if r4 else []
        recent = conn.execute(text(f"""
            SELECT s.sku, DATE_TRUNC('week', s.invoice_date)::date AS w, SUM(s.quantity), SUM(s.amount)
            FROM sales s JOIN products p ON s.sku = p.sku
            WHERE s.invoice_date >= :w4 {type_filter}
            GROUP BY s.sku, w
        """), {"w4": r4[0]}).fetchall() if r4 else []

        # Stock
        try:
            stk = {r[0]: int(r[1]) for r in conn.execute(text("""
                SELECT sku, quantity FROM stock_snapshots
                WHERE snapshot_date = (SELECT MAX(snapshot_date) FROM stock_snapshots)
            """)).fetchall()}
        except Exception:
            stk = {}
        try:
            snap_date = conn.execute(text("SELECT MAX(snapshot_date) FROM stock_snapshots")).fetchone()
            snap_date = str(snap_date[0]) if snap_date and snap_date[0] else None
        except Exception:
            snap_date = None

        # Ongoing PO
        try:
            po_map = {r[0]: int(r[1]) for r in conn.execute(text("""
                SELECT sku, SUM(qty_ordered-qty_received) FROM purchase_orders
                WHERE status IN ('pending','partial') GROUP BY sku
            """)).fetchall()}
        except Exception:
            po_map = {}

    if not sku_rows:
        return {"results": [], "summary": {}, "stock_date": None}

    qty_by, rev_by = {}, {}
    for sku, w, q, a in recent:
        qty_by.setdefault(sku, {})[w] = q
        rev_by.setdefault(sku, {})[w] = a

    # Forecast quantities only (light version: no actual/fitted history)
    try:
        fc_map = {r["sku"]: r for r in get_forecast_light(engine)}
    except Exception:
        fc_map = {}
    if not fc_map:
        try:
            fc_data = get_latest_forecast(engine)
            fc_map = {r["sku"]: r for r in (fc_data or {}).get("results", [])}
        except Exception:
            fc_map = {}

    if scope == "bestseller":  # Pareto 80% set = SKUs that have a forecast
        sku_rows = [r for r in sku_rows if r[0] in fc_map]

    results = []
    for sku, name in sku_rows:
        wmap = qty_by.get(sku, {})
        rmap = rev_by.get(sku, {})

        # L4W metrics
        l4w_qty = sum(int(wmap.get(w, 0)) for w in r4)
        l4w_rev = sum(float(rmap.get(w, 0)) for w in r4)
        avg_l4w = round(l4w_qty / max(len(r4), 1), 1)

        # Stock
        cs = stk.get(sku, 0)
        op = po_map.get(sku, 0)
        available = cs + op

        # Coverage (L4W-based)
        coverage = round(available / avg_l4w, 1) if avg_l4w > 0 else None

        # Forecast/replenishment (DES-based)
        fc = fc_map.get(sku)
        fc_avg = 0; lt6 = 0; lt8 = 0; ss = 0; reco = 0; stockout = None; mape = None; trend_dir = "stable"
        if fc:
            fcq = fc["forecast_data"]["quantities"]
            fc_avg = round(sum(fcq[:8]) / min(len(fcq), 8), 1) if fcq else 0
            lt6 = sum(fcq[:6]) if len(fcq) >= 6 else sum(fcq)
            lt8 = sum(fcq[:8]) if len(fcq) >= 8 else sum(fcq)
            ss = round(lt8 * 0.10, 1)
            reco = max(0, round(lt8 + ss - available))
            mape = fc["mape"]
            trend_dir = fc["trend_direction"]
            cum = 0
            for wi, wf in enumerate(fcq):
                cum += wf
                if cum >= available:
                    stockout = wi + 1
                    break

        # Status: coverage-based, same rule as Best Sellers at Risk
        status = coverage_status(coverage)

        results.append({
            "sku": sku, "product_name": name,
            "l4w_qty": l4w_qty, "l4w_revenue": round(l4w_rev),
            "avg_l4w": avg_l4w,
            "current_stock": cs, "ongoing_po": op, "available": available,
            "coverage": coverage,
            "fc_avg_weekly": fc_avg,
            "lt_demand_8w": round(lt8, 1), "safety_stock": ss,
            "reco_po": reco, "stockout_week": stockout,
            "mape": mape, "trend_direction": trend_dir,
            "status": status,
        })

    # Sort
    status_order = {"understocked": 0, "healthy": 1, "overstocked": 2, "no_sales": 3}
    if sort == "priority":
        results.sort(key=lambda x: (status_order.get(x["status"], 9), -(x["l4w_revenue"])))
    elif sort == "revenue":
        results.sort(key=lambda x: -x["l4w_revenue"])
    elif sort == "coverage":
        results.sort(key=lambda x: x["coverage"] if x["coverage"] is not None else 9999)
    elif sort == "stockout":
        results.sort(key=lambda x: (x["stockout_week"] if x["stockout_week"] else 999, -(x["l4w_revenue"])))

    sm = {
        "total": len(results),
        "understocked": len([r for r in results if r["status"] == "understocked"]),
        "healthy": len([r for r in results if r["status"] == "healthy"]),
        "overstocked": len([r for r in results if r["status"] == "overstocked"]),
        "no_sales": len([r for r in results if r["status"] == "no_sales"]),
    }

    return {"results": results, "summary": sm, "stock_date": snap_date, "has_stock": len(stk) > 0}


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
    forecast_cache.pop("light", None)
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
async def replenishment():
    """
    Replenishment recommendations based on Holt's DES forecast.
    Lead time: 6w (optimistic) / 8w (conservative). Safety stock: 10%.
    """
    engine = get_engine()

    # Ensure forecast is available
    data = get_latest_forecast(engine)
    if "error" in data:
        return {"results": [], "summary": {"error": data["error"]}}

    result = calculate_replenishment(engine, data)
    return result


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)