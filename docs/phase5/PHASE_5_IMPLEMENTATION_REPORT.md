# Phase 5 — Implementation Report

Branch `phase5-ux` (from `main@08fca67`). Frontend only. Decisions A1–A5 from
`PHASE_5_INFORMATION_ARCHITECTURE.md` §6 applied.

## 1. Files

| Action | File | What |
|---|---|---|
| Created | `static/js/core/decision.js` | `BUCKETS`, `bucketOf()` (spec §9.2 / D2, first match wins), `byPriority()` (spec §9.3), `explainSku()` (deterministic text from API fields, fallback), `fetchRepl()` (one cached `/api/replenishment` request shared by Overview and Replenishment) |
| Created | `static/js/pages/sales.js` | Sales history moved from the Overview (period filter, sales KPIs, revenue / units trend, actual vs forecast, top products) — same endpoint `/api/overview`, same rendering |
| Rewritten | `static/js/pages/overview.js` | New Overview: data context, stock health, Action Center, demand and PO lines |
| Rewritten | `static/js/pages/replenishment.js` | Bucket tabs, Demand / week column, side drawer with "Why", formula, collapsed detail; same export and quantity editing |
| Modified | `static/js/pages/analysis.js` | Sales sub-view; one status filter (default Understocked) instead of dropdown + checkbox; "Replenishment →" from the SKU history modal |
| Modified | `static/js/pages/po.js` | Confirm before "Close short" / "Cancel" |
| Modified | `static/js/pages/upload.js`, `core/auth.js`, `core/settings.js`, `app.js` | Re-wiring: refresh / upload reload the Overview; period setting reloads Sales; Esc closes the drawer; stale globals removed |
| Modified | `templates/dashboard.html` | New Overview markup; Sales sub-view; Replenishment tabs and drawer; Data page groups; PO legend and title; visible navigation labels; English texts |
| Modified | `static/css/foundations.css` | Solid cards, radius / spacing / elevation / focus tokens |
| Modified | `static/css/components.css`, `layout.css`, `responsive.css`, `pages/overview.css` (rewritten), `pages/po.css`, `pages/upload.css`, `pages/replenishment.css` | New components (drawer, states, bucket tags, disclosure, why box), nav labels, Data groups, sticky selection bar |
| Created | `tests/unit/test_decision_js.py` | Bucket rules, one-bucket-per-SKU, explanation content, fallback (runs `decision.js` in Node) |
| Modified | `tests/e2e/regression.js` | Flows for the new UI + check that Overview counts equal the API |
| Created | `docs/phase5/*` | Audit, user flow, IA, design system, this report, regression report, handover |

No backend, API, schema, forecasting, inventory or PO rule file was changed (`git diff main -- '*.py' vercel.json`
shows only test files).

## 2. Changes per module

### Overview
- Removed: 6-tile inventory strip, Financial Metrics, Purchase Recommendations, sales strip, Insights, System Health
  rings ("100 − WMAPE" accuracy), Best Sellers at Risk cards, Actual vs Forecast + Forecast Statistics, trend charts,
  Best Seller Scope (Pareto), Top 10, connection bar, period filter.
- Added: context line with three explicit dates (stock, sales, forecast); stock health line (one bar, four statuses +
  out-of-stock subset, each clickable to Replenishment); **Purchasing Action Center** (four buckets with SKUs,
  units, value; top 5 SKUs of the selected bucket with order-by, stock + incoming, demand / week, recommended and
  a one-line reason; row → Replenishment drawer; "See all" → Replenishment filtered); demand line (forecast next
  8 weeks, declining / rising counts) and PO line (open POs, incoming units, lines past ETA, POs without ETA).
- Health and bucket counts come from the same rows → they always add up (verified on test data and in the e2e
  check).

### Replenishment (function unchanged)
- Tabs = the four buckets + All (one definition shared with the Overview). The old "Order now" tab used a different
  rule; the pre-selection rule for export is unchanged and now written next to the selection bar.
- Removed "All SKUs / Company list / Pareto only" filter and Hero / Uprising tags (A3). The "est." tag for the simple
  average forecast stays (existing fact, not a reliability label).
- Added "Demand / week" column (`fc_avg_weekly`). Rows sorted by priority (order-by, estimates last, revenue).
- Detail moved from below the table to a right-side drawer: Why (rule-based), recommended quantity with its formula,
  six key facts, warnings; 12-week projection, open PO lines and comparisons (order-up-to, reorder point, safety
  stock scenarios) collapsed. "Demand history →" opens the SKU history.
- Selection bar sticky; buttons "Export list" (current filter) and "Export order list" (selected). CSV columns
  unchanged.

### Product Analysis
- New **Sales** sub-view holding everything moved from the Overview (same API and charts).
- Stock & Coverage: one status filter, default "Understocked" (same default result as before, without the
  contradictory "All Status" + checkbox).
- "Refresh forecast" moved to Data. SKU history modal has "Replenishment →".

### PO Tracking
- Title "PO Tracking"; status legend for the existing line states and filters; confirmation before closing a line
  short or cancelling it (same API calls).

### Data (was Upload Data)
- "Weekly routine" (1 Sales, 2 Stock, 3 Refresh forecast) and "When something changes" (PO list, Hero, Food,
  Master). Removed the non-functional "PO Receipts — Coming in P2" card. Instructions in English. Data status text
  without Pareto wording. Upload logic and messages from the API unchanged.

### Shared
- Visible labels under navigation icons; solid cards; consistent states (loading, empty, error with Retry);
  focus ring.

## 3. Architectural decisions

| Decision | Why |
|---|---|
| Buckets and explanations computed in the browser from existing fields | Approved (A1, A2); no API change. Rules are in one file and unit-tested. |
| One shared `/api/replenishment` fetch for Overview and Replenishment | Same numbers on both pages and one request instead of two. |
| Sales history moved, not deleted | No feature removed; it is analysis, so it lives in Product Analysis. |
| Pareto UI removed, code kept in git history | A3: hidden temporarily; endpoint and data untouched; previous UI at `main@08fca67`. |
| No new dependency, classic scripts kept | Same delivery model as Phase 4. |
