# Stockwise

Inventory & purchasing decision system for PT X's import best sellers: Holt's DES forecast,
coverage status (≤ 8 / 8–20 / > 20 weeks), order-by date and recommended order quantity, PO tracking.
FastAPI + Supabase Postgres, deployed on Vercel (`app.py` is the entry point).

Documentation: `docs/spec.md` (rules and KPIs), `docs/architecture.md`, `docs/database.md`,
`docs/api-inventory.md`, `docs/refactoring-plan.md`, `docs/audit.md`.

## Layout

```
app.py              FastAPI app: middleware, page route, routers
auth.py             login + /api/* protection
config.py           planning parameters (env SW_* overrides)
replenishment.py    purchasing rules (pure)      metrics.py   cost / turnover / freshness
forecast.py         Holt's DES + Pareto ∪ Hero    etl.py       Accurate sales cleaning
src/core            config, database engine, runtime caches, runtime DDL, logging
src/parsers         Excel readers (PO master list, SKU lists)
src/repositories    shared SQL
src/services        use cases (inventory, purchasing, forecast, products, imports, PO, overview)
src/routers         HTTP endpoints
templates/          dashboard.html (single-page UI)
tests/              unit, integration, regression
```

## Run locally

```
pip install -r requirements.txt
```
Create `.env` (never commit it):
```
SUPABASE_DB_URL=postgresql://...
APP_USERNAME=...
APP_PASSWORD_HASH=...      # python make_password_hash.py
SESSION_SECRET=...         # 40+ random characters
```
```
uvicorn app:app --reload
```

## Tests

Unit tests need nothing:
```
python -m pytest tests/unit -q
```
Integration and regression tests need an **empty local** PostgreSQL database (they reset it; Supabase URLs are refused):
```
createdb stockwise_test
TEST_DATABASE_URL=postgresql://postgres@localhost:5432/stockwise_test python -m pytest -q
```
`tests/regression/test_old_vs_new.py` runs the same 248 requests on the reference commit
(`REGRESSION_BASE_REF`, default `e6e1e5c`) and on the working tree and requires identical responses.

## Deploy

Push to `main`; Vercel builds `app.py` (region `sin1`). Environment variables are set in the Vercel project.
