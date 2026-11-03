-- Read-only export used by evaluate.py / experiments.py. Run in the Supabase SQL editor (or psql) and
-- save each result as CSV in analysis/forecast_accuracy/data/ (that folder is git-ignored: company data).
-- Set the end week (Monday of the last week that has any sales) before running query 2.

-- 1) scope_<date>.csv  — header: sku,pareto,hero,revenue,method,alpha,beta,mape
SELECT sku,
       (forecast_data::jsonb)->'scope'->>'pareto'  AS pareto,
       (forecast_data::jsonb)->'scope'->>'hero'    AS hero,
       (forecast_data::jsonb)->'scope'->>'revenue' AS revenue,
       (forecast_data::jsonb)->>'method'           AS method,
       alpha, beta, round(mape::numeric, 4)        AS mape
FROM forecast_results ORDER BY sku;

-- 2) weekly_<date>.csv — no header: sku,first_week,total_units,"q1 q2 … qn"
--    Same weekly aggregation as forecast_repo.weekly_import_sales_df, zero-filled from each SKU's first sale.
WITH w AS (
  SELECT s.sku, DATE_TRUNC('week', s.invoice_date)::date AS wk, SUM(s.quantity) AS q
  FROM sales s JOIN products p ON p.sku = s.sku
  WHERE s.invoice_date IS NOT NULL AND COALESCE(p.product_type, 'import') = 'import'
    AND s.sku IN (SELECT sku FROM forecast_results)
  GROUP BY 1, 2),
f AS (SELECT sku, MIN(wk) AS f0 FROM w GROUP BY 1),
d AS (SELECT f.sku, f.f0, g::date AS wk FROM f, generate_series(f.f0, DATE '2026-10-26', INTERVAL '7 day') g)
SELECT d.sku, d.f0, SUM(COALESCE(w.q, 0))::bigint AS total,
       string_agg(COALESCE(w.q, 0)::bigint::text, ' ' ORDER BY d.wk) AS series
FROM d LEFT JOIN w ON w.sku = d.sku AND w.wk = d.wk
GROUP BY d.sku, d.f0 ORDER BY d.sku;
