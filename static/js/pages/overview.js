/* Inventory Overview: header + data date → inventory health KPIs → Purchasing Action Center → Priority SKUs →
   Inventory Insights (health + coverage distribution) → Operational Summary.
   Every inventory number comes from the same /api/replenishment rows (fetchRepl), so KPIs, groups, table and charts
   always agree with the Replenishment page. Nothing here recalculates a business value: statuses, coverage,
   order-by dates and recommended quantities are shown as the API returns them. */
let ovSeq = 0, ovRows = [], ovFc = null, ovHealthChartObj = null, ovCovChartObj = null;
const PRIORITY_LIMIT = 8;

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
    ['chHealthWrap', 'chCovWrap'].forEach(id => { document.getElementById(id).innerHTML = '<div class="state">Not available</div>' });
    document.getElementById('ovAsOf').innerHTML = '<span class="text-dim">Data status unknown</span>';
  }
  const [ov, po] = await Promise.all([fcP, poP]);
  if (seq !== ovSeq) return;
  ovFc = ov && ov.forecast_summary;
  if (replData) renderOvAsOf();
  renderOvOps(po);
}

/* Section A: header data date (the "last updated" of what the page shows) */
function renderOvAsOf() {
  const f = (replData && replData.freshness) || {};
  const parts = [`Stock snapshot <b>${f.stock_date ? dfmt(f.stock_date) : 'none'}</b>`];
  if (ovFc && ovFc.calculated_at) parts.push(`Forecast <b>${fmtStamp(ovFc.calculated_at)}</b>`);
  document.getElementById('ovAsOf').innerHTML = `<span class="ov-asof-l">Last updated</span><span>${parts.join(' · ')}</span>`;
}
function fmtStamp(ts) {
  const s = String(ts), d = s.slice(0, 10), t = s.length > 15 ? s.slice(11, 16) : '';
  return dfmt(d) + (t ? ' ' + t : '');
}

/* Section B: inventory health KPIs. Out of stock is a subset of the statuses, never added to them. */
function healthCounts(rows) {
  const c = k => rows.filter(r => (r.status || null) === k).length;
  return { total: rows.length, understocked: c('understocked'), healthy: c('healthy'), overstocked: c('overstocked'), unclassified: c(null), oos: rows.filter(r => r.out_of_stock).length };
}
function renderOvKpis() {
  const h = healthCounts(ovRows), st = (replData && replData.settings) || {};
  const U = st.understock_weeks ?? 8, O = st.overstock_weeks ?? 20;
  const pct = n => h.total ? Math.round(n / h.total * 100) + '%' : '0%';
  const bar = [['understocked', 'var(--red)'], ['healthy', 'var(--green)'], ['overstocked', 'var(--slate)'], ['unclassified', 'var(--chart-3)']]
    .map(([k, c]) => h[k] ? `<span style="width:${(h[k] / h.total * 100).toFixed(2)}%;background:${c}"></span>` : '').join('');
  const card = (o) => {
    const inner = `<div class="kc-l"><span class="kc-dot" style="background:${o.c}"></span>${o.l}</div><div class="kc-v">${fmt(o.v)}${o.pct ? `<small>${o.pct}</small>` : ''}</div><div class="kc-d">${o.d}</div>${o.extra || ''}`;
    return o.go ? `<button class="card kc kc-${o.k}" onclick="${o.go}" title="${o.t}">${inner}</button>` : `<div class="card kc kc-${o.k}">${inner}</div>`;
  };
  document.getElementById('ovKpis').innerHTML = [
    { k: 'total', l: 'Total SKUs', v: h.total, c: 'var(--chart-1)', d: `Best-seller SKUs with a forecast · ${fmt(h.healthy)} healthy${h.unclassified ? ` · ${fmt(h.unclassified)} not classified` : ''}`, extra: `<div class="kc-bar" aria-hidden="true">${bar}</div>`, go: "openReplBucket('all')", t: 'Open all SKUs in Replenishment' },
    { k: 'under', l: 'Understocked', v: h.understocked, pct: pct(h.understocked), c: 'var(--red)', d: `Coverage ≤ ${U} weeks`, go: "openReplStatus('understocked')", t: 'Show understocked SKUs in Replenishment' },
    { k: 'oos', l: 'Out of Stock', v: h.oos, pct: pct(h.oos), c: 'var(--red)', d: 'Stock ≤ 0 · already counted in the statuses' },
    { k: 'over', l: 'Overstocked', v: h.overstocked, pct: pct(h.overstocked), c: 'var(--slate)', d: `Coverage > ${O} weeks, or stock without sales`, go: "openReplStatus('overstocked')", t: 'Show overstocked SKUs in Replenishment' },
  ].map(card).join('');
}

/* Section C: Purchasing Action Center. Buckets come from bucketOf (core/decision.js, spec §9.2): mutually exclusive. */
function bucketStats(rows) {
  const priced = rows.filter(r => (r.reco_po || 0) > 0 && r.unit_cost != null);
  return {
    units: rows.reduce((a, r) => a + (r.reco_po || 0), 0),
    cost: priced.reduce((a, r) => a + r.reco_po * r.unit_cost, 0),
    noCost: rows.filter(r => (r.reco_po || 0) > 0 && r.unit_cost == null).length,
    over: rows.reduce((a, r) => a + (r.overstock_value || 0), 0),
    verified: rows.every(r => r.cost_verified !== false),
  };
}
function renderActionCenter() {
  const rows = ovRows;
  document.getElementById('acMeta').textContent = rows.length ? `${fmt(rows.length)} SKUs in 4 groups · each SKU is counted once` : 'No forecasted best sellers yet';
  document.getElementById('acBuckets').innerHTML = BUCKETS.map(b => {
    const rs = rows.filter(r => r.bucket === b.key), s = bucketStats(rs);
    let sub = '';
    if (!rs.length) sub = '';
    else if (b.key === 'now' || b.key === 'soon') sub = `${fmt(s.units)} units recommended${s.cost ? ` · ≈ ${fmtRpS(s.cost)}` : ''}${s.noCost ? ` (+${s.noCost} without cost)` : ''}${!s.verified ? ' · cost unverified' : ''}`;
    else if (b.key === 'hold') sub = s.over ? `${fmtRpS(s.over)} overstock value` : 'Incoming PO covers the need';
    else sub = 'Nothing to order this cycle';
    return `<button class="ac-b ac-${b.tone}" onclick="openReplBucket('${b.key}')" title="Open “${b.label}” in Replenishment">
      <span class="ac-b-l"><span class="dot"></span>${b.label}</span><span class="ac-b-v">${fmt(rs.length)}<small> SKUs</small></span>
      <span class="ac-b-h">${b.hint}</span>${sub ? `<span class="ac-b-s">${sub}</span>` : ''}<span class="ac-b-go">View list →</span></button>`;
  }).join('');
}

/* Section D: Priority SKUs = Order now, then Upcoming orders, in the Replenishment priority order (spec §9.3). */
function priorityRows() {
  return ovRows.filter(r => r.bucket === 'now' || r.bucket === 'soon').sort((a, b) => (a.bucket === b.bucket ? byPriority(a, b) : a.bucket === 'now' ? -1 : 1));
}
function renderPriority() {
  const el = document.getElementById('prList');
  if (!ovRows.length) { el.innerHTML = '<div class="state">No forecasted best sellers yet. Upload sales and stock data, then run Refresh forecast on the Data page.</div>'; return }
  const all = priorityRows();
  if (!all.length) { el.innerHTML = '<div class="state">No SKU needs ordering in the next 2 weeks.</div>'; document.getElementById('prMeta').textContent = 'SKUs requiring purchasing attention.'; return }
  const top = all.slice(0, PRIORITY_LIMIT);
  document.getElementById('prMeta').textContent = `SKUs requiring purchasing attention · top ${top.length} of ${fmt(all.length)}, most urgent first`;
  const covCls = r => r.status === 'understocked' ? 't-red' : r.status === 'overstocked' ? 'text-dim' : '';
  let h = `<div class="tbl-wrap"><table class="ro-table pr-table"><thead><tr><th>Product / SKU</th><th class="r">Current stock</th>
    <th class="r" title="Average weekly sales of the last 4 weeks">Avg weekly sales</th><th class="r" title="(stock + open PO) ÷ average weekly sales of the last 4 weeks">Stock coverage</th>
    <th class="r" title="Open PO quantity still to arrive">Incoming PO</th><th class="r">Recommended qty</th><th>Action</th></tr></thead><tbody>`;
  top.forEach(r => {
    const sk = String(r.sku).replace(/'/g, "\\'"), b = BUCKET[r.bucket];
    const inc = r.ongoing_po ? `${fmt(r.ongoing_po)}${r.unconfirmed_incoming ? `<div class="ob-sub ob-soon" title="Open PO without ETA is not counted in the recommendation">${fmt(r.unconfirmed_incoming)} without ETA</div>` : ''}` : '<span class="text-dim">0</span>';
    h += `<tr class="clickable" onclick="openReplSku('${sk}')"><td><div class="p-name" title="${esc(r.product_name)}">${esc(r.product_name)}</div><div class="p-sku">${esc(r.sku)}</div></td>
      <td class="r">${r.out_of_stock ? '<span class="t-red t-strong">0</span>' : fmt(r.current_stock)}</td>
      <td class="r">${r.avg_l4w != null ? fmt(Math.round(r.avg_l4w)) : '--'}</td>
      <td class="r ${covCls(r)}">${r.coverage != null ? r.coverage.toFixed(1) + ' wks' : '<span class="text-dim">no sales</span>'}</td>
      <td class="r">${inc}</td><td class="r t-strong">${fmt(r.reco_po || 0)}</td>
      <td><div class="pr-act"><span class="tag tag-b-${b.tone}">${b.label}</span><span class="ob-sub">${orderByShort(r)}</span></div></td></tr>`;
  });
  h += '</tbody></table></div>';
  if (all.length > top.length) h += `<div class="ac-more"><button class="link-btn" onclick="openReplBucket('${top[top.length - 1].bucket === 'soon' ? 'soon' : 'now'}')">${fmt(all.length - top.length)} more in Replenishment →</button></div>`;
  el.innerHTML = h;
}
function orderByShort(r) {
  if (!r.order_by || r.order_by_days == null) return '';
  const d = r.order_by_days;
  return d < 0 ? `order-by ${-d} days ago` : d === 0 ? 'order-by today' : `order by ${dfmt(r.order_by)}`;
}

/* Section E: Inventory Insights. Both charts use only status and coverage from the API. */
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
function renderOvCharts() {
  if (typeof Chart === 'undefined') return;
  const h = healthCounts(ovRows);
  const hw = document.getElementById('chHealthWrap'), cw = document.getElementById('chCovWrap');
  if (!ovRows.length) { hw.innerHTML = cw.innerHTML = '<div class="state">No data yet.</div>'; return }
  if (!document.getElementById('ovHealthChart')) hw.innerHTML = '<canvas id="ovHealthChart" aria-label="Inventory health distribution chart" role="img"></canvas>';
  if (!document.getElementById('ovCovChart')) cw.innerHTML = '<canvas id="ovCovChart" aria-label="Stock coverage distribution chart" role="img"></canvas>';
  const ct = getCT();
  const tip = { backgroundColor: ct.tipBg, titleColor: ct.tipTitle, bodyColor: ct.tipBody, borderColor: ct.tipBorder, borderWidth: 1, padding: 10, cornerRadius: 8 };

  const hs = [['understocked', 'Understocked', '--red'], ['healthy', 'Healthy', '--green'], ['overstocked', 'Overstocked', '--slate']];
  if (h.unclassified) hs.push([null, 'Not classified', '--chart-3']);
  document.getElementById('chHealthMeta').textContent = `SKUs per stock status · ${fmt(h.total)} SKUs · click a bar to open the list`;
  if (ovHealthChartObj) ovHealthChartObj.destroy();
  ovHealthChartObj = new Chart(document.getElementById('ovHealthChart'), {
    type: 'bar',
    data: { labels: hs.map(x => x[1]), datasets: [{ data: hs.map(x => h[x[0] || 'unclassified']), backgroundColor: hs.map(x => cv(x[2])), borderRadius: 4, barThickness: 22 }] },
    options: {
      indexAxis: 'y', responsive: true, maintainAspectRatio: false,
      plugins: { legend: { display: false }, tooltip: { ...tip, callbacks: { label: c => `${fmt(c.parsed.x)} SKUs (${h.total ? Math.round(c.parsed.x / h.total * 100) : 0}%)` } } },
      scales: { x: { min: 0, grid: { color: ct.grid }, border: { display: false }, ticks: { color: ct.tick, font: ct.font, precision: 0 } }, y: { grid: { display: false }, border: { color: ct.grid }, ticks: { color: ct.tick, font: ct.font } } },
      onClick: (e, els) => { if (els.length) { const k = hs[els[0].index][0]; if (k) openReplStatus(k) } },
      onHover: (e, els) => { e.native.target.style.cursor = els.length && hs[els[0].index][0] ? 'pointer' : 'default' },
    },
  });

  const bins = covBins();
  const counts = bins.map(b => ovRows.filter(r => b.t ? (r.coverage != null && b.t(r.coverage)) : r.coverage == null).length);
  if (ovCovChartObj) ovCovChartObj.destroy();
  ovCovChartObj = new Chart(document.getElementById('ovCovChart'), {
    type: 'bar',
    data: { labels: bins.map(b => b.l), datasets: [{ data: counts, backgroundColor: bins.map(b => cv('--' + b.z)), borderRadius: 4, maxBarThickness: 40 }] },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: { legend: { display: false }, tooltip: { ...tip, callbacks: { title: c => c[0].label === 'No sales' ? 'No sales in 4 weeks' : `${c[0].label} weeks of coverage`, label: c => `${fmt(c.parsed.y)} SKUs` } } },
      scales: { x: { grid: { display: false }, border: { color: ct.grid }, ticks: { color: ct.tick, font: ct.font, maxRotation: 0 }, title: { display: true, text: 'Weeks of coverage', color: ct.tick, font: ct.font } }, y: { min: 0, grid: { color: ct.grid }, border: { display: false }, ticks: { color: ct.tick, font: ct.font, precision: 0 } } },
    },
  });
}

/* Section F: Operational Summary: is the data behind the page current? */
function renderOvOps(po) {
  const f = (replData && replData.freshness) || {};
  const ago = n => n == null ? '' : n === 0 ? 'today' : n === 1 ? '1 day ago' : `${n} days ago`;
  const lvl = f.stock_level;
  const stockState = !f.stock_date ? ['crit', 'No stock snapshot'] : lvl === 'fresh' ? ['ok', 'Up to date'] : lvl === 'stale' ? ['warn', 'Getting old'] : ['crit', 'Upload a new stock report'];
  const tile = (l, v, d, tone, link) => `<div class="op-t"><div class="op-l">${tone ? `<span class="op-dot op-${tone}"></span>` : ''}${l}</div><div class="op-v">${v}</div><div class="op-d">${d}</div>${link || ''}</div>`;
  const tiles = [
    tile('Last sales update', f.last_sale_date ? dfmt(f.last_sale_date) : '--', f.last_sale_date ? `Latest sale in the data · ${ago(f.sales_age_days)}` : 'No sales uploaded yet'),
    tile('Last stock snapshot', f.stock_date ? dfmt(f.stock_date) : '--', f.stock_date ? `Report date · ${ago(f.stock_age_days)}` : 'Upload a stock report on the Data page', stockState[0]),
    po ? tile('Pending purchase orders', `${fmt(po.open_po_numbers || 0)} <small>open POs</small>`, `${fmt(po.outstanding_qty || 0)} units to arrive · ${po.overdue ? `<b class="t-red">${fmt(po.overdue)}</b> lines past ETA` : 'none past ETA'}`, po.overdue ? 'warn' : null, `<button class="link-btn" onclick="switchTab('po')">PO Tracking →</button>`)
      : tile('Pending purchase orders', '--', 'Not available'),
    tile('Data upload status', stockState[1], `${ovFc && ovFc.calculated_at ? `Forecast refreshed ${fmtStamp(ovFc.calculated_at)}` : 'No forecast run yet'} · stock counts as current for ${f.warn_days ?? 7} days`, stockState[0], `<button class="link-btn" onclick="switchTab('upload')">Data →</button>`),
  ];
  document.getElementById('ovOps').innerHTML = tiles.join('');
}

/* Navigation into Replenishment with context */
function openReplBucket(k) { replTab = k; replStatus = ''; switchTab('replenishment') }
function openReplStatus(st) { replTab = 'all'; replStatus = st; switchTab('replenishment') }
function openReplSku(sku) { const r = (replData && replData.results || []).find(x => x.sku === sku); replTab = r ? r.bucket : 'all'; replStatus = ''; switchTab('replenishment'); openReplDetail(sku) }
