/* PO Tracking: PO list by month (PO date) and by PO / by product, filters, received-quantity edits, receive-all,
   and the purchasing chart (forecast vs actual sales vs ordered quantity). Revision R7 adds the period filter and chart. */
let poPeriod=null,poChartState=null,poFcModeV='eta';
let poData=[],poMode='all',poView='po',poPage=0,poWarnDays=60,poOpenSku=new Set(),poByNum=new Map();
const PO_GROUPS_PER_PAGE=12,NO_PO='(no PO number)';
const poMo=d=>d==null?'--':(d/30.4).toFixed(1);
const todayIso=()=>{const t=new Date();t.setMinutes(t.getMinutes()-t.getTimezoneOffset());return t.toISOString().slice(0,10)};
const jsq=v=>String(v).replace(/\\/g,'\\\\').replace(/'/g,"\\'");
const PO_COLORS={pending:['var(--amber)','var(--amber-bg)'],partial:['var(--link)','var(--blue-bg)'],received:['var(--green)','var(--green-bg)'],completed:['var(--green)','var(--green-bg)'],closed:['var(--amber)','var(--amber-bg)'],cancelled:['var(--red)','var(--red-bg)'],superseded:['var(--text3)','var(--surface2)']};
function poBadge(s){const c=PO_COLORS[s]||['var(--text3)','var(--surface2)'];return`<span class="badge" style="color:${c[0]};background:${c[1]};text-transform:capitalize" ${s==='superseded'?'title="Replaced by a later PO upload (not a cancellation)"':s==='closed'?'title="Closed short: the rest will not arrive"':''}>${s}</span>`}
function poIndex(){poByNum=new Map();poData.forEach(p=>{const k=p.po_number||NO_PO;if(!poByNum.has(k))poByNum.set(k,[]);poByNum.get(k).push(p)})}
function poRecalc(p){const t=todayIso();p.remaining=p.qty_ordered-p.qty_received;p.is_open=['pending','partial'].includes(p.status)&&p.remaining>0;p.outstanding=p.is_open?p.remaining:0;
p.overdue=!!(p.is_open&&p.eta&&p.eta<t);p.days_overdue=p.overdue?Math.round((new Date(t)-new Date(p.eta))/864e5):null;
const end=(!p.is_open&&p.received_date)?p.received_date:t;p.age_days=p.order_date?Math.round((new Date(end)-new Date(p.order_date))/864e5):null;p.aging=!!(p.is_open&&p.age_days!=null&&p.age_days>poWarnDays)}
function poState(p){const rem=p.remaining;
if(p.status==='cancelled')return'<span class="badge badge-gray">Cancelled</span>';
if(rem<0)return`<span class="badge badge-yellow" title="${fmt(-rem)} units more than ordered">Over +${fmt(-rem)}</span>`;
if(rem===0)return'<span class="badge badge-green">Complete</span>';
if(!p.is_open)return`<span class="badge badge-yellow" title="Closed: the remaining ${fmt(rem)} units will not arrive">Closed short −${fmt(rem)}</span>`;
if(!p.qty_received)return'<span class="badge badge-gray">Not received</span>';
return`<span class="badge badge-blue" title="${fmt(rem)} units still to arrive">Partial −${fmt(rem)}</span>`}
function poFilter(p){switch(poMode){case'open':return p.is_open;case'overdue':return p.overdue;case'aging':return p.aging;case'diff':return p.status!=='cancelled'&&p.qty_received>0&&p.remaining!==0;case'done':return!p.is_open&&p.status!=='cancelled';default:return true}}
function poAge(d,warn){if(d==null)return'<span class="text-dim">--</span>';return`<div class="ob-date ${warn?'ob-late':'ob-ok'}" ${warn?`title="Open more than ${poWarnDays} days since the PO date"`:''}>${fmt(d)} d${warn?` <span class="tag tag-late">&gt; ${poWarnDays} d</span>`:''}</div><div class="ob-sub ob-none">${poMo(d)} months</div>`}
function poEta(p){if(!p.is_open)return`<span class="text-dim">${p.eta?dfmt(p.eta):'--'}</span>`;if(p.eta_source==='unknown')return'<span class="tag tag-warn" title="No ETA and no PO date: not counted as incoming">Unknown</span>';return`${dfmt(p.eta)}${p.eta_source==='estimated'?' <span class="tag tag-info" title="PO date + default lead time">est.</span>':''}${p.overdue?`<div class="ob-sub ob-late">${p.days_overdue} days late</div>`:''}`}
function poRecv(p){if(p.status==='cancelled')return fmt(p.qty_received);return`<input class="qty-in" type="number" min="0" step="1" value="${p.qty_received}" onchange="setPoReceived(${p.id},this)" onclick="event.stopPropagation()" aria-label="Qty received for ${esc(p.sku)}">`}
function poRecvDate(p){if(!p.qty_received)return'<span class="text-dim">--</span>';return`<input type="date" class="date-mini" value="${p.received_date||''}" max="${todayIso()}" onchange="setPoRecvDate(${p.id},this.value)" onclick="event.stopPropagation()" aria-label="Receipt date">`}
function poAct(p){if(p.is_open)return p.qty_received>0?`<button class="mini-btn" title="The rest will not arrive: close this line (stops counting it as incoming)" onclick="setPoStatus(${p.id},'closed')">Close short</button>`:`<button class="mini-btn" style="color:var(--text3)" title="Nothing will arrive: cancel this line" onclick="setPoStatus(${p.id},'cancelled')">Cancel</button>`;
if(p.status==='closed'||p.status==='cancelled')return`<button class="mini-btn" onclick="setPoStatus(${p.id},'reopen')">Reopen</button>`;return''}
function skuBtn(sku){return`<button class="link-btn" style="font-size:14px;margin:0" onclick="event.stopPropagation();openSkuDrawer('${jsq(sku)}')">${esc(sku||'-')}</button>`}
async function loadPoList(){try{const r=await fetch('/api/po/list?status=all');const d=await readJson(r);if(!r.ok)throw new Error(d.detail||'PO list failed');poData=d.data||[];poWarnDays=d.aging_warn_days||60;poIndex();
document.getElementById('poAgingL').innerHTML=`Open &gt; ${poWarnDays} Days`;document.querySelector('#poStatusSeg [data-v="aging"]').innerHTML=`&gt; ${poWarnDays} days`;renderPoTable();if(poChartState)renderPoFc()}catch(e){console.error('PO list:',e);document.getElementById('poEmpty').textContent=e.message;document.getElementById('poEmpty').style.display='block'}}
async function loadPoSummary(){try{const r=await fetch('/api/po/summary');const d=await readJson(r);if(!r.ok)throw new Error(d.detail||'PO summary failed');
countUp('poOpen',d.open_po_numbers??d.open_pos??0,fmt);document.getElementById('poOpenSub').textContent=`${fmt(d.open_pos||0)} open lines`;
countUp('poIncoming',d.outstanding_qty||0,fmt);countUp('poOverdue',d.overdue||0,fmt);countUp('poAging',d.aging||0,fmt);
document.getElementById('poIncomingSub').textContent=`units still to arrive${d.eta_unknown?` · ${d.eta_unknown} lines without ETA`:''}`;
const lt=d.lead_time||{},defD=(d.default_lead_time_weeks||8)*7,pl=document.getElementById('poLead');pl.classList.remove('skel');
if(lt.available){pl.textContent=`${Math.round(lt.median_days??lt.avg_days)} days`;pl.style.color=(lt.p90_days||0)>defD?'var(--amber)':'';document.getElementById('poLeadSub').textContent=`median · p90 ${lt.p90_days} d · plan assumes ${defD} d`}
else{pl.textContent=`${d.default_lead_time_weeks} weeks`;pl.style.color='';document.getElementById('poLeadSub').textContent='default assumption (no receipts yet)'}
const f=d.fulfillment||{},so=d.stockout_rate||{};const card=(on,l,v,sub)=>`<div class="perf-card ${on?'on':''}"><div class="p-l">${l}</div><div class="p-v">${on?v:'<span class="tag tag-mute">Not enough data</span>'}</div><div class="p-s">${sub}</div></div>`;
const sup=(lt.by_supplier||[]).filter(x=>x.std_days!=null);
document.getElementById('perfGrid').innerHTML=card(f.available,'PO Fill Rate',f.rate_pct+'%',f.available?`Received ÷ ordered (over-delivery capped), ${fmt(f.po_lines)} due lines · ${fmt(f.short_lines||0)} short, ${fmt(f.over_lines||0)} over`:'Needs received quantities on PO lines that are due.')
+card(lt.available,'Actual Lead Time',`${lt.avg_days} days`,lt.available?`Average PO date → receipt date, ${fmt(lt.deliveries)} received lines · median ${lt.median_days} d`:'Needs PO date and receipt date on the same line.')
+card(sup.length>0,'Lead Time Variance',sup.length?sup.map(x=>`±${x.std_days} days`).slice(0,1).join(''):'',sup.length?`Standard deviation, ${esc(sup[0].supplier)}`:'Needs at least 3 received lines.')
+card(so.available,'Stockout Rate',so.rate_pct+'%',so.available?`SKU-weeks with stock ≤ 0, best sellers, last ${so.weeks} weeks`:`Needs ≥ ${so.required||4} weekly stock snapshots (have ${so.snapshots||0}).`)}catch(e){console.error('PO summary:',e)}}
async function loadPoPage(){loadPoList();loadPoSummary();loadPoFc()}
document.querySelectorAll('#poStatusSeg button').forEach(b=>b.addEventListener('click',()=>{poMode=b.dataset.v;markSeg('poStatusSeg',poMode);poPage=0;renderPoTable()}));
document.querySelectorAll('#poViewSeg button').forEach(b=>b.addEventListener('click',()=>{poView=b.dataset.v;markSeg('poViewSeg',poView);poPage=0;renderPoTable()}));
function poGoPage(p){poPage=p;renderPoTable();document.getElementById('poSection').scrollIntoView({block:'start',behavior:'smooth'})}
const poMonthOf=p=>p.order_date?String(p.order_date).slice(0,7):'nodate';
function poPeriods(){const ms=[...new Set(poData.map(poMonthOf).filter(m=>m!=='nodate'))].sort().reverse();if(poData.some(p=>!p.order_date))ms.push('nodate');return ms}
const poPeriodLabel=k=>k==='nodate'?'No PO date':k==='all'?'All periods':new Date(k+'-01T00:00:00').toLocaleDateString('en-GB',{month:'long',year:'numeric'});
function syncPoPeriod(){const ms=poPeriods();if(!poPeriod||(poPeriod!=='all'&&!ms.includes(poPeriod)))poPeriod=ms[0]||'all';const sel=document.getElementById('poPeriodSel');
sel.innerHTML=[...ms,'all'].map(k=>`<option value="${k}"${k===poPeriod?' selected':''}>${poPeriodLabel(k)}${k!=='all'?` (${fmt(new Set(poData.filter(p=>poMonthOf(p)===k).map(p=>p.po_number||NO_PO)).size)} PO)`:''}</option>`).join('');
const months=ms.filter(m=>m!=='nodate'),i=months.indexOf(poPeriod);document.getElementById('poPrev').disabled=i<0||i>=months.length-1;document.getElementById('poNext').disabled=i<=0}
function setPoPeriod(k){poPeriod=k;poPage=0;renderPoTable()}
function shiftPoPeriod(d){const months=poPeriods().filter(m=>m!=='nodate'),i=months.indexOf(poPeriod);if(i<0)return;const j=i+d;if(j>=0&&j<months.length)setPoPeriod(months[j])}
const inPoPeriod=p=>poPeriod==='all'||poMonthOf(p)===poPeriod;
/* Summary of the selected period: one PO number counts once however many lines it has. */
function renderPoPeriodSum(){const ls=poData.filter(inPoPeriod),valid=ls.filter(p=>!['cancelled','superseded'].includes(p.status));
const nums=new Set(valid.map(p=>p.po_number||NO_PO)),pend=valid.filter(p=>p.is_open),pendN=new Set(pend.map(p=>p.po_number||NO_PO));
const cell=(l,v,s,t)=>`<div class="pps-c"${t?` title="${esc(t)}"`:''}><div class="pps-l">${l}</div><div class="pps-v">${v}</div><div class="pps-s">${s}</div></div>`;
document.getElementById('poPeriodSum').innerHTML=cell('PO count',fmt(nums.size),`${fmt(valid.length)} lines${ls.length>valid.length?` · ${fmt(ls.length-valid.length)} cancelled / superseded not counted`:''}`,'Distinct PO numbers with a PO date in the period (cancelled and superseded lines excluded)')
+cell('Ordered quantity',fmt(valid.reduce((a,p)=>a+p.qty_ordered,0)),`received ${fmt(valid.reduce((a,p)=>a+Math.min(p.qty_received,p.qty_ordered),0))}`)
+cell('Pending',`${fmt(pendN.size)} <small>PO</small>`,`${fmt(pend.length)} open lines · ${fmt(pend.reduce((a,p)=>a+p.outstanding,0))} units to arrive`,'Lines with status pending or partial and less received than ordered')
+cell('PO value','<span class="text-dim">n/a</span>','PO lines carry no price','The PO list upload has no price column, so the PO value cannot be shown')}
function renderPoTable(){const empty=document.getElementById('poEmpty'),table=document.getElementById('poTable');
if(!poData.length){empty.style.display='block';table.style.display='none';document.getElementById('poPaging').innerHTML='';document.getElementById('poPeriodSum').innerHTML='';return}empty.style.display='none';table.style.display='table';
syncPoPeriod();renderPoPeriodSum();
const q=(document.getElementById('poSearch').value||'').toLowerCase().trim();let rows=poData.filter(p=>inPoPeriod(p)&&poFilter(p));
if(q)rows=rows.filter(p=>[p.sku,p.product,p.po_number].some(x=>String(x||'').toLowerCase().includes(q)));
poView==='po'?renderPoByPo(rows):renderPoBySku(rows)}
function renderPoByPo(rows){
document.getElementById('poHead').innerHTML=`<tr><th>SKU</th><th>Product</th><th class="r">Ordered</th><th class="r" title="Type the quantity that actually arrived">Received</th><th>Status</th><th title="Expected date; est. = PO date + default lead time">ETA</th><th title="Date the goods arrived (editable, used for actual lead time)">Received on</th><th title="Days since PO date (until receipt for finished lines)">PO age</th><th></th></tr>`;
const g=new Map();rows.forEach(p=>{const k=p.po_number||NO_PO;if(!g.has(k))g.set(k,[]);g.get(k).push(p)});
const groups=[...g.entries()].map(([k,ls])=>{const all=poByNum.get(k)||ls;return{k,ls,all,open:all.some(x=>x.is_open),date:all.map(x=>x.order_date).filter(Boolean).sort()[0]||''}}).sort((a,b)=>(b.open-a.open)||b.date.localeCompare(a.date));
const st=poPage*PO_GROUPS_PER_PAGE,pg=groups.slice(st,st+PO_GROUPS_PER_PAGE);
document.getElementById('poBody').innerHTML=pg.length?pg.map(G=>{const all=G.all.filter(x=>x.status!=='cancelled'),ord=all.reduce((s,x)=>s+x.qty_ordered,0),rec=all.reduce((s,x)=>s+Math.min(x.qty_received,x.qty_ordered),0),pct=ord?Math.round(rec/ord*100):0;
const openL=all.filter(x=>x.is_open),age=openL.length?Math.max(...openL.map(x=>x.age_days||0)):null,aging=openL.some(x=>x.aging),late=openL.filter(x=>x.overdue).length,over=all.filter(x=>x.remaining<0).length;
const head=`<tr class="po-grp"><td colspan="9"><div class="po-grp-in"><b>${esc(G.k)}</b><span class="text-dim">${G.date?dfmt(G.date):'no PO date'}</span><span class="po-prog" title="${pct}% received"><i style="width:${pct}%"></i></span><span class="text-dim">${fmt(rec)} / ${fmt(ord)} pcs · ${all.length-openL.length}/${all.length} lines done</span>${openL.length?`<span class="tag tag-info">${openL.length} open</span>`:'<span class="badge badge-green">Complete</span>'}${aging?`<span class="tag tag-late" title="Open more than ${poWarnDays} days">${fmt(age)} days · ${poMo(age)} months</span>`:age!=null?`<span class="text-dim">${fmt(age)} days</span>`:''}${late?`<span class="tag tag-late">${late} past ETA</span>`:''}${over?`<span class="tag tag-warn">${over} over</span>`:''}${openL.length?`<button class="btn btn-ghost btn-xs" style="margin-left:auto" onclick="receiveAllPo('${jsq(G.k)}')" ${G.k===NO_PO?'disabled':''}>Mark all received</button>`:''}</div></td></tr>`;
return head+G.ls.map(p=>`<tr><td class="t-strong">${skuBtn(p.sku)}</td><td><div class="trunc" style="max-width:240px" title="${esc(p.product||'')}">${esc(p.product||'-')}</div></td><td class="r t-strong">${fmt(p.qty_ordered)}</td><td class="r">${poRecv(p)}</td><td>${poState(p)}</td><td>${poEta(p)}</td><td>${poRecvDate(p)}</td><td>${poAge(p.age_days,p.aging)}</td><td>${poAct(p)}</td></tr>`).join('')}).join(''):'<tr><td colspan="9"><div class="empty-state">No PO lines match.</div></td></tr>';
renderPaging('poPaging',poPage,groups.length,'poGoPage','POs',PO_GROUPS_PER_PAGE)}
function renderPoBySku(rows){
document.getElementById('poHead').innerHTML=`<tr><th style="width:28px"></th><th>SKU</th><th>Product</th><th>PO lines</th><th class="r" title="Open PO lines for this SKU">Total PO Aktif</th><th class="r" title="Units still to arrive (ordered − received) on open lines">Total QTY</th><th title="Oldest open line, days since PO date">PO Age (days)</th><th class="r">PO Age (months)</th></tr>`;
const m=new Map();rows.forEach(p=>{if(!m.has(p.sku))m.set(p.sku,[]);m.get(p.sku).push(p)});
const items=[...m.entries()].map(([sku,ls])=>{const open=ls.filter(x=>x.is_open);return{sku,name:ls[0].product,ls,openN:open.length,qty:open.reduce((s,x)=>s+x.outstanding,0),age:open.length?Math.max(...open.map(x=>x.age_days||0)):null,aging:open.some(x=>x.aging)}}).sort((a,b)=>(b.age??-1)-(a.age??-1)||b.qty-a.qty);
const st=poPage*PAGE_SIZE,pg=items.slice(st,st+PAGE_SIZE);
document.getElementById('poBody').innerHTML=pg.length?pg.map(it=>{const open=poOpenSku.has(it.sku),k=jsq(it.sku);
const mini=it.ls.slice(0,3).map(x=>`${esc(x.po_number||'-')} · ${x.order_date?dfmt(x.order_date):'--'} · ${fmt(x.qty_ordered)} pcs`).join('<br>')+(it.ls.length>3?`<br>+${it.ls.length-3} more`:'');
let h=`<tr class="po-sku-row ${open?'open':''}" onclick="togglePoSku('${k}')"><td><span class="chev">&#8250;</span></td><td class="t-strong">${skuBtn(it.sku)}</td><td><div class="trunc" style="max-width:240px" title="${esc(it.name||'')}">${esc(it.name||'-')}</div></td><td><div class="po-mini">${mini}</div></td><td class="r t-strong">${it.openN||'--'}</td><td class="r t-strong">${it.qty?fmt(it.qty):'--'}</td><td>${it.age!=null?poAge(it.age,it.aging):'<span class="text-dim">--</span>'}</td><td class="r ${it.aging?'ob-late':''}" style="font-weight:600">${it.age!=null?poMo(it.age):'--'}</td></tr>`;
if(open)h+=`<tr class="po-sub-wrap"><td colspan="8"><table class="po-sub"><thead><tr><th>PO number</th><th>PO date</th><th class="r">Ordered</th><th class="r">Received</th><th>Status</th><th>ETA</th><th>Received on</th><th>PO age</th><th></th></tr></thead><tbody>${it.ls.map(p=>`<tr><td class="t-strong">${esc(p.po_number||'-')}</td><td>${p.order_date?dfmt(p.order_date):'--'}</td><td class="r">${fmt(p.qty_ordered)}</td><td class="r">${poRecv(p)}</td><td>${poState(p)}</td><td>${poEta(p)}</td><td>${poRecvDate(p)}</td><td>${poAge(p.age_days,p.aging)}</td><td>${poAct(p)}</td></tr>`).join('')}</tbody></table></td></tr>`;return h}).join(''):'<tr><td colspan="8"><div class="empty-state">No PO lines match.</div></td></tr>';
renderPaging('poPaging',poPage,items.length,'poGoPage','products')}
function togglePoSku(sku){poOpenSku.has(sku)?poOpenSku.delete(sku):poOpenSku.add(sku);renderPoTable()}
async function patchPo(id,body,okMsg){try{const r=await fetch('/api/po/'+id,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});const d=await readJson(r);if(!r.ok)throw new Error(d.detail||'Update failed');
const p=poData.find(x=>x.id===id);if(p){p.qty_received=d.qty_received;p.status=d.line_status;p.received_date=d.received_date;poRecalc(p)}
invSummaryCache=null;replData=null;renderPoTable();loadPoSummary();if(okMsg&&p)toast(okMsg(p),'success')}catch(e){toast(e.message,'error');renderPoTable()}}
function setPoReceived(id,el){const v=Number(el.value);if(!Number.isInteger(v)||v<0){toast('Received must be a whole number ≥ 0','error');renderPoTable();return}
patchPo(id,{qty_received:v},p=>`${p.sku}: ${fmt(p.qty_received)} of ${fmt(p.qty_ordered)} received${p.remaining<0?` (over +${fmt(-p.remaining)})`:p.remaining>0?` (${fmt(p.remaining)} to come)`:''}`)}
function setPoRecvDate(id,v){if(!v)return;patchPo(id,{received_date:v},p=>`${p.sku}: receipt date ${dfmt(v)}`)}
function setPoStatus(id,s){if((s==='closed'||s==='cancelled')&&!confirm(s==='closed'?'Close this line short? The rest will no longer count as incoming stock.':'Cancel this line? Nothing will count as incoming for it.'))return;patchPo(id,{status:s},p=>`${p.sku}: ${s==='reopen'?'reopened':s==='closed'?'closed short':s}`)}
async function receiveAllPo(po){const n=(poByNum.get(po)||[]).filter(x=>x.is_open).length;if(!confirm(`Mark all ${n} open lines of ${po} as fully received today?\n(Edit a line afterwards if something was short.)`))return;
try{const r=await fetch('/api/po/receive-all',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({po_number:po})});const d=await readJson(r);if(!r.ok)throw new Error(d.detail||'Update failed');toast(`${po}: ${d.lines} lines marked received`,'success');invSummaryCache=null;replData=null;loadPoPage()}catch(e){toast(e.message,'error')}}

/* Forecast Demand vs Actual Sales vs Actual Ordered Quantity (weekly). Forecast and actual from /api/forecast/plan
   (holdout weeks + 12-week horizon); ordered quantity from the PO lines already loaded on this page.
   By ETA: expected_date week (lines without an expected date are reported, not placed). By PO date: order_date week. */
document.querySelectorAll('#poFcMode button').forEach(b=>b.addEventListener('click',()=>{poFcModeV=b.dataset.v;markSeg('poFcMode',poFcModeV);renderPoFc()}));
const mondayOf=iso=>{const d=new Date(String(iso).slice(0,10)+'T00:00:00');const k=(d.getDay()+6)%7;d.setDate(d.getDate()-k);return`${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`};
let poFcChartObj=null,poFcSeq=0;
async function loadPoFc(){const seq=++poFcSeq;try{const p=await fetchPlan();if(seq!==poFcSeq)return;poChartState={plan:p};document.getElementById('poFcSkuList').innerHTML=(p.results||[]).map(x=>`<option value="${esc(x.sku)}">${esc(x.product_name||'')}</option>`).join('');renderPoFc()}catch(e){if(seq!==poFcSeq)return;poChartState=null;document.getElementById('poFcWrap').innerHTML=`<div class="state">${esc(e.message)}</div>`}}
function renderPoFc(){if(!poChartState||typeof Chart==='undefined')return;const plan=poChartState.plan,wrap=document.getElementById('poFcWrap');
const raw=(document.getElementById('poFcSku').value||'').trim(),one=raw&&(plan.results||[]).some(x=>x.sku===raw)?raw:'';
const skus=one?new Set([one]):new Set((plan.results||[]).map(x=>x.sku));const s=demandSeries(plan,skus);
if(!s.weeks.length){wrap.innerHTML='<div class="state">No forecast yet. Run Refresh forecast on the Data page.</div>';return}
const wset=new Set(s.weeks),ord={};let noDate=0,noDateL=0,before=0,after=0;const first=s.weeks[0],last=s.weeks[s.weeks.length-1];
poData.filter(p=>skus.has(p.sku)&&!['cancelled','superseded'].includes(p.status)).forEach(p=>{const d=poFcModeV==='eta'?p.expected_date:p.order_date;if(!d){noDate+=p.qty_ordered;noDateL++;return}const w=mondayOf(d);if(wset.has(w))ord[w]=(ord[w]||0)+p.qty_ordered;else if(w<first)before+=p.qty_ordered;else after+=p.qty_ordered});
if(!document.getElementById('poFcChart'))wrap.innerHTML='<canvas id="poFcChart" aria-label="Forecast, actual sales and ordered quantity per week" role="img"></canvas>';
const ct=getCT(),c1=cv('--chart-1'),c2=cv('--chart-2'),am=cv('--amber');
const act=s.weeks.map(w=>s.past.has(w)?Math.round(s.actual[w]||0):null),fc=s.weeks.map(w=>Math.round((s.past.has(w)?s.holdoutFc[w]:s.forecast[w])||0)),od=s.weeks.map(w=>ord[w]||0);
if(poFcChartObj)poFcChartObj.destroy();const firstFut=s.weeks.findIndex(w=>!s.past.has(w));
poFcChartObj=new Chart(document.getElementById('poFcChart'),{data:{labels:s.weeks.map(wkLabel),datasets:[
 {type:'bar',label:'Actual sales',data:act,backgroundColor:hexA(c1,.8),borderRadius:2,maxBarThickness:16,order:3},
 {type:'bar',label:poFcModeV==='eta'?'Ordered (by ETA week)':'Ordered (by PO date week)',data:od,backgroundColor:hexA(am,.75),borderRadius:2,maxBarThickness:16,order:2},
 {type:'line',label:'Forecast demand',data:fc,borderColor:c2,borderDash:[6,4],borderWidth:2,pointRadius:2,pointBackgroundColor:c2,tension:.2,fill:false,order:1}]},
 options:{responsive:true,maintainAspectRatio:false,interaction:{mode:'index',intersect:false},plugins:{legend:{display:false},tooltip:{...chartTip(),callbacks:{title:c=>{const w=s.weeks[c[0].dataIndex];return`Week of ${dfmt(w)}${s.past.has(w)?' · past':' · forecast horizon'}`},label:c=>c.parsed.y==null?null:`${c.dataset.label}: ${fmt(c.parsed.y)} units`,afterBody:c=>{const i=c[0].dataIndex,o=od[i],f=fc[i];return o?`Ordered − forecast: ${o-f>=0?'+':''}${fmt(o-f)} units`:''}}}},
 scales:{x:{grid:{display:false},border:{color:ct.grid},ticks:{color:ct.tick,font:{...ct.font,size:11},maxRotation:0,autoSkipPadding:8}},y:{min:0,grid:{color:ct.grid},border:{display:false},ticks:{color:ct.tick,font:ct.font,callback:v=>fmt(v)}}}},
 plugins:[{id:'split',beforeDraw(ch){if(firstFut<1)return;const x=ch.scales.x,{top,bottom,right}=ch.chartArea,px=(x.getPixelForValue(firstFut-1)+x.getPixelForValue(firstFut))/2,g=ch.ctx;g.save();g.fillStyle=hexA(c2,.06);g.fillRect(px,top,right-px,bottom-top);g.fillStyle=cv('--text3');g.font='11px Inter';g.fillText('Forecast horizon',px+6,top+12);g.restore()}}]});
document.getElementById('poFcOrdL').textContent=poFcModeV==='eta'?'Ordered (by ETA week)':'Ordered (by PO date week)';
const name=one?((plan.results||[]).find(x=>x.sku===one)||{}).product_name:'';
document.getElementById('poFcMeta').textContent=`${one?`${one} · ${name||''}`:`All ${fmt(skus.size)} best-seller SKUs`} · ${dfmt(first)} – ${dfmt(last)} · ordered quantity grouped by ${poFcModeV==='eta'?'expected arrival (ETA) week':'PO date week'}`;
const dq=[];if(noDate)dq.push(`<span class="tag tag-warn">${fmt(noDate)} units</span> on ${fmt(noDateL)} line${noDateL===1?'':'s'} without ${poFcModeV==='eta'?'an expected arrival date':'a PO date'}: not placed in any week`);
if(before||after)dq.push(`${fmt(before+after)} units ${poFcModeV==='eta'?'arrive':'were ordered'} outside the chart window (${before?fmt(before)+' before':''}${before&&after?', ':''}${after?fmt(after)+' after':''})`);
if(raw&&!one)dq.push(`<span class="tag tag-warn">SKU "${esc(raw)}" is not a forecasted best seller</span>: showing all best sellers`);
document.getElementById('poFcDq').innerHTML=dq.length?'Data quality: '+dq.join(' · '):'All ordered quantity in the window has '+(poFcModeV==='eta'?'an expected arrival date':'a PO date')+'.'}
