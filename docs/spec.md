# Stockwise — Product Specification

Version 1.0 · 2026-11-03 · based on `docs/audit.md` (repo `e6e1e5c`, production DB as read on 2026-11-03).

Every rule below is marked:

- **[Current]** — implemented and running in production today.
- **[Proposed]** — approved for a later phase; not implemented yet.
- **[Open]** — needs a decision (see §15).

---

## 1. Product overview

Stockwise is the inventory and purchasing decision system for PT X's import products. It turns Accurate exports (sales, stock), the purchasing PO list and the company Hero SKU list into one answer per best-seller SKU: **what to buy, by when, and how much**.

Objectives:

1. Show which best sellers will run out, and when to order so goods arrive in time.
2. Recommend an order quantity from a documented formula that purchasing can override.
3. Show which products tie up capital in excess stock.
4. Track open purchase orders and actual supplier lead time.
5. Make every number traceable to its formula and data date.

Out of scope: creating POs (done in Accurate), food products (excluded by design), AI chat, multi-user roles, marketplace pricing/margin.

## 2. Users and workflows

| User | Need | Main pages |
|---|---|---|
| Purchasing staff | Weekly order decisions, PO follow-up | Replenishment, PO & Supplier, Upload Data |
| Purchasing / marketing supervisor | Health of best sellers, excess stock, capital | Overview, Product Analysis |
| Thesis examiner | Method transparency (Pareto, Holt's DES, safety stock) | Overview scope card, Product Analysis |

Weekly workflow **[Current]**:

1. Upload sales (Accurate *Sales By Item Detail*) → preview → confirm.
2. Upload stock snapshot with its **report date**.
3. Upload the PO master list.
4. Run **Refresh forecast**.
5. Replenishment → *Order now* tab → review → adjust *Units to order* → export → create PO in Accurate.
6. PO & Supplier → record received quantities, follow up lines older than 60 days.

Occasional: Master SKU, Food SKU list, Hero SKU list uploads.

## 3. Scope of analysis

**Best-seller scope [Current, D6]** = Pareto 80% ∪ Hero SKU list.

| Part | Rule |
|---|---|
| Product type | `products.product_type = 'import'` (missing type counts as import). Food products are tagged by the Food SKU list and their sales moved to `sales_excluded`. |
| Pareto set | Rank import SKUs by total revenue (`SUM(sales.amount)`) over **all sales history in the database**. Include SKUs while the cumulative revenue *before* them is below 80% of total (the SKU that crosses 80% is included). |
| Forecast eligibility (Pareto SKUs) | ≥ 1 sale in the last 8 weeks, sales in ≥ 3 of the last 12 weeks, ≥ 4 weekly points, non-zero total. Others are excluded from the forecast and shown in the scope list with the reason. |
| Hero SKU list | Every SKU on the uploaded list is in scope regardless of Pareto rank. If Holt's DES cannot run (no sales, inactive, < 4 weeks, fit failure) it gets a **naive forecast** = mean of its last 4 weekly quantities, flat for 12 weeks, marked `est.`. |
| Decision scope | Purchasing pages (Replenishment, Action Center) use only the best-seller scope. Expanding it needs approval. |

Inventory analytics may show all import SKUs where stated (e.g. turnover), but no purchasing recommendation outside the scope.

## 4. Functional requirements

| ID | Requirement | State |
|---|---|---|
| FR-01 | Login with one configured account; all `/api/*` routes need the session cookie | Current |
| FR-02 | Sales ETL with preview, duplicate skip, footer removal, food routing | Current |
| FR-03 | Stock snapshot upload with report date (not in the future); replaces rows of that date | Current |
| FR-04 | Master SKU, Food SKU, Hero SKU list uploads | Current |
| FR-05 | Forecast run: Pareto ∪ Hero, Holt's DES + naive fallback, metrics saved | Current |
| FR-06 | Per-SKU purchasing plan (status, coverage, SS, ROP, reco, projection, order-by) | Current |
| FR-07 | Replenishment table: tabs All / Order now, status and source filters, editable qty, export selected, detail panel | Current |
| FR-08 | PO tracker: master-list import (upsert by line key), edit received, close short / cancel / reopen, receive-all, aging, overdue | Current |
| FR-09 | Delivery performance: fill rate, actual lead time (mean/median/p90), variance, stockout rate | Current |
| FR-10 | Best-seller scope card: Pareto curve, counts, SKU list with source and forecast method | Current |
| FR-11 | Data freshness banner on every decision page | Current |
| FR-12 | **Action Center** on Overview: four buckets with counts, value and SKU list (§9) | Proposed |
| FR-13 | **Priority** column = Order-by bucket (§9), replacing urgency in the UI and API docs | Proposed |
| FR-14 | Each recommendation carries a **reason** string built from its plan fields (§9.4) | Proposed |
| FR-15 | KPI cards clickable → filtered SKU list | Proposed |
| FR-16 | Inventory trend charts (value trend, inflow vs outflow, coverage distribution over time) shown only with ≥ 4 weekly snapshots (D7) | Proposed |
| FR-17 | Overstock & stockout risk section with estimated money impact (value only where cost exists, flagged unverified) | Proposed |

## 5. Non-functional requirements

| ID | Requirement |
|---|---|
| NFR-01 | One authoritative implementation per formula (`replenishment.py`, `metrics.py`, `forecast.py`); the frontend never recomputes purchasing numbers. |
| NFR-02 | Every API response that carries numbers includes its as-of dates (stock date, last sale date, forecast calculated_at). |
| NFR-03 | Page API calls finish < 3 s on production data (Vercel `sin1`, Supabase ap-southeast-1); uploads and forecast < 300 s (Vercel limit). |
| NFR-04 | Works on desktop and 390 px mobile without horizontal page scroll; light and dark theme. |
| NFR-05 | No fabricated values: missing data → explicit "not enough data" / "unverified" state. |
| NFR-06 | Uploads are idempotent (re-upload does not duplicate). |
| NFR-07 | Behaviour preserved through refactoring (regression tests in §14). |

## 6. Dashboard information architecture

Pages stay as today: **Overview · Replenishment · Product Analysis · PO & Supplier · Upload Data**. The Overview is reorganised **[Proposed]**:

| Section | Content | Source |
|---|---|---|
| A. Inventory overview | Best-seller SKUs, Inventory value (unverified flag), Understocked %, Healthy %, Overstocked %, Out of stock (count, subset), Turnover, Days of cover (§7) + freshness line | `/api/inventory/summary` |
| B. Purchasing Action Center | 4 bucket cards (Order now / Within 2 weeks / Hold / Healthy) + top recommendations table with priority and reason; link to Replenishment | `/api/replenishment` (+ bucket fields) |
| C. Inventory analytics | Stock status distribution, coverage distribution, actual vs forecast, revenue/sales trend; value trend & inflow/outflow only with ≥ 4 snapshots | existing endpoints |
| D. Risk | Best sellers at risk (stockout) and overstock capital list | `/api/best-sellers-at-risk`, summary |
| — | Best Seller Scope card (Pareto explanation) stays | `/api/bestseller/scope` |

Design **[D4]**: keep the existing light + dark glass theme and tokens; improve hierarchy, spacing and density only.

## 7. KPI definitions

Scope column: **BS** = best-seller scope, **Imp** = all import SKUs. "Stock" = latest snapshot (`MAX(snapshot_date)`), summed per SKU.

| KPI | Definition / formula | Source | Scope | Interpretation | Unavailable when | State |
|---|---|---|---|---|---|---|
| Best-seller SKUs | count of SKUs in scope with a forecast row | `forecast_results` | BS | Size of the decision set | No forecast run | Current |
| Stock on hand | Σ max(stock, 0) | `stock_snapshots` | BS | Physical units | No snapshot | Current |
| Out of stock | count of SKUs with stock ≤ 0 (subset, **not a status**) | snapshot | BS | Immediate availability gap | No snapshot | Current (label per D1) |
| Understocked / Healthy / Overstocked | count of SKUs per status (§8); % = count ÷ (SKUs with a status) | `build_inventory` | BS | Health mix | No sales weeks | Current counts; % Proposed |
| Unclassified | SKUs with no sales in 4 weeks and stock ≤ 0 | same | BS | Not a status; shown as a footnote | — | Current |
| Inventory value | Σ max(stock,0) × unit cost; SKUs without cost excluded and counted | `metrics.unit_costs` | BS | Capital in stock | No cost at all | Current |
| Unit cost | `master_cogs.hpp` (verified) else Σ`cogs_accurate` ÷ Σ`quantity` over the last 12 weeks (unverified) | sales, master_cogs | — | Cost basis | No COGS in 12 weeks → none (never 0) | Current |
| Overstock qty / value | qty above 20 weeks of avg weekly sales: max(0, available − 20 × avg_l4w); no sales → all available; value = qty × unit cost | `rp.overstock_qty` | BS | Excess capital | No cost → value unavailable | Current |
| Inventory turnover | (COGS between first and last full snapshot × 52 ÷ weeks) ÷ average inventory value at those snapshots; snapshots with < 50% of the fullest SKU count are skipped | `metrics.inventory_turnover` | BS | Times per year stock turns | < 2 full snapshots or < 1 week apart | Current |
| Days of cover (per SKU) | coverage × 7 (coverage includes open PO) | `build_inventory` | BS | Days stock + PO last | avg_l4w = 0 → none | Current |
| Average days of cover (portfolio) | **[Open U2]** definition to approve: proposed **median** of per-SKU days of cover over SKUs with sales | — | BS | Typical cover | — | Proposed |
| Order now / Within 2 weeks / Hold / Healthy | bucket counts (§9.2) | plan fields | BS | Purchasing workload | No forecast | Proposed |
| Est. purchase cost | Σ reco_qty × unit cost (unverified unless master_cogs) | plan + cost | BS | Budget for recommended orders | No cost → partial, count shown | Current |
| Forecast MAPE (in-sample) | mean of per-SKU MAPE of Holt's DES fitted values (naive rows excluded) | `forecast_results.mape` | BS Holt | Thesis accuracy metric | — | Current |
| Holdout WMAPE | Σ\|actual − forecast\| ÷ Σ actual over the last 8 weeks, α/β fitted on earlier weeks only | `forecast.holdout_wmape` | BS Holt | Out-of-sample accuracy | < 12 weeks history | Current |
| Trend | (mean forecast next 8 wk − mean actual last 8 wk) ÷ mean actual; ±10% → increasing / decreasing / stable | `rp.relative_trend` | BS | Direction | actual = 0 → "new demand" / "insufficient baseline" | Current |
| Fill rate | Σ min(received, ordered) ÷ Σ ordered on due lines (closed, or open past ETA) | `purchase_orders` | all PO | Supplier completeness | No receipts | Current |
| Actual lead time | received_date − order_date on received lines (received ≥ order); mean, median, p90 | `purchase_orders` | all PO | Real delivery time vs 8-week assumption | No dated receipts | Current |
| PO aging | open lines with (today − order_date) > 60 days | `purchase_orders` | all PO | Follow-up list | — | Current |
| Stockout rate | SKU-weeks with stock ≤ 0 ÷ SKU-weeks observed, last 12 weeks | snapshots | BS | Service level history | < 4 snapshots | Current |

Rule: a KPI that cannot be computed returns `available: false` + `reason` and the UI shows "Not enough data", never 0.

## 8. Inventory health classification [Current, D1]

```
avg_l4w     = (Σ units sold in the last 4 complete sales weeks) ÷ 4
open_po     = Σ (qty_ordered − qty_received) over lines with status pending/partial and ordered > received
coverage    = (stock + open_po) ÷ avg_l4w            (weeks; none when avg_l4w = 0)
```

| Status | Condition |
|---|---|
| Understocked | coverage ≤ 8 |
| Healthy | 8 < coverage ≤ 20 |
| Overstocked | coverage > 20, **or** avg_l4w = 0 and stock > 0 |
| (unclassified) | avg_l4w = 0 and stock ≤ 0 |

Exactly three statuses. *Out of stock* (stock ≤ 0) is a subset count shown next to them. Thresholds come from `SW_UNDERSTOCK_WEEKS` (8) and `SW_OVERSTOCK_WEEKS` (20).

## 9. Purchasing decision rules

### 9.1 Per-SKU plan [Current] — `replenishment.plan_sku`

Inputs: stock (may be negative), open PO lines, 12 weekly forecast values `f[1..12]`, lead time `L` = 8 weeks, review period `R` = 14 days.

| Step | Formula |
|---|---|
| PO ETA | expected_date → else order_date + L weeks (*estimated*) → else unknown |
| Confirmed incoming | PO with an ETA; placed in its ETA week (before week 1 → week 1; after week 12 → outside horizon) |
| Unconfirmed incoming | PO with unknown ETA: **never counted**, shown separately, flag *needs PO check* |
| Safety stock **[D5]** | `SS = 0.10 × (f1 + … + f8)` — 10% of the next 8-week forecast (`SW_SAFETY_STOCK_PCT`, `SW_SS_BASIS_WEEKS`). It equals "10% of lead-time demand" only while L = 8. |
| Lead-time demand | `D_L = f1 + … + fL` |
| ROP | `D_L + SS` |
| Inventory position | `stock + confirmed incoming arriving within weeks 1..L` (negative stock kept, so the deficit is re-ordered) |
| **Recommended order** | `max(0, round(D_L + SS − position))` — never negative |
| Order-up-to (comparison only) | `max(0, round(f1..f(L+2) + SS − (stock + confirmed within L+2)))` |
| Projection | week by week from max(stock,0): closing = max(0, opening + incoming − demand); unmet demand is lost |
| Stockout week | first week with demand > 0 and level ≤ 0 |
| **Order by** | start date of the stockout week − L weeks. No stockout in 12 weeks → (week 12 start + 7 days) − L, marked *estimate* |
| Flags | negative_stock, lead_time_beyond_horizon, protection_beyond_horizon |

Deprecated: `urgency` (critical/high/medium/low from slack weeks) is still returned by the API but must not drive UI decisions (D3). It stays until Phase 4 removes its UI uses, then it is marked deprecated in the API.

### 9.2 Action Center buckets [Proposed, D2] — derived only from existing fields

Evaluated in this order; the first match wins.

| Bucket | Rule (fields from 9.1 / 8) | Priority (D3) | Recommended action |
|---|---|---|---|
| **Order now** | `order_by_is_estimate = false` and `order_by_days ≤ 0` | Critical | Create PO now; ordered goods arrive after the projected stockout, so also check stock transfer / faster shipping |
| **Order within 2 weeks** | `order_by_is_estimate = false` and `1 ≤ order_by_days ≤ 14` | High | Include in this review's PO |
| **Hold purchase** | status = Overstocked, **or** (`reco_qty = 0` and confirmed_incoming > 0) | — | Do not reorder; review excess / incoming |
| **Healthy** | everything else | Medium if `15 ≤ order_by_days ≤ 28` (next review cycle, 2 × R), otherwise Low | No action this cycle |

Notes:
- 14 days = one review period (`SW_REVIEW_PERIOD_DAYS`); 28 = two review periods. No new parameter.
- A SKU in *Order now* can have `reco_qty = 0` (incoming PO counted inside the lead time but arriving after the stockout). Its reason must say so and show the order-up-to figure.
- Bucket value = Σ reco_qty × unit cost for Order now / Within 2 weeks; Σ overstock value for Hold. Flag unverified cost.

### 9.3 Priority sort [Proposed]

Order by date ascending (estimates last), then L4W revenue descending.

### 9.4 Reason text [Proposed]

Built server-side from fields, e.g.:

- "Stock runs out in week 3 (16 Nov); with an 8-week lead time the order is 6 days late."
- "Open PO of 300 arrives in week 4, after the stockout in week 3."
- "Coverage 26 weeks (> 20): hold."
- "PO without ETA (120 units) not counted — confirm the ETA."
- "Forecast est.: less than 4 weeks of history."

Allowed facts only: stockout week/date, order-by date and days, coverage, incoming (confirmed/unconfirmed), reco vs order-up-to, forecast method, flags.

## 10. Forecasting rules [Current]

| Item | Rule |
|---|---|
| Granularity | Weekly (`DATE_TRUNC('week')`, Monday), zero-filled from the SKU's first sale |
| Model | Holt's DES; level₀ = y₀, trend₀ = y₁ − y₀ |
| Parameter search | α, β ∈ {0.1 … 0.9} (81 combinations), minimise in-sample MAPE |
| Horizon | 12 weeks, negative forecasts clipped to 0 |
| Metrics | In-sample MAPE (thesis metric); holdout WMAPE over the last 8 weeks with α/β chosen on training weeks only (no leakage; unit-tested) |
| Fallback | Hero SKUs only: naive mean of last 4 weeks; excluded from MAPE/WMAPE |
| Storage | `forecast_results` (one row per SKU, JSON with actual, fitted, forecast, holdout, method, scope) |
| Trigger | Manual **Refresh forecast**; also after a sales import |

## 11. Database requirements

See `docs/database.md`. Summary: 13 tables in production, schema defined partly by runtime DDL; a migrations folder and baseline are required before any schema change; RLS must be enabled (pending approval).

## 12. API requirements

Existing contracts are kept (see `docs/architecture.md` §5). Proposed additions, Phase 4:

| Change | Detail |
|---|---|
| Plan rows gain `action_bucket`, `priority`, `reason` | Computed in the backend from §9.2–9.4; `urgency` kept but marked deprecated |
| `/api/inventory/summary` gains `buckets` | counts and values per bucket, status percentages, `days_of_cover_median` once U2 is decided |
| Trend endpoints | `available:false, reason:'insufficient_snapshots', required:4` until 4 weekly snapshots exist |

## 13. Edge cases

| Case | Required behaviour | State |
|---|---|---|
| No sales in 4 weeks, stock > 0 | Overstocked; overstock qty = all available | Current |
| No sales, no stock | Unclassified, excluded from % | Current |
| Negative stock | Projection starts at 0; deficit re-ordered; flag | Current |
| PO without ETA and order date | Not counted; flag *needs PO check* | Current |
| PO partially received | Only outstanding counts | Current |
| Over-received PO line | Completed; shown as Over +X; fill rate capped per line | Current |
| Closed short / cancelled / superseded PO | Never counted as incoming | Current |
| Stock snapshot > 7 / > 14 days | Warning / very-stale banner | Current |
| Partial stock upload (< 50% SKUs) | Ignored for turnover | Current |
| Duplicate sales rows (same SKU, date, qty, amount) | Skipped on import. Risk: two genuine identical sales on one day count once **[Open U10]** | Current |
| Same name under several SKUs | Exact-case name first, then latest-stock SKU, then dotted code | Current |
| Hero code without dots / bundle `A+B` | Matched ignoring dots; bundle split to components | Current |
| Forecast older than latest upload or Hero list | Scope card warns; stale badge proposed on all pages | Current / Proposed |
| Lead time > forecast horizon | Flag; demand capped to 12 weeks | Current |
| No unit cost | Value = none (never 0), SKU counted in "no cost" | Current |
| < 4 weekly snapshots | Trend charts show insufficient-data state | Proposed |

## 14. Acceptance criteria

Business rules (must hold before and after every refactor step):

1. Only three statuses appear anywhere; Out of stock is a separate count.
2. Status boundaries: 8.0 → Understocked, 8.1 → Healthy, 20.0 → Healthy, 20.1 → Overstocked.
3. `reco_qty ≥ 0` for every SKU; equals the formula in §9.1 within rounding.
4. SS = 10% of the next 8 forecast weeks for every SKU.
5. A PO without ETA never changes `reco_qty` or the projection.
6. The same SKU shows identical status, coverage, reco and order-by on Overview, Replenishment, Product Analysis and the API.
7. Pareto set equals the rule in §3; every Hero SKU appears in Replenishment after Refresh forecast.
8. MAPE/WMAPE exclude naive rows; holdout α/β depend only on training weeks.
9. Re-uploading the same sales, stock, PO or Hero file changes no counts.
10. Unavailable KPIs display a reason, never 0.

Action Center (Phase 4):

11. Every BS SKU is in exactly one bucket; bucket counts sum to the BS count.
12. Bucket and priority follow §9.2 exactly; no new formula in the frontend.
13. Each recommendation shows a reason built from §9.4 facts.

Quality:

14. Unit, integration and e2e suites pass (see `docs/refactoring-plan.md`).
15. No console errors; no horizontal scroll at 390 px; light and dark theme.

## 15. Open decisions

| ID | Question | Recommendation |
|---|---|---|
| U1 | Enable RLS on all 13 tables (Supabase ERROR advisory) | Yes, no policies; app unaffected |
| U2 | Portfolio "average days of cover": median vs mean; with or without PO | Median, with PO (same basis as status) |
| U3 | Source for verified HPP (`master_cogs` is empty) | Finance export → `master_cogs` upload |
| U4 | Medium priority window 15–28 days | Accept (2 × review period) |
| U5 | Inflow source for inflow/outflow chart | PO received qty by received_date |
| U6 | Remove legacy files/tables/deps listed in the audit | Remove after Phase 3 tests are green |
| U7 | Forecast run on ~250 SKUs inside one request | Measure in prod; if > 120 s, chunk or schedule |
| U8 | Pareto window (all history vs last 12 months) | Keep all history (current, documented in thesis) |
| U9 | Which Hero file is official (HERO1 = 134 SKUs with GROUP/STATUS, BEST_SELLER = 198 without) | Company to confirm |
| U10 | Sales duplicate rule may drop genuine identical same-day rows | Keep; document as limitation, or add invoice number if Accurate export allows |
