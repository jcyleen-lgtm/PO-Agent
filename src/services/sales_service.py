"""Data imports: Accurate sales ETL (preview + confirm) and stock snapshot upload."""

from datetime import date, datetime
import os
import uuid

from fastapi import HTTPException
import pandas as pd

from etl import clean_sales_data, load_product_lookup_from_db
from src.core.config import UPLOAD_DIR
from src.core.logging import get_logger
from src.core.database import get_engine
from src.repositories import inventory as stock_repo, sales as sales_repo, sales_reports as reports
from src.core.ddl import ensure_sales_excluded
from src.core.state import forecast_cache, master_sku, processed_cache
from src.parsers.sku_lists import is_food
from src.repositories.products import load_food_rules
from src.services.forecast_service import _run_auto_forecast


log = get_logger("etl")


async def etl_upload(filename: str, content: bytes):
    if not filename.endswith((".xls", ".xlsx")):
        raise HTTPException(400, "Format file harus .xls atau .xlsx")
    file_id = str(uuid.uuid4())[:8]
    ext = os.path.splitext(filename)[1]
    save_path = os.path.join(UPLOAD_DIR, f"{file_id}{ext}")
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
        "filename": filename, "created_at": datetime.now().isoformat(),
    }
    preview_rows = df.head(20).copy()
    for col in preview_rows.columns:
        if preview_rows[col].dtype == "datetime64[ns]":
            preview_rows[col] = preview_rows[col].dt.strftime("%Y-%m-%d")
    preview_rows = preview_rows.fillna("").to_dict("records")
    return {
        "file_id": file_id, "filename": filename,
        "stats": result["stats"], "preview": preview_rows,
        "columns": list(df.columns),
    }


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
            log.info(f"duplicate columns dropped: {dup}")
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
        food_tagged = 0
        prod_rows = []
        for sku_, pname in zip(products["SKU"], products["product_name"]):
            ptype = None
            if has_food_rules:
                ptype = "food" if is_food(sku_, pname, food_codes, food_names) else "import"
                food_tagged += ptype == "food"
            prod_rows.append((str(sku_), pname, ptype))
        inserted, food_rows = sales_repo.import_sales(prod_rows, sales)
        skipped = len(sales) - inserted - food_rows
        total = inserted
        engine = get_engine()
        with engine.connect() as c:
            row = reports.db_counts(c)
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


async def stock_upload(filename: str, content: bytes, report_date: str = None):
    """report_date = the date of the stock report (YYYY-MM-DD). Defaults to today when not given.
    The upload time is kept separately in created_at."""
    try:
        snap_day = date.fromisoformat(report_date) if report_date else date.today()
    except ValueError:
        raise HTTPException(400, "report_date harus format YYYY-MM-DD")
    if snap_day > date.today():
        raise HTTPException(400, "Tanggal laporan stok tidak boleh di masa depan")
    if not filename.endswith((".xls", ".xlsx")):
        raise HTTPException(400, "Format harus .xls/.xlsx")
    tmp = os.path.join(UPLOAD_DIR, f"stok_{uuid.uuid4().hex[:8]}{os.path.splitext(filename)[1]}")
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
        stock_skus = sorted(set(df["sku"].astype(str).str.strip()))
        stock_repo.replace_snapshot(
            today, stock_skus,
            lambda: df.drop_duplicates(subset=["sku"]).set_index("sku")["product"].to_dict(),
            [(str(s_), int(q_), today) for s_, q_ in zip(df["sku"], df["quantity"])])
        saved = len(df)
        os.remove(tmp)
        return {"status":"success","message":f"{saved} SKU stock saved for {today}","date":str(today),"total_skus":saved}
    except Exception as e:
        if os.path.exists(tmp): os.remove(tmp)
        raise HTTPException(500, f"Gagal parse: {e}")
