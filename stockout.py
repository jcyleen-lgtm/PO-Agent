"""
Stockwise — stockout (censored demand) correction for the Pareto best-seller cut.

A week with no sales is not always a week with no demand: when the item is out of stock nothing can be sold.
Ranking SKUs on raw revenue then pushes a best seller that ran out down the list, and it can drop out of
the Pareto 80% scope exactly when it most needs a purchase order.

Rule (applied only to the Pareto ranking; Holt's DES, coverage and the recommended quantity are unchanged):

  A run of consecutive zero-sales weeks inside the Pareto window is a STOCKOUT when
    1. it is at least `MIN_RUN` weeks long,
    2. the SKU was selling steadily just before it: sales in at least `MIN_ACTIVE` of the `BASELINE` weeks
       immediately before the run, and
    3. there is evidence that the item was out of stock:
       a. a stock snapshot of 0 (or no stock) in a week of the run, or
       b. a PO receipt in a week of the run or the week after it, and sales resume right after the run.

  Each stockout week is credited with the SKU's average weekly revenue of the `BASELINE` weeks before the run.
  Weeks with sales are never changed; a run without evidence stays 0 (the item may simply not have sold).
"""

from datetime import timedelta

MIN_RUN = 2       # weeks
BASELINE = 4      # weeks before the run used as the "normal" level
MIN_ACTIVE = 3    # of the BASELINE weeks must have sales


def detect_runs(weeks, revenue, receipt_weeks=(), zero_stock_weeks=(), window_start=None):
    """Stockout runs of one SKU.

    weeks: every week start (date) in order, including the BASELINE weeks before the window.
    revenue: {week: revenue} (missing = 0).
    receipt_weeks / zero_stock_weeks: week starts with a PO receipt / a zero stock snapshot.
    window_start: first week of the Pareto window; only zero runs that start on or after it are checked.
    Returns [{"start", "end", "weeks", "baseline", "evidence"}]."""
    rec, zero = set(receipt_weeks), set(zero_stock_weeks)
    sold = [float(revenue.get(w, 0) or 0) > 0 for w in weeks]
    out, i, n = [], 0, len(weeks)
    while i < n:
        if sold[i]:
            i += 1
            continue
        j = i
        while j + 1 < n and not sold[j + 1]:
            j += 1
        run = weeks[i:j + 1]
        if (window_start is None or run[0] >= window_start) and len(run) >= MIN_RUN and i >= BASELINE:
            base = weeks[i - BASELINE:i]
            active = sum(sold[k] for k in range(i - BASELINE, i))
            if active >= MIN_ACTIVE:
                resumed = j + 1 < n and sold[j + 1]
                after = run[-1] + timedelta(weeks=1)
                if any(w in zero for w in run):
                    ev = "zero_stock"
                elif resumed and any(w in rec for w in run + [after]):
                    ev = "po_receipt"
                else:
                    ev = None
                if ev:
                    out.append({"start": run[0], "end": run[-1], "weeks": len(run),
                                "baseline": sum(float(revenue.get(w, 0) or 0) for w in base) / BASELINE,
                                "evidence": ev})
        i = j + 1
    return out


def adjusted_revenue(weekly, receipts, zero_stock, window_start, last_week):
    """Pareto ranking revenue per SKU over [window_start, last_week].

    weekly: {sku: {week: revenue}} (may include weeks before the window, used for the baseline).
    receipts / zero_stock: {sku: set(week)}.
    Returns {sku: {"revenue": actual, "adjusted": actual + imputed, "stockout_weeks": n, "runs": [...]}},
    only for SKUs with actual revenue > 0 in the window."""
    first = window_start - timedelta(weeks=BASELINE)
    weeks, w = [], first
    while w <= last_week:
        weeks.append(w)
        w += timedelta(weeks=1)
    out = {}
    for sku, rev in weekly.items():
        actual = sum(float(v or 0) for k, v in rev.items() if window_start <= k <= last_week)
        if actual <= 0:
            continue
        runs = detect_runs(weeks, rev, receipts.get(sku, ()), zero_stock.get(sku, ()), window_start)
        imputed = sum(r["baseline"] * r["weeks"] for r in runs)
        out[sku] = {"revenue": actual, "adjusted": actual + imputed,
                    "stockout_weeks": sum(r["weeks"] for r in runs), "runs": runs}
    return out


def pareto_revenue(conn, window_weeks, enabled=True):
    """Load import sales, PO receipts and zero stock snapshots, and return (window_start, last_week, ranking)
    where ranking = adjusted_revenue(...) (or actual revenue only when `enabled` is False)."""
    from src.repositories import forecast as forecast_repo
    df = forecast_repo.weekly_import_sales_df(conn)
    if df.empty:
        return None, None, {}
    import pandas as pd
    df["week_start"] = pd.to_datetime(df["week_start"]).dt.date
    last_week = df["week_start"].max()
    window_start = last_week - timedelta(weeks=int(window_weeks) - 1)
    weekly = {}
    for sku, wk, amt in df[["sku", "week_start", "total_amount"]].itertuples(index=False):
        weekly.setdefault(sku, {})[wk] = float(amt or 0)
    rec, zero = {}, {}
    if enabled:
        for sku, wk in forecast_repo.po_receipt_weeks(conn):
            rec.setdefault(sku, set()).add(wk)
        for sku, wk in forecast_repo.zero_stock_weeks(conn):
            zero.setdefault(sku, set()).add(wk)
    return window_start, last_week, adjusted_revenue(weekly, rec, zero, window_start, last_week)
