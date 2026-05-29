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
var CACHE_KEY     = 'pp_inv_mgmt_v4';          // main (phase 1) IDB key -- v4: fixed Brand/Desc FIDs 197/205
var CACHE_KEY_DTL = 'pp_inv_mgmt_dtl_v4';      // detail (phase 2) IDB key -- v4: added need_qty/etd per supplier
var CACHE_KEY_SS  = 'pp_inv_mgmt_ss_v5';       // sessionStorage fast-path key (bump to evict stale data)
var CACHE_TTL     = 24 * 60 * 60 * 1000;       // 24h (was 6h)
// Pre-filter: exclude truly inactive items (qty_oh=0 AND opt_wos=0).
// ItemStatus (fid 294) is a lookup -- cannot be used in QB WHERE -- so we use numeric fields.
var IF_PRE_FILTER = '{24.GT.0}OR{137.GT.0}';

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

// -- IndexedDB cache (no quota limits, survives browser restart) ---------------
var _idb = (function() {
  var _db = null; var STORE = 'kv';
  function _open() {
    if (_db) return Promise.resolve(_db);
    return new Promise(function(res, rej) {
      var req = indexedDB.open('pp_inv_mgmt_idb_v1', 1);
      req.onupgradeneeded = function(e) { e.target.result.createObjectStore(STORE); };
      req.onsuccess = function(e) { _db = e.target.result; res(_db); };
      req.onerror   = function(e) { rej(e.target.error); };
    });
  }
  return {
    get: function(key) {
      return _open().then(function(db) {
        return new Promise(function(res, rej) {
          var tx = db.transaction(STORE,'readonly');
          var req = tx.objectStore(STORE).get(key);
          req.onsuccess = function() { res(req.result||null); };
          req.onerror   = function(e) { rej(e.target.error); };
        });
      });
    },
    set: function(key, val) {
      return _open().then(function(db) {
        return new Promise(function(res, rej) {
          var tx = db.transaction(STORE,'readwrite');
          var req = tx.objectStore(STORE).put(val, key);
          req.onsuccess = function() { res(); };
          req.onerror   = function(e) { rej(e.target.error); };
        });
      });
    },
    del: function(key) {
      return _open().then(function(db) {
        return new Promise(function(res) {
          var tx = db.transaction(STORE,'readwrite');
          tx.objectStore(STORE).delete(key);
          tx.oncomplete = res; tx.onerror = res;
        });
      }).catch(function(){});
    }
  };
})();

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
  Alt3QtyOrd:1837, Alt3PctOrd:1840,
  // Need-to-order per supplier (created 2026-05-27)
  NeedQtyMain:1918, NeedETDMain:1919,
  NeedQtyAlt1:1920, NeedETDAlt1:1921,
  NeedQtyAlt2:1922, NeedETDAlt2:1923,
  NeedQtyAlt3:1924, NeedETDAlt3:1925
};
// Supplier FIDs that are only needed for the detail panel (phase 2 load)
var IF_SUPP_FIDS = [
  IF_F.SupplierInfo, IF_F.FOBCost, IF_F.ELC_NJ, IF_F.ELC_LA,
  IF_F.MU_NJ, IF_F.MU_LA, IF_F.QtyOrdSupplier, IF_F.PctUnitsOrdSupplier,
  IF_F.Alt1Name, IF_F.Alt1FOB, IF_F.Alt1MOQ, IF_F.Alt1LT,
  IF_F.Alt1ELC_NJ, IF_F.Alt1ELC_LA, IF_F.Alt1MU_NJ, IF_F.Alt1MU_LA,
  IF_F.Alt1QtyOrd, IF_F.Alt1PctOrd,
  IF_F.Alt2Name, IF_F.Alt2FOB, IF_F.Alt2MOQ, IF_F.Alt2LT,
  IF_F.Alt2ELC_NJ, IF_F.Alt2ELC_LA, IF_F.Alt2MU_NJ, IF_F.Alt2MU_LA,
  IF_F.Alt2QtyOrd, IF_F.Alt2PctOrd,
  IF_F.Alt3Name, IF_F.Alt3FOB, IF_F.Alt3MOQ, IF_F.Alt3LT,
  IF_F.Alt3ELC_NJ, IF_F.Alt3ELC_LA, IF_F.Alt3MU_NJ, IF_F.Alt3MU_LA,
  IF_F.Alt3QtyOrd, IF_F.Alt3PctOrd,
  IF_F.NeedQtyMain, IF_F.NeedETDMain,
  IF_F.NeedQtyAlt1, IF_F.NeedETDAlt1,
  IF_F.NeedQtyAlt2, IF_F.NeedETDAlt2,
  IF_F.NeedQtyAlt3, IF_F.NeedETDAlt3
];
// Main scalar FIDs: everything in IF_F except supplier detail fields
var IF_F_MAIN_FIDS = Object.values(IF_F).filter(function(fid) {
  return IF_SUPP_FIDS.indexOf(fid) === -1;
});
var IF_BEG = [134,8,9,10,110,111,112,113,114,115,116,117,118,128,129,130,131,120,121,122,123,124,125,126,127,119];
var IF_RCV = [28,35,36,50,51,65,66,67,68,69,70,71,72,73,74,75,76,77,78,79,80,81,82,83,84,85];
var IF_PRJ = [146,147,150,151,152,153,154,155,156,157,158,159,160,161,162,163,164,165,166,167,168,169,170,171,172,173];
var IF_ATS = [716,717,718,719,720,715,722,723,724,725,726,727,728,729,730,731,902,903,904,905,906,907,908,909,910,911];

var PRJ_F = { Mstyle:196, CustName:874, StatusCust:10, PTItemStatus:374, Brand:197, Description:205, AcctMStyleKey:292, POGEndDate:1595 };
var PRJ_MANUAL = [22,25,28,31,34,37,40,43,46,49,52,55,58,61,64,67,70,73,76,79,82,85,88,91,94,97];

// Orders and Shipments (bphc3vs5h) -- for Open Customer PO per-week data + hover
var ORDS_TID = 'bphc3vs5h';
// A-Open W1..W26 FIDs (index 0=W1 .. 25=W26)
var ORDS_AOPEN_FIDS = [184,185,186,187,188,189,190,191,192,193,194,195,196,197,198,199,200,201,202,203,204,205,206,207,208,209];
var _custOrderCache = {};  // mstyle -> array of order objects (fetched on panel open)

async function fetchCustOrders(mstyle) {
  if (_custOrderCache[mstyle]) return _custOrderCache[mstyle];
  var fids = [11, 76, 7, 14, 15, 10, 80].concat(ORDS_AOPEN_FIDS);
  // FID 11=Mstyle, 76=CustName, 7=AcctNum, 14=QtyOrd, 15=QtyOpen, 10=CancelDate, 80=StartShip
  var rows = await qbQuery(ORDS_TID, fids, '{11.EX.\'' + mstyle + '\'}{15.GT.0}', 0, 500);
  var orders = rows.map(function(row) {
    var g = function(fid) { var v = row[fid]; return v ? v.value : null; };
    return {
      custName:   String(g(76) || ''),
      acctNum:    toNum(g(7)),
      qtyOrd:     toNum(g(14)),
      qtyOpen:    toNum(g(15)),
      cancelDate: g(10) || null,
      startShip:  g(80) || null,
      weekQtys:   ORDS_AOPEN_FIDS.map(function(fid) { return toNum(g(fid)); })
    };
  }).filter(function(o) { return o.qtyOpen > 0; });
  _custOrderCache[mstyle] = orders;
  return orders;
}

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

// -- Cache (IndexedDB primary, sessionStorage fast-path for same-tab F5) ------
// Date-aware JSON serialization so IDB stores a plain string (avoids structured-clone
// failures for Date objects in open_pos, next_rcpt_dt, purchase_rec_etd, etc.)
var _DATE_TAG = '__D__';
function _jsonSer(key, val) {
  return (val instanceof Date) ? _DATE_TAG + val.toISOString() : val;
}
function _jsonDes(key, val) {
  return (typeof val === 'string' && val.indexOf(_DATE_TAG) === 0)
    ? new Date(val.slice(_DATE_TAG.length)) : val;
}
async function saveCache(data) {
  var json = JSON.stringify({ ts: Date.now(), data: data }, _jsonSer);
  // sessionStorage fast-path (may fail silently if quota exceeded -- that's OK)
  try { sessionStorage.setItem(CACHE_KEY_SS, json); } catch(_) {}
  // IDB stores the JSON string (not a structured object) -- always serializable
  try { await _idb.set(CACHE_KEY, json); } catch(e) {
    console.warn('[InvMgmt] IDB save failed:', e && e.message);
  }
  // Evict stale old-version keys
  try { localStorage.removeItem('pp_inv_mgmt_codepage_v2'); } catch(_) {}
  try { sessionStorage.removeItem('pp_inv_mgmt_codepage_v2'); } catch(_) {}
  try { sessionStorage.removeItem('pp_inv_mgmt_ss_v3'); } catch(_) {}
}
async function loadCache() {
  // 1. sessionStorage -- instant same-tab F5, no async needed
  try {
    var raw = sessionStorage.getItem(CACHE_KEY_SS);
    if (raw) {
      var obj = JSON.parse(raw, _jsonDes);
      if (obj && typeof obj.ts === 'number' && obj.data &&
          Date.now() - obj.ts <= CACHE_TTL) return { obj: obj, src: 'session' };
    }
  } catch(_) {}
  // 2. IndexedDB -- cross-tab, cross-session
  try {
    var stored = await _idb.get(CACHE_KEY);
    if (typeof stored === 'string') {
      var obj = JSON.parse(stored, _jsonDes);
      if (obj && typeof obj.ts === 'number' && obj.data &&
          Date.now() - obj.ts <= CACHE_TTL) {
        try { sessionStorage.setItem(CACHE_KEY_SS, stored); } catch(_) {}
        return { obj: obj, src: 'idb' };
      }
    }
  } catch(_) {}
  return null;
}
async function clearCache() {
  try { localStorage.removeItem('pp_inv_mgmt_codepage_v2'); } catch(_) {}
  try { sessionStorage.removeItem(CACHE_KEY_SS); } catch(_) {}
  try { await _idb.del(CACHE_KEY); } catch(_) {}
  try { await _idb.del(CACHE_KEY_DTL); } catch(_) {}
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
var DEFAULT_SORT_CHAIN = ['brand','mstyle'];
var colFilters = {};
var currentPage = 0;
var PAGE_SIZE   = 100;
var selActions    = new Set();
var selCountries  = new Set();
var selBrands     = new Set();
var selMgrs       = new Set();
var selPriorities = new Set();
var selStockStatus = new Set();
var showNeedPurchase = false;  // toggle: only show records where purchase_rec > 0
var recoSheet = [];
var purchaseSelections = {};   // keyed by mstyle; { main:{checked,needQty,etd}, alt1:{...}, alt2:{...}, alt3:{...} }

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
  // Phase 1: scalars + beg_inv + prj only -- enough to compute all main-table columns.
  // IF_RCV, IF_ATS, and supplier FIDs are loaded in background (phase 2).
  var ifFieldIds = IF_F_MAIN_FIDS.concat(IF_BEG, IF_PRJ);
  // QB can't filter on formula fields (fid 927) or lookup fields (fid 294), so load all and
  // apply the field-927 Case() formula logic client-side after the pull.
  // IF_PRE_FILTER (module-level) excludes truly inactive items; client-side ItemStatus filter
  // runs afterwards for precision.
  var ifRowsAll = await qbQueryAll(INVF_TID, ifFieldIds, IF_PRE_FILTER, 'Loading Inventory Flow');
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
    if (!prjByMs[ms]) prjByMs[ms] = { custs:[], desc:desc, brand:brand, pog_end_warns:[] };
    else { if(desc && !prjByMs[ms].desc) prjByMs[ms].desc=desc; if(brand && !prjByMs[ms].brand) prjByMs[ms].brand=brand; }
    prjByMs[ms].custs.push({ customer:cust, weekly:weekly, total:total });
    // F_POG_END_WARN: detect active customers approaching POG End Date cutoff (6 wks before)
    var _peStr = (String((row[PRJ_F.POGEndDate]||{}).value||'')).trim().slice(0,10);
    var _peSC  = (String((row[PRJ_F.StatusCust]||{}).value||'')).trim().toUpperCase();
    if (_peStr && _peSC.startsWith('A')) {
      var _peDate = new Date(_peStr);
      if (!isNaN(_peDate.getTime())) {
        var _peCutoff = new Date(_peDate.getTime() - 6 * 7 * 86400000);
        var _peToday  = new Date(); _peToday.setHours(0,0,0,0);
        var _peW1Sun  = new Date(_peToday); _peW1Sun.setDate(_peToday.getDate() - _peToday.getDay());
        var _peCutIdx = Math.floor((_peCutoff - _peW1Sun) / (7 * 86400000));
        if (_peCutIdx < 26) {
          var _peStart   = Math.max(0, _peCutIdx);
          var _peExp     = weekly.slice(_peStart).reduce(function(a,b){return a+b;}, 0);
          if (_peExp > 0) {
            prjByMs[ms].pog_end_warns.push({
              customer: cust,
              pog_end:  _peStr,
              cutoff_wk: Math.max(1, _peCutIdx + 1),
              exposure:  _peExp
            });
          }
        }
      }
    }
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
    // rcv and ATS default to zeros; phase 2 (attachDetailData) fills in real values
    var rcv     = IF_RCV.map(function(){return 0;});
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
      // Supplier fields -- defaulted to empty; populated by phase 2 (attachDetailData)
      supplier_info:'', fob_cost:0, elc_nj:0, elc_la:0,
      mu_nj:0, mu_la:0, qty_ord_supplier:0, pct_units_ord_supplier:0,
      alt1_name:'', alt1_fob:0, alt1_moq:0, alt1_lt:0,
      alt1_elc_nj:0, alt1_elc_la:0, alt1_mu_nj:0, alt1_mu_la:0,
      alt1_qty_ord:0, alt1_pct_ord:0,
      alt2_name:'', alt2_fob:0, alt2_moq:0, alt2_lt:0,
      alt2_elc_nj:0, alt2_elc_la:0, alt2_mu_nj:0, alt2_mu_la:0,
      alt2_qty_ord:0, alt2_pct_ord:0,
      alt3_name:'', alt3_fob:0, alt3_moq:0, alt3_lt:0,
      alt3_elc_nj:0, alt3_elc_la:0, alt3_mu_nj:0, alt3_mu_la:0,
      alt3_qty_ord:0, alt3_pct_ord:0,
      _detail_loaded: false,
      is_multi:isMulti, pcs_per_kit:pcsKit, root_mstyle:rootMs,
      qty_oh_root:0, it_iw_root:0, ats_oh_oo_root:0, assembleable_kits:0,
      open_pos:openPos,
      manual_demand_26w:manDem26w, customer_demand:custDemand, demand_26w:0,
      pog_end_warns: pi.pog_end_warns || [],
      pipeline_total:0, oh_excess:0, pipeline_excess:0, pipeline_wos:0,
      gap_weeks:[], overstocked:false, stock_status:'', recommendations:[], priority:'LOW', flag:'',
      purchase_rec:0, purchase_rec_etd:null, purchase_rec_push_supplier:false,
      purchase_rec_receipt_date:null, purchase_rec_trigger_idx:-1,
      ats_sim_beg:[]
    };

    computeDerived(rec, today);
    records.push(rec);
  }
  return records;
}

// -- Phase 2: background load of detail-only fields (IF_RCV, IF_ATS, supplier) --
var _detailPromise = null;
async function attachDetailData(records) {
  // Check IDB cache first (stored as JSON string to avoid structured-clone failures)
  try {
    var rawDtl = await _idb.get(CACHE_KEY_DTL);
    if (typeof rawDtl === 'string') {
      var cachedDtl = JSON.parse(rawDtl, _jsonDes);
      if (cachedDtl && typeof cachedDtl.ts === 'number' && cachedDtl.map &&
          Date.now() - cachedDtl.ts <= CACHE_TTL) {
        _applyDetailMap(records, cachedDtl.map);
        console.info('[InvMgmt] detail data loaded from IDB cache');
        return;
      }
    }
  } catch(_) {}

  // Fresh pull: mstyle + IF_RCV + IF_ATS + supplier FIDs
  // Use same pre-filter as main load to avoid pulling 12K+ rows
  var detailFids = [IF_F.Mstyle].concat(IF_RCV, IF_ATS, IF_SUPP_FIDS);
  var rows = await qbQueryAll(INVF_TID, detailFids, IF_PRE_FILTER, 'Loading detail data');
  var map = {};
  rows.forEach(function(row) {
    var gv = function(fid) { return (row[fid]||{}).value; };
    var ms = String(gv(IF_F.Mstyle)||'').trim();
    if (!ms) return;
    map[ms] = {
      rcv:  IF_RCV.map(function(fid){ return Math.round(toNum(gv(fid))); }),
      ats:  IF_ATS.map(function(fid){ return Math.round(toNum(gv(fid))); }),
      supplier_info:         String(gv(IF_F.SupplierInfo)||''),
      fob_cost:              toNum(gv(IF_F.FOBCost)),
      elc_nj:                toNum(gv(IF_F.ELC_NJ)),
      elc_la:                toNum(gv(IF_F.ELC_LA)),
      mu_nj:                 toNum(gv(IF_F.MU_NJ)),
      mu_la:                 toNum(gv(IF_F.MU_LA)),
      qty_ord_supplier:      toNum(gv(IF_F.QtyOrdSupplier)),
      pct_units_ord_supplier:toNum(gv(IF_F.PctUnitsOrdSupplier)),
      alt1_name: String(gv(IF_F.Alt1Name)||''), alt1_fob:toNum(gv(IF_F.Alt1FOB)), alt1_moq:toNum(gv(IF_F.Alt1MOQ)), alt1_lt:toNum(gv(IF_F.Alt1LT)),
      alt1_elc_nj:toNum(gv(IF_F.Alt1ELC_NJ)), alt1_elc_la:toNum(gv(IF_F.Alt1ELC_LA)), alt1_mu_nj:toNum(gv(IF_F.Alt1MU_NJ)), alt1_mu_la:toNum(gv(IF_F.Alt1MU_LA)),
      alt1_qty_ord:toNum(gv(IF_F.Alt1QtyOrd)), alt1_pct_ord:toNum(gv(IF_F.Alt1PctOrd)),
      alt2_name: String(gv(IF_F.Alt2Name)||''), alt2_fob:toNum(gv(IF_F.Alt2FOB)), alt2_moq:toNum(gv(IF_F.Alt2MOQ)), alt2_lt:toNum(gv(IF_F.Alt2LT)),
      alt2_elc_nj:toNum(gv(IF_F.Alt2ELC_NJ)), alt2_elc_la:toNum(gv(IF_F.Alt2ELC_LA)), alt2_mu_nj:toNum(gv(IF_F.Alt2MU_NJ)), alt2_mu_la:toNum(gv(IF_F.Alt2MU_LA)),
      alt2_qty_ord:toNum(gv(IF_F.Alt2QtyOrd)), alt2_pct_ord:toNum(gv(IF_F.Alt2PctOrd)),
      alt3_name: String(gv(IF_F.Alt3Name)||''), alt3_fob:toNum(gv(IF_F.Alt3FOB)), alt3_moq:toNum(gv(IF_F.Alt3MOQ)), alt3_lt:toNum(gv(IF_F.Alt3LT)),
      alt3_elc_nj:toNum(gv(IF_F.Alt3ELC_NJ)), alt3_elc_la:toNum(gv(IF_F.Alt3ELC_LA)), alt3_mu_nj:toNum(gv(IF_F.Alt3MU_NJ)), alt3_mu_la:toNum(gv(IF_F.Alt3MU_LA)),
      alt3_qty_ord:toNum(gv(IF_F.Alt3QtyOrd)), alt3_pct_ord:toNum(gv(IF_F.Alt3PctOrd)),
      need_qty_main:toNum(gv(IF_F.NeedQtyMain)), need_etd_main:String(gv(IF_F.NeedETDMain)||''),
      need_qty_alt1:toNum(gv(IF_F.NeedQtyAlt1)), need_etd_alt1:String(gv(IF_F.NeedETDAlt1)||''),
      need_qty_alt2:toNum(gv(IF_F.NeedQtyAlt2)), need_etd_alt2:String(gv(IF_F.NeedETDAlt2)||''),
      need_qty_alt3:toNum(gv(IF_F.NeedQtyAlt3)), need_etd_alt3:String(gv(IF_F.NeedETDAlt3)||'')
    };
  });
  _applyDetailMap(records, map);
  try {
    var dtlJson = JSON.stringify({ ts: Date.now(), map: map }, _jsonSer);
    await _idb.set(CACHE_KEY_DTL, dtlJson);
  } catch(e) { console.warn('[InvMgmt] detail IDB save failed:', e && e.message); }
  console.info('[InvMgmt] detail data loaded fresh and cached');
}
function _applyDetailMap(records, map) {
  var today = new Date(); today.setHours(0,0,0,0);
  records.forEach(function(rec) {
    var d = map[rec.mstyle];
    if (!d) { rec._detail_loaded = true; return; }
    rec.rcv           = d.rcv;
    // ATS array fields used in detail panel
    rec._ats_arr      = d.ats;
    rec.supplier_info         = d.supplier_info;
    rec.fob_cost              = d.fob_cost;
    rec.elc_nj                = d.elc_nj;
    rec.elc_la                = d.elc_la;
    rec.mu_nj                 = d.mu_nj;
    rec.mu_la                 = d.mu_la;
    rec.qty_ord_supplier      = d.qty_ord_supplier;
    rec.pct_units_ord_supplier= d.pct_units_ord_supplier;
    rec.alt1_name = d.alt1_name; rec.alt1_fob = d.alt1_fob; rec.alt1_moq = d.alt1_moq; rec.alt1_lt = d.alt1_lt;
    rec.alt1_elc_nj = d.alt1_elc_nj; rec.alt1_elc_la = d.alt1_elc_la; rec.alt1_mu_nj = d.alt1_mu_nj; rec.alt1_mu_la = d.alt1_mu_la;
    rec.alt1_qty_ord = d.alt1_qty_ord; rec.alt1_pct_ord = d.alt1_pct_ord;
    rec.alt2_name = d.alt2_name; rec.alt2_fob = d.alt2_fob; rec.alt2_moq = d.alt2_moq; rec.alt2_lt = d.alt2_lt;
    rec.alt2_elc_nj = d.alt2_elc_nj; rec.alt2_elc_la = d.alt2_elc_la; rec.alt2_mu_nj = d.alt2_mu_nj; rec.alt2_mu_la = d.alt2_mu_la;
    rec.alt2_qty_ord = d.alt2_qty_ord; rec.alt2_pct_ord = d.alt2_pct_ord;
    rec.alt3_name = d.alt3_name; rec.alt3_fob = d.alt3_fob; rec.alt3_moq = d.alt3_moq; rec.alt3_lt = d.alt3_lt;
    rec.alt3_elc_nj = d.alt3_elc_nj; rec.alt3_elc_la = d.alt3_elc_la; rec.alt3_mu_nj = d.alt3_mu_nj; rec.alt3_mu_la = d.alt3_mu_la;
    rec.alt3_qty_ord = d.alt3_qty_ord; rec.alt3_pct_ord = d.alt3_pct_ord;
    rec.need_qty_main = d.need_qty_main; rec.need_etd_main = d.need_etd_main;
    rec.need_qty_alt1 = d.need_qty_alt1; rec.need_etd_alt1 = d.need_etd_alt1;
    rec.need_qty_alt2 = d.need_qty_alt2; rec.need_etd_alt2 = d.need_etd_alt2;
    rec.need_qty_alt3 = d.need_qty_alt3; rec.need_etd_alt3 = d.need_etd_alt3;
    rec._detail_loaded = true;
    // Re-run purchase_rec computation now that supplier data is available
    computeDerived(rec, today);
  });
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
    // Forward-simulate ATS OH+OO through 26 weeks by subtracting projected demand.
    // Using ATS (available-to-ship) instead of raw Qty OH so holds/allocations are excluded.
    var _atsRunning = rec.ats_oh_oo || 0;
    var _atsSimBeg = [];
    for (var _i = 0; _i < 26; _i++) {
      _atsSimBeg[_i] = _atsRunning;
      _atsRunning = Math.max(0, _atsRunning - (rec.prj[_i] || 0));
    }
    rec.ats_sim_beg = _atsSimBeg;
    var _lastAbove = -1;
    for (var _i = 0; _i < 26; _i++) {
      if (_atsSimBeg[_i] >= rec.opt_oh) _lastAbove = _i;
    }
    var _trigIdx = (_lastAbove >= 0 && _lastAbove < 25) ? _lastAbove + 1 : -1;
    if (_trigIdx >= 1 && _trigIdx <= 25) {
      var _trigInv = _atsSimBeg[_trigIdx];
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
        // Required ETD = receipt date minus ocean transit only
        // (ETD = goods depart origin; transit brings them to US; LT is separate production time)
        var _reqETD = addDays(_rcptDate, -rec.transit_days);
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
  { id:'inv_manager', label:'Inv Mgr', align:'left',
    get:function(r){return r.inv_manager;},
    render:function(r){return '<td title="'+esc(r.inv_manager)+'"><div class="cell-clamp2">'+esc(r.inv_manager)+'</div></td>';} },
  { id:'status_sub', label:'Status', align:'left',
    get:function(r){return r.item_status_flow||r.item_status||'';},
    filterValue:function(r){return (r.item_status_flow||r.item_status||'');},
    render:function(r){var s=r.item_status_flow||r.item_status||'';return '<td title="'+esc(s)+'"><div class="cell-clamp2">'+esc(s)+'</div></td>';} },
  { id:'customer_count', label:'Cust', align:'right', numeric:true, get:function(r){return r.customer_count;}, render:function(r){return '<td class="right">'+r.customer_count+'</td>';} },
  { id:'qty_oh', label:'Qty OH', align:'right', numeric:true, get:function(r){return r.qty_oh;},
    render:function(r){return '<td class="right '+(r.qty_oh<0?'neg':'')+'">'+fmtInt(r.qty_oh)+'</td>';} },
  { id:'ats_now', label:'ATS Now', align:'right', numeric:true, get:function(r){return r.ats_now;},
    render:function(r){return '<td class="right '+(r.ats_now<0?'neg':'')+'">'+fmtInt(r.ats_now)+'</td>';} },
  { id:'qty_oh_root', label:'Pcs OH (root)', align:'right', numeric:true,
    get:function(r){return r.is_multi?r.qty_oh_root:-1;},
    render:function(r){return '<td class="right">'+(r.is_multi?'<b>'+fmtInt(r.qty_oh_root)+'</b> <span style="color:#888;font-size:10px;">(+'+fmtInt(r.assembleable_kits)+')</span>':'<span style="color:#bbb;">&#8212;</span>')+'</td>';} },
  { id:'it_qty', label:'I/T', align:'right', numeric:true, get:function(r){return r.it_qty;}, render:function(r){return '<td class="right">'+fmtInt(r.it_qty)+'</td>';} },
  { id:'iw_qty', label:'I/W', align:'right', numeric:true, get:function(r){return r.iw_qty;}, render:function(r){return '<td class="right">'+fmtInt(r.iw_qty)+'</td>';} },
  { id:'hold_qty', label:'Hold', align:'right', numeric:true, get:function(r){return r.hold_qty;},
    render:function(r){return '<td class="right '+(r.hold_qty>0?'pri-MEDIUM':'')+'">'+fmtInt(r.hold_qty)+'</td>';} },
  { id:'open_cust_po_qty', label:'Open Cust PO', align:'right', numeric:true, get:function(r){return r.open_cust_po_qty;}, render:function(r){return '<td class="right">'+fmtInt(r.open_cust_po_qty)+'</td>';} },
  { id:'shp_wk_l4', label:'Shpd/Wk L4', align:'right', numeric:true, get:function(r){return r.shp_wk_l4;}, render:function(r){return '<td class="right">'+fmt(r.shp_wk_l4)+'</td>';} },
  { id:'shp_wk_l13', label:'Shpd/Wk L13', align:'right', numeric:true, get:function(r){return r.shp_wk_l13;}, render:function(r){return '<td class="right">'+fmt(r.shp_wk_l13)+'</td>';} },
  { id:'prj_wk', label:'Prj/Wk', align:'right', numeric:true, get:function(r){return r.prj_wk;}, render:function(r){return '<td class="right">'+fmtInt(r.prj_wk)+'</td>';} },
  { id:'prj_l4w_change', label:'+/- L4w', align:'right', numeric:true, get:function(r){return r.prj_l4w_change;},
    render:function(r){var l4w=r.prj_l4w_change;var up=l4w>5,dn=l4w<-5;var clr=up?'#2e7d32':dn?'#c62828':'inherit';var arr=up?'&#9650;':dn?'&#9660;':'';return '<td class="right" style="color:'+clr+';font-weight:'+(up||dn?'600':'400')+'">'+(arr?arr+' ':'')+fmt(l4w)+'%</td>';} },
  { id:'ats_wos_oh', label:'ATS WOS', align:'right', numeric:true, get:function(r){return r.ats_wos_oh;},
    render:function(r){return '<td class="right '+(r.ats_wos_oh>0&&r.ats_wos_oh<r.opt_wos?'pri-HIGH':'')+'">'+fmt(r.ats_wos_oh)+'</td>';} },
  { id:'ats_wos_oh_oo', label:'ATS WOS OH+OO', align:'right', numeric:true, get:function(r){return r.ats_wos_oh_oo;},
    render:function(r){return '<td class="right '+(r.ats_wos_oh_oo>0&&r.ats_wos_oh_oo<r.opt_wos?'pri-HIGH':'')+'">'+fmt(r.ats_wos_oh_oo)+'</td>';} },
  { id:'opt_oh', label:'Opt OH', align:'right', numeric:true, get:function(r){return r.opt_oh;}, render:function(r){return '<td class="right">'+fmtInt(r.opt_oh)+'</td>';} },
  { id:'days_oos_next_rcpt', label:'Days OOS&#8594;Rcpt', tooltip:'Days OOS until Next Available Receipt', align:'right', numeric:true, get:function(r){return r.days_oos_next_rcpt;},
    render:function(r){return '<td class="right '+(r.days_oos_next_rcpt>0?'pri-CRITICAL':'')+'">'+fmtInt(r.days_oos_next_rcpt)+'</td>';} },
  { id:'next_rcpt_dt', label:'Nxt Avl Rcpt', align:'left',
    get:function(r){return r.next_rcpt_dt?r.next_rcpt_dt.toISOString():'zzzz';},
    render:function(r){return '<td>'+fmtDate(r.next_rcpt_dt)+'</td>';} },
  { id:'gap_weeks_n', label:'OOS Wks', align:'right', numeric:true,
    get:function(r){return r.gap_weeks.length;},
    render:function(r){return '<td class="right '+(r.gap_weeks.length>0?'pri-CRITICAL':'')+'">'+r.gap_weeks.length+'</td>';} },
  { id:'oh_excess', label:'OH Excess', align:'right', numeric:true,
    get:function(r){return r.oh_excess;},
    tooltip:'OH Excess = Qty OH minus cumulative projected demand up to the Next Available Receipt date.\nNegative = expected OOS before receipt arrives.',
    render:function(r){var v=r.oh_excess;var neg=v<0;var cls='right '+(neg?'pri-CRITICAL':v>2500?'pri-HIGH':'');var disp=neg?'<span style="color:#c62828;font-weight:700;">('+fmtInt(-v)+')</span>':fmtInt(v);return '<td class="'+cls+'">'+disp+'</td>';} },
  { id:'pipeline_excess', label:'OH+OO Excess', align:'right', numeric:true,
    get:function(r){return r.pipeline_excess;},
    tooltip:'OH+OO Excess = (Qty OH + IT + IW + all open POs) minus cumulative projected demand up to the Next Available Receipt date.\nNegative = pipeline short. > 2,500 triggers Overstock flag.',
    render:function(r){var v=r.pipeline_excess;var neg=v<0;var cls='right '+(neg?'pri-CRITICAL':v>2500?'pri-HIGH':'');var disp=neg?'<span style="color:#c62828;font-weight:700;">('+fmtInt(-v)+')</span>':fmtInt(v);return '<td class="'+cls+'">'+disp+'</td>';} },
  { id:'pipeline_wos', label:'OH+OO WOS', align:'right', numeric:true,
    get:function(r){return r.pipeline_wos==null?1e9:r.pipeline_wos;},
    render:function(r){return '<td class="right">'+(r.pipeline_wos==null?'&#8734;':fmt(r.pipeline_wos))+'</td>';} },
  { id:'action', label:'Action', align:'left',
    get:function(r){if(!r.recommendations.length)return'CLEAN';var c={};r.recommendations.forEach(function(rc){c[rc.action]=(c[rc.action]||0)+1;});return Object.keys(c).sort(function(a,b){return c[b]-c[a];})[0];},
    render:function(r){return '<td>'+actionTag(r)+'</td>';} },
  { id:'purchase_rec', label:'Need to Buy', align:'right', numeric:true,
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
  // Auto-fit: distribute columns proportionally across 100% of the available
  // container width.  COL_WIDTHS still controls relative column proportions;
  // we just express them as percentages rather than fixed pixels so the table
  // always fills the viewport without a horizontal scrollbar.
  var totalW=cols.reduce(function(s,c){return s+(COL_WIDTHS[c.id]||62);},0);
  cg.innerHTML=cols.map(function(c){return '<col style="width:'+(((COL_WIDTHS[c.id]||62)/totalW)*100).toFixed(2)+'%">';}).join('');
  tbl.style.width='100%';

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
  var hideInactive=_chk('hideInactive',true);
  var activeCols=[];
  Object.keys(colFilters).forEach(function(cid){var c=COLS.find(function(x){return x.id===cid;});if(c)activeCols.push({c:c,needle:colFilters[cid].toLowerCase()});});
  return ALL.filter(function(r){
    if(q){var hay=(r.mstyle+' '+r.description+' '+r.brand).toLowerCase();if(hay.indexOf(q)<0)return false;}
    if(selCountries.size>0&&!selCountries.has(r.country))return false;
    if(selBrands.size>0&&!selBrands.has(r.brand))return false;
    if(selMgrs.size>0&&!selMgrs.has(r.inv_manager))return false;
    if(hideInactive&&!r.is_replen)return false;
    if(showNeedPurchase&&!(r.purchase_rec>0))return false;
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
  currentPage=0;
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
  var needPurchaseN = ALL.filter(function(r){return r.purchase_rec>0;}).length;
  var npActive = showNeedPurchase;
  var npBtn = '<button class="pri-btn '+(npActive?'active':'')+'" id="needPurchaseBtn" title="Show only records with a Need to Buy quantity" '
    +'style="margin-left:14px;background:'+(npActive?'#1b5e20':'#ffffff')+';color:'+(npActive?'#fff':'#1b5e20')+';border:1.5px solid #1b5e20;">'
    +'&#128722; Need to Buy <b style="margin-left:4px;">'+needPurchaseN.toLocaleString()+'</b></button>';
  document.getElementById('statsBar').innerHTML=allBtn+btn('CRITICAL','&#128308; Critical','#b71c1c')+btn('HIGH','&#128992; High','#e65100')+btn('MEDIUM','&#129001; Medium','#f9a825')+btn('LOW','&#9898; Low','#5d4037')+btn('NO_OOS','&#9898; No OOS','#9e9e9e')+npBtn+'<div class="stat" style="margin-left:14px;"><b>'+gapsN+'</b> OOS Risk</div><div class="stat"><b>'+overN+'</b> Overstocked</div><div class="stat"><b>'+inScope.toLocaleString()+'</b> Shown</div>';
  document.getElementById('statsBar').querySelectorAll('.pri-btn').forEach(function(b){b.onclick=function(){
    var key=b.dataset.pri;
    if(key==='__ALL__'){selPriorities.clear();}
    else{if(selPriorities.has(key))selPriorities.delete(key);else selPriorities.add(key);}
    syncPriorityDd();applyFilters();
  };});
  var npEl=document.getElementById('needPurchaseBtn');
  if(npEl){npEl.onclick=function(){showNeedPurchase=!showNeedPurchase;applyFilters();};}
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
  var totalPages=Math.max(1,Math.ceil(FILTERED.length/PAGE_SIZE));
  if(currentPage>=totalPages)currentPage=totalPages-1;
  var start=currentPage*PAGE_SIZE;
  var pageData=FILTERED.slice(start,start+PAGE_SIZE);
  var html='';
  pageData.forEach(function(r){
    var safeMs=r.mstyle.replace(/[^a-zA-Z0-9]/g,'_');
    html+='<tr class="row row-'+r.priority+'" data-ms="'+esc(r.mstyle)+'" onclick="toggleDetail(this.dataset.ms)">';
    cols.forEach(function(c){html+=c.render(r);});
    html+='</tr>';
    html+='<tr class="detail-pane" id="detail-'+safeMs+'" style="display:none"><td colspan="'+nCols+'"></td></tr>';
  });
  tb.innerHTML=html;
  renderPagination(totalPages);
}
function renderPagination(totalPages) {
  var bar=document.getElementById('pgBar');
  if(!bar)return;
  if(FILTERED.length<=PAGE_SIZE){bar.innerHTML='';return;}
  var html='';
  html+='<button onclick="goPage('+Math.max(0,currentPage-1)+')"'+(currentPage===0?' disabled':'')+'>&#8592; Prev</button>';
  // show at most 7 page buttons centered around currentPage
  var start=Math.max(0,currentPage-3), end=Math.min(totalPages-1,start+6);
  start=Math.max(0,end-6);
  for(var p=start;p<=end;p++){
    html+='<button class="'+(p===currentPage?'pg-active':'')+'" onclick="goPage('+p+')">'+(p+1)+'</button>';
  }
  html+='<button onclick="goPage('+Math.min(totalPages-1,currentPage+1)+')"'+(currentPage===totalPages-1?' disabled':'')+'>Next &#8594;</button>';
  var s=currentPage*PAGE_SIZE+1, e=Math.min(FILTERED.length,(currentPage+1)*PAGE_SIZE);
  html+='<span class="pg-info">'+s+'&#8211;'+e+' of '+FILTERED.length.toLocaleString()+'</span>';
  bar.innerHTML=html;
}
function goPage(p) {
  currentPage=p;
  renderTable();
  // scroll table back to top
  var wrap=document.getElementById('mainTable');
  if(wrap&&wrap.parentElement)wrap.parentElement.scrollTop=0;
}

// Fetch Open Customer PO data for a record and re-render its detail panel once loaded.
// Called after each initial renderDetail() -- no-op if already cached on the record.
function _loadCustOrdersAndUpdate(r, dtr) {
  if (r.cust_orders) return;
  fetchCustOrders(r.mstyle).then(function(orders) {
    r.cust_orders = orders;
    if (dtr && dtr.style.display === 'table-row') {
      try {
        dtr.querySelector('td').innerHTML = renderDetail(r);
        dtr.dataset.loaded = '1';
      } catch(e) {
        console.error('[InvMgmt] cust-orders re-render failed:', e);
      }
    }
  }).catch(function(e) {
    console.warn('[InvMgmt] fetchCustOrders failed for ' + r.mstyle + ':', e);
  });
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
  // If detail data hasn't loaded yet, show a loading placeholder and wait
  if (!r._detail_loaded && _detailPromise) {
    dtr.querySelector('td').innerHTML = '<div style="padding:20px;color:#666;font-style:italic;">&#8987; Loading supplier and detail data... (usually a few seconds on first open)</div>';
    dtr.dataset.loaded = '';  // mark as NOT loaded so it re-renders when data arrives
    _detailPromise.then(function() {
      if (dtr.style.display === 'table-row') {
        try {
          dtr.querySelector('td').innerHTML = renderDetail(r);
          dtr.dataset.loaded = '1';
          _loadCustOrdersAndUpdate(r, dtr);
        } catch(e) {
          dtr.querySelector('td').innerHTML = '<div style="padding:20px;color:#c62828;">Error rendering detail panel: ' + esc(String(e)) + '</div>';
          dtr.dataset.loaded = '1';
          console.error('[InvMgmt] renderDetail failed:', e);
        }
      }
    });
    return;
  }
  try {
    dtr.querySelector('td').innerHTML = renderDetail(r);
    dtr.dataset.loaded = '1';
    _loadCustOrdersAndUpdate(r, dtr);
  } catch(e) {
    dtr.querySelector('td').innerHTML = '<div style="padding:20px;color:#c62828;">Error rendering detail panel: ' + esc(String(e)) + '</div>';
    dtr.dataset.loaded = '1';
    console.error('[InvMgmt] renderDetail failed:', e);
  }
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
  var purTrigInv    = purTrigIdx >= 0 ? ((r.ats_sim_beg && r.ats_sim_beg[purTrigIdx]) || 0) : 0;
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
  function fmtCpoHover(wi, custOrds) {
    if (!custOrds.length) return '';
    var hasData = custOrds.some(function(o){ return (o.weekQtys[wi]||0) > 0; });
    if (!hasData) return '';
    var wkDate = (new Date(w1sun.getTime()+wi*7*86400000)).toLocaleDateString('en-US',{month:'short',day:'numeric'});
    var h = 'Open Cust POs - W'+(wi+1)+' (week of '+wkDate+')\n'+new Array(38).join('-')+'\n';
    custOrds.forEach(function(o) {
      var wkQty = Math.round(o.weekQtys[wi]||0);
      if (!wkQty) return;
      var cancel = o.cancelDate ? new Date(o.cancelDate).toLocaleDateString('en-US',{month:'short',day:'numeric',year:'2-digit'}) : '--';
      h += (o.custName||'Unknown')+': '+fmt(wkQty)+' | Cancel: '+cancel+'\n';
    });
    return h;
  }
  function fmtCpoTotalHover(custOrds) {
    if (!custOrds.length) return '';
    var h = 'Open Customer POs\n'+new Array(38).join('-')+'\n';
    custOrds.slice().sort(function(a,b){return b.qtyOpen-a.qtyOpen;}).forEach(function(o) {
      var cancel = o.cancelDate ? new Date(o.cancelDate).toLocaleDateString('en-US',{month:'short',day:'numeric',year:'2-digit'}) : '--';
      var start  = o.startShip  ? new Date(o.startShip).toLocaleDateString('en-US',{month:'short',day:'numeric'}) : '--';
      h += (o.custName||'Unknown')+': '+fmt(Math.round(o.qtyOpen))+' open of '+fmt(Math.round(o.qtyOrd))+' ord | Strt: '+start+' | Cancel: '+cancel+'\n';
    });
    return h;
  }

  // Rec action by PO for rcv row highlighting
  var recByPo={};
  (r.recommendations||[]).forEach(function(rc){if(rc.action==='PUSH_OUT'||rc.action==='PULL_UP')recByPo[rc.po_number]=rc.action;});
  var weekAction={};
  Object.keys(poByWeek).forEach(function(wi){poByWeek[wi].forEach(function(p){var action=recByPo[p.po_number];if(action&&(!weekAction[wi]||action==='PULL_UP'))weekAction[wi]=action;});});
  function rcvHL(wi){var a=weekAction[wi];if(a==='PUSH_OUT')return'background:#fff3e0;color:#e65100;font-weight:600;';if(a==='PULL_UP')return'background:#e3f2fd;color:#1565c0;font-weight:600;';return null;}

  // -- Forward-simulation WOS (matches Forecast Manager viewer logic) ---------
  // Simulates week-by-week depletion so spike weeks don't distort WOS.
  function _wosForward(bv, prj, startIdx) {
    if (!bv || bv <= 0 || !prj) return 0;
    var inv = bv, j;
    for (j = startIdx; j < prj.length; j++) {
      var d = prj[j] || 0;
      if (d > 0) {
        if (inv <= d) return (j - startIdx) + (inv / d);
        inv -= d;
      }
    }
    return prj.length - startIdx;
  }

  // -- Gap pre-compute: weeks below Opt WOS before next receipt ---------------
  var _ifOptWos = r.opt_wos || 0;
  var _ifGap = { weeks: [], nextRcptWeekIdx: -1, nextRcptDate: null };
  if (r.is_replen && _ifOptWos > 0) {
    // Coerce to Date -- may arrive as a string if loaded from a cache that lacked __D__ tags
    var _nrRaw  = r.next_rcpt_dt || null;
    var _nrDate = _nrRaw ? (_nrRaw instanceof Date ? _nrRaw : new Date(_nrRaw)) : null;
    if (_nrDate && isNaN(_nrDate.getTime())) _nrDate = null;
    var _nrIdx  = _nrDate ? wkIdxForDate(today, _nrDate) : 25;
    _ifGap.nextRcptDate    = _nrDate;
    _ifGap.nextRcptWeekIdx = _nrIdx;
    var _checkUntil = (_nrIdx < 0) ? -1 : Math.min(25, _nrIdx);
    for (var gi = 0; gi <= _checkUntil; gi++) {
      var _gbv  = r.beg_inv[gi] || 0;
      var _gwos = _wosForward(_gbv, r.prj, gi);
      if (_gbv <= 0 || _gwos < _ifOptWos) {
        _ifGap.weeks.push({ wi: gi + 1, wos: _gwos, deficit: _ifOptWos - _gwos });
      }
    }
  }

  // -- 26-week grid (Forecast Manager style: per-cell coloring + gap flags) ---
  var _invFmt1 = function(n) {
    if (n == null || !isFinite(n)) return ' - ';
    return n.toLocaleString('en-US', { minimumFractionDigits: 1, maximumFractionDigits: 1 });
  };
  var _mpLow  = r.master_pack || 1;
  var _lowThr = _mpLow * 2;

  var begCells = '<td class="lbl" style="color:#6d4c00;font-weight:600;background:#fffbea" title="Beginning-of-week projected warehouse inventory (Wk1..Wk26)">Beg Inv</td>';
  var prjCells = '<td class="lbl" style="color:#2e7d32;font-weight:600;background:#f1f8e9" title="Projected demand this week (Prj Wk1..Prj Wk26) - hover for customer breakdown">Prj Demand</td>';
  var rcvCells = '<td class="lbl" style="color:#1565c0;font-weight:600;background:#f0f7ff" title="Expected supplier receipts that week (RcvWk1..RcvWk26) - hover for PO detail">Expected Receipts</td>';
  var cpoCells = '<td class="lbl" style="color:#6a1b9a;font-weight:600;background:#f3e5f5" title="Open customer PO qty by week (A-Open W1-W26). Hover cells for customer / cancel date detail.">Open Customer POs</td>';
  var endCells = '<td class="lbl" style="color:#37474f;font-weight:600;background:#eceff1" title="Ending Inv = Beg Inv - Prj Demand - Open Customer POs + Expected Receipts">Ending Inv</td>';
  var wosCells = '<td class="lbl" style="color:#4a148c;font-weight:600;background:#f8f0fb" title="Weeks of Supply Onhand: forward simulation from Beg Inv over projected demand">WOS OH</td>';
  var begTot = 0, prjTot = 0, rcvTot = 0;

  for (var i = 0; i < 26; i++) {
    // -- Beg Inv: color-coded by health vs projected demand --
    var bv = r.beg_inv[i] || 0;
    begTot += bv;
    var prjThisWk = r.prj[i] || 0;
    var begClr = '#6d4c00';
    if      (bv < 0)                                          begClr = '#c62828';
    else if (bv === 0 && prjThisWk > 0)                       begClr = '#c62828';
    else if (bv > 0 && bv < _lowThr && prjThisWk > 0)         begClr = '#e65100';
    else if (bv === 0)                                         begClr = '#bbb';
    begCells += '<td style="color:'+begClr+';font-size:10px;background:#fffbea">'+fmt(Math.round(bv))+'</td>';

    // -- Prj Demand: show dash for zero, tooltip with customer breakdown --
    var pv = r.prj[i] || 0;
    prjTot += pv;
    var prjClr = pv > 0 ? '#2e7d32' : '#bbb';
    var prjTip = fmtPrjHover(i);
    var prjTA  = prjTip ? ' title="'+prjTip.replace(/"/g,'&quot;')+'"' : '';
    prjCells += '<td style="color:'+prjClr+';font-size:10px;background:#f1f8e9;'+(pv>0?'cursor:help;':'')+'"'+prjTA+'>'+(pv>0?fmt(Math.round(pv)):'&#8212;')+'</td>';

    // -- Expected Receipts: hover = PO detail; PUSH_OUT/PULL_UP highlighting --
    var rv = r.rcv[i] || 0;
    rcvTot += rv;
    var rcvClr = rv > 0 ? '#1565c0' : '#bbb';
    var rcvTip = fmtPoHover(i);
    var rcvTA  = rcvTip ? ' title="'+rcvTip.replace(/"/g,'&quot;')+'"' : '';
    var rcvHlS = rcvHL(i) || '';
    rcvCells += '<td style="color:'+rcvClr+';font-size:10px;background:#f0f7ff;'+(rcvTip?'cursor:help;':'')+rcvHlS+'"'+rcvTA+'>'+(rv>0?fmt(rv):'&#8212;')+'</td>';

    // -- Open Customer POs: per-week from A-Open W1..W26 (fetched async on panel open) --
    var _custOrds = r.cust_orders || [];
    var cpoWkVal = 0;
    _custOrds.forEach(function(o){ cpoWkVal += (o.weekQtys[i] || 0); });
    cpoWkVal = Math.round(cpoWkVal);
    var cpoTip = fmtCpoHover(i, _custOrds);
    var cpoTA  = cpoTip ? ' title="'+cpoTip.replace(/"/g,'&quot;')+'"' : '';
    var cpoClr = cpoWkVal > 0 ? '#6a1b9a' : '#bbb';
    cpoCells += '<td style="color:'+cpoClr+';font-size:10px;background:#f3e5f5;'+(cpoTip?'cursor:help;':'')+'"'+cpoTA+'>'+(cpoWkVal>0?fmt(cpoWkVal):'&#8212;')+'</td>';

    // -- Ending Inv per week: Beg - Prj - OpenCustPO + Receipts --
    var ev = Math.round(bv - pv - cpoWkVal + rv);
    var evClr = ev < 0 ? '#c62828' : (ev === 0 ? '#bbb' : '#37474f');
    endCells += '<td style="color:'+evClr+';font-size:10px;background:#eceff1;">'+fmt(ev)+'</td>';

    // -- WOS OH: forward simulation; gap weeks highlighted in red --
    var wosVal, wosTxt, wosClr, wosCellBg = '#f8f0fb';
    if (bv > 0) {
      wosVal = _wosForward(bv, r.prj, i);
      var maxWks = 26 - i;
      if (wosVal >= maxWks) {
        wosTxt = _invFmt1(wosVal); wosClr = '#1b5e20';
      } else {
        wosTxt = _invFmt1(wosVal);
        if      (wosVal < 1)                               wosClr = '#c62828';
        else if (wosVal < _ifOptWos && _ifOptWos > 0)      wosClr = '#e65100';
        else if (wosVal < 4)                               wosClr = '#e65100';
        else                                               wosClr = '#4a148c';
      }
    } else {
      wosVal = 0; wosTxt = ' - '; wosClr = '#bbb';
    }
    var isGapWk = _ifOptWos > 0 && _ifGap.nextRcptWeekIdx >= 0
      && i <= Math.min(25, _ifGap.nextRcptWeekIdx) && bv > 0 && wosVal < _ifOptWos;
    if (isGapWk) { wosCellBg = '#ffebee'; wosClr = '#c62828'; }
    var wosXtra = isGapWk ? ' title="Gap: WOS '+_invFmt1(wosVal)+' &lt; Opt WOS '+_invFmt1(_ifOptWos)+'"' : '';
    var wosBold = (isGapWk || (bv > 0 && wosVal < 4)) ? 700 : 400;
    wosCells += '<td style="color:'+wosClr+';font-size:10px;background:'+wosCellBg+';font-weight:'+wosBold+'"'+wosXtra+'>'+wosTxt+'</td>';
  }

  // Total cells (WOS total is not meaningful so leave as dash)
  begCells += '<td style="font-weight:700;color:#6d4c00;background:#fffbea">'+fmt(Math.round(begTot))+'</td>';
  prjCells += '<td style="font-weight:700;color:#2e7d32;background:#f1f8e9">'+fmt(Math.round(prjTot))+'</td>';
  rcvCells += '<td style="font-weight:700;color:#1565c0;background:#f0f7ff">'+fmt(Math.round(rcvTot))+'</td>';
  var _custOrdsFinal = r.cust_orders || [];
  var cpTot = _custOrdsFinal.length
    ? Math.round(_custOrdsFinal.reduce(function(s,o){return s+o.qtyOpen;},0))
    : (r.open_cust_po_qty || 0);
  var cpoTotTip = fmtCpoTotalHover(_custOrdsFinal);
  var cpoTotTA  = cpoTotTip ? ' title="'+cpoTotTip.replace(/"/g,'&quot;')+'"' : '';
  cpoCells += '<td style="font-weight:700;color:#6a1b9a;background:#f3e5f5;'+(_custOrdsFinal.length?'cursor:help;':'')+'"'+cpoTotTA+'>'+fmt(cpTot)+'</td>';
  var endTot = Math.round((r.beg_inv[0]||0) - prjTot - cpTot + rcvTot);
  var endTotClr = endTot < 0 ? '#c62828' : '#37474f';
  endCells += '<td style="font-weight:700;color:'+endTotClr+';background:#eceff1;">'+fmt(endTot)+'</td>';
  wosCells += '<td style="color:#bbb;background:#f8f0fb" title="WOS total not meaningful"> - </td>';

  // Header row with MM/DD week dates
  var ifHdr = '<th class="lbl" style="background:#ede9fe;"></th>';
  for (var i = 1; i <= 26; i++) {
    var s = new Date(w1sun.getTime() + (i-1)*7*86400000);
    var lbl = (s.getMonth()+1)+'/'+s.getDate();
    ifHdr += '<th title="W'+i+' - week of '+s.toLocaleDateString('en-US',{month:'short',day:'numeric',year:'numeric'})
      +'" style="line-height:1.15;">'
      +'<div style="font-size:9px;color:#7b1fa2;font-weight:600;">Wk'+i+'</div>'
      +'<div>'+lbl+'</div>'
      +'</th>';
  }
  ifHdr += '<th>Total</th>';

  var invFlow = '<table class="subtbl grid26"><tr style="background:#ede9fe;">'+ifHdr+'</tr>'
    +'<tr>'+begCells+'</tr>'
    +'<tr>'+prjCells+'</tr>'
    +'<tr>'+rcvCells+'</tr>'
    +'<tr>'+cpoCells+'</tr>'
    +'<tr>'+endCells+'</tr>'
    +'<tr>'+wosCells+'</tr>'
    +'</table>';

  // Gap banner (placed outside the overflow-x:auto scroll div -- see return statement)
  var invGapBanner = '';
  if (_ifOptWos > 0 && r.is_replen) {
    var _optStr = _invFmt1(_ifOptWos);
    var _nrStr  = _ifGap.nextRcptDate
      ? _ifGap.nextRcptDate.toLocaleDateString('en-US', { month:'short', day:'numeric', year:'numeric' })
      : 'unknown';
    var _nrWkStr = (_ifGap.nextRcptWeekIdx >= 0 && _ifGap.nextRcptWeekIdx <= 25)
      ? '(W'+(_ifGap.nextRcptWeekIdx+1)+')' : (_ifGap.nextRcptWeekIdx > 25 ? '(beyond W26)' : '');
    var _fmLink = '<a href="https://pim.quickbase.com/db/bpd24h9wy?a=dbpage&pageID=50&search='+encodeURIComponent(r.mstyle)+'" target="_blank" style="color:inherit;font-weight:700;text-decoration:underline;">View in Forecast Manager &#8599;</a>';
    if (_ifGap.weeks.length === 0) {
      invGapBanner = '<div style="margin-top:6px;padding:6px 10px;background:#e8f5e9;border:1px solid #a5d6a7;border-radius:4px;font-size:11px;color:#1b5e20;">&#10003; <b>No gaps:</b> all weeks through next receipt '+_nrStr+' '+_nrWkStr+' maintain '+_optStr+' WOS (Opt WOS). &nbsp;'+_fmLink+'</div>';
    } else {
      invGapBanner = '<div style="margin-top:6px;padding:6px 10px;background:#ffebee;border:1px solid #ef9a9a;border-radius:4px;font-size:11px;color:#b71c1c;">&#x26a0; <b>Inventory Gap:</b> '+_ifGap.weeks.length+' week'+(_ifGap.weeks.length===1?'':'s')+' below Opt WOS ('+_optStr+') before next receipt '+_nrStr+' '+_nrWkStr+'. Moving up open POs may close this gap. &nbsp;'+_fmLink+'</div>';
    }
  } else if (!r.is_replen) {
    invGapBanner = '<div style="margin-top:6px;padding:4px 10px;background:#fafafa;border:1px solid #e0e0e0;border-radius:4px;font-size:10px;color:#888;font-style:italic;">Gap analysis only runs on Replen items (Status: '+esc(r.item_status_flow||'unknown')+').</div>';
  }

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

  function kvRow(lbl,val){return '<div style="display:flex;gap:6px;padding:1px 0;font-size:11px;line-height:1.45;"><span style="color:#666;min-width:80px;flex-shrink:0;">'+lbl+'</span><span style="font-weight:500;color:#222;">'+val+'</span></div>';}
  // kvBox: pass width (e.g. '220px') for a fixed-size box; omit for flex:1 stretchy
  function kvBox(lbl,rows,bg,flex,width){
    var flexStyle=width?'flex:0 0 auto;width:'+width+';':'flex:'+(flex||'1')+';min-width:160px;';
    return '<div style="'+flexStyle+'background:'+(bg||'#f8f9fa')+';border:1px solid #e4e7eb;border-radius:4px;padding:8px 10px;">'+(lbl?'<div style="font-size:10px;font-weight:700;color:#9e9e9e;text-transform:uppercase;letter-spacing:0.6px;margin-bottom:5px;">'+lbl+'</div>':'')+rows+'</div>';}

  var identityBox=kvBox('Identity',kvRow('Mstyle','<b>'+esc(r.mstyle)+'</b>')+kvRow('Rank',esc(r.item_rank)||'&#8212;')+kvRow('Status',esc(r.item_status_flow)||'&#8212;')+kvRow('Sub Stat',esc(r.sub_status)||'&#8212;')+(r.season?kvRow('Season',esc(r.season)):'')+( r.size_ct?kvRow('Size/Ct',esc(r.size_ct)):'')+( r.fragrance?kvRow('Fragrance',esc(r.fragrance)):'')+( flagBadges.length?'<div style="margin-top:6px;display:flex;gap:4px;flex-wrap:wrap;">'+flagBadges.join('')+'</div>':''),'#e3f2fd',null,'220px');
  var itemDataBox=kvBox('Item Data','<div style="display:grid;grid-template-columns:1fr 1fr;gap:0 10px;"><div>'+kvRow('Inner Pack',r.inner_pack||'&#8212;')+kvRow('Master Pack',r.master_pack||'&#8212;')+kvRow('MOQ',r.moq?fmt(r.moq):'-')+kvRow('Opt OH',fmtInt(r.opt_oh))+'</div><div>'+kvRow('Opt WOS',fmt(r.opt_wos))+kvRow('LT (Wks)',fmt(r.lt_wks))+kvRow('LT+Opt Wks',fmt(r.lt_opt_weeks))+(r.upc?kvRow('UPC #',esc(r.upc)):'')+( r.gtin?kvRow('GTIN #',esc(r.gtin)):'')+'</div></div>','#e8f5e9',null,'340px');
  var stockStatusBox='<div style="flex:0 0 auto;width:300px;background:#f3e5f5;border:1px solid #e4e7eb;border-radius:4px;padding:8px 10px;"><div style="font-size:10px;font-weight:700;color:#9e9e9e;text-transform:uppercase;letter-spacing:0.6px;margin-bottom:6px;">Stock Status</div><div style="display:grid;grid-template-columns:1fr 1fr;gap:0 8px;">'+
    '<div>'+kvRow('Qty OH','<b>'+fmtInt(r.qty_oh)+'</b>')+kvRow('Qty I/W',fmtInt(r.iw_qty))+kvRow('Qty I/T',fmtInt(r.it_qty))+kvRow('OH WOS','<span style="color:'+wosColor(r.ats_wos_oh)+'">'+fmt(r.ats_wos_oh)+'</span>')+kvRow('OH+OO WOS','<span style="color:'+wosColor(r.ats_wos_oh_oo)+'">'+fmt(r.ats_wos_oh_oo)+'</span>')+'</div>'+
    '<div>'+kvRow('ATS Now','<b style="color:'+(r.ats_now<0?'#c62828':'inherit')+'">'+fmtInt(r.ats_now)+'</b>')+kvRow('ATS OH',fmtInt(r.ats_qty_oh))+kvRow('ATS OH+OO',fmtInt(r.ats_oh_oo))+kvRow('ATS OH WOS','<span style="color:'+wosColor(r.ats_wos_oh)+'">'+fmt(r.ats_wos_oh)+'</span>')+kvRow('ATS OH+OO WOS','<span style="color:'+wosColor(r.ats_wos_oh_oo)+'">'+fmt(r.ats_wos_oh_oo)+'</span>')+'</div>'+
    '</div></div>';

  var atsHtml='<table class="subtbl"><tr><th>Position</th><th class="right">Qty</th><th>Position</th><th class="right">Qty</th><th>WOS Metric</th><th class="right">Value</th></tr><tr><td>Qty OH (total)</td><td class="right"><b>'+fmtInt(r.qty_oh)+'</b></td><td>I/T (in transit)</td><td class="right">'+fmtInt(r.it_qty)+'</td><td>ATS WOS OH</td><td class="right">'+fmt(r.ats_wos_oh)+'</td></tr><tr><td>ATS Qty OH</td><td class="right">'+fmtInt(r.ats_qty_oh)+'</td><td>I/W (in work)</td><td class="right">'+fmtInt(r.iw_qty)+'</td><td>ATS WOS OH+OO</td><td class="right">'+fmt(r.ats_wos_oh_oo)+'</td></tr><tr><td>ATS Now</td><td class="right"><b>'+fmtInt(r.ats_now)+'</b></td><td>I/T + I/W</td><td class="right">'+fmtInt(r.it_iw)+'</td><td>ATS WOS OH+OO (w/ kits)</td><td class="right">'+fmt(r.ats_wos_oh_oo_w_kits)+'</td></tr><tr><td>ATS OH + OO</td><td class="right">'+fmtInt(r.ats_oh_oo)+'</td><td>I/W + I/T w/ Kits</td><td class="right">'+fmtInt(r.it_iw_kits)+'</td><td>ATS WOS (w/o test/excl)</td><td class="right">'+fmt(r.ats_wos_oh_oo_wo_test)+'</td></tr><tr><td>ATS OH + OO (w/ kits)</td><td class="right">'+fmtInt(r.ats_oh_oo_w_kits)+'</td><td>Open Cust PO Qty</td><td class="right">'+fmtInt(r.open_cust_po_qty)+'</td><td>ATS OH + I/T Booked WOS</td><td class="right">'+fmt(r.ats_oh_it_booked_wos)+'</td></tr><tr><td>ATS Qty (not alloc\'d)</td><td class="right">'+fmtInt(r.ats_qty_not_alloc)+'</td><td>Hold Order Qty</td><td class="right '+(r.hold_qty>0?'pri-MEDIUM':'')+'">'+fmtInt(r.hold_qty)+'</td><td>Opt WOS</td><td class="right"><b>'+fmt(r.opt_wos)+'</b></td></tr><tr><td>NJ ATS OH</td><td class="right">'+fmtInt(r.nj_ats_oh)+'</td><td>Test Order Qty</td><td class="right">'+fmtInt(r.test_order_qty)+'</td><td>Opt OH</td><td class="right">'+fmt(r.opt_oh)+'</td></tr><tr><td>CA ATS OH</td><td class="right">'+fmtInt(r.ca_ats_oh)+'</td><td>Exclude PO from WOS</td><td class="right">'+fmtInt(r.exclude_po_wos)+'</td><td>LT (Wks) / CNY / LT+Opt</td><td class="right">'+fmt(r.lt_wks)+' / '+fmt(r.cny_weeks)+' / '+fmt(r.lt_opt_weeks)+'</td></tr></table>';

  var demandHtml='<table class="subtbl"><tr><th>Demand</th><th class="right">Qty</th><th>Shipments</th><th class="right">Qty</th><th>Date</th><th>Value</th></tr><tr><td>Prj / Wk</td><td class="right"><b>'+fmt(r.prj_wk)+'</b></td><td>Shpd / Wk L4</td><td class="right"><b>'+fmt(r.shp_wk_l4)+'</b></td><td>Last Shp Date</td><td>'+fmtDate(r.last_shp_date)+'</td></tr><tr><td>Max Prj / Wk</td><td class="right">'+fmt(r.max_prj_wk)+'</td><td>Shpd / Wk L13</td><td class="right"><b>'+fmt(r.shp_wk_l13)+'</b></td><td>1st Shpd Date</td><td>'+fmtDate(r.first_shpd_date)+'</td></tr><tr><td>+/- Prj L4w</td><td class="right">'+fmt(r.prj_l4w_change)+'%</td><td>Total Shpd L4</td><td class="right">'+fmtInt(r.tot_shpd_l4)+'</td><td>Date 1st Rcvd</td><td>'+fmtDate(r.date_1st_rcvd)+'</td></tr><tr><td>Prj 26 Wks</td><td class="right">'+fmtInt(r.prj_26wks)+'</td><td>Total Shpd L13w</td><td class="right">'+fmtInt(r.tot_shpd_l13w)+'</td><td>Last Whs Rcvd</td><td>'+fmtDate(r.last_whs_rcvd)+'</td></tr><tr><td>Manual demand (rollup)</td><td class="right">'+fmtInt(r.manual_demand_26w)+'</td><td>Total Shpd LTD</td><td class="right">'+fmtInt(r.tot_shpd_ltd)+'</td><td>1st Out Date</td><td>'+fmtDate(r.first_out_date)+'</td></tr><tr><td>Demand (Inv Flow 26w Sum)</td><td class="right">'+fmtInt(r.demand_26w)+'</td><td colspan="2"></td><td>Last OOS Date</td><td>'+fmtDate(r.last_oos_date)+'</td></tr></table><div class="stat-text" style="margin-top:4px;"><b>Days OOS till Next Rcpt:</b> '+fmtInt(r.days_oos_next_rcpt)+' / <b>Days OOS L12m:</b> '+fmtInt(r.days_oos_l12m)+'</div>';

  function kpi(label,value,hint,color){return '<div class="kpi"'+(hint?' title="'+esc(hint)+'"':'')+'>  <div class="kpi-lbl">'+label+'</div><div class="kpi-val" style="'+(color?'color:'+color+';':'')+'">'+value+'</div></div>';}
  function wosColor(w){if(w==null||w===0)return'#888';if(w<r.opt_wos)return'#c62828';if(w>26)return'#1b5e20';return'#1565c0';}
  function excessColor(e){return e>2500?'#c62828':(e<-2500?'#e65100':'#1b5e20');}
  function oosColor(d){return d>14?'#c62828':(d>0?'#e65100':'#1b5e20');}
  var kpiStrip='<div class="kpi-strip">'+kpi('ATS Now',fmtInt(r.ats_now),'Available to sell - after holds / allocations',r.ats_now<0?'#c62828':'')+kpi('ATS WOS OH',fmt(r.ats_wos_oh),'Weeks of supply on hand (per QB)',wosColor(r.ats_wos_oh))+kpi('Open Cust PO',fmtInt(r.open_cust_po_qty),'Outstanding customer PO qty awaiting shipment','')+kpi('Hold Qty',fmtInt(r.hold_qty),'Hold Order Qty - orders parked, not shipping',r.hold_qty>0?'#e65100':'')+kpi('Days->Next Rcpt',fmtInt(r.days_oos_next_rcpt),'Days OOS until next supplier receipt arrives',oosColor(r.days_oos_next_rcpt))+kpi('Pipe Excess',fmtInt(r.pipeline_excess),'Total pipeline - 26w demand - safety stock. Positive = overstock',excessColor(r.pipeline_excess))+kpi('PipeWOS',(r.pipeline_wos==null?'&#8734;':fmt(r.pipeline_wos)),'Pipeline weeks of supply (all I/T + I/W + OH / 26w demand)','')+kpi('LT + CNY',fmt(r.lt_wks)+' + '+fmt(r.cny_weeks),'Lead time (weeks) + Chinese New Year shutdown weeks','')+kpi('Days OOS L12m',fmtInt(r.days_oos_l12m),'Days out-of-stock in trailing 12 months',r.days_oos_l12m>30?'#c62828':'')+kpi('Customers',fmtInt(r.customer_count),'Active Acct-MStyles rolled up into this mstyle','')+kpi('Manual Demand',fmtInt(r.manual_demand_26w),'26-week sum of customer manual projections (rolled up)','')+'</div>'+(r.is_multi?'<div style="margin-top:8px;padding:6px 10px;background:#fff8e1;border:1px solid #ffe082;border-radius:4px;font-size:11px;color:#5d4037;">&#127873; <b>Multi-pack:</b> Each unit = '+r.pcs_per_kit+' pcs of root <b>'+esc(r.root_mstyle)+'</b>. Root OH: <b>'+fmtInt(r.qty_oh_root)+'</b> pcs -> can assemble <b>'+fmtInt(r.assembleable_kits)+'</b> more kits. Total effective kit availability: '+fmt((r.beg_inv&&r.beg_inv[0])||0)+' on-hand + '+fmtInt(r.assembleable_kits)+' buildable = <b>'+fmt(((r.beg_inv&&r.beg_inv[0])||0)+r.assembleable_kits)+'</b> kits.</div>':'');

  var totalAged=(r.aged_inv_0_90||0)+(r.aged_inv_91_180||0)+(r.aged_inv_181_365||0)+(r.aged_inv_365plus||0);
  function agePct(n){return totalAged>0?Math.round(n/totalAged*100)+'%':'--';}
  function ageCard(lbl,val,pct,bg,valColor){
    return '<div class="age-card" style="background:'+bg+';">'
      +'<div class="age-card-lbl">'+lbl+'</div>'
      +'<div class="age-card-val" style="color:'+valColor+'">'+fmtInt(val)+'</div>'
      +'<div class="age-card-pct">'+pct+' of total</div>'
      +'</div>';
  }
  var _ageClr=r.invtry_age_days>180?'#c62828':r.invtry_age_days>90?'#e65100':'#1565c0';
  var agedInvHtml='<div class="age-cards">'
    +'<div class="age-card" style="background:#f0f4ff;">'
    +'<div class="age-card-lbl">Avg Inv Age</div>'
    +'<div class="age-card-val" style="color:'+_ageClr+'">'+Math.round(r.invtry_age_days||0)+'</div>'
    +'<div class="age-card-pct">days</div>'
    +'</div>'
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
    // Initialize purchaseSelections from QB-stored values on first render
    var _msId = r.mstyle.replace(/[^a-zA-Z0-9]/g, '_');
    if (!purchaseSelections[r.mstyle]) {
      purchaseSelections[r.mstyle] = {
        main: { checked:(r.need_qty_main||0)>0, needQty:r.need_qty_main||0, etd:r.need_etd_main||'' },
        alt1: { checked:(r.need_qty_alt1||0)>0, needQty:r.need_qty_alt1||0, etd:r.need_etd_alt1||'' },
        alt2: { checked:(r.need_qty_alt2||0)>0, needQty:r.need_qty_alt2||0, etd:r.need_etd_alt2||'' },
        alt3: { checked:(r.need_qty_alt3||0)>0, needQty:r.need_qty_alt3||0, etd:r.need_etd_alt3||'' }
      };
    }
    var purSel = purchaseSelections[r.mstyle];

    // Compute total need qty across checked suppliers
    var totalNeedQty = 0;
    ['main','alt1','alt2','alt3'].forEach(function(k){var ss=purSel[k]||{};if(ss.checked)totalNeedQty+=(ss.needQty||0);});

    // Summary bar: Rec Qty | Total Need Qty (read-only) | Required ETD (baseline) | Receipt Needed By
    var etdColor = r.purchase_rec_push_supplier ? '#c62828' : '#e65100';
    var etdDisplay = r.purchase_rec_etd ? fmtDate(r.purchase_rec_etd) : '&#8212;';
    var pushBadge = r.purchase_rec_push_supplier
      ? ' <span style="font-size:10px;font-weight:700;background:#c62828;color:#fff;border-radius:3px;padding:1px 5px;vertical-align:middle;">PUSH SUPPLIER</span>'
      : '';
    var nxtAvlNote = (r.purchase_rec_push_supplier && r.nxt_avl_etd)
      ? '<div style="font-size:10px;color:#c62828;margin-top:2px;">Supplier earliest: '+fmtDate(r.nxt_avl_etd)+'</div>'
      : '';

    var purSummary = '<div style="display:flex;gap:0;flex-wrap:wrap;align-items:stretch;background:#e8eaf6;border:1px solid #c5cae9;border-radius:6px;margin-bottom:16px;overflow:hidden;">'
      +'<div style="flex:0 0 auto;padding:12px 20px;border-right:1px solid #c5cae9;">'
        +'<div style="font-size:10px;font-weight:700;color:#5c6bc0;text-transform:uppercase;letter-spacing:0.5px;margin-bottom:3px;">Recommended Qty</div>'
        +'<div style="font-size:24px;font-weight:700;color:#1a237e;line-height:1;">'+fmtInt(r.purchase_rec)+'</div>'
        +'<div style="font-size:10px;color:#888;margin-top:1px;">units</div>'
      +'</div>'
      +'<div style="flex:0 0 auto;padding:12px 20px;border-right:1px solid #c5cae9;">'
        +'<div style="font-size:10px;font-weight:700;color:#5c6bc0;text-transform:uppercase;letter-spacing:0.5px;margin-bottom:3px;">Total Need Qty</div>'
        +'<div style="font-size:24px;font-weight:700;color:#1a237e;line-height:1;" id="purTotalNeedQty_'+_msId+'">'+(totalNeedQty||0).toLocaleString()+'</div>'
        +'<div style="font-size:10px;color:#888;margin-top:1px;">sum of checked suppliers</div>'
      +'</div>'
      +'<div style="flex:0 0 auto;padding:12px 20px;border-right:1px solid #c5cae9;">'
        +'<div style="font-size:10px;font-weight:700;color:#5c6bc0;text-transform:uppercase;letter-spacing:0.5px;margin-bottom:3px;">Baseline Required ETD</div>'
        +'<div style="font-size:18px;font-weight:700;color:'+etdColor+';">'+etdDisplay+pushBadge+'</div>'
        +nxtAvlNote
        +'<div style="font-size:10px;color:#888;margin-top:1px;">use to set per-supplier ETD below</div>'
      +'</div>'
      +'<div style="flex:0 0 auto;padding:12px 20px;">'
        +'<div style="font-size:10px;font-weight:700;color:#5c6bc0;text-transform:uppercase;letter-spacing:0.5px;margin-bottom:3px;">Receipt Needed By</div>'
        +'<div style="font-size:18px;font-weight:700;color:#333;">'+(r.purchase_rec_receipt_date?fmtDate(r.purchase_rec_receipt_date):'&#8212;')+'</div>'
        +'<div style="font-size:10px;color:#888;margin-top:1px;">before Wk '+purTrigWk+' dip</div>'
      +'</div>'
      +'</div>';

    // Build supplier columns
    var suppCols = [];
    var mainName = '';
    if (r.supplier_info) {
      var beforeBr = r.supplier_info.split(/<br\s*\/?>/i)[0];
      mainName = stripHtml(beforeBr).trim();
    }
    if (!mainName && r.fob_cost > 0) mainName = 'Main Supplier';
    if (mainName || r.fob_cost > 0 || r.elc_nj > 0) {
      suppCols.push({ key:'main', badge:'Main', label:mainName||'Main Supplier',
        fob:r.fob_cost, moq:r.moq, lt:r.lt_trans_days,
        elc_nj:r.elc_nj, elc_la:r.elc_la, mu_nj:r.mu_nj, mu_la:r.mu_la,
        qty_ord:r.qty_ord_supplier, pct_ord:r.pct_units_ord_supplier });
    }
    if (r.alt1_name) suppCols.push({ key:'alt1', badge:'ALT 1', label:r.alt1_name,
      fob:r.alt1_fob, moq:r.alt1_moq, lt:r.alt1_lt,
      elc_nj:r.alt1_elc_nj, elc_la:r.alt1_elc_la, mu_nj:r.alt1_mu_nj, mu_la:r.alt1_mu_la,
      qty_ord:r.alt1_qty_ord, pct_ord:r.alt1_pct_ord });
    if (r.alt2_name) suppCols.push({ key:'alt2', badge:'ALT 2', label:r.alt2_name,
      fob:r.alt2_fob, moq:r.alt2_moq, lt:r.alt2_lt,
      elc_nj:r.alt2_elc_nj, elc_la:r.alt2_elc_la, mu_nj:r.alt2_mu_nj, mu_la:r.alt2_mu_la,
      qty_ord:r.alt2_qty_ord, pct_ord:r.alt2_pct_ord });
    if (r.alt3_name) suppCols.push({ key:'alt3', badge:'ALT 3', label:r.alt3_name,
      fob:r.alt3_fob, moq:r.alt3_moq, lt:r.alt3_lt,
      elc_nj:r.alt3_elc_nj, elc_la:r.alt3_elc_la, mu_nj:r.alt3_mu_nj, mu_la:r.alt3_mu_la,
      qty_ord:r.alt3_qty_ord, pct_ord:r.alt3_pct_ord });

    var bestMU = suppCols.reduce(function(b, s){ return (s.mu_nj||0) > b ? (s.mu_nj||0) : b; }, 0);

    function scRow(lbl, val, hl) {
      var vStyle = hl ? 'font-weight:700;color:#1b5e20;' : 'color:#222;font-weight:500;';
      return '<div style="display:flex;justify-content:space-between;align-items:baseline;padding:4px 0;border-bottom:1px solid #f0f0f0;font-size:12px;">'
        +'<span style="color:#666;">'+lbl+'</span><span style="'+vStyle+'">'+val+'</span></div>';
    }
    function scRowPair(lbl1, val1, lbl2, val2, hl1, hl2) {
      var s1 = hl1 ? 'font-weight:700;color:#1b5e20;' : 'color:#222;font-weight:500;';
      var s2 = hl2 ? 'font-weight:700;color:#1b5e20;' : 'color:#222;font-weight:500;';
      return '<div style="display:flex;justify-content:space-between;align-items:baseline;padding:4px 0;border-bottom:1px solid #f0f0f0;font-size:12px;">'
        +'<span style="color:#666;">'+lbl1+'</span><span style="'+s1+'margin-right:10px;">'+val1+'</span>'
        +'<span style="color:#666;">'+lbl2+'</span><span style="'+s2+'">'+val2+'</span></div>';
    }
    function scSection(lbl) {
      return '<div style="font-size:9px;font-weight:700;color:#9e9e9e;text-transform:uppercase;letter-spacing:0.5px;padding:6px 0 2px;">'+lbl+'</div>';
    }

    // Receipt date ISO string for ETD default calculation
    var _rcptIso = r.purchase_rec_receipt_date ? r.purchase_rec_receipt_date.toISOString().slice(0,10) : '';

    var suppCards = '<div style="display:flex;gap:12px;flex-wrap:wrap;">';
    if (suppCols.length === 0) {
      suppCards += '<div style="color:#888;font-style:italic;font-size:12px;">No supplier data available for this item.</div>';
    } else {
      suppCols.forEach(function(s) {
        var ss = purSel[s.key] || { checked:false, needQty:0, etd:'' };
        var isBestMU = (s.mu_nj||0) === bestMU && bestMU > 0;
        var cardBorder = ss.checked ? '2px solid #1b5e20' : (isBestMU ? '2px solid #43a047' : '1px solid #ddd');
        var cardBg     = ss.checked ? '#f1f8e9' : '#fff';
        var hdrBg      = s.key === 'main' ? '#283593' : '#3f51b5';

        // Default ETD: same receipt-needed-by date for all suppliers
        // (supplier knows their own lead time; we communicate one target date)
        var defaultEtd = ss.etd || _rcptIso;

        var cardId    = 'suppCard_'+_msId+'_'+s.key;
        var inputsId  = 'suppInputs_'+_msId+'_'+s.key;
        var chkId     = 'suppChk_'+_msId+'_'+s.key;
        var nqId      = 'suppNQ_'+_msId+'_'+s.key;
        var etdId     = 'suppETD_'+_msId+'_'+s.key;
        var msEsc     = esc(r.mstyle);

        suppCards += '<div id="'+cardId+'" style="flex:1;min-width:190px;max-width:270px;border:'+cardBorder+';border-radius:6px;background:'+cardBg+';overflow:hidden;">'
          +'<div style="background:'+hdrBg+';color:#fff;padding:8px 10px;">'
            +'<div style="font-size:9px;font-weight:700;text-transform:uppercase;letter-spacing:0.8px;opacity:0.8;margin-bottom:2px;">'+esc(s.badge)+'</div>'
            +'<div style="font-size:13px;font-weight:700;line-height:1.25;">'+esc(s.label)+'</div>'
          +'</div>'
          +'<div style="padding:8px 10px;">'
            +scRow('FOB Cost', fmtCur(s.fob))
            +scRowPair('Lead Time', s.lt ? s.lt+'d' : '&#8212;', 'MOQ', s.moq ? fmtInt(s.moq) : '&#8212;')
            +scRowPair('ELC NJ', fmtCur(s.elc_nj), 'ELC LA', fmtCur(s.elc_la))
            +scRowPair('MU% NJ', fmtPct(s.mu_nj), 'MU% LA', fmtPct(s.mu_la), isBestMU, false)
            +scSection('Ordered from Supplier')
            +scRowPair('Units', s.qty_ord ? fmtInt(s.qty_ord) : '&#8212;', '% Total', s.pct_ord ? fmtPct(s.pct_ord) : '&#8212;')
          +'</div>'
          +'<div style="padding:8px 10px;border-top:1px solid #eee;">'
            // Use this Supplier checkbox
            +'<label style="display:flex;align-items:center;gap:6px;font-size:12px;font-weight:600;cursor:pointer;color:'+(ss.checked?'#1b5e20':'#3f51b5')+';margin-bottom:8px;">'
              +'<input type="checkbox" id="'+chkId+'" '+(ss.checked?'checked':'')+' '
                +'onchange="toggleSuppSelection(\''+msEsc+'\',\''+s.key+'\',this.checked)" '
                +'style="cursor:pointer;accent-color:#1b5e20;width:14px;height:14px;">'
              +'Use this Supplier'
            +'</label>'
            // Need Qty + Needed ETD inputs (shown when checked)
            +'<div id="'+inputsId+'" style="display:'+(ss.checked?'block':'none')+';margin-top:4px;">'
              +'<div style="margin-bottom:6px;">'
                +'<div style="font-size:10px;font-weight:700;color:#5c6bc0;text-transform:uppercase;letter-spacing:0.4px;margin-bottom:2px;">Need Qty</div>'
                +'<input type="number" id="'+nqId+'" value="'+(ss.needQty||0)+'" min="0" step="1" '
                  +'style="width:100%;font-size:15px;font-weight:700;color:#1a237e;border:2px solid #7986cb;border-radius:4px;padding:3px 6px;text-align:right;font-family:inherit;background:#fff;" '
                  +'oninput="updateSuppNeedQty(\''+msEsc+'\',\''+s.key+'\',this.value)">'
              +'</div>'
              +'<div>'
                +'<div style="font-size:10px;font-weight:700;color:#5c6bc0;text-transform:uppercase;letter-spacing:0.4px;margin-bottom:2px;">Needed ETD</div>'
                +'<input type="date" id="'+etdId+'" value="'+esc(defaultEtd)+'" '
                  +'style="width:100%;font-size:13px;font-weight:600;color:#e65100;border:2px solid #ffcc80;border-radius:4px;padding:3px 6px;font-family:inherit;background:#fff;" '
                  +'oninput="updateSuppETD(\''+msEsc+'\',\''+s.key+'\',this.value)">'
              +'</div>'
            +'</div>'
          +'</div>'
          +'</div>';
      });
    }
    suppCards += '</div>';

    // Save to QB button
    var saveBtnHtml = '<div style="margin-top:14px;display:flex;align-items:center;gap:12px;">'
      +'<button onclick="saveSupplierSelections(\''+esc(r.mstyle)+'\')" '
        +'style="padding:8px 20px;font-size:12px;font-weight:700;background:#1565c0;color:#fff;border:none;border-radius:4px;cursor:pointer;font-family:inherit;">&#128190; Save to QB</button>'
      +'<span id="purSaveStatus_'+_msId+'" style="font-size:11px;color:#888;"></span>'
      +'</div>';

    purRecSection = '<div class="section"><h3>&#128722; Recommended Purchase</h3>'
      +purSummary
      +suppCards
      +saveBtnHtml
      +'</div>';
  } else {
    purRecSection = '<div class="section"><h3>&#128722; Recommended Purchase</h3>'
      +'<div style="color:#1b5e20;font-style:italic;">&#10003; No new order needed - projected inventory stays above Opt OH ('+fmtInt(r.opt_oh)+' units) through the 26-week window.</div>'
      +'</div>';
  }

  // POG End Date overstock warning — fires when any customer is Active + within 6 wks of POG End
  var pogEndWarnHtml = '';
  if (r.pog_end_warns && r.pog_end_warns.length > 0) {
    var _pwItems = r.pog_end_warns.map(function(w) {
      return '<li><b>' + esc(w.customer) + '</b>: POG End ' + fmtDate(w.pog_end)
        + ', zero-out target W' + w.cutoff_wk
        + (w.exposure > 0 ? ' -- ' + fmt(w.exposure) + ' units at overstock risk' : '')
        + '</li>';
    }).join('');
    pogEndWarnHtml = '<div class="section">'
      + '<h3 style="color:#b71c1c;">&#x26A0; POG End Date -- Overstock Alert</h3>'
      + '<div style="padding:10px 12px;background:#ffebee;border:2px solid #ef9a9a;border-radius:4px;font-size:11px;color:#b71c1c;">'
      + '<div style="font-weight:700;font-size:12px;margin-bottom:6px;">One or more customers are within 6 weeks of their POG End Date. AI forecast extends past the zero-out cutoff, creating overstock risk.</div>'
      + '<ul style="margin:4px 0;padding-left:18px;">' + _pwItems + '</ul>'
      + '<div style="margin-top:8px;font-style:italic;color:#880000;">Status @ Cust is Active for all items above. If the buyer has confirmed the POG is ending, change Status @ Cust to FD (Future Delete) in the Forecast Manager -- the AI will zero the forecast automatically from the cutoff week.</div>'
      + '</div></div>';
  }

  return '<div class="dwrap"><div class="section" style="padding:10px 14px;"><div style="display:flex;gap:10px;flex-wrap:wrap;align-items:flex-start;">'+identityBox+itemDataBox+stockStatusBox+'</div></div>'+pogEndWarnHtml+'<div class="section"><h3>&#128230; Inventory Flow <span style="font-size:10px;font-weight:400;color:#888;">- hover Expected Receipts cells for PO detail / hover Prj Demand cells for customer breakdown</span></h3><div style="overflow-x:auto">'+invFlow+'</div>'+invGapBanner+kpiStrip+'</div><div class="section"><h3>&#128197; Aged Inventory</h3>'+agedInvHtml+'</div><div class="section"><h3>&#127919; Recommended Actions</h3><div class="recs-wrap">'+recs+'</div></div>'+purRecSection+'</div>';
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

// -- Supplier selection helpers (called from inline event handlers) ------------
function toggleSuppSelection(ms, key, checked) {
  if (!purchaseSelections[ms]) purchaseSelections[ms] = {};
  if (!purchaseSelections[ms][key]) purchaseSelections[ms][key] = { checked:false, needQty:0, etd:'' };
  purchaseSelections[ms][key].checked = checked;
  var _id = ms.replace(/[^a-zA-Z0-9]/g,'_');
  var inp = document.getElementById('suppInputs_'+_id+'_'+key);
  if (inp) inp.style.display = checked ? 'block' : 'none';
  var lbl = document.getElementById('suppChk_'+_id+'_'+key);
  if (lbl && lbl.parentNode) lbl.parentNode.style.color = checked ? '#1b5e20' : '#3f51b5';
  if (lbl && lbl.parentNode) lbl.parentNode.style.fontWeight = checked ? '700' : '600';
  var card = document.getElementById('suppCard_'+_id+'_'+key);
  if (card) { card.style.border = checked ? '2px solid #1b5e20' : '1px solid #ddd'; card.style.background = checked ? '#f1f8e9' : '#fff'; }
  updatePurTotalNeedQty(ms);
}
function updateSuppNeedQty(ms, key, val) {
  if (!purchaseSelections[ms]) purchaseSelections[ms] = {};
  if (!purchaseSelections[ms][key]) purchaseSelections[ms][key] = { checked:true, needQty:0, etd:'' };
  purchaseSelections[ms][key].needQty = parseInt(val) || 0;
  updatePurTotalNeedQty(ms);
}
function updateSuppETD(ms, key, val) {
  if (!purchaseSelections[ms]) purchaseSelections[ms] = {};
  if (!purchaseSelections[ms][key]) purchaseSelections[ms][key] = { checked:true, needQty:0, etd:'' };
  purchaseSelections[ms][key].etd = val;
}
function updatePurTotalNeedQty(ms) {
  var purSel = purchaseSelections[ms] || {};
  var total = 0;
  ['main','alt1','alt2','alt3'].forEach(function(k){var ss=purSel[k]||{};if(ss.checked)total+=(ss.needQty||0);});
  var el = document.getElementById('purTotalNeedQty_'+ms.replace(/[^a-zA-Z0-9]/g,'_'));
  if (el) el.textContent = total.toLocaleString();
}
function saveSupplierSelections(ms) {
  var purSel = purchaseSelections[ms];
  if (!purSel) { return; }
  var statusEl = document.getElementById('purSaveStatus_'+ms.replace(/[^a-zA-Z0-9]/g,'_'));
  if (statusEl) { statusEl.textContent = 'Saving...'; statusEl.style.color = '#888'; }
  var data = {};
  data[IF_F.Mstyle]     = { value: ms };
  data[IF_F.NeedQtyMain] = { value: (purSel.main&&purSel.main.checked) ? (purSel.main.needQty||0) : 0 };
  data[IF_F.NeedETDMain] = { value: (purSel.main&&purSel.main.checked&&purSel.main.etd) ? purSel.main.etd : null };
  data[IF_F.NeedQtyAlt1] = { value: (purSel.alt1&&purSel.alt1.checked) ? (purSel.alt1.needQty||0) : 0 };
  data[IF_F.NeedETDAlt1] = { value: (purSel.alt1&&purSel.alt1.checked&&purSel.alt1.etd) ? purSel.alt1.etd : null };
  data[IF_F.NeedQtyAlt2] = { value: (purSel.alt2&&purSel.alt2.checked) ? (purSel.alt2.needQty||0) : 0 };
  data[IF_F.NeedETDAlt2] = { value: (purSel.alt2&&purSel.alt2.checked&&purSel.alt2.etd) ? purSel.alt2.etd : null };
  data[IF_F.NeedQtyAlt3] = { value: (purSel.alt3&&purSel.alt3.checked) ? (purSel.alt3.needQty||0) : 0 };
  data[IF_F.NeedETDAlt3] = { value: (purSel.alt3&&purSel.alt3.checked&&purSel.alt3.etd) ? purSel.alt3.etd : null };
  fetch('https://api.quickbase.com/v1/records', {
    method: 'POST',
    headers: { 'QB-Realm-Hostname':QB_REALM, 'Authorization':QB_TOKEN, 'Content-Type':'application/json', 'User-Agent':'petspeople-inv-mgmt-viewer/1.0' },
    body: JSON.stringify({ to: INVF_TID, data: [data], mergeFieldId: IF_F.Mstyle, fieldsToReturn: [] })
  })
  .then(function(resp){return resp.json().then(function(j){return{ok:resp.ok,body:j};});})
  .then(function(res){
    if (statusEl) {
      statusEl.textContent = res.ok ? 'Saved to QB' : 'Save failed: '+(res.body.message||'unknown');
      statusEl.style.color = res.ok ? '#1b5e20' : '#c62828';
    }
  })
  .catch(function(e){if(statusEl){statusEl.textContent='Error: '+e.message;statusEl.style.color='#c62828';}});
}

// -- Boot ----------------------------------------------------------------------
async function boot() {
  var scr=document.getElementById('loadingScreen');
  setStep(1,'active');setBar(5);setStatus('Checking cache...');

  var cached = await loadCache();
  if(cached){
    setBar(80);setStatus('Loading from cache (' + cached.src + ')...');
    ALL=cached.obj.data;
    var asOf=document.getElementById('dataAsOf');
    if(asOf)asOf.textContent='Data as of '+fmtTimestamp(cached.obj.ts)+' (cached)';
    setBar(90);setStatus('Building view...');
    setStep(4,'active');
    buildFilterDropdowns();buildTableHead();applyFilters();
    setBar(100);setStep(4,'done');
    await new Promise(function(r){setTimeout(r,350);});
    if(scr){scr.classList.add('hidden');setTimeout(function(){scr.style.display='none';},500);}
    // Fire phase 2 (detail data) in background -- non-blocking
    _detailPromise = attachDetailData(ALL).catch(function(e) {
      console.warn('[InvMgmt] detail data load failed (non-fatal):', e);
      _detailPromise = null;
    }).then(function() {
      _detailPromise = null;
      // Re-render any currently open detail panel
      var open = document.querySelector('.detail-pane[style*="table-row"]');
      if (open) { open.dataset.loaded = ''; open.style.display = 'none'; }
    });
    return;
  }

  try {
    var records=await loadData();
    ALL=records;
    setStep(4,'active');setBar(85);setStatus('Building view...');
    await new Promise(function(r){setTimeout(r,50);});
    buildFilterDropdowns();buildTableHead();applyFilters();
    // Defer cache write until after first render so JSON.stringify doesn't block the UI
    setTimeout(function(){saveCache(records).catch(function(){});},200);
    var ts=Date.now();
    var asOf=document.getElementById('dataAsOf');
    if(asOf)asOf.textContent='Data as of '+fmtTimestamp(ts);
    setBar(100);setStep(4,'done');setStatus('Ready!');
    await new Promise(function(r){setTimeout(r,350);});
    if(scr){scr.classList.add('hidden');setTimeout(function(){scr.style.display='none';},500);}
    // Fire phase 2 (detail data) in background -- non-blocking
    _detailPromise = attachDetailData(ALL).catch(function(e) {
      console.warn('[InvMgmt] detail data load failed (non-fatal):', e);
      _detailPromise = null;
    }).then(function() {
      _detailPromise = null;
      // Re-render any currently open detail panel
      var open = document.querySelector('.detail-pane[style*="table-row"]');
      if (open) { open.dataset.loaded = ''; open.style.display = 'none'; }
    });
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
  await clearCache();
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
    document.getElementById('hideInactive').checked=true;
    showNeedPurchase=false;
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

  boot().then(function() {
    // If the page was opened with ?mstyle=XX (e.g. from the "View in Inventory Manager"
    // link in the Forecast Manager), pre-populate the search box and filter to that mstyle.
    var ms = new URLSearchParams(window.location.search).get('mstyle');
    if (ms) {
      var si = document.getElementById('searchInput');
      if (si) { si.value = ms; applyFilters(); }
    }
  });
});
