"""Product lists and scope: Hero SKU, best-seller scope, Master SKU, Food SKU (archive/restore sales), product history."""

from datetime import date, timedelta
import io
import os

from fastapi import HTTPException
import pandas as pd

from src.core.database import get_engine
from src.core.ddl import ensure_food_tables, ensure_hero_table
from src.core.state import forecast_cache, master_sku
from src.parsers.sku_lists import _hero_columns, _norm_name, is_food, read_sku_name_sheet
from src.repositories import (hero as hero_repo, inventory as stock_repo, products as product_repo,
                              purchase_orders as po_repo, sales as sales_repo)
from src.repositories.products import move_food_sales
from src.services.forecast_service import get_forecast_light



async def upload_hero_sku(filename: str, content: bytes):
    """Company best-seller list (sheet HERO SKU / BEST SELLER SKU): SKU + optional PRODUK, GROUP, STATUS.
    Every SKU on it is analysed by Stockwise, on top of the Pareto 80% set. Replaces the stored list.
    Codes are matched to Accurate SKUs: exact, else ignoring dots (299028001 = 299.028.001);
    a bundle 'A+B' becomes its component SKUs."""
    if not filename.lower().endswith((".xls", ".xlsx")):
        raise HTTPException(400, "File harus .xls atau .xlsx")
    ext = os.path.splitext(filename)[1].lower()
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
        prods = product_repo.names_by_sku(conn)
        snap = stock_repo.latest_snapshot_date(conn)
        in_stock = stock_repo.skus_in_snapshot(conn, snap) if snap else set()
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
            product_repo.insert_missing(conn, new_products)
        hero_repo.replace_list(conn, list(saved.values()))
    forecast_cache.clear()
    return {
        "status": "success", "skus_saved": len(saved), "bundles_split": bundles,
        "normalized": normalized[:30], "normalized_count": len(normalized),
        "duplicates_skipped": dupes, "unmatched": unmatched[:30], "unmatched_count": len(unmatched),
        "new_products": len(new_products),
        "groups": len({v["g"] for v in saved.values() if v["g"]}),
        "message": f"{len(saved)} SKU Hero disimpan. Jalankan Refresh forecast supaya semuanya masuk analisis.",
    }


async def bestseller_scope():
    """How the best-seller set is formed: Pareto 80% of import revenue over the last `pareto_window_weeks`
    weeks (same rule as the forecast run) plus the company Hero SKU list. Returns the Pareto curve, the
    counts and one row per SKU in scope."""
    from config import settings as planning_settings
    win = int(planning_settings().get("pareto_window_weeks") or 26)
    engine = get_engine()
    with engine.begin() as conn:
        ensure_hero_table(conn)
        sales = sales_repo.import_revenue_ranking(conn, win)
        hero = hero_repo.list_with_names(conn)
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
        "period": {"start": str(first) if first else None, "end": str(last) if last else None, "window_weeks": win},
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


async def upload_master_sku(filename: str, content: bytes):
    """Master SKU list -> products table (the sales ETL resolves names to SKUs from products).
    Kept in the database, not on disk: Vercel's file system is read-only and every instance needs the list."""
    if not filename.lower().endswith((".xls", ".xlsx")):
        raise HTTPException(400, "Format file harus .xls atau .xlsx")
    try:
        products_by_sku = read_sku_name_sheet(content, filename)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(400, f"Gagal membaca Master SKU: {e}")
    if not products_by_sku:
        raise HTTPException(400, "Tidak ada SKU yang terbaca. Format: kolom A = SKU, kolom B = nama produk.")
    try:
        # this instance's in-memory mapping (description -> SKU), used next to the products table
        master_sku.update({n.lower(): k for k, n in products_by_sku.items() if n})

        product_repo.upsert_names(list(products_by_sku.items()))

        return {
            "status": "success",
            "entries": len(products_by_sku),
            "products_synced": len(products_by_sku),
            "message": f"Master SKU berhasil di-upload dan {len(products_by_sku)} SKU disinkronkan ke database.",
        }
    except Exception as e:
        raise HTTPException(500, f"Gagal memproses Master SKU: {e}")


async def upload_food_sku(filename: str, content: bytes):
    """Upload the food list (Excel: col A = SKU/code, col B = product name).
    Replaces the stored list, re-tags every product as food or import, and takes food sales OUT of the
    system: they are moved to sales_excluded (reason 'food'), so no page, forecast or KPI uses them.
    A product removed from the list gets its archived sales back."""
    if not filename.lower().endswith((".xls", ".xlsx")):
        raise HTTPException(400, "File harus .xls atau .xlsx")
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
        product_repo.replace_food_list(conn, entries)
        codes, names = set(entries), {_norm_name(n) for n in entries.values() if n}

        products = product_repo.all_names(conn)
        food = [sku for sku, name in products if is_food(sku, name, codes, names)]
        food_set = set(food)
        auto_matched = sorted({name for sku, name in products
                               if sku in food_set and str(sku).startswith("AUTO-")})

        product_repo.set_product_types(conn, food)

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


async def product_history(sku: str, weeks: int = 52):
    """Product Analysis drill-down: weekly sales, stock snapshots and PO lines for one SKU."""
    engine = get_engine()
    with engine.connect() as conn:
        name = product_repo.name_of(conn, sku)
        if name is None:
            raise HTTPException(404, f"SKU {sku} not found")
        mx = sales_repo.last_sale_date(conn)
        sales = sales_repo.weekly_for_sku(conn, sku, (mx - timedelta(weeks=weeks)) if mx else date(1900, 1, 1))
        snaps = stock_repo.history_for_sku(conn, sku)
        pos = po_repo.lines_for_sku(conn, sku)
    return {"sku": sku, "product_name": name,
            "weekly_sales": [{"week": str(w), "qty": int(q or 0), "revenue": float(a or 0)} for w, q, a in sales],
            "stock_snapshots": [{"date": str(d), "qty": int(q)} for d, q in snaps],
            "stock_movement_available": len(snaps) >= 2,
            "purchase_orders": [{"id": r[0], "qty_ordered": r[1], "qty_received": r[2], "status": r[3],
                                 "order_date": str(r[4]) if r[4] else None, "expected_date": str(r[5]) if r[5] else None,
                                 "received_date": str(r[6]) if r[6] else None, "supplier": r[7]} for r in pos]}
