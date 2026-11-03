# Forecast accuracy analysis

Reproducible, read-only evaluation behind `docs/forecast-accuracy.md`. Nothing here writes to the database or
changes the app; the model is imported from the production `forecast.py`.

| File | Purpose |
|---|---|
| `export.sql` | The two read-only SELECTs that produce the input CSVs |
| `evaluate.py` | Audit metrics: in-sample vs holdout, weekly vs 4-week, MAPE/WAPE/MASE/bias, low-volume weeks, per-SKU, demand classes, rolling origin |
| `experiments.py` | Offline what-if: Holt variants vs naive / moving-average benchmarks on the same rolling origins |
| `data/` | Input CSVs (**git-ignored** — company sales data; the repo is public) |
| `output/` | Results; only `experiments.txt` (aggregates) is committed, per-SKU files and summaries with revenue stay local |

Run from the repo root:
```
py analysis/forecast_accuracy/evaluate.py --weekly analysis/forecast_accuracy/data/weekly_2026-11-03.csv --scope analysis/forecast_accuracy/data/scope_2026-11-03.csv --last-full-week 2026-10-19 --out analysis/forecast_accuracy/output
py analysis/forecast_accuracy/experiments.py --weekly analysis/forecast_accuracy/data/weekly_2026-11-03.csv --scope analysis/forecast_accuracy/data/scope_2026-11-03.csv --last-full-week 2026-10-19
```

Input used for the 2026-11-03 report (verify a re-export with `sha256sum`):

| File | Rows | SHA-256 |
|---|---|---|
| `scope_2026-11-03.csv` | 194 SKUs + header | `e726cd84ef5ec58b124e4ad26952a516766acbe8b49b843adbe85caa89b18730` |
| `weekly_2026-11-03.csv` | 193 SKUs (one Hero SKU has no sales) | `c33181b60dc1680a99f475eb9c6650bcefec8e0f4fce28b5c9818e276a9002e8` |

Integrity check done on export: every row's weekly sum equals its total and its length matches the weeks from
first sale to 2026-10-26; re-running the production grid search on the export reproduced α, β and MAPE of all
185 Holt SKUs exactly.
