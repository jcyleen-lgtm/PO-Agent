"""Accurate 'Sales by Item Detail' exports come in two layouts: the product name sits under "Invoice Date"
(default) or under "Invoice No." when that column is switched on. Both must give the same rows."""
import pandas as pd
import pytest
from etl import clean_sales_data


def _report(path, with_invoice_no):
    lead = ["Invoice No."] if with_invoice_no else []
    head = lead + ["Invoice Date", "Description", "Quantity", "Amount", "COGS Amount"]
    rows = [["BETTER ACCESORIES"], ["Sales By Item Detail"], ["From 01 Jan 2025 to 31 Mar 2025"], [], head]
    for name, txs in [("Alpha Kran", [("05 Jan 2025", "2,00"), ("12 Jan 2025", "3,00")]), ("beta sikat", [("20 Feb 2025", "1,00")])]:
        rows.append([name])
        for d, q in txs:
            rows.append((["OL/01/25/0001"] if with_invoice_no else []) + [d, "SR", q, "10.000,00", "5.000,00"])
        rows.append(([None] if with_invoice_no else []) + [None, None, "5,00" if name == "Alpha Kran" else "1,00"])
    pd.DataFrame(rows).to_excel(path, header=False, index=False)


@pytest.mark.parametrize("with_invoice_no", [False, True])
def test_both_layouts_keep_product_names(tmp_path, with_invoice_no):
    p = tmp_path / "s.xlsx"; _report(p, with_invoice_no)
    r = clean_sales_data(str(p))
    assert not r["errors"]
    df = r["df"]
    assert r["stats"]["product_headers"] == 2
    got = df.groupby("product_name")["quantity"].sum().to_dict()
    assert got == {"Alpha Kran": 5.0, "beta sikat": 1.0}
