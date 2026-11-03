# Phase 5 — Design System

Implemented in `static/css` (no framework, no new dependency). Light and dark come from the same tokens
(`foundations.css`, `:root` and `:root[data-theme="dark"]`). Phase 5 refined the existing system instead of
replacing it (decision D4: keep the theme, improve hierarchy and density).

## 1. Foundations

| Token group | Values | Notes |
|---|---|---|
| Surfaces | `--bg` page, `--surface` cards / drawer, `--surface2` inset panels, `--surface-hover` row / button hover, `--seg` track of segmented controls and bars | **Cards are solid now** (`--surface` + 1 px `--border` + `--card-shadow`). Glass (`.glass`) is kept only for the floating navigation. |
| Text | `--text` primary, `--text2` secondary, `--text3` labels / meta | Contrast checked against both themes by eye on screenshots; not measured with a tool. |
| Brand | `--primary` (buttons), `--navy` (active nav, accents), `--link` | |
| Status (semantic) | `--red` / `--red-bg` = Understocked, Order now, errors · `--amber` / `--amber-bg` = Within 2 weeks, warnings · `--green` / `--green-bg` = Healthy, No action needed, success · `--slate` = Overstocked · `--blue` / `--blue-bg` = Hold, info | Same meaning on every page. **Never colour alone**: every coloured dot / bar / chip has a text label next to it. |
| Charts | `--chart-1 … --chart-4`, `--grid` | Read at render time (`core/charts.js`), so charts follow the theme. |
| Radius | `--radius` 14 px (cards), `--radius-sm` 10 px (inner panels, buttons, inputs), `--radius-xs` 6 px (chips) | Was 20 / 12 / 8 — fewer "bubbles inside bubbles". |
| Spacing | `--space-1 … --space-6` = 4, 8, 12, 16, 24, 32 px | Cards 20 px padding, 16 px gap between cards. |
| Elevation | `--card-shadow` (cards), `--shadow-pop` (menus, popovers), drawer shadow | Two levels only. |
| Focus | `--focus` ring on `:focus-visible` | Keyboard users see where they are. |
| Typography | Inter; page title 30 px / 700; section title 18 px / 600; body 15 px; table 14 px; meta 12–13 px; numbers `tabular-nums` | Unchanged scale, used consistently. |

## 2. Layout

Floating left navigation (now with **visible labels** under each icon) + main column (max 1840 px). Breakpoints in
`responsive.css`: 1100 px (2-column grids → 1–2 columns), 900 px, 768 px (navigation moves to a bottom bar, tables
scroll horizontally inside their card, non-essential table columns hide).

## 3. Components

| Component | Class / file | Behaviour |
|---|---|---|
| Card | `.card` (`components.css`) | solid surface, section title `h2.section-label`, optional `.section-meta` |
| Section header | `.card-header` | title left, one action right (link-style) |
| Buttons | `.btn-brand` primary (one per view), `.btn-ghost` secondary, `.link-btn` navigation, `.icon-btn` close | Labels say what happens ("Export order list", never "Create PO") |
| Inputs / search | `.search-box`, `.qty-in`, date inputs | `/` focuses the page search |
| Filters | `.seg` segmented, `.tabs-count` tabs with counts, `.cdrop` dropdown | Counts in tabs are computed from the same data as the table |
| Tables | `.ro-table`, `.tbl-wrap` (horizontal scroll on small screens), `tr.clickable`, `tr.row-selected` | Row click opens detail; inputs inside rows stop propagation |
| KPI / metric tile | `.kpi-strip` (PO, Sales), `.hl-item` (Overview health), `.ac-b` (Action Center bucket) | Number + label + one line of context; no oversized tiles |
| Status chip | `statusBadge()` → Understocked / Healthy / Overstocked / Not classified | text + colour |
| Bucket chip | `.tag-b-crit / warn / info / ok` | same tones as the Action Center |
| Side drawer | `.drawer-backdrop` + `.drawer` (`#rpDrawer`) | Right side, keeps the list visible; Esc / ✕ / backdrop click closes; body scroll locked |
| Modal | `.modal-backdrop` + `.modal` | SKU history (`#skuDrawer`) and Settings |
| Disclosure | `details.disc` | Secondary detail collapsed by default (projection, PO lines, comparisons) |
| Explanation | `.why-box` | Bulleted "Why" from `explainSku()` |
| Tooltip | `.info` "i" + `#tipBox` | Definitions; hover, focus and tap |
| Banners | `.fresh-line` (data freshness), `.banner.warn` | Only when there is something to act on |
| Loading | `.loader-full`, `.skel`, `.loading-spinner` | |
| Empty / error | `.state`, `.state-error` | Says what happened and offers the next action (Retry, go to Data) |
| Toasts | `toast(msg, type)` | Result of an action |
| Sticky action bar | `.sel-bar` (Replenishment) | Selection totals + export, stays visible while scrolling |

## 4. Content rules

- English UI text everywhere (upload instructions were Indonesian before).
- One date format for display: `10 Aug 2026` (`dfmt`). Browser date inputs keep their native format.
- No Pareto / company-list wording in the UI for now (decision A3).
- Forecast wording is method-neutral on decision pages ("forecast", not a method name); no reliability labels
  (decision A4). Product Analysis keeps factual method descriptions where they already existed.
