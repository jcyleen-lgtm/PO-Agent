/* Inventory Overview: KPIs → health donut, action groups, coverage → Priority SKUs → demand actual vs forecast,
   operational summary.
   Inventory numbers come from the same /api/replenishment rows (fetchRepl) as the Replenishment page, so they always
   agree. The demand chart sums the per-SKU weekly sales and Holt's DES forecasts the API already returns
   (same data as Product Analysis › Sales). Nothing here recalculates a business value. */
let ovSeq = 0, ovRows = [], ovFc = null, ovDem = null, ovHealthChartObj = null, ovCovChartObj = null, ovDemChartObj = null;
const PRIORITY_LIMIT = 8;

const OVI = {
  box: '<path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"/><polyline points="3.27 6.96 12 12.01 20.73 6.96"/><line x1="12" y1="22.08" x2="12" y2="12"/>',
  money: '<rect x="2" y="6" width="20" height="12" rx="2"/><circle cx="12" cy="12" r="2.5"/><path d="M6 12h.01M18 12h.01"/>',
  down: '<path d="M12 5v14M5 12l7 7 7-7"/>',
  empty: '<circle cx="12" cy="12" r="9"/><path d="M5.6 5.6l12.8 12.8"/>',
  layers: '<path d="M12 2 2 7l10 5 10-5-10-5z"/><path d="m2 17 10 5 10-5"/><path d="m2 12 10 5 10-5"/>',
  cart: '<circle cx="9" cy="20" r="1.5"/><circle cx="18" cy="20" r="1.5"/><path d="M2 3h3l2.7 12.4a2 2 0 0 0 2 1.6h8.6a2 2 0 0 0 2-1.6L22 7H6"/>',
  clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  pause: '<circle cx="12" cy="12" r="9"/><path d="M10 9v6M14 9v6"/>',
  check: '<circle cx="12" cy="12" r="9"/><path d="m8 12 3 3 5-6"/>',
  sales: '<path d="M3 3v18h18"/><path d="m7 15 4-4 3 3 6-6"/>',
  truck: '<rect x="1" y="4" width="14" height="12" rx="2"/><path d="M15 9h4l3 3v4h-7z"/><circle cx="6" cy="18.5" r="2"/><circle cx="18" cy="18.5" r="2"/>',
  upload: '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/>',
  cal: '<rect x="3" y="4" width="18" height="17" rx="2"/><path d="M16 2v4M8 2v4M3 10h18"/>',
  fc: '<path d="M3 17l5-5 4 3 8-8"/><path d="M14 7h6v6"/>',
};
const ico = (k, cls = '') => `<span class="ico-t ${cls}"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">${OVI[k]}</svg></span>`;

async function loadOverview() {
  const seq = ++ovSeq;
  ovFc = null;
  const fcP = fetch(`/api/overview?preset=l4w&top_sort=revenue&product_type=${PT}`).then(r => r.ok ? r.json() : null).catch(() => null);
  const poP = fetch('/api/po/summary').then(r => r.ok ? r.json() : null).catch(() => null);
  try {
    const d = await fetchRepl();
    if (seq !== ovSeq) return;
    ovRows = d.results || [];
    renderFresh(d.freshness);
    renderOvAsOf();
    renderOvKpis();
    renderActionCenter();
    renderPriority();
    renderOvCharts();
  } catch (e) {
    if (seq !== ovSeq) return;
    ovRows = [];
    const err = `<div class="state state-error">${ICON.alert}<div><b>Could not load inventory data.</b><br>${esc(e.message)}</div><button class="btn btn-ghost" onclick="replData=null;loadOverview()">Retry</button></div>`;
    document.getElementById('ovKpis').innerHTML = '';
    document.getElementById('acBuckets').innerHTML = err;
    document.getElementById('prList').innerHTML = '';
    document.getElementById('ovHealthLegend').innerHTML = '';
    ['chHealthWrap', 'chCovWrap'].forEach(id => { document.getElementById(id).innerHTML = '<div class="state">Not available</div>' });
    document.getElementById('ovAsOf').innerHTML = '<span class="text-dim">Data status unknown</span>';
  }
  loadOvDemand(seq);
  const [ov, po] = await Promise.all([fcP, poP]);
  if (seq !== ovSeq) return;
  ovFc = ov && ov.forecast_summary;
  if (replData) renderOvAsOf();
  renderOvOps(po);
}

/* Header: what the numbers are based on. Each chip opens the Data page (where both are updated). */
function renderOvAsOf() {
  const f = (replData && replData.freshness) || {};
  let h = `<button class="chip" onclick="switchTab('upload')" title="Stock report date · open the Data page">${ico('cal', 'ico-s')}Stock snapshot <b>${f.stock_date ? dfmt(f.stock_date) : 'none'}</b></button>`;
  if (ovFc && ovFc.calculated_at) h += `<button class="chip" onclick="switchTab('upload')" title="Last forecast run · open the Data page">${ico('fc', 'ico-s')}Forecast <b>${fmtStamp(ovFc.calculated_at)}</b></button>`;
  document.getElementById('ovAsOf').innerHTML = h;
}
function fmtStamp(ts) {
  const s = String(ts), d = s.slice(0, 10), t = s.length > 15 ? s.slice(11, 16) : '';
  return dfmt(d) + (t ? ' ' + t : '');
}

/* KPIs. Out of stock is a subset of the statuses, never added to them. */
function healthCounts(rows) {
  const c = k => rows.filter(r => (r.status || null) === k).length;
  return { total: rows.length, understocked: c('understocked'), healthy: c('healthy'), overstocked: c('overstocked'), unclassified: c(null), oos: rows.filter(r => r.out_of_stock).length };
}
function renderOvKpis() {
  const h = healthCounts(ovRows), st = (replData && replData.settings) || {};
  const U = st.understock_weeks ?? 8, O = st.overstock_weeks ?? 20;
  const pct = n => h.total ? Math.round(n / h.total * 100) + '%' : '0%';
  const valued = ovRows.filter(r => r.inventory_value != null);
  const value = valued.reduce((a, r) => a + (r.inventory_value || 0), 0);
  const verified = ovRows.every(r => r.cost_verified !== false);
  const card = o => {
    const inner = `<div class="kc-top">${ico(o.i, 'ico-' + o.tone)}${o.badge ? `<span class="kc-badge kb-${o.tone}">${o.badge}</span>` : ''}</div>
      <div class="kc-l">${o.l}</div><div class="kc-v kv-${o.tone}">${o.v}</div><div class="kc-d">${o.d}</div>`;
    return o.go ? `<button class="card kc" data-k="${o.k}" onclick="${o.go}" title="${o.t}">${inner}</button>` : `<div class="card kc" data-k="${o.k}">${inner}</div>`;
  };
  document.getElementById('ovKpis').innerHTML = [
    { k: 'total', i: 'box', tone: 'navy', l: 'Total SKUs', v: fmt(h.total), d: 'Best-seller SKUs with a forecast', go: "openReplBucket('all')", t: 'Open all SKUs in Replenishment' },
    { k: 'value', i: 'money', tone: 'blue', l: 'Stock Value', v: fmtRpS(value), d: `At unit cost${verified ? '' : ' · <span title="Unit cost from Accurate COGS, not yet confirmed by finance">unverified</span>'}${valued.length < h.total ? ` · ${h.total - valued.length} SKUs without cost` : ''}` },
    { k: 'under', i: 'down', tone: 'red', l: 'Understocked', v: fmt(h.understocked), badge: pct(h.understocked), d: `Coverage ≤ ${U} weeks`, go: "openReplStatus('understocked')", t: 'Show understocked SKUs in Replenishment' },
    { k: 'oos', i: 'empty', tone: 'amber', l: 'Out of Stock', v: fmt(h.oos), badge: pct(h.oos), d: 'Stock ≤ 0 · counted in the statuses' },
    { k: 'over', i: 'layers', tone: 'slate', l: 'Overstocked', v: fmt(h.overstocked), badge: pct(h.overstocked), d: `Coverage > ${O} weeks, or no sales`, go: "openReplStatus('overstocked')", t: 'Show overstocked SKUs in Replenishment' },
  ].map(card).join('');
}

/* Action groups from bucketOf (core/decision.js, spec §9.2): mutually exclusive. */
function bucketStats(rows) {
  const priced = rows.filter(r => (r.reco_po || 0) > 0 && r.unit_cost != null);
  return {
    units: rows.reduce((a, r) => a + (r.reco_po || 0), 0),
    cost: priced.reduce((a, r) => a + r.reco_po * r.unit_cost, 0),
    over: rows.reduce((a, r) => a + (r.overstock_value || 0), 0),
  };
}
const BUCKET_ICON = { now: 'cart', soon: 'clock', hold: 'pause', none: 'check' };
function renderActionCenter() {
  const rows = ovRows;
  document.getElementById('acMeta').textContent = rows.length ? `${fmt(rows.length)} SKUs · each counted once` : 'No forecasted best sellers yet';
  document.getElementById('acBuckets').innerHTML = BUCKETS.map(b => {
    const rs = rows.filter(r => r.bucket === b.key), s = bucketStats(rs);
    let sub = b.hint;
    if (rs.length && (b.key === 'now' || b.key === 'soon')) sub = `${fmt(s.units)} units${s.cost ? ` · ≈ ${fmtRpS(s.cost)}` : ''}`;
    else if (rs.length && b.key === 'hold' && s.over) sub = `${fmtRpS(s.over)} overstock value`;
    const share = rows.length ? rs.length / rows.length * 100 : 0;
    return `<button class="ac-b ac-${b.tone}" onclick="openReplBucket('${b.key}')" title="${esc(b.hint)} · open in Replenishment">
      <span class="ac-b-l">${ico(BUCKET_ICON[b.key], 'ico-' + b.tone + ' ico-s')}${b.label}</span>
      <span class="ac-b-v">${fmt(rs.length)}<small> SKUs</small></span>
      <span class="ac-b-s">${sub}</span>
      <span class="ac-bar"><span style="width:${share.toFixed(1)}%"></span></span></button>`;
  }).join('');
}

/* Priority SKUs = Order now, then Upcoming orders, in the Replenishment priority order (spec §9.3). */
function priorityRows() {
  return ovRows.filter(r => r.bucket === 'now' || r.bucket === 'soon').sort((a, b) => (a.bucket === b.bucket ? byPriority(a, b) : a.bucket === 'now' ? -1 : 1));
}
function renderPriority() {
  const el = document.getElementById('prList'), more = document.getElementById('prMore');
  more.textContent = '';
  if (!ovRows.length) { el.innerHTML = '<div class="state">No forecasted best sellers yet. Upload sales and stock data, then run Refresh forecast on the Data page.</div>'; return }
  const all = priorityRows();
  if (!all.length) { el.innerHTML = '<div class="state">No SKU needs ordering in the next 2 weeks.</div>'; return }
  const top = all.slice(0, PRIORITY_LIMIT);
  document.getElementById('prMeta').textContent = `SKUs requiring purchasing attention · most urgent first`;
  if (all.length > top.length) more.textContent = `Showing ${top.length} of ${fmt(all.length)}`;
  const st = (replData && replData.settings) || {}, O = st.overstock_weeks ?? 20;
  const covCell = r => {
    if (r.coverage == null) return '<span class="text-dim">no sales</span>';
    const tone = r.status === 'understocked' ? 'red' : r.status === 'overstocked' ? 'slate' : 'green';
    return `<div class="cov"><span class="cov-v t-${tone}">${r.coverage.toFixed(1)} wks</span><span class="cov-bar"><span class="cb-${tone}" style="width:${Math.min(100, r.coverage / O * 100).toFixed(1)}%"></span></span></div>`;
  };
  let h = `<div class="tbl-wrap"><table class="ro-table pr-table"><thead><tr><th>Product / SKU</th><th class="r">Current stock</th>
    <th class="r" title="Average weekly sales of the last 4 weeks">Avg weekly sales</th><th title="(stock + open PO) ÷ average weekly sales of the last 4 weeks">Stock coverage</th>
    <th class="r" title="Open PO quantity still to arrive">Incoming PO</th><th class="r">Recommended qty</th><th>Status</th><th>Action</th></tr></thead><tbody>`;
  top.forEach(r => {
    const sk = String(r.sku).replace(/'/g, "\\'"), b = BUCKET[r.bucket];
    const inc = r.ongoing_po ? `${fmt(r.ongoing_po)}${r.unconfirmed_incoming ? `<div class="ob-sub ob-soon" title="Open PO without ETA is not counted in the recommendation">${fmt(r.unconfirmed_incoming)} without ETA</div>` : ''}` : '<span class="text-dim">0</span>';
    h += `<tr class="clickable" onclick="openReplSku('${sk}')"><td><div class="p-name" title="${esc(r.product_name)}">${esc(r.product_name)}</div><div class="p-sku">${esc(r.sku)}</div></td>
      <td class="r">${r.out_of_stock ? '<span class="t-red t-strong">0</span>' : fmt(r.current_stock)}</td>
      <td class="r">${r.avg_l4w != null ? fmt(Math.round(r.avg_l4w)) : '--'}</td>
      <td>${covCell(r)}</td><td class="r">${inc}</td><td class="r t-strong">${fmt(r.reco_po || 0)}</td>
      <td><span class="pill pill-${b.tone}">${b.label}</span><div class="ob-sub">${orderByShort(r)}</div></td>
      <td><button class="btn btn-ghost btn-sm" onclick="event.stopPropagation();openReplSku('${sk}')">Review</button></td></tr>`;
  });
  el.innerHTML = h + '</tbody></table></div>';
}
function orderByShort(r) {
  if (!r.order_by || r.order_by_days == null) return '';
  const d = r.order_by_days;
  return d < 0 ? `order-by ${-d} days ago` : d === 0 ? 'order-by today' : `order by ${dfmt(r.order_by)}`;
}

/* Charts: health donut, coverage distribution (status and coverage from the API only). */
function covBins() {
  const st = (replData && replData.settings) || {};
  const U = st.understock_weeks ?? 8, O = st.overstock_weeks ?? 20;
  const half = Math.round(U / 2 * 10) / 10, mid = Math.round((U + O) / 2 * 10) / 10;
  return [
    { l: '0', t: c => c === 0, z: 'red' },
    { l: `0–${half}`, t: c => c > 0 && c <= half, z: 'red' },
    { l: `${half}–${U}`, t: c => c > half && c <= U, z: 'red' },
    { l: `${U}–${mid}`, t: c => c > U && c <= mid, z: 'green' },
    { l: `${mid}–${O}`, t: c => c > mid && c <= O, z: 'green' },
    { l: `> ${O}`, t: c => c > O, z: 'slate' },
    { l: 'No sales', t: null, z: 'chart-3' },
  ];
}
function chartTip() { const ct = getCT(); return { backgroundColor: ct.tipBg, titleColor: ct.tipTitle, bodyColor: ct.tipBody, borderColor: ct.tipBorder, borderWidth: 1, padding: 10, cornerRadius: 8 } }
function healthSeries(h) {
  const hs = [['understocked', 'Understocked', '--red'], ['healthy', 'Healthy', '--green'], ['overstocked', 'Overstocked', '--slate']];
  if (h.unclassified) hs.push([null, 'Not classified', '--chart-3']);
  return hs;
}
function renderOvCharts() {
  if (typeof Chart === 'undefined') return;
  const h = healthCounts(ovRows);
  const hw = document.getElementById('chHealthWrap'), cw = document.getElementById('chCovWrap');
  if (!ovRows.length) { hw.innerHTML = cw.innerHTML = '<div class="state">No data yet.</div>'; document.getElementById('donutCenter').innerHTML = ''; return }
  if (!document.getElementById('ovHealthChart')) hw.innerHTML = '<canvas id="ovHealthChart" aria-label="Inventory health distribution chart" role="img"></canvas>';
  if (!document.getElementById('ovCovChart')) cw.innerHTML = '<canvas id="ovCovChart" aria-label="Stock coverage distribution chart" role="img"></canvas>';
  const ct = getCT(), tip = chartTip();

  const hs = healthSeries(h), surface = cv('--surface');
  if (ovHealthChartObj) ovHealthChartObj.destroy();
  ovHealthChartObj = new Chart(document.getElementById('ovHealthChart'), {
    type: 'doughnut',
    data: { labels: hs.map(x => x[1]), datasets: [{ data: hs.map(x => h[x[0] || 'unclassified']), backgroundColor: hs.map(x => cv(x[2])), borderColor: surface, borderWidth: 3, hoverOffset: 4 }] },
    options: {
      responsive: true, maintainAspectRatio: false, cutout: '72%',
      plugins: { legend: { display: false }, tooltip: { ...tip, callbacks: { label: c => ` ${c.label}: ${fmt(c.parsed)} SKUs (${h.total ? Math.round(c.parsed / h.total * 100) : 0}%)` } } },
      onClick: (e, els) => { if (els.length) { const k = hs[els[0].index][0]; if (k) openReplStatus(k) } },
      onHover: (e, els) => { e.native.target.style.cursor = els.length && hs[els[0].index][0] ? 'pointer' : 'default' },
    },
  });
  document.getElementById('donutCenter').innerHTML = `<div class="dc-v">${fmt(h.total)}</div><div class="dc-l">SKUs</div>`;
  document.getElementById('ovHealthLegend').innerHTML = hs.map(([k, l, c]) => {
    const n = h[k || 'unclassified'], p = h.total ? Math.round(n / h.total * 100) : 0;
    const inner = `<span class="hl-l"><span class="sw" style="background:var(${c})"></span>${l}</span><span class="hl-v">${fmt(n)}</span><span class="hl-p">${p}%</span>`;
    return k ? `<button class="hl-item" onclick="openReplStatus('${k}')" title="Show ${l.toLowerCase()} SKUs in Replenishment">${inner}</button>` : `<div class="hl-item">${inner}</div>`;
  }).join('');

  const bins = covBins();
  const counts = bins.map(b => ovRows.filter(r => b.t ? (r.coverage != null && b.t(r.coverage)) : r.coverage == null).length);
  if (ovCovChartObj) ovCovChartObj.destroy();
  ovCovChartObj = new Chart(document.getElementById('ovCovChart'), {
    type: 'bar',
    data: { labels: bins.map(b => b.l), datasets: [{ data: counts, backgroundColor: bins.map(b => cv('--' + b.z)), borderRadius: 5, maxBarThickness: 30 }] },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: { legend: { display: false }, tooltip: { ...tip, callbacks: { title: c => c[0].label === 'No sales' ? 'No sales in 4 weeks' : `${c[0].label} weeks of coverage`, label: c => `${fmt(c.parsed.y)} SKUs` } } },
      scales: { x: { grid: { display: false }, border: { color: ct.grid }, ticks: { color: ct.tick, font: { ...ct.font, size: 11 }, maxRotation: 0, autoSkip: false } }, y: { min: 0, grid: { color: ct.grid }, border: { display: false }, ticks: { color: ct.tick, font: ct.font, precision: 0 } } },
    },
  });
  if (ovDem) renderOvDemandChart();
}

/* Demand: actual weekly units vs Holt's DES forecast, summed over the best-seller SKUs (same data as Sales). */
async function loadOvDemand(seq) {
  const wrap = document.getElementById('chDemWrap');
  try {
    const d = await fetchWeekly(PT, 'bestseller');
    if (seq !== ovSeq) return;
    const rows = d.results || [];
    const spark = rows.map(r => r.sparkline || []).filter(a => a.length);
    const n = spark.length ? Math.max(...spark.map(a => a.length)) : 0;
    const actual = Array(n).fill(0);
    spark.forEach(a => { const off = n - a.length; a.forEach((v, i) => actual[off + i] += Number(v) || 0) });
    const inSet = new Set(rows.map(r => r.sku));
    const fq = Object.values(fcDataMap).filter(f => inSet.has(f.sku)).map(f => (f.forecast_data && f.forecast_data.quantities) || []).filter(a => a.length);
    const m = fq.length ? Math.max(...fq.map(a => a.length)) : 0;
    const forecast = Array(m).fill(0);
    fq.forEach(a => a.forEach((v, i) => forecast[i] += Number(v) || 0));
    if (!n && !m) { ovDem = null; wrap.innerHTML = '<div class="state">No weekly sales or forecast yet.</div>'; document.getElementById('dmStats').textContent = ''; return }
    ovDem = { actual, forecast, skus: inSet.size };
    renderOvDemandChart();
  } catch (e) {
    if (seq !== ovSeq) return;
    ovDem = null;
    wrap.innerHTML = `<div class="state">Demand data not available.</div>`;
  }
}
function renderOvDemandChart() {
  if (!ovDem || typeof Chart === 'undefined') return;
  const wrap = document.getElementById('chDemWrap');
  if (!document.getElementById('ovDemChart')) wrap.innerHTML = '<canvas id="ovDemChart" aria-label="Actual weekly sales and forecast chart" role="img"></canvas>';
  const { actual, forecast } = ovDem, n = actual.length, m = forecast.length;
  const labels = [...actual.map((_, i) => i === n - 1 ? 'Last wk' : `W-${n - 1 - i}`), ...forecast.map((_, i) => `W+${i + 1}`)];
  const aData = [...actual, ...Array(m).fill(null)];
  const fData = n ? [...Array(n - 1).fill(null), actual[n - 1], ...forecast] : [...forecast];
  const ctx = document.getElementById('ovDemChart').getContext('2d');
  const c1 = cv('--chart-1'), c2 = cv('--chart-2');
  const o = baseOpts(v => fmt(v), c => `${c.dataset.label}: ${fmt(Math.round(c.parsed.y))} units`);
  if (ovDemChartObj) ovDemChartObj.destroy();
  ovDemChartObj = new Chart(ctx, { type: 'line', data: { labels, datasets: [
    { label: 'Actual', data: aData, borderColor: c1, backgroundColor: areaFill(ctx, c1), fill: true, tension: .3, pointRadius: 0, pointHoverRadius: 4, borderWidth: 2, spanGaps: false },
    { label: 'Forecast', data: fData, borderColor: c2, fill: false, borderDash: [6, 4], tension: .3, pointRadius: 0, pointHoverRadius: 4, borderWidth: 2, spanGaps: false },
  ] }, options: o });
  const last4 = actual.slice(-4), a4 = last4.length ? last4.reduce((x, y) => x + y, 0) / last4.length : null;
  const next8 = forecast.slice(0, 8), f8 = next8.length ? next8.reduce((x, y) => x + y, 0) / next8.length : null;
  document.getElementById('dmStats').innerHTML = [a4 != null ? `Last 4 weeks <b>${fmt(Math.round(a4))}</b> units/week` : '', f8 != null ? `Forecast next ${next8.length} weeks <b>${fmt(Math.round(f8))}</b> units/week` : ''].filter(Boolean).join(' · ');
}

/* Operational Summary */
function renderOvOps(po) {
  const f = (replData && replData.freshness) || {};
  const ago = n => n == null ? '' : n === 0 ? 'today' : n === 1 ? '1 day ago' : `${n} days ago`;
  const lvl = f.stock_level;
  const stockState = !f.stock_date ? ['crit', 'No snapshot'] : lvl === 'fresh' ? ['ok', 'Up to date'] : lvl === 'stale' ? ['warn', 'Getting old'] : ['crit', 'Outdated'];
  const row = (i, tone, l, v, d, pill, go) => `<${go ? `button class="op-r" onclick="${go}"` : 'div class="op-r"'}>${ico(i, 'ico-' + tone)}<div class="op-m"><div class="op-l">${l}</div><div class="op-v">${v}</div><div class="op-d">${d}</div></div>${pill || ''}</${go ? 'button' : 'div'}>`;
  const pill = (tone, t) => `<span class="pill pill-${tone}">${t}</span>`;
  document.getElementById('ovOps').innerHTML = [
    row('sales', 'navy', 'Last sales update', f.last_sale_date ? dfmt(f.last_sale_date) : '--', f.last_sale_date ? `Latest sale in the data · ${ago(f.sales_age_days)}` : 'No sales uploaded yet', '', "switchTab('upload')"),
    row('box', 'blue', 'Last stock snapshot', f.stock_date ? dfmt(f.stock_date) : '--', f.stock_date ? `Report date · ${ago(f.stock_age_days)}` : 'Upload a stock report on the Data page', '', "switchTab('upload')"),
    po ? row('truck', 'amber', 'Pending purchase orders', `${fmt(po.open_po_numbers || 0)} open POs`, `${fmt(po.outstanding_qty || 0)} units to arrive`, po.overdue ? pill('warn', `${fmt(po.overdue)} past ETA`) : pill('ok', 'On schedule'), "switchTab('po')")
      : row('truck', 'amber', 'Pending purchase orders', '--', 'Not available'),
    row('upload', 'slate', 'Data upload status', stockState[1], `${ovFc && ovFc.calculated_at ? `Forecast refreshed ${fmtStamp(ovFc.calculated_at)}` : 'Forecast not run yet'} · stock counts as current for ${f.warn_days ?? 7} days`, pill(stockState[0], stockState[0] === 'ok' ? 'OK' : 'Action'), "switchTab('upload')"),
  ].join('');
}

/* Navigation into Replenishment with context */
function openReplBucket(k) { replTab = k; replStatus = ''; switchTab('replenishment') }
function openReplStatus(st) { replTab = 'all'; replStatus = st; switchTab('replenishment') }
function openReplSku(sku) { const r = (replData && replData.results || []).find(x => x.sku === sku); replTab = r ? r.bucket : 'all'; replStatus = ''; switchTab('replenishment'); openReplDetail(sku) }
