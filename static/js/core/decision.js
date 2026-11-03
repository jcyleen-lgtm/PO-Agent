/* Purchasing decision support shared by Overview and Replenishment (approved in docs/phase5, A1 + A2).
   - bucketOf(row): Action Center bucket, spec §9.2 (decision D2), first match wins → every SKU in exactly one bucket.
   - explainSku(row): deterministic, rule-based explanation built only from fields /api/replenishment returns.
   - fetchRepl(): one cached /api/replenishment request per product type, so both pages show the same numbers.
   Nothing here changes a calculation: it labels and explains the values the API already computed. */
const BUCKETS = [
  { key: 'now', label: 'Order now', tone: 'crit', hint: 'Order-by date has passed' },
  { key: 'soon', label: 'Order within 2 weeks', tone: 'warn', hint: 'Order-by date in the next 14 days' },
  { key: 'hold', label: 'Hold', tone: 'info', hint: 'Overstocked, or incoming PO already covers the need' },
  { key: 'none', label: 'No action needed', tone: 'ok', hint: 'Order-by date is more than 2 weeks away' },
];
const BUCKET = Object.fromEntries(BUCKETS.map(b => [b.key, b]));

function bucketOf(r) {
  const d = r.order_by_days, firm = r.order_by_is_estimate === false && d != null;
  if (firm && d <= 0) return 'now';
  if (firm && d >= 1 && d <= 14) return 'soon';
  if (r.status === 'overstocked' || ((r.reco_po || 0) === 0 && (r.confirmed_incoming || 0) > 0)) return 'hold';
  return 'none';
}

// Priority inside a bucket (spec §9.3): order-by date ascending, estimates last, then L4W revenue descending.
function byPriority(a, b) {
  const ea = a.order_by_is_estimate ? 1 : 0, eb = b.order_by_is_estimate ? 1 : 0;
  if (ea !== eb) return ea - eb;
  const da = a.order_by || '9999-12-31', db = b.order_by || '9999-12-31';
  if (da !== db) return da < db ? -1 : 1;
  return (b.l4w_revenue || 0) - (a.l4w_revenue || 0);
}

const ROUND = v => fmt(Math.round(v || 0));
const weeksTxt = n => `${n} week${n === 1 ? '' : 's'}`;

function explainSku(r, settings) {
  const st = settings || (replData && replData.settings) || {};
  const L = st.lead_time_weeks;
  const out = [];
  const b = bucketOf(r);
  const haveDate = !!r.order_by && r.order_by_days != null;
  if (!haveDate && r.reco_po == null && r.status == null) return ['Not enough data to explain this recommendation.'];

  // 1. Timing
  if (r.stockout_week && haveDate && !r.order_by_is_estimate) {
    const d = r.order_by_days;
    const when = d < 0 ? `the order-by date (${dfmt(r.order_by)}) passed ${d === -1 ? '1 day' : -d + ' days'} ago`
      : d === 0 ? `the order-by date is today (${dfmt(r.order_by)})`
        : `order by ${dfmt(r.order_by)} (in ${d === 1 ? '1 day' : d + ' days'})`;
    out.push(`Stock is projected to run out in week ${r.stockout_week}${L ? `; with a ${L}-week lead time ${when}` : `; ${when}`}.`);
    if (d < 0) out.push('An order placed today arrives after the projected stockout.');
  } else if (haveDate && r.order_by_is_estimate) {
    out.push(`No stockout within the 12-week forecast; earliest sensible order date ${dfmt(r.order_by)} (estimate).`);
  }

  // 2. Quantity
  if (r.reco_po != null && r.lt_demand_8w != null && r.safety_stock != null && r.inventory_position != null) {
    if (r.reco_po > 0) {
      out.push(`Recommended ${fmt(r.reco_po)} units = lead-time demand ${ROUND(r.lt_demand_8w)} + safety stock ${ROUND(r.safety_stock)} − stock and confirmed incoming ${ROUND(r.inventory_position)}.`);
    } else {
      out.push(`Recommended 0: stock and confirmed incoming (${ROUND(r.inventory_position)}) already cover lead-time demand ${ROUND(r.lt_demand_8w)} + safety stock ${ROUND(r.safety_stock)}.`);
      if (b === 'now' || b === 'soon') out.push(`Review anyway: the projected stockout${r.stockout_week ? ` in week ${r.stockout_week}` : ''} is after the lead time.`);
    }
  }

  // 3. Coverage / hold
  if (r.status === 'overstocked') {
    out.push(r.coverage != null ? `Coverage ${r.coverage.toFixed(1)} weeks is above ${st.overstock_weeks || 20} weeks: no need to reorder.`
      : 'Stock without sales in the last 4 weeks: no need to reorder.');
  } else if (b === 'hold' && (r.confirmed_incoming || 0) > 0) {
    out.push(`Incoming PO with an ETA (${fmt(r.confirmed_incoming)} units) already covers the need.`);
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
    const r = await fetch(`/api/replenishment?product_type=${pt}`);
    if (!r.ok) throw new Error('Replenishment request failed (' + r.status + ')');
    const d = await r.json();
    d._pt = pt;
    (d.results || []).forEach(x => { x.qty = x.reco_po || 0; x.sel = needOrder(x) && (x.reco_po || 0) > 0; x.bucket = bucketOf(x); });
    replData = d;
    return d;
  })();
  replPromise._pt = pt;
  try { return await replPromise; } finally { replPromise = null; }
}
