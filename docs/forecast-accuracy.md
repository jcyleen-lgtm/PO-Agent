# Stockwise — Forecast Accuracy Analysis

Version 1.0 · 2026-11-03 · read-only analysis of the production forecast after the Hero refresh (194 SKUs).
Basis for the Phase 4 dashboard and for BAB 4. **Nothing in production was changed** (forecasts, α/β, data,
API, dashboard). Reproduce with `analysis/forecast_accuracy/` (see its README; input checksums listed there).

---

## 0. Summary

1. **The headline problem is not the metric, it is the forecast.** Out-of-sample, weekly Holt's DES is worse than
   simple benchmarks: over 997 rolling-origin forecasts its weekly WAPE is **110.6 %** against **71.7 %** for a
   4-week moving average and **67.7 %** for an 8-week moving average; it is the best method for only **6 of 169
   SKUs**. No tuning of how α/β are chosen closes the gap (102–111 %).
2. In-sample MAPE (the number the app shows today) **looks bad and is also biased**: for the median SKU, weeks
   of 1–4 units are 24 % of its selling weeks and 4.6 % of its units but produce **56 % of its MAPE**; weeks with 0 units (median
   12 %) are silently dropped; α/β are chosen by minimising this same number on the same data.
3. Week-level error is the wrong target for purchasing. The replenishment rules use the **8-week total**
   (coverage, safety stock = 10 % of the 8-week forecast). Measured on that total, Holt's error is 99.8 % vs
   47–52 % for moving averages.
4. **WAPE alone would hide problems**, as feared: the pooled holdout WAPE (76 %) is far below the median SKU
   (95 %); Hero-only SKUs are much worse (pooled 146 %) than Pareto SKUs (71 %).
5. Two pipeline issues found: the **last, partial week is used as a full week** (data end Thursday 2026-10-29),
   which drags recent levels down — **22 SKUs currently have a 12-week forecast of exactly 0**; and Holt often
   extrapolates a short-term trend (α = 0.9 for 50 SKUs), giving **+29.5 % over-forecast** overall while 14 % of
   its forecasts collapse to zero.
6. Recommended metric set (§7): out-of-sample, complete weeks only, rolling origin; **8-week-total error + bias**
   per SKU for decisions; **MASE** (vs naive) to show whether the model adds value; **WAPE distribution**
   (median/P75, per group/class) instead of one pooled number; MAPE only as a secondary, clearly-defined figure.
7. Decisions needed before Phase 4 (§8): what the replenishment page should rely on when Holt is unreliable, and
   how BAB 4 frames the result (with the advisor).

## 1. Data and method

| Item | Value |
|---|---|
| Source | `sales` (import products), weekly `DATE_TRUNC('week')`, zero-filled from each SKU's first sale — identical to `forecast_repo.weekly_import_sales_df` |
| Scope | 194 SKUs in `forecast_results` (2026-11-03 13:46 WIB): 185 Holt's DES, 9 naive fallback (excluded from model metrics) |
| History | 2024-12-30 … 2026-10-26 (sales to Thu 2026-10-29) |
| Main cut-off | last **complete** week starting 2026-10-19 (the partial week is analysed separately, §2 A4) |
| Model | production `forecast.holts_des` / `grid_search_holts` imported unchanged; the export reproduces α, β and MAPE of all 185 SKUs exactly |
| Fixed-origin holdout | production definition: last 8 weeks (weeks of 2026-08-31 … 2026-10-19), α/β chosen on train only, needs ≥ 12 train weeks → 169 SKUs |
| Rolling origin | 6 origins, 4 weeks apart, 8-week horizon each, train ≥ 12 weeks → 997 forecasts on 169 SKUs (robustness, Tashman 2000) |
| Benchmarks | naive (last week repeated); MA4 (mean of last 4 weeks = production fallback); MA8 |

### Metric definitions (per SKU over the evaluated weeks; a = actual, f = forecast)

| Metric | Formula | Reads as | Weak spot |
|---|---|---|---|
| MAPE | mean(\|a−f\| / a) × 100 over weeks with a > 0 | average % miss per week | explodes on small a; ignores a = 0 weeks |
| WAPE (= WMAPE) | Σ\|a−f\| / Σa × 100 | units missed per unit sold | pooled value dominated by big SKUs/weeks |
| 8-week total error | \|Σf − Σa\| / Σa × 100 over the 8 weeks | error of the quantity a purchase is based on | errors inside the window can cancel |
| Bias | (Σf − Σa) / Σa × 100 | + over-forecast (overstock risk), − under-forecast (stock-out risk) | — |
| MASE | mean\|a−f\| / mean\|aₜ − aₜ₋₁\| (train) | < 1 = better than the naive rule | not intuitive for users |
| Demand class | ADI and CV² of non-zero weeks (Syntetos–Boylan–Croston 2005): smooth, erratic, intermittent, lumpy | how forecastable the pattern is | needs ≥ 2 non-zero weeks |

## 2. Step 1 — Audit of the current calculation

| # | Finding | Effect |
|---|---|---|
| A1 | In-sample MAPE is one-step-ahead on the training data, first week skipped; α/β are chosen by minimising it | optimistic and not an accuracy measure; it is the fitting criterion |
| A2 | Weeks with actual = 0 are excluded from MAPE (median 11.6 % of weeks, P90 37 %) | errors on zero weeks never count; intermittent SKUs look better than they are |
| A3 | Fitted values are not clipped at 0 (150 of 185 SKUs have negative fitted weeks); forecasts are clipped to [0, 2.5 × max] | inflates in-sample MAPE; forecasts can become exactly 0 |
| A4 | **The last week (2026-10-26) has 4 of 7 days but is used as a full week** | lowers the last level/trend: next-8 / last-8 ratio median 0.7 vs 0.9 on complete weeks; zero 12-week forecasts 22 vs 20 SKUs |
| A5 | Holdout WMAPE (fixed origin, 8 weeks, multi-step) is correct and leak-free, but a single window | one 8-week window is noisy; rolling origin added here |
| A6 | Naive rows store `mape = 999` as "no value" | excluded by the app summary; must be excluded by any other consumer |
| A7 | Dashboard: "accuracy" ring = 100 − portfolio holdout WMAPE; in-sample MAPE colour thresholds 25 / 50 | shows ≈ 0–24 % "accuracy" and is undefined above 100 %; thresholds differ from Lewis (1982) 10/20/50 |
| A8 | Parameters: α = 0.9 for 50 SKUs, β = 0.1 for 75 | many SKUs track the last week almost fully — noise is extrapolated |

In-sample MAPE today on the Lewis scale: 0 SKUs < 20 %, 16 SKUs 20–50 %, 169 SKUs ≥ 50 % ("inaccurate").

## 3. Step 2 — Weekly vs 4-week (≈ monthly)

4-week = non-overlapping 4-week totals (aligned to the end; not calendar months).

| | Weekly model | Weekly model, scored on 4-week totals | Model refitted on 4-week series |
|---|---|---|---|
| In-sample MAPE, median SKU | 94.1 % | — | 75.0 % |
| In-sample WAPE, median SKU | 72.0 % | — | 63.7 % |
| Holdout WAPE, median SKU | 95.1 % | 85.3 % | 65.7 % |
| Rolling origin, 8-week total error (pooled) | 99.8 % | — | 65.7 % |

Aggregation reduces noise and helps Holt materially (8-week total error 99.8 → 65.7 %), but a 4-week Holt is still
behind MA4 (52.1 %) and MA8 (47.3 %) on the same origins (§5).

## 4. Step 3 & 4 — MAPE vs WAPE, and low-volume weeks

| Fixed-origin holdout (169 SKUs) | Median SKU | Pooled |
|---|---|---|
| Holt MAPE | 98.3 % | — |
| Holt WAPE | 95.1 % | 76.0 % |
| Holt bias | −16.9 % | +0.9 % |
| Holt 8-week total error | 82.9 % | — |

The gap between median (95 %) and pooled (76 %) WAPE is the volume effect San warned about: big SKUs are
forecast better than small ones, so one pooled number overstates quality for most SKUs.

Contribution of low-volume weeks to the **in-sample MAPE** (median over SKUs):

| Weekly actual | Share of selling weeks | Share of units | Share of MAPE |
|---|---|---|---|
| 1 unit | 5.0 % | 0.4 % | 18.6 % |
| 2–4 units | 17.2 % | 4.4 % | 27.8 % |
| 5–9 units | 20.6 % | 13.0 % | 15.1 % |
| ≥ 10 units | 45.5 % | 79.1 % | 20.5 % |

(Medians per row; they do not add up. Taken together, 1–4-unit weeks are, for the median SKU, 24.3 % of selling
weeks, 4.6 % of units and 56.2 % of the MAPE.) Dropping weeks below 5 units lowers the median in-sample MAPE from 94.1 % to 60.9 % — still "inaccurate", so
low-volume weeks inflate MAPE but **do not explain the whole problem**.

## 5. Step 5 — Consistency per SKU, group and benchmark

**Benchmarks, rolling origin (997 forecasts, 169 SKUs, complete weeks):**

| Method | Weekly WAPE | 8-week total error | Bias | Forecast = 0 | Best for SKUs |
|---|---|---|---|---|---|
| Holt's DES, production (81 combos, min in-sample MAPE) | 110.6 % | 99.8 % | +29.5 % | 14.4 % | 6 |
| Holt, 81 combos, min in-sample MAE | 106.5 % | 95.2 % | +25.9 % | 17.8 % | 4 |
| Holt, β ≤ 0.3, min MAPE | 102.1 % | 90.8 % | +20.8 % | 15.6 % | 0 |
| Holt on 4-week series | 82.9 % | 65.7 % | −0.1 % | 10.4 % | 22 |
| Naive (last week) | 81.1 % | 66.6 % | −0.7 % | 21.7 % | 37 |
| MA4 (production fallback) | 71.7 % | 52.1 % | −8.0 % | 6.4 % | 28 |
| MA8 | 67.7 % | 47.3 % | −8.9 % | 2.1 % | 72 |

Fixed-origin check agrees: Holt MASE median 1.2 (worse than naive in-sample scale); MASE < 1 for 66/169 SKUs;
Holt beats naive on 54/169 and MA4 on 59/168 SKUs. With the partial week included (production setting) the
ranking is the same (Holt 114.3 % / MA8 69.6 % weekly WAPE).

**By demand class** (Syntetos–Boylan–Croston):

| Class | SKUs | In-sample MAPE (median) | Holdout WAPE (median) | Holdout MASE (median) |
|---|---|---|---|---|
| Smooth | 54 | 80.1 % | 69.1 % | 1.33 |
| Erratic | 84 | 102.1 % | 98.5 % | 1.09 |
| Intermittent | 21 | 76.9 % | 100.0 % | 1.21 |
| Lumpy | 26 | 105.7 % | 100.0 % | 1.96 |

Only 54 of 185 SKUs are "smooth", the pattern Holt's DES is designed for; 131 are erratic, intermittent or lumpy.

**By scope group** (holdout): Pareto 130 SKUs — median WAPE 91.2 %, pooled 71.0 %; Hero-only 39 SKUs — median
100 %, pooled 146.2 % (short history, many from Dec 2025 or later).

**Important SKUs:** the 20 highest-revenue SKUs carry 32 % of holdout units; their pooled WAPE is 65.1 % (rest
81.2 %), yet **15 of the 20 have WAPE > 50 %** and several have a holdout forecast of 0 or a bias beyond ±65 %.
A good aggregate would therefore hide high errors on SKUs that matter (per-SKU list in the local output file).

## 6. What this means for decisions

- A purchase quantity depends on the **8-week total** and its **direction of error**. Holt over-forecasts in
  total (+29.5 % → overstock and cash tied up) while 14 % of its forecasts are 0 (→ "no need to order" → stock-out
  risk on best sellers). Both directions occur on the same portfolio, so a single average hides them.
- 22 SKUs in production now carry a 12-week forecast of 0; their status, order-by date and recommended quantity
  are driven by that 0.
- A per-week accuracy number, even a correct one, does not tell the user whether to trust a SKU's
  recommendation. A per-SKU reliability signal does.

## 7. Recommendation — metrics for the dashboard and BAB 4

**Evaluation protocol (both):** out-of-sample only; complete weeks only; rolling origin (6 origins × 8 weeks,
4-week step) with α/β re-chosen on each training window; report alongside naive and MA benchmarks.

| Use | Metric | Why |
|---|---|---|
| Decision (per SKU, dashboard) | **8-week total error** and **bias** (over/under) | matches what replenishment uses; shows overstock vs stock-out risk |
| Is the forecast worth using? (per SKU + portfolio) | **MASE** vs naive, and "better than MA benchmark?" | scale-free, handles zero weeks (Hyndman & Koehler 2006) |
| Portfolio quality | **WAPE distribution**: median, P75, share of SKUs ≤ threshold; split by group (Pareto/Hero) and demand class; pooled WAPE only next to these | answers San's concern that pooled WAPE hides small SKUs (Kolassa & Schütz 2007 on WAPE) |
| Literature comparability (BAB 4 only) | **MAPE**, out-of-sample, zero weeks excluded and their share reported, with Lewis scale | reviewers expect it; never as the only or headline metric |
| Not to be presented as accuracy | in-sample MAPE | it is the fitting criterion (A1) |

Dashboard (Phase 4, proposal): replace the "accuracy" ring with (1) a per-SKU reliability label from the rolling
evaluation, (2) a warning on SKUs whose forecast is 0 or whose bias is beyond a threshold, (3) the demand class,
and (4) a portfolio panel with median/P75 WAPE and the benchmark comparison. Thresholds are a decision (§8).

## 8. Open decisions (for San, some with the advisor)

| ID | Question | Options | Suggestion |
|---|---|---|---|
| F1 | Partial last week (A4) | use complete weeks only / keep | complete weeks only — a data-handling fix, no method change |
| F2 | What replenishment relies on when weekly Holt is worse than a benchmark | (a) keep Holt everywhere, show reliability flag; (b) Holt on 4-week series; (c) per-SKU choice between Holt and MA by rolling error; (d) add a method for intermittent demand (e.g. Croston/SBA) | discuss with advisor: (c) keeps Holt as the studied method and avoids acting on a forecast known to be worse; (d) changes thesis scope |
| F3 | How α/β are chosen | in-sample MAPE (now) / out-of-sample error | out-of-sample, if the method is revisited at all |
| F4 | BAB 4 framing | report Holt's measured suitability per demand class with benchmarks | this is a valid result: the proposal says suitability is evaluated in the results chapter |
| F5 | Reliability thresholds | e.g. reliable = MASE < 1 and 8-week error ≤ 50 %; watch = one of them; unreliable = neither or forecast 0 | to be set after F2 |
| F6 | Zero sales vs stock-out | treat zero weeks as zero demand (now) / mark as censored when stock was 0 | needs weekly stock history; only 2 snapshots exist today → limitation |

## 9. Limitations

- One company, 22 months of data; Hero SKUs mostly from late Dec 2025; rolling origins cover only about the last
  7 months.
- Zero-sales weeks may be stock-outs, not zero demand (censored demand); cannot be separated with 2 snapshots.
- 4-week blocks are not calendar months. MA8 was added as a benchmark in the same run as the others, so its
  "best" status should be confirmed on later data before relying on it.
- References to verify before citing: Lewis (1982) *Industrial and Business Forecasting Methods*; Hyndman &
  Koehler (2006) IJF 22(4); Syntetos, Boylan & Croston (2005) JORS 56(5); Tashman (2000) IJF 16(4);
  Kolassa & Schütz (2007) *Foresight* 6.
