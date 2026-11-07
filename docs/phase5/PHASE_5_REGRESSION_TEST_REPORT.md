# Phase 5 — Regression Test Report

Environment: local PostgreSQL with the synthetic fixtures (`tests/fixtures/seed.py`), app started by
`tests/e2e/run_regression.py`, Chromium via Playwright, contexts desktop 1440 px light (with data edits), desktop dark,
phone 390 px light. Reference run: `main@08fca67` (same seed, same day). **Not yet tested on production data.**

## 1. Automated suites

| Suite | Result |
|---|---|
| Backend: unit + integration + API contract + old-vs-new API regression (`pytest`) | **40 passed** (incl. 4 new `test_decision_js.py`) |
| Browser flow (`tests/e2e/regression.js`, 87 checkpoints) | **0 page / console errors**, server log without tracebacks |
| Overview values vs API (inside the browser flow, all 3 contexts) | **equal**: buckets 39 / 0 / 5 / 4 = 48 SKUs; health 37 / 6 / 3 / 2, out of stock 35 — computed independently from `/api/replenishment` |
| Mobile horizontal scroll (page width ≤ 390 px on every phone checkpoint) | **pass** after fix `10fe8a2` (the first Phase 5 run found Replenishment 548 px wide because the five bucket tabs did not wrap) |

## 2. Contract checks (Phase 5 vs `main`, same flow)

| Check | Expected | Actual | Result |
|---|---|---|---|
| Endpoints called by the UI | only existing endpoints | no new endpoint; `/api/best-sellers-at-risk` and `/api/bestseller/scope` no longer called (still available) | pass |
| Data-changing requests (login, PO received qty, Hero upload ×2) | identical method, URL and body | identical | pass |
| Replenishment CSV export | identical | byte-identical (48 rows) | pass |
| Inventory CSV export | identical | byte-identical (48 rows) | pass |
| Python / schema / `vercel.json` / requirements | unchanged | unchanged (only test files) | pass |

## 3. Functional checks per module (browser flow)

| Module | Tested | Expected vs actual | Result |
|---|---|---|---|
| Login | wrong password message, login | message shown, app loads | pass |
| Overview | 4 bucket cards selectable; counts vs API; health legend → Replenishment filtered; Action Center row → Replenishment drawer; Esc closes | as expected | pass |
| Replenishment | tabs Order now / All / Hold; status filter Understocked; quantity edit 999; CSV export; row → drawer; open all collapsed sections; Esc | as expected; CSV unchanged | pass |
| Product Analysis | Stock & Coverage table + export; SKU history modal open / Esc; Demand plan units / sales; weekly table; **Sales** sub-view; period 8W | charts render, values load | pass |
| PO Tracking | by PO / by product; status filter All; received qty edit | same PATCH as before | pass |
| Data | page + status; wrong file type → error; Hero upload → success | same messages from the API | pass |
| Shared | settings modal, theme toggle, keyboard shortcuts 1–5, light / dark, 390 px | as expected | pass |
| Decision logic (unit) | bucket rules incl. first match and estimate dates, one bucket per SKU, explanation numbers, fallback | as expected | pass |

## 4. Visual review

Screenshots reviewed by eye for Overview (light, dark, phone), Replenishment list and drawer, Product Analysis
(Stock, Demand plan, Sales), PO Tracking and Data. Pixel comparison with `main` is not meaningful here (the
redesign changes the screens on purpose).

## 5. Not tested / known issues

- Production data volume and real SKU names (preview deploy needed).
- Sales ETL preview + confirm, stock / food / master / PO uploads through the browser (covered by API integration
  tests only); Refresh forecast button; drag & drop; custom date range.
- Close short / cancel confirm dialogs (the flow avoids native dialogs).
- Browsers other than Chromium; colour contrast with a tool; screen readers.
- Open gaps G1–G8 in `PHASE_5_HANDOVER.md`.
