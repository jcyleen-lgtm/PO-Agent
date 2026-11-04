/* Inventory Overview (revision R7): KPIs → purchasing groups → Demand: Actual vs Forecast and Operational Summary →
   Priority SKUs → Inventory Insights.
   Inventory numbers come from the same /api/replenishment rows (fetchRepl) as the Replenishment page, so they agree.
   Demand comes from /api/forecast/plan (core/demand.js); monthly sales and best sellers from /api/overview for the
   chosen month. Nothing here recalculates a business value. */
let ovSeq = 0, ovRows = [], ovFc = null, ovDem = null, ovMonths = [], ovMonthSel = null, ovMonthSeq = 0;
let ovHealthChartObj = null, ovCovChartObj = null, ovDemChartObj = null, ovMonChartObj = null, ovPo = null;
const PRIORITY_LIMIT = 8;

const OVI = {
  box: '<path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"/><polyline points="3.27 6.96 12 12.01 20.73 6.96"/><line x1="12" y1="22.08" x2="12" y2="12"/>',
  down: '<path d="M12 5v14M5 12l7 7 7-7"/>',
  ok: '<circle cx="12" cy="12" r="9"/><path d="m8 12 3 3 5-6"/>',
  empty: '<circle cx="12" cy="12" r="9"/><path d="M5.6 5.6l12.8 12.8"/>',
  layers: '<path d="M12 2 2 7l10 5 10-5-10-5z"/><path d="m2 17 10 5 10-5"/><path d="m2 12 10 5 10-5"/>',
  cart: '<circle cx="9" cy="20" r="1.5"/><circle cx="18" cy="20" r="1.5"/><path d="M2 3h3l2.7 12.4a2 2 0 0 0 2 1.6h8.6a2 2 0 0 0 2-1.6L22 7H6"/>',
  eye: '<path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/>',
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
    loadOvMonths(seq);
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
  ovPo = po;
  if (replData) renderOvAsOf();
  renderOvOps();
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

/* B. KPIs. Out of stock is a subset of the statuses, never added to them. */
function healthCounts(rows) {
  const c = k => rows.filter(r => (r.status || null) === k).length;
  return { total: rows.length, understocked: c('understocked'), healthy: c('healthy'), overstocked: c('overstocked'), unclassified: c(null), oos: rows.filter(r => r.out_of_stock).length };
}
function renderOvKpis() {
  const h = healthCounts(ovRows), st = (replData && replData.settings) || {};
  const U = st.understock_weeks ?? 8, O = st.overstock_weeks ?? 20;
  const pct = n => h.total ? Math.round(n / h.total * 100) + '%' : '0%';
  const card = o => {
    const inner = `<div class="kc-top">${ico(o.i, 'ico-' + o.tone)}${o.badge ? `<span class="kc-badge kb-${o.tone}">${o.badge}</span>` : ''}</div>
      <div class="kc-l">${o.l}</div><div class="kc-v kv-${o.tone}">${o.v}</div><div class="kc-d">${o.d}</div>`;
    return o.go ? `<button class="card kc" data-k="${o.k}" onclick="${o.go}" title="${o.t}">${inner}</button>` : `<div class="card kc" data-k="${o.k}">${inner}</div>`;
  };
  document.getElementById('ovKpis').innerHTML = [
    { k: 'total', i: 'box', tone: 'navy', l: 'Total SKUs', v: fmt(h.total), d: `Best-seller SKUs with a forecast${h.unclassified ? ` · ${fmt(h.unclassified)} not classified` : ''}`, go: "openReplBucket('all')", t: 'Open all SKUs in Replenishment' },
    { k: 'under', i: 'down', tone: 'red', l: 'Understocked', v: fmt(h.understocked), badge: pct(h.understocked), d: `Coverage ≤ ${U} weeks`, go: "openReplStatus('understocked')", t: 'Show Understocked SKUs in Replenishment' },
    { k: 'healthy', i: 'ok', tone: 'green', l: 'Healthy', v: fmt(h.healthy), badge: pct(h.healthy), d: `Coverage > ${U} up to ${O} weeks`, go: "openReplStatus('healthy')", t: 'Show Healthy SKUs in Replenishment' },
    { k: 'over', i: 'layers', tone: 'slate', l: 'Overstocked', v: fmt(h.overstocked), badge: pct(h.overstocked), d: `Coverage > ${O} weeks, or no sales`, go: "openReplStatus('overstocked')", t: 'Show Overstocked SKUs in Replenishment' },
    { k: 'oos', i: 'empty', tone: 'amber', l: 'Out of Stock', v: fmt(h.oos), badge: pct(h.oos), d: 'Stock ≤ 0 · already counted in the statuses' },
  ].map(card).join('');
}

/* Purchasing groups (core/decision.js): mutually exclusive, each opens its Replenishment tab. */
const BUCKET_ICON = { order: 'cart', covered: 'eye', hold: 'pause', none: 'check' };
function renderActionCenter() {
  const rows = ovRows;
  const unc = rows.filter(r => r.bucket === 'unclassified').length;
  document.getElementById('acMeta').textContent = rows.length ? `${fmt(rows.length)} SKUs · each counted once${unc ? ` · ${fmt(unc)} not classified` : ''}` : 'No forecasted best sellers yet';
  document.getElementById('acBuckets').innerHTML = BUCKETS.map(b => {
    const rs = rows.filter(r => r.bucket === b.key);
    let sub = '';
    if (b.key === 'order') { const u = rs.reduce((a, r) => a + (r.reco_po || 0), 0), c = rs.reduce((a, r) => a + (r.unit_cost != null ? (r.reco_po || 0) * r.unit_cost : 0), 0); sub = rs.length ? `${fmt(u)} units${c ? ` · ≈ ${fmtRpS(c)}` : ''} · in the order list` : 'Nothing to order'; }
    else if (b.key === 'covered') { const fz = rs.filter(r => r.zero === 'forecast_zero').length; sub = rs.length ? `${fmt(rs.length - fz)} stock / PO sufficient${fz ? ` · <b class="t-red">${fmt(fz)} forecast warning</b>` : ''}` : 'None'; }
    else if (b.key === 'hold') { const sr = rs.filter(r => r.sysReco).length; sub = sr ? `${fmt(sr)} System Recommended` : 'Optional'; }
    else sub = 'Optional';
    return `<button class="ac-b ac-${b.tone}" data-k="${b.key}" onclick="openReplBucket('${b.key}')" title="${esc(b.hint)} · open in Replenishment">
      <span class="ac-b-l">${ico(BUCKET_ICON[b.key], 'ico-' + b.tone + ' ico-s')}${b.label}</span>
      <span class="ac-b-v">${fmt(rs.length)}<small> SKUs</small></span>
      <span class="ac-b-s">${sub}</span></button>`;
  }).join('');
}

/* E. Priority SKUs: Understocked first (Order Now, then Covered / Monitor), each in the Replenishment priority order. */
function priorityRows() {
  const rank = { order: 0, covered: 1 };
  return ovRows.filter(r => r.bucket in rank).sort((a, b) => (a.bucket === b.bucket ? byPriority(a, b) : rank[a.bucket] - rank[b.bucket]));
}
function renderPriority() {
  const el = document.getElementById('prList'), more = document.getElementById('prMore');
  more.textContent = '';
  if (!ovRows.length) { el.innerHTML = '<div class="state">No forecasted best sellers yet. Upload sales and stock data, then run Refresh forecast on the Data page.</div>'; return }
  const all = priorityRows();
  if (!all.length) { el.innerHTML = '<div class="state">No Understocked SKUs.</div>'; return }
  const top = all.slice(0, PRIORITY_LIMIT);
  if (all.length > top.length) more.textContent = `Showing ${top.length} of ${fmt(all.length)} Understocked SKUs`;
  const st = (replData && replData.settings) || {}, O = st.overstock_weeks ?? 20;
  const covCell = r => {
    if (r.coverage == null) return '<span class="text-dim">no sales</span>';
    const tone = r.status === 'understocked' ? 'red' : r.status === 'overstocked' ? 'slate' : 'green';
    return `<div class="cov"><span class="cov-v t-${tone}">${r.coverage.toFixed(1)} wks</span><span class="cov-bar"><span class="cb-${tone}" style="width:${Math.min(100, r.coverage / O * 100).toFixed(1)}%"></span></span></div>`;
  };
  let h = `<div class="tbl-wrap"><table class="ro-table pr-table"><thead><tr><th>SKU</th><th>Product</th><th>Status</th><th class="r">Stock</th>
    <th title="(stock + open PO) ÷ average weekly sales of the last 4 weeks">Coverage</th><th class="r" title="Open PO quantity still to arrive">Incoming PO</th>
    <th class="r" title="Average forecast demand per week, next 8 weeks">Forecast / Wk</th><th class="r">Recommended PO</th></tr></thead><tbody>`;
  top.forEach(r => {
    const sk = String(r.sku).replace(/'/g, "\\'"), b = BUCKET[r.bucket];
    const reason = r.zero === 'forecast_zero' ? ' <span class="tag tag-fw" title="' + esc(zeroReasonText(r)) + '">Forecast warning</span>' : r.zero === 'covered' ? ' <span class="tag tag-cov" title="' + esc(zeroReasonText(r)) + '">Stock / PO sufficient</span>' : '';
    h += `<tr class="clickable" onclick="openReplSku('${sk}')"><td class="p-sku">${esc(r.sku)}</td><td><div class="p-name" title="${esc(r.product_name)}">${esc(r.product_name)}</div></td>
      <td>${statusBadge(r.status)}<div class="pr-grp"><span class="pill pill-${b.tone}">${b.label}</span>${reason}</div></td>
      <td class="r">${r.out_of_stock ? '<span class="t-red t-strong">' + fmt(r.current_stock) + '</span>' : fmt(r.current_stock)}</td>
      <td>${covCell(r)}</td><td class="r">${r.ongoing_po ? fmt(r.ongoing_po) : '<span class="text-dim">0</span>'}</td>
      <td class="r">${r.fc_avg_weekly != null ? fmt(Math.round(r.fc_avg_weekly)) : '--'}</td><td class="r t-strong">${fmt(r.reco_po || 0)}</td></tr>`;
  });
  el.innerHTML = h + '</tbody></table></div>';
}

/* F. Inventory Insights: health donut, coverage distribution (status and coverage from the API only). */
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
    data: { labels: bins.map(b => b.l), datasets: [{ data: counts, backgroundColor: bins.map(b => cv('--' + b.z)), borderRadius: 5, maxBarThickness: 34 }] },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: { legend: { display: false }, tooltip: { ...tip, callbacks: { title: c => c[0].label === 'No sales' ? 'No sales in 4 weeks' : `${c[0].label} weeks of coverage`, label: c => `${fmt(c.parsed.y)} SKUs` } } },
      scales: { x: { grid: { display: false }, border: { color: ct.grid }, ticks: { color: ct.tick, font: { ...ct.font, size: 11 }, maxRotation: 0, autoSkip: false }, title: { display: true, text: 'Weeks of coverage', color: ct.tick, font: ct.font } }, y: { min: 0, grid: { color: ct.grid }, border: { display: false }, ticks: { color: ct.tick, font: ct.font, precision: 0 } } },
    },
  });
  if (ovDem) renderOvDemandChart();
  if (ovMonths.length) renderOvMonthChart();
}

/* C. Demand — Actual vs Forecast: weekly, holdout weeks (actual vs forecast for the same weeks) then the horizon. */
async function loadOvDemand(seq) {
  const wrap = document.getElementById('chDemWrap');
  try {
    const p = await fetchPlan();
    if (seq !== ovSeq) return;
    const s = demandSeries(p);
    if (!s.weeks.length) { ovDem = null; wrap.innerHTML = '<div class="state">No forecast yet. Run Refresh forecast on the Data page.</div>'; document.getElementById('dmStats').textContent = ''; return }
    ovDem = s;
    renderOvDemandChart();
  } catch (e) {
    if (seq !== ovSeq) return;
    ovDem = null;
    wrap.innerHTML = '<div class="state">Demand data not available.</div>';
  }
}
function renderOvDemandChart() {
  if (!ovDem || typeof Chart === 'undefined') return;
  const s = ovDem, wrap = document.getElementById('chDemWrap');
  if (!document.getElementById('ovDemChart')) wrap.innerHTML = '<canvas id="ovDemChart" aria-label="Weekly actual sales and forecast chart" role="img"></canvas>';
  const ct = getCT(), c1 = cv('--chart-1'), c2 = cv('--chart-2');
  const last = (replData && replData.freshness || {}).last_sale_date;
  const partial = w => last && s.past.has(w) && new Date(w + 'T00:00:00').getTime() + 6 * 864e5 > new Date(last + 'T00:00:00').getTime();
  const labels = s.weeks.map(w => wkLabel(w) + (partial(w) ? '*' : ''));
  const act = s.weeks.map(w => s.past.has(w) ? Math.round(s.actual[w] || 0) : null);
  const fc = s.weeks.map(w => s.past.has(w) ? Math.round(s.holdoutFc[w] || 0) : Math.round(s.forecast[w] || 0));
  if (ovDemChartObj) ovDemChartObj.destroy();
  const firstFut = s.weeks.findIndex(w => !s.past.has(w));
  ovDemChartObj = new Chart(document.getElementById('ovDemChart'), {
    data: { labels, datasets: [
      { type: 'bar', label: 'Actual', data: act, backgroundColor: hexA(c1, .75), borderRadius: 3, maxBarThickness: 26, order: 2 },
      { type: 'line', label: 'Forecast', data: fc, borderColor: c2, borderDash: [6, 4], borderWidth: 2, pointRadius: 2.5, pointBackgroundColor: c2, tension: .25, fill: false, order: 1 },
    ] },
    options: {
      responsive: true, maintainAspectRatio: false, interaction: { mode: 'index', intersect: false },
      plugins: { legend: { display: false }, tooltip: { ...chartTip(), callbacks: {
        title: c => { const w = s.weeks[c[0].dataIndex]; return `Week of ${dfmt(w)}${s.past.has(w) ? ' · past' : ' · forecast'}${partial(w) ? ' (partial week)' : ''}` },
        label: c => c.parsed.y == null ? null : `${c.dataset.label}: ${fmt(c.parsed.y)} units`,
        afterBody: c => { const w = s.weeks[c[0].dataIndex]; if (!s.past.has(w)) return ''; const a = s.actual[w] || 0, f = s.holdoutFc[w] || 0; return a ? `Forecast ${f >= a ? 'above' : 'below'} actual by ${fmt(Math.abs(Math.round(f - a)))} units (${fmtPct((f - a) / a * 100)})` : '' },
      } } },
      scales: { x: { grid: { display: false }, border: { color: ct.grid }, ticks: { color: ct.tick, font: { ...ct.font, size: 11 }, maxRotation: 0, autoSkipPadding: 8 } },
        y: { min: 0, grid: { color: ct.grid }, border: { display: false }, ticks: { color: ct.tick, font: ct.font, callback: v => fmt(v) } } },
    },
    plugins: [{ id: 'split', beforeDraw(ch) { if (firstFut < 1) return; const x = ch.scales.x, { top, bottom } = ch.chartArea, px = (x.getPixelForValue(firstFut - 1) + x.getPixelForValue(firstFut)) / 2, g = ch.ctx; g.save(); g.fillStyle = hexA(c2, .06); g.fillRect(px, top, ch.chartArea.right - px, bottom - top); g.strokeStyle = cv('--border2'); g.setLineDash([3, 3]); g.beginPath(); g.moveTo(px, top); g.lineTo(px, bottom); g.stroke(); g.fillStyle = cv('--text3'); g.font = '11px Inter'; g.fillText('Forecast horizon', px + 6, top + 12); g.restore() } }],
  });
  const b = biasSummary(s);
  const nf = s.weeks.filter(w => !s.past.has(w)).slice(0, 8), f8 = nf.length ? nf.reduce((a, w) => a + (s.forecast[w] || 0), 0) / nf.length : null;
  document.getElementById('dmMeta').textContent = `Weekly units · ${fmt(s.evaluated)} of ${fmt(s.skus)} best-seller SKUs have past forecast weeks${labels.some(l => l.endsWith('*')) ? ' · * partial week' : ''}`;
  document.getElementById('dmStats').innerHTML = [b.weeks ? `Past ${b.weeks} weeks: forecast <b>${fmt(Math.round(b.fsum))}</b> vs actual <b>${fmt(Math.round(b.asum))}</b> units${b.pct != null ? ` (${fmtPct(b.pct)})` : ''} · above actual in ${b.over} of ${b.weeks} weeks` : '', f8 != null ? `Next ${nf.length} weeks: <b>${fmt(Math.round(f8))}</b> units/week` : ''].filter(Boolean).join(' · ');
}

/* D. Operational Summary: monthly sales (selected month), best sellers by revenue, PO and data status. */
async function loadOvMonths(seq) {
  const f = (replData && replData.freshness) || {};
  const sel = document.getElementById('ovMonth'), box = document.getElementById('ovSales');
  if (!f.last_sale_date) { box.innerHTML = '<div class="state">No sales uploaded yet.</div>'; sel.innerHTML = ''; return }
  const end = new Date(f.last_sale_date + 'T00:00:00'), start = new Date(end.getFullYear(), end.getMonth() - 11, 1);
  const iso = d => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
  try {
    const r = await fetch(`/api/overview?start=${iso(start)}&end=${f.last_sale_date}&top_sort=revenue&product_type=${PT}`);
    if (!r.ok) throw new Error('Sales request failed (' + r.status + ')');
    const d = await r.json();
    if (seq !== ovSeq) return;
    ovMonths = (d.trend || []).map(t => ({ k: String(t.date).slice(0, 7), revenue: Number(t.revenue) || 0, qty: Number(t.quantity) || 0 })).filter(m => m.revenue || m.qty);
    if (!ovMonths.length) { box.innerHTML = '<div class="state">No sales in the last 12 months.</div>'; return }
    if (!ovMonthSel || !ovMonths.some(m => m.k === ovMonthSel)) ovMonthSel = ovMonths[ovMonths.length - 1].k;
    sel.innerHTML = ovMonths.slice().reverse().map(m => `<option value="${m.k}"${m.k === ovMonthSel ? ' selected' : ''}>${monthName(m.k)}</option>`).join('');
    loadOvMonth(ovMonthSel);
  } catch (e) {
    if (seq !== ovSeq) return;
    box.innerHTML = `<div class="state state-error">${ICON.alert}<div>${esc(e.message)}</div></div>`;
  }
}
const monthName = k => new Date(k + '-01T00:00:00').toLocaleDateString('en-GB', { month: 'long', year: 'numeric' });
async function loadOvMonth(k) {
  ovMonthSel = k;
  const seq = ++ovMonthSeq, f = (replData && replData.freshness) || {}, box = document.getElementById('ovSales');
  const [y, m] = k.split('-').map(Number), lastDay = new Date(y, m, 0).getDate();
  let end = `${k}-${String(lastDay).padStart(2, '0')}`; const partial = f.last_sale_date && f.last_sale_date < end;
  if (partial) end = f.last_sale_date;
  box.querySelectorAll('.os-v').forEach(e => e.classList.add('skel'));
  try {
    const r = await fetch(`/api/overview?start=${k}-01&end=${end}&top_sort=revenue&product_type=${PT}`);
    if (!r.ok) throw new Error('Sales request failed (' + r.status + ')');
    const d = await r.json();
    if (seq !== ovMonthSeq) return;
    const rev = Number((d.kpis || {}).revenue) || 0, qty = Number((d.kpis || {}).total_sales) || 0;
    const top = (d.top_products || []).slice(0, 10), topRev = top.reduce((a, t) => a + (Number(t.revenue) || 0), 0);
    const share = rev ? topRev / rev * 100 : null;
    box.innerHTML = `<div class="os-grid">
      <div class="os-c"><div class="op-l">Monthly sales${partial ? ` <span class="tag tag-info" title="Sales data ends ${dfmt(f.last_sale_date)}">to ${dfmt(f.last_sale_date)}</span>` : ''}</div><div class="os-v">${fmtRpS(rev)}</div><div class="op-d">${fmt(qty)} units · ${monthName(k)}</div></div>
      <div class="os-c"><div class="op-l" title="Revenue of the top ${top.length} SKUs ranked by revenue ÷ total revenue of the month × 100">Best-seller revenue contribution</div><div class="os-v">${share != null ? share.toFixed(1) + '%' : '--'}</div><div class="op-d">Top ${top.length} products by revenue = ${fmtRpS(topRev)} of ${fmtRpS(rev)}</div></div>
    </div>
    <div class="chart-wrap os-chart"><canvas id="ovMonChart" aria-label="Monthly sales chart" role="img"></canvas></div>
    <details class="disc os-top"><summary>Best-selling products, ${monthName(k)} (ranked by revenue)</summary><ol class="top-list">${top.map(t => `<li><span class="tl-n" title="${esc(t.name)}">${esc(t.name)}</span><span class="tl-v">${fmtRpS(t.revenue)} <small>${rev ? (t.revenue / rev * 100).toFixed(1) + '%' : ''}</small></span></li>`).join('') || '<li class="text-dim">No sales</li>'}</ol></details>`;
    renderOvMonthChart();
  } catch (e) {
    if (seq !== ovMonthSeq) return;
    box.innerHTML = `<div class="state state-error">${ICON.alert}<div>${esc(e.message)}</div></div>`;
  }
}
function renderOvMonthChart() {
  const cvs = document.getElementById('ovMonChart');
  if (!cvs || typeof Chart === 'undefined' || !ovMonths.length) return;
  const ct = getCT(), c1 = cv('--chart-1'), c3 = cv('--chart-3');
  if (ovMonChartObj) ovMonChartObj.destroy();
  ovMonChartObj = new Chart(cvs, {
    type: 'bar',
    data: { labels: ovMonths.map(m => new Date(m.k + '-01T00:00:00').toLocaleDateString('en-GB', { month: 'short' })), datasets: [{ data: ovMonths.map(m => m.revenue), backgroundColor: ovMonths.map(m => m.k === ovMonthSel ? c1 : hexA(c3, .7)), borderRadius: 3, maxBarThickness: 22 }] },
    options: { responsive: true, maintainAspectRatio: false,
      plugins: { legend: { display: false }, tooltip: { ...chartTip(), callbacks: { title: c => monthName(ovMonths[c[0].dataIndex].k), label: c => fmtRp(c.parsed.y) } } },
      scales: { x: { grid: { display: false }, border: { color: ct.grid }, ticks: { color: ct.tick, font: { ...ct.font, size: 10 }, maxRotation: 0 } }, y: { display: false, min: 0 } },
      onClick: (e, els) => { if (els.length) { const k = ovMonths[els[0].index].k; document.getElementById('ovMonth').value = k; loadOvMonth(k) } },
      onHover: (e, els) => { e.native.target.style.cursor = els.length ? 'pointer' : 'default' } },
  });
}
function renderOvOps() {
  const f = (replData && replData.freshness) || {}, po = ovPo;
  const ago = n => n == null ? '' : n === 0 ? 'today' : n === 1 ? '1 day ago' : `${n} days ago`;
  const lvl = f.stock_level;
  const stockState = !f.stock_date ? ['crit', 'No snapshot'] : lvl === 'fresh' ? ['ok', 'Up to date'] : lvl === 'stale' ? ['warn', 'Getting old'] : ['crit', 'Outdated'];
  const row = (i, tone, l, v, d, pill, go) => `<${go ? `button class="op-r" onclick="${go}"` : 'div class="op-r"'}>${ico(i, 'ico-' + tone)}<div class="op-m"><div class="op-l">${l}</div><div class="op-v">${v}</div><div class="op-d">${d}</div></div>${pill || ''}</${go ? 'button' : 'div'}>`;
  const pill = (tone, t) => `<span class="pill pill-${tone}">${t}</span>`;
  document.getElementById('ovOps').innerHTML = [
    po ? row('truck', 'amber', 'Pending purchase orders', `${fmt(po.open_po_numbers || 0)} open POs`, `${fmt(po.outstanding_qty || 0)} units to arrive`, po.overdue ? pill('warn', `${fmt(po.overdue)} past ETA`) : pill('ok', 'On schedule'), "switchTab('po')")
      : row('truck', 'amber', 'Pending purchase orders', '--', 'Not available'),
    row('sales', 'navy', 'Last sales update', f.last_sale_date ? dfmt(f.last_sale_date) : '--', f.last_sale_date ? ago(f.sales_age_days) : 'No sales uploaded yet', '', "switchTab('upload')"),
    row('box', 'blue', 'Last stock snapshot', f.stock_date ? dfmt(f.stock_date) : '--', f.stock_date ? ago(f.stock_age_days) : 'Upload a stock report', '', "switchTab('upload')"),
    row('upload', 'slate', 'Data upload status', stockState[1], `${ovFc && ovFc.calculated_at ? `Forecast ${fmtStamp(ovFc.calculated_at)}` : 'Forecast not run yet'} · stock current for ${f.warn_days ?? 7} days`, pill(stockState[0], stockState[0] === 'ok' ? 'OK' : 'Action'), "switchTab('upload')"),
  ].join('');
}

/* Navigation into Replenishment with context */
function openReplBucket(k) { replTab = k; replStatus = ''; replSysOnly = false; switchTab('replenishment') }
function openReplStatus(st) { replTab = 'all'; replStatus = st; replSysOnly = false; switchTab('replenishment') }
function openReplSku(sku) { const r = (replData && replData.results || []).find(x => x.sku === sku); replTab = r && r.bucket !== 'unclassified' ? r.bucket : 'all'; replStatus = ''; replSysOnly = false; switchTab('replenishment'); openReplDetail(sku) }
