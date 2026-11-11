"""Integration: sales ETL (Accurate layout), food list archive/restore, master SKU list."""

import datetime as dt

import pandas as pd

from tests.conftest import needs_db, q

pytestmark = needs_db


def test_sales_etl_food_and_master(seeded):
    c, E, S, skus = seeded["client"], seeded["engine"], seeded["S"], seeded["skus"]
    name = q(E, "SELECT name FROM products WHERE sku=:s", s=skus[1])[0][0]
    food_sku, food_name = q(E, "SELECT sku, name FROM products WHERE product_type='food' ORDER BY sku LIMIT 1")[0]
    c.post("/api/food-sku/upload", files={"file": ("food.xlsx", S.two_col_file([(food_sku, food_name)]), "x")})
    d1, d2 = dt.date(2026, 10, 10), dt.date(2026, 10, 11)
    f = S.accurate_sales_file([(name, [(d1, 3, 45000, 12000), (d2, 2, 30000, 8000)]),
                               (food_name, [(d1, 4, 20000, 5000)]),
                               ("Produk Baru Tanpa Kode", [(d2, 1, 99000, 30000)])])
    up = c.post("/api/etl/upload", files={"file": ("SALES.xlsx", f, "x")})
    assert up.status_code == 200
    fid = up.json()["file_id"]
    before = q(E, "SELECT COUNT(*) FROM sales")[0][0]
    cf = c.post(f"/api/etl/confirm/{fid}").json()
    after = q(E, "SELECT COUNT(*) FROM sales")[0][0]
    assert after - before == 3                     # 2 import + 1 new product; the food row is excluded
    assert cf["food_rows_excluded"] == 1
    assert q(E, "SELECT COUNT(*) FROM sales WHERE sku=:s AND invoice_date=:d", s=skus[1], d=d1)[0][0] == 1

    # A second upload of the same period replaces it: the period now holds exactly this file's rows
    # (the "Produk Baru" row is gone, nothing is added twice) and the preview says so up front.
    f2 = S.accurate_sales_file([(name, [(d1, 3, 45000, 12000), (d2, 2, 30000, 8000)])])
    up2 = c.post("/api/etl/upload", files={"file": ("SALES.xlsx", f2, "x")}).json()
    assert (up2["stats"]["replace_from"], up2["stats"]["replace_to"]) == (str(d1), str(d2))
    assert up2["stats"]["replace_existing_rows"] == 4                  # 3 sales + 1 archived food row
    c.post(f"/api/etl/confirm/{up2['file_id']}")
    assert q(E, "SELECT COUNT(*) FROM sales")[0][0] == after - 1
    assert q(E, "SELECT COUNT(*) FROM sales WHERE invoice_date BETWEEN :a AND :b", a=d1, b=d2)[0][0] == 2
    assert q(E, "SELECT COUNT(*) FROM sales_excluded WHERE reason='food' AND invoice_date BETWEEN :a AND :b",
             a=d1, b=d2)[0][0] == 0

    imp = q(E, "SELECT sku, name FROM products WHERE COALESCE(product_type,'import')='import' "
               "AND sku LIKE '299.%' ORDER BY sku LIMIT 1")[0]
    n_sales = q(E, "SELECT COUNT(*) FROM sales WHERE sku=:s", s=imp[0])[0][0]
    fj = c.post("/api/food-sku/upload", files={"file": ("food.xlsx", S.two_col_file([imp]), "x")}).json()
    assert fj["sales_rows_archived"] >= n_sales > 0
    assert q(E, "SELECT COUNT(*) FROM sales WHERE sku=:s", s=imp[0])[0][0] == 0
    assert q(E, "SELECT product_type FROM products WHERE sku=:s", s=imp[0])[0][0] == "food"
    other = (food_sku, food_name)   # the list is replaced on every upload: imp is no longer food
    fj2 = c.post("/api/food-sku/upload", files={"file": ("food.xlsx", S.two_col_file([other]), "x")}).json()
    assert fj2["sales_rows_restored"] >= n_sales                       # removed from the list -> sales back
    assert q(E, "SELECT COUNT(*) FROM sales WHERE sku=:s", s=imp[0])[0][0] == n_sales

    mj = c.post("/api/master-sku/upload",
                files={"file": ("master.xlsx", S.two_col_file([(skus[2], "Nama Baru"), ("299.888.88.001", "Baru")]), "x")}).json()
    assert mj["products_synced"] == 2
    assert q(E, "SELECT name FROM products WHERE sku=:s", s=skus[2])[0][0] == "Nama Baru"


def test_group_codes_never_receive_sales_or_stock(seeded):
    """Accurate item codes are hierarchical. A group code (299.052.02.011 "Tang" with items under it) is a
    category: an unknown name that merely contains its name ("Sarung tangan ...") must not be booked on
    it, and its stock row (the total of its items) must not be stored next to the items."""
    c, E, S = seeded["client"], seeded["engine"], seeded["S"]
    with E.begin() as cn:
        from sqlalchemy import text
        cn.execute(text("INSERT INTO products (sku, name) VALUES ('299.052.02.011','Tang'),"
                        "('299.052.02.011.001','Tang Pelubang tali pinggang dan tali tas'),"
                        "('299.052.02.011.009','Tang Set  bisa ganti kepala  5 in 1') ON CONFLICT DO NOTHING"))
    d = dt.date(2026, 10, 20)
    f = S.accurate_sales_file([("Sarung tangan kerja karet", [(d, 5, 125000, 50000)]),
                               ("tang set bisa ganti kepala 5 in 1", [(d, 1, 346500, 200000)]),
                               ("Tang", [(d, 2, 50000, 20000)])])
    up = c.post("/api/etl/upload", files={"file": ("SALES.xlsx", f, "x")}).json()
    assert "Sarung tangan kerja karet" in up["stats"]["unmatched_names"]
    assert "Tang" in up["stats"]["unmatched_names"]        # a category name is not a product either
    c.post(f"/api/etl/confirm/{up['file_id']}")
    assert q(E, "SELECT COUNT(*) FROM sales WHERE sku='299.052.02.011'")[0][0] == 0
    # same name, different capitals and spacing -> the item, not the category
    assert q(E, "SELECT quantity FROM sales WHERE sku='299.052.02.011.009' AND invoice_date=:d", d=d)[0][0] == 1

    stock = pd.DataFrame([["299.052.02.011", "Tang", "30,00"],
                          ["299.052.02.011.001", "Tang Pelubang", "10,00"],
                          ["299.052.02.011.009", "Tang Set", "20,00"]])
    r = c.post("/api/stock/upload", files={"file": ("stok.xlsx", S.xlsx(stock), "x")},
               data={"report_date": "2026-10-21"}).json()
    assert r["group_rows_skipped"] == 1
    rows = dict(q(E, "SELECT sku, quantity FROM stock_snapshots WHERE snapshot_date='2026-10-21'"))
    assert rows == {"299.052.02.011.001": 10, "299.052.02.011.009": 20}
