# Revision R7: Dashboard, Replenishment, Product Analysis, PO Tracking (Stage 1–3)

Status: **for approval, no code changed yet.** Incremental revision of the current app
(branch `overview-v2` = Phase 5 + Overview v2, the version running in production since 4 Nov 2026).
Numbers below are from the production database on 4 Nov 2026 (read-only), computed with the backend's own
functions (`replenishment.plan_sku`).

## 0. Base branch (needs a decision)

The brief says "branch from the latest main". `main` is still Phase 4 (`08fca67`); Phase 5 and Overview v2
exist only on `phase5-ux` / `overview-v2`, and a deployment of `overview-v2` is currently Production.
Branching from `main` would drop both and re-introduce the old Overview. **Proposal:** branch `r7-revision`
from `overview-v2`, and merge `overview-v2` into `main` first (fast-forward) once you approve.

## 1. Stage 1: Requirement mapping

Legend: **OK** = existing data and endpoint; **FE** = frontend only; **GAP** = data does not exist or needs a decision.

### 1.1 Overview

| Requirement | Source | Status |
|---|---|---|
| Header, last updated | `freshness`, `forecast_summary.calculated_at` | OK (exists) |
| Filters Store, Platform | none: the Accurate sales export has no store/platform column, no table has one | **GAP**: not possible without new data |
| Filters Month / Year / Week for inventory | inventory endpoints take no date; stock = latest snapshot only | **GAP**: inventory cannot be filtered by date |
| Month filter for sales figures | `/api/overview?start&end` (already used by Sales) | OK, FE: applies to Monthly Sales and Best-seller contribution only |
| 5 KPI cards (Total, Under, Healthy, Over, Out of stock) | `/api/replenishment` rows | OK, FE. Note: the current **Stock Value** card is not in the list and would be removed |
| Demand: Actual vs Forecast, same periods | `/api/forecast/plan`: per SKU 8 **holdout** weeks (forecast from a model trained without those weeks, next to actual) + 12 forecast weeks | OK, FE. Weekly. Past forecasts older than the 8 holdout weeks are **not stored** (each run overwrites) |
| Monthly Sales across periods | `/api/overview` with a range > 90 days returns `trend` per **month** (revenue, units) | OK, FE |
| Best-selling products + revenue contribution % | `/api/overview` `top_products` (top 10, `top_sort=revenue`) and `kpis.revenue` for the same month | OK, FE. Ranking **by revenue**; N fixed at 10 (the API returns 10) |
| Pending POs, Last sales, Last stock, Data status | `/api/po/summary`, `freshness` | OK (exists) |
| Priority SKUs with Forecast/Week | rows: `fc_avg_weekly` | OK, FE |
| Inventory Insights charts | rows | OK (exists) |

### 1.2 Replenishment Planning

| Requirement | Source | Status |
|---|---|---|
| Tabs All / Order Now / Hold / No Action Needed by **status** | `status` per row | FE. **Changes the approved A1 classification** (order-by based) to status based, see §3 |
| Remove "Upcoming orders" | | FE |
| Understocked mandatory in export, Healthy / Overstocked optional via checkbox | existing selection (`x.sel`) and quantity inputs | FE, with two conflicts (§3.2) |
| **Excel** export with "Mandatory / Manually selected" column | today: CSV | FE: `.xlsx` needs a client library (SheetJS 0.18.5 from cdnjs). No backend change |
| "Not classified" SKUs (no sales, no stock: 4 today) | | **Decision**: proposed All tab only |

### 1.3 Forecast vs Actual Sales vs Actual Ordered Quantity

| Series | Source | Notes |
|---|---|---|
| Forecast demand | `/api/forecast/plan`: holdout forecast (8 past weeks) + forecast (12 future weeks) | No forecast history before the holdout window |
| Actual sales | `/api/forecast/plan` holdout actual; per SKU longer history `/api/product/{sku}/history` | weekly |
| Ordered quantity | `/api/po/list?status=all`: `qty_ordered`, `order_date`, `expected_date` | **Alignment decision** (§3.4). 323 lines (all cancelled/superseded) have no order date |

Placement: new section on **PO Tracking** ("Purchasing vs Forecast"), with SKU picker (all best sellers or one SKU).

### 1.4 Product Analysis

| Requirement | Status |
|---|---|
| Forecast vs Actual per SKU (holdout weeks vs actual, horizon separate, over / under per week) | OK, FE: `/api/forecast/plan` already has it. No reliability labels |
| Table column order (15 columns) | OK, FE: all 15 columns already exist, only the order changes |
| Sorting, sticky header, horizontal scroll, search / filters kept | FE: sorting by header click is new; the rest exists |

### 1.5 PO Tracking

| Requirement | Source | Status |
|---|---|---|
| Month / year period filter (default latest) | `/api/po/list?status=all` `order_date` | OK, FE. Months with POs: Jan, Mar–Oct 2026 |
| Lines without order date | 323 lines, all cancelled / superseded | needs a bucket: proposed "No order date" option |
| Summary: PO count, ordered qty, pending count | distinct `po_number`, Σ `qty_ordered`, status | OK, FE (1–2 PO numbers per month today) |
| Total PO value | **no price field** on `purchase_orders` | **GAP**: omit, or show an *estimate* (qty × unit cost from COGS), labelled as estimate |
| Supplier column / search | `supplier` is empty on all lines | GAP (data): column would be empty; proposed hide until filled |
| Expected arrival, received qty, status | exists | OK |

## 2. Stage 2: Page structure (wireframes)

### Overview (order as requested)
```
┌ Inventory Overview · description ···· [Stock snapshot 2 Nov] [Forecast 3 Nov 13:46] ┐
│ KPI: Total SKUs │ Understocked │ Healthy │ Overstocked │ Out of Stock (subset)      │
├──────────────────────────────────────────────┬─────────────────────────────────────┤
│ Demand — Actual vs Forecast (weekly)          │ Operational Summary   [Month ▾]     │
│ 8 holdout wks: actual ── vs forecast - -      │ Monthly sales  Rp …  (bar per month)│
│ | forecast horizon 12 wks - -                 │ Top 10 by revenue = 45% of revenue  │
│ tooltip: exact units                          │ Pending POs · Last sales · Stock ·  │
│                                               │ Data status                          │
├──────────────────────────────────────────────┴─────────────────────────────────────┤
│ Priority SKUs (Understocked first, by order-by date) · SKU, Product, Status, Stock, │
│ Coverage, Incoming PO, Forecast/Wk, Reco PO · View all →                            │
├────────────────────────────────────────────────────────────────────────────────────┤
│ Inventory Insights: Health distribution │ Coverage distribution                    │
└────────────────────────────────────────────────────────────────────────────────────┘
```
"What Needs Your Attention?" groups move to Replenishment tabs (counts stay visible there); proposal: keep
a one-line count strip under the KPIs linking to the tabs, so the Overview still shows how many to order.

### Replenishment Planning
```
[All 194] [Order Now 32] [Hold 56] [No Action Needed 102]   search · status filter
☑ (locked) SKU · Product · Status · Stock · Coverage · Incoming · Forecast/Wk · Reco · Units to order · Source
   Order Now rows: checkbox ticked and locked, "Mandatory"
   Hold / No Action rows: checkbox off; tick = "Manual"
Sticky bar: 32 mandatory + 3 manual SKUs · 4,157 units · ≈ Rp …   [Export order list (.xlsx)]
```

### Product Analysis
- Stock & Coverage table, columns exactly: SKU · Product · Status · Coverage · Stockout · Stock · Open PO ·
  Forecast/Wk · Reco PO · Movement · Days Inv. · Overstock · Value · Avg/Wk L4W · L4W Revenue; sortable
  headers, sticky header and first column, horizontal scroll.
- SKU detail: chart "Forecast vs Actual" (8 holdout weeks: actual bars, forecast line, difference per week
  marked over / under; then 12 forecast weeks shaded) + a short line: "Forecast was above actual in 5 of 8
  weeks; total forecast 312 vs actual 280 (+11%)".

### PO Tracking
```
[‹] [October 2026 ▾] [›]   search PO number / SKU
Summary (selected month): PO numbers · Lines · Ordered qty · Pending lines
Table: PO Number · Order date · SKU / Product · Ordered · Received · Expected arrival · Status
Purchasing vs Forecast: [All best sellers ▾ | SKU] weekly: forecast · actual sales · ordered qty
```

## 3. Stage 3: Business logic validation (production data, 4 Nov 2026)

### 3.1 New classification
Understocked → Order Now (32 SKUs) · Healthy → Hold (56) · Overstocked → No Action Needed (102) ·
Not classified (4) → All only. Sum = 194. Mutually exclusive by construction.

This replaces the approved A1 rule (order-by date). Thesis impact: the purchasing trigger becomes
**coverage from the last 4 weeks of actual sales**, while the recommended quantity stays **forecast-based**.
The two disagree for some SKUs:

| Status | SKUs | Recommended qty > 0 | Recommended 0 |
|---|---:|---:|---:|
| Understocked (Order Now) | 32 | 19 | **13** |
| Healthy (Hold) | 56 | **6** (2,936 units) | 50 |
| Overstocked (No Action) | 102 | 0 | 102 |

### 3.2 Conflicts to decide
1. **13 Understocked SKUs with recommendation 0** (forecast says stock + PO is enough, mostly declining
   forecasts; 4 already have an open PO). "Mandatory in export" with 0 units adds rows nobody will order.
2. **6 Healthy SKUs with recommendation > 0** (2,936 units). Under the new rule they sit in Hold, unselected,
   and would be missed unless someone ticks them. 15 Healthy SKUs also have an order-by date that has passed.
3. Overview: replacing the Stock Value card with Healthy SKUs removes the only inventory-value figure on the page.

### 3.3 Calculations
- **Monthly Sales** = Σ `sales.amount` of import products with invoice date in the month (same as Sales KPI
  "Revenue"). Latest month (Oct 2026) is partial: sales data ends 29 Oct.
- **Best-seller revenue contribution (%)** = Σ revenue of the top 10 SKUs **ranked by revenue** in the month
  ÷ total revenue of the month × 100. Not mixed with quantity ranking.
- **Forecast vs Actual** = per week, Σ holdout forecast vs Σ actual over the best-seller SKUs (8 weeks), then
  Σ forecast for 12 weeks. Same weekly buckets (Monday weeks) on both series.
- **Forecast vs Actual Ordered Qty**: ordered qty (status not cancelled/superseded) summed per week.

### 3.4 PO alignment (decision)
POs are placed 1–2 times a month and cover about 8 weeks of demand, so ordered qty per **order week** shows
spikes that never line up with weekly demand. Options: (a) by **expected arrival week** (when the goods can
be sold; closest to the demand period), (b) by order week, (c) both with a toggle. The chart will state that
a difference between the lines is not an error by itself (stock, open POs and lead time also decide the qty).

## 4. What does not change
Backend, API contracts, database, Holt's DES, coverage, safety stock, lead time, recommended quantity,
PO status logic, sidebar and pages. No new endpoint is needed for anything in §1 except the two GAP items
(Store/Platform data, PO price), which are left out.
