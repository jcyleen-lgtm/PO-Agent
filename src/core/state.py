"""Per-process runtime state (one Vercel instance = one process).

These are caches only: a cold instance rebuilds them from the database, so they never decide a
result on their own. Tests call reset_runtime_state() after resetting the schema.
"""

import os

from etl import load_master_sku
from src.core.config import MASTER_SKU_PATH
from src.core.logging import get_logger

log = get_logger("state")

# description (lower case) -> SKU, from an optional MASTER_SKU.xlsx next to app.py plus uploads
# made on this instance. The products table is the main lookup; this is the fallback.
master_sku: dict = {}
if os.path.exists(MASTER_SKU_PATH):
    master_sku.update(load_master_sku(MASTER_SKU_PATH))
    log.info("Master SKU loaded: %s entries", len(master_sku))

processed_cache: dict = {}   # ETL previews waiting for confirm, keyed by file_id
forecast_cache: dict = {}    # "latest" (full forecast), "light" (dashboard fields), "light_calc_at"


def reset_runtime_state():
    """Forget every per-process cache (used by tests after the schema is rebuilt)."""
    from src.core import ddl
    processed_cache.clear()
    forecast_cache.clear()
    ddl.reset()
