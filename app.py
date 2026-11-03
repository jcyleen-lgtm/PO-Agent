"""
Stockwise — Inventory & Purchasing Decision System (formerly PO Agent).

Entry point for Vercel (`vercel.json` builds this file) and for `uvicorn app:app`.
This module only wires the application together:

    src/core          infrastructure: config, database engine, runtime caches, runtime DDL, logging
    src/parsers       Excel parsing (Accurate, PO master list, SKU lists) — no database
    src/repositories  SQL shared between services
    src/services      use cases (inventory, purchasing, forecast, products, imports, PO, overview)
    src/routers       HTTP endpoints (paths, parameters, responses unchanged)

Business rules stay in the top-level pure modules: replenishment.py (status, SS, ROP, reco, order-by),
metrics.py (cost, movement, freshness, turnover), forecast.py (Holt's DES, Pareto ∪ Hero scope), etl.py.
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from auth import require_login, router as auth_router
from src.core import config  # noqa: F401  (loads .env and creates the upload folder first)
from src.routers import (forecast as forecast_router, inventory as inventory_router, overview as overview_router,
                         products as products_router, purchase_orders as po_router, purchasing as purchasing_router,
                         sales as sales_router)

app = FastAPI(title="Stockwise", version="4.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
app.middleware("http")(require_login)   # every /api/* route needs a login cookie (see auth.py)
app.include_router(auth_router)         # /api/auth/login, /api/auth/logout, /api/auth/me
# Frontend CSS/JS (no data; same exposure as the page itself). On Vercel the CDN serves /static first.
app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/", response_class=HTMLResponse)
async def dashboard_page():
    with open("templates/dashboard.html", "r", encoding="utf-8") as f:
        return f.read()


for _r in (products_router, sales_router, purchasing_router, inventory_router, forecast_router,
           overview_router, po_router):
    app.include_router(_r.router)


# Backward-compatible names (tests and one-off scripts import them from app).
from src.core.database import get_engine, get_raw_conn  # noqa: E402,F401
from src.core.state import forecast_cache, reset_runtime_state  # noqa: E402,F401
from src.parsers.po_master import _is_po_master, _parse_po_master, map_po_columns  # noqa: E402,F401
from src.services.forecast_service import forecast_summary_fast, get_forecast_light  # noqa: E402,F401
from src.services.inventory_service import build_inventory  # noqa: E402,F401

__all__ = ["app", "config", "get_engine", "get_raw_conn", "forecast_cache", "reset_runtime_state", "_is_po_master",
           "_parse_po_master", "map_po_columns", "forecast_summary_fast", "get_forecast_light", "build_inventory"]


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
