/* Weekly forecast vs actual, shared by Overview, Product Analysis and PO Tracking (revision R7).
   Source: /api/forecast/plan (also used by the Demand Plan). Per SKU it returns
   - holdout: the last 8 actual weeks and the forecast for those same weeks from a model trained WITHOUT them
     (the only past forecasts Stockwise keeps: every forecast run replaces the previous one), and
   - forecast: the next 12 weeks.
   All series use the same Monday weeks, so actual and forecast are compared week by week. */
let planPromise = null;
async function fetchPlan() {
  const pt = PT;
  if (planData && planData._pt === pt) return planData;
  if (planPromise && planPromise._pt === pt) return planPromise;
  planPromise = (async () => {
    const r = await fetch(`/api/forecast/plan?product_type=${pt}`);
    if (!r.ok) throw new Error('Forecast request failed (' + r.status + ')');
    const d = await r.json();
    d._pt = pt;
    planData = d;
    if (typeof buildMonths === 'function') buildMonths();
    return d;
  })();
  planPromise._pt = pt;
  try { return await planPromise; } finally { planPromise = null; }
}

/* Sum the plan over SKUs (all, or the ones passed). Returns
   { weeks, past: Set of holdout weeks, actual{w}, holdoutFc{w}, forecast{w}, skus, evaluated } */
function demandSeries(plan, skuSet) {
  const actual = {}, hfc = {}, fc = {}, past = new Set(), fut = new Set();
  let skus = 0, evaluated = 0;
  (plan && plan.results || []).forEach(p => {
    if (skuSet && !skuSet.has(p.sku)) return;
    skus++;
    const h = p.holdout || {};
    if (Array.isArray(h.actual) && Array.isArray(h.forecast) && h.weeks && h.weeks.length) {
      evaluated++;
      h.weeks.forEach((w, i) => {
        const k = String(w).slice(0, 10);
        past.add(k);
        actual[k] = (actual[k] || 0) + (Number(h.actual[i]) || 0);
        hfc[k] = (hfc[k] || 0) + (Number(h.forecast[i]) || 0);
      });
    }
    (p.forecast && p.forecast.weeks || []).forEach((w, i) => {
      const k = String(w).slice(0, 10);
      fut.add(k);
      fc[k] = (fc[k] || 0) + (Number((p.forecast.quantities || [])[i]) || 0);
    });
  });
  const weeks = [...new Set([...past, ...fut])].sort();
  return { weeks, past, actual, holdoutFc: hfc, forecast: fc, skus, evaluated };
}

const wkLabel = w => { const d = new Date(w + 'T00:00:00'); return d.toLocaleDateString('en-GB', { day: 'numeric', month: 'short' }) };

/* Over / under summary for the holdout weeks: forecast − actual per week. */
function biasSummary(s) {
  const ws = [...s.past].sort();
  let over = 0, under = 0, fsum = 0, asum = 0;
  ws.forEach(w => { const f = s.holdoutFc[w] || 0, a = s.actual[w] || 0; fsum += f; asum += a; if (f > a) over++; else if (f < a) under++; });
  return { weeks: ws.length, over, under, fsum, asum, pct: asum ? (fsum - asum) / asum * 100 : null };
}
