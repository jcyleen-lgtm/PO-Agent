# Inventory Overview redesign (Overview v2)

Branch `overview-v2`, built on `phase5-ux` (the Phase 5 Action Center this brief continues).
Frontend only: no change to the backend, API contracts, database, forecasting (Holt's DES), coverage,
reorder, safety stock, lead time or PO logic.

Screenshots (synthetic test data): `img/overview-desktop-light.png`, `img/overview-desktop-dark.png`,
`img/overview-mobile.png`.

## 1. Audit (Stage 1)

### Data available to the Overview

| Need in the brief | Source | Field(s) | Available |
|---|---|---|---|
| SKU count, statuses, out of stock | `GET /api/replenishment?product_type=import` | `results[].status`, `out_of_stock` | yes |
| Action groups | same rows, `bucketOf()` in `core/decision.js` | `order_by_days`, `order_by_is_estimate`, `status`, `reco_po`, `confirmed_incoming` | yes |
| Current stock | same rows | `current_stock` | yes |
| Average weekly sales | same rows | `avg_l4w` (last 4 weeks; the coverage denominator) | yes |
| Stock coverage | same rows | `coverage` (weeks; `null` = no sales in 4 weeks) | yes |
| Incoming PO | same rows | `ongoing_po` (all open PO qty), `unconfirmed_incoming` (without ETA) | yes |
| Recommended order qty | same rows | `reco_po` | yes |
| Status thresholds | same response | `settings.understock_weeks`, `settings.overstock_weeks` | yes |
| Last stock snapshot, last sale | same response | `freshness.stock_date`, `stock_age_days`, `stock_level`, `last_sale_date`, `sales_age_days`, `warn_days` | yes |
| Forecast last run | `GET /api/overview?preset=l4w` | `forecast_summary.calculated_at` | yes |
| Pending POs | `GET /api/po/summary` | `open_po_numbers`, `outstanding_qty`, `overdue` | yes |
| Inventory history (trend) | — | only the latest stock snapshot is used by the inventory API | **no** |

### Global filters asked for (Month, Year, Week, Store, Platform)

- **Store, Platform:** do not exist. Sales come from the Accurate "Sales by Item Detail" export, which has
  no store or platform column, and no table or endpoint carries one.
- **Month, Year, Week:** the inventory endpoints take no date parameter. Stock is the latest snapshot and
  coverage uses the last 4 weeks of sales, so a date filter would not change any number on this page.
  Period filters exist only for sales history (Product Analysis › Sales).
- **Product type** (`import` / `food` / `all`) exists in the API, but the app is fixed to `import` and there
  is no UI switch on any page.

**Decision:** no global filter on the Overview. The brief says filters are "based on existing
implementation" and "do not create buttons that have no actual functionality". The header instead shows
what the numbers are based on (stock snapshot date + forecast run time). Adding real filters would need
backend work and approval (see Limitations).

## 2. Design specification (Stage 2)

Order on the page (desktop, single column of full-width sections):

| | Section | Content | Interaction |
|---|---|---|---|
| A | Header | Greeting, **Inventory Overview**, supporting text; right: *Last updated* = stock snapshot date + forecast run time; avatar (settings) | avatar → Account settings |
| B | KPI cards (4) | Total SKUs (+ status bar), Understocked, Out of Stock, Overstocked; value, % of SKUs, rule text from `settings` | Total → Replenishment *All*; Understocked / Overstocked → Replenishment filtered by status; Out of Stock is not clickable (no such filter exists) |
| C | What Needs Your Attention? | 4 group cards: Order now, Upcoming orders, Hold / Monitor, No action needed; count, rule, units / value line | each card → Replenishment on that group |
| D | Priority SKUs | top 8 of *Order now* then *Upcoming orders*, columns per the brief; Action = group tag + order-by date | row → Replenishment detail drawer; *View all Replenishment*; *N more in Replenishment* |
| E | Inventory Insights | Health distribution (horizontal bars) and Coverage distribution (bins 0, 0–4, 4–8, 8–14, 14–20, > 20, No sales; colour = status zone) | health bar → Replenishment filtered by status |
| F | Operational Summary | Last sales update, Last stock snapshot, Pending purchase orders, Data upload status | PO Tracking →, Data → |

Rules kept from the approved Phase 5 decisions:
- Out of Stock is a subset: it is shown with its own %, never added to the status totals.
- Groups come from `bucketOf()` (spec §9.2, first match wins): every SKU in exactly one group.
- Priority order is the Replenishment order (spec §9.3: order-by date ascending, estimates last, then L4W
  revenue), with *Order now* before *Upcoming orders*.
- Labels renamed per this brief: "Order within 2 weeks" → **Upcoming orders**, "Hold" → **Hold / Monitor**
  (Overview and Replenishment tabs). The classification itself is unchanged.
- No Pareto, no reliability or confidence labels, no decision saving, no marketing KPIs.

Coverage bins are derived from `settings` (half of the understock threshold, midpoint between thresholds),
so the colour boundaries always match the status rules (≤ 8 understocked, > 8–20 healthy, > 20 overstocked).

Responsive: KPIs and group cards 4 → 2 columns ≤ 1100 px; charts stack ≤ 1100 px; on phones the priority
table shows Product, Recommended qty and Action only, and the operational summary becomes a list.

Theme: all colours are CSS tokens; both charts read tokens at render time and are redrawn on theme change
(`rerenderCharts()` in `core/settings.js`).

## 3. Implementation (Stage 3)

| File | Change |
|---|---|
| `templates/dashboard.html` | New Overview markup (sections A–F); Replenishment tab labels "Upcoming orders", "Hold / Monitor" |
| `static/js/pages/overview.js` | Rewritten: `renderOvAsOf`, `renderOvKpis`, `renderActionCenter`, `renderPriority`, `renderOvCharts`, `renderOvOps`; navigation helpers unchanged |
| `static/css/pages/overview.css` | Rewritten for the new layout |
| `static/js/core/decision.js` | Group labels only (`soon`, `hold`, `none` hint) |
| `static/js/core/settings.js` | `rerenderCharts()` also redraws the Overview charts |
| `tests/e2e/regression.js` | Overview flow and value check rewritten |

Reused: `fetchRepl()` (one shared request with Replenishment), `bucketOf`, `byPriority`, `BUCKETS`,
`openReplBucket` / `openReplStatus` / `openReplSku`, `renderFresh`, `getCT` / `cv` (chart theming),
`.card`, `.ro-table`, `.tag-b-*`, `.state`, `.link-btn`. New components: KPI card (`.kc`), operational
tile (`.op-t`).

The removed Phase 5 "Demand forecast" line (8-week forecast units, rising / falling counts) is still in
Product Analysis › Demand Plan.

## 4. Validation (Stage 4) — test data, 3 browser contexts

`tests/e2e/run_regression.py` (desktop light, desktop dark, mobile 390 px):

- **Values vs API, all 3 contexts:** KPIs [48, 37, 35, 3], groups [39, 0, 5, 4] (sum = 48), the 8 priority
  rows (SKU and recommended qty, in order) and both chart datasets equal an independent recomputation from
  `/api/replenishment`; coverage bins sum to 48.
- **Navigation:** each group card opens Replenishment on the same group; Understocked KPI opens the
  understocked filter; a priority row opens the detail drawer; *View all Replenishment* opens *All*.
- 84 checkpoints, **0 page errors**, no server tracebacks; no horizontal page scroll at 390 px.
- 40 backend tests pass.
- Compared with the `phase5-ux` run: other pages differ only in the two renamed tab labels and in which
  tab is open after the new Overview clicks; API calls on load are the same three as Phase 5; no new
  endpoint; data-changing requests unchanged.
- Loading state (spinners), empty state (no forecasted SKUs / no SKU due) and error state (Retry) are in
  `overview.js`; the error and empty paths were not forced in the e2e run.

## 5. Known limitations

1. **No global filters** (see §1). Real Store / Platform filters need that data in the sales import;
   date filters on inventory need historical snapshots per date and an API parameter. Both are backend
   changes that need approval.
2. **No inventory trend chart:** only the latest stock snapshot is used by the inventory API (per the
   brief, no trend without history).
3. **Out of Stock card is not clickable:** Replenishment has no out-of-stock filter, and adding one was
   not in scope.
4. Sales freshness has no backend rule (only stock has `stale_warn_days`), so the sales age is shown
   without a good / bad judgement.
5. Verified on synthetic test data only; production numbers must be checked on the Vercel preview.
