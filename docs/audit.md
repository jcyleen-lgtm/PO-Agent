# Stockwise — Phase 1 Audit

As of 2026-11-03 · repository `jcyleen-lgtm/PO-Agent` @ `e6e1e5c` (main) · production DB Supabase `wjltofewqntucjzugwtz`.

Scope of this phase: read-only inspection. No code, schema or data was changed.

---

## 1. Repository map

| File | Lines | Bytes | Responsibility |
|---|---:|---:|---|
| `app.py` | 2,386 | 124 KB | FastAPI app: DB engine, forecast cache, **all** HTTP routes, ETL orchestration, runtime DDL, PO import/parser, Hero SKU, food rules, `build_inventory`, overview/weekly/coverage aggregations |
| `templates/dashboard.html` | 1,521 | 217 KB | Whole SPA: 600 lines CSS + 580 lines JS (159 functions, 73 `innerHTML` writes, 185 lines > 300 chars, longest 2,044 chars) |
| `forecast.py` | 610 | 23 KB | Holt's DES, 81-combo grid search, holdout WMAPE, Pareto + Hero scope, naive fallback, save/load; **legacy** `calculate_replenishment` |
| `etl.py` | 336 | 14 KB | Accurate "Sales By Item Detail" cleaning, footer removal, name→SKU lookup |
| `replenishment.py` | 242 | 11 KB | Pure purchasing rules: status, coverage, ETA hierarchy, safety stock, ROP, reco, projection, order-by |
| `metrics.py` | 115 | 6 KB | Unit cost, movement class, freshness, turnover |
| `auth.py` | 163 | 6 KB | PBKDF2 single-user login, signed cookie, `/api/*` middleware |
| `config.py` | 43 | 2 KB | Planning parameters with env overrides |
| `tests/test_stockwise.py` | 241 | 9 KB | 22 unit tests (pure functions + PO parser) |
| `schema.sql` | 320 | — | **Out of date** (see §5) |
| `README.md` | 93 | — | **Out of date**: describes `migrate.py` / margin pipeline that no longer exists |
| `templates/index.html` | 391 | 31 KB | **Unused** legacy page (not served by any route) |
| `make_password_hash.py` | 30 | — | CLI to create `APP_PASSWORD_HASH` |
| `vercel.json` | 21 | — | `@vercel/python`, region `sin1`, `/static/*` route with no `static/` folder |
| `requirements.txt` | 12 | — | Includes **unused** `anthropic`, `gspread`, `google-auth` |

Stack: Python 3 / FastAPI / SQLAlchemy + psycopg2 → Supabase Postgres (transaction pooler); vanilla HTML/CSS/JS + Chart.js 4.4.1 (cdnjs); deployed as one Vercel Python function (300 s limit, read-only FS except `/tmp`). No frontend build step.

---

## 2. Feature inventory

| Feature | Current behaviour | Key code | Data | Refactor risk |
|---|---|---|---|---|
| Login | Single user from env, PBKDF2, signed cookie, SameSite=Lax, 401 on `/api/*` | `auth.py` | env | High |
| Sales ETL | Upload → parse/clean → preview → confirm; dedupe by date range; food rows routed to `sales_excluded`; batch insert | `etl.py`, `app.py` 886–1088 | `sales`, `sales_excluded`, `products` | High |
| Stock snapshot | Upload Accurate stock with report date; batched upsert; future dates rejected | `app.py` 1089–1183 | `stock_snapshots` | High |
| Master SKU | 2-col list → upsert names into `products` | `app.py` 494–564 | `products` | Medium |
| Food list | Tags products food/import; archives/restores food sales | `app.py` 767–885 | `food_skus`, `sales_excluded` | High |
| Hero SKU list | Company best-seller list; dotless/bundle normalisation; joins scope | `app.py` 287–396 | `hero_skus` | Medium |
| Best-seller scope | Pareto 80% (import revenue, all history) ∪ Hero list; explanation card + list | `forecast.py` 160–190, `app.py` 397–483 | `sales`, `hero_skus` | High |
| Forecast | Holt's DES per SKU, weekly, 12-week horizon, grid search α/β; in-sample MAPE + 8-week holdout WMAPE (no leakage); naive fallback for Hero SKUs | `forecast.py` | `forecast_results` (JSON) | High |
| Inventory build | One pass for stock, coverage, status, cost, plan per SKU | `app.py` 592–766 `build_inventory` | sales 12 wk, latest snapshot, open PO | High |
| Replenishment | Status, Order by, editable qty, export CSV, detail (projection, PO lines, SS comparison) | `replenishment.py`, `/api/replenishment*` | as above | High |
| Product analysis | Stock & coverage, demand plan, weekly table, SKU drawer | `/api/forecast/plan`, `/api/weekly-analysis`, `/api/product/{sku}/history` | | Medium |
| Overview | KPIs, risk list, actual vs forecast, revenue/sales trend, top 10, scope card | `/api/overview`, `/api/inventory/summary`, `/api/best-sellers-at-risk` | | Medium |
| PO tracker | Master-list import (line_key upsert), edit received, close short, receive-all, aging > 60 d, delivery KPIs | `app.py` 1184–1748 | `purchase_orders` | High |
| Data status | Freshness of stock/sales, counts | `/api/db/stats` | | Low |

All features above are live in production. Endpoints present but **not called by the frontend**: `/api/coverage`, `/api/settings` (still useful for debugging). Function present but **never called**: `forecast.calculate_replenishment` (≈140 lines, an older reorder formula).

---

## 3. Business rules as implemented (single sources)

| Rule | Implementation | Where |
|---|---|---|
| Coverage (weeks) | (stock + all open PO outstanding) ÷ avg weekly units, last 4 weeks | `rp.coverage_weeks` |
| Status (3 only) | ≤ 8 wk Understocked · > 8–20 Healthy · > 20 Overstocked; no sales + stock > 0 → Overstocked; no sales + stock ≤ 0 → unclassified | `rp.stock_status` |
| Safety stock | 10% × forecast of next **8 weeks** (`ss_basis_weeks`) | `rp.plan_sku` |
| Lead time | 8 weeks default (`SW_LEAD_TIME_WEEKS`), not per SKU | `config.py` |
| ROP | forecast over lead time + SS | `rp.plan_sku` |
| Recommended order | max(0, LT demand + SS − (stock + PO with ETA inside LT)); negative stock kept in position | `rp.plan_sku` |
| ETA hierarchy | expected_date → order_date + LT → unknown (not counted) | `rp.po_eta` |
| Order by | start of projected stockout week − LT; no stockout in 12 wk → estimate after horizon | `rp.plan_sku` |
| Pareto | revenue rank, all import history, include boundary SKU; forecast also needs a sale in last 8 wk and ≥ 3 of last 12 wk | `forecast.run_forecast` |
| Unit cost | `master_cogs.hpp` (verified) else Σ cogs_accurate ÷ Σ qty, 12 wk (unverified) | `metrics.unit_costs` |
| Turnover | needs ≥ 2 full snapshots | `metrics.inventory_turnover` |
| Freshness | stock > 7 d warn, > 14 d very stale | `metrics.freshness` |
| PO aging | open line > 60 d since PO date | `config.po_aging_warn_days` |

Consistency check: status, coverage, reco and order-by are computed only in `replenishment.py` and reach every page through `build_inventory` → **no duplicated formula found** on the backend. The frontend does not recompute purchasing numbers (it only filters/sorts). `app.coverage_status` is a thin wrapper.

Known, intentional differences that must stay documented:
- Coverage counts **all** open PO (also no-ETA and late ones); the order quantity counts only PO with an ETA inside the lead time. A SKU can therefore be Healthy and still need an order (the reason Order by exists).
- Safety stock = 10% of the 8-week forecast. This equals "10% of lead-time demand" only while lead time = 8 weeks.
- `urgency` (critical/high/medium/low) is still computed and returned; the UI uses Order by instead.

---

## 4. Production data state (read 2026-11-03)

| Item | Value | Consequence |
|---|---|---|
| Sales | 108k rows, last invoice 2026-10-29 | OK |
| Food sales archived | 36k rows in `sales_excluded` | OK |
| Stock snapshots | 2026-10-07 (2,268 SKUs), 2026-11-02 (2,589) | Turnover now possible; trends need more weeks |
| Forecast | 140 SKUs, last run 2026-10-31 | **Older than the 2 Nov stock and the Hero list** → run Refresh forecast |
| Hero SKU | table exists, **0 rows** | Company list not uploaded yet |
| `master_cogs` | **0 rows** | Every cost and inventory value is *unverified* |
| PO | 653 completed, 117 pending, 38 partial, 156 cancelled, 167 superseded | Master list imported |
| Actual lead time | median ≈ 38 d, p90 ≈ 61 d (local test on the same file) | p90 above the 56-day assumption |

---

## 5. Technical debt

| # | Issue | Impact | Proposed solution |
|---|---|---|---|
| T1 | `app.py` 2,386 lines with ~10 responsibilities (routes, ETL, DDL, PO parser, inventory engine, caching) | Hard to change one area safely; merge conflicts | Split into `routers/` + `services/` + `repositories/` (Phase 3), moving code unchanged first |
| T2 | `dashboard.html` 217 KB, CSS + 159 JS functions inline, very long one-line functions | Unreadable diffs, no reuse, easy regressions | Split into `static/css/*.css` and ES-module `static/js/*.js` per page (no build step needed) |
| T3 | Schema lives in 3 places: `schema.sql` (outdated: has `abc_results`, `restock_recommendations`; lacks `product_type`, `hero_skus`, `food_skus`, `sales_excluded`, PO columns), runtime `ensure_*` DDL in `app.py`, and the live DB | No reproducible database; local and prod drift (`quarter_label` width differed before) | `migrations/` with numbered SQL files + `docs/database.md`; keep `ensure_*` only as guarded fallbacks |
| T4 | Dead code: `templates/index.html`, `forecast.calculate_replenishment`, `/api/coverage`, unused deps (`anthropic`, `gspread`, `google-auth`), legacy tables (`restock_recommendations`, `margin_results`, `selling_prices`, `marketplace_fees`, `master_cogs` functions `calc_margin*`) | Confusion, bigger cold start | Remove after approval; keep `master_cogs` (used for verified cost) |
| T5 | `build_inventory` recomputed per request; several endpoints call it on one page load; cache only for forecast, per Vercel instance | Repeated 12-week sales scans; slower pages | Request-level memo + short TTL cache keyed by data version (max snapshot date, max sale, PO updated_at) |
| T5b | `get_engine()` creates a new SQLAlchemy engine (and pool) on every call (found in Phase 2) | Extra connection setup per request; slower pages | Cache one engine per process |
| T6 | Forecast run is synchronous inside one HTTP request | With Pareto ∪ Hero (~250 SKUs) it can approach the 300 s limit | Batch by chunks or move to a background job / cron; at least time it in prod |
| T7 | 31 broad `except Exception` blocks, mixed `print` logging, error messages mixed ID/EN | Silent failures (e.g. cost lookup) | Central error handler + structured logging; keep user messages in one language |
| T8 | f-string SQL fragments (`_type_filter`) | Safe today (whitelisted values) but fragile | Keep whitelist, move to repository helpers with bound parameters |
| T9 | No integration tests in the repo (DB tests and e2e live only in a scratch environment) | Regressions only caught manually | Add `tests/integration` (Postgres in CI or local) and `tests/e2e` (Playwright) to the repo |
| T10 | `README.md` describes a removed workflow | Onboarding wrong | Rewrite with the real run/deploy steps |

### Security

| # | Finding | Severity | Fix |
|---|---|---|---|
| S1 | Supabase advisor: **RLS disabled on all 13 public tables** (exposed through the PostgREST API to anyone with the anon key) | ERROR | `ALTER TABLE … ENABLE ROW LEVEL SECURITY` on every table, no policies. The app connects as the database owner, so it is not affected |
| S2 | Functions `calc_margin*` with mutable `search_path` | WARN | Drop (legacy) or `SET search_path = public` |
| S3 | CORS `allow_origins=["*"]` | Low (cookie auth, no credentials flag) | Restrict to the Vercel domain |
| S4 | No login rate limit | Low–medium | Simple per-IP throttle on `/api/auth/login` |
| S5 | No upload size limit | Low | Reject files over a fixed size before parsing |

---

## 6. Dependency map

```
dashboard.html ──fetch──▶ app.py routes
app.py ──▶ replenishment.py (pure)      ◀── tests
       ──▶ metrics.py (DB + pure)
       ──▶ forecast.py (DB + pure)  ──▶ (no app imports)
       ──▶ etl.py (pure + DB lookup)
       ──▶ config.py, auth.py
```
No circular imports. Business rules sit in pure modules already; the debt is in `app.py` (I/O + orchestration) and the single HTML file.

---

## 7. Master prompt vs current system: points that need a decision

These items in the redesign brief conflict with rules already agreed for this thesis. Nothing will change until each one is decided.

| # | Brief says | Current agreed rule | Proposal |
|---|---|---|---|
| D1 | KPIs "Healthy %, **Low Stock %**, Out of Stock %" | Exactly 3 statuses: Understocked / Healthy / Overstocked | Low Stock = Understocked (label only); Out of stock shown as a subset count |
| D2 | Action Center: Order Now / Order within 1–2 weeks / Healthy / Hold | Order by date + status | Buckets derived only from existing fields: Order now = Order by ≤ today; 1–2 weeks = Order by in 1–14 days; Hold = Overstocked or open PO covers the lead time; Healthy = rest. No new formula |
| D3 | Priority Critical / High / Medium column | Urgency was removed from the UI in favour of Order by | Show priority as the same Order-by buckets, not the old urgency |
| D4 | Dark navy enterprise theme | "Tema design tetap pakai punya kita" (light + dark glass theme) | Keep current tokens; tighten density/spacing only |
| D5 | Safety stock = 10% of lead-time demand | 10% of 8-week forecast | Same today (LT = 8). Choose one wording for the thesis |
| D6 | "Inspect 1,126 SKUs" | Scope = best sellers (Pareto ∪ Hero) | Keep scope; analytics may show all import SKUs where useful |
| D7 | Inventory value trend, inflow vs outflow | Only 2 snapshots; receipts only from PO list | Show as "not enough data" until ≥ 4 weekly snapshots |
| D8 | Full Clean Architecture refactor | Thesis deadline | Do it incrementally, backend first, behaviour unchanged, tests before/after each step |

---

## 8. Tests executed in this phase

- Unit tests on a fresh clone of `e6e1e5c`: 22 passed (`pytest tests -q`).
- Run on the same code on 2026-11-02 (local Postgres 16, not part of the repo): DB integration 18 passed; Hero scope 9 passed; PO import with the real `LIST_PO_TERUPDATE.xlsx` 6 passed; Playwright desktop light/dark + 390 px: no console errors, no horizontal scroll.
- Not tested: forecast run time on production data with Hero list; production upload of Hero list.

## 9. Next phase (Phase 2 — specification)

Write `docs/spec.md`, `docs/architecture.md`, `docs/database.md` (actual live schema + migrations needed), `docs/refactoring-plan.md`, using the decisions taken on D1–D8.
