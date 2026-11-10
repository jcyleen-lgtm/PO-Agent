/* Account settings, theme (light / dark / system), greeting and default period (localStorage). */
const SKEY='po-settings-v4';
const DEFAULTS={name:'',photo:'',theme:'light',period:'l4w'};
function getSettings(){try{return Object.assign({},DEFAULTS,JSON.parse(localStorage.getItem(SKEY)||'{}'))}catch(e){return Object.assign({},DEFAULTS)}}
function saveSettings(s){try{localStorage.setItem(SKEY,JSON.stringify(s));return true}catch(e){return false}}
function resolveTheme(t){return t==='system'?(matchMedia('(prefers-color-scheme: dark)').matches?'dark':'light'):t}
function rerenderCharts(){if(lastTrend){renderRevChart(lastTrend,lastAgg);renderSalesChart(lastTrend,lastAgg)}if(lastProj)renderProjChart();if(lastSku)renderSkuChart();if(typeof ovRows!=='undefined'&&ovRows.length)renderOvCharts();if(typeof lastSd!=='undefined'&&lastSd)renderSdChart()}
function setTheme(t){const r=resolveTheme(t);if(document.documentElement.getAttribute('data-theme')!==r){document.documentElement.setAttribute('data-theme',r);rerenderCharts()}}
function toggleTheme(){const s=getSettings();s.theme=document.documentElement.getAttribute('data-theme')==='dark'?'light':'dark';saveSettings(s);setTheme(s.theme)}
try{matchMedia('(prefers-color-scheme: dark)').addEventListener('change',()=>{if(getSettings().theme==='system')setTheme('system')})}catch(e){}
function initials(n){const p=String(n||'').trim().split(/\s+/).filter(Boolean);if(!p.length)return'?';return((p[0][0]||'')+(p.length>1?p[p.length-1][0]:'')).toUpperCase()}
/* Profile photo: a 192px JPEG data URL kept with the other settings in this browser's localStorage (never uploaded). */
function paintAvatar(el,name,photo){if(!el)return;const ok=typeof photo==='string'&&photo.indexOf('data:image/')===0;el.classList.toggle('has-photo',ok);el.style.backgroundImage=ok?`url("${photo}")`:'';el.textContent=ok?'':initials(name)}
function pickPhoto(inp){const f=inp.files&&inp.files[0];inp.value='';if(!f)return;if(!/^image\/(png|jpeg|webp)$/.test(f.type)){toast('Choose a PNG, JPG or WebP image.','error');return}if(f.size>10*1024*1024){toast('That image is too large (max 10 MB).','error');return}
  const url=URL.createObjectURL(f),img=new Image();img.onload=()=>{try{const S=192,c=document.createElement('canvas');c.width=c.height=S;const k=Math.max(S/img.width,S/img.height),w=img.width*k,h=img.height*k;const g=c.getContext('2d');g.fillStyle='#fff';g.fillRect(0,0,S,S);g.drawImage(img,(S-w)/2,(S-h)/2,w,h);draft.photo=c.toDataURL('image/jpeg',.86);previewSettings()}catch(e){toast('Could not read that image.','error')}URL.revokeObjectURL(url)};img.onerror=()=>{URL.revokeObjectURL(url);toast('Could not read that image.','error')};img.src=url}
function removePhoto(){if(draft){draft.photo='';previewSettings()}}
function greetingWord(){const h=new Date().getHours();return h>=5&&h<12?'Good morning':h>=12&&h<17?'Good afternoon':'Good evening'}
function renderGreeting(){const s=getSettings();const g=greetingWord();document.getElementById('greeting').innerHTML=s.name?`${g}, <span class="name">${esc(s.name)}</span>`:g;const av=document.getElementById('headAvatar');paintAvatar(av,s.name,s.photo);av.title=s.name?`${s.name} · Account settings`:'Account settings'}
setInterval(renderGreeting,60000);
function markSeg(id,v){document.querySelectorAll(`#${id} button`).forEach(b=>b.classList.toggle('active',b.dataset.v===v))}
function setActivePill(p){document.querySelectorAll('.pill').forEach(x=>x.classList.toggle('active',x.dataset.p===p))}
function applySettings(){const s=getSettings();currentPreset=s.period||'l4w';setActivePill(currentPreset);renderGreeting()}
let draft=null,lastFocus=null;
function openSettings(){draft=getSettings();lastFocus=document.activeElement;document.getElementById('setName').value=draft.name;markSeg('setTheme',draft.theme);markSeg('setPeriod',draft.period);previewSettings();document.getElementById('settingsModal').hidden=false;setTimeout(()=>document.getElementById('setName').focus(),50)}
function previewSettings(){const n=document.getElementById('setName').value.trim(),ph=draft?draft.photo:'';paintAvatar(document.getElementById('setAvatar'),n,ph);document.getElementById('setPreviewName').textContent=n||'Your name';document.getElementById('setPhotoRemove').hidden=!ph}
function closeSettings(){const m=document.getElementById('settingsModal');if(m.hidden)return;m.hidden=true;setTheme(getSettings().theme);if(lastFocus&&lastFocus.focus)lastFocus.focus()}
function saveSettingsForm(){const prev=getSettings();const s=Object.assign({},draft,{name:document.getElementById('setName').value.trim()});if(!saveSettings(s)){toast('Could not save: browser storage is full or blocked.','error');return}closeSettings();renderGreeting();if(s.period!==prev.period){currentPreset=s.period;setActivePill(s.period);document.getElementById('dateStart').value='';document.getElementById('dateEnd').value='';if(salesLoaded)loadSales()}toast('Settings saved','success')}
document.querySelectorAll('#setTheme button').forEach(b=>b.addEventListener('click',()=>{draft.theme=b.dataset.v;markSeg('setTheme',draft.theme);setTheme(draft.theme)}));
document.querySelectorAll('#setPeriod button').forEach(b=>b.addEventListener('click',()=>{draft.period=b.dataset.v;markSeg('setPeriod',draft.period)}));
