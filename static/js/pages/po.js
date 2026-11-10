/* PO Tracking: PO list by month (PO date) and by PO / by product, filters, received-quantity edits, receive-all,
   Revision R7: month and PO-number filters, period summary, PO status (Completed / On going / Over 2 months). */
let poPeriod=null,poNum='all';
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
document.getElementById('poAgingL').innerHTML=`Open &gt; ${poWarnDays} Days`;document.querySelector('#poStatusSeg [data-v="aging"]').innerHTML=`&gt; ${poWarnDays} days`;renderPoTable()}catch(e){console.error('PO list:',e);document.getElementById('poEmpty').textContent=e.message;document.getElementById('poEmpty').style.display='block'}}
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
async function loadPoPage(){loadPoList();loadPoSummary()}
document.querySelectorAll('#poStatusSeg button').forEach(b=>b.addEventListener('click',()=>{poMode=b.dataset.v;markSeg('poStatusSeg',poMode);poPage=0;renderPoTable()}));
document.querySelectorAll('#poViewSeg button').forEach(b=>b.addEventListener('click',()=>{poView=b.dataset.v;markSeg('poViewSeg',poView);poPage=0;renderPoTable()}));
function poGoPage(p){poPage=p;renderPoTable();document.getElementById('poSection').scrollIntoView({block:'start',behavior:'smooth'})}
const poMonthOf=p=>p.order_date?String(p.order_date).slice(0,7):'nodate';
const PO_DONE=['cancelled','superseded'];
/* Months with POs, oldest first; "No PO date" after them, then "All periods". */
function poPeriods(){const ms=[...new Set(poData.map(poMonthOf).filter(m=>m!=='nodate'))].sort();if(poData.some(p=>!p.order_date))ms.push('nodate');return ms}
const poPeriodLabel=k=>k==='nodate'?'No PO date':k==='all'?'All periods':new Date(k+'-01T00:00:00').toLocaleDateString('en-GB',{month:'long',year:'numeric'});
/* PO status from its lines (cancelled / superseded lines ignored): Completed = nothing open; Over 2 months = open and
   the oldest open line is older than the aging limit (60 days); otherwise On going. */
function poStatusOf(k){const ls=(poByNum.get(k)||[]).filter(p=>!PO_DONE.includes(p.status));if(!ls.length)return'cancelled';const open=ls.filter(p=>p.is_open);if(!open.length)return'done';return open.some(p=>p.aging)?'aging':'open'}
const PO_ST={done:['Completed','badge-green'],open:['On going','badge-blue'],aging:['Over 2 months','badge-red'],cancelled:['Cancelled / superseded','badge-gray']};
const poStBadge=st=>`<span class="badge ${PO_ST[st][1]}">${PO_ST[st][0]}</span>`;
function poNumsIn(k){return[...new Set(poData.filter(p=>k==='all'||poMonthOf(p)===k).map(p=>p.po_number||NO_PO))].sort()}
function syncPoPeriod(){const ms=poPeriods(),months=ms.filter(m=>m!=='nodate');if(!poPeriod||(poPeriod!=='all'&&!ms.includes(poPeriod)))poPeriod=months[months.length-1]||ms[0]||'all';
document.getElementById('poPeriodSel').innerHTML=[...ms,'all'].map(k=>`<option value="${k}"${k===poPeriod?' selected':''}>${poPeriodLabel(k)}${k!=='all'?` (${fmt(poNumsIn(k).length)} PO)`:''}</option>`).join('');
const i=months.indexOf(poPeriod);document.getElementById('poPrev').disabled=i<=0;document.getElementById('poNext').disabled=i<0||i>=months.length-1;
const nums=poNumsIn(poPeriod);if(poNum!=='all'&&!nums.includes(poNum))poNum='all';
document.getElementById('poNumSel').innerHTML=`<option value="all">All POs (${fmt(nums.length)})</option>`+nums.map(n=>`<option value="${esc(n)}"${n===poNum?' selected':''}>${esc(n)} · ${PO_ST[poStatusOf(n)][0]}</option>`).join('')}
function setPoPeriod(k){poPeriod=k;poNum='all';poPage=0;renderPoTable()}
function setPoNum(n){poNum=n;poPage=0;renderPoTable()}
function shiftPoPeriod(d){const months=poPeriods().filter(m=>m!=='nodate'),i=months.indexOf(poPeriod);if(i<0)return;const j=i+d;if(j>=0&&j<months.length)setPoPeriod(months[j])}
const inPoPeriod=p=>(poPeriod==='all'||poMonthOf(p)===poPeriod)&&(poNum==='all'||(p.po_number||NO_PO)===poNum);
/* Summary of the selection: one PO number counts once however many lines it has. */
function renderPoPeriodSum(){const ls=poData.filter(inPoPeriod),valid=ls.filter(p=>!PO_DONE.includes(p.status));
const nums=[...new Set(valid.map(p=>p.po_number||NO_PO))],st=nums.map(poStatusOf),c=k=>st.filter(x=>x===k).length;
const cell=(l,v,s,t,cls='')=>`<div class="pps-c ${cls}"${t?` title="${esc(t)}"`:''}><div class="pps-l">${l}</div><div class="pps-v">${v}</div><div class="pps-s">${s}</div></div>`;
document.getElementById('poPeriodSum').innerHTML=cell('PO count',fmt(nums.length),`${fmt(valid.length)} lines${ls.length>valid.length?` · ${fmt(ls.length-valid.length)} cancelled / superseded not counted`:''}`,'Distinct PO numbers (cancelled and superseded lines excluded)')
+cell('Completed',fmt(c('done')),'all lines finished','No open lines left','pps-ok')
+cell('On going',fmt(c('open')),`${fmt(valid.filter(p=>p.is_open).reduce((a,p)=>a+p.outstanding,0))} units still to arrive`,'Still has lines to arrive, PO date within 60 days','pps-info')
+cell('Over 2 months',fmt(c('aging')),`still open, PO date &gt; ${poWarnDays} days ago`,'Open lines on a PO older than the aging limit','pps-crit')
+cell('Ordered quantity',fmt(valid.reduce((a,p)=>a+p.qty_ordered,0)),`received ${fmt(valid.reduce((a,p)=>a+Math.min(p.qty_received,p.qty_ordered),0))} · no price on PO lines`)}
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
const head=`<tr class="po-grp"><td colspan="9"><div class="po-grp-in"><b>${esc(G.k)}</b><span class="text-dim">${G.date?dfmt(G.date):'no PO date'}</span><span class="po-prog" title="${pct}% received"><i style="width:${pct}%"></i></span><span class="text-dim">${fmt(rec)} / ${fmt(ord)} pcs · ${all.length-openL.length}/${all.length} lines done</span>${poStBadge(poStatusOf(G.k))}${openL.length?`<span class="tag tag-info">${openL.length} open line${openL.length===1?'':'s'}</span>`:''}${aging?`<span class="tag tag-late" title="Open more than ${poWarnDays} days">${fmt(age)} days · ${poMo(age)} months</span>`:age!=null?`<span class="text-dim">${fmt(age)} days</span>`:''}${late?`<span class="tag tag-late">${late} past ETA</span>`:''}${over?`<span class="tag tag-warn">${over} over</span>`:''}${openL.length?`<button class="btn btn-ghost btn-xs" style="margin-left:auto" onclick="receiveAllPo('${jsq(G.k)}')" ${G.k===NO_PO?'disabled':''}>Mark all received</button>`:''}</div></td></tr>`;
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

