"""Readers for SKU list uploads (Master SKU, Hero SKU) and the food-name rules. No database access."""

import io
import os

import pandas as pd
from fastapi import HTTPException


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


def _norm_name(name):
    return " ".join(str(name or "").lower().split())


def is_food(sku, name, codes, names):
    """Food if the SKU equals a listed code or sits under one (299.025 covers 299.025.xx...),
    or if the product name matches a listed name exactly (catches AUTO-xxxx SKUs)."""
    sku = str(sku or "").strip()
    parts = sku.split(".")
    if any(".".join(parts[:i]) in codes for i in range(1, len(parts) + 1)):
        return True
    return _norm_name(name) in names
