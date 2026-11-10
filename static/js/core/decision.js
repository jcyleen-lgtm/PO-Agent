/* Purchasing decision support shared by Overview and Replenishment (revision R7, docs/revision-r7).
   - bucketOf(row): purchasing group from the inventory status (San, 7 Nov 2026: one group per status, no Hold):
       Understocked                     → Order Now        (recommended > 0: mandatory in the order export;
                                                            recommended 0: shown with the reason, manual only)
       Healthy                          → Covered / Monitor (optional; recommended > 0 = "System Recommended")
       Overstocked                      → No Action Needed (optional, manual selection)
       no status (no sales, no stock)   → Not classified   (All tab only)
     Every SKU is in exactly one group.
   - zeroReason(row): why an Understocked SKU has a recommendation of 0: the stock and incoming PO cover the
     lead-time requirement, or the Holt forecast reaches 0 within the lead time (a forecast warning, not cover).
   - explainSku(row): deterministic explanation built only from fields the API returns.
   - fetchRepl(): one cached /api/replenishment request (plus the weekly forecast map) shared by both pages.
   Nothing here changes a calculation: it groups and explains the values the API already computed. */
const BUCKETS = [
  { key: 'order', label: 'Order Now', tone: 'crit', hint: 'Understocked: SKUs with a recommended quantity are always in the order export; recommended 0 shows the reason, tick to add' },
  { key: 'covered', label: 'Covered / Monitor', tone: 'warn', hint: 'Healthy: no order by default; System Recommended = the forecast still suggests a quantity, tick to add' },
  { key: 'none', label: 'No Action Needed', tone: 'ok', hint: 'Overstocked: no order by default, select manually if needed' },
];
const UNCLASSIFIED = { key: 'unclassified', label: 'Not classified', tone: 'mute', hint: 'No sales in 4 weeks and no stock' };
const BUCKET = Object.fromEntries([...BUCKETS, UNCLASSIFIED].map(b => [b.key, b]));

function bucketOf(r) {
  if (r.status === 'understocked') return 'order';
  if (r.status === 'healthy') return 'covered';
  if (r.status === 'overstocked') return 'none';
  return 'unclassified';
}

// Weekly forecast for a SKU from the forecast map (/api/forecast/results), or null when not loaded.
function fcWeeks(sku) {
  const f = typeof fcDataMap !== 'undefined' ? fcDataMap[sku] : null;
  const q = f && f.forecast_data && f.forecast_data.quantities;
  return Array.isArray(q) && q.length ? q.map(Number) : null;
}

// Only for Understocked SKUs with recommendation 0: 'forecast_zero' | 'sufficient' | 'unknown'.
function zeroReason(r, settings) {
  if (r.status !== 'understocked' || (r.reco_po || 0) > 0) return null;
  const st = settings || (replData && replData.settings) || {};
  const L = st.lead_time_weeks || 8;
  const q = fcWeeks(r.sku);
  if (!q) return 'unknown';
  return q.slice(0, L).some(v => v === 0) ? 'forecast_zero' : 'sufficient';
}

// Healthy SKUs the system still recommends ordering (shown with a "System Recommended" badge, never pre-selected).
const sysRecommended = r => r.status === 'healthy' && (r.reco_po || 0) > 0;

// Priority inside a group (spec §9.3): mandatory rows first (recommended > 0), then order-by date ascending,
// estimates last, then L4W revenue descending.
function byPriority(a, b) {
  const ma = a.mandatory ? 0 : 1, mb = b.mandatory ? 0 : 1;
  if (ma !== mb) return ma - mb;
  const ea = a.order_by_is_estimate ? 1 : 0, eb = b.order_by_is_estimate ? 1 : 0;
  if (ea !== eb) return ea - eb;
  const da = a.order_by || '9999-12-31', db = b.order_by || '9999-12-31';
  if (da !== db) return da < db ? -1 : 1;
  return (b.l4w_revenue || 0) - (a.l4w_revenue || 0);
}

const ROUND = v => fmt(Math.round(v || 0));
const weeksTxt = n => `${n} week${n === 1 ? '' : 's'}`;

function zeroReasonText(r, settings) {
  const st = settings || (replData && replData.settings) || {};
  const L = st.lead_time_weeks || 8;
  const z = zeroReason(r, st);
  if (z === 'forecast_zero') {
    const q = fcWeeks(r.sku) || [];
    const wk = q.findIndex(v => v === 0) + 1;
    return `Forecast warning: the Holt forecast reaches 0 in week ${wk}, while sales in the last 4 weeks averaged ${r.avg_l4w != null ? fmt(r.avg_l4w) : '--'} units/week. The recommendation is 0 because of the forecast, not because stock covers demand. Check before skipping.`;
  }
  if (z === 'sufficient') {
    return `Stock / incoming PO sufficient: stock and PO with an ETA within the ${L}-week lead time (${ROUND(r.inventory_position)}) cover lead-time demand ${ROUND(r.lt_demand_8w)} + safety stock ${ROUND(r.safety_stock)}.`;
  }
  if (z === 'unknown') return 'Recommended 0; the weekly forecast is not available to tell why.';
  return null;
}

function explainSku(r, settings) {
  const st = settings || (replData && replData.settings) || {};
  const L = st.lead_time_weeks;
  const out = [];
  const haveDate = !!r.order_by && r.order_by_days != null;
  if (!haveDate && r.reco_po == null && r.status == null) return ['Not enough data to explain this recommendation.'];

  // 1. Status (the group comes from it)
  if (r.status === 'understocked' || r.status === 'healthy') {
    out.push(`Coverage ${r.coverage != null ? r.coverage.toFixed(1) + ' weeks' : '--'}: ${r.status === 'understocked' ? `${st.understock_weeks || 8} weeks or less, Understocked` : `between ${st.understock_weeks || 8} and ${st.overstock_weeks || 20} weeks, Healthy`}.`);
  } else if (r.status === 'overstocked') {
    out.push(r.coverage != null ? `Coverage ${r.coverage.toFixed(1)} weeks is above ${st.overstock_weeks || 20} weeks: no need to reorder.`
      : 'Stock without sales in the last 4 weeks: no need to reorder.');
  }

  // 2. Recommended quantity
  const zr = zeroReasonText(r, st);
  if (zr) out.push(zr);
  else if (r.reco_po != null && r.lt_demand_8w != null && r.safety_stock != null && r.inventory_position != null) {
    if (r.reco_po > 0) {
      out.push(`Recommended ${fmt(r.reco_po)} units = lead-time demand ${ROUND(r.lt_demand_8w)} + safety stock ${ROUND(r.safety_stock)} − stock and confirmed incoming ${ROUND(r.inventory_position)}.`);
      if (sysRecommended(r)) out.push('Healthy (Covered / Monitor), but the forecast-based recommendation is above 0 (System Recommended). Not selected for the order export unless you tick it.');
    } else {
      out.push(`Recommended 0: stock and confirmed incoming (${ROUND(r.inventory_position)}) already cover lead-time demand ${ROUND(r.lt_demand_8w)} + safety stock ${ROUND(r.safety_stock)}.`);
    }
  }

  // 3. Timing
  if (r.stockout_week && haveDate && !r.order_by_is_estimate) {
    const d = r.order_by_days;
    const when = d < 0 ? `the order-by date (${dfmt(r.order_by)}) passed ${d === -1 ? '1 day' : -d + ' days'} ago`
      : d === 0 ? `the order-by date is today (${dfmt(r.order_by)})`
        : `order by ${dfmt(r.order_by)} (in ${d === 1 ? '1 day' : d + ' days'})`;
    out.push(`Stock is projected to run out in week ${r.stockout_week}${L ? `; with a ${L}-week lead time ${when}` : `; ${when}`}.`);
  } else if (haveDate && r.order_by_is_estimate) {
    out.push(`No stockout within the 12-week forecast; earliest sensible order date ${dfmt(r.order_by)} (estimate).`);
  }

  // 4. Data caveats
  if ((r.unconfirmed_incoming || 0) > 0) out.push(`Open PO without ETA (${fmt(r.unconfirmed_incoming)} units) is not counted — confirm the ETA.`);
  if (r.fc_method === 'naive') out.push(`Forecast is a simple average (${naiveWhy(r.fc_method_reason).toLowerCase()}).`);
  (r.flags || []).forEach(f => {
    const t = { negative_stock: 'Book stock is negative; the deficit is included in the order.',
      lead_time_beyond_horizon: 'Lead time is longer than the 12-week forecast horizon.',
      protection_beyond_horizon: 'Lead time + review period exceed the 12-week forecast.' }[f];
    if (t) out.push(t);
  });
  return out.length ? out : ['Not enough data to explain this recommendation.'];
}

let replPromise = null;
async function fetchRepl() {
  const pt = PT;
  if (replData && replData._pt === pt) return replData;
  if (replPromise && replPromise._pt === pt) return replPromise;
  replPromise = (async () => {
    const fcP = Object.keys(fcDataMap).length ? Promise.resolve() : loadFcMap();
    const r = await fetch(`/api/replenishment?product_type=${pt}`);
    if (!r.ok) throw new Error('Replenishment request failed (' + r.status + ')');
    const d = await r.json();
    await fcP;
    d._pt = pt;
    (d.results || []).forEach(x => {
      x.qty = x.reco_po || 0;
      x.bucket = bucketOf(x);
      x.mandatory = x.bucket === 'order' && (x.reco_po || 0) > 0;   // always in the order export
      x.sel = false;                               // manual selection (optional groups)
      x.zero = zeroReason(x, d.settings);
      x.sysReco = sysRecommended(x);
    });
    replData = d;
    return d;
  })();
  replPromise._pt = pt;
  try { return await replPromise; } finally { replPromise = null; }
}

// Rows that go into the order export: Order Now SKUs with a recommendation plus manually selected ones (units > 0).
const inOrder = r => (r.mandatory || r.sel) && (r.qty || 0) > 0;
