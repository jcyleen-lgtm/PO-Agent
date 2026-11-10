/* Shared state used by several pages: product-type / best-seller scope, data caches, chart handles,
   and the light forecast map that Overview, Replenishment and Product Analysis read. */
let skuChartObj=null,revChartObj=null,salesChartObj=null,projChartObj=null;
let lastTrend=null,lastAgg=null,lastProj=null,lastSku=null;
let weeklyData=[],invData=[],fcDataMap={},invSummaryCache=null;
let weeklyPage=0,invPage=0;
const PAGE_SIZE=50;
let anSub='stock';
let PT='import',BS=true;
// Stockwise scope: imported best sellers only (food sales are kept out of the system)
const bsScope=()=>BS?'bestseller':'all';
const ptWord=()=>PT==='all'?'':PT+' ';
async function loadFcMap(){try{const r=await fetch('/api/forecast/results?limit=1000&fields=light');const d=await r.json();(d.results||[]).forEach(f=>{fcDataMap[f.sku]=f})}catch(e){}}
