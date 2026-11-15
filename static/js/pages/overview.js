/* Inventory Overview (revision R8): 4 KPIs → Demand: Actual vs Forecast + Stock Status → Pareto curve +
   Operational Summary → Priority SKUs → data status strip. Each number appears once on the page.
   Inventory numbers come from the same /api/replenishment rows (fetchRepl) as the Replenishment page, so they agree.
   Demand comes from /api/forecast/plan (core/demand.js); monthly sales and best sellers from /api/overview for the
   chosen month. Nothing here recalculates a business value. */
let ovSeq = 0, ovRows = [], ovFc = null, ovDem = null, ovMonths = [], ovMonthSel = null, ovMonthSeq = 0;
let ovDemChartObj = null, ovMonChartObj = null, ovParChartObj = null, ovPo = null, ovScope = null, ovPlan = null;
const PRIORITY_LIMIT = 5;

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
    renderOvStatus();
    renderPriority();
    loadOvMonths(seq);
  } catch (e) {
    if (seq !== ovSeq) return;
    ovRows = [];
    const err = `<div class="state state-error">${ICON.alert}<div><b>Could not load inventory data.</b><br>${esc(e.message)}</div><button class="btn btn-ghost" onclick="replData=null;loadOverview()">Retry</button></div>`;
    document.getElementById('ovKpis').innerHTML = err;
    document.getElementById('prList').innerHTML = '';
    document.getElementById('ovStatus').innerHTML = '<div class="state">Not available</div>';
    document.getElementById('ovAsOf').innerHTML = '<span class="text-dim">Data status unknown</span>';
  }
  loadOvDemand(seq);
  loadOvPareto(seq);
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
  if (ovFc && ovFc.calculated_at) {
    // the period being forecast (from the week after the latest sales data), then when it was calculated
    const end = ovFc.horizon_end ? new Date(ovFc.horizon_end + 'T00:00:00') : null;
    if (end) end.setDate(end.getDate() + 6);                       // last forecast week runs Monday to Sunday
    const iso = d => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
    const period = ovFc.horizon_start && end ? `${dfmt(ovFc.horizon_start)} – ${dfmt(iso(end))}` : '';
    const tip = `${ovFc.horizon_weeks || 12}-week forecast, from the week after the latest sales data. Updated ${fmtStamp(ovFc.calculated_at)} (runs after every sales upload). Open the Data page`;
    h += `<button class="chip" onclick="switchTab('upload')" title="${esc(tip)}">${ico('fc', 'ico-s')}Forecast <b>${period || fmtStamp(ovFc.calculated_at)}</b>${period ? `<span class="text-dim">· updated ${fmtStamp(ovFc.calculated_at)}</span>` : ''}</button>`;
  }
  document.getElementById('ovAsOf').innerHTML = h;
}
function fmtStamp(ts) {
  // Timestamps come from the server in UTC ("2026-11-11T10:03:11+00:00"); show them in the viewer's local time.
  const s = String(ts);
  if (s.length <= 10) return dfmt(s);
  const dt = new Date(s.replace(' ', 'T'));
  if (isNaN(dt)) return dfmt(s.slice(0, 10)) + ' ' + s.slice(11, 16);
  const p = n => String(n).padStart(2, '0');
  return dfmt(`${dt.getFullYear()}-${p(dt.getMonth() + 1)}-${p(dt.getDate())}`) + ' ' + p(dt.getHours()) + ':' + p(dt.getMinutes());
}

/* B. KPIs: one number per question. Out of stock is a subset of the statuses, never added to them. */
function healthCounts(rows) {
  const c = k => rows.filter(r => (r.status || null) === k).length;
  return { total: rows.length, understocked: c('understocked'), healthy: c('healthy'), overstocked: c('overstocked'), unclassified: c(null), oos: rows.filter(r => r.out_of_stock).length };
}
const median = a => { if (!a.length) return null; const s = a.slice().sort((x, y) => x - y), m = s.length >> 1; return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2 };
/* Forecast error of all best sellers together on the holdout weeks: Σ|Σactual − Σforecast| ÷ Σactual (weekly totals),
   next to the median per-SKU holdout WMAPE. Both come from the same holdout run as the demand chart. */
function ovAccuracy() {
  if (!ovDem) return null;
  const ws = [...ovDem.past].sort();
  let ae = 0, act = 0;
  ws.forEach(w => { const a = ovDem.actual[w] || 0, f = ovDem.holdoutFc[w] || 0; ae += Math.abs(a - f); act += a });
  const per = (ovPlan && ovPlan.results || []).map(p => p.holdout && p.holdout.wmape).filter(v => v != null);
  return { all: act ? ae / act * 100 : null, perSku: median(per), n: per.length, weeks: ws.length };
}
function renderOvKpis() {
  const el = document.getElementById('ovKpis');
  if (!el) return;
  const rows = ovRows, h = healthCounts(rows);
  const order = rows.filter(r => r.bucket === 'order'), m = order.filter(r => r.mandatory);
  const units = m.reduce((a, r) => a + (r.reco_po || 0), 0), cost = m.reduce((a, r) => a + (r.unit_cost != null ? (r.reco_po || 0) * r.unit_cost : 0), 0);
  const withCost = rows.filter(r => r.unit_cost != null), val = withCost.reduce((a, r) => a + Math.max(r.current_stock || 0, 0) * r.unit_cost, 0);
  const covMed = median(rows.map(r => r.coverage).filter(v => v != null));
  const sc = ovScope && ovScope.pareto, acc = ovAccuracy();
  const pct1 = v => v == null ? '--' : (Math.round(v * 10) / 10).toLocaleString('id-ID') + '%';
  const card = o => {
    const inner = `<div class="kc-l">${o.l}${info(o.d)}</div><div class="kc-v kv-${o.tone}">${o.v}</div><div class="kc-d">${o.s}</div>`;
    return o.go ? `<button class="card kc kc-${o.k}" data-k="${o.k}" onclick="${o.go}" title="${o.t}">${inner}</button>` : `<div class="card kc kc-${o.k}" data-k="${o.k}">${inner}</div>`;
  };
  el.innerHTML = [
    { k: 'total', tone: 'navy', l: 'Best-seller SKUs', v: fmt(h.total),
      s: sc ? `${fmt(sc.skus)} of ${fmt(ovScope.skus_sold)} import SKUs (${sc.share_of_skus_pct}%) · ${sc.revenue_pct}% of revenue` : 'Pareto 80% of import revenue',
      d: 'SKUs that together make up 80% of import revenue (Pareto). Only these are forecasted.', go: "openReplBucket('all')", t: 'Open all best-seller SKUs in Replenishment' },
    { k: 'order', tone: 'red', l: 'Order Now', v: `${fmt(order.length)} <small>SKUs</small>`,
      s: order.length ? `${fmt(m.length)} to order · ${fmt(units)} units${cost ? ` · ≈ ${fmtRpS(cost)}` : ''}` : 'Nothing to order',
      d: 'Understocked SKUs (coverage ≤ 8 weeks). The recommended quantity is in Replenishment.', go: "openReplBucket('order')", t: 'Open the order list in Replenishment' },
    { k: 'value', tone: 'navy', l: 'Inventory Value', v: withCost.length ? fmtRpS(val) : '--',
      s: `${covMed != null ? `median coverage ${covMed.toFixed(1)} wks` : 'coverage not available'}${withCost.length < rows.length ? ` · ${fmt(rows.length - withCost.length)} SKUs without cost` : ''}`,
      d: 'Stock on hand × unit cost, best-seller SKUs. Coverage = (stock + open PO) ÷ average weekly sales of the last 4 weeks.' },
    { k: 'acc', tone: 'blue', l: 'Forecast Error', v: acc && acc.all != null ? pct1(acc.all) : '--',
      s: acc && acc.all != null ? `all SKUs combined, ${acc.weeks}-week holdout · per-SKU median ${pct1(acc.perSku)}` : 'Run Refresh forecast',
      d: 'WMAPE on the holdout weeks (the model did not see them). All SKUs combined = error of the weekly total. Per-SKU median = the typical single SKU.',
      go: "switchTab('analysis');setSub('forecast')", t: 'Open the demand plan' },
  ].map(card).join('');
}

/* C2. Stock status: one stacked bar and one clickable row per status (replaces the donut, coverage chart and groups). */
function renderOvStatus() {
  const el = document.getElementById('ovStatus');
  if (!el) return;
  if (!ovRows.length) { el.innerHTML = '<div class="state">No forecasted best sellers yet.</div>'; return }
  const h = healthCounts(ovRows), st = (replData && replData.settings) || {};
  const U = st.understock_weeks ?? 8, O = st.overstock_weeks ?? 20;
  const items = [['understocked', 'Understocked', `≤ ${U} wks`, '--c-red'], ['healthy', 'Healthy', `${U}–${O} wks`, '--c-green'], ['overstocked', 'Overstocked', `> ${O} wks`, '--c-slate']];
  if (h.unclassified) items.push([null, 'Not classified', 'no sales, no stock', '--chart-3']);
  const pct = n => h.total ? n / h.total * 100 : 0;
  const bar = items.map(([k, l, , c]) => { const n = h[k || 'unclassified']; return n ? `<span style="width:${pct(n).toFixed(2)}%;background:var(${c})" title="${l}: ${fmt(n)} SKUs"></span>` : '' }).join('');
  const rows = items.map(([k, l, rule, c]) => {
    const n = h[k || 'unclassified'];
    const inner = `<span class="hl-l"><span class="sw" style="background:var(${c})"></span>${l} <span class="text-dim">${rule}</span></span><span class="hl-v">${fmt(n)}</span><span class="hl-p">${Math.round(pct(n))}%</span>`;
    return k ? `<button class="hl-item" onclick="openReplStatus('${k}')" title="Show ${l.toLowerCase()} SKUs in Replenishment">${inner}</button>` : `<div class="hl-item">${inner}</div>`;
  }).join('');
  el.innerHTML = `<div class="st-bar" role="img" aria-label="Share of SKUs per stock status">${bar}</div><div class="hl-legend">${rows}</div>
    <div class="st-oos">${ico('empty', 'ico-amber ico-s')}<span><b>${fmt(h.oos)}</b> out of stock (stock ≤ 0) · already counted above</span></div>`;
}
/* Charts redraw on theme change (core/settings.js). */
function renderOvCharts() { if (ovDem) renderOvDemandChart(); if (ovMonths.length) renderOvMonthChart(); if (ovScope) renderOvPareto() }

/* E. Priority SKUs: Understocked (Order Now), recommended > 0 first, in the Replenishment priority order. */
function priorityRows() {
  return ovRows.filter(r => r.bucket === 'order').sort(byPriority);
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
    const reason = r.zero === 'forecast_zero' ? ' <span class="tag tag-fw" title="' + esc(zeroReasonText(r)) + '">Forecast warning</span>' : r.zero === 'sufficient' ? ' <span class="tag tag-cov" title="' + esc(zeroReasonText(r)) + '">Stock / PO sufficient</span>' : '';
    h += `<tr class="clickable" onclick="openReplSku('${sk}')"><td class="p-sku">${esc(r.sku)}</td><td><div class="p-name" title="${esc(r.product_name)}">${esc(r.product_name)}</div></td>
      <td>${statusBadge(r.status)}<div class="pr-grp"><span class="pill pill-${b.tone}">${b.label}</span>${reason}</div></td>
      <td class="r">${r.out_of_stock ? '<span class="t-red t-strong">' + fmt(r.current_stock) + '</span>' : fmt(r.current_stock)}</td>
      <td>${covCell(r)}</td><td class="r">${r.ongoing_po ? fmt(r.ongoing_po) : '<span class="text-dim">0</span>'}</td>
      <td class="r">${r.fc_avg_weekly != null ? fmt(Math.round(r.fc_avg_weekly)) : '--'}</td><td class="r t-strong">${fmt(r.reco_po || 0)}</td></tr>`;
  });
  el.innerHTML = h + '</tbody></table></div>';
}

function chartTip() { const ct = getCT(); return { backgroundColor: ct.tipBg, titleColor: ct.tipTitle, bodyColor: ct.tipBody, borderColor: ct.tipBorder, borderWidth: 1, padding: 10, cornerRadius: 8 } }

/* C. Demand — Actual vs Forecast: weekly, holdout weeks (actual vs forecast for the same weeks) then the horizon. */
async function loadOvDemand(seq) {
  const wrap = document.getElementById('chDemWrap');
  try {
    const p = await fetchPlan();
    if (seq !== ovSeq) return;
    const s = demandSeries(p);
    if (!s.weeks.length) { ovDem = null; wrap.innerHTML = '<div class="state">No forecast yet. Run Refresh forecast on the Data page.</div>'; document.getElementById('dmStats').textContent = ''; return }
    ovDem = s; ovPlan = p;
    renderOvDemandChart();
    renderOvKpis();
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
  const ct = getCT(), ca = cv('--chart-act'), cf = cv('--chart-fc');
  const last = (replData && replData.freshness || {}).last_sale_date;
  const partial = w => last && s.past.has(w) && new Date(w + 'T00:00:00').getTime() + 6 * 864e5 > new Date(last + 'T00:00:00').getTime();
  const labels = s.weeks.map(w => wkLabel(w) + (partial(w) ? '*' : ''));
  const act = s.weeks.map(w => s.past.has(w) ? Math.round(s.actual[w] || 0) : null);
  const fc = s.weeks.map(w => s.past.has(w) ? Math.round(s.holdoutFc[w] || 0) : Math.round(s.forecast[w] || 0));
  if (ovDemChartObj) ovDemChartObj.destroy();
  const firstFut = s.weeks.findIndex(w => !s.past.has(w));
  ovDemChartObj = new Chart(document.getElementById('ovDemChart'), {
    type: 'line',
    data: { labels, datasets: [
      { label: 'Actual', data: act, borderColor: ca, backgroundColor: ca, borderWidth: 2.5, pointRadius: 3, pointHoverRadius: 5, tension: .25, fill: false, spanGaps: false },
      { label: 'Forecast', data: fc, borderColor: cf, backgroundColor: cf, borderWidth: 2.5, pointRadius: 3, pointHoverRadius: 5, tension: .25, fill: false },
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
    plugins: [{ id: 'split', beforeDraw(ch) { if (firstFut < 1) return; const x = ch.scales.x, { top, bottom } = ch.chartArea, px = (x.getPixelForValue(firstFut - 1) + x.getPixelForValue(firstFut)) / 2, g = ch.ctx; g.save(); g.fillStyle = hexA(cf, .07); g.fillRect(px, top, ch.chartArea.right - px, bottom - top); g.strokeStyle = cv('--border2'); g.setLineDash([3, 3]); g.beginPath(); g.moveTo(px, top); g.lineTo(px, bottom); g.stroke(); g.fillStyle = cv('--text3'); g.font = '11px Inter'; g.fillText('Forecast horizon', px + 6, top + 12); g.restore() } }],
  });
  const b = biasSummary(s);
  const nf = s.weeks.filter(w => !s.past.has(w)).slice(0, 8), f8 = nf.length ? nf.reduce((a, w) => a + (s.forecast[w] || 0), 0) / nf.length : null;
  document.getElementById('dmMeta').textContent = `Weekly units · ${fmt(s.evaluated)} of ${fmt(s.skus)} best-seller SKUs have past forecast weeks${labels.some(l => l.endsWith('*')) ? ' · * partial week' : ''}`;
  document.getElementById('dmStats').innerHTML = [b.weeks ? `Past ${b.weeks} weeks: forecast <b>${fmt(Math.round(b.fsum))}</b> vs actual <b>${fmt(Math.round(b.asum))}</b> units${b.pct != null ? ` (${fmtPct(b.pct)})` : ''} · above actual in ${b.over} of ${b.weeks} weeks` : '', f8 != null ? `Next ${nf.length} weeks: <b>${fmt(Math.round(f8))}</b> units/week` : ''].filter(Boolean).join(' · ');
}

/* C3. Pareto curve: the best-seller scope (same rule as the forecast run), from /api/bestseller/scope. */
async function loadOvPareto(seq) {
  const wrap = document.getElementById('chParWrap');
  try {
    const r = await fetch('/api/bestseller/scope');
    if (!r.ok) throw new Error('Scope request failed (' + r.status + ')');
    const d = await r.json();
    if (seq !== ovSeq) return;
    ovScope = d;
    renderOvPareto();
    renderOvKpis();
  } catch (e) {
    if (seq !== ovSeq) return;
    ovScope = null;
    wrap.innerHTML = '<div class="state">Pareto curve not available.</div>';
  }
}
function renderOvPareto() {
  const d = ovScope, wrap = document.getElementById('chParWrap');
  if (!d || !wrap || typeof Chart === 'undefined') return;
  const pts = (d.curve || []).map(c => ({ x: c.rank, y: c.cum_pct }));
  if (!pts.length) { wrap.innerHTML = '<div class="state">No import sales yet.</div>'; return }
  if (!document.getElementById('ovParChart')) wrap.innerHTML = '<canvas id="ovParChart" aria-label="Pareto curve of import revenue" role="img"></canvas>';
  const ct = getCT(), cf = cv('--chart-fc'), red = cv('--c-red'), cut = d.pareto.skus, n = d.skus_sold;
  const p = d.period || {};
  document.getElementById('paMeta').innerHTML = `<b>${fmt(cut)}</b> of ${fmt(n)} import SKUs (${d.pareto.share_of_skus_pct}%) give <b>${d.pareto.revenue_pct}%</b> of import revenue${p.start ? ` · ${dfmt(p.start)} – ${dfmt(p.end)}` : ''}`;
  if (ovParChartObj) ovParChartObj.destroy();
  ovParChartObj = new Chart(document.getElementById('ovParChart'), {
    type: 'line',
    data: { datasets: [{ data: [{ x: 0, y: 0 }, ...pts], borderColor: cf, backgroundColor: cf, borderWidth: 2.5, pointRadius: 0, pointHoverRadius: 4, tension: 0, fill: false }] },
    options: {
      responsive: true, maintainAspectRatio: false, interaction: { mode: 'nearest', axis: 'x', intersect: false }, parsing: false,
      plugins: { legend: { display: false }, tooltip: { ...chartTip(), callbacks: { title: c => `Top ${fmt(c[0].parsed.x)} SKUs`, label: c => `${c.parsed.y.toFixed(1)}% of import revenue` } } },
      scales: {
        x: { type: 'linear', min: 0, max: n, grid: { display: false }, border: { color: ct.grid }, ticks: { color: ct.tick, font: ct.font, callback: v => fmt(v) }, title: { display: true, text: 'SKU rank (highest revenue first)', color: ct.tick, font: ct.font } },
        y: { min: 0, max: 100, grid: { color: ct.grid }, border: { display: false }, ticks: { color: ct.tick, font: ct.font, stepSize: 20, callback: v => v + '%' } },
      },
    },
    plugins: [{ id: 'scope', beforeDatasetsDraw(ch) {
      const x = ch.scales.x, y = ch.scales.y, { top, bottom, left, right } = ch.chartArea, g = ch.ctx, px = x.getPixelForValue(cut), py = y.getPixelForValue(80);
      g.save();
      g.fillStyle = hexA(cf, .08); g.fillRect(left, top, px - left, bottom - top);
      g.strokeStyle = red; g.setLineDash([5, 4]); g.beginPath(); g.moveTo(left, py); g.lineTo(right, py); g.stroke(); g.setLineDash([]);
      g.font = '12px Inter'; g.fillStyle = red; g.textAlign = 'right'; g.fillText('80% of revenue', right - 4, py - 6);
      g.fillStyle = cf; g.textAlign = 'left'; g.font = '600 12px Inter'; g.fillText(`← ${fmt(cut)} best-seller SKUs`, px + 8, bottom - 10);
      g.restore();
    } }],
  });
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
/* Data status strip: PO, last sales, last stock snapshot, forecast run. */
function renderOvOps() {
  const el = document.getElementById('ovOps');
  if (!el) return;
  const f = (replData && replData.freshness) || {}, po = ovPo;
  const ago = n => n == null ? '' : n === 0 ? 'today' : n === 1 ? '1 day ago' : `${n} days ago`;
  const lvl = f.stock_level;
  const stockState = !f.stock_date ? ['crit', 'No snapshot'] : lvl === 'fresh' ? ['ok', 'Up to date'] : lvl === 'stale' ? ['warn', 'Getting old'] : ['crit', 'Outdated'];
  const pill = (tone, t) => `<span class="pill pill-${tone}">${t}</span>`;
  const it = (i, tone, l, v, go) => `<button class="op-s" onclick="${go}">${ico(i, 'ico-' + tone + ' ico-s')}<span class="op-l">${l}</span><span class="op-v">${v}</span></button>`;
  el.innerHTML = [
    it('truck', 'amber', 'Open POs', po ? `${fmt(po.open_po_numbers || 0)} · ${fmt(po.outstanding_qty || 0)} units ${po.overdue ? pill('warn', `${fmt(po.overdue)} past ETA`) : ''}` : '--', "switchTab('po')"),
    it('sales', 'navy', 'Sales to', f.last_sale_date ? `${dfmt(f.last_sale_date)} <span class="text-dim">${ago(f.sales_age_days)}</span>` : 'none', "switchTab('upload')"),
    it('box', 'blue', 'Stock snapshot', f.stock_date ? `${dfmt(f.stock_date)} ${pill(stockState[0], stockState[1])}` : pill('crit', 'Upload a stock report'), "switchTab('upload')"),
    it('fc', 'slate', 'Forecast run', ovFc && ovFc.calculated_at ? fmtStamp(ovFc.calculated_at) : 'not run yet', "switchTab('upload')"),
  ].join('');
}

/* Navigation into Replenishment with context */
function openReplBucket(k) { replTab = k; replStatus = ''; replSysOnly = false; switchTab('replenishment') }
function openReplStatus(st) { replTab = 'all'; replStatus = st; replSysOnly = false; switchTab('replenishment') }
function openReplSku(sku) { const r = (replData && replData.results || []).find(x => x.sku === sku); replTab = r && r.bucket !== 'unclassified' ? r.bucket : 'all'; replStatus = ''; replSysOnly = false; switchTab('replenishment'); openReplDetail(sku) }
