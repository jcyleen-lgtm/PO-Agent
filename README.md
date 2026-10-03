# PO Agent — Web-Based Purchase Decision Support System

BI dashboard with Holt's Double Exponential Smoothing for inventory control.

## Stack
- **Backend:** FastAPI (Python)
- **Database:** PostgreSQL via Supabase
- **Frontend:** HTML/CSS/JavaScript + Chart.js
- **Forecasting:** Holt's DES with grid search (α 0.1–0.9, β 0.1–0.9)

## Files
| File | Purpose |
|---|---|
| `app.py` | FastAPI backend — all endpoints |
| `etl.py` | ETL: clean & validate sales data from ERP Accurate |
| `forecast.py` | Holt's DES forecasting + replenishment logic |
| `templates/dashboard.html` | Single-page dashboard frontend |
| `schema.sql` | PostgreSQL schema (5 tables) |
| `requirements.txt` | Python dependencies |

## Setup
```bash
pip install -r requirements.txt
# Set SUPABASE_DB_URL in .env
python -m uvicorn app:app --reload
```

## Database Tables
1. `products` — master SKU
2. `sales` — transaction-level sales (87k+ rows)
3. `stock_snapshots` — inventory snapshots
4. `purchase_orders` — ongoing POs
5. `forecast_results` — Holt's DES output per SKU
