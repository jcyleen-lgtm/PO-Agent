# Stockwise — API Inventory

As of Phase 3 (branch `phase3-backend-refactor`). Generated from `src/routers/*.py` and one run of
`tests/regression/scenario.py`; every row below is checked against the pre-refactor commit `e6e1e5c` by
`tests/regression/test_api_contract.py` (paths, methods, parameter names, defaults) and
`tests/regression/test_old_vs_new.py` (status codes and full JSON of 248 responses).

**Authentication.** Every `/api/*` path except `/api/auth/login`, `/api/auth/logout`, `/api/auth/me` needs the
session cookie set by `POST /api/auth/login` (`auth.py` middleware) and answers `401 {"detail": "Not logged in"}` without it.
`GET /` (dashboard HTML) is public. Errors use `{"detail": "<message>"}`.

**Auth routes** (`auth.py`, unchanged): `POST /api/auth/login` body `{username, password, remember}` → `{username}` + cookie;
`POST /api/auth/logout` → `{ok: true}`; `GET /api/auth/me` → `{username}` or 401.

"Tables" lists the tables the service reads or writes (directly or through a repository/helper).

| Method | Path | Query parameters (default) | Body | Response top-level keys | Tables | Router |
|---|---|---|---|---|---|---|
| GET | `/api/forecast/results` | `limit`=200, `sort`='volume', `fields`='full' | — | `summary`, `results`, `total` | forecast_results, products | forecast |
| POST | `/api/forecast/run` | — | — | `status`, `summary`, `saved_to_db` | forecast_results, hero_skus, products, sales | forecast |
| GET | `/api/forecast/sku/{sku}` | — | — | `sku`, `product_name`, `alpha`, `beta`, `mape`, `trend`, `trend_direction`, `level`, `avg_weekly_demand`, `training_weeks`, `actual_data`, `fitted_data`, `forecast_data`, `holdout`, `method`, `method_reason`, `scope` | forecast_results, products | forecast |
| GET | `/api/best-sellers-at-risk` | `product_type`='all', `limit`=50 | — | `results`, `total_understocked`, `stock_now_date`, `stock_prev_date`, `weeks_between`, `comparison_available`, `best_sellers_only`, `freshness` | forecast_results, master_cogs, products, purchase_orders, sales, stock_snapshots | inventory |
| GET | `/api/inventory` | `sort`='priority', `product_type`='import', `scope`='all', `summary_only`=False | — | `summary`, `freshness`, `stock_date`, `has_stock`, `snapshot_count`, `sales_weeks`, `settings`, `results` | forecast_results, master_cogs, products, purchase_orders, sales, stock_snapshots | inventory |
| GET | `/api/inventory/summary` | `product_type`='import', `scope`='all' | — | `summary`, `freshness`, `stock_date`, `has_stock`, `snapshot_count`, `sales_weeks`, `settings`, `turnover`, `top_reorder`, `order_soon`, `order_overdue` | forecast_results, master_cogs, products, purchase_orders, sales, stock_snapshots | inventory |
| GET | `/api/db/stats` | — | — | `products`, `sales`, `date_range`, `last_update`, `unique_skus`, `forecast_entries`, `stock_date`, `stock_skus` | forecast_results, products, sales, stock_snapshots | overview |
| GET | `/api/overview` | `start`=None, `end`=None, `preset`='l4w', `top_sort`='revenue', `product_type`='all' | — | `period`, `prev_period`, `kpis`, `comparison`, `trend`, `top_products`, `forecast_summary` | forecast_results, products, sales | overview |
| GET | `/api/weekly-analysis` | `product_type`='import', `scope`='all' | — | `results`, `week_labels`, `total_weeks`, `total_skus` | forecast_results, products, sales | overview |
| GET | `/api/bestseller/scope` | — | — | `period`, `import_revenue`, `skus_sold`, `pareto`, `hero`, `counts`, `forecast_stale`, `curve`, `groups`, `rows` | forecast_results, hero_skus, products, sales | products |
| POST | `/api/food-sku/upload` | — | multipart `file` | `status`, `food_skus_in_file`, `products_marked_food`, `products_marked_import`, `sales_rows_restored`, `sales_rows_archived`, `auto_sku_matched_by_name`, `auto_sku_matched_count` | food_skus, products, sales, sales_excluded | products |
| POST | `/api/hero-sku/upload` | — | multipart `file` | `status`, `skus_saved`, `bundles_split`, `normalized`, `normalized_count`, `duplicates_skipped`, `unmatched`, `unmatched_count`, `new_products`, `groups`, `message` | hero_skus, products, stock_snapshots | products |
| POST | `/api/master-sku/upload` | — | multipart `file` | `status`, `entries`, `products_synced`, `message` | products, sales | products |
| GET | `/api/product/{sku}/history` | `weeks`=52 | — | `sku`, `product_name`, `weekly_sales`, `stock_snapshots`, `stock_movement_available`, `purchase_orders` | products, purchase_orders, sales, stock_snapshots | products |
| GET | `/api/po/list` | `status`='open' | — | `count`, `data`, `aging_warn_days` | products, purchase_orders | purchase_orders |
| POST | `/api/po/receive-all` | — | JSON object | `status`, `po_number`, `lines` | purchase_orders | purchase_orders |
| GET | `/api/po/summary` | — | — | `open_pos`, `open_qty_ordered`, `open_qty_received`, `outstanding_qty`, `overdue`, `eta_unknown`, `eta_estimated`, `aging`, `aging_warn_days`, `open_po_numbers`, `by_status`, `suppliers`, `default_lead_time_weeks`, `fulfillment`, `lead_time`, `stockout_rate` | forecast_results, purchase_orders, stock_snapshots | purchase_orders |
| POST | `/api/po/upload` | — | multipart `file` | `status`, `format`, `message`, `matched`, `new_lines`, `updated_lines`, `po_numbers`, `open_lines`, `superseded_old_rows`, `unmatched`, `unmatched_names`, `warnings` | products, purchase_orders | purchase_orders |
| DELETE | `/api/po/{po_id}` | — | — | `status`, `id` | purchase_orders | purchase_orders |
| PATCH | `/api/po/{po_id}` | — | JSON object | `status`, `id`, `qty_ordered`, `qty_received`, `line_status`, `received_date` | purchase_orders | purchase_orders |
| GET | `/api/forecast/plan` | `product_type`='import' | — | `results`, `calculated_at`, `holdout_weeks`, `horizon_weeks` | forecast_results, products, sales | purchasing |
| GET | `/api/replenishment` | `product_type`='import', `only_reorder`=False, `urgency`='all' | — | `results`, `summary`, `freshness`, `settings`, `stock_date` | forecast_results, master_cogs, products, purchase_orders, sales, stock_snapshots | purchasing |
| GET | `/api/replenishment/sku/{sku}` | — | — | `sku`, `product_name`, `current_stock`, `stock_date`, `unit_cost`, `forecast`, `plan`, `safety_stock_comparison`, `freshness` | forecast_results, master_cogs, products, purchase_orders, sales, stock_snapshots | purchasing |
| GET | `/api/settings` | — | — | `lead_time_weeks`, `review_period_days`, `ss_pct`, `ss_basis_weeks`, `understock_weeks`, `overstock_weeks`, `stale_warn_days`, `stale_block_days`, `cost_window_weeks`, `cost_verified`, `fast_share`, `medium_share`, `movement_window_weeks`, `po_aging_warn_days` | — | purchasing |
| POST | `/api/etl/confirm/{file_id}` | — | — | `status`, `message`, `food_rows_excluded`, `food_products_tagged`, `database`, `forecast_status` | food_skus, forecast_results, hero_skus, products, sales, sales_excluded | sales |
| POST | `/api/etl/upload` | — | multipart `file` | `file_id`, `filename`, `stats`, `preview`, `columns` | products, stock_snapshots | sales |
| POST | `/api/stock/upload` | — | multipart `file` + form `report_date` | `status`, `message`, `date`, `total_skus` | products, stock_snapshots | sales |

## Known behaviour preserved as-is (not changed in Phase 3)

| ID | Endpoint | Behaviour | Proposed fix (needs approval) |
|---|---|---|---|
| B1 | `GET /api/replenishment?product_type=food` | **500** (`KeyError: 'forecasted'`) when the product type has no sales, e.g. after all food sales are archived — the case in production. The dashboard only calls `product_type=import`, so users do not see it. | Return the normal shape with zero counts |
| B2 | `GET /api/coverage` (legacy) | Tied rows in non-deterministic order | **Resolved 2026-11-03: endpoint removed (U6)** |
| B3 | Runtime DDL `ensure_sales_excluded` | Would create `sales_excluded` with its own `BIGSERIAL` key; production's table has no primary key and shares `sales_id_seq` | Align in migration `0003` (docs/database.md §5) |
