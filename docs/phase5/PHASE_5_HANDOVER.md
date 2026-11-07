# Phase 5 — Handover (for Phase 6)

Branch `phase5-ux`. Read with `PHASE_5_IMPLEMENTATION_REPORT.md` (what changed per file) and
`PHASE_5_REGRESSION_TEST_REPORT.md` (evidence).

## What changed and why

| Screen | Redesigned | Why |
|---|---|---|
| Overview | **yes — restructured** (12 blocks → 5 sections, Action Center first) | Answer "what needs a purchasing decision now?" in one screen; remove repeated and sales-only content |
| Replenishment | **layout** (bucket tabs, demand column, side drawer); function unchanged | Same groups as the Overview; detail next to the list; daily facts first, analysis collapsed |
| Product Analysis | **partly** (new Sales sub-view, one status filter, link to Replenishment) | Investigation page now holds sales history; contradictory filter removed |
| PO Tracking | **light** (title, legend, confirmations) | Status vocabulary explained; destructive actions confirmed |
| Data | **yes** (weekly routine vs occasional, refresh forecast here, dead card removed, English) | Matches how often each task is done |
| Shared | navigation labels, solid cards, tokens, states, focus ring | Readability, consistency, accessibility |

Components created: side drawer, Action Center bucket card, health legend item, why box, disclosure (`details.disc`),
state / error panel with Retry, bucket tags, sticky selection bar (restyled). Reused: cards, tables, segmented
controls, dropdowns, tooltips, toasts, modals, SKU history modal, charts.

Interactions improved: bucket → filtered Replenishment; status count → filtered Replenishment; Action Center row →
Replenishment drawer; drawer → demand history → back to Replenishment; Esc closes drawer; confirm before close
short / cancel; consistent loading / empty / error states.

## Business logic and API contracts — preserved

Evidence (details in the regression report):
- No Python, schema, `vercel.json` or requirements change on this branch (only test files).
- Browser run on the same seeded data: **no endpoint added**; every data-changing request (login, PO edit, Hero upload)
  byte-identical to `main`; both CSV exports **byte-identical** to `main`.
- Overview bucket and health counts equal the counts computed independently from `/api/replenishment` in the test
  (checked in all three viewport/theme contexts).
- Buckets implement spec §9.2 exactly (unit-tested incl. first-match and estimate cases); no thresholds changed.
- `/api/best-sellers-at-risk` and `/api/bestseller/scope` are no longer called by the UI; they still exist.

## Incomplete / known gaps

| # | Gap | Suggested next step |
|---|---|---|
| G1 | Stock & Coverage table still has 12 columns and scrolls horizontally at 1440 px | "Columns ▾" picker with 7 default columns (IA §3) |
| G2 | PO rows still show the received-date input and close/cancel as inline text buttons (confirmed now, not yet in a row menu) | row action menu (IA §4) |
| G3 | Upload cards other than sales still upload immediately (no preview step) | needs a backend preview endpoint per type → out of Phase 5 scope (no API change) |
| G4 | Inline `on*=` handlers remain (Phase 4 debt T1) | migrate per page to delegation |
| G5 | Contrast measured by eye, not by tool | run an automated contrast check (axe) |
| G6 | Forecast reliability labels deferred (A4) | after F2–F5 in `docs/forecast-accuracy.md` |
| G7 | Pareto / company-list UI hidden (A3) | decide with the Pareto discussion; previous UI at `main@08fca67` |
| G8 | Product Analysis tooltips still name Holt's DES where they describe the current method | revisit after F2 |

## What to test in Phase 6

1. On **production data** (preview deploy): Action Center counts vs Replenishment tabs vs a manual count from the
   export; explanation text for 10 random SKUs against the numbers in the drawer.
2. Edge cases: SKU with PO without ETA, naive forecast SKU, negative stock, overstocked SKU with recent order-by.
3. Usability: time for a purchasing staff member to answer "what must I order today?" on the old vs new Overview.
4. Keyboard only: navigation, drawer open/close, quantity edit, export.
5. Tablet (≈ 820 px) and small laptop (1280 px) layouts; dark theme on every page.
6. Browsers other than Chromium (Safari on Mac).
