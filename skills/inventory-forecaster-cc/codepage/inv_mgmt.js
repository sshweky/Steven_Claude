// Immediate diagnostic - if this runs, JS is executing
(function(){
  var el = document.getElementById('loadStatus');
  if (el) el.textContent = 'Script executing...';
})();

window.onerror = function(msg, src, line, col, err) {
  var el = document.getElementById('loadStatus');
  if (el) el.textContent = 'JS Error (line ' + line + '): ' + msg;
  var steps = document.getElementById('loadSteps');
  if (steps) steps.innerHTML = '<div style="color:#ff8a65;font-size:11px;word-break:break-all;">' + msg + '<br>Line: ' + line + '</div>';
  return true;
};

// -- Constants -----------------------------------------------------------------
var QB_REALM  = 'pim.quickbase.com';
var QB_TOKEN  = 'QB-USER-TOKEN b39re4_mkf7_du2buby24kr7d4hkcu9cpxn69s';
var INVF_TID  = 'bpsaju5pm';
var PROJ_TID  = 'bpd237tvm';
var CACHE_KEY = 'pp_inv_mgmt_codepage_v2';
var CACHE_TTL = 6 * 60 * 60 * 1000;

var OPT_WOS_DEFAULT       = 4.0;
var PUR_REC_BUFFER_WKS    = 4;     // extra weeks of demand added to Opt_OH as reorder buffer (~monthly reorder cycle)
var WAREHOUSE_LAG_DAYS    = 10;
var USA_WAREHOUSE_LAG     = 3;
var FAST_VESSEL_TRANSIT   = 18;
var PARTIAL_MIN_PCS       = 2500;
var ETD_LOCK_DAYS         = 7;
var FASTER_VESSEL_HI      = 15;
var CANCEL_HORIZON_DAYS   = 60;
var OVERSTOCK_EXCESS_TH   = 2500;
var OVERSTOCK_WOS_TH      = 33;
var MIN_PULLUP_DAYS       = 7;
var MIN_RESOLVED_GAP_DEF  = 1.0;

// -- Field ID maps -------------------------------------------------------------
var IF_F = {
  Mstyle:20, Country:223, ItemStatus:294, SubStatus:297, Season:1068, ItemRank:1573,
  NVO:1487, NewItemNoPrj:1893, KitStyle:1759, PcsKitUse:1882, RootMstyle:792,
  OptWOS:137, OptWOSFinal:1897, OptOH:234, NextAvlRcptDt:235, NxtAvlETD:1917,
  LTTransDays:225, TransitDays:1751, LTWks:1525, CNYWeeks:1891, LTOptWeeks:446,
  OpenSupplierPOs:241, MOQ:226, InvManager:981,
  QtyOH:24, ATSQtyOH:179, ATSNow:932, ATSOHplusOO:180, ATSOHOOwKits:1722,
  ATSQtyNotAlloc:871, NJATSOH:1462, CAATSOH:1463, HoldOrderQty:867,
  IT:25, IW:26, ITplusIW:218, ITIWwKits:1721, OpenCustPOQty:183,
  TestOrderQty:1909, ExcludePOWOSQty:1910, ATSOHITBookedWOS:1217,
  ATSWOSOH:208, ATSWOSOHplusOO:209, ATSWOSOHOOwKits:1723, ATSWOSOHOOwotestexcl:1911,
  DaysOOSNextRcpt:1009, DaysOOSL12m:1231, LastOOSDate:1233,
  PrjWk:133, MaxPrjWk:1883, PrjL4wAll:963, Prj26Wks:1225,
  ShpWkL4:1841, ShpWkL13:314, TotShpdL13w:316, TotShpdL4:315, TotShpdLTD:712, LastShpDate:305,
  Date1stRcvd:711, LastWhsRcvd:1199, FirstShpdDate:1892, FirstOutDate:260,
  ActiveKL:1486, AMZDoNotShip:1829, AMZSuppression:1871, TransferQtyOpen:1572,
  ShipmentStatusSummary:1224, ATSSummary:912, InventoryNotes:257, StyleAlert:994,
  OOSPriorityNotes:985, ActiveReplCusts:1223,
  SizeCt:533, Fragrance:589, PvtLblExcl:1795, CommitItem:1515, InnerPack:1023, MasterPack:929,
  UPC:1915, GTIN:1916,
  OOSDates:262, OverCommittedQty:448, OvrComtWOS:444, InvtryAgeDays:1269,
  AgedInv090:1308, AgedInv91180:1310, AgedInv181365:1312, AgedInv365plus:1314, PctTimeInStock:1232,
  // Main supplier
  SupplierInfo:1830, FOBCost:220, ELC_NJ:1028, ELC_LA:1026,
  MU_NJ:1809, MU_LA:1810, QtyOrdSupplier:1833, PctUnitsOrdSupplier:1834,
  // Alt Supplier 1
  Alt1Name:1702, Alt1FOB:1705, Alt1MOQ:1708, Alt1LT:1711,
  Alt1ELC_NJ:1816, Alt1ELC_LA:1813, Alt1MU_NJ:1819, Alt1MU_LA:1822,
  Alt1QtyOrd:1835, Alt1PctOrd:1838,
  // Alt Supplier 2
  Alt2Name:1703, Alt2FOB:1706, Alt2MOQ:1709, Alt2LT:1712,
  Alt2ELC_NJ:1817, Alt2ELC_LA:1814, Alt2MU_NJ:1820, Alt2MU_LA:1823,
  Alt2QtyOrd:1836, Alt2PctOrd:1839,
  // Alt Supplier 3
  Alt3Name:1704, Alt3FOB:1707, Alt3MOQ:1710, Alt3LT:1713,
  Alt3ELC_NJ:1818, Alt3ELC_LA:1815, Alt3MU_NJ:1821, Alt3MU_LA:1824,
  Alt3QtyOrd:1837, Alt3PctOrd:1840
};
var IF_BEG = [134,8,9,10,110,111,112,113,114,115,116,117,118,128,129,130,131,120,121,122,123,124,125,126,127,119];
var IF_RCV = [28,35,36,50,51,65,66,67,68,69,70,71,72,73,74,75,76,77,78,79,80,81,82,83,84,85];
var IF_PRJ = [146,147,150,151,152,153,154,155,156,157,158,159,160,161,162,163,164,165,166,167,168,169,170,171,172,173];
var IF_ATS = [716,717,718,719,720,715,722,723,724,725,726,727,728,729,730,731,902,903,904,905,906,907,908,909,910,911];

var PRJ_F = { Mstyle:196, CustName:376, StatusCust:10, PTItemStatus:374, Brand:398, Description:399, AcctMStyleKey:292 };
var PRJ_MANUAL = [22,25,28,31,34,37,40,43,46,49,52,55,58,61,64,67,70,73,76,79,82,85,88,91,94,97];

// -- QB API --------------------------------------------------------------------
async function qbQuery(tableId, fieldIds, where, skip, top) {
  skip = skip || 0; top = top || 1000;
  var body = { from: tableId, select: fieldIds, where: where || '', options: { skip: skip, top: top } };
  var r = await fetch('https://api.quickbase.com/v1/records/query', {
    method: 'POST',
    headers: {
      'QB-Realm-Hostname': QB_REALM,
      'Authorization': QB_TOKEN,
      'Content-Type': 'application/json'
    },
    body: JSON.stringify(body)
  });
  if (!r.ok) throw new Error('QB API ' + r.status + ': ' + await r.text());
  return r.json();
}

async function qbQueryAll(tableId, fieldIds, where, label) {
  var skip = 0, top = 1000, all = [];
  var page = 0;
  while (true) {
    page++;
    if (label) setStatus((label) + ' (page ' + page + ', ' + all.length + ' records)...');
    var data = await qbQuery(tableId, fieldIds, where, skip, top);
    var rows = data.data || [];
    all = all.concat(rows);
    if (rows.length < top) break;
    skip += top;
  }
  return all;
}

// -- Cache (localStorage with sessionStorage fallback for large datasets) ------
function saveCache(data) {
  var payload = JSON.stringify({ ts: Date.now(), data: data });
  // Try localStorage first (persists across browser refreshes for up to CACHE_TTL)
  try {
    localStorage.setItem(CACHE_KEY, payload);
    sessionStorage.removeItem(CACHE_KEY); // clear session copy if ls succeeded
    return;
  } catch(e) {}
  // localStorage full (QuotaExceededError) -- fall back to sessionStorage
  // sessionStorage survives Ctrl+R but clears when the tab closes
  try { sessionStorage.setItem(CACHE_KEY, payload); } catch(e) {}
}
function loadCache() {
  // Prefer localStorage (cross-refresh), fall back to sessionStorage
  var raw = null;
  try { raw = localStorage.getItem(CACHE_KEY); } catch(e) {}
  if (!raw) { try { raw = sessionStorage.getItem(CACHE_KEY); } catch(e) {} }
  if (!raw) return null;
  try {
    var obj = JSON.parse(raw);
    if (Date.now() - obj.ts > CACHE_TTL) return null;
    return obj;
  } catch(e) { return null; }
}
function clearCache() {
  try { localStorage.removeItem(CACHE_KEY); } catch(e) {}
  try { sessionStorage.removeItem(CACHE_KEY); } catch(e) {}
}
function fmtTimestamp(ts) {
  var d = new Date(ts);
  var h = d.getHours(), m = d.getMinutes();
  var ampm = h < 12 ? 'AM' : 'PM';
  var h12 = h % 12 || 12;
  return (d.getMonth()+1) + '/' + d.getDate() + '/' + d.getFullYear() + ' ' + h12 + ':' + String(m).padStart(2,'0') + ' ' + ampm;
}

// -- PO parser -----------------------------------------------------------------
// Format: "FC607491 - SUPPLIER NAME - I/T: 0 pcs / I/W: 1200 pcs - ETD: 05-17-2026 - ETA: 06-04-2026"
var PO_RE = /^\s*([A-Z0-9\-]+)\s*-\s*(.+?)\s*-\s*I\/T:\s*([\d,]+)\s*pcs\s*\/\s*I\/W:\s*([\d,]+)\s*pcs\s*-\s*ETD:\s*(\d{2}-\d{2}-\d{4})\s*-\s*ETA:\s*(\d{2}-\d{2}-\d{4})/i;

function parseMMDDYYYY(s) {
  if (!s) return null;
  var m = s.trim().match(/^(\d{2})-(\d{2})-(\d{4})$/);
  if (!m) return null;
  return new Date(parseInt(m[3]), parseInt(m[1])-1, parseInt(m[2]));
}

function parsePOs(text) {
  if (!text) return [];
  var out = [];
  var chunks = text.split(/[;\r\n]+/);
  for (var i = 0; i < chunks.length; i++) {
    var c = chunks[i].trim();
    if (!c) continue;
    var m = PO_RE.exec(c);
    if (!m) continue;
    var it = parseInt(m[3].replace(/,/g,''));
    var iw = parseInt(m[4].replace(/,/g,''));
    var etd = parseMMDDYYYY(m[5]);
    var eta = parseMMDDYYYY(m[6]);
    var td = (etd && eta) ? Math.round((eta - etd) / 86400000) : null;
    out.push({
      po_number: m[1].trim(), supplier: m[2].trim(),
      in_transit_qty: it, in_work_qty: iw, qty: it+iw,
      etd: etd, eta: eta, transit_days: td,
      is_in_transit: it > 0,
      etd_iso: etd ? fmtISO(etd) : null,
      eta_iso: eta ? fmtISO(eta) : null
    });
  }
  return out;
}

function poStatus(po, today, country) {
  if (po.is_in_transit) return 'IN_TRANSIT';
  if (!po.etd) return 'UNKNOWN';
  var dtd = Math.round((po.etd - today) / 86400000);
  if (dtd < ETD_LOCK_DAYS) return 'LOCKED';
  var isUSA = /^(usa|united states)$/i.test(country || '');
  if (dtd >= 8 && dtd <= FASTER_VESSEL_HI) return isUSA ? 'PULL_UP_NARROW' : 'FASTER_VESSEL_WINDOW';
  return 'MOVABLE';
}

// -- Utilities -----------------------------------------------------------------
function fmt(n) { if (n == null || n === '' || n === undefined) return '0'; var num=Number(n); if (isNaN(num)) return String(n); return num.toLocaleString('en-US', {maximumFractionDigits:1}); }
function fmtInt(n) { if (n == null || n === '' || n === undefined) return '0'; var num=Math.round(Number(n)); if (isNaN(num)) return String(n); return num.toLocaleString('en-US', {maximumFractionDigits:0}); }
function fmtCur(n) { if (n == null || n === '' || n === undefined || n === 0) return '&#8212;'; var num=Number(n); if (isNaN(num)) return String(n); return '$'+num.toFixed(2); }
function fmtPct(n) { if (n == null || n === '' || n === undefined || n === 0) return '&#8212;'; var num=Number(n); if (isNaN(num)) return String(n); return num.toFixed(2)+'%'; }
function esc(s) { return String(s == null ? '' : s).replace(/[<>&"']/g, function(c){return {'<':'&lt;','>':'&gt;','&':'&amp;','"':'&quot;',"'":'&#39;'}[c];}); }
// Strip HTML tags and common entities from QB-returned rich-text fields
function stripHtml(s) {
  if (!s) return '';
  return String(s)
    .replace(/<[^>]*>/g, '')
    .replace(/&amp;/g,'&').replace(/&lt;/g,'<').replace(/&gt;/g,'>').replace(/&nbsp;/g,' ').replace(/&#\d+;/g,'').replace(/&[a-z]+;/gi,'')
    .replace(/\s+/g,' ').trim();
}
// Extract plain string from QB user-type fields (returned as {id,name,email} objects)
function qbUser(v) {
  if (!v) return '';
  if (typeof v === 'object') return String(v.name || v.email || v.id || '');
  return String(v);
}
function fmtDate(d) {
  if (!d) return '&#8212;';
  try { var dt = (typeof d === 'string') ? new Date(d) : d; if (!dt || isNaN(dt)) return '&#8212;'; return dt.toLocaleDateString('en-US', {month:'short', day:'numeric'}); }
  catch(e) { return String(d); }
}
function fmtISO(d) { if (!d) return null; var dt = (typeof d === 'string') ? new Date(d) : d; if (!dt || isNaN(dt)) return null; return dt.toISOString().slice(0,10); }
function toNum(v) { if (v == null || v === '') return 0; return parseFloat(v) || 0; }
function toBool(v) { if (v == null) return false; if (typeof v === 'boolean') return v; if (typeof v === 'number') return v !== 0; return /^(true|1|yes)$/i.test(String(v)); }
function addDays(d, n) { var r = new Date(d); r.setDate(r.getDate()+n); return r; }
function addWeeks(d, n) { return addDays(d, n*7); }
function wkSunday(today, idx) {
  var w1 = new Date(today); w1.setDate(today.getDate() - today.getDay());
  return addWeeks(w1, idx);
}
function wkIdxForDate(today, dt) {
  if (!dt) return 25;
  var dtObj = (typeof dt === 'string') ? new Date(dt) : dt;
  var w1 = new Date(today); w1.setDate(today.getDate() - today.getDay());
  return Math.floor((dtObj - w1) / (7 * 86400000));
}

// -- State ---------------------------------------------------------------------
var ALL = [], FILTERED = [];
var currentSort = { id: null, dir: 1 };
var DEFAULT_SORT_CHAIN = ['inv_manager','brand','mstyle'];
var colFilters = {};
var selActions    = new Set();
var selCountries  = new Set();
var selBrands     = new Set();
var selMgrs       = new Set();
var selPriorities = new Set();
var selStockStatus = new Set();
var recoSheet = [];
var purchaseSelections = {};   // keyed by mstyle; { needQty: number, chosenSupplier: string }

// -- Multi-select dropdown helpers ---------------------------------------------
function toggleDd(evt, ddId) {
  evt.stopPropagation();
  var panel = document.getElementById(ddId).querySelector('.ms-dd-panel');
  var isOpen = panel.classList.contains('open');
  document.querySelectorAll('.ms-dd-panel.open').forEach(function(p){p.classList.remove('open');});
  if (!isOpen) panel.classList.add('open');
}
document.addEventListener('click', function(){
  document.querySelectorAll('.ms-dd-panel.open').forEach(function(p){p.classList.remove('open');});
});
function getDdValues(ddId) {
  return Array.from(document.querySelectorAll('#'+ddId+' input[type=checkbox]:checked')).map(function(c){return c.value;});
}
function updateDdBtn(ddId, allLabel) {
  var vals = getDdValues(ddId);
  var btn = document.querySelector('#'+ddId+' .ms-dd-btn');
  if (!btn) return;
  btn.textContent = vals.length===0 ? allLabel : vals.length+' selected';
  btn.classList.toggle('has-sel', vals.length>0);
}
function buildDdPanel(ddId, items, allLabel, setState) {
  var panel = document.querySelector('#'+ddId+' .ms-dd-panel');
  if (!panel) return;
  panel.innerHTML = '';
  items.forEach(function(item) {
    var lbl = document.createElement('label');
    lbl.className = 'ms-item';
    var cb = document.createElement('input');
    cb.type = 'checkbox'; cb.value = item.v;
    cb.addEventListener('change', function() {
      var vals = getDdValues(ddId);
      setState(new Set(vals));
      updateDdBtn(ddId, allLabel);
      applyFilters();
    });
    lbl.appendChild(cb);
    lbl.appendChild(document.createTextNode(item.label || item.v));
    panel.appendChild(lbl);
  });
}

function setBar(pct) { var b=document.getElementById('loadBar'); if(b) b.style.width=pct+'%'; }
function setStatus(msg) { var s=document.getElementById('loadStatus'); if(s) s.textContent=msg; }
function setStep(n, state) {
  for (var i=1;i<=4;i++) { var el=document.getElementById('ls'+i); if(!el) continue; el.className=i<n?'done':i===n?state:'pending'; }
}

// -- Data loading --------------------------------------------------------------
async function loadData() {
  var today = new Date(); today.setHours(0,0,0,0);

  setStep(2,'active'); setBar(10);
  var ifFieldIds = Object.values(IF_F).concat(IF_BEG, IF_RCV, IF_PRJ, IF_ATS);
  // QB can't filter on formula fields (fid 927) or lookup fields (fid 294), so load all and
  // apply the field-927 Case() formula logic client-side after the pull.
  var ifRowsAll = await qbQueryAll(INVF_TID, ifFieldIds, '', 'Loading Inventory Flow');
  var IF_ACTIVE_STATUSES = {
    "Active: Promo":1,"Active: Promo Commt":1,"Active: Replen":1,"Active: Replen Commt":1,
    "Active: Multi-Pk Replen":1,"Future Delete":1,
    "In Prodn: Promo":1,"In Prodn: Promo Commt":1,"In Prodn: Replen":1
  };
  var ifRows = ifRowsAll.filter(function(row){
    var s = String((row[IF_F.ItemStatus]||{}).value||'').trim();
    return IF_ACTIVE_STATUSES[s] === 1;
  });
  setStatus('IF: ' + ifRows.length + ' active of ' + ifRowsAll.length + ' total');

  setStep(3,'active'); setBar(55); setStatus('Loading Projections...');
  // Active projections only (StatusCust starts with 'A') — provides demand + brand/description
  var prjFieldIds = Object.values(PRJ_F).concat(PRJ_MANUAL);
  var prjRows = await qbQueryAll(PROJ_TID, prjFieldIds, "{10}.CT.'A'", 'Loading Projections');

  setBar(70); setStatus('Processing data...');

  // Build projections lookup by mstyle
  var prjByMs = {};
  for (var i=0; i<prjRows.length; i++) {
    var row = prjRows[i];
    var ms   = String((row[PRJ_F.Mstyle]||{}).value||'').trim();
    var cust = String((row[PRJ_F.CustName]||{}).value||'').trim();
    var desc = stripHtml((row[PRJ_F.Description]||{}).value);
    var brand= stripHtml((row[PRJ_F.Brand]||{}).value);
    if (!ms) continue;
    var weekly = PRJ_MANUAL.map(function(fid){return toNum((row[fid]||{}).value);});
    var total  = weekly.reduce(function(a,b){return a+b;},0);
    if (!prjByMs[ms]) prjByMs[ms] = { custs:[], desc:desc, brand:brand };
    else { if(desc && !prjByMs[ms].desc) prjByMs[ms].desc=desc; if(brand && !prjByMs[ms].brand) prjByMs[ms].brand=brand; }
    prjByMs[ms].custs.push({ customer:cust, weekly:weekly, total:total });
  }

  // Build records from Inventory Flow
  var records = [];
  for (var i=0; i<ifRows.length; i++) {
    var row = ifRows[i];
    var g = function(fid) { return (row[fid]||{}).value; };

    var mstyle  = String(g(IF_F.Mstyle)||'').trim();
    if (!mstyle) continue;
    var country = String(g(IF_F.Country)||'').trim();
    var isMulti = toBool(g(IF_F.KitStyle));
    var pcsKit  = toNum(g(IF_F.PcsKitUse)) || 1;
    if (pcsKit > 1) continue; // hide any record with PcsKitUse > 1, regardless of KitStyle
    var rootMs  = String(g(IF_F.RootMstyle)||'').trim();

    var pi = prjByMs[mstyle] || { custs:[], desc:'', brand:'' };

    var optWOS = toNum(g(IF_F.OptWOSFinal)) || toNum(g(IF_F.OptWOS)) || OPT_WOS_DEFAULT;
    var beg_inv = IF_BEG.map(function(fid){return Math.round(toNum(g(fid)));});
    var rcv     = IF_RCV.map(function(fid){return Math.round(toNum(g(fid)));});
    var prj     = IF_PRJ.map(function(fid){return Math.round(toNum(g(fid)));});

    var custDemand = (pi.custs||[]).sort(function(a,b){return b.total-a.total;});
    var manDem26w  = custDemand.reduce(function(s,c){return s+c.total;},0);

    var openPosRaw = String(g(IF_F.OpenSupplierPOs)||'');
    var rawPOs = parsePOs(openPosRaw);
    var openPos = rawPOs.map(function(p){
      return Object.assign({}, p, {
        status: poStatus(p, today, country),
        etd: p.etd_iso, eta: p.eta_iso,
        etd_obj: p.etd, eta_obj: p.eta
      });
    });

    var nrRaw = g(IF_F.NextAvlRcptDt);
    var nextRcptDt = nrRaw ? new Date(nrRaw) : null;

    var itemStatusFlow = String(g(IF_F.ItemStatus)||'').trim();
    // Skip non-actionable statuses
    if (/^(restricted|ready to sell|ready to quote|discontinued|dropped|in develop)/i.test(itemStatusFlow)) continue;
    if (/component/i.test(itemStatusFlow)) continue;
    var isReplen = /^(replen|active|r)/i.test(itemStatusFlow) || toNum(g(IF_F.OptWOS)) > 0;

    var shpL4  = toNum(g(IF_F.ShpWkL4));
    var shpL13 = toNum(g(IF_F.ShpWkL13));
    var prjL4wChange = 0;
    if (shpL4 !== 0 && shpL13 !== 0) prjL4wChange = Math.round(((shpL4-shpL13)/shpL13)*100);

    var rec = {
      mstyle:mstyle, country:country,
      brand: pi.brand||'', description: pi.desc||'',
      inv_manager: qbUser(g(IF_F.InvManager)).trim(),
      item_status: itemStatusFlow, item_status_flow:itemStatusFlow,
      sub_status: String(g(IF_F.SubStatus)||'').trim(),
      season: String(g(IF_F.Season)||'').trim(),
      item_rank: String(g(IF_F.ItemRank)||'').trim(),
      customer_count: custDemand.length,
      is_replen: isReplen,
      beg_inv:beg_inv, rcv:rcv, prj:prj,
      opt_wos:optWOS, opt_oh:toNum(g(IF_F.OptOH)),
      next_rcpt_dt:nextRcptDt,
      lt_trans_days:toNum(g(IF_F.LTTransDays)), transit_days:toNum(g(IF_F.TransitDays)), nxt_avl_etd:g(IF_F.NxtAvlETD)||null,
      lt_wks:toNum(g(IF_F.LTWks)), cny_weeks:toNum(g(IF_F.CNYWeeks)), lt_opt_weeks:toNum(g(IF_F.LTOptWeeks)),
      moq:toNum(g(IF_F.MOQ)),
      qty_oh:toNum(g(IF_F.QtyOH)), ats_qty_oh:toNum(g(IF_F.ATSQtyOH)),
      ats_now:toNum(g(IF_F.ATSNow)), ats_oh_oo:toNum(g(IF_F.ATSOHplusOO)),
      ats_oh_oo_w_kits:toNum(g(IF_F.ATSOHOOwKits)), ats_qty_not_alloc:toNum(g(IF_F.ATSQtyNotAlloc)),
      nj_ats_oh:toNum(g(IF_F.NJATSOH)), ca_ats_oh:toNum(g(IF_F.CAATSOH)),
      hold_qty:toNum(g(IF_F.HoldOrderQty)),
      it_qty:toNum(g(IF_F.IT)), iw_qty:toNum(g(IF_F.IW)), it_iw:toNum(g(IF_F.ITplusIW)),
      it_iw_kits:toNum(g(IF_F.ITIWwKits)), open_cust_po_qty:toNum(g(IF_F.OpenCustPOQty)),
      test_order_qty:toNum(g(IF_F.TestOrderQty)), exclude_po_wos:toNum(g(IF_F.ExcludePOWOSQty)),
      ats_wos_oh:toNum(g(IF_F.ATSWOSOH)), ats_wos_oh_oo:toNum(g(IF_F.ATSWOSOHplusOO)),
      ats_wos_oh_oo_w_kits:toNum(g(IF_F.ATSWOSOHOOwKits)), ats_wos_oh_oo_wo_test:toNum(g(IF_F.ATSWOSOHOOwotestexcl)),
      ats_oh_it_booked_wos:toNum(g(IF_F.ATSOHITBookedWOS)),
      days_oos_next_rcpt:toNum(g(IF_F.DaysOOSNextRcpt)), days_oos_l12m:toNum(g(IF_F.DaysOOSL12m)),
      last_oos_date: g(IF_F.LastOOSDate)||null,
      prj_wk:toNum(g(IF_F.PrjWk)), max_prj_wk:toNum(g(IF_F.MaxPrjWk)),
      prj_l4w_change:prjL4wChange, prj_26wks:toNum(g(IF_F.Prj26Wks)),
      shp_wk_l4:shpL4, shp_wk_l13:shpL13,
      tot_shpd_l13w:toNum(g(IF_F.TotShpdL13w)), tot_shpd_l4:toNum(g(IF_F.TotShpdL4)),
      tot_shpd_ltd:toNum(g(IF_F.TotShpdLTD)), last_shp_date:g(IF_F.LastShpDate)||null,
      date_1st_rcvd:g(IF_F.Date1stRcvd)||null, last_whs_rcvd:g(IF_F.LastWhsRcvd)||null,
      first_shpd_date:g(IF_F.FirstShpdDate)||null, first_out_date:g(IF_F.FirstOutDate)||null,
      nvo:toBool(g(IF_F.NVO)), new_item_no_prj:toBool(g(IF_F.NewItemNoPrj)),
      active_kl:toBool(g(IF_F.ActiveKL)), amz_do_not_ship:toBool(g(IF_F.AMZDoNotShip)),
      amz_suppression:toBool(g(IF_F.AMZSuppression)),
      transfer_qty_open:toNum(g(IF_F.TransferQtyOpen))>0,
      pvt_lbl_excl:toBool(g(IF_F.PvtLblExcl)), commit_item:toBool(g(IF_F.CommitItem)),
      shipment_status_summary:String(g(IF_F.ShipmentStatusSummary)||''),
      ats_summary:String(g(IF_F.ATSSummary)||''),
      inventory_notes:String(g(IF_F.InventoryNotes)||''),
      style_alert:String(g(IF_F.StyleAlert)||''),
      oos_priority_notes:String(g(IF_F.OOSPriorityNotes)||''),
      active_replen_customers:String(g(IF_F.ActiveReplCusts)||''),
      size_ct:String(g(IF_F.SizeCt)||''), fragrance:String(g(IF_F.Fragrance)||''),
      inner_pack:String(g(IF_F.InnerPack)||''), master_pack:String(g(IF_F.MasterPack)||''),
      upc:String(g(IF_F.UPC)||''), gtin:String(g(IF_F.GTIN)||''),
      oos_dates:String(g(IF_F.OOSDates)||''),
      over_committed_qty:toNum(g(IF_F.OverCommittedQty)), ovr_comt_wos:toNum(g(IF_F.OvrComtWOS)),
      invtry_age_days:toNum(g(IF_F.InvtryAgeDays)),
      aged_inv_0_90:toNum(g(IF_F.AgedInv090)), aged_inv_91_180:toNum(g(IF_F.AgedInv91180)),
      aged_inv_181_365:toNum(g(IF_F.AgedInv181365)), aged_inv_365plus:toNum(g(IF_F.AgedInv365plus)),
      pct_time_in_stock:toNum(g(IF_F.PctTimeInStock)),
      // Supplier fields
      supplier_info:String(g(IF_F.SupplierInfo)||''),
      fob_cost:toNum(g(IF_F.FOBCost)), elc_nj:toNum(g(IF_F.ELC_NJ)), elc_la:toNum(g(IF_F.ELC_LA)),
      mu_nj:toNum(g(IF_F.MU_NJ)), mu_la:toNum(g(IF_F.MU_LA)),
      qty_ord_supplier:toNum(g(IF_F.QtyOrdSupplier)), pct_units_ord_supplier:toNum(g(IF_F.PctUnitsOrdSupplier)),
      alt1_name:String(g(IF_F.Alt1Name)||''), alt1_fob:toNum(g(IF_F.Alt1FOB)), alt1_moq:toNum(g(IF_F.Alt1MOQ)), alt1_lt:toNum(g(IF_F.Alt1LT)),
      alt1_elc_nj:toNum(g(IF_F.Alt1ELC_NJ)), alt1_elc_la:toNum(g(IF_F.Alt1ELC_LA)), alt1_mu_nj:toNum(g(IF_F.Alt1MU_NJ)), alt1_mu_la:toNum(g(IF_F.Alt1MU_LA)),
      alt1_qty_ord:toNum(g(IF_F.Alt1QtyOrd)), alt1_pct_ord:toNum(g(IF_F.Alt1PctOrd)),
      alt2_name:String(g(IF_F.Alt2Name)||''), alt2_fob:toNum(g(IF_F.Alt2FOB)), alt2_moq:toNum(g(IF_F.Alt2MOQ)), alt2_lt:toNum(g(IF_F.Alt2LT)),
      alt2_elc_nj:toNum(g(IF_F.Alt2ELC_NJ)), alt2_elc_la:toNum(g(IF_F.Alt2ELC_LA)), alt2_mu_nj:toNum(g(IF_F.Alt2MU_NJ)), alt2_mu_la:toNum(g(IF_F.Alt2MU_LA)),
      alt2_qty_ord:toNum(g(IF_F.Alt2QtyOrd)), alt2_pct_ord:toNum(g(IF_F.Alt2PctOrd)),
      alt3_name:String(g(IF_F.Alt3Name)||''), alt3_fob:toNum(g(IF_F.Alt3FOB)), alt3_moq:toNum(g(IF_F.Alt3MOQ)), alt3_lt:toNum(g(IF_F.Alt3LT)),
      alt3_elc_nj:toNum(g(IF_F.Alt3ELC_NJ)), alt3_elc_la:toNum(g(IF_F.Alt3ELC_LA)), alt3_mu_nj:toNum(g(IF_F.Alt3MU_NJ)), alt3_mu_la:toNum(g(IF_F.Alt3MU_LA)),
      alt3_qty_ord:toNum(g(IF_F.Alt3QtyOrd)), alt3_pct_ord:toNum(g(IF_F.Alt3PctOrd)),
      is_multi:isMulti, pcs_per_kit:pcsKit, root_mstyle:rootMs,
      qty_oh_root:0, it_iw_root:0, ats_oh_oo_root:0, assembleable_kits:0,
      open_pos:openPos,
      manual_demand_26w:manDem26w, customer_demand:custDemand, demand_26w:0,
      pipeline_total:0, oh_excess:0, pipeline_excess:0, pipeline_wos:0,
      gap_weeks:[], overstocked:false, stock_status:'', recommendations:[], priority:'LOW', flag:'',
      purchase_rec:0, purchase_rec_etd:null, purchase_rec_push_supplier:false,
      purchase_rec_receipt_date:null, purchase_rec_trigger_idx:-1
    };

    computeDerived(rec, today);
    records.push(rec);
  }
  return records;
}

// -- computeDerived ------------------------------------------------------------
function computeDerived(rec, today) {
  var openPOTotal = rec.open_pos.reduce(function(s,p){return s+(p.qty||0);},0);
  rec.pipeline_total = (rec.beg_inv[0]||0) + openPOTotal;
  rec.demand_26w     = rec.prj.reduce(function(s,v){return s+v;},0);
  rec.assembleable_kits = (rec.is_multi && rec.pcs_per_kit>0) ? Math.floor(rec.qty_oh_root/rec.pcs_per_kit) : 0;

  // OH Excess & OH+OO Excess — both measured at the Next Available Receipt date.
  // Cumulative projected demand is burned week-by-week up to (but not including) the receipt week.
  // OH Excess   = qty_oh minus demand consumed before receipt arrives.
  // OH+OO Excess = (qty_oh + IT + IW + all open PO qty) minus same demand.
  var nrIdx = rec.next_rcpt_dt ? Math.max(0, wkIdxForDate(today, rec.next_rcpt_dt)) : -1;
  var weeksToRcpt = (nrIdx >= 0) ? Math.min(nrIdx, 26) : 26;
  var cumDemandToRcpt = 0;
  for (var _wi = 0; _wi < weeksToRcpt; _wi++) { cumDemandToRcpt += (rec.prj[_wi] || 0); }
  rec.oh_excess       = Math.round(rec.qty_oh - cumDemandToRcpt);
  rec.pipeline_excess = Math.round((rec.qty_oh + rec.it_qty + rec.iw_qty + openPOTotal) - cumDemandToRcpt);

  // Pipeline WOS — 26-week view, used for overstock flag
  if (rec.demand_26w > 0) {
    rec.pipeline_wos = parseFloat((rec.pipeline_total * 26.0 / rec.demand_26w).toFixed(1));
  } else {
    rec.pipeline_wos = rec.pipeline_total > 0 ? null : 0;
  }
  rec.overstocked = (rec.pipeline_excess > OVERSTOCK_EXCESS_TH)
    || (rec.demand_26w > 0 && rec.pipeline_wos != null && rec.pipeline_wos > OVERSTOCK_WOS_TH);

  // Gap detection — true OOS weeks (beg_inv=0) before next available receipt
  rec.gap_weeks = [];
  if (rec.is_replen) {
    var nrIdx = rec.next_rcpt_dt ? wkIdxForDate(today, rec.next_rcpt_dt) : 25;
    // check strictly before receipt week (nrIdx-1); if no receipt, check all 25 weeks
    var checkUntil = Math.min(24, nrIdx - 1);
    for (var i=0; i<=checkUntil; i++) {
      var bv=rec.beg_inv[i]||0, pv=rec.prj[i]||0;
      if (pv>0 && bv<=0) {
        var wkd = wkSunday(today, i);
        rec.gap_weeks.push({ wi:i+1, date:fmtISO(wkd), beg:bv, prj:pv,
          wos:0, deficit:parseFloat(rec.opt_wos.toFixed(1)) });
      }
    }
  }

  // Recommendations
  var actionableGaps = rec.gap_weeks.filter(function(g){return g.deficit>=MIN_RESOLVED_GAP_DEF;});
  var isUSA = /^(usa|united states)$/i.test(rec.country||'');
  var whLag = isUSA ? USA_WAREHOUSE_LAG : WAREHOUSE_LAG_DAYS;
  var movable = rec.open_pos.filter(function(p){
    return ['MOVABLE','PULL_UP_NARROW','FASTER_VESSEL_WINDOW'].indexOf(p.status)>=0;
  });

  var rawProposals = [];
  for (var gi=0; gi<actionableGaps.length; gi++) {
    var gap = actionableGaps[gi];
    if (!movable.length) continue;
    var gapWkStart = wkSunday(today, gap.wi-1);
    var targetETA  = addDays(gapWkStart, -whLag);
    var candidates = movable.slice().sort(function(a,b){
      var da=a.etd_obj?a.etd_obj:new Date('9999-12-31');
      var db=b.etd_obj?b.etd_obj:new Date('9999-12-31');
      return da-db;
    });
    var found=false;
    for (var ci=0; ci<candidates.length && !found; ci++) {
      var po = candidates[ci];
      if (!po.etd_obj) continue;
      var transit = po.transit_days || rec.transit_days || 26;
      var minEtd = addDays(today, ETD_LOCK_DAYS);
      var dtd = Math.round((po.etd_obj - today) / 86400000);
      var inFast = dtd>=8 && dtd<=FASTER_VESSEL_HI;
      if (inFast && !isUSA) {
        var propETA = addDays(po.etd_obj, FAST_VESSEL_TRANSIT);
        if (propETA <= addDays(targetETA, 3)) {
          rawProposals.push({ po:po, newEtd:po.etd_obj, gapWks:[gap.wi], action:'FASTER_VESSEL' });
          found=true; break;
        }
      }
      var rawNewEtd = addDays(targetETA, -transit);
      var newEtd = rawNewEtd > minEtd ? rawNewEtd : minEtd;
      if (newEtd >= po.etd_obj) continue;
      var pullupDays = Math.round((po.etd_obj - newEtd) / 86400000);
      if (pullupDays < MIN_PULLUP_DAYS) continue;
      rawProposals.push({ po:po, newEtd:newEtd, gapWks:[gap.wi], action:'PULL_UP' });
      found=true;
    }
  }

  // Deduplicate by (action, po_number)
  var grouped = {};
  for (var pi=0; pi<rawProposals.length; pi++) {
    var rp = rawProposals[pi];
    var key = rp.action+'|'+rp.po.po_number;
    if (!grouped[key]) grouped[key]={ po:rp.po, newEtd:rp.newEtd, gapWks:rp.gapWks.slice(), action:rp.action };
    else {
      if (rp.newEtd < grouped[key].newEtd) grouped[key].newEtd=rp.newEtd;
      grouped[key].gapWks = grouped[key].gapWks.concat(rp.gapWks);
    }
  }

  rec.recommendations = [];
  var keys = Object.keys(grouped);
  for (var ki=0; ki<keys.length; ki++) {
    var g2 = grouped[keys[ki]];
    var wks = g2.gapWks.filter(function(v,i,a){return a.indexOf(v)===i;}).sort(function(a,b){return a-b;});
    var wkStr = wks.length<=4 ? wks.map(function(w){return 'W'+w;}).join(', ') : 'W'+wks[0]+'-W'+wks[wks.length-1];
    var transit2 = g2.po.transit_days || rec.transit_days || 26;
    var po2 = g2.po;
    if (g2.action==='FASTER_VESSEL') {
      rec.recommendations.push({
        action:'FASTER_VESSEL', po_number:po2.po_number, supplier:po2.supplier,
        qty_affected:po2.qty, orig_etd:po2.etd, proposed_etd:po2.etd,
        orig_eta:po2.eta, proposed_eta:fmtISO(addDays(po2.etd_obj, FAST_VESSEL_TRANSIT)),
        delta_days:0, delta_qty:0, priority:'HIGH',
        reason:'Request faster vessel - covers gaps in '+wkStr,
        in_transit_qty:po2.in_transit_qty, in_work_qty:po2.in_work_qty, po_total_qty:po2.qty,
        keep_qty:0, push_qty:0, gap_weeks_fixed:wks
      });
    } else {
      var newEta2 = addDays(g2.newEtd, transit2);
      rec.recommendations.push({
        action:'PULL_UP', po_number:po2.po_number, supplier:po2.supplier,
        qty_affected:po2.qty, orig_etd:po2.etd, proposed_etd:fmtISO(g2.newEtd),
        orig_eta:po2.eta, proposed_eta:fmtISO(newEta2),
        delta_days:Math.round((g2.newEtd-po2.etd_obj)/86400000), delta_qty:0, priority:'HIGH',
        reason:'Pull-up - covers gaps in '+wkStr,
        in_transit_qty:po2.in_transit_qty, in_work_qty:po2.in_work_qty, po_total_qty:po2.qty,
        keep_qty:0, push_qty:0, gap_weeks_fixed:wks
      });
    }
  }

  if (actionableGaps.length>0 && rec.recommendations.length===0) {
    rec.recommendations.push({
      action:'NO_LEVER', po_number:'', supplier:'', qty_affected:0,
      orig_etd:null, proposed_etd:null, orig_eta:null, proposed_eta:null,
      delta_days:0, delta_qty:0, priority:'HIGH',
      reason:actionableGaps.length+' actionable gap weeks but no movable PO can land in time.',
      in_transit_qty:0, in_work_qty:0, po_total_qty:0, keep_qty:0, push_qty:0, gap_weeks_fixed:[]
    });
  }

  // Overstock recs
  var partMin = Math.max(rec.moq>0 ? Math.floor(rec.moq/2) : PARTIAL_MIN_PCS, 1);
  if (rec.overstocked && rec.open_pos.length>0) {
    var furthest = rec.open_pos.reduce(function(best,p){
      var bd=best.etd_obj||new Date(0), pd=p.etd_obj||new Date(0);
      return pd>bd?p:best;
    }, rec.open_pos[0]);
    var excess = Math.max(0, rec.pipeline_excess);
    var fEtd = furthest.etd_obj||null;
    var horizDays = fEtd ? Math.round((fEtd-today)/86400000) : 0;
    var transit3 = furthest.transit_days||rec.transit_days||26;
    if (fEtd && horizDays>CANCEL_HORIZON_DAYS) {
      var cancelQ = Math.min(excess, furthest.qty);
      if (cancelQ>=partMin) {
        rec.recommendations.push({
          action:'CANCEL', po_number:furthest.po_number, supplier:furthest.supplier,
          qty_affected:cancelQ, orig_etd:furthest.etd, proposed_etd:furthest.etd,
          orig_eta:furthest.eta, proposed_eta:furthest.eta,
          delta_days:0, delta_qty:-cancelQ, priority:'MEDIUM',
          reason:'Pipeline excess '+excess.toLocaleString()+' pcs / '+furthest.po_number+' ETD is >60d out -> cancel '+cancelQ.toLocaleString()+' pcs',
          in_transit_qty:furthest.in_transit_qty, in_work_qty:furthest.in_work_qty, po_total_qty:furthest.qty,
          keep_qty:0, push_qty:0, gap_weeks_fixed:[]
        });
      }
    } else if (fEtd) {
      var pushQ = Math.min(excess, furthest.qty);
      var newEtd3 = addWeeks(fEtd, 8);
      var newEta3 = addDays(newEtd3, transit3);
      var ddelta = Math.round((newEtd3-fEtd)/86400000);
      if (pushQ>=partMin) {
        var keepQ = furthest.qty-pushQ;
        if (keepQ>=partMin) {
          rec.recommendations.push({
            action:'SPLIT', po_number:furthest.po_number, supplier:furthest.supplier,
            qty_affected:pushQ, orig_etd:furthest.etd, proposed_etd:fmtISO(newEtd3),
            orig_eta:furthest.eta, proposed_eta:fmtISO(newEta3),
            delta_days:ddelta, delta_qty:0, priority:'MEDIUM',
            reason:'Split shipment - keep '+keepQ.toLocaleString()+' at original ETD, push '+pushQ.toLocaleString()+' to free up early-window inventory',
            in_transit_qty:furthest.in_transit_qty, in_work_qty:furthest.in_work_qty, po_total_qty:furthest.qty,
            keep_qty:keepQ, push_qty:pushQ, gap_weeks_fixed:[]
          });
        } else {
          rec.recommendations.push({
            action:'PUSH_OUT', po_number:furthest.po_number, supplier:furthest.supplier,
            qty_affected:furthest.qty, orig_etd:furthest.etd, proposed_etd:fmtISO(newEtd3),
            orig_eta:furthest.eta, proposed_eta:fmtISO(newEta3),
            delta_days:ddelta, delta_qty:0, priority:'MEDIUM',
            reason:'Push entire PO out ~8 weeks - splitting would leave partial below MOQ/2 = '+partMin.toLocaleString(),
            in_transit_qty:furthest.in_transit_qty, in_work_qty:furthest.in_work_qty, po_total_qty:furthest.qty,
            keep_qty:0, push_qty:furthest.qty, gap_weeks_fixed:[]
          });
        }
      }
    }
  }

  // OOS Priority rollup — volume-weighted waterfall (first match wins)
  var _gc  = rec.gap_weeks.length;
  var _vel = rec.shp_wk_l13;
  var _fd  = /future.?delete/i.test(rec.item_status) || /future.?delete/i.test(rec.sub_status);
  if      (_fd || _gc === 0)      rec.priority = 'NO_OOS';
  else if (_vel > 500 && _gc >= 3) rec.priority = 'CRITICAL';
  else if (_vel > 200 && _gc >= 2) rec.priority = 'HIGH';
  else if (_vel > 100 && _gc >= 2) rec.priority = 'MEDIUM';
  else                             rec.priority = 'LOW';

  // Stock status badge — mirrors fcst_status pill on the projections manager view
  if (!rec.is_replen || rec.demand_26w === 0) {
    rec.stock_status = 'Inactive';
  } else if (rec.overstocked) {
    rec.stock_status = 'Over-Stocked';
  } else if (rec.gap_weeks.length > 0 || (rec.ats_wos_oh > 0 && rec.ats_wos_oh < rec.opt_wos)) {
    rec.stock_status = 'Under-Stocked';
  } else {
    rec.stock_status = 'In Stock';
  }

  // Purchase Recommendation
  // Trigger: find last week where beg_inv >= Opt_OH; trigger = next week (first sustained dip below)
  // Qty: Opt_OH - beg_inv[trigger week], floor at MOQ
  // Required receipt date: start of last-above week (one week before trigger)
  // Required ETD: receipt_date - LT_Trans_Days; display = max(required_ETD, Nxt_Avl_ETD)
  //   Exception: if Nxt_Avl_ETD is within 14 days after required_ETD, use required_ETD + flag push-supplier
  rec.purchase_rec = 0;
  rec.purchase_rec_etd = null;
  rec.purchase_rec_push_supplier = false;
  rec.purchase_rec_receipt_date = null;
  rec.purchase_rec_trigger_idx = -1;
  if (rec.is_replen && rec.prj_wk > 0 && rec.moq > 0 && rec.opt_oh > 0) {
    var _lastAbove = -1;
    for (var _i = 0; _i < 26; _i++) {
      if ((rec.beg_inv[_i] || 0) >= rec.opt_oh) _lastAbove = _i;
    }
    var _trigIdx = (_lastAbove >= 0 && _lastAbove < 25) ? _lastAbove + 1 : -1;
    if (_trigIdx >= 1 && _trigIdx <= 25) {
      var _trigInv = rec.beg_inv[_trigIdx] || 0;
      var _purTarget = rec.opt_oh + PUR_REC_BUFFER_WKS * rec.prj_wk;
      var _purGap = Math.max(0, _purTarget - _trigInv);
      if (_purGap > 0) {
        var _mp = Math.round(toNum(rec.master_pack)) || 1;
        var _purQty = Math.max(_purGap, rec.moq);
        rec.purchase_rec = _mp > 1 ? Math.ceil(_purQty / _mp) * _mp : _purQty;
        rec.purchase_rec_trigger_idx = _trigIdx;
        // Receipt needed by: start of last-above week (one week before trigger)
        var _rcptDate = wkSunday(today, _lastAbove);
        rec.purchase_rec_receipt_date = _rcptDate;
        // Required ETD = receipt date minus full LT_Trans_Days (always show formula result)
        var _reqETD = addDays(_rcptDate, -rec.lt_trans_days);
        rec.purchase_rec_etd = _reqETD;
        // Push supplier flag: supplier's next avail ETD is later than what we need
        var _nxtETD = rec.nxt_avl_etd ? new Date(rec.nxt_avl_etd) : null;
        rec.purchase_rec_push_supplier = !!(_nxtETD && _nxtETD > _reqETD);
      }
    }
  }
}

// -- Columns -------------------------------------------------------------------
var COLS = [
  { id:'priority', label:'OOS Pri', align:'left', numeric:true,
    get:function(r){return {CRITICAL:0,HIGH:1,MEDIUM:2,LOW:3,NO_OOS:4}[r.priority]!=null?{CRITICAL:0,HIGH:1,MEDIUM:2,LOW:3,NO_OOS:4}[r.priority]:9;},
    filterValue:function(r){return r.priority;},
    render:function(r){var lbl=r.priority==='NO_OOS'?'No OOS':r.priority;return '<td class="pri-'+r.priority+'">'+lbl+'</td>';} },
  { id:'stock_status', label:'Stock Status', align:'left',
    get:function(r){return {'Over-Stocked':0,'Under-Stocked':1,'In Stock':2,'Inactive':3}[r.stock_status]!=null?{'Over-Stocked':0,'Under-Stocked':1,'In Stock':2,'Inactive':3}[r.stock_status]:9;},
    filterValue:function(r){return r.stock_status;},
    render:function(r){
      var styles={'Over-Stocked':'background:#e65100;color:#fff','Under-Stocked':'background:#c62828;color:#fff','In Stock':'background:#2e7d32;color:#fff','Inactive':'background:#757575;color:#fff'};
      var st=styles[r.stock_status];
      if(!st)return'<td></td>';
      return'<td><span style="'+st+';display:inline-block;padding:2px 8px;border-radius:10px;font-size:10px;font-weight:700;white-space:nowrap">'+r.stock_status+'</span></td>';
    } },
  { id:'mstyle', label:'Mstyle', align:'left',
    get:function(r){return r.mstyle;},
    render:function(r){return '<td style="font-weight:700;color:#1a4dff;font-size:12px;white-space:nowrap;">'+esc(r.mstyle)+(r.is_multi?' <span class="badge badge-purple" title="Multi-pack (kit)">KIT</span>':'')+'</td>';} },
  { id:'description', label:'Description', align:'left',
    get:function(r){return r.description;},
    render:function(r){return '<td title="'+esc(r.description)+'"><div class="cell-clamp2">'+esc(r.description)+'</div></td>';} },
  { id:'brand', label:'Brand', align:'left',
    get:function(r){return r.brand;},
    render:function(r){return '<td title="'+esc(r.brand)+'"><div class="cell-clamp2">'+esc(r.brand)+'</div></td>';} },
  { id:'country', label:'Country', align:'left', get:function(r){return r.country;}, render:function(r){return '<td>'+esc(r.country)+'</td>';} },
  { id:'inv_manager', label:'Inv Mgr', align:'left',
    get:function(r){return r.inv_manager;},
    render:function(r){return '<td title="'+esc(r.inv_manager)+'"><div class="cell-clamp2">'+esc(r.inv_manager)+'</div></td>';} },
  { id:'status_sub', label:'Status', align:'left',
    get:function(r){return r.item_status_flow||r.item_status||'';},
    filterValue:function(r){return (r.item_status_flow||r.item_status||'');},
    render:function(r){var s=r.item_status_flow||r.item_status||'';return '<td title="'+esc(s)+'"><div class="cell-clamp2">'+esc(s)+'</div></td>';} },
  { id:'item_rank', label:'Rank', align:'left', get:function(r){return r.item_rank;}, render:function(r){return '<td>'+esc(r.item_rank)+'</td>';} },
  { id:'customer_count', label:'Cust', align:'right', numeric:true, get:function(r){return r.customer_count;}, render:function(r){return '<td class="right">'+r.customer_count+'</td>';} },
  { id:'qty_oh', label:'Qty OH', align:'right', numeric:true, get:function(r){return r.qty_oh;},
    render:function(r){return '<td class="right '+(r.qty_oh<0?'neg':'')+'">'+fmtInt(r.qty_oh)+'</td>';} },
  { id:'ats_now', label:'ATS Now', align:'right', numeric:true, get:function(r){return r.ats_now;},
    render:function(r){return '<td class="right '+(r.ats_now<0?'neg':'')+'">'+fmt(r.ats_now)+'</td>';} },
  { id:'qty_oh_root', label:'Pcs OH (root)', align:'right', numeric:true,
    get:function(r){return r.is_multi?r.qty_oh_root:-1;},
    render:function(r){return '<td class="right">'+(r.is_multi?'<b>'+fmt(r.qty_oh_root)+'</b> <span style="color:#888;font-size:10px;">(+'+fmt(r.assembleable_kits)+')</span>':'<span style="color:#bbb;">&#8212;</span>')+'</td>';} },
  { id:'it_qty', label:'I/T', align:'right', numeric:true, get:function(r){return r.it_qty;}, render:function(r){return '<td class="right">'+fmt(r.it_qty)+'</td>';} },
  { id:'iw_qty', label:'I/W', align:'right', numeric:true, get:function(r){return r.iw_qty;}, render:function(r){return '<td class="right">'+fmt(r.iw_qty)+'</td>';} },
  { id:'hold_qty', label:'Hold', align:'right', numeric:true, get:function(r){return r.hold_qty;},
    render:function(r){return '<td class="right '+(r.hold_qty>0?'pri-MEDIUM':'')+'">'+fmt(r.hold_qty)+'</td>';} },
  { id:'open_cust_po_qty', label:'Open Cust PO', align:'right', numeric:true, get:function(r){return r.open_cust_po_qty;}, render:function(r){return '<td class="right">'+fmt(r.open_cust_po_qty)+'</td>';} },
  { id:'shp_wk_l4', label:'Shpd/Wk L4', align:'right', numeric:true, get:function(r){return r.shp_wk_l4;}, render:function(r){return '<td class="right">'+fmt(r.shp_wk_l4)+'</td>';} },
  { id:'shp_wk_l13', label:'Shpd/Wk L13', align:'right', numeric:true, get:function(r){return r.shp_wk_l13;}, render:function(r){return '<td class="right">'+fmt(r.shp_wk_l13)+'</td>';} },
  { id:'prj_wk', label:'Prj/Wk', align:'right', numeric:true, get:function(r){return r.prj_wk;}, render:function(r){return '<td class="right">'+fmtInt(r.prj_wk)+'</td>';} },
  { id:'prj_l4w_change', label:'+/- L4w', align:'right', numeric:true, get:function(r){return r.prj_l4w_change;},
    render:function(r){var l4w=r.prj_l4w_change;var up=l4w>5,dn=l4w<-5;var clr=up?'#2e7d32':dn?'#c62828':'inherit';var arr=up?'&#9650;':dn?'&#9660;':'';return '<td class="right" style="color:'+clr+';font-weight:'+(up||dn?'600':'400')+'">'+(arr?arr+' ':'')+fmt(l4w)+'%</td>';} },
  { id:'opt_wos', label:'Opt WOS', align:'right', numeric:true, get:function(r){return r.opt_wos;}, render:function(r){return '<td class="right">'+fmt(r.opt_wos)+'</td>';} },
  { id:'ats_wos_oh', label:'ATS WOS', align:'right', numeric:true, get:function(r){return r.ats_wos_oh;},
    render:function(r){return '<td class="right '+(r.ats_wos_oh>0&&r.ats_wos_oh<r.opt_wos?'pri-HIGH':'')+'">'+fmt(r.ats_wos_oh)+'</td>';} },
  { id:'ats_wos_oh_oo', label:'ATS WOS OH+OO', align:'right', numeric:true, get:function(r){return r.ats_wos_oh_oo;},
    render:function(r){return '<td class="right '+(r.ats_wos_oh_oo>0&&r.ats_wos_oh_oo<r.opt_wos?'pri-HIGH':'')+'">'+fmt(r.ats_wos_oh_oo)+'</td>';} },
  { id:'opt_oh', label:'Opt OH', align:'right', numeric:true, get:function(r){return r.opt_oh;}, render:function(r){return '<td class="right">'+fmtInt(r.opt_oh)+'</td>';} },
  { id:'lt_wks', label:'LT Wks', align:'right', numeric:true, get:function(r){return r.lt_wks;}, render:function(r){return '<td class="right">'+fmt(r.lt_wks)+'</td>';} },
  { id:'cny_weeks', label:'CNY', align:'right', numeric:true, get:function(r){return r.cny_weeks;}, render:function(r){return '<td class="right">'+fmt(r.cny_weeks)+'</td>';} },
  { id:'days_oos_next_rcpt', label:'Days OOS&#8594;Rcpt', tooltip:'Days OOS until Next Available Receipt', align:'right', numeric:true, get:function(r){return r.days_oos_next_rcpt;},
    render:function(r){return '<td class="right '+(r.days_oos_next_rcpt>0?'pri-CRITICAL':'')+'">'+fmt(r.days_oos_next_rcpt)+'</td>';} },
  { id:'next_rcpt_dt', label:'Next Rcpt', align:'left',
    get:function(r){return r.next_rcpt_dt?r.next_rcpt_dt.toISOString():'zzzz';},
    render:function(r){return '<td>'+fmtDate(r.next_rcpt_dt)+'</td>';} },
  { id:'gap_weeks_n', label:'OOS Wks', align:'right', numeric:true,
    get:function(r){return r.gap_weeks.length;},
    render:function(r){return '<td class="right '+(r.gap_weeks.length>0?'pri-CRITICAL':'')+'">'+r.gap_weeks.length+'</td>';} },
  { id:'oh_excess', label:'OH Excess', align:'right', numeric:true,
    get:function(r){return r.oh_excess;},
    tooltip:'OH Excess = Qty OH minus cumulative projected demand up to the Next Available Receipt date.\nNegative = expected OOS before receipt arrives.',
    render:function(r){return '<td class="right '+(r.oh_excess<0?'pri-CRITICAL':r.oh_excess>2500?'pri-HIGH':'')+'">'+fmtInt(r.oh_excess)+'</td>';} },
  { id:'pipeline_excess', label:'OH+OO Excess', align:'right', numeric:true,
    get:function(r){return r.pipeline_excess;},
    tooltip:'OH+OO Excess = (Qty OH + IT + IW + all open POs) minus cumulative projected demand up to the Next Available Receipt date.\nNegative = pipeline short. > 2,500 triggers Overstock flag.',
    render:function(r){return '<td class="right '+(r.pipeline_excess<0?'pri-CRITICAL':r.pipeline_excess>2500?'pri-HIGH':'')+'">'+fmtInt(r.pipeline_excess)+'</td>';} },
  { id:'pipeline_wos', label:'OH+OO WOS', align:'right', numeric:true,
    get:function(r){return r.pipeline_wos==null?1e9:r.pipeline_wos;},
    render:function(r){return '<td class="right">'+(r.pipeline_wos==null?'&#8734;':fmt(r.pipeline_wos))+'</td>';} },
  { id:'action', label:'Action', align:'left',
    get:function(r){if(!r.recommendations.length)return'CLEAN';var c={};r.recommendations.forEach(function(rc){c[rc.action]=(c[rc.action]||0)+1;});return Object.keys(c).sort(function(a,b){return c[b]-c[a];})[0];},
    render:function(r){return '<td>'+actionTag(r)+'</td>';} },
  { id:'purchase_rec', label:'Pur Rec', align:'right', numeric:true,
    tooltip:'Qty to order now so projected inv at Next Avl Rcpt Dt meets Opt OH target.\nETD = Nxt Avl ETD field (soonest available ETD date).',
    get:function(r){return r.purchase_rec||0;},
    render:function(r){
      if(!r.purchase_rec)return'<td class="right" style="color:#bbb;">&#8212;</td>';
      var tip=fmtInt(r.purchase_rec)+' units'+(r.purchase_rec_etd?' | Nxt Avl ETD: '+fmtDate(r.purchase_rec_etd):'');
      return'<td class="right" title="'+tip+'" style="font-weight:700;color:#1a237e;">'+fmtInt(r.purchase_rec)+'</td>';
    }
  }
];

function actionTag(r) {
  if (!r.recommendations.length) return '<span class="badge badge-green">CLEAN</span>';
  var counts={};
  r.recommendations.forEach(function(rc){counts[rc.action]=(counts[rc.action]||0)+1;});
  var top=Object.keys(counts).sort(function(a,b){return counts[b]-counts[a];})[0];
  var cls={PULL_UP:'badge-amber',FASTER_VESSEL:'badge-purple',PUSH_OUT:'badge-amber',SPLIT:'badge-amber',CANCEL:'badge-red',NO_LEVER:'badge-gray'}[top]||'badge-gray';
  return '<span class="badge '+cls+'">'+top+'</span>'+(r.recommendations.length>1?' <span style="color:#888;font-size:10px;">+'+( r.recommendations.length-1)+'</span>':'');
}

var COL_WIDTHS = {
  priority:66, stock_status:88, mstyle:84, description:120, brand:68, country:44, inv_manager:68,
  status_sub:72, item_rank:34, customer_count:32,
  qty_oh:48, ats_now:48, qty_oh_root:56,
  it_qty:38, iw_qty:38, hold_qty:38, open_cust_po_qty:52,
  shp_wk_l4:44, shp_wk_l13:46, prj_wk:40, prj_l4w_change:44,
  opt_wos:40, ats_wos_oh:44, ats_wos_oh_oo:52, opt_oh:44, lt_wks:38, cny_weeks:32,
  days_oos_next_rcpt:46, next_rcpt_dt:54, gap_weeks_n:38,
  oh_excess:54, pipeline_excess:58, pipeline_wos:48, action:96, purchase_rec:60
};

function visibleCols() {
  var hideMulti=(document.getElementById('hideMulti')||{}).checked;
  return COLS.filter(function(c){return !(hideMulti&&c.id==='qty_oh_root');});
}

// -- Table rendering -----------------------------------------------------------
function buildTableHead() {
  var head=document.getElementById('theadMain');
  var cols=visibleCols();

  // Colgroup — controls column widths for the whole table
  var tbl=document.getElementById('mainTable');
  var cg=tbl.querySelector('colgroup');
  if(!cg){cg=document.createElement('colgroup');tbl.insertBefore(cg,tbl.firstChild);}
  var totalW=cols.reduce(function(s,c){return s+(COL_WIDTHS[c.id]||62);},0);
  cg.innerHTML=cols.map(function(c){return '<col style="width:'+(COL_WIDTHS[c.id]||62)+'px">';}).join('');
  tbl.style.width=totalW+'px';

  var h1='<tr class="sort-row">';
  cols.forEach(function(c){
    var a=c.align==='right'?' class="right"':'';
    var arrow=(currentSort.id===c.id)?'<span class="sort-arrow">'+(currentSort.dir>0?'&#9650;':'&#9660;')+'</span>':'';
    var tip=c.tooltip?' title="'+c.tooltip.replace(/"/g,'&quot;')+'"':'';
    h1+='<th'+a+tip+' data-col="'+c.id+'"><span class="col-label">'+c.label+'</span>'+arrow+'</th>';
  });
  h1+='</tr>';
  var h2='<tr class="filter-row">';
  cols.forEach(function(c){
    var a=c.align==='right'?' class="right"':'';
    var v=colFilters[c.id]||'';
    h2+='<th'+a+'><input data-filter="'+c.id+'" type="text" placeholder="filter..." value="'+esc(v)+'"></th>';
  });
  h2+='</tr>';
  head.innerHTML=h2+h1;
  head.querySelectorAll('th[data-col]').forEach(function(th){
    th.onclick=function(){
      var id=th.dataset.col;
      if(currentSort.id===id)currentSort.dir=-currentSort.dir;
      else{currentSort.id=id;currentSort.dir=1;}
      buildTableHead();applyFilters();
    };
  });
  head.querySelectorAll('input[data-filter]').forEach(function(inp){
    inp.oninput=function(){var id=inp.dataset.filter,v=inp.value.trim();if(v)colFilters[id]=v;else delete colFilters[id];applyFilters();};
    inp.onclick=function(e){e.stopPropagation();};
  });
  // Fix sort-row sticky offset: measure filter-row actual height so sort row sits precisely below it
  setTimeout(function(){
    var filterTh=head.querySelector('tr.filter-row th');
    var filterH=filterTh?filterTh.offsetHeight:22;
    head.querySelectorAll('tr.sort-row th').forEach(function(th){th.style.top=filterH+'px';});
  },0);
}

function buildFilterDropdowns() {
  var countries=new Set(),brands=new Set(),mgrs=new Set();
  ALL.forEach(function(r){if(r.country)countries.add(r.country);if(r.brand)brands.add(r.brand);if(r.inv_manager)mgrs.add(r.inv_manager);});
  function toItems(set){return Array.from(set).sort().map(function(v){return{v:v};});}
  buildDdPanel('dd-country', toItems(countries), 'All Countries', function(s){selCountries=s;});
  buildDdPanel('dd-brand',   toItems(brands),    'All Brands',    function(s){selBrands=s;});
  buildDdPanel('dd-mgr',     toItems(mgrs),      'All Inv Mgrs',  function(s){selMgrs=s;});
  updateDdBtn('dd-country','All Countries');
  updateDdBtn('dd-brand',  'All Brands');
  updateDdBtn('dd-mgr',    'All Inv Mgrs');
}

function _chk(id,def){var el=document.getElementById(id);return el?el.checked:(def===undefined?false:def);}
function _filterRecords(skipPri) {
  var q=(document.getElementById('searchInput')||{value:''}).value.toLowerCase().trim();
  var gaps=_chk('gapsOnly',false);
  var over=_chk('overstockOnly',false);
  var hideInactive=_chk('hideInactive',true);
  var activeCols=[];
  Object.keys(colFilters).forEach(function(cid){var c=COLS.find(function(x){return x.id===cid;});if(c)activeCols.push({c:c,needle:colFilters[cid].toLowerCase()});});
  return ALL.filter(function(r){
    if(q){var hay=(r.mstyle+' '+r.description+' '+r.brand).toLowerCase();if(hay.indexOf(q)<0)return false;}
    if(selCountries.size>0&&!selCountries.has(r.country))return false;
    if(selBrands.size>0&&!selBrands.has(r.brand))return false;
    if(selMgrs.size>0&&!selMgrs.has(r.inv_manager))return false;
    if(hideInactive&&!r.is_replen)return false;
    if(gaps&&r.gap_weeks.length===0)return false;
    if(over&&!r.overstocked)return false;
    if(selActions.size>0){
      if(selActions.has('__NONE__')){if(r.recommendations.length!==0)return false;}
      else{if(!r.recommendations.some(function(rc){return selActions.has(rc.action);}))return false;}
    }
    if(!skipPri&&selPriorities.size>0&&!selPriorities.has(r.priority))return false;
    if(selStockStatus.size>0&&!selStockStatus.has(r.stock_status))return false;
    for(var i=0;i<activeCols.length;i++){var ac=activeCols[i];var val=ac.c.filterValue?ac.c.filterValue(r):ac.c.get(r);if(val==null)return false;if(String(val).toLowerCase().indexOf(ac.needle)<0)return false;}
    return true;
  });
}

function applyFilters() {
  FILTERED=_filterRecords(false);
  var cmpCol=function(c,a,b,dir){var va=c.get(a),vb=c.get(b);if(c.numeric)return(Number(va)-Number(vb))*dir;var sa=String(va==null?'':va).toLowerCase(),sb=String(vb==null?'':vb).toLowerCase();return sa<sb?-dir:sa>sb?dir:0;};
  if(currentSort.id){
    var sc=COLS.find(function(c){return c.id===currentSort.id;});
    if(sc){var dir=currentSort.dir;FILTERED.sort(function(a,b){return cmpCol(sc,a,b,dir);});}
  } else {
    var chain=DEFAULT_SORT_CHAIN.map(function(id){return COLS.find(function(c){return c.id===id;});}).filter(Boolean);
    FILTERED.sort(function(a,b){for(var i=0;i<chain.length;i++){var r2=cmpCol(chain[i],a,b,1);if(r2!==0)return r2;}return 0;});
  }
  renderStats();renderTable();
}

function renderStats() {
  var npf=_filterRecords(true);
  var inScope=FILTERED.length,gapsN=FILTERED.filter(function(r){return r.gap_weeks.length>0;}).length;
  var overN=FILTERED.filter(function(r){return r.overstocked;}).length;
  var pri={CRITICAL:0,HIGH:0,MEDIUM:0,LOW:0,NO_OOS:0};
  npf.forEach(function(r){pri[r.priority]=(pri[r.priority]||0)+1;});
  var total=npf.length;
  var PRI_TIPS={CRITICAL:'CRITICAL: L13W >500/wk with 3+ OOS gap weeks.',HIGH:'HIGH: L13W >200/wk with 2+ OOS gap weeks.',MEDIUM:'MEDIUM: L13W >100/wk with 2+ OOS gap weeks.',LOW:'LOW: has OOS gaps, L13W ≤200/wk, Active status (not Future Delete).',NO_OOS:'No OOS: Future Delete status or zero gap weeks — no action needed.'};
  function btn(key,label,color){var active=selPriorities.has(key);return '<button class="pri-btn '+(active?'active':'')+'" data-pri="'+key+'" title="'+PRI_TIPS[key]+'" style="background:'+(active?color:'#ffffff')+';color:'+(active?'#fff':color)+';border:1.5px solid '+color+';">'+label+' <b style="margin-left:4px;">'+(pri[key]||0).toLocaleString()+'</b></button>';}
  var allActive=selPriorities.size===0;
  var allBtn='<button class="pri-btn '+(allActive?'active':'')+'" data-pri="__ALL__" title="All OOS priorities" style="background:'+(allActive?'#37474f':'#ffffff')+';color:'+(allActive?'#fff':'#37474f')+';border:1.5px solid #37474f;">All <b style="margin-left:4px;">'+total.toLocaleString()+'</b></button>';
  document.getElementById('statsBar').innerHTML=allBtn+btn('CRITICAL','&#128308; Critical','#b71c1c')+btn('HIGH','&#128992; High','#e65100')+btn('MEDIUM','&#129001; Medium','#f9a825')+btn('LOW','&#9898; Low','#5d4037')+btn('NO_OOS','&#9898; No OOS','#9e9e9e')+'<div class="stat" style="margin-left:14px;"><b>'+gapsN+'</b> OOS Risk</div><div class="stat"><b>'+overN+'</b> Overstocked</div><div class="stat"><b>'+inScope.toLocaleString()+'</b> Shown</div>';
  document.getElementById('statsBar').querySelectorAll('.pri-btn').forEach(function(b){b.onclick=function(){
    var key=b.dataset.pri;
    if(key==='__ALL__'){selPriorities.clear();}
    else{if(selPriorities.has(key))selPriorities.delete(key);else selPriorities.add(key);}
    syncPriorityDd();applyFilters();
  };});
}
function syncPriorityDd(){
  var panel=document.querySelector('#dd-priority .ms-dd-panel');
  if(!panel)return;
  panel.querySelectorAll('input[type=checkbox]').forEach(function(cb){cb.checked=selPriorities.has(cb.value);});
  updateDdBtn('dd-priority','All OOS Pri');
}

function renderTable() {
  var tb=document.getElementById('tbody');
  var cols=visibleCols();var nCols=cols.length;
  var html='';
  FILTERED.forEach(function(r){
    var safeMs=r.mstyle.replace(/[^a-zA-Z0-9]/g,'_');
    html+='<tr class="row row-'+r.priority+'" data-ms="'+esc(r.mstyle)+'" onclick="toggleDetail(this.dataset.ms)">';
    cols.forEach(function(c){html+=c.render(r);});
    html+='</tr>';
    html+='<tr class="detail-pane" id="detail-'+safeMs+'" style="display:none"><td colspan="'+nCols+'"></td></tr>';
  });
  tb.innerHTML=html;
}

function toggleDetail(mstyle) {
  var id='detail-'+mstyle.replace(/[^a-zA-Z0-9]/g,'_');
  var dtr=document.getElementById(id);
  if(!dtr)return;
  if(dtr.style.display==='table-row'){dtr.style.display='none';return;}
  dtr.style.display='table-row';
  if(dtr.dataset.loaded==='1')return;
  var r=ALL.find(function(x){return x.mstyle===mstyle;});
  if(!r)return;
  dtr.querySelector('td').innerHTML=renderDetail(r);
  dtr.dataset.loaded='1';
}

// Re-render the detail panel for a given mstyle (e.g. after supplier selection or needQty change)
function renderDetailForMstyle(mstyle) {
  var id = 'detail-' + mstyle.replace(/[^a-zA-Z0-9]/g, '_');
  var dtr = document.getElementById(id);
  if (!dtr || dtr.style.display !== 'table-row') return;
  var r = ALL.find(function(x) { return x.mstyle === mstyle; });
  if (!r) return;
  dtr.querySelector('td').innerHTML = renderDetail(r);
  dtr.dataset.loaded = '1';
}

// -- renderDetail --------------------------------------------------------------
function renderDetail(r) {
  var today=new Date();today.setHours(0,0,0,0);
  var isUSA=/^(usa|united states)$/i.test(r.country||'');
  var lag=isUSA?USA_WAREHOUSE_LAG:WAREHOUSE_LAG_DAYS;
  var w1sun=new Date(today);w1sun.setDate(today.getDate()-today.getDay());

  // Purchase recommendation detail vars
  var purTrigIdx    = r.purchase_rec_trigger_idx;  // 0-based index of trigger week (e.g. 15 = Wk16)
  var purTrigWk     = purTrigIdx >= 0 ? purTrigIdx + 1 : 0;  // 1-based week label
  var purTrigInv    = purTrigIdx >= 0 ? (r.beg_inv[purTrigIdx] || 0) : 0;
  var purBufUnits   = PUR_REC_BUFFER_WKS * r.prj_wk;
  var purTarget     = r.opt_oh + purBufUnits;
  var purGap        = Math.max(0, purTarget - purTrigInv);

  // Map POs to forecast weeks via ETA + warehouse lag
  var poByWeek={};
  (r.open_pos||[]).forEach(function(p){
    if(!p.eta_obj&&!p.eta)return;
    var etaDt=p.eta_obj||(p.eta?new Date(p.eta):null);
    if(!etaDt)return;
    var wh=addDays(etaDt,lag);
    var wi=Math.floor((wh-w1sun)/86400000/7);
    if(wi<0||wi>25)return;
    if(!poByWeek[wi])poByWeek[wi]=[];
    poByWeek[wi].push(p);
  });

  var allOpenPos = r.open_pos||[];
  function fmtPoHover(wi){
    var wkVal=r.rcv[wi]||0;if(wkVal===0&&allOpenPos.length===0)return'';
    var wkDate=(new Date(w1sun.getTime()+wi*7*86400000)).toLocaleDateString('en-US',{month:'short',day:'numeric'});
    var h='Week of '+wkDate+' | Expected: '+fmt(wkVal)+' units\n'+('-').repeat(40)+'\n';
    if(!allOpenPos.length){h+='(no open POs)';return h;}
    allOpenPos.forEach(function(p){
      h+='PO: '+p.po_number+' | Supplier: '+(p.supplier||'--')+'\n';
      h+='  I/W: '+fmt(p.in_work_qty||0)+' | I/T: '+fmt(p.in_transit_qty||0);
      h+=' | ETD: '+(p.etd||'--')+' | ETA: '+(p.eta||'--')+'\n';
    });
    return h;
  }
  function fmtPrjHover(wi){
    var cd=r.customer_demand||[];
    var wk=(new Date(w1sun.getTime()+wi*7*86400000)).toLocaleDateString('en-US',{month:'short',day:'numeric'});
    var h='Prj Demand - W'+(wi+1)+' (week of '+wk+')\nPer-customer rollup from QB Projections:\n\n';
    var sorted=cd.map(function(c){return{customer:c.customer,qty:(c.weekly&&c.weekly[wi])||0};}).filter(function(c){return c.qty!==0;}).sort(function(a,b){return b.qty-a.qty;});
    if(!sorted.length)h+='(no per-customer projection for this week)';
    sorted.forEach(function(c){h+='- '+(c.customer||'(unknown)')+': '+fmt(c.qty)+'\n';});
    return h;
  }

  // Rec action by PO for rcv row highlighting
  var recByPo={};
  (r.recommendations||[]).forEach(function(rc){if(rc.action==='PUSH_OUT'||rc.action==='PULL_UP')recByPo[rc.po_number]=rc.action;});
  var weekAction={};
  Object.keys(poByWeek).forEach(function(wi){poByWeek[wi].forEach(function(p){var action=recByPo[p.po_number];if(action&&(!weekAction[wi]||action==='PULL_UP'))weekAction[wi]=action;});});
  function rcvHL(wi){var a=weekAction[wi];if(a==='PUSH_OUT')return'background:#fff3e0;color:#e65100;font-weight:600;';if(a==='PULL_UP')return'background:#e3f2fd;color:#1565c0;font-weight:600;';return null;}

  // 26-week grid
  var invFlow='<table class="subtbl grid26"><tr style="background:#ede9fe;"><th class="lbl" style="background:#ede9fe;"></th>';
  for(var i=1;i<=26;i++){var s=new Date(w1sun.getTime()+(i-1)*7*86400000);var lbl=(s.getMonth()+1)+'/'+s.getDate();invFlow+='<th title="W'+i+' - week of '+s.toLocaleDateString('en-US',{month:'short',day:'numeric',year:'numeric'})+'">'+lbl+'</th>';}
  invFlow+='<th>Total</th></tr>';

  function renderRow(label,arr,hoverFn,hlFn,rowBg,showTotal){
    var rStyle=rowBg?' style="background:'+rowBg+';"':'';
    var html='<tr'+rStyle+'><td class="lbl"'+(rowBg?' style="background:'+rowBg+';"':'')+'>'+label+'</td>';var tot=0;
    for(var i=0;i<26;i++){
      var v=arr[i]||0;tot+=v;var c=v<0?'neg':'ok';
      var styleStr='',extra='';
      if(hoverFn){var tip=hoverFn(i);if(tip){extra+=' title="'+tip.replace(/"/g,'&quot;')+'"';styleStr+='cursor:help;';}}
      if(hlFn){var hl=hlFn(i);if(hl)styleStr+=hl;}
      var sa=styleStr?' style="'+styleStr+'"':'';
      html+='<td class="'+c+'"'+extra+sa+'>'+(v===0?'&#8212;':fmt(v))+'</td>';
    }
    html+='<td>'+(showTotal===false?'':'<b>'+fmt(tot)+'</b>')+'</td></tr>';return html;
  }
  invFlow+=renderRow('Beg Inv',r.beg_inv,null,null,'#dbeafe',false);
  invFlow+=renderRow('Expected Receipts',r.rcv,fmtPoHover,rcvHL,'#dcfce7');
  invFlow+=renderRow('Prj Demand',r.prj,fmtPrjHover,null,'#fef9c3');
  var wosRow='<tr style="background:#fce7f3;"><td class="lbl" style="background:#fce7f3;">WOS OH</td>';
  for(var i=0;i<26;i++){var b=r.beg_inv[i]||0,p=r.prj[i]||0;var v='&#8212;',cls='ok';if(p>0){var w=b/p;v=w.toFixed(1);if(w<r.opt_wos)cls='gap';if(w<0)cls='neg';}else if(b>0){v='&#8734;';}wosRow+='<td class="'+cls+'">'+v+'</td>';}
  wosRow+='<td></td></tr>';
  invFlow+=wosRow+'</table>';

  // Open POs
  var pos='<table class="subtbl"><tr><th>PO #</th><th>Supplier</th><th class="right">I/T</th><th class="right">I/W</th><th>ETD</th><th>ETA</th><th class="right">Transit</th><th>Status</th></tr>';
  r.open_pos.forEach(function(p){
    var sc={LOCKED:'badge-gray',IN_TRANSIT:'badge-purple',MOVABLE:'badge-green',FASTER_VESSEL_WINDOW:'badge-amber',PULL_UP_NARROW:'badge-amber'}[p.status]||'badge-gray';
    pos+='<tr><td><b>'+esc(p.po_number)+'</b></td><td>'+esc(p.supplier)+'</td><td class="right">'+fmt(p.in_transit_qty)+'</td><td class="right">'+fmt(p.in_work_qty)+'</td><td>'+fmtDate(p.etd)+'</td><td>'+fmtDate(p.eta)+'</td><td class="right">'+(p.transit_days||'&#8212;')+'d</td><td><span class="badge '+sc+'">'+p.status+'</span></td></tr>';
  });
  if(!r.open_pos.length)pos+='<tr><td colspan="8" style="color:#888;font-style:italic;">No open POs.</td></tr>';
  pos+='</table>';

  // Recommendations
  var recs='';
  if(!r.recommendations.length){recs='<div style="color:#1b5e20;font-style:italic;">&#10003; No actions recommended.</div>';}
  else {
    var arrow='<span style="color:#888;margin:0 4px;">&#8594;</span>';
    function beforeAfter(before,after,isDate){var a=isDate?fmtDate(before):fmt(before);var b=isDate?fmtDate(after):fmt(after);if(before===after)return'<span>'+a+'</span>';return'<span style="color:#888;text-decoration:line-through;">'+a+'</span>'+arrow+'<span style="color:#1565c0;font-weight:600;">'+b+'</span>';}
    r.recommendations.forEach(function(rc){
      var cls='priority-'+rc.priority+' action-'+rc.action;
      var header='<div style="display:flex;align-items:baseline;gap:10px;flex-wrap:wrap;"><span class="rec-action '+rc.action+'">'+rc.action+'</span><b style="font-size:13px;">'+esc(rc.po_number||'&#8212;')+'</b>'+(rc.supplier?'<span style="color:#555;font-size:11px;">/ '+esc(rc.supplier)+'</span>':'')+'</div>';
      var body='';
      if(rc.action==='PULL_UP'||rc.action==='FASTER_VESSEL'){
        body+='<div class="rec-row"><span class="rec-lbl">Current qty:</span> <b>'+fmt(rc.po_total_qty)+'</b> pcs <span style="color:#888;">(I/T '+fmt(rc.in_transit_qty)+', I/W '+fmt(rc.in_work_qty)+')</span></div>';
        body+='<div class="rec-row"><span class="rec-lbl">ETD:</span> '+beforeAfter(rc.orig_etd,rc.proposed_etd,true)+'</div>';
        body+='<div class="rec-row"><span class="rec-lbl">ETA:</span> '+beforeAfter(rc.orig_eta,rc.proposed_eta,true)+(rc.delta_days?'<span style="color:#888;margin-left:8px;">('+( rc.delta_days>0?'+':'')+rc.delta_days+' days)</span>':'')+'</div>';
        if(rc.action==='FASTER_VESSEL')body+='<div class="rec-row" style="color:#5e35b1;font-style:italic;font-size:11px;">Same ETD - request faster vessel only (transit ~18 days vs ~26)</div>';
      } else if(rc.action==='SPLIT'){
        body+='<div class="rec-row"><span class="rec-lbl">Original PO:</span> <b>'+fmt(rc.po_total_qty)+'</b> pcs / ETD '+fmtDate(rc.orig_etd)+' &#8594; ETA '+fmtDate(rc.orig_eta)+' <span style="color:#888;">(I/T '+fmt(rc.in_transit_qty)+', I/W '+fmt(rc.in_work_qty)+')</span></div>';
        body+='<div class="rec-row"><span class="rec-lbl">Keep:</span> <b style="color:#1b5e20;">'+fmt(rc.keep_qty)+'</b> pcs at original ETD '+fmtDate(rc.orig_etd)+' &#8594; ETA '+fmtDate(rc.orig_eta)+'</div>';
        body+='<div class="rec-row"><span class="rec-lbl">Push:</span> <b style="color:#e65100;">'+fmt(rc.push_qty)+'</b> pcs to new ETD '+fmtDate(rc.proposed_etd)+' &#8594; ETA '+fmtDate(rc.proposed_eta)+' <span style="color:#888;margin-left:6px;">(+'+rc.delta_days+' days)</span></div>';
      } else if(rc.action==='PUSH_OUT'){
        body+='<div class="rec-row"><span class="rec-lbl">Current qty:</span> <b>'+fmt(rc.po_total_qty)+'</b> pcs <span style="color:#888;">(I/T '+fmt(rc.in_transit_qty)+', I/W '+fmt(rc.in_work_qty)+')</span></div>';
        body+='<div class="rec-row"><span class="rec-lbl">ETD:</span> '+beforeAfter(rc.orig_etd,rc.proposed_etd,true)+'</div>';
        body+='<div class="rec-row"><span class="rec-lbl">ETA:</span> '+beforeAfter(rc.orig_eta,rc.proposed_eta,true)+(rc.delta_days?'<span style="color:#888;margin-left:8px;">(+'+rc.delta_days+' days)</span>':'')+'</div>';
      } else if(rc.action==='CANCEL'){
        body+='<div class="rec-row"><span class="rec-lbl">Original PO:</span> <b>'+fmt(rc.po_total_qty)+'</b> pcs / ETD '+fmtDate(rc.orig_etd)+' &#8594; ETA '+fmtDate(rc.orig_eta)+'</div>';
        body+='<div class="rec-row"><span class="rec-lbl">Cancel:</span> <b style="color:#c62828;">'+fmt(rc.qty_affected)+'</b> pcs <span style="color:#888;">(remaining: '+fmt(rc.po_total_qty-rc.qty_affected)+')</span></div>';
      }
      var wks=rc.gap_weeks_fixed||[];
      var gapChip=wks.length?(wks.length<=4?'Covers gaps in <b>'+wks.map(function(w){return 'W'+w;}).join(', ')+'</b>':'Covers <b>'+wks.length+'</b> gap weeks (W'+wks[0]+'-W'+wks[wks.length-1]+')'):'';
      var reasonRow=(rc.reason&&!gapChip)?'<div class="rec-reason">'+esc(rc.reason)+'</div>':gapChip?'<div class="rec-reason">'+gapChip+'</div>':'';
      var addBtn=rc.po_number?'<div style="margin-top:8px;text-align:right;"><button class="add-excel-btn" data-mstyle="'+esc(r.mstyle)+'" data-action="'+rc.action+'" data-po="'+esc(rc.po_number||'')+'" data-supplier="'+esc(rc.supplier||'')+'" data-qty="'+(rc.po_total_qty||0)+'" data-curr-etd="'+(rc.orig_etd||'')+'" data-curr-eta="'+(rc.orig_eta||'')+'" data-req-etd="'+(rc.proposed_etd||'')+'" data-req-eta="'+(rc.proposed_eta||'')+'" onclick="addToRecoSheet(this)" style="font-size:11px;padding:3px 10px;background:#e3f2fd;color:#0d47a1;border:1px solid #90caf9;border-radius:3px;cursor:pointer;font-family:inherit;">&#10133; Add to Excel</button></div>':'';
      recs+='<div class="rec-box '+cls+'">'+header+'<div class="rec-body">'+body+'</div>'+reasonRow+addBtn+'</div>';
    });
  }

  // Gap detail
  var gapDetail='';
  if(r.gap_weeks.length){
    gapDetail='<table class="subtbl"><tr><th>Wk</th><th>Date</th><th class="right">Beg</th><th class="right">Prj</th><th class="right">WOS</th><th class="right">Deficit</th></tr>';
    r.gap_weeks.forEach(function(g){gapDetail+='<tr><td>W'+g.wi+'</td><td>'+fmtDate(g.date)+'</td><td class="right">'+fmt(g.beg)+'</td><td class="right">'+fmt(g.prj)+'</td><td class="right">'+g.wos+'</td><td class="right pri-CRITICAL">'+g.deficit+'</td></tr>';});
    gapDetail+='</table>';
  } else { gapDetail='<div style="color:#1b5e20;font-style:italic;">&#10003; No gap weeks before next receipt.</div>'; }

  // Badges
  var flagBadges=[];
  if(r.active_kl)flagBadges.push('<span class="badge badge-green">Active KL</span>');
  if(r.nvo)flagBadges.push('<span class="badge badge-purple">NVO</span>');
  if(r.new_item_no_prj)flagBadges.push('<span class="badge badge-amber">New Item / No Prj</span>');
  if(r.amz_do_not_ship)flagBadges.push('<span class="badge badge-red">AMZ DO NOT SHIP</span>');
  if(r.amz_suppression)flagBadges.push('<span class="badge badge-red">AMZ Suppression</span>');
  if(r.transfer_qty_open)flagBadges.push('<span class="badge badge-amber">Transfer Qty Open</span>');
  if(r.pvt_lbl_excl)flagBadges.push('<span class="badge badge-amber">Pvt Lbl / Excl</span>');
  if(r.commit_item)flagBadges.push('<span class="badge badge-green">Commit Item</span>');
  if(r.is_multi)flagBadges.push('<span class="badge badge-purple">Multi-Pack (Kit)</span>');

  function kvRow(lbl,val){return '<div style="display:flex;gap:6px;padding:1px 0;font-size:11px;line-height:1.45;"><span style="color:#666;min-width:90px;flex-shrink:0;">'+lbl+'</span><span style="font-weight:500;color:#222;">'+val+'</span></div>';}
  function kvBox(lbl,rows,bg,flex){return '<div style="flex:'+(flex||'1')+';min-width:160px;background:'+(bg||'#f8f9fa')+';border:1px solid #e4e7eb;border-radius:4px;padding:8px 10px;">'+(lbl?'<div style="font-size:10px;font-weight:700;color:#9e9e9e;text-transform:uppercase;letter-spacing:0.6px;margin-bottom:5px;">'+lbl+'</div>':'')+rows+'</div>';}

  var identityBox=kvBox('Identity',kvRow('Mstyle','<b>'+esc(r.mstyle)+'</b>')+kvRow('Rank',esc(r.item_rank)||'&#8212;')+kvRow('Status',esc(r.item_status_flow)||'&#8212;')+kvRow('Sub Stat',esc(r.sub_status)||'&#8212;')+(r.season?kvRow('Season',esc(r.season)):'')+( r.size_ct?kvRow('Size/Ct',esc(r.size_ct)):'')+( r.fragrance?kvRow('Fragrance',esc(r.fragrance)):'')+( flagBadges.length?'<div style="margin-top:6px;display:flex;gap:4px;flex-wrap:wrap;">'+flagBadges.join('')+'</div>':''),'#e3f2fd');
  var itemDataBox=kvBox('Item Data','<div style="display:grid;grid-template-columns:1fr 1fr;gap:0 14px;"><div>'+kvRow('Inner Pack',r.inner_pack||'&#8212;')+kvRow('Master Pack',r.master_pack||'&#8212;')+kvRow('MOQ',r.moq?fmt(r.moq):'-')+kvRow('Opt OH',fmt(r.opt_oh))+'</div><div>'+kvRow('Opt WOS',fmt(r.opt_wos))+kvRow('LT (Wks)',fmt(r.lt_wks))+kvRow('LT + Opt Wks',fmt(r.lt_opt_weeks))+(r.upc?kvRow('UPC #',esc(r.upc)):'')+( r.gtin?kvRow('GTIN #',esc(r.gtin)):'')+'</div></div>','#e8f5e9');
  var stockStatusBox='<div style="flex:2;min-width:320px;background:#f3e5f5;border:1px solid #e4e7eb;border-radius:4px;padding:8px 10px;"><div style="font-size:10px;font-weight:700;color:#9e9e9e;text-transform:uppercase;letter-spacing:0.6px;margin-bottom:6px;">Stock Status</div><div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:0 10px;">'+
    '<div>'+kvRow('Qty OH','<b>'+fmt(r.qty_oh)+'</b>')+kvRow('Qty I/W',fmt(r.iw_qty))+kvRow('Qty I/T',fmt(r.it_qty))+kvRow('OH WOS','<span style="color:'+wosColor(r.ats_wos_oh)+'">'+fmt(r.ats_wos_oh)+'</span>')+kvRow('OH+OO WOS','<span style="color:'+wosColor(r.ats_wos_oh_oo)+'">'+fmt(r.ats_wos_oh_oo)+'</span>')+'</div>'+
    '<div>'+kvRow('ATS Now','<b style="color:'+(r.ats_now<0?'#c62828':'inherit')+'">'+fmt(r.ats_now)+'</b>')+kvRow('ATS OH',fmt(r.ats_qty_oh))+kvRow('ATS OH+OO',fmt(r.ats_oh_oo))+kvRow('ATS OH WOS','<span style="color:'+wosColor(r.ats_wos_oh)+'">'+fmt(r.ats_wos_oh)+'</span>')+kvRow('ATS OH+OO WOS','<span style="color:'+wosColor(r.ats_wos_oh_oo)+'">'+fmt(r.ats_wos_oh_oo)+'</span>')+'</div>'+
    '<div>'+kvRow('Over Cmtd Qty','<span style="color:'+(r.over_committed_qty>0?'#c62828':'inherit')+'">'+fmt(r.over_committed_qty)+'</span>')+kvRow('Ovr Comt WOS','<span style="color:'+(r.ovr_comt_wos>0?'#c62828':'inherit')+'">'+fmt(r.ovr_comt_wos)+'</span>')+kvRow('% In Stock',(r.pct_time_in_stock>0?fmt(r.pct_time_in_stock)+'%':'-'))+'</div>'+
    '</div></div>';

  var atsHtml='<table class="subtbl"><tr><th>Position</th><th class="right">Qty</th><th>Position</th><th class="right">Qty</th><th>WOS Metric</th><th class="right">Value</th></tr><tr><td>Qty OH (total)</td><td class="right"><b>'+fmt(r.qty_oh)+'</b></td><td>I/T (in transit)</td><td class="right">'+fmt(r.it_qty)+'</td><td>ATS WOS OH</td><td class="right">'+fmt(r.ats_wos_oh)+'</td></tr><tr><td>ATS Qty OH</td><td class="right">'+fmt(r.ats_qty_oh)+'</td><td>I/W (in work)</td><td class="right">'+fmt(r.iw_qty)+'</td><td>ATS WOS OH+OO</td><td class="right">'+fmt(r.ats_wos_oh_oo)+'</td></tr><tr><td>ATS Now</td><td class="right"><b>'+fmt(r.ats_now)+'</b></td><td>I/T + I/W</td><td class="right">'+fmt(r.it_iw)+'</td><td>ATS WOS OH+OO (w/ kits)</td><td class="right">'+fmt(r.ats_wos_oh_oo_w_kits)+'</td></tr><tr><td>ATS OH + OO</td><td class="right">'+fmt(r.ats_oh_oo)+'</td><td>I/W + I/T w/ Kits</td><td class="right">'+fmt(r.it_iw_kits)+'</td><td>ATS WOS (w/o test/excl)</td><td class="right">'+fmt(r.ats_wos_oh_oo_wo_test)+'</td></tr><tr><td>ATS OH + OO (w/ kits)</td><td class="right">'+fmt(r.ats_oh_oo_w_kits)+'</td><td>Open Cust PO Qty</td><td class="right">'+fmt(r.open_cust_po_qty)+'</td><td>ATS OH + I/T Booked WOS</td><td class="right">'+fmt(r.ats_oh_it_booked_wos)+'</td></tr><tr><td>ATS Qty (not alloc\'d)</td><td class="right">'+fmt(r.ats_qty_not_alloc)+'</td><td>Hold Order Qty</td><td class="right '+(r.hold_qty>0?'pri-MEDIUM':'')+'">'+fmt(r.hold_qty)+'</td><td>Opt WOS</td><td class="right"><b>'+fmt(r.opt_wos)+'</b></td></tr><tr><td>NJ ATS OH</td><td class="right">'+fmt(r.nj_ats_oh)+'</td><td>Test Order Qty</td><td class="right">'+fmt(r.test_order_qty)+'</td><td>Opt OH</td><td class="right">'+fmt(r.opt_oh)+'</td></tr><tr><td>CA ATS OH</td><td class="right">'+fmt(r.ca_ats_oh)+'</td><td>Exclude PO from WOS</td><td class="right">'+fmt(r.exclude_po_wos)+'</td><td>LT (Wks) / CNY / LT+Opt</td><td class="right">'+fmt(r.lt_wks)+' / '+fmt(r.cny_weeks)+' / '+fmt(r.lt_opt_weeks)+'</td></tr></table>';

  var demandHtml='<table class="subtbl"><tr><th>Demand</th><th class="right">Qty</th><th>Shipments</th><th class="right">Qty</th><th>Date</th><th>Value</th></tr><tr><td>Prj / Wk</td><td class="right"><b>'+fmt(r.prj_wk)+'</b></td><td>Shpd / Wk L4</td><td class="right"><b>'+fmt(r.shp_wk_l4)+'</b></td><td>Last Shp Date</td><td>'+fmtDate(r.last_shp_date)+'</td></tr><tr><td>Max Prj / Wk</td><td class="right">'+fmt(r.max_prj_wk)+'</td><td>Shpd / Wk L13</td><td class="right"><b>'+fmt(r.shp_wk_l13)+'</b></td><td>1st Shpd Date</td><td>'+fmtDate(r.first_shpd_date)+'</td></tr><tr><td>+/- Prj L4w</td><td class="right">'+fmt(r.prj_l4w_change)+'%</td><td>Total Shpd L4</td><td class="right">'+fmt(r.tot_shpd_l4)+'</td><td>Date 1st Rcvd</td><td>'+fmtDate(r.date_1st_rcvd)+'</td></tr><tr><td>Prj 26 Wks</td><td class="right">'+fmt(r.prj_26wks)+'</td><td>Total Shpd L13w</td><td class="right">'+fmt(r.tot_shpd_l13w)+'</td><td>Last Whs Rcvd</td><td>'+fmtDate(r.last_whs_rcvd)+'</td></tr><tr><td>Manual demand (rollup)</td><td class="right">'+fmt(r.manual_demand_26w)+'</td><td>Total Shpd LTD</td><td class="right">'+fmt(r.tot_shpd_ltd)+'</td><td>1st Out Date</td><td>'+fmtDate(r.first_out_date)+'</td></tr><tr><td>Demand (Inv Flow 26w Sum)</td><td class="right">'+fmt(r.demand_26w)+'</td><td colspan="2"></td><td>Last OOS Date</td><td>'+fmtDate(r.last_oos_date)+'</td></tr></table><div class="stat-text" style="margin-top:4px;"><b>Days OOS till Next Rcpt:</b> '+fmt(r.days_oos_next_rcpt)+' / <b>Days OOS L12m:</b> '+fmt(r.days_oos_l12m)+'</div>';

  function kpi(label,value,hint,color){return '<div class="kpi"'+(hint?' title="'+esc(hint)+'"':'')+'>  <div class="kpi-lbl">'+label+'</div><div class="kpi-val" style="'+(color?'color:'+color+';':'')+'">'+value+'</div></div>';}
  function wosColor(w){if(w==null||w===0)return'#888';if(w<r.opt_wos)return'#c62828';if(w>26)return'#1b5e20';return'#1565c0';}
  function excessColor(e){return e>2500?'#c62828':(e<-2500?'#e65100':'#1b5e20');}
  function oosColor(d){return d>14?'#c62828':(d>0?'#e65100':'#1b5e20');}
  var kpiStrip='<div class="kpi-strip">'+kpi('ATS Now',fmt(r.ats_now),'Available to sell - after holds / allocations',r.ats_now<0?'#c62828':'')+kpi('ATS WOS OH',fmt(r.ats_wos_oh),'Weeks of supply on hand (per QB)',wosColor(r.ats_wos_oh))+kpi('Open Cust PO',fmt(r.open_cust_po_qty),'Outstanding customer PO qty awaiting shipment','')+kpi('Hold Qty',fmt(r.hold_qty),'Hold Order Qty - orders parked, not shipping',r.hold_qty>0?'#e65100':'')+kpi('Days->Next Rcpt',fmt(r.days_oos_next_rcpt),'Days OOS until next supplier receipt arrives',oosColor(r.days_oos_next_rcpt))+kpi('Pipe Excess',fmt(r.pipeline_excess),'Total pipeline - 26w demand - safety stock. Positive = overstock',excessColor(r.pipeline_excess))+kpi('PipeWOS',(r.pipeline_wos==null?'&#8734;':fmt(r.pipeline_wos)),'Pipeline weeks of supply (all I/T + I/W + OH / 26w demand)','')+kpi('LT + CNY',fmt(r.lt_wks)+' + '+fmt(r.cny_weeks),'Lead time (weeks) + Chinese New Year shutdown weeks','')+kpi('Days OOS L12m',fmt(r.days_oos_l12m),'Days out-of-stock in trailing 12 months',r.days_oos_l12m>30?'#c62828':'')+kpi('Customers',fmt(r.customer_count),'Active Acct-MStyles rolled up into this mstyle','')+kpi('Manual Demand',fmt(r.manual_demand_26w),'26-week sum of customer manual projections (rolled up)','')+'</div>'+(r.is_multi?'<div style="margin-top:8px;padding:6px 10px;background:#fff8e1;border:1px solid #ffe082;border-radius:4px;font-size:11px;color:#5d4037;">&#127873; <b>Multi-pack:</b> Each unit = '+r.pcs_per_kit+' pcs of root <b>'+esc(r.root_mstyle)+'</b>. Root OH: <b>'+fmt(r.qty_oh_root)+'</b> pcs -> can assemble <b>'+fmt(r.assembleable_kits)+'</b> more kits. Total effective kit availability: '+fmt((r.beg_inv&&r.beg_inv[0])||0)+' on-hand + '+fmt(r.assembleable_kits)+' buildable = <b>'+fmt(((r.beg_inv&&r.beg_inv[0])||0)+r.assembleable_kits)+'</b> kits.</div>':'');

  var totalAged=(r.aged_inv_0_90||0)+(r.aged_inv_91_180||0)+(r.aged_inv_181_365||0)+(r.aged_inv_365plus||0);
  function agePct(n){return totalAged>0?Math.round(n/totalAged*100)+'%':'--';}
  function ageCard(lbl,val,pct,bg,valColor){
    return '<div class="age-card" style="background:'+bg+';">'
      +'<div class="age-card-lbl">'+lbl+'</div>'
      +'<div class="age-card-val" style="color:'+valColor+'">'+fmt(val)+'</div>'
      +'<div class="age-card-pct">'+pct+' of total</div>'
      +'</div>';
  }
  var ageHeader='<div style="display:flex;align-items:center;gap:18px;margin-bottom:10px;">'
    +'<div><span style="font-size:11px;color:#666;">Avg Inv Age</span><div style="font-size:22px;font-weight:700;color:'+(r.invtry_age_days>180?'#c62828':r.invtry_age_days>90?'#e65100':'#1565c0')+'">'+Math.round(r.invtry_age_days||0)+' <span style="font-size:13px;font-weight:400;color:#888;">days</span></div></div>'
    +'<div><span style="font-size:11px;color:#666;">Total Aged Inv</span><div style="font-size:22px;font-weight:700;color:#333;">'+fmt(totalAged)+' <span style="font-size:13px;font-weight:400;color:#888;">units</span></div></div>'
    +'</div>';
  var agedInvHtml=ageHeader+'<div class="age-cards">'
    +ageCard('0 - 90 Days',r.aged_inv_0_90,agePct(r.aged_inv_0_90),'#f0fdf4','#15803d')
    +ageCard('91 - 180 Days',r.aged_inv_91_180,agePct(r.aged_inv_91_180),'#fff7ed',(r.aged_inv_91_180>0?'#c2410c':'#666'))
    +ageCard('181 - 365 Days',r.aged_inv_181_365,agePct(r.aged_inv_181_365),'#fef2f2',(r.aged_inv_181_365>0?'#b91c1c':'#666'))
    +ageCard('365+ Days',r.aged_inv_365plus,agePct(r.aged_inv_365plus),'#fdf2f8',(r.aged_inv_365plus>0?'#9d174d':'#666'))
    +'</div>';

  // Purchase Recommendation section
  var purRecSection;
  if (!r.is_replen || r.prj_wk <= 0 || r.moq <= 0 || r.lt_trans_days <= 0 || r.opt_oh <= 0) {
    purRecSection = '';
  } else if (r.purchase_rec > 0) {
    var purETDLabel = r.purchase_rec_push_supplier
      ? '<b style="color:#e65100;">'+fmtDate(r.purchase_rec_etd)+'</b> <span style="color:#c62828;font-size:10px;font-weight:700;">&#9888; PUSH SUPPLIER</span>'
      : (r.purchase_rec_etd ? '<b style="color:#e65100;">'+fmtDate(r.purchase_rec_etd)+'</b>' : '&#8212;');

    // Retrieve persisted selections for this mstyle
    var purSel = purchaseSelections[r.mstyle] || {};
    var needQtyVal = purSel.needQty != null ? purSel.needQty : r.purchase_rec;
    var chosenSupplier = purSel.chosenSupplier || '';

    // Summary bar
    var purSummary = '<div style="display:flex;gap:16px;flex-wrap:wrap;align-items:center;background:#e8eaf6;border:1px solid #c5cae9;border-radius:6px;padding:12px 16px;margin-bottom:14px;">'
      +'<div style="flex:0 0 auto;">'
        +'<div style="font-size:10px;font-weight:700;color:#5c6bc0;text-transform:uppercase;letter-spacing:0.5px;">Recommended Qty</div>'
        +'<div style="font-size:22px;font-weight:700;color:#1a237e;">'+fmtInt(r.purchase_rec)+' <span style="font-size:12px;font-weight:400;color:#666;">units</span></div>'
      +'</div>'
      +'<div style="flex:0 0 auto;">'
        +'<div style="font-size:10px;font-weight:700;color:#5c6bc0;text-transform:uppercase;letter-spacing:0.5px;">Required ETD</div>'
        +'<div style="font-size:16px;font-weight:700;">'+purETDLabel+'</div>'
      +'</div>'
      +'<div style="flex:0 0 auto;">'
        +'<div style="font-size:10px;font-weight:700;color:#5c6bc0;text-transform:uppercase;letter-spacing:0.5px;">Receipt Needed By</div>'
        +'<div style="font-size:14px;font-weight:600;color:#333;">'+(r.purchase_rec_receipt_date?fmtDate(r.purchase_rec_receipt_date):'&#8212;')+'</div>'
      +'</div>'
      +'<div style="flex:0 0 auto;">'
        +'<div style="font-size:10px;font-weight:700;color:#5c6bc0;text-transform:uppercase;letter-spacing:0.5px;">Calc: Wk '+purTrigWk+' Prj OH</div>'
        +'<div style="font-size:13px;color:#444;">'+fmtInt(purTrigInv)+' + '+fmtInt(purGap)+' needed = '+fmtInt(purTarget)+'</div>'
      +'</div>'
      +'<div style="flex:0 0 auto;margin-left:auto;">'
        +'<label style="font-size:11px;font-weight:700;color:#5c6bc0;text-transform:uppercase;letter-spacing:0.5px;display:block;margin-bottom:4px;">Need Qty (editable)</label>'
        +'<input type="number" id="needQty_'+esc(r.mstyle)+'" value="'+needQtyVal+'" min="0" step="1" '
          +'style="width:120px;font-size:16px;font-weight:700;color:#1a237e;border:2px solid #7986cb;border-radius:4px;padding:4px 8px;text-align:right;font-family:inherit;" '
          +'onchange="(function(el){var ms=\''+esc(r.mstyle)+'\';if(!purchaseSelections[ms])purchaseSelections[ms]={};purchaseSelections[ms].needQty=parseInt(el.value)||0;})(this)">'
      +'</div>'
      +'</div>';

    // Build supplier columns
    // Each entry: { key, label, fob, moq, lt, elc_nj, elc_la, mu_nj, mu_la, qty_ord, pct_ord, infoHtml }
    // For main supplier: parse from supplier_info HTML or use individual fields
    var suppCols = [];

    // Main supplier - extract name from supplier_info if possible, else use FOB cost presence
    var mainName = '';
    if (r.supplier_info) {
      var nameMatch = stripHtml(r.supplier_info).match(/^([^\n]+)/);
      if (nameMatch) mainName = nameMatch[1].trim();
    }
    if (!mainName && r.fob_cost > 0) mainName = 'Main Supplier';
    if (mainName || r.fob_cost > 0 || r.elc_nj > 0) {
      suppCols.push({ key:'main', label: mainName || 'Main Supplier',
        fob:r.fob_cost, moq:r.moq, lt:r.lt_trans_days,
        elc_nj:r.elc_nj, elc_la:r.elc_la, mu_nj:r.mu_nj, mu_la:r.mu_la,
        qty_ord:r.qty_ord_supplier, pct_ord:r.pct_units_ord_supplier });
    }
    if (r.alt1_name) {
      suppCols.push({ key:'alt1', label:r.alt1_name,
        fob:r.alt1_fob, moq:r.alt1_moq, lt:r.alt1_lt,
        elc_nj:r.alt1_elc_nj, elc_la:r.alt1_elc_la, mu_nj:r.alt1_mu_nj, mu_la:r.alt1_mu_la,
        qty_ord:r.alt1_qty_ord, pct_ord:r.alt1_pct_ord });
    }
    if (r.alt2_name) {
      suppCols.push({ key:'alt2', label:r.alt2_name,
        fob:r.alt2_fob, moq:r.alt2_moq, lt:r.alt2_lt,
        elc_nj:r.alt2_elc_nj, elc_la:r.alt2_elc_la, mu_nj:r.alt2_mu_nj, mu_la:r.alt2_mu_la,
        qty_ord:r.alt2_qty_ord, pct_ord:r.alt2_pct_ord });
    }
    if (r.alt3_name) {
      suppCols.push({ key:'alt3', label:r.alt3_name,
        fob:r.alt3_fob, moq:r.alt3_moq, lt:r.alt3_lt,
        elc_nj:r.alt3_elc_nj, elc_la:r.alt3_elc_la, mu_nj:r.alt3_mu_nj, mu_la:r.alt3_mu_la,
        qty_ord:r.alt3_qty_ord, pct_ord:r.alt3_pct_ord });
    }

    // Determine best MU% column for highlighting (highest NJ MU%)
    var bestMU = suppCols.reduce(function(best, s){ return (s.mu_nj||0) > best ? (s.mu_nj||0) : best; }, 0);

    var suppTable = '';
    if (suppCols.length > 0) {
      // Metric rows
      var rows = [
        { lbl:'FOB Cost',      fn:function(s){ return fmtCur(s.fob); } },
        { lbl:'MOQ',           fn:function(s){ return s.moq ? fmtInt(s.moq)+' units' : '&#8212;'; } },
        { lbl:'Lead Time',     fn:function(s){ return s.lt ? s.lt+' days' : '&#8212;'; } },
        { lbl:'ELC NJ',        fn:function(s){ return fmtCur(s.elc_nj); } },
        { lbl:'ELC LA',        fn:function(s){ return fmtCur(s.elc_la); } },
        { lbl:'MU% NJ',        fn:function(s){ return fmtPct(s.mu_nj); }, highlight:function(s){ return (s.mu_nj||0) === bestMU && bestMU > 0; } },
        { lbl:'MU% LA',        fn:function(s){ return fmtPct(s.mu_la); } },
        { lbl:'Units Ordered', fn:function(s){ return s.qty_ord ? fmtInt(s.qty_ord) : '&#8212;'; } },
        { lbl:'% of Orders',   fn:function(s){ return s.pct_ord ? fmtPct(s.pct_ord) : '&#8212;'; } }
      ];

      // Table header
      suppTable += '<div style="overflow-x:auto;"><table style="width:100%;border-collapse:collapse;font-size:12px;">';
      suppTable += '<thead><tr style="background:#3f51b5;color:#fff;">'
        +'<th style="padding:8px 10px;text-align:left;font-weight:600;min-width:120px;border-right:1px solid #5c6bc0;">Metric</th>';
      suppCols.forEach(function(s, idx) {
        var isBest = (s.mu_nj||0) === bestMU && bestMU > 0;
        var hdrBg = idx === 0 ? 'background:#283593;' : (isBest ? 'background:#1b5e20;' : 'background:#3f51b5;');
        suppTable += '<th style="padding:8px 10px;text-align:center;font-weight:600;min-width:150px;'+hdrBg+'border-right:1px solid #5c6bc0;">';
        if (idx === 0) suppTable += '<span style="font-size:9px;opacity:0.8;display:block;text-transform:uppercase;letter-spacing:0.5px;">Main</span>';
        else suppTable += '<span style="font-size:9px;opacity:0.8;display:block;text-transform:uppercase;letter-spacing:0.5px;">ALT '+(idx)+'</span>';
        suppTable += '<span style="font-size:13px;">'+esc(s.label)+'</span></th>';
      });
      suppTable += '</tr></thead><tbody>';

      // Data rows
      rows.forEach(function(row, ri) {
        var rowBg = ri % 2 === 0 ? '#f5f5f5' : '#ffffff';
        suppTable += '<tr style="background:'+rowBg+';">'
          +'<td style="padding:6px 10px;font-weight:600;color:#555;border-right:1px solid #e0e0e0;border-bottom:1px solid #e9ecef;">'+row.lbl+'</td>';
        suppCols.forEach(function(s) {
          var val = row.fn(s);
          var isHL = row.highlight && row.highlight(s);
          var cellStyle = 'padding:6px 10px;text-align:center;border-right:1px solid #e0e0e0;border-bottom:1px solid #e9ecef;';
          if (isHL) cellStyle += 'background:#e8f5e9;color:#1b5e20;font-weight:700;';
          suppTable += '<td style="'+cellStyle+'">'+val+'</td>';
        });
        suppTable += '</tr>';
      });

      // Choose this Supplier row
      suppTable += '<tr style="background:#e8eaf6;border-top:2px solid #7986cb;">'
        +'<td style="padding:8px 10px;font-weight:700;color:#3f51b5;border-right:1px solid #c5cae9;">Choose</td>';
      suppCols.forEach(function(s) {
        var isChosen = chosenSupplier === s.key;
        var btnStyle = isChosen
          ? 'background:#1b5e20;color:#fff;border:2px solid #1b5e20;border-radius:5px;padding:5px 10px;font-size:11px;font-weight:700;cursor:pointer;font-family:inherit;width:100%;'
          : 'background:#fff;color:#3f51b5;border:2px solid #7986cb;border-radius:5px;padding:5px 10px;font-size:11px;font-weight:600;cursor:pointer;font-family:inherit;width:100%;';
        var btnLabel = isChosen ? '&#10003; Selected' : 'Choose this Supplier';
        suppTable += '<td style="padding:8px 10px;text-align:center;border-right:1px solid #c5cae9;">'
          +'<button style="'+btnStyle+'" '
            +'onclick="(function(btn){var ms=\''+esc(r.mstyle)+'\';var key=\''+s.key+'\';'
              +'if(!purchaseSelections[ms])purchaseSelections[ms]={};'
              +'purchaseSelections[ms].chosenSupplier=key;'
              +'renderDetailForMstyle(ms);})(this)">'
          +btnLabel+'</button></td>';
      });
      suppTable += '</tr></tbody></table></div>';
    } else {
      suppTable = '<div style="color:#888;font-style:italic;font-size:12px;">No supplier data available for this item.</div>';
    }

    purRecSection = '<div class="section"><h3>&#128722; Recommended Purchase</h3>'
      +purSummary
      +'<div style="margin-top:4px;margin-bottom:8px;font-size:11px;color:#666;">'
        +'<b>Calc:</b> Opt OH ('+fmtInt(r.opt_oh)+') + 4-wk buffer ('+fmtInt(purBufUnits)+') - Wk '+purTrigWk+' Prj OH ('+fmtInt(purTrigInv)+') = <b>'+fmtInt(purGap)+'</b> units gap, floored at MOQ ('+fmtInt(r.moq)+'), rounded to master pack.'
      +'</div>'
      +suppTable
      +'</div>';
  } else {
    purRecSection = '<div class="section"><h3>&#128722; Recommended Purchase</h3>'
      +'<div style="color:#1b5e20;font-style:italic;">&#10003; No new order needed - projected inventory stays above Opt OH ('+fmtInt(r.opt_oh)+' units) through the 26-week window.</div>'
      +'</div>';
  }

  return '<div class="dwrap"><div class="section" style="padding:10px 14px;"><div style="display:flex;gap:10px;flex-wrap:wrap;align-items:flex-start;">'+identityBox+itemDataBox+stockStatusBox+'</div></div><div class="section"><h3>&#128230; Inventory Flow <span style="font-size:10px;font-weight:400;color:#888;">- hover Expected Receipts cells for PO detail / hover Prj Demand cells for customer breakdown</span></h3><div style="overflow-x:auto">'+invFlow+'</div>'+kpiStrip+'</div><div class="section"><h3>&#128197; Aged Inventory</h3>'+agedInvHtml+'</div><div class="section"><h3>&#127919; Recommended Actions</h3><div class="recs-wrap">'+recs+'</div></div>'+purRecSection+'</div>';
}

// -- Reco spreadsheet ----------------------------------------------------------
function addToRecoSheet(btn) {
  var d=btn.dataset;
  recoSheet.push({mstyle:d.mstyle,action:d.action,po_number:d.po,supplier:d.supplier,qty_open:d.qty,curr_etd:d.currEtd,curr_eta:d.currEta,req_etd:d.reqEtd,req_eta:d.reqEta});
  btn.textContent='+ Added';btn.disabled=true;btn.style.background='#e8f5e9';btn.style.color='#2e7d32';btn.style.borderColor='#a5d6a7';
  var badge=document.getElementById('recoBadge');if(badge){badge.textContent=recoSheet.length;badge.style.display='inline';}
}
function generateRecoSheet() {
  if(!recoSheet.length){alert('No recommendations added yet.\nOpen a record and click "Add to Excel" on any recommendation card.');return;}
  var headers=['Mstyle','Action','PO #','Supplier','Qty Open','Current ETD','Current ETA','Requested ETD','Requested ETA'];
  var rows=recoSheet.map(function(r){return[r.mstyle,r.action,r.po_number,r.supplier,r.qty_open,r.curr_etd,r.curr_eta,r.req_etd,r.req_eta];});
  var csv=[headers].concat(rows).map(function(row){return row.map(function(v){return'"'+String(v!=null?v:'').replace(/"/g,'""')+'"';}).join(',');}).join('\n');
  var blob=new Blob([''+csv],{type:'text/csv;charset=utf-8;'});
  var a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download='PO_Recommendations_'+new Date().toISOString().slice(0,10)+'.csv';a.click();
}

// -- Boot ----------------------------------------------------------------------
async function boot() {
  var scr=document.getElementById('loadingScreen');
  setStep(1,'active');setBar(5);setStatus('Checking cache...');

  var cached=loadCache();
  if(cached){
    setBar(80);setStatus('Loading from cache...');
    ALL=cached.data;
    var asOf=document.getElementById('dataAsOf');
    if(asOf)asOf.textContent='Data as of '+fmtTimestamp(cached.ts)+' (cached)';
    setBar(90);setStatus('Building view...');
    setStep(4,'active');
    buildFilterDropdowns();buildTableHead();applyFilters();
    setBar(100);setStep(4,'done');
    await new Promise(function(r){setTimeout(r,350);});
    if(scr){scr.classList.add('hidden');setTimeout(function(){scr.style.display='none';},500);}
    return;
  }

  try {
    var records=await loadData();
    ALL=records;
    saveCache(records);
    setStep(4,'active');setBar(85);setStatus('Building view...');
    await new Promise(function(r){setTimeout(r,50);});
    buildFilterDropdowns();buildTableHead();applyFilters();
    var ts=Date.now();
    var asOf=document.getElementById('dataAsOf');
    if(asOf)asOf.textContent='Data as of '+fmtTimestamp(ts);
    setBar(100);setStep(4,'done');setStatus('Ready!');
    await new Promise(function(r){setTimeout(r,350);});
    if(scr){scr.classList.add('hidden');setTimeout(function(){scr.style.display='none';},500);}
  } catch(err) {
    var msg = err.message || String(err);
    setStatus('Load failed');
    console.error('P+P Inventory Manager boot error:', err);
    document.getElementById('loadSteps').innerHTML =
      '<div style="color:#ff8a65;font-size:13px;font-weight:600;margin-bottom:6px;">Error loading data:</div>' +
      '<div style="color:#ffcdd2;font-size:12px;word-break:break-all;">' + esc(msg) + '</div>' +
      '<div style="margin-top:12px;"><button onclick="refreshData()" style="padding:6px 16px;background:#fff;color:#0d47a1;border:none;border-radius:4px;cursor:pointer;font-size:12px;font-weight:600;">Retry</button></div>';
    setBar(100);
    document.getElementById('loadBar').style.background='#ef5350';
  }
}

async function forceRefresh() {
  clearCache();
  ALL=[];FILTERED=[];
  document.getElementById('tbody').innerHTML='';
  document.getElementById('statsBar').innerHTML='';
  var scr=document.getElementById('loadingScreen');
  if(scr){scr.style.display='flex';scr.classList.remove('hidden');}
  document.getElementById('loadBar').style.width='0%';
  document.getElementById('loadSteps').innerHTML='<div id="ls1" class="pending">Check cache</div><div id="ls2" class="pending">Load Inventory Flow</div><div id="ls3" class="pending">Load Projections</div><div id="ls4" class="pending">Build view</div>';
  await boot();
}

// -- Wire up controls + boot ----------------------------------------------------
// IMPORTANT: wrapped in DOMContentLoaded so DOM elements exist before we
// reference them.  The <script> tag in the HTML should be at the END of <body>
// (not in <head>) - but this guard handles both placements safely.
document.addEventListener('DOMContentLoaded', function() {
  document.getElementById('searchInput').oninput=applyFilters;
  document.getElementById('gapsOnly').onchange=applyFilters;
  document.getElementById('overstockOnly').onchange=applyFilters;
  document.getElementById('hideInactive').onchange=applyFilters;

  // Wire up static Action dropdown panel checkboxes
  buildDdPanel('dd-action',[
    {v:'PULL_UP'},{v:'FASTER_VESSEL'},{v:'PUSH_OUT'},{v:'SPLIT'},{v:'CANCEL'},{v:'NO_LEVER'},{v:'__NONE__',label:'No recs (clean)'}
  ],'All Actions',function(s){selActions=s;});

  buildDdPanel('dd-stock-status',[
    {v:'Over-Stocked'},{v:'Under-Stocked'},{v:'In Stock'},{v:'Inactive'}
  ],'All Statuses',function(s){selStockStatus=s;});

  // Wire up static Priority dropdown panel checkboxes
  // setState only updates selPriorities; renderStats() (called by applyFilters) redraws stats bar
  buildDdPanel('dd-priority',[
    {v:'CRITICAL',label:'Critical'},{v:'HIGH',label:'High'},{v:'MEDIUM',label:'Medium'},{v:'LOW',label:'Low'},{v:'NO_OOS',label:'No OOS'}
  ],'All OOS Pri',function(s){selPriorities=s;});

  document.getElementById('clearBtn').onclick=function(){
    document.getElementById('searchInput').value='';
    document.getElementById('gapsOnly').checked=false;
    document.getElementById('overstockOnly').checked=false;
    document.getElementById('hideInactive').checked=true;
    selActions.clear();selCountries.clear();selBrands.clear();selMgrs.clear();selPriorities.clear();selStockStatus.clear();
    ['dd-action','dd-country','dd-brand','dd-mgr','dd-priority','dd-stock-status'].forEach(function(id){
      var el=document.getElementById(id);if(!el)return;
      el.querySelectorAll('input[type=checkbox]').forEach(function(cb){cb.checked=false;});
      var labels={
        'dd-action':'All Actions','dd-country':'All Countries','dd-brand':'All Brands',
        'dd-mgr':'All Inv Mgrs','dd-priority':'All OOS Pri'
      };
      updateDdBtn(id,labels[id]);
    });
    Object.keys(colFilters).forEach(function(k){delete colFilters[k];});
    currentSort={id:null,dir:1};
    buildTableHead();applyFilters();
  };

  boot();
});
