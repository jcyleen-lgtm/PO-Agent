/* API access: session-expiry aware fetch wrapper (401 → login) and readJson() for non-JSON error pages.
   Endpoint URLs and payloads are unchanged; see docs/api-inventory.md. */
/* Server answered with something that is not JSON (e.g. Vercel timeout page): turn it into a readable error */
async function readJson(r){const t=await r.text();try{return JSON.parse(t)}catch(e){const msg=r.status===504||/timed out|FUNCTION_INVOCATION_TIMEOUT/i.test(t)?'The server took too long (time limit reached). Try again, or upload from the laptop.':(r.status>=500?`Server error (${r.status}). Try again in a moment.`:`Unexpected response (${r.status}).`);return{detail:msg}}}
const _fetch=window.fetch.bind(window);
let sessionExpiredShown=false;
window.fetch=async(input,init)=>{const r=await _fetch(input,init);const url=typeof input==='string'?input:(input&&input.url)||'';
  if(r.status===401&&url.indexOf('/api/auth/')===-1&&document.getElementById('loginOverlay').classList.contains('hidden')){if(!sessionExpiredShown){sessionExpiredShown=true;toast('Your session has ended. Please log in again.','info')}showLogin()}
  return r};
