/* Overview (Phase 5): data context → stock health → Purchasing Action Center → demand and PO context lines.
   Counts for health and buckets come from the same /api/replenishment rows (fetchRepl), so they always agree.
   Sales history moved to Product Analysis › Sales (pages/sales.js). */
let ovSeq = 0, acSel = 'now', ovRows = [];

async function loadOverview() {
  const seq = ++ovSeq;
  setOvLoading();
  const fcP = fetch(`/api/overview?preset=l4w&top_sort=revenue&product_type=${PT}`).then(r => r.ok ? r.json() : null).catch(() => null);
  const poP = fetch('/api/po/summary').then(r => r.ok ? r.json() : null).catch(() => null);
  try {
    const d = await fetchRepl();
    if (seq !== ovSeq) return;
    ovRows = d.results || [];
    renderFresh(d.freshness);
    renderOvContext(d, null);
    renderOvHealth(ovRows);
    if (!ovRows.some(r => r.bucket === acSel)) acSel = (BUCKETS.find(b => ovRows.some(r => r.bucket === b.key)) || BUCKETS[0]).key;
    renderActionCenter();
  } catch (e) {
    if (seq !== ovSeq) return;
    document.getElementById('acList').innerHTML = `<div class="state state-error">${ICON.alert}<div><b>Could not load recommendations.</b><br>${esc(e.message)}</div><button class="btn btn-ghost" onclick="replData=null;loadOverview()">Retry</button></div>`;
    document.getElementById('ovHealthLegend').innerHTML = '<span class="text-dim">Not available</span>';
    document.getElementById('acBuckets').innerHTML = '';
  }
  fcP.then(ov => { if (seq !== ovSeq) return; renderOvDemand(ov && ov.forecast_summary); if (replData) renderOvContext(replData, ov && ov.forecast_summary) });
  poP.then(po => { if (seq === ovSeq) renderOvPo(po) });
}

function setOvLoading() {
  ['ovDemand', 'ovPo'].forEach(id => { document.getElementById(id).innerHTML = '<span class="text-dim">Loading…</span>' });
}

function renderOvContext(d, fcs) {
  const f = d.freshness || {};
  const parts = [];
  parts.push(`Stock <b>${f.stock_date ? dfmt(f.stock_date) : 'no snapshot'}</b>`);
  parts.push(`Sales to <b>${f.last_sale_date ? dfmt(f.last_sale_date) : '--'}</b>`);
  if (fcs && fcs.calculated_at) parts.push(`Forecast <b>${dfmt(String(fcs.calculated_at).slice(0, 10))}</b>`);
  document.getElementById('ovContext').innerHTML = `${{ import: 'Import best sellers', food: 'Food', all: 'All products' }[PT] || ''} · ${parts.join(' · ')}`;
}

const HEALTH = [
  ['understocked', 'Understocked', 'var(--red)', '≤ 8 weeks'],
  ['healthy', 'Healthy', 'var(--green)', '> 8–20 weeks'],
  ['overstocked', 'Overstocked', 'var(--slate)', '> 20 weeks'],
  [null, 'Not classified', 'var(--chart-3)', 'no sales and no stock'],
];
function renderOvHealth(rows) {
  const tot = rows.length;
  const cnt = k => rows.filter(r => (r.status || null) === k).length;
  const oos = rows.filter(r => r.out_of_stock).length;
  document.getElementById('ovHealthMeta').textContent = `${fmt(tot)} best-seller SKUs`;
  document.getElementById('ovHealthBar').innerHTML = HEALTH.map(([k, l, c]) => {
    const n = cnt(k); return n ? `<span style="width:${(n / Math.max(tot, 1) * 100).toFixed(2)}%;background:${c}" title="${l}: ${n}"></span>` : '';
  }).join('');
  document.getElementById('ovHealthLegend').innerHTML = HEALTH.map(([k, l, c, h]) => {
    const n = cnt(k); const pct = tot ? Math.round(n / tot * 100) : 0;
    const inner = `<span class="hl-l"><span class="sw" style="background:${c}"></span>${l}</span><span class="hl-v">${fmt(n)} <small>${pct}%</small></span><span class="hl-h">${h}</span>`;
    return k ? `<button class="hl-item" onclick="openReplStatus('${k}')" title="Show ${l.toLowerCase()} SKUs in Replenishment">${inner}</button>` : `<div class="hl-item">${inner}</div>`;
  }).join('') + `<div class="hl-item hl-sub"><span class="hl-l">Out of stock</span><span class="hl-v">${fmt(oos)}</span><span class="hl-h">stock ≤ 0, included above</span></div>`;
}

function bucketStats(rows) {
  const units = rows.reduce((a, r) => a + (r.reco_po || 0), 0);
  const priced = rows.filter(r => (r.reco_po || 0) > 0 && r.unit_cost != null);
  const noCost = rows.filter(r => (r.reco_po || 0) > 0 && r.unit_cost == null).length;
  const cost = priced.reduce((a, r) => a + r.reco_po * r.unit_cost, 0);
  const over = rows.reduce((a, r) => a + (r.overstock_value || 0), 0);
  return { units, cost, noCost, over, verified: rows.every(r => r.cost_verified !== false) };
}

function renderActionCenter() {
  const rows = ovRows;
  document.getElementById('acMeta').textContent = rows.length ? `${fmt(rows.length)} SKUs in 4 groups · each SKU is in exactly one group` : 'No forecasted best sellers yet';
  document.getElementById('acBuckets').innerHTML = BUCKETS.map(b => {
    const rs = rows.filter(r => r.bucket === b.key), s = bucketStats(rs);
    let sub = '';
    if (b.key === 'now' || b.key === 'soon') sub = rs.length ? `${fmt(s.units)} units${s.cost ? ` · ≈ ${fmtRpS(s.cost)}` : ''}${s.noCost ? ` (+${s.noCost} no cost)` : ''}` : 'none';
    else if (b.key === 'hold') sub = rs.length ? (s.over ? `${fmtRpS(s.over)} overstock value` : 'incoming PO covers the need') : 'none';
    else sub = rs.length ? 'order-by more than 2 weeks away' : 'none';
    return `<button class="ac-b ac-${b.tone}${acSel === b.key ? ' active' : ''}" role="tab" aria-selected="${acSel === b.key}" onclick="selectBucket('${b.key}')">
      <span class="ac-b-l"><span class="dot"></span>${b.label}</span><span class="ac-b-v">${fmt(rs.length)}<small> SKUs</small></span><span class="ac-b-s">${sub}${(b.key === 'now' || b.key === 'soon') && rs.length && !s.verified ? ' · <span title="Unit cost not yet confirmed by finance">cost unverified</span>' : ''}</span></button>`;
  }).join('');
  renderAcList();
}
function selectBucket(k) { acSel = k; renderActionCenter() }

function renderAcList() {
  const rs = ovRows.filter(r => r.bucket === acSel).sort(byPriority);
  const b = BUCKET[acSel];
  if (!ovRows.length) { document.getElementById('acList').innerHTML = '<div class="state">No forecasted best sellers yet. Upload sales and stock data, then run Refresh forecast on the Data page.</div>'; return }
  if (!rs.length) { document.getElementById('acList').innerHTML = `<div class="state">No SKUs in “${b.label}”.</div>`; return }
  const top = rs.slice(0, 5);
  let h = `<div class="tbl-wrap"><table class="ro-table ac-table"><thead><tr><th>Product</th><th>Order by</th><th class="r" title="Stock on hand + open PO with a known ETA">Stock + incoming</th><th class="r" title="Average forecast demand per week, next 8 weeks">Demand / week</th><th class="r">Recommended</th><th>Why</th></tr></thead><tbody>`;
  top.forEach(r => {
    const sk = String(r.sku).replace(/'/g, "\\'");
    const why = explainSku(r);
    h += `<tr class="clickable" onclick="openReplSku('${sk}')"><td><div class="p-name" title="${esc(r.product_name)}">${esc(r.product_name)}</div><div class="p-sku">${esc(r.sku)} ${statusBadge(r.status)}</div></td>
      <td>${orderByCell(r, true)}</td><td class="r">${fmt(r.current_stock)}${r.confirmed_incoming ? ` <span class="text-dim">+${fmt(r.confirmed_incoming)}</span>` : ''}</td>
      <td class="r">${r.fc_avg_weekly != null ? fmt(Math.round(r.fc_avg_weekly)) : '--'}</td><td class="r t-strong">${fmt(r.reco_po || 0)}</td>
      <td class="why-cell" title="${esc(why.join(' '))}">${esc(why[0])}</td></tr>`;
  });
  h += '</tbody></table></div>';
  if (rs.length > top.length) h += `<div class="ac-more"><button class="link-btn" onclick="openReplBucket('${acSel}')">See all ${fmt(rs.length)} in Replenishment →</button></div>`;
  document.getElementById('acList').innerHTML = h;
}

function renderOvDemand(fcs) {
  const el = document.getElementById('ovDemand');
  if (!fcs || fcs.forecast_demand_8w == null) { el.innerHTML = '<span class="text-dim">No forecast yet — run Refresh forecast on the Data page.</span>'; return }
  el.innerHTML = `<b>${fmt(fcs.forecast_demand_8w)}</b> units forecast for the next 8 weeks · <b>${fmt(fcs.trending_down || 0)}</b> SKUs with a declining forecast · <b>${fmt(fcs.trending_up || 0)}</b> rising`;
}
function renderOvPo(po) {
  const el = document.getElementById('ovPo');
  if (!po) { el.innerHTML = '<span class="text-dim">Not available</span>'; return }
  const npc = replData && replData.summary ? replData.summary.needs_po_check || 0 : 0;
  el.innerHTML = `<b>${fmt(po.open_po_numbers || 0)}</b> open POs · <b>${fmt(po.outstanding_qty || 0)}</b> units incoming · ${po.overdue ? `<b class="t-red">${fmt(po.overdue)}</b> lines past ETA` : 'no lines past ETA'}${npc ? ` · <b class="t-amber">${fmt(npc)}</b> SKUs with a PO without ETA (not counted)` : ''}`;
}

/* Navigation into Replenishment with context */
function openReplBucket(k) { replTab = k; replStatus = ''; switchTab('replenishment') }
function openReplStatus(st) { replTab = 'all'; replStatus = st; switchTab('replenishment') }
function openReplSku(sku) { const r = (replData && replData.results || []).find(x => x.sku === sku); replTab = r ? r.bucket : 'all'; replStatus = ''; switchTab('replenishment'); openReplDetail(sku) }
