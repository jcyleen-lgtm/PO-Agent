# Stockwise — Frontend (Phase 4)

Phase 4 = frontend refactoring only: structure, separation of concerns, maintainability. No redesign, no change
to business rules, forecasting, API contracts, database or auth. Sections: A audit · B implementation ·
C regression tests · D handover for Phase 5.

---

## A. Audit (before refactoring, `main` @ `5194337`)

### A1. Existing structure

| Item | Finding |
|---|---|
| Pages | One single-page app served by `GET /` (`app.py` reads `templates/dashboard.html`; no Jinja, no template inheritance). Five pages are `<div id="tab-…" class="tab-content">` sections switched in JS (`switchTab`): Overview, Replenishment, Product Analysis, PO & Supplier, Upload Data. Plus login overlay, wait screen, SKU drawer and settings modal. |
| File | `templates/dashboard.html`, 1,521 lines / 217 KB: inline `<style>` 46 KB (≈ 600 lines), markup 45 KB, inline `<script>` 124 KB (≈ 580 very long lines). |
| Static files | None. `vercel.json` already had a `/static/(.*)` route but nothing served it. |
| External | Chart.js 4.4.1 (cdnjs), Inter (Google Fonts). No build step, no framework. |
| JS shape | 159 function declarations + ~60 top-level `const/let` in one global scope. Sections marked by comments (helpers, dropdown, date filter, chart theme, one per page, settings, toasts, login, init). |
| Events | 111 inline handlers in markup (`onclick` 91, `onchange` 11, `oninput` 8, `onsubmit` 1) plus many more inside JS-generated HTML strings; 31 `addEventListener` (document-level delegation for tooltips, dropdowns, keyboard shortcuts, drag & drop). |
| State | Module-level globals (`replData`, `planData`, `poData`, `invData`, `fcDataMap`, `PT`, `activeTab`, chart instances, sequence counters `ovSeq/riskSeq/invSeq` against stale responses). Settings in `localStorage['po-settings-v4']`. |
| API access | 31 `fetch` calls with literal `/api/...` URLs. `window.fetch` is wrapped once to detect an expired session (401 → login). `readJson()` turns non-JSON error pages into readable errors (13 uses); other calls use `r.json()` / `r.ok` directly. |
| Theme | Light + dark via `data-theme` on `<html>` and CSS custom properties; an inline head script applies the saved theme before first paint. |
| CSS shape | ≈ 550 rules. 27 commented sections in a sensible order, then a 170-line "STOCKWISE additions" block appended later (tooltips, tags, banners, replenishment table, demand plan, PO, scope, …) with its own media queries; 6 of its selectors redefine earlier ones. 67 inline `style=` in markup, 93 inside JS templates. |

### A2. Problems

1. **One 217 KB file** mixes styles, markup and all behaviour; every change touches the same file, diffs are hard to
   review, and the browser cannot cache CSS/JS separately from the page.
2. **CSS order is historical, not by responsibility**: the "additions" block mixes shared components with page
   styles and sits after the responsive rules.
3. **JS has no module boundaries**: shared helpers, API access, UI widgets and page logic share one global scope;
   section names are stale (`TAB 2: FORECAST`, `TAB 3: INVENTORY`, `TAB 4: ETL` do not match today's pages).
4. **Inline handlers everywhere** couple markup to global function names (blocks ES modules / bundling).
5. **API calls are spread** across page code with literal URLs and two error-handling styles.
6. No automated frontend regression test in the repo (earlier Playwright scripts lived outside it).

### A3. Proposed structure (adapted to the stack)

No server-side page routing exists, so template inheritance (`base.html` + `pages/*.html`) would require Jinja and
a backend change for no functional gain. The SPA stays one HTML shell; CSS and JS move to `static/`:

```
templates/dashboard.html      HTML shell + markup (tiny inline theme bootstrap kept to avoid a flash)
static/css/                   foundations · layout · components · pages/* · responsive (load order = cascade)
static/js/core/               format, api, ui widgets, charts, settings/theme, auth
static/js/pages/              overview, replenishment, analysis, po, upload
static/js/app.js              tab switching, keyboard shortcuts, start-up
```

Scripts stay **classic scripts loaded in order** (not ES modules): behaviour, globals used by inline handlers and
initialisation order stay exactly as today. Converting to modules needs the inline handlers removed first —
listed as Phase 5 work.

### A4. Required backend/deploy change (approved by San, 2026-11-03)

Serving `static/` needs `app.mount("/static", StaticFiles(...))` in `app.py` (local / fallback) and a
`@vercel/static` build in `vercel.json` (the `/static/(.*)` route already existed). No API, business rule or auth
change; `/static` is outside the `/api/*` login guard, like the page itself.

---

## B. Implementation

Work was done in three steps on branch `phase4-frontend`, each verified with the browser regression (section C).

### B1. Files

| Action | Path | Notes |
|---|---|---|
| Modified | `templates/dashboard.html` | 1,521 → 367 lines (217 → 47 KB). Markup only, plus `<link>`/`<script src>` tags. Kept inline: the 1-line theme bootstrap in `<head>` (must run before first paint). |
| Created | `static/css/foundations.css` | design tokens light + dark, base elements, glass surface |
| Created | `static/css/layout.css` | sidebar navigation, main area, page header, tab containers |
| Created | `static/css/components.css` | cards, KPI strip, tables, inputs, dropdowns, buttons, badges/tags, segmented controls, date popovers, paging, modals, toasts, tooltips, banners, explain panel, shared cell styles |
| Created | `static/css/pages/{login,overview,replenishment,analysis,po,upload}.css` | page-only styles |
| Created | `static/css/responsive.css` | all breakpoints (1100 / 900 / 768 px), loaded last |
| Created | `static/js/core/format.js` | number / money / date / HTML-escape helpers |
| Created | `static/js/core/state.js` | shared filters (`PT`, `BS`), data caches, chart handles, `loadFcMap` |
| Created | `static/js/core/api.js` | session-aware `fetch` wrapper (401 → login), `readJson` |
| Created | `static/js/core/ui.js` | icons, badges, tooltips, freshness banner, paging, SVG rings/sparklines, dropdowns, CSV download, toasts, count-up |
| Created | `static/js/core/charts.js` | Chart.js theming (`getCT`, `baseOpts`, `areaFill`) |
| Created | `static/js/core/settings.js` | settings modal, theme, greeting, default period |
| Created | `static/js/core/auth.js` | login, logout, `startApp` |
| Created | `static/js/pages/{overview,replenishment,analysis,po,upload}.js` | one file per page |
| Created | `static/js/app.js` | tab switching, keyboard shortcuts, start-up (loaded last) |
| Modified | `app.py` | `app.mount("/static", StaticFiles(directory="static"))` — see A4 |
| Modified | `vercel.json` | `@vercel/static` build for `static/**` |
| Created | `tests/e2e/*`, `tests/unit/test_frontend_assets.py` | regression harness, asset wiring test |
| Modified | `tests/regression/scenario.py` | page step checks the page contract instead of its byte length |

No file was removed. Intermediate `static/css/app.css` / `static/js/app.js` (step 1) were replaced by the split
files in step 2–3.

### B2. How the split was done (and why it is safe)

- **Moved verbatim by a parser, not by hand.** CSS: every top-level rule assigned to a file by its original
  section; the later "additions" block by selector. JS: every top-level statement (acorn parse) assigned by name
  or section, with the comments that preceded it. Check after the move: the multiset of CSS rules (477) and of JS
  statements (235) is identical to the original.
- **CSS**: file order = cascade order. The "additions" rules moved ahead of the responsive block; the regression
  shows no computed value changed.
- **JS**: classic scripts in a fixed order (core → pages → app). All top-level executable statements only
  register listeners or start the app, and every function they need at load time is defined earlier. Global
  names used by inline handlers are unchanged.
- **API layer**: the existing single `fetch` wrapper and `readJson` were moved to `core/api.js`. Call sites were
  **not** rewritten: URLs, methods, payloads and error handling stay byte-for-byte as before (regression compares
  every API call). A typed client is left for Phase 5 (D2).

### B3. Cleanups (behaviour-neutral, verified by search + regression)

| Removed | Why it was dead |
|---|---|
| `setProductType()` + its `.type-seg` click listener | no `.type-seg` element exists in markup or generated HTML; the listener attached to nothing |
| `setBestSellerOnly()` | never called; targets `.bs-only`, which does not exist |
| `urgBadge()`, `URG_O` | urgency was removed from the UI (decision D3); never called / never read |
| `weeklyPromise` variable | declared, never read (`weeklyPromises` map is used instead) |
| stale section names (`TAB 2: FORECAST`, `TAB 3: INVENTORY`, `TAB 4: ETL`) | replaced by per-file headers named after today's pages; one stale sort comment corrected (risk cards sort by order-by date, not urgency) |

## C. Regression tests

**Harness** (`tests/e2e`, committed): seeds a local Postgres with the synthetic fixtures, prepares the same data
every run (forecast, PO master list, Hero list, stock snapshot), starts the app, and drives Chromium through
**78 checkpoints** in 3 contexts — desktop 1440 px light (with data edits), desktop dark, mobile 390 px light.
Browser clock fixed, Chart.js served locally with animations off. Per checkpoint it records: visible text,
every `/api/*` request (method, URL, body), active navigation, theme, page width (horizontal scroll), number of
charts, console/page errors; for 36 checkpoints a full-page screenshot; on desktop-light a hash of **every
computed property of every element and its ::before/::after** (catches CSS changes on hidden elements too);
CSV exports are saved and compared byte-for-byte.

```
TEST_DATABASE_URL=postgresql://postgres@localhost:5432/stockwise_test python tests/e2e/run_regression.py --root <original checkout> --out A
TEST_DATABASE_URL=postgresql://postgres@localhost:5432/stockwise_test python tests/e2e/run_regression.py --out B
python tests/e2e/compare.py A B --tolerance 0.3
```
(`npm install` in `tests/e2e` once; tolerance = share of changed pixels per screenshot, set from two runs of the
unchanged code, which differ by ≤ 0.25 % from rendering noise.)

**Flows covered**

| Module | Covered |
|---|---|
| Login | page, wrong password message, login |
| Overview | KPIs, charts, period 8W, risk sort by revenue, best-seller scope list + "company list only" filter |
| Replenishment | list, All / Order-now tabs, source and status filters, quantity edit, CSV export, row detail |
| Product Analysis | Stock & Coverage table, CSV export, SKU drawer (open / Esc), Demand Plan units / sales, weekly table |
| PO & Supplier | by PO / by product, status filter, received-quantity edit (desktop-light) |
| Upload | page and data status, wrong file type error, Hero list upload success |
| Shared | sidebar navigation + active state, settings modal, theme toggle, keyboard shortcuts 1–5, light/dark, 390 px layout |

**Results**

| Comparison | Text | API calls | CSV | Errors | Screenshots | Computed styles |
|---|---|---|---|---|---|---|
| Unchanged code vs itself (noise check) | identical | identical | identical | none | ≤ 0.25 % noise | — |
| Step 1 (CSS/JS moved to `static/`) vs `main` | identical | identical | identical | none | identical (≤ tolerance) | only time-dependent animation values (skeleton shimmer, toast fade) → harness now freezes animations |
| Steps 2–3 (split + cleanups) vs `main` | identical | identical | identical | none | identical (≤ tolerance) | see final run below; a direct value-by-value check (2,259 elements × 3 contexts, login state) found **0 value differences** — only the enumeration order of custom properties changed, so the hash now sorts properties |

**Final run** (steps 1–3 together vs `main@5194337`, harness with frozen animations and sorted properties):
78 checkpoints and 36 screenshots — text, API calls, CSV exports, errors (none), screenshots and computed styles
of every element **IDENTICAL**. Server log without tracebacks. Backend suite: 36 passed (unit 26 incl. layering
and asset wiring, integration, API contract, old-vs-new API regression).

**Not covered automatically**: real Accurate sales ETL upload + confirm, stock / food / master / PO uploads via
the browser (covered by API integration tests), Refresh forecast button, drag & drop, custom date range and month
grid, hover-only states, browsers other than Chromium, production data volume. Untested against Vercel until the
branch preview is deployed (static file serving there is the main new risk).

## D. Handover for Phase 5

### D1. Final structure

```
templates/dashboard.html          markup (login, sidebar, 5 page sections, SKU drawer, settings modal)
static/css/
  foundations.css  layout.css  components.css
  pages/login.css  overview.css  replenishment.css  analysis.css  po.css  upload.css
  responsive.css                  (load order in dashboard.html = cascade order)
static/js/
  core/format.js  state.js  api.js  ui.js  charts.js  settings.js  auth.js
  pages/overview.js  replenishment.js  analysis.js  po.js  upload.js
  app.js                          (load order in dashboard.html matters: core → pages → app)
tests/e2e/                        browser regression (run before and after every frontend change)
```

Rule of thumb for Phase 5: a style used on one page goes in `pages/<page>.css`; used on two or more pages →
`components.css`; colours only through tokens in `foundations.css` (light and dark). Page logic goes in
`pages/<page>.js`; anything a second page needs moves to `core/`.

### D2. Remaining technical debt

| # | Debt | Suggested fix | Effort / risk |
|---|---|---|---|
| T1 | ~110 inline `on*=` handlers in markup and many in JS-generated HTML; everything is global | event delegation with `data-action` attributes, page by page; then ES modules | medium; must be done before a bundler/modules |
| T2 | API calls are literal URLs spread over page files; two error-handling styles (`readJson` vs `r.json()`) | small client in `core/api.js` (`api.get(path, params)`, one error path), migrate per page with the harness | medium; harness compares every call |
| T3 | 67 inline `style=` in markup, 93 in JS templates | move to classes in `components.css` / page CSS during the redesign | low per page |
| T4 | Large page files (`analysis.js` 29 KB, `overview.js` 28 KB) with long one-line functions | format with Prettier and split render vs data functions when a page is redesigned | low |
| T5 | `components.css` (21 KB) is broad | split further (tables, forms, overlays) when Phase 5 touches them | low |
| T6 | Markup still one file | Jinja2 partials would need a backend dependency + route change; only worth it if server rendering is ever needed | — |
| T7 | Chart.js / Inter from CDNs | pin with SRI hashes, or self-host under `static/vendor` | low |
| T8 | No cache-busting on `/static` files | add `?v=<commit>` or hashed names if users see stale CSS/JS after a deploy (Vercel revalidates by default) | low |

### D3. Recommendations for Phase 5 (UI/UX)

1. Decide the forecast-related decisions first (`docs/forecast-accuracy.md` §8, F1–F6): the reliability label,
   replacement of the "accuracy" ring and the zero-forecast warning are UI work that depends on them.
2. Redesign one page at a time; after each page run the harness and review **intended** differences (screenshots
   and text will change on purpose — check that API calls and CSV exports stay identical unless a change is
   planned).
3. Start with T1/T2 on the page being redesigned, not globally.
4. Action Center buckets (spec §9.2, D2) are the first new UI feature; they use existing fields only.

### D4. Backend / deploy items

- Only change outside the frontend: `/static` serving (A4). Verify on the Vercel preview that
  `/static/css/foundations.css` and `/static/js/app.js` return 200 before merging.
- No API, schema, business-rule, forecasting or auth change. Evidence: API-call logs, page text and CSV exports
  identical to `main` in every checkpoint; backend test suite unchanged and green.
