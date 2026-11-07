// Drives every Stockwise page and records, per checkpoint: screenshot, visible text, API calls, layout width.
// Started by run_regression.py:  node regression.js <baseUrl> <outDir> <fixtureDir>
// Deterministic on purpose: fixed browser clock, Chart.js served locally with animations off, same flow every run.
const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');

const [BASE, OUT, FX] = process.argv.slice(2);
const CHART = path.join(path.dirname(require.resolve('chart.js')), 'chart.umd.js');
const CHART_SRC = fs.readFileSync(CHART, 'utf8') + '\n;Chart.defaults.animation=false;';
const XLSX = require('xlsx');
const XLSX_SRC = fs.readFileSync(path.join(path.dirname(require.resolve('xlsx')), 'dist', 'xlsx.full.min.js'), 'utf8');
const CONTEXTS = [
  { name: 'desktop-light', w: 1440, h: 900, theme: 'light', mutate: true, styles: true },
  { name: 'desktop-dark', w: 1440, h: 900, theme: 'dark' },
  { name: 'mobile-light', w: 390, h: 844, theme: 'light' },
];
const mask = s => s.replace(/\b\d{1,2}:\d{2}(:\d{2})?\b/g, '<time>')
  .replace(/\b\d+ (sec|second|min|minute|hour|hr)s?\b( ago)?/gi, '<ago>');

(async () => {
  const browser = await chromium.launch();
  const errors = [];
  const states = [];
  for (const C of CONTEXTS) {
    const ctx = await browser.newContext({ viewport: { width: C.w, height: C.h }, acceptDownloads: true });
    const p = await ctx.newPage();
    await p.clock.setFixedTime(new Date('2026-11-03T10:00:00+07:00'));
    let calls = [];
    let inflight = 0;
    p.on('pageerror', e => errors.push(`${C.name} pageerror: ${e.message}`));
    p.on('console', m => { if (m.type() === 'error' && !/401|Failed to load resource/.test(m.text())) errors.push(`${C.name} console: ${m.text()}`); });
    p.on('request', r => {
      const u = new URL(r.url());
      if (!u.pathname.startsWith('/api/')) return;
      inflight++;
      const ct = r.headers()['content-type'] || '';
      const body = ct.startsWith('multipart') ? '<multipart>' : (r.postData() || '');
      calls.push(`${r.method()} ${u.pathname}${u.search} ${body}`.trim());
    });
    const done = r => { if (new URL(r.url()).pathname.startsWith('/api/')) inflight--; };
    p.on('requestfinished', done);
    p.on('requestfailed', done);
    await p.route('**/*', r => {
      const u = new URL(r.request().url());
      if (u.hostname.includes('cdnjs') && u.pathname.includes('/xlsx/')) return r.fulfill({ body: XLSX_SRC, contentType: 'application/javascript' });
      if (u.hostname.includes('cdnjs')) return r.fulfill({ body: CHART_SRC, contentType: 'application/javascript' });
      if (u.hostname.includes('fonts.g')) return r.fulfill({ body: '', contentType: 'text/css' });
      return r.continue();
    });
    await p.addInitScript(t => localStorage.setItem('po-settings-v4', JSON.stringify({ name: 'San', theme: t, period: 'l4w' })), C.theme);

    const settle = async (ms = 700) => {
      await p.waitForTimeout(ms);
      for (let i = 0; i < 100 && inflight > 0; i++) await p.waitForTimeout(100);
      await p.waitForTimeout(500);   // count-up numbers, rendering
    };
    const snap = async (name, shot = true) => {
      await settle();
      const id = `${C.name}__${name}`;
      const st = await p.evaluate(() => {
        const vis = el => el && el.offsetParent !== null;
        const parts = [];
        const login = document.getElementById('loginOverlay');
        if (login && getComputedStyle(login).display !== 'none' && vis(login)) parts.push('[login] ' + login.innerText);
        const tab = document.querySelector('.tab-content.active');
        if (tab) parts.push(`[${tab.id}] ` + tab.innerText);
        document.querySelectorAll('.modal-backdrop').forEach(m => { if (getComputedStyle(m).display !== 'none' && m.classList.contains('open')) parts.push(`[${m.id}] ` + m.innerText); });
        const toasts = document.getElementById('toasts');
        if (toasts && toasts.innerText.trim()) parts.push('[toasts] ' + toasts.innerText);
        return {
          text: parts.join('\n'),
          theme: document.documentElement.getAttribute('data-theme'),
          activeNav: [...document.querySelectorAll('.nav-item.active')].map(b => b.dataset.tab).join(','),
          scrollWidth: document.documentElement.scrollWidth,
          canvases: [...document.querySelectorAll('canvas')].filter(c => c.offsetParent !== null).length,
        };
      });
      st.text = mask(st.text);
      if (shot && C.styles) st.styles = await p.evaluate(() => {
        // FNV-1a hash of every computed property of every element (+ ::before/::after): catches any CSS change,
        // including on elements that are hidden in this state.
        // freeze running animations/transitions at their start so values do not depend on timing
        const running = document.getAnimations().filter(a => a.playState === 'running');
        running.forEach(a => { try { a.pause(); a.currentTime = 0; } catch (e) {} });
        const h = s => { let x = 0x811c9dc5; for (let i = 0; i < s.length; i++) { x ^= s.charCodeAt(i); x = Math.imul(x, 16777619); } return (x >>> 0).toString(36); };
        // properties sorted by name: the enumeration order of custom properties depends on which file
        // declared them, which is not a visual change
        const all = (el, pseudo) => { const cs = getComputedStyle(el, pseudo); const k = []; for (let i = 0; i < cs.length; i++) k.push(cs[i]); return k.sort().map(n => n + ':' + cs.getPropertyValue(n)).join(';'); };
        const res = [...document.querySelectorAll('body *')].filter(el => el.tagName !== 'SCRIPT').map(el => {
          const d = el.tagName.toLowerCase() + (el.id ? '#' + el.id : '') + (typeof el.className === 'string' && el.className ? '.' + el.className.trim().split(/\s+/).join('.') : '');
          return d + ' ' + h(all(el, null) + '|' + all(el, '::before') + '|' + all(el, '::after'));
        });
        running.forEach(a => { try { a.play(); } catch (e) {} });
        return res;
      });
      st.calls = calls; calls = [];
      states.push({ id, ...st });
      if (shot) await p.screenshot({ path: path.join(OUT, `${id}.png`), fullPage: true, animations: 'disabled' });
    };
    const click = async (sel, ms) => {
      const el = await p.$(sel);
      if (!el) { errors.push(`${C.name}: missing ${sel}`); return; }
      try { await el.click({ timeout: 3000 }); } catch (e) { errors.push(`${C.name}: not clickable ${sel}`); return; }
      await settle(ms);
    };
    const download = async (name, sel, ext = 'csv') => {
      const el = await p.$(sel); if (!el) { errors.push(`${C.name}: missing ${sel}`); return null; }
      const [d] = await Promise.all([p.waitForEvent('download'), el.click()]);
      const f = path.join(OUT, `${C.name}__${name}.${ext}`);
      fs.copyFileSync(await d.path(), f);
      await settle();
      return f;
    };

    // Login
    await p.goto(BASE + '/');
    await snap('00-login');
    await p.fill('#loginEmail', 'admin'); await p.fill('#loginPass', 'wrong'); await p.click('#loginBtn');
    await snap('01-login-wrong', false);
    await p.fill('#loginPass', 'rahasia123'); await p.click('#loginBtn');
    await settle(2500);

    // Overview
    await snap('10-overview');
    // Overview values must equal an independent recomputation from /api/replenishment (R7 groups, KPIs, priority, charts)
    const check = await p.evaluate(async () => {
      const d = await (await fetch('/api/replenishment?product_type=import')).json();
      const R = d.results || [];
      // groups (7 Nov 2026): Understocked → order, Healthy → covered, Overstocked → none
      const g = r => r.status === 'understocked' ? 'order' : r.status === 'healthy' ? 'covered' : r.status === 'overstocked' ? 'none' : 'unclassified';
      const exp = ['order', 'covered', 'none'].map(k => R.filter(r => g(r) === k).length);
      const num = t => parseInt(String(t).replace(/[^0-9]/g, ''), 10);
      const shown = [...document.querySelectorAll('#acBuckets .ac-b-v')].map(e => num(e.firstChild.textContent));
      const st = k => R.filter(r => (r.status || null) === k).length;
      const expK = [R.length, st('understocked'), st('healthy'), st('overstocked'), R.filter(r => r.out_of_stock).length];
      const shownK = ['total', 'under', 'healthy', 'over', 'oos'].map(k => num(document.querySelector(`#ovKpis [data-k="${k}"] .kc-v`).textContent));
      const pri = (x, y) => { const mx = (x.reco_po || 0) > 0 ? 0 : 1, my = (y.reco_po || 0) > 0 ? 0 : 1; if (mx !== my) return mx - my;
        const ex = x.order_by_is_estimate ? 1 : 0, ey = y.order_by_is_estimate ? 1 : 0; if (ex !== ey) return ex - ey;
        const dx = x.order_by || '9999-12-31', dy = y.order_by || '9999-12-31'; if (dx !== dy) return dx < dy ? -1 : 1; return (y.l4w_revenue || 0) - (x.l4w_revenue || 0); };
      const att = R.filter(r => g(r) === 'order').sort(pri).slice(0, 8);
      const expP = att.map(r => [r.sku, r.reco_po || 0]);
      const shownP = [...document.querySelectorAll('#prList tbody tr')].map(tr => [tr.children[0].textContent.trim(), num(tr.children[7].textContent)]);
      const hc = Chart.getChart('ovHealthChart'), cc = Chart.getChart('ovCovChart'), dc = Chart.getChart('ovDemChart'), mc = Chart.getChart('ovMonChart');
      const chartH = hc ? hc.data.datasets[0].data.slice(0, 3) : null;
      const covSum = cc ? cc.data.datasets[0].data.reduce((x, y) => x + y, 0) : null;
      // demand chart: past weeks = sum of holdout actual / forecast from /api/forecast/plan
      const pl = await (await fetch('/api/forecast/plan?product_type=import')).json();
      const act = {}; (pl.results || []).forEach(x => { const h = x.holdout || {}; if (Array.isArray(h.actual) && Array.isArray(h.forecast)) (h.weeks || []).forEach((w, i) => { const k = String(w).slice(0, 10); act[k] = (act[k] || 0) + (Number(h.actual[i]) || 0) }) });
      const expA = Object.keys(act).sort().map(k => Math.round(act[k]));
      const shownA = dc ? dc.data.datasets[0].data.filter(v => v != null) : null;
      const months = document.querySelectorAll('#ovMonth option').length;
      const share = (document.querySelector('#ovSales .os-c:nth-child(2) .os-v') || {}).textContent || '';
      const same = (x, y) => JSON.stringify(x) === JSON.stringify(y);
      const ok = same(exp, shown) && same(expK, shownK) && same(expP, shownP) && same(chartH, [st('understocked'), st('healthy'), st('overstocked')])
        && covSum === R.length && exp.reduce((x, y) => x + y, 0) + R.filter(r => g(r) === 'unclassified').length === R.length
        && same(expA, shownA) && !!mc && months > 0 && /%$/.test(share);
      return { ok, exp, shown, expK, shownK, expP, shownP, chartH, covSum, expA, shownA, months, share, total: R.length };
    });
    if (!check.ok) errors.push(`${C.name}: overview values differ from API ${JSON.stringify(check)}`);
    states.push({ id: `${C.name}__11-values`, text: JSON.stringify(check), calls: [], theme: '', activeNav: '', scrollWidth: 0, canvases: 0 });
    // month selector: pick the earliest month, the sales block must update
    const firstMonth = await p.evaluate(() => { const o = [...document.querySelectorAll('#ovMonth option')]; return o.length ? o[o.length - 1].value : null });
    if (firstMonth) { await p.selectOption('#ovMonth', firstMonth); await settle(); await snap('11-overview-month', false); }
    // every purchasing card opens Replenishment on the same group
    for (const [i, k] of [[1, 'order'], [2, 'covered'], [3, 'none']]) {
      await click(`#acBuckets .ac-b:nth-child(${i})`, 300);
      const t = await p.evaluate(() => [document.querySelector('.nav-item.active').dataset.tab, (document.querySelector('#rpTabs button.active') || {}).dataset?.v]);
      if (t[0] !== 'replenishment' || t[1] !== k) errors.push(`${C.name}: action card ${k} opened ${t}`);
      if (i === 2) await snap('12-ac-to-repl', false);
      await click('.nav-item[data-tab="overview"]', 200);
    }
    await click('#ovKpis button[data-k="under"]');
    const ks = await p.evaluate(() => (document.querySelector('#rpStatus button.active') || {}).dataset?.v);
    if (ks !== 'understocked') errors.push(`${C.name}: understocked KPI opened status ${ks}`);
    await click('.nav-item[data-tab="overview"]', 200);
    const prRow = await p.$('#prList tbody tr');
    if (prRow) { await prRow.click(); await snap('13-priority-row-drawer'); await p.keyboard.press('Escape'); await settle(); }
    await click('.nav-item[data-tab="overview"]', 200);
    await click('#prViewAll');
    const va = await p.evaluate(() => (document.querySelector('#rpTabs button.active') || {}).dataset?.v);
    if (va !== 'order') errors.push(`${C.name}: View all opened tab ${va}`);

    // Replenishment: groups, mandatory rows locked, optional selection, Excel order list
    await click('.nav-item[data-tab="replenishment"]'); await snap('20-repl');
    const rc = await p.evaluate(() => {
      const R = replData.results, cnt = k => R.filter(r => r.bucket === k).length;
      const tabs = Object.fromEntries([...document.querySelectorAll('#rpTabs .cnt')].map(c => [c.dataset.c, parseInt(c.textContent.replace(/\D/g, ''), 10)]));
      const locked = [...document.querySelectorAll('#rpTableWrap tbody input.cb:disabled')].length;
      const mand = R.filter(r => r.mandatory), badMand = mand.filter(r => r.status !== 'understocked' || !((r.reco_po || 0) > 0)).length
        + R.filter(r => r.status === 'understocked' && (r.reco_po || 0) > 0 && !r.mandatory).length;
      return { tabs, exp: { all: R.length, order: cnt('order'), covered: cnt('covered'), none: cnt('none') }, locked, mand: mand.length, badMand, shown: document.querySelectorAll('#rpTableWrap tbody tr').length,
        sys: R.filter(r => r.status === 'healthy' && (r.reco_po || 0) > 0).length, sysShown: parseInt(document.getElementById('rpSysCnt').textContent, 10),
        fz: R.filter(r => r.zero === 'forecast_zero').length };
    });
    if (JSON.stringify(rc.tabs) !== JSON.stringify(rc.exp) || rc.locked !== Math.min(rc.shown, rc.mand) || rc.badMand || rc.sys !== rc.sysShown) errors.push(`${C.name}: replenishment groups ${JSON.stringify(rc)}`);
    states.push({ id: `${C.name}__20-repl-values`, text: JSON.stringify(rc), calls: [], theme: '', activeNav: '', scrollWidth: 0, canvases: 0 });
    await click('#rpTabs button[data-v="all"]'); await snap('21-repl-all', false);
    await click('#rpTabs button[data-v="covered"]'); await snap('21-repl-covered', false);
    await click('#rpSysReco'); await snap('22-repl-sysreco', false); await click('#rpSysReco');
    // tick one optional (Covered / Monitor) row
    const opt = await p.$('#rpTableWrap tbody input.cb:not(:disabled)');
    let manualSku = null;
    if (opt) { manualSku = await opt.evaluate(e => e.closest('tr').querySelector('.p-sku').firstChild.textContent.trim()); await opt.evaluate(e => { e.checked = true; e.dispatchEvent(new Event('change', { bubbles: true })) }); await settle(300);
      const q = await p.$('#rpTableWrap tbody tr input.cb:checked:not(:disabled)'); if (q) { const qi = await q.evaluate(e => !!e.closest('tr').querySelector('.qty-in')); if (qi) { const inp = await q.evaluateHandle(e => e.closest('tr').querySelector('.qty-in')); await inp.fill('25'); await inp.press('Tab'); await settle(300) } } }
    await click('#rpTabs button[data-v="all"]');
    await click('#rpStatus button[data-v="understocked"]'); await snap('23-repl-under', false);
    await click('#rpStatus button[data-v=""]');
    await snap('24-repl-qty', false);
    if (C.mutate) {
      await download('repl-export', 'button[onclick="exportReplenishment()"]');
      const xf = await download('order-list', 'button[onclick="exportReplenishment(true)"]', 'xlsx');
      if (xf) {
        const wbk = XLSX.readFile(xf), rows = XLSX.utils.sheet_to_json(wbk.Sheets['Order list']);
        const exp = await p.evaluate(() => ({ mand: replData.results.filter(r => r.mandatory).length, man: replData.results.filter(r => !r.mandatory && r.sel && r.qty > 0).map(r => r.sku) }));
        const mand = rows.filter(r => r['Included as'] === 'Mandatory').length, man = rows.filter(r => r['Included as'] === 'Manual').map(r => r.SKU);
        const okx = mand === exp.mand && JSON.stringify(man) === JSON.stringify(exp.man) && (!manualSku || man.includes(manualSku)) && rows.every(r => r['Group'] !== undefined);
        if (!okx) errors.push(`${C.name}: order list export ${JSON.stringify({ mand, man, exp, manualSku, n: rows.length })}`);
        states.push({ id: `${C.name}__24-order-list`, text: JSON.stringify({ rows: rows.length, mand, man }), calls: [], theme: '', activeNav: '', scrollWidth: 0, canvases: 0 });
      }
    }
    const row = await p.$('#rpTableWrap tbody tr');
    if (row) {
      await row.click(); await snap('25-repl-detail');
      for (const d of await p.$$('#rpDetail details')) await d.evaluate(e => { e.open = true; });
      await snap('26-repl-detail-open', false); await p.keyboard.press('Escape'); await settle();
    }

    // Product analysis
    await click('.nav-item[data-tab="analysis"]'); await snap('30-analysis-stock');
    const heads = await p.evaluate(() => [...document.querySelectorAll('#invTableWrap thead th')].map(t => t.textContent.replace(/[▲▼]/g, '').trim()));
    const wantH = ['SKU', 'Product', 'Status', 'Coverage', 'Stockout', 'Stock', 'Open PO', 'Forecast/Wk', 'Reco PO', 'Movement', 'Days Inv.', 'Overstock', 'Value', 'Avg/Wk L4W', 'L4W Revenue'];
    if (JSON.stringify(heads) !== JSON.stringify(wantH)) errors.push(`${C.name}: product table columns ${JSON.stringify(heads)}`);
    await click('#invTableWrap thead th:nth-child(4) .th-sort', 300);
    const sorted = await p.evaluate(() => { const v = [...document.querySelectorAll('#invTableWrap tbody tr td:nth-child(4)')].map(t => parseFloat(t.textContent)).filter(x => !isNaN(x)); return v.every((x, i) => i === 0 || v[i - 1] >= x) });
    if (!sorted) errors.push(`${C.name}: coverage header sort`);
    await snap('30-analysis-sorted', false);
    if (C.mutate) await download('inv-export', 'button[onclick^="exportInventory"]');
    const invRow = await p.$('#invTableWrap tbody tr');
    if (invRow) { await invRow.click(); await settle(800); await snap('31-sku-drawer');
      const fv = await p.evaluate(() => ({ main: !!Chart.getChart('sdChart'), dev: !!Chart.getChart('sdDev') || !!document.querySelector('#sdFva .section-meta') }));
      if (!fv.main || !fv.dev) errors.push(`${C.name}: SKU forecast vs actual ${JSON.stringify(fv)}`);
      await p.keyboard.press('Escape'); await settle(); }
    await click('#anSeg button[data-v="forecast"]'); await snap('32-demand-plan');
    await click('#dpMetric button[data-v="sales"]'); await snap('33-plan-sales', false);
    await click('#dpView button[data-v="weekly"]'); await snap('34-plan-weekly');
    await click('#anSeg button[data-v="sales"]'); await snap('35-sales');
    await click('.pill[data-p="l8w"]'); await snap('36-sales-8w', false);

    // PO
    await click('.nav-item[data-tab="po"]'); await settle(800); await snap('40-po');
    const pc = await p.evaluate(() => { const k = document.getElementById('poPeriodSel').value;
      const ls = poData.filter(x => (x.order_date ? String(x.order_date).slice(0, 7) : 'nodate') === k && !['cancelled', 'superseded'].includes(x.status));
      const nums = [...new Set(ls.map(x => x.po_number || '(no PO number)'))];
      const st = n => { const l = poData.filter(x => (x.po_number || '(no PO number)') === n && !['cancelled', 'superseded'].includes(x.status)), o = l.filter(x => x.is_open); return !o.length ? 'done' : o.some(x => x.aging) ? 'aging' : 'open' };
      const exp = [nums.length, nums.filter(n => st(n) === 'done').length, nums.filter(n => st(n) === 'open').length, nums.filter(n => st(n) === 'aging').length];
      const shown = [...document.querySelectorAll('#poPeriodSum .pps-v')].slice(0, 4).map(e => parseInt(e.textContent.replace(/\D/g, ''), 10));
      const months = [...document.querySelectorAll('#poPeriodSel option')].map(o => o.value).filter(v => /^\d{4}-\d{2}$/.test(v));
      const asc = months.every((m, i) => i === 0 || months[i - 1] < m);
      const latest = months[months.length - 1];
      const numOpts = document.querySelectorAll('#poNumSel option').length - 1;
      return { k, latest, asc, exp, shown, numOpts, nums: nums.length } });
    if (pc.k !== pc.latest || !pc.asc || JSON.stringify(pc.exp) !== JSON.stringify(pc.shown) || pc.numOpts < pc.nums) errors.push(`${C.name}: PO period ${JSON.stringify(pc)}`);
    states.push({ id: `${C.name}__40-po-values`, text: JSON.stringify(pc), calls: [], theme: '', activeNav: '', scrollWidth: 0, canvases: 0 });
    const firstPo = await p.evaluate(() => { const o = document.querySelectorAll('#poNumSel option'); return o.length > 1 ? o[1].value : null });
    if (firstPo) {
      await p.selectOption('#poNumSel', firstPo); await settle(400);
      const onlyOne = await p.evaluate(n => [...document.querySelectorAll('#poBody .po-grp b')].map(b => b.textContent).every(t => t === n), firstPo);
      if (!onlyOne) errors.push(`${C.name}: PO number filter shows other POs`);
      await snap('40-po-one', false);
      await p.selectOption('#poNumSel', 'all'); await settle(300);
    }
    await click('#poPrev', 300); await snap('40-po-prev-month', false);
    await p.selectOption('#poPeriodSel', 'all'); await settle(300); await snap('40-po-all-periods', false);
    await click('#poViewSeg button[data-v="sku"]'); await snap('41-po-by-sku', false);
    await click('#poViewSeg button[data-v="po"]');
    await click('#poStatusSeg button[data-v="all"]'); await snap('42-po-all', false);
    if (C.mutate) {
      const recv = await p.$('#poTable input.po-recv, #poBody input[type="number"]');
      if (recv) { await recv.fill('3'); await recv.press('Enter'); await snap('43-po-edit', false); }
    }

    // Upload
    await click('.nav-item[data-tab="upload"]'); await snap('50-upload');
    if (C.mutate) {
      await p.setInputFiles('#heroFile', path.join(FX, 'bad.txt')); await settle();
      await click('#btnHeroUpload'); await snap('51-upload-bad', false);
      await p.setInputFiles('#heroFile', path.join(FX, 'HERO.xlsx')); await settle();
      await click('#btnHeroUpload'); await snap('52-upload-hero', false);
    }

    // Shared: settings modal, theme toggle, keyboard shortcuts, logout
    await click('.nav-item[onclick^="openSettings"]'); await snap('60-settings');
    await p.keyboard.press('Escape'); await settle();
    await click('.nav-item[onclick^="toggleTheme"]'); await snap('61-theme-toggled', false);
    await click('.nav-item[onclick^="toggleTheme"]');
    for (const k of ['1', '2', '3', '4', '5']) { await p.keyboard.press(k); await settle(400); }
    await snap('62-after-shortcuts', false);
    await ctx.close();
  }
  fs.writeFileSync(path.join(OUT, 'states.json'), JSON.stringify(states, null, 1));
  fs.writeFileSync(path.join(OUT, 'errors.json'), JSON.stringify(errors, null, 1));
  console.log(`${states.length} checkpoints, ${errors.length} errors`);
  if (errors.length) console.log(errors.join('\n'));
  await browser.close();
})();
