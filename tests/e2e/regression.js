// Drives every Stockwise page and records, per checkpoint: screenshot, visible text, API calls, layout width.
// Started by run_regression.py:  node regression.js <baseUrl> <outDir> <fixtureDir>
// Deterministic on purpose: fixed browser clock, Chart.js served locally with animations off, same flow every run.
const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');

const [BASE, OUT, FX] = process.argv.slice(2);
const CHART = path.join(path.dirname(require.resolve('chart.js')), 'chart.umd.js');
const CHART_SRC = fs.readFileSync(CHART, 'utf8') + '\n;Chart.defaults.animation=false;';
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
    const download = async (name, sel) => {
      const el = await p.$(sel); if (!el) { errors.push(`${C.name}: missing ${sel}`); return; }
      const [d] = await Promise.all([p.waitForEvent('download'), el.click()]);
      fs.copyFileSync(await d.path(), path.join(OUT, `${C.name}__${name}.csv`));
      await settle();
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
    // Action Center: every bucket, and the values shown must equal what /api/replenishment returns
    for (const i of [2, 3, 4, 1]) await click(`#acBuckets .ac-b:nth-child(${i})`, 300);
    await snap('11-action-center', false);
    const check = await p.evaluate(async () => {
      const d = await (await fetch('/api/replenishment?product_type=import')).json();
      const R = d.results || [];
      const b = r => { const x = r.order_by_days, f = r.order_by_is_estimate === false && x != null;
        return f && x <= 0 ? 'now' : f && x >= 1 && x <= 14 ? 'soon' : (r.status === 'overstocked' || ((r.reco_po || 0) === 0 && (r.confirmed_incoming || 0) > 0)) ? 'hold' : 'none'; };
      const exp = ['now', 'soon', 'hold', 'none'].map(k => R.filter(r => b(r) === k).length);
      const num = t => parseInt(String(t).replace(/[^0-9]/g, ''), 10);
      const shown = [...document.querySelectorAll('#acBuckets .ac-b-v')].map(e => num(e.firstChild.textContent));
      const st = k => R.filter(r => (r.status || null) === k).length;
      const expH = [st('understocked'), st('healthy'), st('overstocked'), st(null), R.filter(r => r.out_of_stock).length];
      const shownH = [...document.querySelectorAll('#ovHealthLegend .hl-v')].map(e => num(e.firstChild.textContent));
      const same = (a, c) => JSON.stringify(a) === JSON.stringify(c);
      return { ok: same(exp, shown) && same(expH, shownH) && exp.reduce((a, c) => a + c, 0) === R.length, exp, shown, expH, shownH, total: R.length };
    });
    if (!check.ok) errors.push(`${C.name}: overview values differ from API ${JSON.stringify(check)}`);
    states.push({ id: `${C.name}__11-values`, text: JSON.stringify(check), calls: [], theme: '', activeNav: '', scrollWidth: 0, canvases: 0 });
    await click('#ovHealthLegend button.hl-item'); await snap('12-health-to-repl', false);
    await click('.nav-item[data-tab="overview"]');
    const acRow = await p.$('#acList tbody tr');
    if (acRow) { await acRow.click(); await snap('13-ac-row-drawer'); await p.keyboard.press('Escape'); await settle(); }

    // Replenishment
    await click('.nav-item[data-tab="replenishment"]'); await snap('20-repl');
    await click('#rpTabs button[data-v="all"]'); await snap('21-repl-all', false);
    await click('#rpTabs button[data-v="hold"]'); await snap('22-repl-hold', false);
    await click('#rpTabs button[data-v="all"]');
    await click('#rpStatus button[data-v="understocked"]'); await snap('23-repl-under', false);
    await click('#rpStatus button[data-v=""]');
    const qty = await p.$('#rpTableWrap tbody tr:nth-child(2) .qty-in');
    if (qty) { await qty.fill('999'); await qty.press('Tab'); }
    await snap('24-repl-qty', false);
    if (C.mutate) await download('repl-export', 'button[onclick^="exportReplenishment"]');
    const row = await p.$('#rpTableWrap tbody tr');
    if (row) {
      await row.click(); await snap('25-repl-detail');
      for (const d of await p.$$('#rpDetail details')) await d.evaluate(e => { e.open = true; });
      await snap('26-repl-detail-open', false); await p.keyboard.press('Escape'); await settle();
    }

    // Product analysis
    await click('.nav-item[data-tab="analysis"]'); await snap('30-analysis-stock');
    if (C.mutate) await download('inv-export', 'button[onclick^="exportInventory"]');
    const invRow = await p.$('#invTableWrap tbody tr');
    if (invRow) { await invRow.click(); await snap('31-sku-drawer'); await p.keyboard.press('Escape'); await settle(); }
    await click('#anSeg button[data-v="forecast"]'); await snap('32-demand-plan');
    await click('#dpMetric button[data-v="sales"]'); await snap('33-plan-sales', false);
    await click('#dpView button[data-v="weekly"]'); await snap('34-plan-weekly');
    await click('#anSeg button[data-v="sales"]'); await snap('35-sales');
    await click('.pill[data-p="l8w"]'); await snap('36-sales-8w', false);

    // PO
    await click('.nav-item[data-tab="po"]'); await snap('40-po');
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
