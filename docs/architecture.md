# Stockwise — Architecture

Version 1.1 · 2026-11-03. Section 2 describes the system before Phase 3 (`e6e1e5c`). **Section 4 describes the backend as implemented in Phase 3** (branch `phase3-backend-refactor`). The frontend split (§9) is still a target.

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

## 4. Module map — implemented in Phase 3

The Phase 3 brief fixed the folder names (`src/core`, `repositories`, `services`, `routers`) and kept the
pure business modules at the top level. This replaces the Phase 2 proposal (`infra/`, `domain/`): same
layering, different names, and no move of the rule modules (moving them would only change import paths).

```
app.py                      57   entry: FastAPI app, middleware, page route, router registration, compat re-exports
auth.py                         login, cookie, /api/* middleware (unchanged)
config.py                       planning parameters, env SW_* (unchanged)
replenishment.py                pure purchasing rules (unchanged)
metrics.py                      cost, movement, freshness, turnover (unchanged)
forecast.py                     Holt's DES, grid search, holdout, Pareto ∪ Hero, save (legacy calculate_replenishment removed)
etl.py                          Accurate sales cleaning, name -> SKU lookup (unchanged)
src/
  core/config.py            20   .env, DB_URL, UPLOAD_DIR, MASTER_SKU_PATH, re-export of config.settings
  core/database.py          49   get_engine (one engine per process, pool_pre_ping), get_raw_conn
  core/state.py             31   per-process caches: forecast_cache, processed_cache, master_sku; reset_runtime_state()
  core/ddl.py               68   runtime CREATE/ALTER fallbacks (hero, food, sales_excluded, PO columns)
  core/logging.py           16   get_logger
  parsers/po_master.py     116   PO column mapping, master-list detection and parsing
  parsers/sku_lists.py      58   SKU-name sheet reader, Hero header detection, food-name rules
  repositories/forecast.py  89   load saved forecast, light rows, max calculated_at
  repositories/inventory.py 19   stock snapshot dates and stock per SKU
  repositories/sales.py     42   sales layout, last sale, recent weeks, sold SKUs, weekly sales
  repositories/products.py  44   product-type filter, food rules, food-sales archive/restore
  repositories/purchase_orders.py 85  open PO lines, master-list upsert
  services/inventory_service.py       build_inventory + inventory, summary, risk, stockout rate
  services/purchasing_service.py 140  replenishment list/detail, demand plan, planning settings
  services/forecast_service.py   195  run + save, cached full/light forecast, headline numbers, forecast endpoints
  services/product_service.py    311  Hero list, best-seller scope, Master SKU, Food list, product history
  services/sales_service.py      290  sales ETL upload/confirm, stock snapshot upload
  services/purchase_order_service.py 377  PO upload, list, summary, edit, delete, receive-all
  services/overview_service.py   267  db stats, overview, weekly analysis
  routers/*.py              22–37 one file per area, HTTP only
```

Dependency rules in force: routers → services; services → repositories, parsers, core, top-level rule modules;
repositories → core; parsers → nothing internal. No circular imports (checked by importing every module).
Services receive `(filename, content)` for uploads instead of `UploadFile`; they still raise `HTTPException`
for user errors (kept to preserve messages and status codes).

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

- *[Done in Phase 3]* `get_engine()` used to call `create_engine()` on every call (new pool per request). It now builds one engine per process with `pool_pre_ping=True`.
- psycopg2 raw connection with `execute_values` for bulk writes.
- Repositories own SQL. Only whitelisted literals may be interpolated (product type); everything else is bound.
- Bulk loads: staging temp table + `INSERT … WHERE NOT EXISTS` (sales), delete-by-date + insert (stock), `ON CONFLICT (line_key)` upsert (PO).
- Runtime DDL (`ensure_*`) stays as a guarded fallback; real schema changes go through `migrations/` (see `docs/database.md`).

## 8. Caching

| Cache | Today | Target |
|---|---|---|
| Forecast (full + light) | module dict per Vercel instance; cleared on run/upload | done: `src/core/state.py` |
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

Implemented in Phase 3: `tests/unit` (22), `tests/integration` (7, need `TEST_DATABASE_URL`), `tests/regression` (API contract + old-vs-new run of 248 requests against `e6e1e5c`). E2E Playwright scripts are still outside the repo.
