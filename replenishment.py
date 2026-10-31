"""
Stockwise — replenishment logic (the ONLY place these numbers are calculated).

Pure functions, no database access, so every page and every test uses exactly the same rules.

Definitions (see the audit document):
- Stock status: coverage = (stock + outstanding PO) / avg weekly sales of the last 4 weeks.
    <= 8 weeks Understocked · > 8–20 Healthy · > 20 Overstocked.
    No sales in 4 weeks: stock > 0 -> Overstocked (stock not moving); stock <= 0 -> not classified.
- Safety stock (thesis policy): 10% x forecast demand of the next 8 weeks.
- Reorder point: forecast demand during the lead time + safety stock.
- Negative stock (book stock below zero): kept as-is in the inventory position, so the deficit is
  re-ordered (same as before); the week-by-week projection starts at 0; valuation counts it as 0.
- Inventory position: physical stock + Confirmed Incoming that arrives within the window being planned. Confirmed = PO with an ETA (expected_date, or order_date + lead time).
  Unconfirmed Incoming (no ETA at all) is never counted, but shown next to the result.
- Recommended order (primary, thesis formula): max(0, forecast over lead time + SS - inventory position).
- Order-up-to (comparison only): max(0, forecast over lead time + review period + SS - inventory position).
- Stockout week: week-by-week projection, PO quantities added only in their ETA week.
- Purchase urgency: slack = stockout week - lead time (weeks).
    <= 0 Critical · <= R High (must be ordered at this review) · <= 2R Medium · > 2R Low.
"""

import math
from datetime import date, timedelta


# ── status ────────────────────────────────────────────────────

def coverage_weeks(stock, outstanding_po, avg_weekly):
    if not avg_weekly or avg_weekly <= 0:
        return None
    return round((stock + outstanding_po) / avg_weekly, 1)


def stock_status(coverage, stock, cfg):
    """Exactly 3 statuses. Returns None when a SKU cannot be classified (no sales and no stock)."""
    if coverage is None:
        return "overstocked" if stock > 0 else None
    if coverage <= cfg["understock_weeks"]:
        return "understocked"
    if coverage <= cfg["overstock_weeks"]:
        return "healthy"
    return "overstocked"


def overstock_qty(available, avg_weekly, cfg):
    """Quantity above the 20-week target. No sales -> everything available is excess."""
    if available <= 0:
        return 0
    if not avg_weekly or avg_weekly <= 0:
        return int(available)
    return int(max(0, round(available - cfg["overstock_weeks"] * avg_weekly)))


# ── forecast trend (relative) ─────────────────────────────────

def relative_trend(actual_recent, forecast):
    """(avg forecast next 8 weeks - avg actual previous 8 weeks) / avg actual previous 8 weeks."""
    a = [x for x in (actual_recent or [])[-8:]]
    f = (forecast or [])[:8]
    if not f:
        return None, "insufficient_baseline"
    fa = sum(f) / len(f)
    if not a or sum(a) == 0:
        return None, ("new_demand" if fa > 0 else "insufficient_baseline")
    aa = sum(a) / len(a)
    pct = round((fa - aa) / aa * 100, 1)
    return pct, ("increasing" if pct > 10 else "decreasing" if pct < -10 else "stable")


# ── incoming PO ───────────────────────────────────────────────

def _to_date(v):
    if v is None or isinstance(v, date):
        return v
    try:
        return date.fromisoformat(str(v)[:10])
    except ValueError:
        return None


def po_eta(po, lead_time_weeks):
    """ETA hierarchy: expected_date -> order_date + lead time -> unknown."""
    exp = _to_date(po.get("expected_date"))
    if exp:
        return exp, "expected"
    od = _to_date(po.get("order_date"))
    if od:
        return od + timedelta(weeks=lead_time_weeks), "estimated"
    return None, "unknown"


def week_index(d, week_starts):
    """0-based forecast week the date falls in. Before the first week -> 0 (due now / overdue).
    After the last week -> None (outside the forecast horizon)."""
    if d is None or not week_starts:
        return None
    if d < week_starts[0]:
        return 0
    for i, ws in enumerate(week_starts):
        if ws <= d < ws + timedelta(days=7):
            return i
    return None


# ── per-SKU plan ──────────────────────────────────────────────

def plan_sku(stock, pos, forecast_qty, forecast_weeks, cfg, lead_time_weeks=None, today=None):
    """
    stock          physical stock (latest snapshot)
    pos            [{'outstanding', 'expected_date', 'order_date', 'id', 'status', 'supplier'}]
    forecast_qty   weekly forecast quantities (12 weeks)
    forecast_weeks ISO week-start strings that go with forecast_qty
    """
    today = today or date.today()
    lt = int(lead_time_weeks or cfg["lead_time_weeks"])
    r_weeks = cfg["review_period_days"] / 7
    fc = [float(x or 0) for x in (forecast_qty or [])]
    horizon = len(fc)
    weeks = [_to_date(w) for w in (forecast_weeks or [])]
    flags = []

    # Incoming PO, split by ETA knowledge
    lines, arrivals = [], [0.0] * horizon
    confirmed_total = unconfirmed_total = 0
    for po in pos or []:
        q = max(0, int(po.get("outstanding") or 0))
        if q <= 0:
            continue
        eta, src = po_eta(po, lt)
        wi = week_index(eta, weeks) if eta else None
        overdue = bool(eta and eta < today)
        lines.append({"id": po.get("id"), "qty": q, "eta": str(eta) if eta else None, "eta_source": src,
                      "eta_week": (wi + 1) if wi is not None else None, "overdue": overdue,
                      "supplier": po.get("supplier"), "status": po.get("status")})
        if src == "unknown":
            unconfirmed_total += q
        else:
            confirmed_total += q
            if wi is not None:
                arrivals[wi] += q

    def arriving_within(n_weeks):
        return sum(arrivals[:min(n_weeks, horizon)])

    on_hand = max(0, int(stock or 0))
    if stock is not None and stock < 0:
        flags.append("negative_stock")

    ss_basis = min(cfg["ss_basis_weeks"], horizon)
    ss = round(cfg["ss_pct"] * sum(fc[:ss_basis]), 1)
    if lt > horizon:
        flags.append("lead_time_beyond_horizon")
    lt_demand = sum(fc[:min(lt, horizon)])
    rop = round(lt_demand + ss, 1)

    book = int(stock or 0)                      # may be negative: the deficit has to be re-ordered
    position = book + arriving_within(lt)
    reco = max(0, round(lt_demand + ss - position))
    reco_if_unconfirmed = max(0, round(lt_demand + ss - position - unconfirmed_total))

    prot = lt + int(math.ceil(r_weeks))
    if prot > horizon:
        flags.append("protection_beyond_horizon")
    prot_demand = sum(fc[:min(prot, horizon)])
    reco_out = max(0, round(prot_demand + ss - (book + arriving_within(prot))))

    # Week-by-week projection (conservative: unconfirmed PO never arrives)
    proj, level, stockout = [], float(on_hand), None
    for i in range(horizon):
        opening = level
        level = opening + arrivals[i] - fc[i]
        closing = max(0.0, level)
        proj.append({"week": i + 1, "week_start": forecast_weeks[i] if i < len(forecast_weeks) else None,
                     "opening": round(opening, 1), "demand": round(fc[i], 1),
                     "incoming": round(arrivals[i], 1), "closing": round(closing, 1),
                     "shortage": round(max(0.0, -level), 1)})
        if stockout is None and fc[i] > 0 and level <= 0:
            stockout = i + 1
        level = closing  # unmet demand is lost, it is not carried as negative stock

    # Optimistic: unconfirmed PO already on hand
    lvl, stockout_opt = float(on_hand + unconfirmed_total), None
    for i in range(horizon):
        lvl = lvl + arrivals[i] - fc[i]
        if stockout_opt is None and fc[i] > 0 and lvl <= 0:
            stockout_opt = i + 1
        lvl = max(0.0, lvl)

    urgency, slack = urgency_for(stockout, lt, r_weeks)

    # "Order by" date: the order must be placed lead-time weeks before the week stock runs out,
    # otherwise it arrives after the stockout. No stockout in the horizon -> earliest date it could matter.
    order_by = None
    if weeks and all(weeks):
        anchor = weeks[stockout - 1] if stockout else weeks[-1] + timedelta(days=7)
        order_by = anchor - timedelta(weeks=lt)

    return {
        "lead_time_weeks": lt, "review_period_weeks": round(r_weeks, 2),
        "safety_stock": ss, "lt_demand": round(lt_demand, 1), "rop": rop,
        "on_hand": on_hand, "confirmed_incoming": confirmed_total, "unconfirmed_incoming": unconfirmed_total,
        "inventory_position": round(position, 1),
        "below_rop": position < rop,
        "reco_qty": reco, "reco_if_unconfirmed_arrives": reco_if_unconfirmed,
        "reco_order_up_to": reco_out, "protection_weeks": prot,
        "stockout_week": stockout, "stockout_week_if_unconfirmed_arrives": stockout_opt,
        "slack_weeks": slack, "urgency": urgency,
        "stockout_date": str(weeks[stockout - 1]) if (stockout and weeks and weeks[stockout - 1]) else None,
        "order_by": str(order_by) if order_by else None,
        "order_by_days": (order_by - today).days if order_by else None,
        "order_by_is_estimate": stockout is None,
        "needs_po_check": unconfirmed_total > 0,
        "po_lines": lines, "projection": proj, "flags": flags,
    }


def urgency_for(stockout_week, lead_time_weeks, review_weeks):
    if stockout_week is None:
        return "low", None
    slack = stockout_week - lead_time_weeks
    if slack <= 0:
        return "critical", slack
    if slack <= review_weeks:
        return "high", slack
    if slack <= 2 * review_weeks:
        return "medium", slack
    return "low", slack


URGENCY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3, None: 4}


# ── safety-stock comparison (simulation only, never used in the recommendation) ──

def statistical_safety_stock(weekly_history, lead_time_weeks, z=1.65):
    """z x sigma_d x sqrt(L), sigma_d = std dev of weekly demand (last 26 weeks)."""
    h = [float(x) for x in (weekly_history or [])][-26:]
    if len(h) < 8:
        return None
    m = sum(h) / len(h)
    sd = math.sqrt(sum((x - m) ** 2 for x in h) / (len(h) - 1))
    return round(z * sd * math.sqrt(lead_time_weeks), 1)