"""Run one fixed scenario against a given code tree and write every HTTP response to JSON.

    python tests/regression/scenario.py --code <dir with app.py> --out result.json

The database (TEST_DATABASE_URL) is reset and seeded first, so two runs on the same day with two
different code trees must produce identical output if the behaviour is unchanged.
Used by tests/regression/test_old_vs_new.py; never point it at production.
"""

import argparse
import importlib.util
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

VOLATILE_KEYS = {"calculated_at", "created_at", "updated_at", "uploaded_at", "file_id", "light_calc_at",
                 "elapsed_s", "duration_s", "excluded_at", "added_at", "path", "saved_path"}
TS = re.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}")


def mask(o):
    if isinstance(o, dict):
        return {k: ("<masked>" if k in VOLATILE_KEYS else mask(v)) for k, v in o.items()}
    if isinstance(o, list):
        return [mask(x) for x in o]
    if isinstance(o, str):
        if TS.match(o):
            return "<ts>"
        return re.sub(r"/tmp/\S+", "<tmpfile>", o)
    return o


def load_seed():
    spec = importlib.util.spec_from_file_location("sw_seed", os.path.join(HERE, "..", "fixtures", "seed.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--code", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    db = os.environ["TEST_DATABASE_URL"]
    if "supabase" in db or "pooler" in db:
        raise SystemExit("refusing to run against Supabase")
    code = os.path.abspath(a.code)
    os.chdir(code)
    sys.path.insert(0, code)
    os.environ.update(SUPABASE_DB_URL=db, APP_USERNAME="admin", SESSION_SECRET="x" * 40)
    from auth import hash_password
    os.environ["APP_PASSWORD_HASH"] = hash_password("rahasia123")
    S = load_seed()
    import app as A
    from fastapi.testclient import TestClient
    from sqlalchemy import text

    engine = A.get_engine()
    skus = S.seed(engine)
    c = TestClient(A.app, raise_server_exceptions=False)   # record 500s as production would return them
    out = []

    def rec(name, resp, unordered_results=False):
        try:
            body = resp.json()
        except Exception:
            body = {"_text_len": len(resp.text)}
        if unordered_results and isinstance(body, dict) and isinstance(body.get("results"), list):
            # legacy /api/coverage sorts by coverage only; ties keep the SQL GROUP BY order, which is not
            # deterministic -> compare as a set (pre-existing behaviour, documented in docs/audit.md)
            body = dict(body, results=sorted(body["results"], key=lambda r: str(r.get("sku"))))
        out.append({"step": name, "status": resp.status_code, "body": mask(body)})
        return body

    def q(sql, **p):
        with engine.connect() as k:
            return k.execute(text(sql), p).fetchall()

    rec("anon inventory", c.get("/api/inventory"))
    rec("login wrong", c.post("/api/auth/login", json={"username": "admin", "password": "nope"}))
    rec("login", c.post("/api/auth/login", json={"username": "admin", "password": "rahasia123"}))
    rec("me", c.get("/api/auth/me"))
    rec("page", c.get("/"))
    rec("forecast run", c.post("/api/forecast/run"))
    fc = sorted(r[0] for r in q("SELECT sku FROM forecast_results"))
    pick = fc[:2] + fc[-1:]

    def reads(tag):
        for pt in ("import", "food", "all"):
            for sc in ("all", "bestseller"):
                rec(f"{tag} inventory {pt} {sc}", c.get(f"/api/inventory?product_type={pt}&scope={sc}"))
                rec(f"{tag} summary {pt} {sc}", c.get(f"/api/inventory/summary?product_type={pt}&scope={sc}"))
            rec(f"{tag} replenishment {pt}", c.get(f"/api/replenishment?product_type={pt}"))
            rec(f"{tag} risk {pt}", c.get(f"/api/best-sellers-at-risk?product_type={pt}"))
            rec(f"{tag} plan {pt}", c.get(f"/api/forecast/plan?product_type={pt}"))
            rec(f"{tag} weekly {pt}", c.get(f"/api/weekly-analysis?product_type={pt}"))
            rec(f"{tag} weekly bs {pt}", c.get(f"/api/weekly-analysis?product_type={pt}&scope=bestseller"))
        for p in ("l4w", "l12w", "this_month", "last_month", "l7d", "l8w"):
            rec(f"{tag} overview {p}", c.get(f"/api/overview?preset={p}&top_sort=revenue&product_type=import"))
        rec(f"{tag} overview custom", c.get("/api/overview?start=2026-08-01&end=2026-09-30&top_sort=quantity&product_type=all"))
        rec(f"{tag} forecast results light", c.get("/api/forecast/results?limit=1000&fields=light"))
        rec(f"{tag} forecast results", c.get("/api/forecast/results?limit=5"))
        for s in pick:
            rec(f"{tag} forecast sku {s}", c.get(f"/api/forecast/sku/{s}"))
            rec(f"{tag} repl sku {s}", c.get(f"/api/replenishment/sku/{s}"))
            rec(f"{tag} history {s}", c.get(f"/api/product/{s}/history"))
        rec(f"{tag} settings", c.get("/api/settings"))
        rec(f"{tag} db stats", c.get("/api/db/stats"))
        rec(f"{tag} scope", c.get("/api/bestseller/scope"))
        for st in ("all", "open", "overdue", "pending"):
            rec(f"{tag} po list {st}", c.get(f"/api/po/list?status={st}"))
        rec(f"{tag} po summary", c.get("/api/po/summary"))

    reads("base")

    # PO master import + edits
    rec("po master upload", c.post("/api/po/upload", files={"file": ("LIST_PO.xlsx", S.po_master_file(skus), "x")}))
    rec("po master re-upload", c.post("/api/po/upload", files={"file": ("LIST_PO.xlsx", S.po_master_file(skus), "x")}))
    ids = [r[0] for r in q("SELECT id FROM purchase_orders WHERE line_key IS NOT NULL ORDER BY line_key")]
    rec("po patch partial", c.patch(f"/api/po/{ids[1]}", json={"qty_received": 5}))
    rec("po patch over", c.patch(f"/api/po/{ids[4]}", json={"qty_received": 9999}))
    rec("po patch date", c.patch(f"/api/po/{ids[4]}", json={"received_date": "2026-10-01"}))
    rec("po close", c.patch(f"/api/po/{ids[1]}", json={"status": "closed"}))
    rec("po reopen", c.patch(f"/api/po/{ids[1]}", json={"status": "reopen"}))
    rec("po bad status", c.patch(f"/api/po/{ids[1]}", json={"status": "bogus"}))
    rec("po bad qty", c.patch(f"/api/po/{ids[1]}", json={"qty_received": -1}))
    rec("po notes", c.patch(f"/api/po/{ids[2]}", json={"notes": "cek agen"}))
    rec("po empty body", c.patch(f"/api/po/{ids[2]}", json={}))
    pon = q("SELECT po_number FROM purchase_orders WHERE po_number IS NOT NULL ORDER BY po_number LIMIT 1")[0][0]
    rec("po receive all", c.post("/api/po/receive-all", json={"po_number": pon}))
    rec("po receive all missing", c.post("/api/po/receive-all", json={}))
    rec("po delete", c.delete(f"/api/po/{ids[-1]}"))
    reads("after po")

    # Hero list + forecast
    base = set(fc)
    rec("hero upload", c.post("/api/hero-sku/upload", files={"file": ("HERO.xlsx", S.hero_file(skus, base), "x")}))
    rec("scope stale", c.get("/api/bestseller/scope"))
    rec("forecast run hero", c.post("/api/forecast/run"))
    reads("after hero")

    # Stock snapshot, food list, master list, sales ETL
    stock = [[s, "x", "1.234,00"] for s in skus[:40]] + [["299.999.99.001", "new", "5"]]
    rec("stock upload", c.post("/api/stock/upload", files={"file": ("stok.xlsx", S.stock_file(stock), "x")},
                               data={"report_date": "2026-09-15"}))
    rec("stock future", c.post("/api/stock/upload", files={"file": ("stok.xlsx", S.stock_file(stock), "x")},
                               data={"report_date": "2099-01-01"}))
    food = q("SELECT sku, name FROM products WHERE product_type='food' ORDER BY sku LIMIT 5")
    rec("food upload", c.post("/api/food-sku/upload", files={"file": ("food.xlsx", S.two_col_file(food), "x")}))
    rec("master upload", c.post("/api/master-sku/upload",
                                files={"file": ("m.xlsx", S.two_col_file([(skus[2], "Nama Baru"), ("299.888.88.001", "Baru")]), "x")}))
    import datetime as dt
    name = q("SELECT name FROM products WHERE sku=:s", s=skus[1])[0][0]
    f = S.accurate_sales_file([(name, [(dt.date(2026, 10, 10), 3, 45000, 12000)]),
                               (food[0][1], [(dt.date(2026, 10, 10), 4, 20000, 5000)]),
                               ("Produk Baru Tanpa Kode", [(dt.date(2026, 10, 11), 1, 99000, 30000)])])
    up = rec("etl upload", c.post("/api/etl/upload", files={"file": ("SALES.xlsx", f, "x")}))
    if isinstance(up, dict) and up.get("file_id"):
        rec("etl confirm", c.post(f"/api/etl/confirm/{up['file_id']}"))
    rec("etl confirm missing", c.post("/api/etl/confirm/doesnotexist"))
    rec("upload wrong type", c.post("/api/hero-sku/upload", files={"file": ("x.txt", b"abc", "x")}))
    reads("final")
    rec("logout", c.post("/api/auth/logout"))

    with open(a.out, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=1, sort_keys=False, default=str)
    print(f"{len(out)} responses written to {a.out}")


if __name__ == "__main__":
    main()
