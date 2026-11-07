# Phase 5 — Stage 2: User Flow and Information Architecture (navigation)

Primary user: **purchasing staff**. Stockwise supports the decision; the purchase itself happens outside Stockwise
(supplier / agent, then the PO is entered in Accurate). Confirmed constraints (San, 2026-11-03):

- No Order / Hold / Monitor state is stored or added. The decision is made by the user; in Stockwise it shows up
  only as the quantity they keep and the rows they export.
- Replenishment keeps its current function. The Company-list / Pareto-only split is not shown as a feature
  (data stays; Pareto logic to be discussed later).

## 1. Sitemap

```
Stockwise
├── Login
├── 1 Overview                 what needs attention now
├── 2 Replenishment            evaluate each SKU, set quantities, export the order list
│     └── SKU detail (drawer)  why flagged, numbers behind the recommendation, open POs
├── 3 Product Analysis         investigate demand when more context is needed
│     ├── SKU view             sales history + forecast chart, stock, POs   (drawer, also opened from 2 and 4)
│     ├── Stock & coverage     portfolio table (filters, export)
│     └── Demand plan          monthly / weekly plan vs actual
├── 4 PO Tracking              follow POs after they were placed; record receipts
│     └── PO / line actions    received qty, received date, close short, cancel, mark all received
├── 5 Data                     keep data current: sales, stock, PO list, SKU lists; refresh forecast; data status
└── Settings (modal)           name, theme, default period
```

Rename proposals (labels only): "PO & Supplier" → **PO Tracking** (one agent in the data, no supplier workflow);
"Upload Data" → **Data** (holds status + uploads + refresh). Order and keyboard shortcuts 1–5 stay.

## 2. High-level flow

```
LOGIN
  │
  ▼
OVERVIEW ── "nothing urgent" ──────────────────────────────► (done for today / check PO Tracking)
  │ Action Center bucket or SKU
  ▼
REPLENISHMENT (filtered to that bucket)
  │ open SKU → drawer: reason, demand, stock, incoming PO, lead time, safety stock, recommended qty
  │ (optional) "View demand history" → Product Analysis SKU view → back
  ▼
PURCHASING DECISION (by the user, not stored)
  ├── ORDER: keep / adjust quantity → select → Export list (CSV)
  │        │
  │        ▼
  │   EXTERNAL PURCHASE (agent / supplier, PO entered in Accurate)    ← outside Stockwise
  │        │ supplier confirms
  │        ▼
  │   DATA → upload PO master list (Purchasing "Master List")
  │        │
  │        ▼
  │   PO TRACKING → record received qty / date → close short or cancel when needed
  │        │
  │        ▼
  │   DATA → weekly stock snapshot + sales upload → INVENTORY UPDATE → back to OVERVIEW
  │
  └── HOLD / MONITOR: leave quantity at 0 or do not export → SKU stays on the list → re-checked next review
```

Wording rule in the UI: buttons say **"Export order list"** / **"Order quantity"**, never "Place order" or
"Create PO" — nothing in Stockwise sends an order.

## 3. Page-to-page flows (target)

Today only some of these links exist (risk card "Plan order" → Replenishment; Purchase Recommendations "Open";
SKU links on the PO page open the SKU drawer). The rest are proposed and use only data already on the client.

| From | Trigger | To | Context carried |
|---|---|---|---|
| Overview Action Center | bucket ("Order now", "Within 2 weeks", "Hold") | Replenishment | bucket filter applied |
| Overview Action Center | SKU row | Replenishment + SKU drawer open | SKU |
| Overview health line | status count (e.g. Understocked) | Replenishment | status filter |
| Overview data line | "stock is 9 days old" warning | Data | — |
| Replenishment drawer | "Demand history" | Product Analysis SKU view | SKU; "← Back to Replenishment" returns with filters intact |
| Replenishment drawer | open PO line | PO Tracking | PO number search |
| Product Analysis table / drawer | "Plan order" | Replenishment + drawer | SKU |
| PO Tracking | SKU link | Product Analysis SKU view | SKU |
| Data | upload success | stays; shows what changed and what to do next (e.g. "Refresh forecast") | — |
| Any page | sidebar / keys 1–5 | page | page keeps its last filters in the session |

## 4. Decision branches inside Replenishment

| Situation (existing fields) | What the user sees | Typical decision |
|---|---|---|
| Order-by date passed (`order_by_days ≤ 0`, not estimate) | "Order now", days late, reason | Order (and consider faster shipping) |
| Order-by within 14 days | "Order within 2 weeks" | Order in this review |
| Overstocked, or recommended 0 with confirmed incoming PO | "Hold" + coverage / incoming | Hold |
| Everything else | "No action this cycle" (order-by later / healthy) | Monitor |
| PO without ETA (`unconfirmed_incoming > 0`) | flag "PO without ETA not counted" | Check the PO, then decide |
| Forecast estimate (naive / short history) | flag "estimate" | Decide with more caution / check history |

These are the approved Action Center buckets (spec §9.2, decision D2), computed from fields the API already returns
(`order_by_days`, `order_by_is_estimate`, `status`, `reco_qty`, `confirmed_incoming`). They label the situation; they
do not store a decision.

## 5. Screen inventory

| Screen / state | Type | Exists today | Phase 5 change |
|---|---|---|---|
| Login | page | yes | restyle only |
| Loading (wait screen, skeletons) | state | yes | keep, align with design system |
| Overview | page | yes | **restructure** (Stage 3) |
| Replenishment list | page | yes | layout, remove source filter, add demand column |
| SKU drawer (replenishment) | drawer | detail panel below table | **move into a side drawer**, group content |
| SKU view (analysis) | drawer/modal | yes (modal) | same drawer component, add back link |
| Stock & coverage table | page section | yes | fewer columns by default, one status filter |
| Demand plan | page section | yes | add metric explanation, method-neutral labels |
| PO Tracking list | page | yes | status legend, row actions in a menu, consistent dates |
| PO line edit | inline | yes | keep inline qty, confirm close-short / cancel |
| Data page | page | yes | group by frequency, add refresh forecast, remove "Coming in P2" card, consistent validation messages |
| Sales upload preview / confirm | inline step | yes | keep |
| Settings | modal | yes | keep |
| Toasts, tooltips, empty / error states | components | yes | unify (design system) |
| Explain panel ("why") in demand plan | panel | yes | reuse as the "why flagged" pattern |
