"""Deterministic synthetic data for integration and regression tests.

Nothing here touches production: callers pass the engine of the TEST database.
The data imitates PT X: 260 SKUs (dotted Accurate codes + AUTO- codes), food and import products,
~21 months of daily sales, one stock snapshot, old-format PO lines.
"""

import datetime as dt
import io
import os
import random

import pandas as pd
from sqlalchemy import text

SCHEMA_SQL = os.path.join(os.path.dirname(__file__), "schema.sql")
SALES_START, SALES_END = dt.date(2025, 1, 2), dt.date(2026, 10, 3)
SNAPSHOT_DATE = dt.date(2026, 10, 7)


def reset_schema(engine):
    with open(SCHEMA_SQL, encoding="utf-8") as f:
        ddl = f.read()
    with engine.begin() as c:
        c.exec_driver_sql(ddl)


def sku_list():
    out = []
    for i in range(260):
        out.append(f"AUTO-{i:08X}" if i % 9 == 0 else f"299.{i // 20:03d}.{i % 20:02d}.001")
    return out


def seed(engine):
    """Reset the schema and load the synthetic data set. Returns the SKU list."""
    reset_schema(engine)
    rnd = random.Random(7)
    skus = sku_list()
    with engine.begin() as c:
        prows = []
        for i, sku in enumerate(skus):
            ptype = "food" if i % 4 == 0 else ("import" if i % 7 else None)  # NULL counts as import
            prows.append({"s": sku, "n": f"Produk {i} {'Snack' if ptype == 'food' else 'Rumah'}", "t": ptype})
        c.execute(text("INSERT INTO products (sku, name, product_type) VALUES (:s, :n, :t)"), prows)

        rows = []
        for k, sku in enumerate(skus):
            base = rnd.choice([0.3, 1, 3, 8, 20])
            first = SALES_START + dt.timedelta(days=rnd.randint(0, 400))
            last = SALES_END if k % 11 else SALES_END - dt.timedelta(days=rnd.randint(30, 200))
            d = first
            while d <= last:
                if rnd.random() < min(0.95, 0.15 * base):
                    q = max(1, int(rnd.gauss(base * 2, base)))
                    a = q * rnd.choice([15000, 32000, 47500.5])
                    rows.append({"s": sku, "d": d, "q": q, "a": a, "c": None if k % 23 == 0 else round(a * 0.28, 2)})
                d += dt.timedelta(days=1)
        for i in range(0, len(rows), 5000):
            c.execute(text("INSERT INTO sales (sku, invoice_date, quantity, amount, cogs_accurate) "
                           "VALUES (:s, :d, :q, :a, :c)"), rows[i:i + 5000])

        srows = []
        for j, sku in enumerate(skus):
            if j % 3 == 2:
                continue
            q = rnd.randint(0, 400) if j % 17 else -rnd.randint(1, 20)
            srows.append({"s": sku, "q": q, "d": SNAPSHOT_DATE})
        c.execute(text("INSERT INTO stock_snapshots (sku, quantity, snapshot_date) VALUES (:s, :q, :d)"), srows)

        porows = []
        for j, sku in enumerate(skus[::4]):
            mode = j % 5
            od = dt.date(2026, 9, 1) + dt.timedelta(days=j % 30)
            eta = [dt.date(2026, 10, 20) + dt.timedelta(weeks=j % 10), None, None, dt.date(2026, 11, 10), None][mode]
            odd = [od, od, None, od, None][mode]
            st = ["pending", "pending", "pending", "partial", "cancelled"][mode]
            porows.append({"s": sku, "o": 300, "r": 100 if st == "partial" else 0, "st": st, "od": odd, "e": eta})
        c.execute(text("INSERT INTO purchase_orders (sku, qty_ordered, qty_received, status, order_date, expected_date) "
                       "VALUES (:s, :o, :r, :st, :od, :e)"), porows)
    return skus


# ── synthetic upload files ────────────────────────────────────

def xlsx(df, header=True):
    b = io.BytesIO()
    df.to_excel(b, index=False, header=header)
    b.seek(0)
    return b


def po_master_file(skus):
    """Purchasing 'Master List' layout (history + open lines, one SKU twice in a PO, a 0206 date, an over-receipt)."""
    import_skus = [s for i, s in enumerate(skus) if i % 4 and not s.startswith("AUTO")][:24]
    rows = []
    for n, sku in enumerate(import_skus):
        po = f"PO/{(n % 3) + 7:02d}/26/{(n % 2) + 1:03d}"
        tgl = dt.date(2026, (n % 3) + 7, 5 + n % 20)
        qty = 50 + 10 * n
        rec = [qty, 0, qty // 2, qty + 3, 0, qty][n % 6]
        rows.append({" Tgl PO": tgl.strftime("%Y-%m-%d"), "PO Number": po, "Item": sku, "Description": f"Desc {sku}",
                     "QTY": qty, "Received": rec if rec else None,
                     "Tgl Terima Barang": (tgl + dt.timedelta(days=30 + n)).strftime("%Y-%m-%d") if rec else None,
                     "Ekspedisi": "MEX" if rec else "-", "Remaining (PCS)": qty - rec, "Note": None,
                     "ETA Date": (tgl + dt.timedelta(days=60)).strftime("%Y-%m-%d")})
    rows.append(dict(rows[1], QTY=12, Received=None))                  # same SKU twice in one PO
    rows.append(dict(rows[2], **{"Tgl Terima Barang": "0206-05-01"}))   # bad year -> warning
    return xlsx(pd.DataFrame(rows))


def hero_file(skus, forecasted):
    """Company Hero SKU list: title rows, header, dotted + dotless code, bundle, duplicate, new and unknown codes."""
    outside = [s for s in skus if s not in forecasted and "." in s][:3]
    a, b, c = outside
    inside = sorted(forecasted)[0]
    rows = [["HERO SKU LIST", None, None, None], [None, None, None, None], ["SKU", "PRODUK", "GROUP", "STATUS"],
            [a, "Produk A", "# G1", "1. Best Seller"],
            [b.replace(".", ""), "Produk B", "# G1", "2. Uprising"],
            [f"{c}+{inside}", "Bundle", "# G2", "1. Best Seller"],
            [a, "Produk A", "# G1", "1. Best Seller"],
            ["299.999.999.001", "Produk Baru Test", "# G3", "2. Uprising"],
            ["123456789", None, "# G3", "1. Best Seller"]]
    return xlsx(pd.DataFrame(rows), header=False)


def two_col_file(rows, header=("SKU", "Nama")):
    data = [list(header)] + [list(r) for r in rows] if header else [list(r) for r in rows]
    return xlsx(pd.DataFrame(data), header=False)


def stock_file(rows):
    """Accurate stock export shape: SKU, name, quantity as Indonesian-formatted text."""
    return xlsx(pd.DataFrame(rows, columns=["a", "b", "c"]))


def accurate_sales_file(lines):
    """Minimal 'Sales By Item Detail' export: title rows, 'Item Unit 1' pre-header, header,
    product rows followed by their transactions and a subtotal, footer.
    lines = [(product_name, [(date, qty, amount, cogs), ...]), ...]"""
    cols = ["Invoice Date", "x1", "Description", "Quantity", "Unit", "Amount", "COGS Amount", "Gross Profit",
            "Date", "Week", "Month", "Quarter", "Year"]
    blank = [None] * len(cols)
    rows = [[None, "PT X"] + [None] * 11, [None, "Sales By Item Detail"] + [None] * 11, blank[:],
            [None, None, None, None, "Item Unit 1"] + [None] * 8, cols]
    for name, txs in lines:
        rows.append([name] + [None] * 12)
        total = 0
        for d, q, a, cogs in txs:
            ds = d.strftime("%d %b %Y")
            rows.append([ds, None, None, f"{q},00", "Pcs", f"{a:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."),
                         f"{cogs:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."), None, ds,
                         "1 - 7", d.strftime("%b %Y"), f"Quarter {((d.month - 1) // 3) + 1} {d.year}", d.year])
            total += q
        rows.append([None, None, None, f"{total},00"] + [None] * 9)
    rows.append(["ACCURATE Accounting System Report"] + [None] * 12)
    return xlsx(pd.DataFrame(rows), header=False)
