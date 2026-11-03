# Stockwise — Database

Version 1.0 · 2026-11-03 · source: live production schema (Supabase project `wjltofewqntucjzugwtz`, schema `public`), read with `information_schema`, `pg_constraint`, `pg_indexes` and the Supabase advisors. `schema.sql` in the repo is **not** the source of truth (see §4).

Engine: PostgreSQL (Supabase), accessed through the transaction pooler as the database owner. No Supabase client / PostgREST is used by the app.

---

## 1. Tables in use

Row counts are planner estimates on 2026-11-03.

### products (~2,882 rows) — master SKU list
| Column | Type | Notes |
|---|---|---|
| sku | varchar(255) **PK** | Accurate item code; AUTO-xxxxxxxx for unmatched names |
| name | varchar(255) NOT NULL | |
| grp | varchar(255) | legacy, unused |
| category | smallint | legacy, unused |
| product_type | varchar(10) default `'import'` | `'import'` / `'food'`, set by the Food list |
| created_at, updated_at | timestamptz | |

Index: `idx_products_type (product_type)`. Not in `schema.sql`: `product_type`, `idx_products_type`.

### sales (~108,442 rows, 48 MB) — import sales lines
| Column | Type | Notes |
|---|---|---|
| id | bigint PK (seq `sales_id_seq`) | |
| sku | varchar(255) NOT NULL **FK → products** | |
| invoice_date | date NOT NULL | |
| quantity | integer NOT NULL | |
| unit | varchar(20) | |
| amount | numeric(15,2) NOT NULL | revenue (Rp) |
| cogs_accurate | numeric(15,2) | basis of the *unverified* unit cost |
| gpao_accurate, gpao_pct_acc | numeric | from Accurate |
| week_label varchar(30), month_label varchar(20), quarter_label varchar(30), year_val smallint | | labels |
| created_at | timestamptz | |

Indexes: `idx_sales_sku`, `idx_sales_date`, `idx_sales_month`, `idx_sales_sku_date (sku, invoice_date)`, `idx_sales_dedup (sku, invoice_date, quantity, amount)`.
Duplicate rule: a row is skipped on import if the same (sku, invoice_date, quantity, amount) exists. No unique constraint enforces it.

### sales_excluded (~36,475 rows) — archived food sales
Same columns as `sales` + `excluded_at timestamptz`, `reason varchar(30)` (`'food'`). Shares sequence `sales_id_seq`. **No primary key** (advisor INFO). Created at runtime by `ensure_sales_excluded`.

### stock_snapshots (~4,857 rows) — stock per report date
| Column | Type | Notes |
|---|---|---|
| id | bigint PK | |
| sku | varchar(50) NOT NULL **FK → products** | |
| quantity | integer NOT NULL | may be negative |
| warehouse | varchar(100) | not filled by the current upload |
| snapshot_date | date NOT NULL | the **report date** chosen at upload |
| created_at | timestamptz | upload time |

Indexes: `idx_stock_sku`, `idx_stock_date`. Upload deletes all rows of the report date then inserts (no unique key). Current data: 2026-10-07 (2,268 SKUs), 2026-11-02 (2,589 SKUs).

### purchase_orders (~1,131 rows) — PO lines
| Column | Type | Notes |
|---|---|---|
| id | bigint PK | |
| sku | varchar(50) NOT NULL **FK → products** | |
| qty_ordered | integer NOT NULL | |
| qty_received | integer default 0 | |
| supplier | varchar(255) | one agent in practice |
| status | varchar(20) default `'pending'` | pending, partial, completed, closed, cancelled, superseded (no CHECK constraint) |
| order_date, expected_date, received_date | date | |
| notes | text | |
| created_at, updated_at | timestamptz | |
| po_number | varchar(60) | runtime DDL |
| line_key | varchar(160) | `PO|SKU|n`, runtime DDL |
| description | text | runtime DDL |
| courier | varchar(60) | runtime DDL |

Indexes: `idx_po_sku`, `idx_po_status`, **`ux_po_line_key` UNIQUE (line_key) WHERE line_key IS NOT NULL** (runtime DDL).
Current data: completed 653, pending 117, partial 38, cancelled 156, superseded 167.

### forecast_results (~140 rows) — latest forecast, one row per SKU
| Column | Type | Notes |
|---|---|---|
| id | bigint PK | |
| sku | varchar(50) NOT NULL **FK → products** | |
| alpha, beta | numeric(5,4) | null for naive rows |
| mape | numeric(8,4) | 999 for naive rows |
| forecast_data | jsonb | `trend, trend_direction, level, avg_weekly_demand, actual{weeks,quantities,amounts}, fitted, forecast{weeks,quantities}, holdout, method, method_reason, scope{pareto,hero,source,rank,revenue,share_pct,cum_share_pct,group,status}` |
| training_months | integer | **holds weeks**, not months (legacy name) |
| calculated_at | timestamptz | |

Index: `idx_forecast_sku`. Replaced in full on every run (TRUNCATE + insert). Last run 2026-10-31.

### food_skus (~323 rows) — food list
`code varchar(80) PK`, `name text`, `added_at timestamptz`. Runtime DDL.

### hero_skus (0 rows) — company best-seller list
`sku varchar(50) PK`, `group_name varchar(160)`, `status varchar(60)`, `source_code varchar(160)`, `product_name text`, `uploaded_at timestamptz`. Runtime DDL. Replaced in full on upload. **No FK to products** (the forecast joins to products, so orphan rows are ignored).

### master_cogs (0 rows) — verified HPP
`sku varchar(50) PK FK → products`, `hpp numeric(15,2) NOT NULL`, `shipping_fee numeric(15,2) default 0`, `cogs numeric(15,2)`, `updated_at`. Read by `metrics.unit_costs`; **empty**, so all costs are unverified (open decision U3).

## 2. Legacy tables (not used by the app)

| Table | Rows | Origin | Proposal (U6) |
|---|---|---|---|
| restock_recommendations | 0 | old reorder engine; unindexed FK, unused index | drop |
| margin_results | 0 | margin pipeline (`calc_margin*` functions) | drop with functions |
| selling_prices | 0 | margin pipeline | drop |
| marketplace_fees | 4 | margin pipeline | drop |
| functions `calc_margin`, `calc_margin_all`, `calc_margin_global` | — | margin pipeline; advisor WARN (mutable search_path) | drop |

`abc_results` exists in `schema.sql` but **not** in production.

## 3. Relationships

```
products 1─* sales
products 1─* stock_snapshots
products 1─* purchase_orders
products 1─* forecast_results
products 1─1 master_cogs
hero_skus.sku  ··· products.sku   (logical, no FK)
food_skus.code ··· products.sku / name (logical, matched by code or normalised name)
sales_excluded ··· same shape as sales (no FK)
```

## 4. Schema drift

| Item | `schema.sql` | Runtime DDL (`app.py`) | Production |
|---|---|---|---|
| products.product_type + index | missing | — | present |
| purchase_orders po_number, line_key, description, courier, ux_po_line_key | missing | `ensure_po_columns` | present |
| food_skus | missing | `ensure_food_tables` | present |
| sales_excluded | missing | `ensure_sales_excluded` | present |
| hero_skus | missing | `ensure_hero_table` | present |
| idx_sales_dedup | missing | — | present (added manually) |
| abc_results | present | — | **absent** |
| restock_recommendations, margin tables | present | — | present, unused |
| RLS | — | — | **disabled on all 13 tables** |

Consequence: a new environment built from `schema.sql` would not run the current app.

## 5. Migration requirements

Phase 3 introduces `migrations/` with plain, numbered SQL files and a `schema_migrations` table. Files are applied in order (Supabase SQL editor or a small `scripts/migrate.py`), each one idempotent where possible.

| File | Content | Changes prod? | Needs approval |
|---|---|---|---|
| `0001_baseline.sql` | Exact DDL of the current production schema (§1 + legacy tables), `IF NOT EXISTS` everywhere; `schema_migrations` table | No (already matches) | No |
| `0002_enable_rls.sql` | `ALTER TABLE … ENABLE ROW LEVEL SECURITY` on all public tables, no policies | Yes | **U1** |
| `0003_sales_excluded_pk.sql` | `ALTER TABLE sales_excluded ADD PRIMARY KEY (id)` (verified 2026-11-03: 0 duplicate ids) | Yes | Yes |
| `0004_po_status_check.sql` | CHECK `status IN (pending, partial, completed, closed, cancelled, superseded)` after verifying existing values | Yes | Yes |
| `0005_drop_legacy.sql` | Drop legacy tables and `calc_margin*` functions | Yes, destructive | **U6** |
| later | Optional `stock_snapshots` unique (sku, snapshot_date, coalesce(warehouse,'')) | Yes | Yes |

Rules:
- `schema.sql` is replaced by `migrations/0001_baseline.sql` (kept as a pointer file or deleted after approval).
- `ensure_*` functions remain as fallbacks until every environment has run the migrations, then become no-ops guarded by the information_schema check that already exists for PO columns.
- Every migration: backup note, verification query, rollback statement in a comment.
- No column renames in this project (e.g. `training_months` keeps its name; documented as weeks).

## 6. Data-quality checks (run before each release)

```sql
-- PO statuses are known
SELECT status, count(*) FROM purchase_orders GROUP BY 1;
-- open lines without ETA and without order date (not counted as incoming)
SELECT count(*) FROM purchase_orders WHERE status IN ('pending','partial') AND expected_date IS NULL AND order_date IS NULL;
-- same digits under several SKUs (name/SKU duplicates)
SELECT replace(sku,'.',''), array_agg(sku) FROM products GROUP BY 1 HAVING count(*) > 1;
-- latest snapshot age and size
SELECT snapshot_date, count(*) FROM stock_snapshots GROUP BY 1 ORDER BY 1 DESC LIMIT 3;
-- forecast older than latest data
SELECT (SELECT max(calculated_at) FROM forecast_results) AS fc, (SELECT max(invoice_date) FROM sales) AS sales,
       (SELECT max(snapshot_date) FROM stock_snapshots) AS stock, (SELECT max(uploaded_at) FROM hero_skus) AS hero;
```
