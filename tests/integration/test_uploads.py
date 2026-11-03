"""Integration: sales ETL (Accurate layout), food list archive/restore, master SKU list."""

import datetime as dt

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

    f2 = S.accurate_sales_file([(name, [(d1, 3, 45000, 12000), (d2, 2, 30000, 8000)])])
    fid2 = c.post("/api/etl/upload", files={"file": ("SALES.xlsx", f2, "x")}).json()["file_id"]
    c.post(f"/api/etl/confirm/{fid2}")
    assert q(E, "SELECT COUNT(*) FROM sales")[0][0] == after          # duplicate rows skipped

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
