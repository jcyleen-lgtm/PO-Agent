/* Account settings, theme (light / dark / system), greeting and default period (localStorage). */
const SKEY='po-settings-v4';
const DEFAULTS={name:'',role:'',email:'',theme:'light',period:'l4w'};
function getSettings(){try{return Object.assign({},DEFAULTS,JSON.parse(localStorage.getItem(SKEY)||'{}'))}catch(e){return Object.assign({},DEFAULTS)}}
function saveSettings(s){try{localStorage.setItem(SKEY,JSON.stringify(s))}catch(e){}}
function resolveTheme(t){return t==='system'?(matchMedia('(prefers-color-scheme: dark)').matches?'dark':'light'):t}
function rerenderCharts(){if(lastTrend){renderRevChart(lastTrend,lastAgg);renderSalesChart(lastTrend,lastAgg)}if(lastProj)renderProjChart();if(lastSku)renderSkuChart();if(typeof ovRows!=='undefined'&&ovRows.length)renderOvCharts()}
function setTheme(t){const r=resolveTheme(t);if(document.documentElement.getAttribute('data-theme')!==r){document.documentElement.setAttribute('data-theme',r);rerenderCharts()}}
function toggleTheme(){const s=getSettings();s.theme=document.documentElement.getAttribute('data-theme')==='dark'?'light':'dark';saveSettings(s);setTheme(s.theme)}
try{matchMedia('(prefers-color-scheme: dark)').addEventListener('change',()=>{if(getSettings().theme==='system')setTheme('system')})}catch(e){}
function initials(n){const p=String(n||'').trim().split(/\s+/).filter(Boolean);if(!p.length)return'?';return((p[0][0]||'')+(p.length>1?p[p.length-1][0]:'')).toUpperCase()}
function greetingWord(){const h=new Date().getHours();return h>=5&&h<12?'Good morning':h>=12&&h<17?'Good afternoon':'Good evening'}
function renderGreeting(){const s=getSettings();const g=greetingWord();document.getElementById('greeting').innerHTML=s.name?`${g}, <span class="name">${esc(s.name)}</span>`:g;const av=document.getElementById('headAvatar');av.textContent=initials(s.name);av.title=s.name?`${s.name} · Account settings`:'Account settings'}
setInterval(renderGreeting,60000);
function markSeg(id,v){document.querySelectorAll(`#${id} button`).forEach(b=>b.classList.toggle('active',b.dataset.v===v))}
function setActivePill(p){document.querySelectorAll('.pill').forEach(x=>x.classList.toggle('active',x.dataset.p===p))}
function applySettings(){const s=getSettings();currentPreset=s.period||'l4w';setActivePill(currentPreset);renderGreeting()}
let draft=null,lastFocus=null;
function openSettings(){draft=getSettings();lastFocus=document.activeElement;document.getElementById('setName').value=draft.name;document.getElementById('setRole').value=draft.role;document.getElementById('setEmail').value=draft.email;markSeg('setTheme',draft.theme);markSeg('setPeriod',draft.period);previewSettings();document.getElementById('settingsModal').hidden=false;setTimeout(()=>document.getElementById('setName').focus(),50)}
function previewSettings(){const n=document.getElementById('setName').value.trim(),r=document.getElementById('setRole').value.trim();document.getElementById('setAvatar').textContent=initials(n);document.getElementById('setPreviewName').textContent=n||'Your name';document.getElementById('setPreviewSub').textContent=r||'Set how the dashboard greets you'}
function closeSettings(){const m=document.getElementById('settingsModal');if(m.hidden)return;m.hidden=true;setTheme(getSettings().theme);if(lastFocus&&lastFocus.focus)lastFocus.focus()}
function saveSettingsForm(){const prev=getSettings();const s=Object.assign({},draft,{name:document.getElementById('setName').value.trim(),role:document.getElementById('setRole').value.trim(),email:document.getElementById('setEmail').value.trim()});saveSettings(s);closeSettings();renderGreeting();if(s.period!==prev.period){currentPreset=s.period;setActivePill(s.period);document.getElementById('dateStart').value='';document.getElementById('dateEnd').value='';if(salesLoaded)loadSales()}toast('Settings saved','success')}
document.querySelectorAll('#setTheme button').forEach(b=>b.addEventListener('click',()=>{draft.theme=b.dataset.v;markSeg('setTheme',draft.theme);setTheme(draft.theme)}));
document.querySelectorAll('#setPeriod button').forEach(b=>b.addEventListener('click',()=>{draft.period=b.dataset.v;markSeg('setPeriod',draft.period)}));
