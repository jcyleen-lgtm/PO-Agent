# Stockwise — Architecture

Version 1.0 · 2026-11-03. Section 2 describes the system **as it is** (`e6e1e5c`). Section 3 onward describes the **target** reached through `docs/refactoring-plan.md`. Nothing in the target exists yet unless marked *[Current]*.

---

## 1. Principles

1. **Behaviour first.** A refactor step moves code; it does not change outputs. Regression tests prove it.
2. **One home per rule.** Purchasing, health, cost and forecast formulas live in pure domain modules. Routes, SQL and UI call them; they never re-implement them.
3. **Dependencies point inward.** HTTP → services → domain; services → repositories → database. Domain imports nothing from the outer layers.
4. **Pragmatic layers.** No interfaces or classes unless two implementations exist or a test needs a seam. Plain functions and modules are fine.
5. **Deploy compatibility.** Still one Vercel Python function (`app.py` entry), same env vars, same URLs.
6. **Explicit data dates.** Every service result carries the dates of the data it used.

## 2. Current architecture [Current]

```
Browser ── dashboard.html (CSS + JS inline, Chart.js CDN)
   │ fetch /api/*  (cookie auth)
   ▼
app.py  (FastAPI, 2,386 lines)
   ├─ infra: get_engine, get_raw_conn, forecast_cache, runtime DDL (ensure_*)
   ├─ ETL orchestration (sales, stock, master, food, hero, PO)
   ├─ build_inventory  ──► replenishment.py (pure)   metrics.py (cost, turnover…)
   ├─ forecast load/light/summary ──► forecast.py (Holt, Pareto, save)
   └─ 30 routes
etl.py (Accurate parsing)   auth.py (login + middleware)   config.py (parameters)
   ▼
Supabase Postgres (13 tables, transaction pooler)
```

Strengths to keep: pure `replenishment.py` with unit tests; single `build_inventory` pass feeding all pages; batched inserts; config via env.

Weak points: one 2.4k-line module mixing HTTP, SQL, parsing and orchestration; one 217 KB HTML; schema spread over `schema.sql`, runtime DDL and the live DB; per-request recomputation of `build_inventory`.

## 3. Target layers

| Layer | Responsibility | May import | Must not |
|---|---|---|---|
| `routers/` | HTTP: parse query/body/file, call one service, shape the response, map errors to status codes | services, schemas | SQL, formulas |
| `services/` | Use cases: orchestrate repositories + domain, transactions, caching | domain, repositories, config | FastAPI types (except `HTTPException` via a small error helper) |
| `domain/` | Pure rules: status, plan, SS, ROP, order-by, buckets, reasons, cost/turnover maths, Holt's DES, Pareto ranking | stdlib, numpy/pandas | DB, HTTP |
| `repositories/` | SQL only; return plain rows/dicts; bound parameters | db | business decisions |
| `parsers/` | Excel/CSV readers for Accurate, PO master, Hero, Food, Master SKU | pandas | DB |
| `infra/` | Engine, raw connection, cache, settings, auth, logging | — | domain |
| `schemas/` | Pydantic request bodies (PATCH PO, receive-all, login) | pydantic | — |

Dependency direction:

```
routers → services → domain
             │
             └→ repositories → infra.db
parsers ← services
```

## 4. Target module map

```
app.py                      # entry: create app, middleware, include routers (~80 lines)
infra/
  db.py                     # get_engine, get_raw_conn (moved verbatim)
  cache.py                  # forecast_cache + inventory memo (versioned key)
  settings.py               # config.settings (re-export of config.py)
  auth.py                   # current auth.py
  ddl.py                    # ensure_* guarded runtime DDL (kept as fallback)
domain/
  replenishment.py          # current file, unchanged
  metrics.py                # pure parts (movement, freshness, turnover maths)
  holt.py                   # holts_des, grid_search_holts, holdout_wmape
  scope.py                  # Pareto ranking + Hero union (pure)
  actions.py                # [Phase 4] bucket, priority, reason
parsers/
  accurate_sales.py         # current etl.py
  stock_report.py
  po_master.py              # _is_po_master, _parse_po_master, map_po_columns
  sku_lists.py              # read_sku_name_sheet, _hero_columns, food list
repositories/
  sales_repo.py  stock_repo.py  po_repo.py  product_repo.py
  forecast_repo.py  hero_repo.py  food_repo.py
services/
  inventory_service.py      # build_inventory
  forecast_service.py       # run_forecast, load, light, summary
  scope_service.py          # bestseller_scope
  etl_service.py            # sales upload/confirm, stock upload
  sku_list_service.py       # master, food (move/restore sales), hero
  po_service.py             # import, list, summary, update, receive-all
  overview_service.py       # overview, weekly analysis, product history
routers/
  auth.py  uploads.py  inventory.py  forecast.py  po.py  overview.py  scope.py
schemas/
  po.py  auth.py
static/
  css/tokens.css  css/base.css  css/components.css  css/pages.css
  js/api.js  js/state.js  js/format.js  js/ui/*.js  js/charts/*.js  js/pages/*.js
templates/
  dashboard.html            # shell: markup + <link>/<script type="module">
tests/
  unit/  integration/  e2e/
migrations/
  0001_baseline.sql …
```

Target size: 150–400 lines per file; cohesion beats line count.

## 5. API architecture

All routes keep their current paths and payloads. Grouping by router:

| Router | Routes |
|---|---|
| auth | `POST /api/auth/login`, `POST /api/auth/logout`, `GET /api/auth/me` |
| uploads | `POST /api/etl/upload`, `POST /api/etl/confirm/{id}`, `POST /api/stock/upload`, `POST /api/master-sku/upload`, `POST /api/food-sku/upload`, `POST /api/hero-sku/upload` |
| inventory | `GET /api/inventory`, `GET /api/inventory/summary`, `GET /api/replenishment`, `GET /api/replenishment/sku/{sku}`, `GET /api/best-sellers-at-risk`, `GET /api/settings` |
| forecast | `POST /api/forecast/run`, `GET /api/forecast/results`, `GET /api/forecast/sku/{sku}`, `GET /api/forecast/plan` |
| scope | `GET /api/bestseller/scope` |
| po | `POST /api/po/upload`, `GET /api/po/list`, `GET /api/po/summary`, `PATCH /api/po/{id}`, `DELETE /api/po/{id}`, `POST /api/po/receive-all` |
| overview | `GET /api/overview`, `GET /api/weekly-analysis`, `GET /api/product/{sku}/history`, `GET /api/db/stats` |
| legacy | `GET /api/coverage` (unused by UI; remove after approval U6) |

Conventions (target): JSON errors `{"detail": str}`; unavailable metrics `{"available": false, "reason": str}`; dates ISO `YYYY-MM-DD`; numbers unrounded except where the UI contract already rounds.

## 6. Data flow

```
Upload (xls) → parser → validation/preview → repository batch insert → invalidate caches
                                                       │
Refresh forecast → forecast_service: sales_repo (weekly import sales) → domain.scope (Pareto ∪ Hero)
                 → domain.holt per SKU → forecast_repo.save (truncate + insert)

Page request → inventory_service.build(scope, type)
   ├ sales_repo: last 12 weeks per SKU
   ├ stock_repo: latest snapshot
   ├ po_repo: open lines
   ├ forecast_repo/light (cached)
   └ domain.replenishment.plan_sku per SKU → rows + summary + freshness
```

## 7. Database access patterns

- *[Current]* `get_engine()` calls `create_engine()` on **every call**, so each request builds a new engine and connection pool (extra connection setup to the pooler). Target: one cached engine per process with `pool_pre_ping=True` and a small pool suited to the Supabase transaction pooler; behaviour otherwise unchanged.
- psycopg2 raw connection with `execute_values` for bulk writes.
- Repositories own SQL. Only whitelisted literals may be interpolated (product type); everything else is bound.
- Bulk loads: staging temp table + `INSERT … WHERE NOT EXISTS` (sales), delete-by-date + insert (stock), `ON CONFLICT (line_key)` upsert (PO).
- Runtime DDL (`ensure_*`) stays as a guarded fallback; real schema changes go through `migrations/` (see `docs/database.md`).

## 8. Caching

| Cache | Today | Target |
|---|---|---|
| Forecast (full + light) | module dict per Vercel instance; cleared on run/upload | same, in `infra/cache.py` |
| Inventory build | none (recomputed per request) | memo keyed by (type, scope, max snapshot date, max sale date, PO max updated_at, forecast calculated_at); TTL 5 min |

Caches are an optimisation only; a cold instance must give the same answer.

## 9. Frontend architecture (target)

- No build step (keeps Vercel deploy simple). Native ES modules served by FastAPI `StaticFiles` at `/static`; `templates/dashboard.html` becomes the markup shell.
- `js/api.js`: the only place with `fetch`; adds JSON parsing (`readJson`), error mapping, 401 → login.
- `js/state.js`: shared filters (product type, period) and caches per page.
- `js/format.js`: `fmt`, `fmtRp`, `dfmt`, `esc`.
- `js/ui/`: KPI card, table + pagination, segmented control, tooltip (`info()`), banner, empty/error states.
- `js/charts/`: Chart.js wrappers using CSS tokens (`cv()`), theme aware.
- `js/pages/`: overview, replenishment, analysis, po, upload — each exports `load()` and `render()`.
- Rule: the frontend filters and sorts only. Buckets, priorities, reasons and every number come from the API.
- Design tokens (light + dark glass) move unchanged into `css/tokens.css` (D4).

## 10. Error handling

| Where | Rule |
|---|---|
| Parsers | Raise a validation error with a user message (Indonesian, as today) + row/column context |
| Services | Catch only expected DB/parse errors; never swallow silently; return `available:false` for missing data |
| Routers | Map validation → 400, not found → 404, conflict → 409, unexpected → 500 with a short message; log the traceback |
| Frontend | `api.js` turns non-JSON / timeout pages into readable messages (existing `readJson` behaviour) |

The 31 broad `except Exception` blocks in `app.py` are reviewed one by one during the move; each keeps its current user-facing behaviour.

## 11. Security

- Auth middleware unchanged (cookie, PBKDF2, SameSite=Lax).
- RLS enabled on all tables (pending U1); app keeps owner connection.
- CORS restricted to the deployment origin (target).
- Upload size limit (target, e.g. 15 MB) and login throttle (target).
- Secrets only from env; `.env` never committed.

## 12. Testing strategy

| Level | Tool | Runs where | Covers |
|---|---|---|---|
| Unit | pytest | local + CI | domain rules (status, plan, SS, order-by, buckets), parsers, Holt holdout leakage |
| Integration | pytest + Postgres 16 (`TEST_DATABASE_URL`, skipped if unset) | local / CI service container | uploads, idempotency, PO upsert, Hero scope, inventory consistency across endpoints |
| Contract / golden | pytest | local | JSON of key endpoints on a fixed seed compared before vs after each refactor step |
| E2E | Playwright | local | page load, filters, edits, exports, 390 px, light/dark, no console errors |

The integration and e2e suites currently live only in a scratch environment; moving them into `tests/` is the first refactor step.
