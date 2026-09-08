# PO Agent — Database Setup

## Prerequisites

- Python 3.10+
- Supabase account (free tier: https://supabase.com)

## Quick Start

### 1. Create Supabase Project

1. Go to https://supabase.com → New Project
2. Note your database password
3. Go to **Settings → Database → Connection string → URI**
4. Copy the URI (starts with `postgresql://`)

### 2. Install Dependencies

```bash
pip install pandas openpyxl psycopg2-binary sqlalchemy
```

### 3. Set Environment Variable

```bash
export SUPABASE_DB_URL="postgresql://postgres.[project]:[password]@aws-0-[region].pooler.supabase.com:6543/postgres"
```

### 4. Place Data Files

Put these in the same directory as `migrate.py`:
- `SALES_2025-2026.xlsx` — cleaned sales data
- `MARGIN_IMPOR.xlsx` — margin calculator with Master COGS

### 5. Run Migration

```bash
python migrate.py
```

This will:
1. Create all tables (idempotent — safe to re-run)
2. Ingest 87k+ sales transactions
3. Ingest Master COGS (HPP + ongkir)
4. Ingest selling prices (Shopee + TikTok)
5. Auto-calculate all margins (GPBO + GPAO)
6. Print validation summary

## Schema Overview

| Table | Purpose | Rows (est.) |
|-------|---------|-------------|
| `products` | Master SKU list | ~1,100 |
| `sales` | Transaction-level sales | 87,000+ |
| `stock_snapshots` | Inventory snapshots | TBD |
| `purchase_orders` | PO tracking | TBD |
| `master_cogs` | HPP + ongkir per SKU | ~960 |
| `marketplace_fees` | Fee config per platform | 4 |
| `selling_prices` | Harga jual per SKU per marketplace | ~1,900 |
| `margin_results` | Calculated GPBO/GPAO | ~1,900 |
| `abc_results` | ABC dual-criteria results | TBD |
| `forecast_results` | Holt's DES forecasts | TBD |
| `restock_recommendations` | PO recommendations | TBD |

## Margin Calculator

Margins are calculated via PostgreSQL functions:

```sql
-- Recalculate one SKU on one marketplace
SELECT calc_margin('299.028.001', 'shopee');

-- Recalculate all SKUs on Shopee
SELECT calc_margin_all('shopee');

-- Recalculate everything
SELECT calc_margin_global();
```

### Update Flow

When HPP changes (new shipment arrives):
1. Update `master_cogs` table (via upload or direct edit)
2. Run `SELECT calc_margin_global();`
3. All GPBO/GPAO recalculated automatically

### Fee Rate Changes

If Shopee changes admin fee from 21% to 22%:
```sql
UPDATE marketplace_fees SET admin_fee_pct = 0.2200 WHERE marketplace = 'shopee';
SELECT calc_margin_all('shopee');
```
