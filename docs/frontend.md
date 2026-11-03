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
