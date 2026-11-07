/* App shell: tab switching, keyboard shortcuts, start-up. Loaded last. */
function activeTab(){const a=document.querySelector('.tab-content.active');return a?a.id.replace('tab-',''):''}
function switchTab(tab){if(tab==='forecast'||tab==='inventory'){anSub=tab==='forecast'?'forecast':'stock';tab='analysis'}
document.querySelectorAll('.tab-content').forEach(t=>t.classList.remove('active'));document.querySelectorAll('.nav-item').forEach(n=>n.classList.remove('active'));document.getElementById('tab-'+tab).classList.add('active');const ni=document.querySelector(`.nav-item[data-tab="${tab}"]`);if(ni)ni.classList.add('active');
if(tab==='analysis')setSub(anSub);if(tab==='replenishment')loadReplenishment();if(tab==='po')loadPoPage();if(tab==='upload')loadDataStatus();
window.scrollTo(0,0);closeAllCdrops();if(tab==='overview'&&!replData)loadOverview()}
function reloadActive(){const t=activeTab();if(t==='analysis'){if(anSub==='forecast'){if(dpView==='weekly')loadWeekly();else loadPlan()}else loadInventory()}if(t==='replenishment')loadReplenishment()}
document.addEventListener('keydown',e=>{const tag=(e.target.tagName||'').toLowerCase();const typing=(tag==='input'&&!['checkbox','radio','button','submit'].includes(e.target.type))||tag==='textarea'||tag==='select'||e.target.isContentEditable;const modalOpen=!document.getElementById('settingsModal').hidden;const loginOpen=!document.getElementById('loginOverlay').classList.contains('hidden');
if(e.key==='Escape'){if(!document.getElementById('rpDrawer').hidden){closeReplDetail();return}if(!document.getElementById('skuDrawer').hidden){closeSkuDrawer();return}if(whyKey){closeWhy();return}if(modalOpen){closeSettings();return}closeAllCdrops();document.getElementById('customRange').classList.remove('show');document.getElementById('monthGrid').classList.remove('show');if(document.getElementById('sub-forecast').classList.contains('active'))closeSkuDetail();if(typing)e.target.blur();return}
if(typing||modalOpen||loginOpen||e.metaKey||e.ctrlKey||e.altKey)return;
const map={'1':'overview','2':'replenishment','3':'analysis','4':'po','5':'upload'};if(map[e.key]){switchTab(map[e.key]);return}
if(e.key==='/'){const sb=document.querySelector('.tab-content.active .search-box');if(sb){e.preventDefault();sb.focus()}}});
applySettings();
(async()=>{try{const r=await _fetch('/api/auth/me',{credentials:'same-origin'});if(r.ok){const d=await r.json();await startApp(d.username);return}}catch(e){}showLogin()})();
