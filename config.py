"""
Stockwise — planning parameters in one place.

Defaults below are the values documented in the thesis methodology. Each one can be overridden
with an environment variable (Vercel → Settings → Environment Variables), so nothing is hardcoded
across the codebase. A later migration moves these into an `app_settings` table.
"""

import os


def _num(name, default, cast=float):
    try:
        return cast(os.getenv(name, default))
    except (TypeError, ValueError):
        return cast(default)


def settings():
    """Read on every call so a changed env var takes effect without code changes."""
    return {
        # Supplier lead time used when a SKU has no specific value (default assumption, not measured).
        "lead_time_weeks": _num("SW_LEAD_TIME_WEEKS", 8, int),
        # Purchasing reviews orders about twice a month, no fixed dates -> rolling 14-day period.
        "review_period_days": _num("SW_REVIEW_PERIOD_DAYS", 14, int),
        # Safety stock policy: 10% of the forecast demand over `ss_basis_weeks` (thesis basis: 8 weeks).
        "ss_pct": _num("SW_SAFETY_STOCK_PCT", 0.10),
        "ss_basis_weeks": _num("SW_SS_BASIS_WEEKS", 8, int),
        # Stock coverage status thresholds (weeks).
        "understock_weeks": _num("SW_UNDERSTOCK_WEEKS", 8),
        "overstock_weeks": _num("SW_OVERSTOCK_WEEKS", 20),
        # Data freshness: stock snapshot older than this is flagged.
        "stale_warn_days": _num("SW_STALE_WARN_DAYS", 7, int),
        "stale_block_days": _num("SW_STALE_BLOCK_DAYS", 14, int),
        # Unit cost window and whether finance has confirmed cogs_accurate = HPP.
        "cost_window_weeks": _num("SW_COST_WINDOW_WEEKS", 12, int),
        "cost_verified": os.getenv("SW_COST_VERIFIED", "false").strip().lower() in ("1", "true", "yes"),
        # Fast / Medium / Slow moving split by rank of avg weekly units (top share, middle share; rest = slow).
        "fast_share": _num("SW_FAST_SHARE", 0.20),
        "medium_share": _num("SW_MEDIUM_SHARE", 0.50),
        "movement_window_weeks": _num("SW_MOVEMENT_WINDOW_WEEKS", 12, int),
    }
