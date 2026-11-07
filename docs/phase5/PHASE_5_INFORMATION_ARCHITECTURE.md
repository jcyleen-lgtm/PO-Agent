# Phase 5 — Stage 3: Information Architecture

Built only on data the API already returns (`/api/inventory/summary`, `/api/replenishment`,
`/api/best-sellers-at-risk`, `/api/overview`, `/api/forecast/*`, `/api/po/*`, `/api/db/stats`). No new metric, rule,
endpoint or field. Business rules referenced by section of `docs/spec.md`.

**Status: approved by San 2026-11-03 with the decisions in §6 (A1–A5); being implemented.**

---

## 1. Overview — target hierarchy

Goal: in 5–10 seconds the user knows (1) whether the data is current, (2) how healthy stock is, (3) which SKUs need a
purchasing decision now, and (4) where to click next.

```
┌───────────────────────────────────────────────────────────────────────────────────────────┐
│ Overview                                     Stock 3 Nov · Sales to 29 Oct · Forecast 3 Nov │ 1 Context
│ ⚠ Stock snapshot is 9 days old — upload this week's stock report  [Go to Data]  (only when) │
├───────────────────────────────────────────────────────────────────────────────────────────┤
│ 48 best-seller SKUs   ● Understocked 37  ● Healthy 6  ● Overstocked 3  ○ Not classified 2  │ 2 Health
│ ███████████████████████████████████████████▌██████▌███  · Out of stock 35 (included above)  │   (1 line + bar)
├───────────────────────────────────────────────────────────────────────────────────────────┤
│ Purchasing Action Center                                         [Open Replenishment →]   │ 3 Action
│ ┌ Order now ──────┐ ┌ Within 2 weeks ─┐ ┌ Hold ───────────┐ ┌ No action needed ┐        │   Center
│ │ 39 SKUs         │ │ 4 SKUs          │ │ 3 SKUs          │ │ 2 SKUs               │      │   (most
│ │ 46,409 units    │ │ 1,920 units     │ │ Rp 34.9 jt over │ │                      │      │   prominent)
│ │ ≈ Rp 374 jt ¹   │ │ ≈ Rp 18 jt ¹    │ │ stock value ¹   │ │                      │      │
│ └─────────────────┘ └─────────────────┘ └─────────────────┘ └──────────────────────┘      │
│ Top SKUs in the selected bucket (default: Order now) — 5 rows                            │
│ SKU / product        Order by        Stock+PO   Demand/wk   Recommended   Why            │
│ Produk 57 Rumah      10 Aug · 85d late   0         258        2,238      Stock runs out… │
│ …                                                                    [See all 39 →]      │
├───────────────────────────────────────────────────────────────────────────────────────────┤
│ Demand context (method-neutral)                                                           │ 4 Supporting
│ Forecast demand next 8 weeks: 52,522 units · 26 SKUs with a declining forecast          │   (one line,
│ Forecast reliability: see Product Analysis › Demand plan            [details →]          │    optional chart)
├───────────────────────────────────────────────────────────────────────────────────────────┤
│ Open POs: 3 POs · 1,687 units incoming · 9 lines overdue        [PO Tracking →]          │ 5 Supporting info
└───────────────────────────────────────────────────────────────────────────────────────────┘
¹ cost marked "unverified" when unit cost is not from master HPP (existing flag)
```

(Numbers are from the synthetic test data, for layout only.)

### 1.1 Section by section

| # | Section | Content | Source (existing) | Why it is here |
|---|---|---|---|---|
| 1 | Context | Page title; **three explicit dates**: stock snapshot, last sales date, forecast run; freshness warning only when stale (existing levels) with a link to Data | `/api/inventory/summary` → `freshness`, `stock_date`; forecast `calculated_at` | Fixes X6 ("Updated" ambiguity). The user must know whether to trust the page before acting. |
| 2 | Health summary | One line: total best-seller SKUs and the three statuses (§8) + not classified, counts and a stacked bar with labels; Out of stock as "included above" subset. Each count links to Replenishment filtered by status | `/api/inventory/summary` (`understocked`, `healthy`, `overstocked`, `unclassified`, `out_of_stock`, `total`) | Replaces 6 KPI tiles + Stock Health card + System Health ring (same numbers, one place, one denominator — fixes X5). |
| 3 | **Action Center** | 4 bucket cards with SKU count, units (Σ recommended qty) and value (Σ recommended qty × unit cost; for Hold: overstock value). Below: top 5 SKUs of the selected bucket sorted by spec §9.3 (order-by ascending, estimates last, then revenue). Each row: product, order-by + days late/left, stock + confirmed incoming, demand per week, recommended qty, reason text. Row → Replenishment with drawer open; bucket → Replenishment filtered | `/api/replenishment?product_type=import` rows: `order_by`, `order_by_days`, `order_by_is_estimate`, `status`, `reco_po`, `confirmed_incoming`, `unconfirmed_incoming`, `stockout_week`, `fc_avg_8w`, `avg_l4w`, `unit_cost`, `cost_verified` | The page's main job. Replaces Purchase Recommendations, Best Sellers at Risk (10 cards), most Insights, Financial Metrics "est. purchase cost / overstock value". |
| 4 | Demand context | One line: forecast demand for the next 8 weeks and the number of SKUs with a declining forecast; link to Product Analysis demand plan. No "accuracy %" ring. Labels say "forecast", not a method name | `/api/overview` → `forecast_summary` (`forecast_demand_8w`, `trending_down`) | Keeps forecast context without implying a method verdict (Holt vs MA under review; `docs/forecast-accuracy.md`). |
| 5 | Supporting info | One line on open POs (open, incoming units, overdue lines) linking to PO Tracking; "PO without ETA not counted" count when > 0 | `/api/po/summary`, summary `needs_po_check` | Incoming stock is part of the decision; detail lives in PO Tracking. |

**Action Center buckets** — exactly the approved rule set (spec §9.2, decision D2), first match wins:

| Bucket | Rule | Label in UI |
|---|---|---|
| Order now | `order_by_is_estimate = false` and `order_by_days ≤ 0` | "Order now" + days late |
| Within 2 weeks | `order_by_is_estimate = false` and `1 ≤ order_by_days ≤ 14` | "Order within 2 weeks" + date |
| Hold | `status = 'overstocked'`, or (`reco_po = 0` and `confirmed_incoming > 0`) | "Hold — enough stock / incoming" |
| No action needed | everything else | "No action needed" |

This is the first implementation of D2 (it is in the spec, not in the code). It is computed in the browser from
fields already in the API response — no API or business-rule change. It labels the situation; it does not store a
decision (no Order/Hold/Monitor state, per San).

### 1.2 Removed from Overview or moved

| Today | Decision | Reason |
|---|---|---|
| Sales KPI strip (total sales, revenue, avg weekly, active SKUs, trending up) | **move** to Product Analysis › Sales | historical sales, not a purchasing trigger |
| Revenue Trend, Sales Trend charts | **move** to Product Analysis › Sales | same |
| Top 10 Products | **move** to Product Analysis › Sales | same |
| Period filter (7D/4W/8W/Custom/Month) | **move** with the sales blocks | today it looks global but changes only sales blocks (audit §1) |
| Best Sellers at Risk (10 cards) | **replace** by Action Center list | same SKUs (understocked, earliest order-by first), easier to compare in rows |
| Insights list | **replace** by bucket reasons + context lines | repeats the same counts |
| System Health (accuracy ring, health ring) | **remove** | "100 − WMAPE" is not a valid accuracy figure; health repeats section 2 |
| Forecast Statistics card + Actual vs Forecast chart | **move** to Product Analysis › Demand plan (chart kept) | analysis, not daily trigger |
| Financial Metrics | **split**: purchase cost / overstock value → bucket cards; inventory value + turnover → Product Analysis › Stock | only the parts tied to a decision stay |
| Connection bar (sales rows, products, date range) | **move** to Data › status | data-management info |
| Best Seller Scope (Pareto chart + Hero tiles) | **remove from UI** for now (endpoint and data stay) | San: not a feature to surface; Pareto logic to be discussed |

Net effect: 12 blocks → 5 sections; the decision list is on the first screen.

## 2. Replenishment — target structure (function unchanged)

```
Replenishment                                          [Export order list ▾]  (all / selected)
[Order now 39] [Within 2 weeks 4] [Hold 3] [All 48]   Search…   Status ▾
┌──────────────────────────────────────────────────────────────────────────────────────────┐
│☐ Product            Status        Order by          Stock+PO  Demand/wk  Cover   Order qty │
│☑ Produk 57 Rumah     Understocked  10 Aug · 85d late    0         258     0 d    [2238]    │
│   299.002.17.001                                                          system 2,238     │
│ …                                                                                          │
├──────────────────────────────────────────────────────────────────────────────────────────┤
│ 37 selected · 46,409 units · ≈ Rp 374 jt (unverified)            [Export selected]         │ sticky
└──────────────────────────────────────────────────────────────────────────────────────────┘
                                               ┌ SKU drawer (right side) ─────────────────┐
                                               │ Produk 57 Rumah · Understocked · Order now│
                                               │ Why: stock runs out in week 1; order was  │
                                               │      due 10 Aug (85 days ago)             │
                                               │ Recommended 2,238 = lead-time demand 2,035│
                                               │   + safety stock 204 − position 0         │
                                               │ Stock 0 · Confirmed incoming 0 · Lead 8 wk│
                                               │ ▸ 12-week projection        (collapsed)   │
                                               │ ▸ Open PO lines             (collapsed)   │
                                               │ ▸ Method comparisons (order-up-to, SS     │
                                               │   scenarios)                (collapsed)   │
                                               │ [Demand history →]  [Close]               │
                                               └───────────────────────────────────────────┘
```

| Change | Type |
|---|---|
| Tabs become the Action Center buckets + All (same rule as Overview) | layout (uses existing fields) |
| Remove "All SKUs / Company list / Pareto only" filter; always all SKUs | per San |
| Add "Demand/wk" column (`fc_avg_8w`) so the quantity can be judged without opening the drawer | existing field |
| Detail moves from below the table into a right-side drawer; daily facts first, projection / PO lines / comparison tables collapsed | layout |
| Recommended quantity shown as its formula with the existing numbers (spec §9.1) | presentation only |
| Selection bar sticky at the bottom; pre-selection rule written next to it ("SKUs with a recommended qty are pre-selected") | clarity |
| Button wording "Export order list" (never "create PO") | clarity |
| No Order / Hold / Monitor input | per San |

## 3. Product Analysis — target structure

Purpose: investigation, entered from a SKU or from the menu.

| Sub-view | Content | From today |
|---|---|---|
| **SKU view** (drawer, shared with Replenishment and PO) | search/select SKU; sales history + forecast chart; stock, coverage, value; POs; "← Back to Replenishment" when opened from there | SKU drawer (modal) |
| **Stock & coverage** | portfolio table with 7 default columns (product, status, stock, open PO, coverage, avg/week, value); more columns via "Columns ▾"; one status filter (remove the extra "Understocked only" checkbox — the status filter already does it); inventory value + turnover line | today's table + Financial Metrics parts |
| **Demand plan** | monthly / weekly plan vs actual (unchanged), short explanation of WMAPE with a benchmark note, labels method-neutral; Actual-vs-forecast portfolio chart moved here | Demand plan + Overview forecast blocks |
| **Sales** | period filter, sales KPIs, revenue / units trend, top products | moved from Overview |

"Refresh forecast" moves to **Data**.

## 4. PO Tracking — target structure

| Area | Change |
|---|---|
| Summary strip | keep (open POs, incoming units, overdue, > 60 days, lead time) |
| Status legend | one legend explaining the existing states: Not received · Partial · Completed · Closed short · Cancelled; filters keep their current meaning (Open, Overdue, > 60 days, Short/Over, Completed, All) |
| Rows | received qty stays inline; date and "close short / cancel" move into a row menu with a confirm step (same API calls) |
| Dates | one display format (`10 Aug 2026`) everywhere; input keeps the browser picker |
| Title | "PO Tracking" (data has one agent; no supplier workflow is invented) |
| Delivery performance | keep, collapsed by default |

## 5. Data — target structure

| Group | Items |
|---|---|
| Status | latest stock snapshot, latest sales date, forecast run, products — with freshness; moved connection info |
| Weekly routine | 1 Sales (preview → confirm, unchanged) · 2 Stock snapshot (date) · 3 Refresh forecast (moved here) |
| When it changes | PO master list · Hero SKU list · Food SKU list · Master SKU |
| Removed | "PO Receipts — Coming in P2" placeholder (no function) |

Every upload card shows the same steps: choose file → (existing validation) → result with counts and errors, next step
hint. Upload rules and processing are unchanged; only messages and layout.

## 6. Decisions (San, 2026-11-03)

| ID | Decision | How it is implemented |
|---|---|---|
| A1 | **Approved, label revised**: show the Action Center buckets; "No action this cycle" → **"No action needed"**; keep the approved rules; counts must not double-count | One function `bucketOf(row)` (spec §9.2, first match wins) used by Overview and Replenishment. Every SKU row gets exactly one bucket, so the four counts always add up to the number of SKUs. |
| A2 | **Approved**: SKU explanation in the frontend, deterministic, rule-based, only from fields the API already returns; fallback when data is insufficient; no API change; no AI text | `explainSku(row)` builds sentences from fixed templates (spec §9.4 patterns) using `stockout_week`, `order_by`, `order_by_days`, `order_by_is_estimate`, `reco_po`, `lt_demand_8w`, `safety_stock`, `inventory_position`, `confirmed_incoming`, `unconfirmed_incoming`, `coverage`, `status`, `fc_method`, `flags`. Missing inputs → "Not enough data to explain this recommendation." |
| A3 | **Approved to hide temporarily**: no Pareto UI or indicators; Pareto must not influence Action Center priority; keep implementation and data | Overview scope card, Replenishment "Company list / Pareto only" filter and Hero/Pareto tags removed from the UI. API `/api/bestseller/scope`, the data and the forecast scope rule are untouched; the removed UI code stays in git history (`main@08fca67`, `static/js/pages/overview.js` scope section). Action Center sort uses only order-by date, estimate flag and L4W revenue (spec §9.3). |
| A4 | **Deferred**: no reliability / confidence labels until F2–F5 are decided | No High/Medium/Low labels. Existing facts stay visible as before (e.g. the "est." tag for the naive fallback, per-SKU WMAPE in the demand plan). The "accuracy %" ring is removed, not replaced. |
| A5 | **Not required**: no decision saving, no new tables / endpoints / workflows | No Order / Hold / Monitor input. Visible actions: filter, open detail, edit quantity, select, export order list — all existing functions. |

## 7. Verification against the API (2026-11-03)

Checked on the synthetic test data through the real endpoints (`/api/replenishment`, `/api/inventory/summary`,
`/api/po/summary`, `/api/overview`):

- Field names used: `reco_po` (recommended qty; the spec's `reco_qty` is its name inside `plan_sku`), `order_by_days`,
  `order_by_is_estimate`, `status` (`understocked` / `healthy` / `overstocked` / `null` = not classified),
  `confirmed_incoming`, `unconfirmed_incoming`, `stockout_week`, `fc_avg_weekly`, `l4w_revenue`, `unit_cost`,
  `cost_verified`, `overstock_value`, `out_of_stock`; summary `freshness` (`stock_date`, `stock_age_days`,
  `stock_level`, `last_sale_date`); `forecast_summary.forecast_demand_8w`, `trending_down`, `calculated_at`;
  PO summary `open_po_numbers`, `outstanding_qty`, `overdue`.
- **Single source for counts**: health line and buckets are both computed from the `/api/replenishment` rows (44 rows
  = inventory summary total 44; status counts 37 / 6 / 0 / 1 and out-of-stock 34 match the summary). Buckets on the
  test data: Order now 39 · Within 2 weeks 0 · Hold 2 · No action needed 3 = 44.
- Edge case found: a SKU can be **Order now with recommended qty 0** (order-by date passed because the projected
  stockout is in week 10–12, but current stock still covers the 8-week lead-time demand + safety stock). The
  explanation states this explicitly; quantities are not changed.
- Replenishment's old "Order now" tab used a different rule (`reco_po > 0` or understocked or order-by ≤ 14 days).
  To keep **one** definition, the tabs become the four buckets + All. Pre-selection for export keeps its existing
  rule (needs order and `reco_po > 0`) and is now stated next to the selection bar.
