/* Number, money, date and HTML-escaping helpers (no DOM, no state). */
const fmt=n=>n!=null?Number(n).toLocaleString('id-ID'):'--';
const fmtRp=n=>'Rp '+fmt(Math.round(n||0));
const fmtPct=n=>(n>0?'+':'')+n.toFixed(1)+'%';
const cv=n=>getComputedStyle(document.documentElement).getPropertyValue(n).trim();
function hexA(hex,a){const h=hex.replace('#','');const r=parseInt(h.substr(0,2),16),g=parseInt(h.substr(2,2),16),b=parseInt(h.substr(4,2),16);return`rgba(${r},${g},${b},${a})`}
const fmtRpS=n=>{if(n==null)return'--';const a=Math.abs(n);return a>=1e9?'Rp '+(n/1e9).toFixed(2).replace('.',',')+' M':a>=1e6?'Rp '+(n/1e6).toFixed(1).replace('.',',')+' jt':fmtRp(n)};
const MON_S=['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
const dfmt=iso=>{if(!iso)return'--';const d=new Date(iso+'T00:00:00');return`${d.getDate()} ${MON_S[d.getMonth()]} ${d.getFullYear()}`};
const esc=t=>String(t).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
