# Stockwise — Refactoring Plan

Version 1.1 · 2026-11-03 · decisions D1–D8 approved; open items U1–U10 in `docs/spec.md` §15.

**Status:** R0–R6 done (branch `phase3-backend-refactor`, module names per the Phase 3 brief — see
`docs/architecture.md` §4). R7 done for the items approved on 2026-11-03 (index.html, .DS_Store, `calculate_replenishment`, `/api/coverage`, unused deps; schema.sql kept until R8), R8 (migrations) for Phase 3b / approval, R9 is
Phase 4, R10 partly done (engine reuse; inventory memo and forecast timing still open).

Goal: split `app.py` (2,386 lines) and `templates/dashboard.html` (217 KB) into the modules of `docs/architecture.md` **without changing behaviour**, backend first (D8). The app stays deployable after every step: each step is one commit (or a small PR), tests green, deployed, checked in production before the next.

---

## 1. Current files and responsibilities

### app.py

| Lines | Responsibility | Destination |
|---|---|---|
| 1–84 | imports, env, app + middleware, `get_engine`, `get_raw_conn` | `app.py` (composition), `infra/db.py` |
| 85–286 | auto forecast, `load_forecast_from_db`, `get_forecast_light`, `forecast_summary_fast`, `get_latest_forecast`, `forecast_cache` | `services/forecast_service.py`, `repositories/forecast_repo.py`, `infra/cache.py` |
| 287–483 | Hero table DDL, hero file columns, hero upload, best-seller scope | `infra/ddl.py`, `parsers/sku_lists.py`, `services/sku_list_service.py`, `services/scope_service.py` + `domain/scope.py`, `routers/uploads.py`, `routers/scope.py` |
| 484–564 | page route, `read_sku_name_sheet`, master SKU upload | `routers/pages.py` (in `app.py`), `parsers/sku_lists.py`, `services/sku_list_service.py` |
| 565–766 | `_type_filter`, `coverage_status`, `_fetch_po_lines`, **`build_inventory`** | `repositories/*`, `services/inventory_service.py` |
| 767–885 | food rules, `sales_excluded`, move/restore food sales, food upload | `repositories/food_repo.py`, `services/sku_list_service.py` |
| 886–1088 | sales ETL upload + confirm (staging, dedupe, food routing, auto forecast) | `services/etl_service.py`, `repositories/sales_repo.py`, `routers/uploads.py` |
| 1089–1183 | stock upload | `parsers/stock_report.py`, `services/etl_service.py`, `repositories/stock_repo.py` |
| 1184–1506 | PO column mapping, PO master parser/import, PO upload | `parsers/po_master.py`, `services/po_service.py`, `repositories/po_repo.py` |
| 1507–1748 | PO list, summary, stockout rate, delete, patch, receive-all | `services/po_service.py`, `routers/po.py`, `schemas/po.py` |
| 1749–2121 | db stats, overview, best sellers at risk, weekly analysis, coverage (legacy) | `services/overview_service.py`, `routers/overview.py`, `routers/inventory.py` |
| 2122–2386 | inventory, summary, forecast run/results/sku/plan, replenishment, replenishment sku, product history, settings | `routers/inventory.py`, `routers/forecast.py` |

### Other files

| File | Action |
|---|---|
| `replenishment.py` | → `domain/replenishment.py` unchanged (shim re-export left at old path) |
| `metrics.py` | split: pure maths → `domain/metrics.py`; SQL (`unit_costs`, turnover queries) → repositories |
| `forecast.py` | Holt/grid/holdout → `domain/holt.py`; Pareto ranking → `domain/scope.py`; DB load/save → `forecast_repo`; `run_forecast` → `forecast_service`; `calculate_replenishment` removed (U6) |
| `etl.py` | → `parsers/accurate_sales.py` (+ lookup query → `product_repo`) |
| `auth.py`, `config.py` | → `infra/` with shims |
| `templates/dashboard.html` | CSS → `static/css/*`, JS → `static/js/*` (step R9) |
| `templates/index.html`, `schema.sql`, README | remove / replace (U6), README rewritten |

## 2. Dependencies to preserve

- `vercel.json` builds `app.py`; `app` object must stay importable from `app.py`.
- `templates/dashboard.html` is read from disk at `/`; static files must be bundled the same way (verify on a preview deploy before R9 goes to production).
- `forecast_cache` must be cleared by: forecast run, sales confirm, food upload, hero upload, master upload (same triggers as today).
- `build_inventory` is the single source for `/api/inventory`, `/summary`, `/replenishment`, `/best-sellers-at-risk`, `/forecast/plan`, `/replenishment/sku`.
- Response JSON keys used by the frontend (all of them, including legacy `urgency`, `no_sales: 0`).
- Error message texts shown to users (Indonesian upload messages).
- Env vars: `SUPABASE_DB_URL`, `APP_USERNAME`, `APP_PASSWORD_HASH`, `SESSION_SECRET`, `SW_*`.
- Old import paths (`import replenishment as rp`, `from forecast import …`) kept as re-export shims until tests import the new paths.

## 3. Refactoring sequence

Each step: move code verbatim first, then adjust imports; no logic edits inside a move step.

| Step | Work | Tests that must pass | Done when |
|---|---|---|---|
| **R0 Safety net** | Add to repo: `tests/integration/` (current scratch DB tests: 18 core + 6 PO + 9 Hero, using `TEST_DATABASE_URL`, skip if unset) with the synthetic seed; `tests/e2e/` Playwright script; **golden snapshots**: JSON of `/api/inventory?scope=bestseller`, `/inventory/summary`, `/replenishment`, `/forecast/plan`, `/po/list`, `/po/summary`, `/bestseller/scope`, `/overview` on the seed, with volatile fields (timestamps) masked | unit 22, integration 33, e2e | Suites run from a clean clone with one command each; golden files committed |
| **R1 Infra** | `infra/db.py` (engine cached per process — the one intended behaviour change, approved under T5b), `infra/cache.py`, `infra/settings.py`, `infra/auth.py`, `infra/ddl.py` (all `ensure_*`) | all + golden identical | `app.py` imports infra; deploy OK |
| **R2 Domain** | Move `replenishment.py`, pure `metrics`, Holt/holdout, Pareto ranking into `domain/`; shims at old paths | unit (same assertions), golden identical | No DB import inside `domain/` (checked by a test that imports domain modules with no DB env) |
| **R3 Parsers** | `parsers/accurate_sales.py` (etl.py), `stock_report.py`, `po_master.py`, `sku_lists.py` | unit (PO parser, column mapping), integration uploads | Parsers have no DB access |
| **R4 Repositories** | Move SQL verbatim into `*_repo.py`; `_type_filter` whitelist kept | integration, golden identical | No `text("SELECT …")` left in services/routers |
| **R5 Services** | `inventory_service` (build_inventory), `forecast_service`, `scope_service`, `etl_service`, `sku_list_service`, `po_service`, `overview_service` | all + golden identical | Services hold all orchestration; `app.py` < 300 lines |
| **R6 Routers** | APIRouter per area (§5 of architecture); `app.py` = app, middleware, routers, page route | all + golden identical; e2e | `app.py` ≈ 80 lines; route list identical (test compares `app.routes` paths/methods with a stored list) |
| **R7 Cleanup** (U6) | Remove `index.html`, `calculate_replenishment`, `/api/coverage`, unused deps, shims; rewrite README | all | Repo has no unreachable modules (vulture/grep check) |
| **R8 Schema** | `migrations/0001_baseline.sql` (+ `0002` RLS if U1 approved, others per approval); `docs/database.md` updated | integration on a DB built from migrations only | Fresh DB from migrations passes integration tests |
| **R9 Frontend split** | CSS → `static/css` (tokens unchanged), JS → ES modules per page, `api.js` single fetch layer; markup unchanged | e2e (desktop light/dark, 390 px), screenshot diff on every page, no console errors | `dashboard.html` < 400 lines of markup; no JS file > 400 lines |
| **R10 Performance** | Inventory memo (versioned key, 5 min TTL); measure `/api/forecast/run` on prod data (U7) | golden identical; timing log | Page APIs < 3 s warm; forecast time recorded |

Phase 4 (dashboard redesign: Action Center, buckets, reasons, clickable KPIs, insufficient-data trend states) starts only after R6 and R9 are done, so new features land in the new structure.

## 4. Regression tests (detail)

Unit (existing 22, kept):
status boundaries; zero / low demand; negative stock; cost none ≠ 0; no PO; PO before/after stockout; unknown ETA; ETA estimated from order date; partial PO; zero outstanding ignored; PO column mapping; freshness levels; holdout insufficient history; **holdout no leakage**; protection beyond horizon; urgency thresholds; reco = thesis formula; movement split; order-by; PO master parser.

Integration (to be added from scratch suites):
PO upload mapping, day-first dates, superseded vs cancelled, duplicate upload; ETA sources; PO arrives in its ETA week; SS sensitivity 5/10/15%; 1 vs 2 snapshots (comparison, turnover); snapshot stored under report date; future date rejected; partial snapshot ignored for turnover; **inventory / replenishment / risk agree**; only 3 statuses; status counts add up; no-cost value is None; PO master: 808 lines, 34,045 open units, re-upload no duplicates, partial/over, higher received kept, receive-all; Hero: save counts, bundle/duplicate/unmatched/new, dotless normalised, stale warning, every Hero SKU forecast, Pareto unchanged, inventory shows all Hero SKUs.

Golden (new): byte-equal JSON (after masking timestamps) before and after every step R1–R6, R10.

E2E (existing scripts): login; every page loads; replenishment qty edit and export; detail panel; PO edit, close short, receive-all; scope list filters; 390 px no horizontal scroll; dark theme; no console errors.

## 5. Completion criteria (Phase 3)

1. All suites green from a clean clone: `pytest tests/unit`, `pytest tests/integration` (with `TEST_DATABASE_URL`), `node tests/e2e/run.js`.
2. Golden snapshots identical to R0 for every endpoint listed.
3. `app.py` ≤ 100 lines; no Python module > 400 lines except where justified in this file; no circular imports.
4. No SQL outside `repositories/` (except `infra/ddl.py` and migrations).
5. No formula outside `domain/`; the frontend contains no purchasing maths.
6. Production checked after each deploy: Overview, Replenishment, PO page, one upload of each type on real files.
7. `docs/architecture.md`, `docs/database.md` updated to the implemented state.

## 6. Risks and controls

| Risk | Control |
|---|---|
| Hidden behaviour change during a move | Verbatim moves + golden JSON diff per step |
| Vercel bundling of new folders / static files | Preview deploy before production for R1 and R9 |
| Long forecast run with Pareto ∪ Hero | Measure in R10; chunk or schedule if > 120 s (U7) |
| Thesis timeline | Steps are independent; the system remains usable after any of them, so work can stop at R6 if needed |
| Schema changes in prod | Only via approved migrations with verification queries (database.md §5) |

## 7. Effort estimate

| Steps | Estimate |
|---|---|
| R0 | 0.5 day |
| R1–R6 backend | 2–3 days |
| R7–R8 | 0.5–1 day |
| R9 frontend split | 1.5–2 days |
| R10 | 0.5 day |
