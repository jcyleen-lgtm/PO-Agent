# Revision R7: implementation and handover (Stage 4–5)

Branch `r7-revision`, built on `overview-v2` (decision 1). Frontend only: no change to the backend, API
contracts, database, Holt's DES, coverage, safety stock, lead time, recommended quantity or PO status logic.
Stage 1–3 (audit, wireframes, validation): `R7_STAGE_1-3.md`.

## 1. Decisions applied

| # | Decision (San, 4 Nov 2026) | Implemented as |
|---|---|---|
| 1 | Branch from `overview-v2` | `r7-revision` on top of `overview-v2` |
| 2 | Understocked with recommended 0 get their own group | **Covered / Monitor**: Understocked + `reco_po = 0`. Not in the order export by default, can be ticked manually. Inventory status stays Understocked |
| 2a | Two reasons, validated on production (13 SKUs: 3 + 10) | **Stock / Incoming PO Sufficient**: no forecast week within the lead time is 0. **Forecast Drops to Zero**: at least one of the first *lead-time* forecast weeks is 0 → red "Forecast warning" badge; the text says the recommendation is 0 because of the forecast, not because stock covers demand |
| 3 | Healthy with recommended > 0 | Stay in **Hold**, badge **System Recommended**, quick filter, never pre-selected (6 SKUs in production) |
| 4 | Ordered quantity: ETA and PO date | Built (toggle, default By ETA), then **removed** on 4 Nov feedback: PO Tracking shows month / PO filters and PO status instead |

Groups (core/decision.js `bucketOf`), each SKU in exactly one:

| Group | Rule | Export | Production 4 Nov |
|---|---|---|---:|
| Order Now | Understocked, recommended > 0 | always (Mandatory) | 19 |
| Covered / Monitor | Understocked, recommended 0 | optional | 13 (3 sufficient, 10 forecast warning) |
| Hold | Healthy | optional | 56 (6 System Recommended) |
| No Action Needed | Overstocked | optional | 102 |
| Not classified | no sales in 4 weeks and no stock | All tab only | 4 |

"Upcoming orders" is removed. The order-by date, urgency and stock projection are still shown per SKU (they
come from the API); they no longer decide the group. The priority order inside a group is unchanged
(order-by date, estimates last, L4W revenue).

## 2. Changes per page

**Overview** (order as requested): header with data-date chips → 5 KPI cards (Total, Understocked, Healthy,
Overstocked, Out of Stock as a subset) → purchasing group strip (4 groups, each opens its tab) → **Demand —
Actual vs Forecast** (weekly; two lines in different colours: actual navy, forecast orange; 8 holdout weeks,
then 12 forecast weeks shaded; tooltip shows over / under per week) next to **Operational Summary** (month selector; monthly sales; best-seller
revenue contribution = Σ revenue of the top 10 SKUs ranked by revenue ÷ total revenue of the month × 100;
12-month sales bars; top-10 list; pending POs; last sales; last stock; data status) → **Priority SKUs**
(Understocked: Order Now then Covered / Monitor; columns SKU, Product, Status, Stock, Coverage, Incoming PO,
Forecast / Wk, Recommended PO). **Inventory Insights** (health donut, coverage distribution) sit above
Priority SKUs (feedback 4 Nov).
The Stock Value KPI card is replaced by Healthy (brief lists five cards); stock value stays in Product Analysis.

**Replenishment Planning**: tabs All / Order Now / Covered / Monitor / Hold / No Action Needed; Order Now
checkboxes ticked and locked ("Mandatory"); other rows optional ("Manual" once ticked); reason badges;
System Recommended filter chip; sticky bar "mandatory + manual SKUs"; **Export order list (.xlsx)** with
columns *Group*, *Included as* (Mandatory / Manual), *Reason (recommended 0)* and an Info sheet. "Export list"
(CSV of the rows shown) is unchanged. SheetJS 0.18.5 is loaded from cdnjs only when the Excel export is used.

**Product Analysis**: Stock & Coverage table columns in exactly the requested order (SKU, Product, Status,
Coverage, Stockout, Stock, Open PO, Forecast/Wk, Reco PO, Movement, Days Inv., Overstock, Value, Avg/Wk L4W,
L4W Revenue); every header sorts (click again to reverse); SKU and Product stay visible when scrolling sideways;
the trend arrow moved into the SKU cell. SKU detail: **Forecast vs Actual** chart, two coloured lines (26 weeks of actual sales,
the 8 holdout weeks with the forecast made without them, then the 12-week horizon) plus a summary (weeks above /
below actual, total forecast vs actual) and a "forecast − actual" bar per holdout week. No reliability labels.

**PO Tracking**: month selector (month of the PO date, **oldest to newest**, then "No PO date" and "All
periods"; default the latest month; previous / next buttons) and a **PO number** selector for that month (All POs
or one PO, each labelled with its status). Each PO gets a status: **Completed** (no open lines), **On going**
(open lines, PO date within 60 days), **Over 2 months** (open lines, PO date more than 60 days ago; the existing
aging limit). The selection summary shows PO count, Completed, On going, Over 2 months and ordered / received
quantity (one PO number counts once). The line filter defaults to All inside the selection; search works as before.
The purchasing chart (forecast vs sales vs ordered) was removed from PO Tracking at San's request (4 Nov).

## 3. Files

| File | Change |
|---|---|
| `static/js/core/decision.js` | new groups, `zeroReason`, `zeroReasonText`, `sysRecommended`, `inOrder`, explanations; `fetchRepl` also loads the weekly forecast map |
| `static/js/core/demand.js` | **new**: `fetchPlan` (shared `/api/forecast/plan` cache), `demandSeries`, `biasSummary`, `wkLabel` |
| `static/js/core/ui.js` | `ensureXlsx` (lazy SheetJS) |
| `static/js/core/settings.js` | theme switch redraws the new charts |
| `static/js/pages/overview.js` | rewritten for the new section order |
| `static/js/pages/replenishment.js` | groups, locked mandatory rows, badges, filter chip, Excel export |
| `static/js/pages/analysis.js` | column order, header sort, SKU forecast vs actual; Demand Plan uses `fetchPlan` |
| `static/js/pages/po.js` | month and PO-number filters, PO status, selection summary |
| `templates/dashboard.html` | Overview, Replenishment, PO markup; `core/demand.js` script tag |
| `static/css/foundations.css` | chart colours `--chart-act` (actual) and `--chart-fc` (forecast), light and dark |
| `static/css/components.css`, `pages/{overview,replenishment,analysis,po}.css` | styles for the above |
| `tests/unit/test_decision_js.py` | rewritten for the R7 rules |
| `tests/e2e/regression.js`, `tests/e2e/package.json` | R7 checks (values vs API, groups, export, column order, PO period); SheetJS for reading the export |

## 4. Known limitations

1. **Global filters**: Store / Platform data does not exist; inventory has no date parameter. Only the
   Operational Summary has a month selector (sales figures).
2. **Forecast history**: only the 8 holdout weeks of the latest run exist; earlier forecasts are overwritten,
   so "forecast vs actual" cannot go further back.
3. **Forecast warning SKUs** (10 in production) come from the Holt trend reaching 0 (see forecast accuracy
   decisions F1–F6). The UI flags them; the forecast itself is unchanged.
4. **PO value**: no price on PO lines. **Supplier**: empty on all lines, so no supplier column / search.
   The forecast vs ordered quantity chart is not shown (removed on request); the data for it is still available.
5. **Last sales week is partial** when sales end mid-week (production: data to 29 Oct); the demand chart marks
   it with `*`.
6. Best-seller contribution uses the top 10 by revenue (the API returns 10); N is not configurable.
7. Verified on synthetic test data in the browser; production numbers to be checked on the preview.

## 5. Tests (4 Nov 2026)

| Test | Result |
|---|---|
| Backend + unit tests (`pytest tests`) | **42 passed** (includes the rewritten `test_decision_js.py`: groups, zero reasons, System Recommended, explanations) |
| Browser regression, desktop light / dark, mobile 390 px (`tests/e2e/run_regression.py`) | **112 checkpoints, 0 errors**, no server tracebacks, no horizontal page scroll |
| Overview values vs API (3 contexts) | KPIs, group counts, 8 priority rows, health and coverage chart data, demand chart past weeks (= Σ holdout actual from `/api/forecast/plan`) all equal |
| Replenishment | tab counts = groups; every Order Now row locked; System Recommended count matches; **.xlsx export read back**: Mandatory rows = Order Now count, the manually ticked SKU appears as Manual |
| Product Analysis | header order exactly the 15 requested columns; coverage sort descending; SKU forecast vs actual chart rendered |
| PO Tracking | months listed oldest to newest, default = latest; PO count, Completed, On going, Over 2 months equal a recount from the PO lines; PO-number filter shows only that PO; previous month; All periods |
| Production data (rules only, read-only) | `decision.js` on the 194 production SKUs: Order Now 19, Covered 13 (3 sufficient + 10 forecast warning), Hold 56 (6 System Recommended), No Action 102, Not classified 4: same as the validation |
| Unchanged | no backend file changed; API calls on load are existing endpoints only (`/api/forecast/plan` and `/api/forecast/results` were already used by Demand Plan and Sales); data-changing requests unchanged; "Export list" CSV columns unchanged |

The test database has no Covered / Monitor or System Recommended SKUs; those paths are covered by the unit
test and the production-data check above, and must be looked at on the preview with production data.

Screenshots (synthetic test data): `img/overview.png`, `img/overview-dark.png`, `img/overview-mobile.png`,
`img/replenishment.png`, `img/sku-forecast-vs-actual.png`, `img/po-tracking.png`.
