/* Login / logout and app start-up after a session is confirmed (server-side session, see auth.py). */
function showLogin(msg){document.getElementById('waitScreen').classList.remove('active');document.getElementById('loginOverlay').classList.remove('hidden');document.getElementById('loginStatus').textContent=msg||'';setTimeout(()=>document.getElementById('loginEmail').focus(),50)}
function togglePw(){const i=document.getElementById('loginPass');const show=i.type==='password';i.type=show?'text':'password';document.getElementById('pwEye').setAttribute('aria-label',show?'Hide password':'Show password')}
function forgotPw(){toast('Ask your system admin to reset your password.','info')}
let appStarted=false;
async function startApp(username){sessionExpiredShown=false;const s=getSettings();if(username&&!s.name){s.name=username;saveSettings(s)}applySettings();
  const ws=document.getElementById('waitScreen');ws.classList.add('active');document.getElementById('loginOverlay').classList.add('hidden');
  const t0=Date.now();
  const capped=pr=>Promise.race([pr,new Promise(r=>setTimeout(r,3000))]);
  if(!appStarted){appStarted=true;renderMonthGrid();loadPoList();await capped(loadOverview())}
  else{invSummaryCache=null;replData=null;salesLoaded=false;planData=null;weeklyData=[];weeklyByCat={};weeklyShownCat=null;fcDataMap={};loadPoList();await capped(loadOverview());reloadActive();if(activeTab()==='po')loadPoPage();if(activeTab()==='upload')loadDataStatus()}
  setTimeout(()=>ws.classList.remove('active'),Math.max(0,600-(Date.now()-t0)))}
async function doLogin(){const btn=document.getElementById('loginBtn'),status=document.getElementById('loginStatus');
  const username=document.getElementById('loginEmail').value.trim(),password=document.getElementById('loginPass').value;
  if(!username||!password){status.textContent='Enter your username and password.';return}
  btn.disabled=true;btn.textContent='Logging in...';status.textContent='';
  try{const r=await _fetch('/api/auth/login',{method:'POST',headers:{'Content-Type':'application/json'},credentials:'same-origin',body:JSON.stringify({username,password,remember:document.getElementById('loginRemember').checked})});
    const d=await r.json().catch(()=>({}));
    if(!r.ok){status.textContent=d.detail||('Login failed ('+r.status+')');document.getElementById('loginPass').select();return}
    document.getElementById('loginPass').value='';await startApp(d.username)}
  catch(e){status.textContent='Cannot reach the server. Check your connection.'}
  finally{btn.disabled=false;btn.textContent='Log In'}}
async function handleLogout(){if(!confirm('Log out?'))return;try{await _fetch('/api/auth/logout',{method:'POST',credentials:'same-origin'})}catch(e){}showLogin()}
