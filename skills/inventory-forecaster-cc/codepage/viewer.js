// ----------------------------------------------------------------------------
// QB CODEPAGE: viewer.js
// Pets+People  -  Inventory Forecaster Validation Viewer (codepage edition)
//
// Loaded by viewer.html via <script src="/db/bpd24h9wy?a=dbpage&pageID=56"></script>.
// All QB I/O happens here  -  viewer.html is pure layout.
//
// Functional parity with the local Python viewer (scripts/viewer.py):
// same filters, same table columns, same expand-row detail panel, same
// Use AI / Use Sugg / Flag / Comment buttons.  No deviations from layout.
//
// Filter dropdowns (severity / volume / priority / pattern / brand / inv mgr /
// customer) all auto-populate from actual values in the QB record set  - 
// nothing hardcoded that can drift out of sync with what the forecaster
// emits.
// ----------------------------------------------------------------------------

const CFG = window.QB_CONFIG;
const QB_API = `https://api.quickbase.com/v1`;

// -- Auth: QB temporary tokens (no embedded user token) ----------------------
//
// QB issues short-lived (5-minute) tokens scoped to one dbid via
//   GET /v1/auth/temporary/{dbid}    (cookies must accompany the request)
// Because this page is served from pim.quickbase.com the visitor already
// has a QB session cookie; `credentials: 'include'` forwards it.  The
// returned token is then used as `Authorization: QB-TEMP-TOKEN <value>`
// for all subsequent calls.
//
// Tokens are cached per-dbid and refreshed every ~4 minutes (or on 401).
// Each user acts with their own QB permissions  -  no service-account secret
// lives in the page, so the QB "Insecure code" warning goes away.
const _tokenCache = new Map();          // dbid > { token, expiresAt }
const TOKEN_TTL_MS = 4 * 60 * 1000;     // refresh 1 min before QB's 5-min expiry

async function _fetchTempToken(dbid) {
  const res = await fetch(`${QB_API}/auth/temporary/${dbid}`, {
    method:      'GET',
    credentials: 'include',             // sends the user's QB session cookie
    headers: {
      'QB-Realm-Hostname': CFG.REALM,
    },
  });
  if (!res.ok) {
    throw new Error(`Could not get temp token for ${dbid} (HTTP ${res.status}). `
                  + `Are you signed in to ${CFG.REALM}?  Body: ${await res.text()}`);
  }
  const data = await res.json();
  return data.temporaryAuthorization;
}

async function getTempToken(dbid) {
  const cached = _tokenCache.get(dbid);
  if (cached && cached.expiresAt > Date.now()) return cached.token;
  try {
    const token = await _fetchTempToken(dbid);
    _tokenCache.set(dbid, { token, expiresAt: Date.now() + TOKEN_TTL_MS });
    return token;
  } catch (e) {
    // If token fetch fails for a secondary table (e.g. comments),
    // fall back to the already-working Projections token.  The subsequent
    // query may still 401 but that error is caught gracefully by callers.
    const proj = _tokenCache.get(CFG.PROJECTIONS_TID);
    if (proj && proj.expiresAt > Date.now() && dbid !== CFG.PROJECTIONS_TID) {
      console.warn(`[Auth] token for ${dbid} unavailable  -  falling back to Projections token`);
      return proj.token;
    }
    throw e;
  }
}

async function _hdrs(dbid) {
  return {
    'QB-Realm-Hostname': CFG.REALM,
    'Authorization':     `QB-TEMP-TOKEN ${await getTempToken(dbid)}`,
    'Content-Type':      'application/json',
  };
}

// -- Current User Identity ---------------------------------------------------
// Decoded once during bootstrap by reading the QB temp token JWT payload.
// QB signs temp tokens as JWTs; we only read the (unsigned) payload to extract
// the visitor's display name and email — no signature verification needed.
//
// The name is matched case-insensitively against inv_manager values in the
// loaded records to determine whether the visitor is a planner (owns records)
// or a director/VP/other (no records assigned to them).
//   Planner → _USER_IS_PLANNER = true; server-side QB fetch pre-filters to their records
//   Director/VP → see all records by default

function _decodeJwt(token) {
  try {
    const payload = token.split('.')[1];
    if (!payload) return {};
    const b64    = payload.replace(/-/g, '+').replace(/_/g, '/');
    const padded = b64 + '='.repeat((4 - b64.length % 4) % 4);
    return JSON.parse(atob(padded));
  } catch (_) { return {}; }
}

async function fetchCurrentUser() {
  try {
    const token = await getTempToken(CFG.PROJECTIONS_TID);
    const p     = _decodeJwt(token);
    const id    = (p.sub || '').trim();
    // Try JWT payload fields first (QB sometimes includes these)
    let name  = (p.name || p.fullName || p.display_name || '').trim();
    let email = (p.email || '').trim();

    // Primary: QB legacy API_GetUserInfo — works in codepage context via session
    // cookie; returns the logged-in user's info directly with no record ownership
    // requirement.  We extract the QB user ID from the <user id="..."> attribute
    // (correct format for EX filters) and prefer <name> over <screenName> since
    // screenName often holds the login handle rather than the display name.
    let qbUserId = id;  // may be overwritten with the correct QB ID below
    if (!name) {
      try {
        const uiResp = await Promise.race([
          fetch(`https://${CFG.REALM}/db/main?a=API_GetUserInfo`, { credentials: 'include' }),
          new Promise((_, rej) => setTimeout(() => rej(new Error('timeout')), 5000)),
        ]);
        const uiXml  = await uiResp.text();
        const _uid   = uiXml.match(/<user[^>]+id="([^"]+)"/);
        const _fn    = uiXml.match(/<firstName>(.*?)<\/firstName>/);
        const _ln    = uiXml.match(/<lastName>(.*?)<\/lastName>/);
        const _em    = uiXml.match(/<email>(.*?)<\/email>/);
        if (_uid && _uid[1].trim()) qbUserId = _uid[1].trim();
        const _fullName = [(_fn && _fn[1].trim()) || '', (_ln && _ln[1].trim()) || ''].filter(Boolean).join(' ');
        if (_fullName) name = _fullName;
        if (_em && _em[1].trim()) email = email || _em[1].trim();
      } catch (_) { /* non-fatal */ }
    }

    // Secondary: scan tables for a record owned by this user (Record Owner = FID 4).
    // Use the QB user ID obtained from API_GetUserInfo (correct EX filter format).
    // Runs when name is absent OR looks like a login handle (no space = not "First Last").
    if ((!name || !name.includes(' ')) && qbUserId) {
      for (const _tid of [CFG.PROJECTIONS_TID, CFG.COMMENTS_TID, 'bv2jirwts']) {
        if (name && name.includes(' ')) break;
        try {
          const r = await qb('/records/query', {
            from:    _tid,
            select:  [4],
            where:   `{4.EX.'${qbUserId}'}`,
            options: { top: 1 },
          });
          const row   = (r.data || [])[0];
          const owner = row && row[4] && row[4].value;
          if (owner) {
            const _n = (owner.name && owner.name !== 'Unknown' ? owner.name : '') || owner.userName || '';
            if (_n.trim()) { name = _n.trim(); email = email || (owner.email || '').trim(); }
          }
        } catch (_) { /* non-fatal */ }
      }
    }

    CURRENT_USER = {
      name:  name || (email ? email.split('@')[0].replace(/[._]/g, ' ') : ''),
      email: email,
      id:    id,
    };
    console.info(`[Auth] Current user: "${CURRENT_USER.name}" (${CURRENT_USER.email || id || '?'})`);
  } catch (e) {
    console.warn('[Auth] Could not decode current user from JWT:', e.message);
  } finally {
    // Always unblock addComment() regardless of success or failure above.
    if (_userReadyResolve) _userReadyResolve();
  }
}

// Extract the dbid an API call targets so we can fetch the right temp token.
//   - body.from  > records/query, records DELETE
//   - body.to    > records POST (insert/upsert)
//   - ?tableId=... > /fields, /tables, etc.
function _dbidFor(path, body) {
  if (body && typeof body === 'object') {
    if (body.from) return body.from;
    if (body.to)   return body.to;
  }
  const m = path.match(/[?&]tableId=([^&]+)/);
  if (m) return m[1];
  return null;
}

async function _qbFetch(path, body, method) {
  const dbid = _dbidFor(path, body);
  if (!dbid) {
    throw new Error(`qb: cannot determine dbid for ${method||'POST'} ${path}  -  `
                  + `no from/to in body and no tableId in querystring`);
  }
  const send = async () => fetch(QB_API + path, {
    method:  method || 'POST',
    headers: await _hdrs(dbid),
    body:    body ? JSON.stringify(body) : undefined,
  });
  let res = await send();
  if (res.status === 401) {            // expired token > drop cache and retry once
    _tokenCache.delete(dbid);
    res = await send();
  }
  if (!res.ok) {
    throw new Error(`QB ${method||'POST'} ${path} > HTTP ${res.status}: ${await res.text()}`);
  }
  const json = await res.json();
  // -- Silent-rejection guard for /records POST -------------------------------
  // QB's bulk records API returns HTTP 200 even when individual rows are
  // rejected (required-field violations, permission issues, validation errors).
  // The actual outcome lives in metadata.createdRecordIds/updatedRecordIds and
  // metadata.lineErrors. Without this check, callers see "success" while
  // nothing was actually written. Throw loudly so the catch block can show
  // a red error toast instead of a green checkmark.
  if (path === '/records' && (method || 'POST') === 'POST' && json && json.metadata) {
    const meta = json.metadata;
    const created   = (meta.createdRecordIds   || []).length;
    const updated   = (meta.updatedRecordIds   || []).length;
    const unchanged = (meta.unchangedRecordIds || []).length;
    const lineErrs  = meta.lineErrors || {};
    const hasErrs   = lineErrs && Object.keys(lineErrs).length > 0;
    if (hasErrs || (created + updated + unchanged === 0)) {
      const errStr = hasErrs ? JSON.stringify(lineErrs) : 'no records created/updated';
      console.error('QB /records silent-rejection:', json);
      throw new Error(`QB rejected the row: ${errStr.slice(0, 500)}`);
    }
  }
  return json;
}

async function qb(path, body, method) { return _qbFetch(path, body, method || 'POST'); }
async function qbGet(path)             { return _qbFetch(path, null, 'GET'); }

// -- Boot overlay helpers ----------------------------------------------------
function _setBoot(msg)   { const e=document.getElementById('bootStatus'); if(e) e.textContent = msg; }
function _setDetail(msg) { const e=document.getElementById('bootDetail'); if(e) e.textContent = msg; }
function _hideBoot()     { const e=document.getElementById('bootOverlay'); if(e) e.style.display = 'none'; }
function _setFreshness(field, ts) {
  const el = document.getElementById(field);
  if (el) el.textContent = new Date(ts).toLocaleString('en-US', { month:'short', day:'numeric', hour:'numeric', minute:'2-digit' });
  const bar = document.getElementById('data-freshness');
  if (bar) bar.style.display = '';
}

// -- Director / VP bypass ---------------------------------------------------
// Users in this set always receive the full dataset regardless of whether
// they have records assigned to them as inv_manager.  Without this, anyone
// who directly manages a brand AND holds a director/VP role would be
// mis-classified as a planner and only see their own brands.
const DIRECTOR_EMAILS = new Set([
  's.shweky@petspeople.com',    // Steven Shweky
  'm.scott@petspeople.com',     // Mikey Scott - Director of Inventory Management
  'nancyl@fetch4pets.com',      // Nancy Lee - VP Supply Chain
]);
function _isDirector() {
  const em = (CURRENT_USER.email || '').toLowerCase().trim();
  return em && DIRECTOR_EMAILS.has(em);
}

// -- State ------------------------------------------------------------------
let ALL_RECORDS      = [];
let FILTERED_RECORDS = [];
let CURRENT_USER     = { name: '', email: '', id: '' };   // resolved in bootstrap via JWT
let _USER_IS_PLANNER = false;   // true when CURRENT_USER.name matches an inv_manager value
// Promise that resolves once fetchCurrentUser() finishes (success or failure).
// addComment() awaits this so the author is always written on the first try,
// even if the user submits a comment before the bootstrap identity call returns.
let _userReadyResolve = null;
const _USER_READY = new Promise(res => { _userReadyResolve = res; });
let MAN_PRJ_FIDS     = [];   // 26 fids for date-stamped manual prj cols (rolling weekly)
let MAN_PRJ_LABELS   = [];
// -- Unsaved MAN projection edits -----------------------------------------
// Keyed by `${recordKey}|${weekIdx}` > { key, weekIdx, oldVal, newVal }.
// Populated by onManEdit(), drained by saveAllManEdits(), cleared by
// discardAllManEdits(). Survives detail-pane collapse/re-expand because the
// renderer reads from this map when (re)building the projection row.
const DIRTY_EDITS = new Map();
let ORD_HIST_FIDS    = [];   // 26 fids for Ord LW + Ord LW-1..Ord LW-25 (oldest>newest)
let SHP_HIST_FIDS    = [];   // 26 fids for Shp LW + Shp LW-1..Shp LW-25 (oldest>newest)
// Last-Year actuals: weeks 27..52 ago, aligned to W1..W26 (oldest>newest).
// LY_ORD_HIST_FIDS[0] = Ord LW-51 (52 weeks ago = LY-W1)
// LY_ORD_HIST_FIDS[25] = Ord LW-26 (27 weeks ago = LY-W26)
let LY_ORD_HIST_FIDS = [];
let LY_SHP_HIST_FIDS = [];
let W1_DATE          = null;

let currentPage = 0;
const PAGE_SIZE = 100;
// Comments and Flag now both live in QB:
//   - Flag    > CFG.FID.FLAGGED on Projections (toggled from the detail panel)
//   - Comment > INSERT into Projection Comments (CFG.COMMENTS_TID)
// Nothing about either control is stored in localStorage anymore.

// -- Discover the rolling weekly field IDs at startup -----------------------
//
// Three sets of weekly fields rotate or are stable but not contiguous:
//   - Manual projection cols   >  labels "MM DD W1" through "MM DD W26"
//   - Order history (L26W)     >  labels "Ord LW", "Ord LW-1", ..., "Ord LW-25"
//   - Shipment history (L26W)  >  labels "Shp LW", "Shp LW-1", ..., "Shp LW-25"
//
// We discover them in one /v1/fields call so we don't have to hardcode a
// fid mapping that goes stale every Monday morning.
async function discoverWeeklyFids() {
  // Same-tab refresh: skip the /fields call if session cache is warm.
  // The cached FIDs are valid until Sunday when columns rotate; on the first
  // refresh after a Sunday rotation the labels won't match and QB will return
  // wrong values, so we expire the FID cache at midnight on Sundays.
  if (!_prjCacheBypassed()) {
    const cached = _loadFidCache();
    if (cached) {
      MAN_PRJ_FIDS     = cached.manFids;
      MAN_PRJ_LABELS   = cached.manLabels;
      ORD_HIST_FIDS    = cached.ordFids;
      SHP_HIST_FIDS    = cached.shpFids;
      LY_ORD_HIST_FIDS = cached.lyOrdFids;
      LY_SHP_HIST_FIDS = cached.lyShpFids;
      W1_DATE          = new Date(cached.w1Date);
      console.info('[Fids] loaded from session cache');
      return;
    }
  }
  const fields = await qbGet(`/fields?tableId=${CFG.PROJECTIONS_TID}`);
  const manRe  = /^(\d{2})\s(\d{2})\sW(\d{1,2})$/;
  const histRe = /^(Ord|Shp)\s+LW(?:-(\d{1,2}))?$/;
  const man = [];
  const ord = new Map(); // offset -> fid (0 = Ord LW, 1 = Ord LW-1, ...)
  const shp = new Map();
  // Stable named fids (KEY, CUST, INV_MGR_NAME, etc.) come straight from
  // CFG.FID  -  we trust the configured IDs rather than looking them up by
  // label, because labels can change in QB.  The only fields we discover
  // dynamically are the rolling weekly columns (manual prj + Ord LW + Shp LW)
  // since their labels rotate every Sunday.
  for (const f of fields) {
    const lbl = (f.label || '').trim();
    let m;
    if ((m = lbl.match(manRe))) {
      const wk = parseInt(m[3], 10);
      if (wk >= 1 && wk <= 26) man.push({ wk, fid: f.id, label: lbl, mm: m[1], dd: m[2] });
    } else if ((m = lbl.match(histRe))) {
      const off = m[2] ? parseInt(m[2], 10) : 0;
      // Capture L26W (offsets 0..25) AND LY-26W (offsets 26..51)  -  the LY
      // range powers the Ordered LY / Shipped LY rows in the detail pane.
      if (off >= 0 && off <= 51) {
        if (m[1] === 'Ord') ord.set(off, f.id);
        else                shp.set(off, f.id);
      }
    }
  }
  if (man.length !== 26) {
    throw new Error(`Expected 26 manual prj fields (MM DD W1..W26), found ${man.length}.`);
  }
  man.sort((a, b) => a.wk - b.wk);
  MAN_PRJ_FIDS   = man.map(x => x.fid);
  MAN_PRJ_LABELS = man.map(x => x.label);
  // Build oldest>newest history arrays (offset 25 = Ord LW-25 = oldest, 0 = Ord LW = newest)
  ORD_HIST_FIDS = [];
  SHP_HIST_FIDS = [];
  for (let off = 25; off >= 0; off--) {
    if (ord.has(off)) ORD_HIST_FIDS.push(ord.get(off));
    if (shp.has(off)) SHP_HIST_FIDS.push(shp.get(off));
  }
  // LY arrays  -  offsets 51..26 (oldest LY week first, newest LY week last).
  // LY_ORD_HIST_FIDS[0] = Ord LW-51 (=LY-W1, 52 weeks ago)
  // LY_ORD_HIST_FIDS[25] = Ord LW-26 (=LY-W26, 27 weeks ago)
  // These align positionally with W1..W26 of the forecast (W1 of forecast
  // is the calendar week 1 year after LY-W1).
  LY_ORD_HIST_FIDS = [];
  LY_SHP_HIST_FIDS = [];
  for (let off = 51; off >= 26; off--) {
    if (ord.has(off)) LY_ORD_HIST_FIDS.push(ord.get(off));
    if (shp.has(off)) LY_SHP_HIST_FIDS.push(shp.get(off));
  }
  // W1 date for the projection grid header
  const yr = new Date().getFullYear();
  W1_DATE = new Date(`${yr}-${man[0].mm}-${man[0].dd}T00:00:00`);
  // Persist to session cache for subsequent same-tab refreshes.
  _saveFidCache({
    manFids: MAN_PRJ_FIDS, manLabels: MAN_PRJ_LABELS,
    ordFids: ORD_HIST_FIDS, shpFids: SHP_HIST_FIDS,
    lyOrdFids: LY_ORD_HIST_FIDS, lyShpFids: LY_SHP_HIST_FIDS,
    w1Date: W1_DATE.toISOString(),
  });
}

// Discover the FID for Open_Supplier_POs on the Inventory Flow table.
// Runs once at startup; result stored in INV_FLOW_SUPP_PO_FID.
async function discoverInvFlowTextFids() {
  if (INV_FLOW_SUPP_PO_FID && INV_FLOW_ATS_NOW_FID) return;
  try {
    const fields = await qbGet(`/fields?tableId=${CFG.INV_FLOW_TID}`);
    for (const f of fields) {
      // Normalize: lowercase, collapse spaces/underscores/+ into single underscore,
      // strip leading/trailing underscores so "ATS OH+OO" → "ats_oh_oo"
      const lbl = (f.label || '').trim().toLowerCase()
                    .replace(/[\s_+]+/g, '_').replace(/^_|_$/g, '');
      if      (lbl === 'open_supplier_pos')  INV_FLOW_SUPP_PO_FID   = f.id;
      else if (lbl === 'ats_now')            INV_FLOW_ATS_NOW_FID   = f.id;
      else if (lbl === 'ats_oh')             INV_FLOW_ATS_OH_FID    = f.id;
      else if (lbl === 'ats_oh_oo')          INV_FLOW_ATS_OO_FID    = f.id;
      else if (lbl === 'ats_wos_oh')         INV_FLOW_ATS_OH_WOS_FID = f.id;
      else if (lbl === 'ats_wos_oh_oo')      INV_FLOW_ATS_OO_WOS_FID  = f.id;
      else if (lbl === '1st_shpd_date')      INV_FLOW_FIRST_SHPD_FID  = f.id;
    }
    console.info('[InvFlow] text/ATS FIDs discovered:',
      { supp_pos: INV_FLOW_SUPP_PO_FID, ats_now: INV_FLOW_ATS_NOW_FID,
        ats_oh: INV_FLOW_ATS_OH_FID, ats_oo: INV_FLOW_ATS_OO_FID,
        ats_oh_wos: INV_FLOW_ATS_OH_WOS_FID, ats_oo_wos: INV_FLOW_ATS_OO_WOS_FID,
        first_shpd: INV_FLOW_FIRST_SHPD_FID });
  } catch (e) {
    console.warn('[InvFlow] discoverInvFlowTextFids failed:', e.message || e);
  }
}

// -- Discover Order History FIDs for Qty Cxld row ---------------------------
// Called lazily the first time a detail panel opens. One /fields call; cached
// in module-level vars so subsequent panels don't re-fetch.
async function discoverOrdHistFids() {
  if (ORD_HIST_QTY_CXLD_FID) return;
  const fids = CFG.ORDER_HIST_FID || {};
  ORD_HIST_ACCT_MSTYLE_FID = fids.ACCT_MSTYLE || null;
  ORD_HIST_CANCEL_DATE_FID = fids.CANCEL_DATE  || null;
  ORD_HIST_QTY_CXLD_FID   = fids.QTY_CXLD     || null;
  ORD_HIST_EXCEP_APPR_FID  = fids.EXCEP_APPR   || null;
  ORD_HIST_EXCEP_NOTES_FID = fids.EXCEP_NOTES  || null;
  ORD_HIST_QTY_OPEN_FID    = fids.QTY_OPEN     || null;
  ORD_HIST_CUST_NAME_FID   = fids.CUST_NAME    || null;
  console.info('[OrdHist] FIDs from CFG:', {
    acct_mstyle: ORD_HIST_ACCT_MSTYLE_FID, cancel_date: ORD_HIST_CANCEL_DATE_FID,
    qty_cxld: ORD_HIST_QTY_CXLD_FID, excep: ORD_HIST_EXCEP_APPR_FID,
    notes: ORD_HIST_EXCEP_NOTES_FID, qty_open: ORD_HIST_QTY_OPEN_FID,
    cust_name: ORD_HIST_CUST_NAME_FID });
}

// -- Build the QB query select list for one row -----------------------------
function buildSelectFids() {
  const F = CFG.FID;
  const sel = [
    F.KEY, F.MSTYLE, F.CUST_SKU, F.CUST, F.DESCRIPTION, F.STATUS_CUST, F.ITEM_STATUS,
    F.INV_MGR_USER, F.INV_MGR_NAME, F.BRAND_NAME, F.MASTER_PACK,
    F.AI_ALERT, F.AI_ANALYSIS, F.ORD_WK_L26W, F.ORD_WK_L13,
    F.LAST_COMMENT, F.LAST_COMMENT_DATE, F.FLAGGED, F.PLANNER_REPLY_PENDING,
    ...(F.MANAGER_REPLY_PENDING ? [F.MANAGER_REPLY_PENDING] : []),  // optional — set FID in CFG
    // POG / ISO context (added 2026-05-10)
    F.STORE_COUNT, F.EST_ISO_QTY,
    ...(F.EST_ISO_INPUT ? [F.EST_ISO_INPUT] : []),
    ...(F.INIT_UPSPW    ? [F.INIT_UPSPW]    : []),
    F.POG_LAUNCH, F.POG_END, F.ISO_SHIP_DATE, F.NEXT_RCPT_DT,
    ...(F.SEASON ? [F.SEASON] : []),
    ...(F.AUTO_PROJECT           ? [F.AUTO_PROJECT]           : []),
    ...(F.SWITCHOVER_ACTIVE      ? [F.SWITCHOVER_ACTIVE]      : []),
    ...(F.SWITCHOVER_TO_MSTYLE   ? [F.SWITCHOVER_TO_MSTYLE]   : []),
    ...(F.SWITCHOVER_DATE        ? [F.SWITCHOVER_DATE]        : []),
  ];
  CFG.AI_PRJ_FIDS.forEach(fid => sel.push(fid));
  CFG.SUG_FIDS.forEach(fid => sel.push(fid));
  CFG.OPN_FIDS.forEach(fid => sel.push(fid));
  MAN_PRJ_FIDS.forEach(fid => sel.push(fid));
  ORD_HIST_FIDS.forEach(fid => sel.push(fid));
  SHP_HIST_FIDS.forEach(fid => sel.push(fid));
  LY_ORD_HIST_FIDS.forEach(fid => sel.push(fid));
  LY_SHP_HIST_FIDS.forEach(fid => sel.push(fid));
  // DI Ord History — sparse text field, small enough to include in the initial pull
  if (F.DI_ORD_HIST) sel.push(F.DI_ORD_HIST);
  return sel;
}

// -- Pull active projections from QB ----------------------------------------
// mgrName (optional): when provided, wraps the status filter with an additional
// inv_manager equality check so only one planner's records come back.
// QB supports parentheses in WHERE: ({A}OR{B})AND{C}
// Directors/VPs pass no mgrName and receive the full dataset.
async function fetchAllRecords(mgrName) {
  const sel = buildSelectFids();
  // Match the forecaster's SQL filter (Status_Cust LIKE 'A%' OR LIKE 'FD%')  -
  // includes both Active and Future-Delete items so the codepage record count
  // lines up with forecast_results.json + the local viewer.  FD items get the
  // F52 wind-down treatment, so planners need to see them here too.
  const _baseWhere = `{${CFG.FID.STATUS_CUST}.SW.'A'}OR{${CFG.FID.STATUS_CUST}.SW.'FD'}`;
  const where = mgrName
    ? `(${_baseWhere})AND{${CFG.FID.INV_MGR_NAME}.EX.'${mgrName.replace(/'/g, "\\'")}'}`
    : _baseWhere;
  const TOP = 1000;
  let skip = 0;
  const all = [];
  while (true) {
    _setDetail(`Fetched ${all.length.toLocaleString()} records...`);
    const resp = await qb('/records/query', {
      from: CFG.PROJECTIONS_TID,
      select: sel,
      where: where,
      options: { top: TOP, skip: skip },
    });
    const rows = resp.data || [];
    if (!rows.length) break;
    all.push(...rows);
    if (rows.length < TOP) break;
    skip += TOP;
  }
  return all;
}

// -- Inventory Flow cache (localStorage, 6h TTL) ----------------------------
// Each browser caches the per-mstyle inv-flow map locally so we don't bulk-
// re-query QB on every codepage open.  ~80 users x every page load was
// hammering /records/query; with this cache each user pays one cold pull
// per 6-hour window instead.  Quota: ~600 KB for ~1,500 mstyles, well
// under the ~5 MB localStorage budget per origin.
//
// Cache version is in the key.  Bump it (_v1 -> _v2) on any schema change
// to force-invalidate all clients on next page load.
// v2 = added Opt WOS / Opt WOS Final / Next Avl Rcpt Dt scalars per mstyle
const INV_FLOW_CACHE_KEY    = 'pp_invflow_v8';  // bumped: IDB-primary cache, 24h TTL
const INV_FLOW_CACHE_TTL_MS = 24 * 60 * 60 * 1000;  // 24 hours (InvFlow updates once/day)
const ATS_HIST_CACHE_KEY    = 'pp_ats_v1';
const ATS_HIST_CACHE_TTL_MS = 6 * 60 * 60 * 1000;  // 6 hours

// Background load promise  -  resolves when inv flow is attached to ALL_RECORDS.
// Boot fires this without awaiting so the table renders immediately.
let _invFlowPromise  = null;
let _openDetailKey   = null;  // key of whichever detail panel is currently expanded
let INV_FLOW_SUPP_PO_FID  = null;   // discovered at startup from /fields on INV_FLOW_TID
let INV_FLOW_ATS_NOW_FID  = null;   // ATS_Now
let INV_FLOW_ATS_OH_FID   = null;   // ATS_OH_
let INV_FLOW_ATS_OO_FID   = null;   // ATS_OH_OO_
let INV_FLOW_ATS_OH_WOS_FID  = null; // ATS_WOS_OH_
let INV_FLOW_ATS_OO_WOS_FID  = null; // ATS_WOS_OH_OO_
let INV_FLOW_FIRST_SHPD_FID  = null; // 1st Shpd Date  (first warehouse shipment date)

// Order History (bpe4maa4c) FIDs — discovered lazily on first detail open
let ORD_HIST_ACCT_MSTYLE_FID = null;
let ORD_HIST_CANCEL_DATE_FID = null;
let ORD_HIST_QTY_CXLD_FID    = null;
let ORD_HIST_EXCEP_APPR_FID  = null;
let ORD_HIST_EXCEP_NOTES_FID = null;
let ORD_HIST_QTY_OPEN_FID    = null;  // Qty_Open  - for open order hover
let ORD_HIST_CUST_NAME_FID   = null;  // Customer name - for open order hover

// Escape hatch: ?nocache=1 in the URL bypasses cache for a single load
// (useful when planners suspect stale data).
function _invFlowCacheBypassed() {
  try { return new URLSearchParams(location.search).get('nocache') === '1'; }
  catch (e) { return false; }
}

async function _loadInvFlowCache() {
  // 1. sessionStorage (instant same-tab F5, no async needed)
  try {
    const raw = sessionStorage.getItem(INV_FLOW_CACHE_KEY);
    if (raw) {
      const obj = JSON.parse(raw);
      if (obj && typeof obj.ts === 'number' && obj.map)
        return { map: obj.map, ageMs: Date.now() - obj.ts, source: 'session' };
    }
  } catch (e) { /* ignore */ }
  // 2. IndexedDB (cross-tab, cross-session — no quota competition with other QB apps)
  try {
    const obj = await _idb.get(INV_FLOW_CACHE_KEY);
    if (obj && typeof obj.ts === 'number' && obj.map) {
      const ageMs = Date.now() - obj.ts;
      if (ageMs <= INV_FLOW_CACHE_TTL_MS) {
        // Warm sessionStorage so same-tab refreshes skip IDB next time
        try { sessionStorage.setItem(INV_FLOW_CACHE_KEY, JSON.stringify(obj)); } catch (_) {}
        return { map: obj.map, ageMs, source: 'idb' };
      }
    }
  } catch (e) { /* ignore */ }
  return null;
}

async function _saveInvFlowCache(map) {
  const obj = { ts: Date.now(), map };
  // sessionStorage: fast same-tab F5 path
  try { sessionStorage.setItem(INV_FLOW_CACHE_KEY, JSON.stringify(obj)); } catch (_) {}
  // IndexedDB: primary persistent store — no quota competition, survives browser restart
  try { await _idb.set(INV_FLOW_CACHE_KEY, obj); } catch (e) {
    console.warn('[InvFlow] IDB save failed:', e && e.message);
  }
}

function _loadAtsHistCache() {
  try {
    const raw = sessionStorage.getItem(ATS_HIST_CACHE_KEY);
    if (raw) { const obj = JSON.parse(raw); if (obj && typeof obj.ts === 'number' && obj.map) return { map: obj.map, ageMs: Date.now() - obj.ts }; }
  } catch (e) { /* ignore */ }
  try {
    const raw = localStorage.getItem(ATS_HIST_CACHE_KEY);
    if (!raw) return null;
    const obj = JSON.parse(raw);
    if (!obj || typeof obj.ts !== 'number' || !obj.map) return null;
    if (Date.now() - obj.ts > ATS_HIST_CACHE_TTL_MS) return null;
    return { map: obj.map, ageMs: Date.now() - obj.ts };
  } catch (e) { return null; }
}
function _saveAtsHistCache(map) {
  const payload = JSON.stringify({ ts: Date.now(), map });
  try { sessionStorage.setItem(ATS_HIST_CACHE_KEY, payload); } catch (e) { /* ignore */ }
  try {
    localStorage.setItem(ATS_HIST_CACHE_KEY, payload);
  } catch (e) {
    try { localStorage.removeItem(ATS_HIST_CACHE_KEY); localStorage.setItem(ATS_HIST_CACHE_KEY, payload); } catch (e2) { /* ignore */ }
  }
}

// Clear every local cache and reload fresh from QB
function clearAllCaches() {
  const _ckAll = [INV_FLOW_CACHE_KEY, ATS_HIST_CACHE_KEY, FID_SESS_KEY,
                  _prjCacheKey(), PRJ_CACHE_KEY_ALL];
  _ckAll.forEach(k => {
    try { localStorage.removeItem(k); }   catch (e) { /* ignore */ }
    try { sessionStorage.removeItem(k); } catch (e) { /* ignore */ }
  });
  // Clear IndexedDB caches — projections + inv flow
  _idb.del(_prjCacheKey()).catch(() => {});
  _idb.del(PRJ_CACHE_KEY_ALL).catch(() => {});
  _idb.del(INV_FLOW_CACHE_KEY).catch(() => {});
}
function forceRefresh() { clearAllCaches(); location.reload(); }

function _fmtCacheAge(ms) {
  const m = Math.floor(ms / 60000);
  if (m < 60)  return `${m}m`;
  const h = Math.floor(m / 60);
  return `${h}h ${m % 60}m`;
}

// -- Projections cache --------------------------------------------------------
// Two-tier: sessionStorage (primary, tab-scoped, instant F5 refresh) +
// IndexedDB (secondary, cross-tab / cross-session, 6h TTL, ~50MB quota).
//
// Why not localStorage?  pim.quickbase.com's localStorage is shared by every
// QB app in the realm.  With 80 users and multiple apps the quota fills and
// _savePrjCache silently failed, causing a cold 5,000-record fetch every
// session.  IndexedDB is a named per-app database — no quota competition.
// Arrays are still stripped (_PRJ_CACHE_STRIP) to keep the payload small.
//
// Behaviour per scenario:
//   F5 same tab            -> sessionStorage hit  -> instant (0 QB calls)
//   New tab, <6h old data  -> IndexedDB hit        -> fast   (0 QB calls)
//   New tab, >6h / no data -> both miss            -> full fetch + save both
//   ?nocache=1 in URL      -> bypassed             -> always fresh pull
//
// Cache key: user-specific when identity is known so each user's browser only
// caches what they actually loaded (planners get ~400–500 records; directors
// get the full 5 k set).  This prevents cross-contamination on shared machines
// and avoids a director overwriting a planner's small cache (or vice-versa).
// Bump the base version any time adaptRow() output shape changes.
const PRJ_CACHE_BUILD    = '%%BUILD_TS%%';        // replaced by deploy_pages.py at deploy time
const PRJ_CACHE_KEY_BASE = `pp_prj_${PRJ_CACHE_BUILD}`;
const PRJ_CACHE_KEY_ALL  = `pp_prj_${PRJ_CACHE_BUILD}_all`;  // directors / unknown users
function _prjCacheKey() {
  if (!CURRENT_USER.name) return PRJ_CACHE_KEY_ALL;
  const safe = CURRENT_USER.name.toLowerCase().replace(/[^a-z0-9]/g, '_').slice(0, 32);
  return `${PRJ_CACHE_KEY_BASE}_u_${safe}`;
}
const PRJ_CACHE_TTL_MS = 6 * 60 * 60 * 1000;  // 6 hours

// FID discovery cache key (sessionStorage only - resets each new tab is fine
// because discoverWeeklyFids makes a single lightweight /fields call).
const FID_SESS_KEY = 'pp_fids_v1';

// -- Minimal IndexedDB key-value wrapper -------------------------------------
// All errors degrade gracefully — falls back to cold fetch, never throws.
const _idb = (() => {
  let _db = null;
  const _ready = new Promise(resolve => {
    try {
      const req = indexedDB.open('pp_viewer_v1', 1);
      req.onupgradeneeded = e => {
        try { e.target.result.createObjectStore('kv'); } catch (_) {}
      };
      req.onsuccess = e => { _db = e.target.result; resolve(true); };
      req.onerror   = ()  => resolve(false);
    } catch (_) { resolve(false); }
  });
  return {
    async get(key) {
      if (!await _ready || !_db) return undefined;
      return new Promise(res => {
        try {
          const req = _db.transaction('kv','readonly').objectStore('kv').get(key);
          req.onsuccess = e => res(e.target.result);
          req.onerror   = ()  => res(undefined);
        } catch (_) { res(undefined); }
      });
    },
    async set(key, value) {
      if (!await _ready || !_db) return;
      return new Promise(res => {
        try {
          const req = _db.transaction('kv','readwrite').objectStore('kv').put(value, key);
          req.onsuccess = () => res();
          req.onerror   = () => res();
        } catch (_) { res(); }
      });
    },
    async del(key) {
      if (!await _ready || !_db) return;
      return new Promise(res => {
        try {
          const req = _db.transaction('kv','readwrite').objectStore('kv').delete(key);
          req.onsuccess = () => res();
          req.onerror   = () => res();
        } catch (_) { res(); }
      });
    },
  };
})();

function _prjCacheBypassed() {
  try { return new URLSearchParams(location.search).get('nocache') === '1'; }
  catch (e) { return false; }
}

async function _loadPrjCache() {
  const key = _prjCacheKey();
  // 1. sessionStorage (instant, same-tab F5)
  try {
    const raw = sessionStorage.getItem(key);
    if (raw) {
      const obj = JSON.parse(raw);
      if (obj && Array.isArray(obj.records))
        return { records: obj.records, ageMs: Date.now() - (obj.ts || 0), source: 'session' };
    }
  } catch (e) { /* ignore */ }
  // 2. IndexedDB (cross-tab, cross-session, 6h TTL)
  try {
    const obj = await _idb.get(key);
    if (obj && Array.isArray(obj.records)) {
      const ageMs = Date.now() - (obj.ts || 0);
      if (ageMs <= PRJ_CACHE_TTL_MS) {
        // Warm sessionStorage so same-tab refreshes are instant from here on
        try { sessionStorage.setItem(key, JSON.stringify(obj)); } catch (_) {}
        return { records: obj.records, ageMs, source: 'idb' };
      }
    }
  } catch (e) { /* ignore */ }
  return null;
}

// Fields that are ONLY used in the detail panel (row expand) — never in the
// main table.  Stripping them before caching shrinks the payload from ~15 MB
// to ~2–3 MB so it reliably fits in localStorage (quota shared with other QB
// apps on pim.quickbase.com).  They are lazy-fetched from QB the first time
// a planner expands a row (_lazyLoadDetail).
const _PRJ_CACHE_STRIP = new Set([
  'ai_fcst', 'weeks_slim', 'suggested', 'opn_w',
  'hist_ord', 'hist_shp', 'ly_ord', 'ly_shp', 'narrative',
]);

async function _savePrjCache(records) {
  // Strip heavy arrays + narrative before caching — detail-panel only fields,
  // fetched lazily from QB on first row expand (_lazyLoadDetail).
  const slim = records.map(r => {
    const out = {};
    for (const k of Object.keys(r)) {
      if (!_PRJ_CACHE_STRIP.has(k)) out[k] = r[k];
    }
    out._needs_detail = true;
    return out;
  });
  const cacheObj = { ts: Date.now(), records: slim };
  const key = _prjCacheKey();
  // sessionStorage (same-tab F5 refresh — instant)
  try { sessionStorage.setItem(key, JSON.stringify(cacheObj)); } catch (_) {}
  // IndexedDB (cross-tab / cross-session — no quota competition)
  try {
    await _idb.set(key, cacheObj);
  } catch (e) {
    console.warn('[Prj] IDB save failed:', e.message || e);
  }
}

// Lazy-fetch the stripped detail fields for one cached record.
// Called on first expand of a row served from localStorage/sessionStorage.
async function _lazyLoadDetail(r) {
  const selectFids = [
    ...CFG.AI_PRJ_FIDS,
    ...CFG.SUG_FIDS,
    ...CFG.OPN_FIDS,
    ...MAN_PRJ_FIDS,
    ...ORD_HIST_FIDS,
    ...SHP_HIST_FIDS,
    ...LY_ORD_HIST_FIDS,
    ...LY_SHP_HIST_FIDS,
    CFG.FID.AI_ANALYSIS,
    CFG.FID.AI_ALERT,
    CFG.FID.DI_ORD_HIST,
  ].filter(Boolean);
  const data = await qb('/records/query', {
    from:    CFG.PROJECTIONS_TID,
    select:  [...new Set(selectFids)],
    where:   `{${CFG.FID.KEY}.EX.'${String(r.key).replace(/'/g, "\\'")}'}`,
    options: { top: 1 },
  });
  if (!data.data || !data.data[0]) return;
  const row = data.data[0];

  const forecast = CFG.AI_PRJ_FIDS.map(fid => num(row, fid));
  const manual   = MAN_PRJ_FIDS  .map(fid => num(row, fid));
  r.ai_fcst   = forecast;
  r.suggested = CFG.SUG_FIDS.map(fid => num(row, fid));
  r.opn_w     = CFG.OPN_FIDS.map(fid => num(row, fid));
  // Recompute conflict with fresh PO + manual data (r.is_offprice preserved from adaptRow)
  const { conflicts: _lazyCfls, hasConflict: _lazyCfl } =
    _computePoPrjConflicts(r.opn_w, manual, r.is_offprice || false);
  r.po_prj_conflicts    = _lazyCfls;
  r.has_po_prj_conflict = _lazyCfl;
  r.hist_ord  = ORD_HIST_FIDS.map(fid => num(row, fid));
  r.hist_shp  = SHP_HIST_FIDS.map(fid => num(row, fid));
  r.ly_ord    = LY_ORD_HIST_FIDS.map(fid => num(row, fid));
  r.ly_shp    = LY_SHP_HIST_FIDS.map(fid => num(row, fid));
  // DI Ord History (FID 1613): comma-separated L26W DI weekly order quantities.
  // Empty string when no DI orders; parse into a 26-element numeric array.
  const _diRaw = str(row, CFG.FID.DI_ORD_HIST) || '';
  r.di_ord = _diRaw
    ? _diRaw.split(',').map(v => parseInt(v, 10) || 0)
    : [];
  r.narrative = str(row, CFG.FID.AI_ANALYSIS) || str(row, CFG.FID.AI_ALERT);
  // F37 v2 capped-weeks (2026-05-26): parse the hidden span injected by the
  // forecaster writeback step.  Format:
  //   <span class="f37-capped" data-weeks="22,25,26"
  //         data-detail='{"22":{"orig":15104,"adj":5269,"cap":5269},...}' hidden></span>
  // Used by the detail-pane renderer to paint AI Forecast cells with a red
  // background when the AI ask exceeded available inventory that week.
  r.f37_capped_weeks  = new Set();
  r.f37_capped_detail = {};
  if (r.narrative) {
    // Match the hidden span.  data-detail is wrapped in single-quotes so its
    // JSON payload (which uses double-quotes) doesn't escape.  Use a
    // backreference so the regex doesn't trip on the inner double-quotes.
    const _capMatch = r.narrative.match(/class=["']f37-capped["'][^>]*data-weeks=["']([0-9,]+)["'][^>]*data-detail=(['"])([\s\S]*?)\2/);
    if (_capMatch) {
      try {
        _capMatch[1].split(',').forEach(w => {
          const n = parseInt(w, 10);
          if (n >= 1 && n <= 26) r.f37_capped_weeks.add(n);
        });
        // data-detail uses &lt; / &amp; HTML-escapes; unescape before JSON.parse
        const _detailRaw = _capMatch[3]
          .replace(/&lt;/g, '<')
          .replace(/&amp;/g, '&');
        r.f37_capped_detail = JSON.parse(_detailRaw) || {};
      } catch (e) { /* malformed; ignore */ }
    }
  }
  // Re-compute weeks_slim (per-week AI vs manual severity).
  // Seasonal customers: 0-weeks between orders are normal — suppress those alerts.
  const _isSeasonal = r.is_seasonal || false;
  const weeks_slim = [];
  for (let i = 0; i < 26; i++) {
    const m = manual[i] || 0, a = forecast[i] || 0;
    let sev = 'OK';
    if (_isSeasonal) {
      if (m > 0 && a === 0) sev = 'ALERT';
      else if (m > 0 && a > 0 && (a / m > 3 || m / Math.max(a, 1) > 3)) sev = 'ALERT';
    } else {
      if ((m === 0 && a > 0) || (a === 0 && m > 0)) sev = 'ALERT';
      else if (m > 0 && (a / m > 3 || m / Math.max(a, 1) > 3)) sev = 'ALERT';
    }
    weeks_slim.push({ week: i + 1, projection: m, severity: sev });
  }
  r.weeks_slim    = weeks_slim;
  r._needs_detail = false;
}

// Cache discoverWeeklyFids() result in sessionStorage so same-tab refreshes
// skip the /fields metadata call entirely.
function _loadFidCache() {
  try {
    const raw = sessionStorage.getItem(FID_SESS_KEY);
    if (!raw) return null;
    const obj = JSON.parse(raw);
    if (!obj || !Array.isArray(obj.manFids)) return null;
    return obj;
  } catch (e) { return null; }
}
function _saveFidCache(data) {
  try { sessionStorage.setItem(FID_SESS_KEY, JSON.stringify(data)); } catch (e) { /* ignore */ }
}

// -- Pull Inventory Flow (per-mstyle projected balances, receipts, demand) --
// The Inventory Flow table is keyed by Mstyle (warehouse-level inventory),
// not Acct-MStyle.  Many Projections records share the same mstyle, so we
// fetch the full table in pages of 1,000 (no WHERE filter) then attach the
// matching row to every Projections record that shares that mstyle.
//
// localStorage cache (above) short-circuits the bulk QB pull on warm loads  -
// each browser pays one pull per 6-hour window instead of every page open.
async function attachInvFlow(records) {
  if (!records.length) return {};

  // -- Cache fast-path ----------------------------------------------------
  // IndexedDB-primary cache (24h TTL) — survives browser restarts, no quota
  // competition with other QB apps.  sessionStorage warm on same-tab F5.
  if (!_invFlowCacheBypassed()) {
    const cached = await _loadInvFlowCache();
    if (cached && cached.map) {
      let nMatched = 0;
      for (const r of records) {
        if (r.mstyle && cached.map[r.mstyle]) {
          const d = cached.map[r.mstyle];
          r.inv_flow_beg       = d.beg || null;
          r.inv_flow_rcv       = d.rcv || null;
          r.inv_flow_prj       = d.prj || null;
          r.inv_flow_opn       = d.opn || null;
          r.inv_flow_opt_wos   = d.opt_wos || 0;
          r.inv_flow_next_rcpt = d.next_rcpt || '';
          r.inv_flow_lt_wks    = d.lt_wks   || 0;
          r.inv_flow_moq       = d.moq      || 0;
          r.inv_flow_supp_pos  = d.supp_pos  || '';
          r.inv_flow_ats_now   = d.ats_now   || 0;
          r.inv_flow_ats_oh    = d.ats_oh    || 0;
          r.inv_flow_ats_oo    = d.ats_oo    || 0;
          r.inv_flow_ats_oh_wos  = d.ats_oh_wos  || 0;
          r.inv_flow_ats_oo_wos  = d.ats_oo_wos  || 0;
          r.inv_flow_first_shpd  = d.first_shpd  || '';
          nMatched++;
        }
      }
      const ageStr = _fmtCacheAge(cached.ageMs);
      console.info(`[InvFlow] loaded from ${cached.source} cache (age ${ageStr})  -  ${Object.keys(cached.map).length} mstyles, ${nMatched} records attached`);
      _setDetail(`Inventory Flow: served from cache (${ageStr} old)  -  append ?nocache=1 to URL for fresh pull`);
      return cached.map;
    }
  } else {
    console.info('[InvFlow] cache bypass requested via ?nocache=1  -  forcing fresh pull');
  }

  const FK         = CFG.INV_FLOW_FK_MSTYLE;
  const BIDS       = CFG.INV_FLOW_BEG_FIDS;   // length 26
  const RIDS       = CFG.INV_FLOW_RCV_FIDS;   // length 26
  const PIDS       = CFG.INV_FLOW_PRJ_FIDS;   // length 26
  const OIDS       = CFG.INV_FLOW_OPN_FIDS;   // length 27: index 0=Wk0, 1..26=Wk1..26
  const OPT_WOS    = CFG.INV_FLOW_OPT_WOS;
  const OPT_FINAL  = CFG.INV_FLOW_OPT_WOS_FINAL;
  const NEXT_RCPT  = CFG.INV_FLOW_NEXT_RCPT_DT;
  const LT_WKS     = CFG.INV_FLOW_LT_WKS;
  const MOQ        = CFG.INV_FLOW_MOQ;
  const SUPP_PO    = INV_FLOW_SUPP_PO_FID;
  const ATS_NOW    = INV_FLOW_ATS_NOW_FID;
  const ATS_OH     = INV_FLOW_ATS_OH_FID;
  const ATS_OO     = INV_FLOW_ATS_OO_FID;
  const ATS_OH_WOS  = INV_FLOW_ATS_OH_WOS_FID;
  const ATS_OO_WOS  = INV_FLOW_ATS_OO_WOS_FID;
  const FIRST_SHPD  = INV_FLOW_FIRST_SHPD_FID;
  const sel         = [FK, ...BIDS, ...RIDS, ...PIDS, ...(OIDS||[]), OPT_WOS, OPT_FINAL, NEXT_RCPT, LT_WKS, MOQ,
                       ...[SUPP_PO, ATS_NOW, ATS_OH, ATS_OO, ATS_OH_WOS, ATS_OO_WOS, FIRST_SHPD].filter(Boolean)];
  const TOP         = 5000;
  const map        = {};
  let totalFetched = 0;

  // Numeric cell parser  -  these fields are NUMERIC, no rich-text stripping.
  const numCell = (row, fid) => {
    const cell = row[String(fid)];
    if (!cell || cell.value == null || cell.value === '') return 0;
    const n = Number(cell.value);
    return Number.isFinite(n) ? n : 0;
  };
  const strCell = (row, fid) => {
    const cell = row[String(fid)];
    return (cell && cell.value != null) ? String(cell.value) : '';
  };

  // Full-table scan in pages of 1,000  -  no WHERE filter.
  // Simpler and faster than building per-mstyle OR chains: QB parses a lean
  // query each time and we avoid the HTTP 400 payload limits that forced the
  // old 175-mstyle batching approach.  Typically 2-5 round trips total.
  let firstRowLogged = false;
  let skip = 0;
  while (true) {
    const resp = await qb('/records/query', {
      from: CFG.INV_FLOW_TID,
      select: sel,
      options: { top: TOP, skip: skip },
    });
    const rows = resp.data || [];
    if (!rows.length) break;
    for (const row of rows) {
      const m = (row[String(FK)] && row[String(FK)].value) || '';
      if (!m) continue;
      if (!firstRowLogged) {
        firstRowLogged = true;
        const begSample = BIDS.slice(0, 4).map(fid => ({ fid, val: row[String(fid)] }));
        const rcvSample = RIDS.slice(0, 4).map(fid => ({ fid, val: row[String(fid)] }));
        const prjSample = PIDS.slice(0, 4).map(fid => ({ fid, val: row[String(fid)] }));
        console.info('[InvFlow] sample row for mstyle', m,
                     '\n  Beg Wk1-4 raw:', begSample,
                     '\n  Rcv Wk1-4 raw:', rcvSample,
                     '\n  Prj Wk1-4 raw:', prjSample,
                     '\n  All keys present in row:', Object.keys(row).sort((a,b) => +a - +b));
      }
      // Opt WOS: prefer "Final" (Opt + Override) when present, fallback to base Opt
      const _final = numCell(row, OPT_FINAL);
      const _base  = numCell(row, OPT_WOS);
      const opt_wos = _final > 0 ? _final : _base;
      // OPN: 27-element raw array [Wk0, Wk1..Wk26].
      // W1 column = Wk0 + Wk1 (past-due merged into current week).
      // W2..W26 = Wk2..Wk26 directly.
      const opnRaw = OIDS ? OIDS.map(fid => numCell(row, fid)) : [];
      const opnDisplay = opnRaw.length === 27
        ? [opnRaw[0] + opnRaw[1], ...opnRaw.slice(2)]   // 26-element display array
        : [];
      map[m] = {
        beg: BIDS.map(fid => numCell(row, fid)),
        rcv: RIDS.map(fid => numCell(row, fid)),
        prj: PIDS.map(fid => numCell(row, fid)),
        opn: opnDisplay,
        opt_wos:   opt_wos,
        next_rcpt: strCell(row, NEXT_RCPT),  // ISO YYYY-MM-DD or empty
        lt_wks:    numCell(row, LT_WKS),     // Lead Time in weeks
        moq:       numCell(row, MOQ),        // Minimum Order Quantity
        supp_pos:  SUPP_PO    ? strCell(row, SUPP_PO)    : '',
        ats_now:   ATS_NOW   ? numCell(row, ATS_NOW)   : 0,
        ats_oh:    ATS_OH    ? numCell(row, ATS_OH)    : 0,
        ats_oo:    ATS_OO    ? numCell(row, ATS_OO)    : 0,
        ats_oh_wos:  ATS_OH_WOS  ? numCell(row, ATS_OH_WOS)  : 0,
        ats_oo_wos:  ATS_OO_WOS  ? numCell(row, ATS_OO_WOS)  : 0,
        first_shpd:  FIRST_SHPD  ? strCell(row, FIRST_SHPD)  : '',
      };
    }
    totalFetched += rows.length;
    _setDetail(`Inventory Flow: ${totalFetched.toLocaleString()} rows fetched...`);
    if (rows.length < TOP) break;
    skip += TOP;
  }

  let nMatched = 0;
  for (const r of records) {
    if (r.mstyle && map[r.mstyle]) {
      const d = map[r.mstyle];
      r.inv_flow_beg       = d.beg;
      r.inv_flow_rcv       = d.rcv;
      r.inv_flow_prj       = d.prj;
      r.inv_flow_opn       = d.opn || null;
      r.inv_flow_opt_wos   = d.opt_wos || 0;
      r.inv_flow_next_rcpt = d.next_rcpt || '';
      r.inv_flow_lt_wks    = d.lt_wks  || 0;
      r.inv_flow_moq       = d.moq     || 0;
      r.inv_flow_supp_pos   = d.supp_pos   || '';
      r.inv_flow_ats_now    = d.ats_now    || 0;
      r.inv_flow_ats_oh     = d.ats_oh     || 0;
      r.inv_flow_ats_oo     = d.ats_oo     || 0;
      r.inv_flow_ats_oh_wos  = d.ats_oh_wos  || 0;
      r.inv_flow_ats_oo_wos  = d.ats_oo_wos  || 0;
      r.inv_flow_first_shpd  = d.first_shpd  || '';
      nMatched++;
    }
  }
  console.info(`[InvFlow] ${Object.keys(map).length} unique mstyles fetched fresh from QB, attached to ${nMatched} records`);

  // Persist to localStorage so subsequent loads (this browser, this user)
  // within the next 6 hours short-circuit the bulk pull.
  if (Object.keys(map).length > 0) {
    await _saveInvFlowCache(map);
    console.info(`[InvFlow] saved ${Object.keys(map).length} mstyles to localStorage cache`);
  }
  return map;
}

// -- On-demand single-mstyle Inventory Flow loader ---------------------------
// Used by toggleDetail when the bulk attachInvFlow scan hasn't finished.
// Fetches one row from Inventory Flow for r.mstyle and attaches it to r.
// One query (typically <500 ms) versus waiting on the full table scan.
// Returns true if data was attached, false otherwise.
const _oneInvFlowInFlight = new Map();  // mstyle -> Promise (dedupe concurrent calls)
async function _loadOneInvFlowRow(r) {
  if (!r || !r.mstyle || !CFG.INV_FLOW_TID) return false;
  if (r.inv_flow_beg) return true;                    // already attached
  if (_oneInvFlowInFlight.has(r.mstyle)) return _oneInvFlowInFlight.get(r.mstyle);

  const FK         = CFG.INV_FLOW_FK_MSTYLE;
  const BIDS       = CFG.INV_FLOW_BEG_FIDS;
  const RIDS       = CFG.INV_FLOW_RCV_FIDS;
  const PIDS       = CFG.INV_FLOW_PRJ_FIDS;
  const OIDS       = CFG.INV_FLOW_OPN_FIDS;
  const OPT_WOS    = CFG.INV_FLOW_OPT_WOS;
  const OPT_FINAL  = CFG.INV_FLOW_OPT_WOS_FINAL;
  const NEXT_RCPT  = CFG.INV_FLOW_NEXT_RCPT_DT;
  const LT_WKS     = CFG.INV_FLOW_LT_WKS;
  const MOQ        = CFG.INV_FLOW_MOQ;
  const SUPP_PO    = INV_FLOW_SUPP_PO_FID;
  const ATS_NOW    = INV_FLOW_ATS_NOW_FID;
  const ATS_OH     = INV_FLOW_ATS_OH_FID;
  const ATS_OO     = INV_FLOW_ATS_OO_FID;
  const ATS_OH_WOS  = INV_FLOW_ATS_OH_WOS_FID;
  const ATS_OO_WOS  = INV_FLOW_ATS_OO_WOS_FID;
  const FIRST_SHPD  = INV_FLOW_FIRST_SHPD_FID;
  const sel = [FK, ...BIDS, ...RIDS, ...PIDS, ...(OIDS||[]), OPT_WOS, OPT_FINAL, NEXT_RCPT, LT_WKS, MOQ,
               ...[SUPP_PO, ATS_NOW, ATS_OH, ATS_OO, ATS_OH_WOS, ATS_OO_WOS, FIRST_SHPD].filter(Boolean)];

  const numCell = (row, fid) => {
    const cell = row[String(fid)];
    if (!cell || cell.value == null || cell.value === '') return 0;
    const n = Number(cell.value);
    return Number.isFinite(n) ? n : 0;
  };
  const strCell = (row, fid) => {
    const cell = row[String(fid)];
    return (cell && cell.value != null) ? String(cell.value) : '';
  };

  const escMstyle = r.mstyle.replace(/'/g, "''");
  const p = (async () => {
    try {
      const resp = await qb('/records/query', {
        from: CFG.INV_FLOW_TID,
        select: sel,
        where: `{${FK}.EX.'${escMstyle}'}`,
        options: { top: 1, skip: 0 },
      });
      const row = (resp && resp.data && resp.data[0]) || null;
      if (!row) return false;
      const _final = numCell(row, OPT_FINAL);
      const _base  = numCell(row, OPT_WOS);
      const opt_wos = _final > 0 ? _final : _base;
      const opnRaw = OIDS ? OIDS.map(fid => numCell(row, fid)) : [];
      const opnDisplay = opnRaw.length === 27 ? [opnRaw[0] + opnRaw[1], ...opnRaw.slice(2)] : [];
      // Only attach if bulk scan hasn't already attached this record's data
      if (!r.inv_flow_beg) {
        r.inv_flow_beg       = BIDS.map(fid => numCell(row, fid));
        r.inv_flow_rcv       = RIDS.map(fid => numCell(row, fid));
        r.inv_flow_prj       = PIDS.map(fid => numCell(row, fid));
        r.inv_flow_opn       = opnDisplay;
        r.inv_flow_opt_wos   = opt_wos;
        r.inv_flow_next_rcpt = strCell(row, NEXT_RCPT);
        r.inv_flow_lt_wks    = numCell(row, LT_WKS);
        r.inv_flow_moq       = numCell(row, MOQ);
        r.inv_flow_supp_pos   = SUPP_PO    ? strCell(row, SUPP_PO)    : '';
        r.inv_flow_ats_now    = ATS_NOW   ? numCell(row, ATS_NOW)   : 0;
        r.inv_flow_ats_oh     = ATS_OH    ? numCell(row, ATS_OH)    : 0;
        r.inv_flow_ats_oo     = ATS_OO    ? numCell(row, ATS_OO)    : 0;
        r.inv_flow_ats_oh_wos  = ATS_OH_WOS  ? numCell(row, ATS_OH_WOS)  : 0;
        r.inv_flow_ats_oo_wos  = ATS_OO_WOS  ? numCell(row, ATS_OO_WOS)  : 0;
        r.inv_flow_first_shpd  = FIRST_SHPD  ? strCell(row, FIRST_SHPD)  : '';
      }
      return true;
    } catch (e) {
      console.warn('[InvFlow] single-row load failed for', r.mstyle, e.message || e);
      return false;
    } finally {
      _oneInvFlowInFlight.delete(r.mstyle);
    }
  })();
  _oneInvFlowInFlight.set(r.mstyle, p);
  return p;
}

// -- ATS (Available to Sell) L26W history from Inventory History - Weekly ----
let _atsHistPromise = null;

// On-demand fetch for a single mstyle (used when a detail panel opens before
// the bulk batch finishes).  Returns the 26-element array (oldest -> newest)
// or null when no row matches.  Caches the result onto r.ats_hist so re-opens
// don't refetch.  Typical latency: 300-500 ms vs the 30-120 sec full batch.
async function _fetchAtsForMstyle(r) {
  if (!r || !r.mstyle || r.ats_hist) return r.ats_hist || null;
  const FK   = CFG.ATS_HIST_FK_MSTYLE;
  const FIDS = CFG.ATS_HIST_FIDS;
  try {
    const esc = String(r.mstyle).replace(/'/g, "''");
    const resp = await qb('/records/query', {
      from:    CFG.ATS_HIST_TID,
      select:  [FK, ...FIDS],
      where:   `{${FK}.EX.'${esc}'}`,
      options: { top: 1, skip: 0 },
    });
    const row = (resp && resp.data && resp.data[0]) || null;
    if (!row) return null;
    const numCell = (rr, fid) => {
      const c = rr[String(fid)];
      if (!c || c.value == null || c.value === '') return 0;
      const n = Number(c.value);
      return Number.isFinite(n) ? n : 0;
    };
    const arr = FIDS.map(fid => numCell(row, fid));
    r.ats_hist = arr;
    return arr;
  } catch (e) {
    console.warn('[AtsHist] single-mstyle fetch failed:', e);
    return null;
  }
}

async function attachAtsHistory(records) {
  if (!records.length) return {};
  if (!_invFlowCacheBypassed()) {
    const cached = _loadAtsHistCache();
    if (cached && cached.map) {
      let n = 0;
      for (const r of records) { if (r.mstyle && cached.map[r.mstyle]) { r.ats_hist = cached.map[r.mstyle]; n++; } }
      const ageStr = _fmtCacheAge(cached.ageMs);
      console.info(`[AtsHist] loaded from cache (age ${ageStr}) - ${n} records attached`);
      return cached.map;
    }
  }
  const FK   = CFG.ATS_HIST_FK_MSTYLE;
  const FIDS = CFG.ATS_HIST_FIDS;
  const sel  = [FK, ...FIDS];
  const TOP  = 1000;
  const map  = {};
  let totalFetched = 0;
  const numCell = (row, fid) => {
    const cell = row[String(fid)];
    if (!cell || cell.value == null || cell.value === '') return 0;
    const n = Number(cell.value);
    return Number.isFinite(n) ? n : 0;
  };
  let skip = 0;
  while (true) {
    const resp = await qb('/records/query', {
      from: CFG.ATS_HIST_TID,
      select: sel,
      options: { top: TOP, skip: skip },
    });
    const rows = resp.data || [];
    if (!rows.length) break;
    for (const row of rows) {
      const m = (row[String(FK)] && row[String(FK)].value) || '';
      if (!m) continue;
      map[m] = FIDS.map(fid => numCell(row, fid));  // oldest->newest
    }
    totalFetched += rows.length;
    if (rows.length < TOP) break;
    skip += TOP;
  }
  let nMatched = 0;
  for (const r of records) {
    if (r.mstyle && map[r.mstyle]) { r.ats_hist = map[r.mstyle]; nMatched++; }
  }
  console.info(`[AtsHist] ${Object.keys(map).length} mstyles fetched, ${nMatched} records attached`);
  if (Object.keys(map).length > 0) _saveAtsHistCache(map);
  return map;
}

// -- Convert a raw QB row into the record shape the UI expects --------------
function v(row, fid) {
  const cell = row[String(fid)];
  return cell == null ? null : cell.value;
}
function num(row, fid) {
  const x = v(row, fid);
  return (x == null || x === '') ? 0 : Number(x) || 0;
}
function str(row, fid) {
  const x = v(row, fid);
  if (x == null) return '';
  if (typeof x === 'object') return (x.name || x.email || '').trim();
  return String(x);
}
function bool(row, fid) {
  const x = v(row, fid);
  return x === true || x === 'true' || x === 1 || x === '1';
}

// QB rich-text fields (e.g. fid 376 "Customr Name") return HTML markup like
// `<div style='color:#33A7FF' align='Left'><font size=-1><b>WAL MART</b></font></div>`.
// Strip tags, decode the few entities QB actually emits, and collapse whitespace.
function _stripHtml(s) {
  if (!s) return '';
  return String(s)
    .replace(/<[^>]+>/g, ' ')
    .replace(/&nbsp;/gi, ' ')
    .replace(/&amp;/gi,  '&')
    .replace(/&lt;/gi,   '<')
    .replace(/&gt;/gi,   '>')
    .replace(/&quot;/gi, '"')
    .replace(/&#39;/gi,  "'")
    .replace(/\s+/g, ' ')
    .trim();
}

// Heuristic: classify each record's forecasting model by matching the
// well-known "why" phrases the python forecaster writes into AI_ALERT
// (and into AI_ANALYSIS as the lead sentence).  Records without any of
// these phrases (i.e. quiet items with no alert text) come back as ''
// and just don't appear in the All-Models filter dropdown.
function _parseModel(txt) {
  if (!txt) return '';
  const s = String(txt).toLowerCase();
  if (s.indexOf('no orders in 13')         !== -1) return 'Inactive';
  if (s.indexOf('account orders in bursts')!== -1) return "Croston's";
  if (s.indexOf('sparse history')          !== -1) return 'Heuristic';
  if (s.indexOf('last 13 weeks')           !== -1) return 'Holt-Winters';
  return '';
}

// Detect weeks where an Open Customer PO and a Manual Projection exist in the
// same week (all customers) or within a 4-week window (off-price accounts).
// Returns { conflicts: [{poWk,prjWk,poQty,prjQty}], hasConflict: bool }
// Off-price rationale: POs ship the same or next week, so a nearby projection
// double-counts demand even when it's not literally the same calendar week.
function _computePoPrjConflicts(opnW, manProj, isOffprice) {
  const nearWks = isOffprice ? 3 : 0;   // 0 = same week only; 3 = ±3 wks (4-wk window)
  const conflicts = [];
  const seen = new Set();
  for (let i = 0; i < 26; i++) {
    const poQty = opnW[i] || 0;
    if (!poQty) continue;
    for (let j = Math.max(0, i - nearWks); j <= Math.min(25, i + nearWks); j++) {
      const prjQty = manProj[j] || 0;
      if (!prjQty) continue;
      const pk = `${i}_${j}`;
      if (seen.has(pk)) continue;
      seen.add(pk);
      conflicts.push({ poWk: i + 1, prjWk: j + 1, poQty, prjQty });
    }
  }
  return { conflicts, hasConflict: conflicts.length > 0 };
}

// Forecast status flag: 'Over-Projected' | 'Under-Projected' | 'On Plan' | 'Inactive' | ''
// Noise filter: max(ai, man) < 1,000 OR |ai−man| < 500 → blank (no flag).
// Threshold: +-7.5% of manual total (matches Priority On-Plan threshold).
//   pct > +7.5% → AI sees more than manual → manual is Under-Projected
//   pct < -7.5% → AI sees less than manual → manual is Over-Projected
function _fcstStatus(ai_model, ai_total, proj_total) {
  if (ai_model === 'Inactive') return 'Inactive';
  const maxVal = Math.max(ai_total, proj_total);
  const gap    = Math.abs(ai_total - proj_total);
  if (maxVal < 1000 || gap < 500) return '';
  // No plan entered but AI has demand: manual is Under-Projected (can't compute a %).
  if (proj_total === 0 && ai_total > 0) return 'Under-Projected';
  const pct = (ai_total - proj_total) / proj_total * 100;
  if (pct <= -7.5) return 'Over-Projected';
  if (pct >=  7.5) return 'Under-Projected';
  return 'On Plan';
}

// Instantly refresh the 6 metric cells in the main table row for a given key.
// Called after any AI or manual projection save so the row reflects the new
// values without a full re-render.  Uses the IDs stamped by renderPage.
function _refreshRowMetrics(key) {
  const rec = ALL_RECORDS.find(x => x.key === key);
  if (!rec) return;
  const sid = key.replace(/[^a-zA-Z0-9]/g, '_');

  // Recalculate all derived metrics from current in-memory state
  const opnTotal  = rec.opn_total  || 0;
  const aiTotal   = rec.ai_total   || 0;
  const projTotal = rec.proj_total || 0;
  const aiWk      = Math.round(((aiTotal   + opnTotal) / 26) * 10) / 10;
  const projWk    = Math.round(((projTotal + opnTotal) / 26) * 10) / 10;
  const aiVsProj  = projTotal > 0 ? (aiTotal - projTotal) / projTotal * 100
                  : (aiTotal > 0 ? null : 0);  // null = no plan entered; 0 = both zero
  const l13       = rec.shp_wk || 0;
  const l13Avail  = l13 > 0;
  const aiVsL13   = l13Avail ? (aiWk   - l13) / l13 * 100 : 0;
  const manVsL13  = l13Avail ? (projWk - l13) / l13 * 100 : 0;
  const fcstStatus = _fcstStatus(rec.ai_model, aiTotal, projTotal);

  // Persist back onto the record so filters / exports stay consistent
  rec.ai_wk      = aiWk;
  rec.proj_wk    = projWk;
  rec.ai_vs_l13  = Math.round(aiVsL13  * 10) / 10;
  rec.man_vs_l13 = Math.round(manVsL13 * 10) / 10;
  rec.pct_diff   = aiVsProj;
  rec.fcst_status = fcstStatus;

  // Patch DOM cells
  const projWkEl = document.getElementById('metric-projwk-' + sid);
  if (projWkEl) projWkEl.textContent = fmtN(Math.round(projWk));

  const aiWkEl = document.getElementById('metric-aiwk-' + sid);
  if (aiWkEl) aiWkEl.textContent = fmtN(Math.round(aiWk));

  const aiProjEl = document.getElementById('metric-aiproj-' + sid);
  if (aiProjEl) {
    aiProjEl.textContent = aiVsProj === null ? '-' : (aiVsProj >= 0 ? '+' : '') + aiVsProj.toFixed(1) + '%';
    aiProjEl.style.color = aiVsProj === null ? '#888' : aiVsProj > 0 ? '#2e7d32' : aiVsProj < 0 ? '#c62828' : '#888';
  }

  const fcstEl = document.getElementById('metric-fcst-' + sid);
  if (fcstEl) fcstEl.innerHTML = _fcstStatusBadge(fcstStatus);

  const aiL13El = document.getElementById('metric-ail13-' + sid);
  if (aiL13El) {
    aiL13El.textContent = l13Avail ? (aiVsL13 >= 0 ? '+' : '') + (Math.round(aiVsL13 * 10) / 10).toFixed(1) + '%' : ' - ';
    aiL13El.style.color = !l13Avail ? '#888' : aiVsL13 > 0 ? '#2e7d32' : aiVsL13 < 0 ? '#c62828' : '#888';
  }

  const manL13El = document.getElementById('metric-manl13-' + sid);
  if (manL13El) {
    manL13El.textContent = l13Avail ? (manVsL13 >= 0 ? '+' : '') + (Math.round(manVsL13 * 10) / 10).toFixed(1) + '%' : ' - ';
    manL13El.style.color = !l13Avail ? '#888' : manVsL13 > 0 ? '#2e7d32' : manVsL13 < 0 ? '#c62828' : '#888';
  }

  // Patch detail-pane Avg/Wk cell (uses proj_total only, no opn_total)
  const avgWkEl = document.getElementById('man-avgwk-' + sid);
  if (avgWkEl) avgWkEl.textContent = fmtN(Math.round(projTotal / 26));

  // Recompute PO/PRJ conflict from updated projection values and refresh badge
  const _updatedManProj = (rec.weeks_slim || []).map(w => w.projection || 0);
  const { conflicts: _newCfls, hasConflict: _newHasCfl } =
    _computePoPrjConflicts(rec.opn_w || [], _updatedManProj, rec.is_offprice || false);
  rec.po_prj_conflicts    = _newCfls;
  rec.has_po_prj_conflict = _newHasCfl;
  const conflictBadgeEl = document.getElementById('conflict-badge-' + sid);
  if (conflictBadgeEl) conflictBadgeEl.style.display = _newHasCfl ? 'inline-block' : 'none';
}

// Colored pill badge for forecast status (returns HTML string or '').
function _fcstStatusBadge(status) {
  const styles = {
    'Over-Projected':  'background:#c62828;color:#fff',
    'Under-Projected': 'background:#66bb6a;color:#fff',
    'On Plan':         'background:#2e7d32;color:#fff',
    'Inactive':        'background:#757575;color:#fff',
  };
  const st = styles[status];
  if (!st) return '';
  return `<span style="${st};display:inline-block;padding:2px 8px;border-radius:10px;font-size:11px;font-weight:700;white-space:nowrap">${status}</span>`;
}

// Customer-friendly label  -  generalized 2026-05-08 to support any retailer's
// POS data (not just Amazon).  As Walmart/Petsmart/Petco POS feeds come
// online, the labeling automatically adapts.  Mirrors _friendly_cust_name in
// scripts/inventory_forecaster.py.
function _friendlyCustName(cust) {
  if (!cust) return 'Retailer';
  const s = String(cust).toUpperCase();
  const M = [
    ['AMAZON','Amazon'], ['WAL MART','Walmart'], ['WALMART','Walmart'],
    ['PETSMART','Petsmart'], ['PETCO','Petco'], ['CHEWY','Chewy'],
    ['TARGET','Target'], ['KROGER','Kroger'], ['LOWES','Lowes'],
    ['HOME DEPOT','Home Depot'], ['ROSS','Ross'], ['BURLINGTON','Burlington'],
    ['CVS','CVS'], ['DOLLAR GENERAL','Dollar General'],
    ['DOLLAR TREE','Dollar Tree'], ['FAMILY DOLLAR','Family Dollar']
  ];
  for (const [needle, label] of M) {
    if (s.indexOf(needle) !== -1) return label;
  }
  const first = (s.split(/\s+/)[0] || 'Retailer').toLowerCase();
  return first.charAt(0).toUpperCase() + first.slice(1);
}

// -- Amazon Listing Info block -----------------------------------------------
// Shown in the detail panel for Amazon records INSTEAD of the POG block.
// Displays ASIN + link + 1st Shpd Date (from Inventory Flow, mstyle-level).
function _buildAmzInfoBlockHtml(r) {
  const asin = (r.cust_sku || '').trim();
  // 1st_Shpd_Date arrives as ISO YYYY-MM-DD (or YYYY-MM-DDThh:mm:ssZ)
  const rawDate = r.inv_flow_first_shpd || '';
  const fmtDate = iso => {
    if (!iso) return '-';
    const m = String(iso).match(/^(\d{4})-(\d{2})-(\d{2})/);
    if (!m) return iso;
    return new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]))
      .toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
  };
  const amzUrl  = asin ? `https://www.amazon.com/dp/${asin}` : '';
  const asinHtml = asin
    ? `<span style="font-family:monospace;font-size:12px;font-weight:600;">${asin}</span>`
    : '<span style="color:#999">-</span>';
  const linkHtml = amzUrl
    ? `<a href="${amzUrl}" target="_blank" rel="noopener noreferrer"
          style="color:#1565c0;text-decoration:none;font-weight:600;white-space:nowrap;">
         View on Amazon &#8594;</a>`
    : '';
  return `
    <div style="margin:8px 12px 0 12px;padding:10px 12px;background:#fff8e1;border:1px solid #ffe082;border-radius:6px;font-size:11px;color:#3e2723;">
      <div style="font-weight:700;margin-bottom:6px;color:#e65100;"> Amazon Listing</div>
      <div style="display:flex;flex-wrap:wrap;gap:10px 28px;align-items:center;">
        <div><b>ASIN:</b> ${asinHtml}</div>
        ${linkHtml ? `<div>${linkHtml}</div>` : ''}
        <div><b>1st Shpd Date:</b> ${fmtDate(rawDate)}</div>
      </div>
    </div>`;
}

// -- POG / ISO Inventory Plan block -----------------------------------------
// Renders POG dates + computed ISO order context.  Customers typically order
// the ISO (in-store opening) shipment 4-6 weeks before POG Start, sized at
// store_count x 1.5 master packs/store (range 1-2).  After ISO they pause
// for ~4 weeks until store inventory needs replenishing.  Lead time from
// cancel-date to in-store is 2-4 weeks.
function _buildPogBlockHtml(r) {
  const pogLaunch   = r.pog_launch    || '';
  const pogEnd      = r.pog_end       || '';
  const stores      = Number(r.store_count   || 0);
  const mp          = Number(r.master_pack   || 1);
  const estIso      = Number(r.est_iso_qty   || 0);   // formula field (read-only)
  const estIsoInput = Number(r.est_iso_input || 0);   // planner-entered ISO qty (FID 1606)
  const initUpspw   = Number(r.init_upspw   || 0);    // planner-entered initial UPSPW (FID 1607)
  // ALWAYS render the block  -  planners need the editable inputs even when
  // QB has no POG/Store data yet (e.g. a new item being set up).

  const fmtDate = iso => {
    if (!iso) return ' - ';
    try {
      const d = new Date(iso);
      if (isNaN(d.getTime())) return iso;
      return d.toLocaleDateString('en-US', { year:'numeric', month:'short', day:'numeric' });
    } catch (e) { return iso; }
  };
  const addDays = (iso, days) => {
    if (!iso) return null;
    const d = new Date(iso);
    if (isNaN(d.getTime())) return null;
    d.setDate(d.getDate() + days);
    return d;
  };
  const fmtRange = (iso, daysA, daysB) => {
    const dA = addDays(iso, daysA), dB = addDays(iso, daysB);
    if (!dA || !dB) return ' - ';
    const opt = { month:'short', day:'numeric' };
    return `${dA.toLocaleDateString('en-US', opt)}-${dB.toLocaleDateString('en-US', opt)}`;
  };

  // Computed ISO context
  let pogDur = '';
  if (pogLaunch && pogEnd) {
    const a = new Date(pogLaunch), b = new Date(pogEnd);
    if (!isNaN(a.getTime()) && !isNaN(b.getTime())) {
      const wks = Math.round((b - a) / (7 * 86400 * 1000));
      pogDur = ` <span style="color:#888;font-weight:normal">(${wks} wks)</span>`;
    }
  }
  // ISO qty estimate band: store_count x {1, 1.5, 2} master packs
  const isoLow  = stores * mp * 1.0;
  const isoMid  = stores * mp * 1.5;
  const isoHigh = stores * mp * 2.0;
  const fmtN    = n => Math.round(n).toLocaleString();

  // Order window: 4-6 weeks before POG Start
  const orderWindow = pogLaunch ? fmtRange(pogLaunch, -42, -28) : ' - ';
  // Lead time from cancel to in-store: 2-4 weeks -> cancel date is 2-4 wks before POG Start
  const cancelWindow = pogLaunch ? fmtRange(pogLaunch, -28, -14) : ' - ';

  // ISO-format YYYY-MM-DD for date inputs (handles full ISO timestamps too)
  const toIsoDate = s => {
    if (!s) return '';
    const m = String(s).match(/^(\d{4}-\d{2}-\d{2})/);
    return m ? m[1] : '';
  };
  const safeKey = (r.key || '').replace(/'/g, "&#39;");
  return `
    <div style="margin:8px 12px 0 12px;padding:10px 12px;background:#f5fbf3;border:1px solid #c7e2bf;border-radius:6px;font-size:11px;color:#2e4f24;">
      <div style="font-weight:700;margin-bottom:6px;color:#1b5e20;"> POG Information</div>
      <div style="display:flex;align-items:center;gap:8px;margin-bottom:8px;padding-bottom:8px;border-bottom:1px solid #d4ead0;flex-wrap:wrap;">
        <b style="white-space:nowrap;">Inventory Request:</b>
        <input type="number" min="1" step="1" id="pog-req-id-${safeKey}"
               placeholder="Request ID #"
               onkeydown="if(event.key==='Enter'){lookupInvRequest('${safeKey}',this.value);}"
               style="font-size:11px;padding:2px 6px;border:1px solid #c7e2bf;border-radius:3px;
                      background:#fff;color:#2e4f24;font-family:inherit;width:110px;">
        <button onclick="lookupInvRequest('${safeKey}',document.getElementById('pog-req-id-${safeKey}').value)"
                style="font-size:10px;padding:2px 8px;border:1px solid #a5c99a;border-radius:3px;
                       background:#e8f5e9;color:#1b5e20;cursor:pointer;white-space:nowrap;">Load</button>
        <span id="pog-req-msg-${safeKey}" style="font-size:10px;color:#555;flex:1;"></span>
      </div>
      <div style="display:flex;flex-wrap:wrap;gap:18px 24px;align-items:center;">
        <div>
          <b>POG Launch:</b>
          <input type="date" id="pog-launch-${safeKey}" value="${toIsoDate(pogLaunch)}"
                 onchange="savePogDate('${safeKey}','launch',this.value,this)"
                 title="Click to edit  -  autosaves on change"
                 style="font-size:11px;padding:2px 6px;border:1px solid #c7e2bf;border-radius:3px;background:#fff;color:#2e4f24;font-family:inherit;margin-left:4px;">
          ${pogDur}
        </div>
        <div>
          <b>POG End:</b>
          <input type="date" id="pog-end-${safeKey}" value="${toIsoDate(pogEnd)}"
                 onchange="savePogDate('${safeKey}','end',this.value,this)"
                 title="Click to edit  -  autosaves on change"
                 style="font-size:11px;padding:2px 6px;border:1px solid #c7e2bf;border-radius:3px;background:#fff;color:#2e4f24;font-family:inherit;margin-left:4px;">
        </div>
        <div>
          <b>Store count:</b>
          <input type="number" min="0" step="1" id="store-count-${safeKey}" value="${stores || ''}"
                 onchange="saveStoreCount('${safeKey}',this.value,this)"
                 title="Autosaves on change"
                 placeholder="0"
                 style="font-size:11px;padding:2px 6px;border:1px solid #c7e2bf;border-radius:3px;background:#fff;color:#2e4f24;font-family:inherit;margin-left:4px;width:80px;">
        </div>
        <div>
          <b>Estimated ISO:</b>
          <input type="number" min="0" step="1" id="est-iso-input-${safeKey}" value="${estIsoInput || ''}"
                 onchange="saveEstIsoInput('${safeKey}',this.value,this)"
                 title="Expected initial stocking order qty the customer will place (~4 wks before POG). Used by AI forecaster for ramp projections."
                 placeholder="0"
                 style="font-size:11px;padding:2px 6px;border:1px solid #c7e2bf;border-radius:3px;background:#fff;color:#2e4f24;font-family:inherit;margin-left:4px;width:90px;">
        </div>
        <div>
          <b>Initial UPSPW:</b>
          <input type="number" min="0" step="0.01" id="init-upspw-${safeKey}" value="${initUpspw || ''}"
                 onchange="saveInitUpspw('${safeKey}',this.value,this)"
                 title="Baseline units-per-store-per-week before sales history is available. AI uses this for weeks 1-4 ramp projections."
                 placeholder="0"
                 style="font-size:11px;padding:2px 6px;border:1px solid #c7e2bf;border-radius:3px;background:#fff;color:#2e4f24;font-family:inherit;margin-left:4px;width:70px;">
        </div>
        <div><b>Master pack:</b> ${mp.toLocaleString()}/case</div>
        ${estIso ? `<div><b>QB Est. ISO Qty:</b> ${estIso.toLocaleString()}</div>` : ''}
        <div id="pog-msg-${safeKey}" style="font-size:10px;color:#1b5e20;"></div>
      </div>
      ${stores > 0 ? `
      <div style="margin-top:8px;padding-top:6px;border-top:1px solid #d4ead0;">
        <b>Expected ISO order:</b> ~${fmtN(isoMid)} units (${stores.toLocaleString()} stores x 1.5 MP @ ${mp}/case).
        Range ${fmtN(isoLow)}-${fmtN(isoHigh)} (1-2 MP/store).
      </div>` : ''}
      ${pogLaunch ? `
      <div style="margin-top:4px;">
        <b>Likely order window:</b> ${orderWindow} <span style="color:#888;">(4-6 wks before POG launch)</span>  | 
        <b>Cancel->in-store lead time:</b> 2-4 wks (cancel ${cancelWindow}).
        After ISO, expect a ~4-wk pause before replenishment orders begin.
      </div>` : ''}
    </div>`;
}

// Inline POG date save  -  fires on every <input type="date"> change.  Writes
// either [POG Launch Date] (fid 1594) or [POG End Date] (fid 1595) on the
// Projections table via the existing /records mergeFieldId upsert pattern.
async function savePogDate(key, which, isoValue, el) {
  const safeKey = key.replace(/'/g, '&#39;');
  const msg = document.getElementById('pog-msg-' + safeKey);
  const fid = which === 'launch' ? CFG.FID.POG_LAUNCH : CFG.FID.POG_END;
  const label = which === 'launch' ? 'POG Launch' : 'POG End';
  if (msg) { msg.textContent = 'Saving...'; msg.style.color = '#888'; }
  if (el) el.style.background = '#fff9c4';
  try {
    const fields = {};
    fields[CFG.FID.KEY] = { value: key };
    // Pass empty string as null so QB clears the date when the planner blanks it
    fields[fid]         = { value: isoValue || null };
    await qb('/records', {
      to: CFG.PROJECTIONS_TID,
      data: [fields],
      mergeFieldId: CFG.FID.KEY,
    });
    // Mirror to local record so re-expand shows the saved value
    const rec = ALL_RECORDS.find(x => x.key === key);
    if (rec) {
      if (which === 'launch') rec.pog_launch = isoValue || '';
      else                    rec.pog_end    = isoValue || '';
    }
    if (msg) { msg.textContent = `\u2713 ${label} saved`; msg.style.color = '#2e7d32'; setTimeout(() => { if (msg) msg.textContent = ''; }, 2500); }
    if (el) el.style.background = '#e8f5e9';
    setTimeout(() => { if (el) el.style.background = '#fff'; }, 2000);
  } catch (e) {
    if (msg) { msg.textContent = `Save failed: ${e.message || e}`; msg.style.color = '#c62828'; }
    if (el) el.style.background = '#ffebee';
  }
}
window.savePogDate = savePogDate;

// Inline Store Count save  -  fires on every <input type="number"> change.
// Writes [Store Count] (fid 14) on Projections via mergeFieldId upsert.
async function saveStoreCount(key, rawValue, el) {
  const safeKey = key.replace(/'/g, '&#39;');
  const msg = document.getElementById('pog-msg-' + safeKey);
  // Coerce empty/blank to null (clears the cell) and non-numeric to 0
  const trimmed = String(rawValue || '').trim();
  const value = trimmed === '' ? null : Math.max(0, parseInt(trimmed, 10) || 0);
  if (msg) { msg.textContent = 'Saving...'; msg.style.color = '#888'; }
  if (el) el.style.background = '#fff9c4';
  try {
    const fields = {};
    fields[CFG.FID.KEY]         = { value: key };
    fields[CFG.FID.STORE_COUNT] = { value: value };
    await qb('/records', {
      to: CFG.PROJECTIONS_TID,
      data: [fields],
      mergeFieldId: CFG.FID.KEY,
    });
    const rec = ALL_RECORDS.find(x => x.key === key);
    if (rec) rec.store_count = value || 0;
    if (msg) { msg.textContent = `\u2713 Store count saved`; msg.style.color = '#2e7d32'; setTimeout(() => { if (msg) msg.textContent = ''; }, 2500); }
    if (el) el.style.background = '#e8f5e9';
    setTimeout(() => { if (el) el.style.background = '#fff'; }, 2000);
  } catch (e) {
    if (msg) { msg.textContent = `Save failed: ${e.message || e}`; msg.style.color = '#c62828'; }
    if (el) el.style.background = '#ffebee';
  }
}
window.saveStoreCount = saveStoreCount;

// Inline Estimated ISO save  -  writes FID 1606 (planner-entered ISO order qty).
// AI forecaster reads this field to build initial ramp projections for new items.
async function saveEstIsoInput(key, rawValue, el) {
  const safeKey = key.replace(/'/g, '&#39;');
  const msg = document.getElementById('pog-msg-' + safeKey);
  const trimmed = String(rawValue || '').trim();
  const value = trimmed === '' ? null : Math.max(0, parseInt(trimmed, 10) || 0);
  if (msg) { msg.textContent = 'Saving...'; msg.style.color = '#888'; }
  if (el) el.style.background = '#fff9c4';
  try {
    const fields = {};
    fields[CFG.FID.KEY]           = { value: key };
    fields[CFG.FID.EST_ISO_INPUT] = { value: value };
    await qb('/records', { to: CFG.PROJECTIONS_TID, data: [fields], mergeFieldId: CFG.FID.KEY });
    const rec = ALL_RECORDS.find(x => x.key === key);
    if (rec) rec.est_iso_input = value || 0;
    if (msg) { msg.textContent = '✓ Estimated ISO saved'; msg.style.color = '#2e7d32'; setTimeout(() => { if (msg) msg.textContent = ''; }, 2500); }
    if (el) el.style.background = '#e8f5e9';
    setTimeout(() => { if (el) el.style.background = '#fff'; }, 2000);
  } catch (e) {
    if (msg) { msg.textContent = `Save failed: ${e.message || e}`; msg.style.color = '#c62828'; }
    if (el) el.style.background = '#ffebee';
  }
}
window.saveEstIsoInput = saveEstIsoInput;

// Inline Initial UPSPW save  -  writes FID 1607 (planner-entered units/store/week baseline).
// AI forecaster uses this as the projection rate for weeks 1-4 before sales history exists.
async function saveInitUpspw(key, rawValue, el) {
  const safeKey = key.replace(/'/g, '&#39;');
  const msg = document.getElementById('pog-msg-' + safeKey);
  const trimmed = String(rawValue || '').trim();
  const value = trimmed === '' ? null : Math.max(0, parseFloat(trimmed) || 0);
  if (msg) { msg.textContent = 'Saving...'; msg.style.color = '#888'; }
  if (el) el.style.background = '#fff9c4';
  try {
    const fields = {};
    fields[CFG.FID.KEY]        = { value: key };
    fields[CFG.FID.INIT_UPSPW] = { value: value };
    await qb('/records', { to: CFG.PROJECTIONS_TID, data: [fields], mergeFieldId: CFG.FID.KEY });
    const rec = ALL_RECORDS.find(x => x.key === key);
    if (rec) rec.init_upspw = value || 0;
    if (msg) { msg.textContent = '✓ Initial UPSPW saved'; msg.style.color = '#2e7d32'; setTimeout(() => { if (msg) msg.textContent = ''; }, 2500); }
    if (el) el.style.background = '#e8f5e9';
    setTimeout(() => { if (el) el.style.background = '#fff'; }, 2000);
  } catch (e) {
    if (msg) { msg.textContent = `Save failed: ${e.message || e}`; msg.style.color = '#c62828'; }
    if (el) el.style.background = '#ffebee';
  }
}
window.saveInitUpspw = saveInitUpspw;

// -- Inventory Request Detail lookup ----------------------------------------
// Called from the Inventory Request row in the POG Info block.  Fetches a record
// from the Inventory Request Detail table (btjf9wtis) by Record ID#, verifies
// it belongs to the current Acct#-MStyle, then auto-populates and saves the
// POG fields on the Projections table.
//
// Fields read from Inventory Request Detail (btjf9wtis):
//   fid 16 = Related Request ID# (lookup key the planner types in)
//   fid 36 = Acct#-Mstyle        (verified against current key)
//   fid 43 = POG Set Date      -> POG Launch (fid 1594 on Projections)
//   fid 44 = POG End Date      -> POG End (fid 1595 on Projections)
//   fid 22 = ISO Units         -> Est ISO Input (fid 1606 on Projections)
//   fid 14 = ISO Pipeline Qty  -> displayed in status (no corresponding Projections field)
//   fid 11 = # Stores          -> Store Count (fid 14 on Projections)
//   fid 12 = UPSPW             -> Init UPSPW (fid 1607 on Projections)
async function lookupInvRequest(key, requestIdStr) {
  const safeKey = key.replace(/'/g, '&#39;');
  const msgEl   = document.getElementById('pog-req-msg-' + safeKey);
  const setMsg  = (txt, color) => { if (msgEl) { msgEl.textContent = txt; msgEl.style.color = color || '#555'; } };

  const rid = parseInt(requestIdStr, 10);
  if (!rid || rid <= 0) { setMsg('Enter a valid Request ID number.', '#c00'); return; }

  setMsg('Looking up...', '#888');
  try {
    const F       = CFG.INV_REQ_FID;
    const safeQbKey = key.replace(/'/g, "\\'");
    const resp = await qb('/records/query', {
      from:    CFG.INV_REQ_DETAIL_TID,
      select:  [F.RELATED_REQ_ID, F.ACCT_MSTYLE, F.POG_SET_DATE, F.POG_END_DATE,
                F.ISO_UNITS, F.ISO_PIPELINE, F.STORES, F.UPSPW],
      where:   `{${F.RELATED_REQ_ID}.EX.${rid}}AND{${F.ACCT_MSTYLE}.EX.'${safeQbKey}'}`,
      options: { top: 1 },
    });

    const rows = resp?.data || [];
    if (!rows.length) {
      setMsg(`No record found for Request #${rid} with item ${key}.`, '#c00');
      return;
    }

    const row = rows[0];

    // Strip timestamps -- QB dates arrive as "YYYY-MM-DDT..." or plain "YYYY-MM-DD"
    const toIso = v => { if (!v) return ''; const m = String(v).match(/^(\d{4}-\d{2}-\d{2})/); return m ? m[1] : ''; };

    const pogSet   = toIso(row[F.POG_SET_DATE]?.value);
    const pogEnd   = toIso(row[F.POG_END_DATE]?.value);
    const isoUnits = Number(row[F.ISO_UNITS]?.value  || 0);
    const isoPipe  = Number(row[F.ISO_PIPELINE]?.value || 0);
    const stores   = Number(row[F.STORES]?.value    || 0);
    const upspw    = Number(row[F.UPSPW]?.value     || 0);

    const filled = [];

    if (pogSet) {
      const el = document.getElementById('pog-launch-' + safeKey);
      if (el) { el.value = pogSet; await savePogDate(key, 'launch', pogSet, el); filled.push('POG Set'); }
      // Default POG End = launch + 364 days when the request has no end date
      if (!pogEnd) {
        const _d = new Date(pogSet);
        _d.setDate(_d.getDate() + 364);
        const _defaultEnd = _d.toISOString().slice(0, 10);
        const endEl = document.getElementById('pog-end-' + safeKey);
        if (endEl) { endEl.value = _defaultEnd; await savePogDate(key, 'end', _defaultEnd, endEl); filled.push('POG End (default)'); }
      }
    }
    if (pogEnd) {
      const el = document.getElementById('pog-end-' + safeKey);
      if (el) { el.value = pogEnd; await savePogDate(key, 'end', pogEnd, el); filled.push('POG End'); }
    }
    if (stores) {
      const el = document.getElementById('store-count-' + safeKey);
      if (el) { el.value = stores; await saveStoreCount(key, stores, el); filled.push('Stores'); }
    }
    if (isoUnits) {
      const el = document.getElementById('est-iso-input-' + safeKey);
      if (el) { el.value = isoUnits; await saveEstIsoInput(key, isoUnits, el); filled.push('ISO Units'); }
    }
    if (upspw) {
      const el = document.getElementById('init-upspw-' + safeKey);
      if (el) { el.value = upspw; await saveInitUpspw(key, upspw, el); filled.push('UPSPW'); }
    }

    let msg = `Loaded from Inventory Request #${rid}`;
    if (filled.length) { msg += `: ${filled.join(', ')}.`; } else { msg += ' (no POG data found).'; }
    if (isoPipe)       { msg += ` ISO Pipeline: ${Math.round(isoPipe).toLocaleString()} units.`; }
    setMsg(msg, '#1b5e20');
  } catch (e) {
    setMsg('Lookup failed: ' + (e.message || e), '#c00');
  }
}
window.lookupInvRequest = lookupInvRequest;

// Client-side ordered-units WoW line for non-POS records.  The multi-window
// L4/L13/L26/L52 "order rate" panel was REMOVED 2026-05-08 per planner
// feedback  -  it used non-zero averaging which contradicted the smart Order
// trend % (all-weeks averaging) on burst-pattern accounts.  The smart trend
// already references L26/L52 windows when they're informative.
function _buildOrderedUnitsWow(histOrd, custLabel) {
  if (!histOrd || histOrd.length < 2) return '';
  const ordLw = Number(histOrd[histOrd.length - 1] || 0);
  const ordPw = Number(histOrd[histOrd.length - 2] || 0);
  if (ordLw === 0 && ordPw === 0) return '';
  let wowStr;
  if (ordPw > 0) {
    const wow = ((ordLw - ordPw) / ordPw) * 100.0;
    wowStr = (wow >= 0 ? '+' : '') + wow.toFixed(0) + '% WoW';
  } else if (ordLw > 0) {
    wowStr = 'n/a (prior wk = 0)';
  } else {
    wowStr = 'n/a';
  }
  return `<b>${custLabel} ordered units:</b> LW ${ordLw.toLocaleString()}, ` +
         `Prior Wk ${ordPw.toLocaleString()} (&#x0394; ${wowStr}).`;
}

// Smart Order-trend insight  -  mirrors _smart_order_trend in
// scripts/inventory_forecaster.py.  2-sentence, data-backed: picks the
// FIRST matching discriminator from a priority-ordered list (gap-week,
// per-order qty shrinkage, cadence drop, multi-quarter softening, YoY
// momentum, burst rebound, sustained quiet, fallback) so the sentence is
// specific to this record's pattern rather than generic seasonality
// boilerplate.  Returns "" when too flat for a meaningful insight.
function _buildOrderTrendInsight(histOrd, lyOrd, custLabel) {
  if (!histOrd || histOrd.length < 4) return '';
  const cl = custLabel || 'this account';
  const h = histOrd.map(v => Number(v) || 0);
  const l4  = h.slice(-4);
  const l13 = h.length >= 13 ? h.slice(-13) : h.slice();
  const l26 = h.length >= 26 ? h.slice(-26) : h.slice();
  const sum = a => a.reduce((x,y)=>x+y, 0);
  const l4_avg  = sum(l4)  / 4.0;
  const l13_avg = sum(l13) / 13.0;
  const l26_avg = l26.length ? sum(l26) / l26.length : 0;
  if (l13_avg <= 0) return '';
  const short_pct = (l4_avg / l13_avg - 1.0) * 100;
  if (Math.abs(short_pct) < 10) return '';
  const l13_nz = l13.filter(v => v > 0);
  const l4_nz  = l4.filter(v => v > 0);
  const per_l13 = l13_nz.length ? sum(l13_nz) / l13_nz.length : 0;
  const per_l4  = l4_nz.length  ? sum(l4_nz)  / l4_nz.length  : 0;
  const freq_l13 = l13_nz.length / 13.0;
  const freq_l4  = l4_nz.length  / 4.0;
  const lw = h[h.length - 1];
  const pw = h.length >= 2 ? h[h.length - 2] : 0;
  const medium_flat = (Math.abs(l26_avg - l13_avg) / Math.max(l13_avg, 1)) < 0.15;
  let l52_avg = null;
  if (lyOrd && lyOrd.length >= 13) {
    const ly = lyOrd.map(v => Number(v) || 0);
    const full52 = ly.concat(l26);
    if (full52.length >= 40) l52_avg = sum(full52) / full52.length;
  }
  const direction = short_pct > 0 ? 'up' : 'down';
  const arrow = short_pct > 0
    ? '<span style="color:#2e7d32;font-weight:700">&#x25B2;</span>'
    : '<span style="color:#c62828;font-weight:700">&#x25BC;</span>';
  const header = `<b>${cl} Order History:</b> ${arrow} ${direction} ` +
                 `${Math.abs(short_pct).toFixed(0)}% L4W (${l4_avg.toFixed(0)}/wk) ` +
                 `vs L13W (${l13_avg.toFixed(0)}/wk).`;
  let expl;
  if (short_pct < 0 && lw === 0 && pw > 0 && per_l13 > 0 &&
      pw <= per_l13 * 1.6 && medium_flat && l4_nz.length >= 1) {
    expl = `Looks like a gap week, not a step-change  -  LW was 0 right after a normal ` +
           `${pw.toFixed(0)}u order, and the L26W rate (${l26_avg.toFixed(0)}/wk) still ` +
           `tracks L13W. ${cl} orders in bursts here, so a single quiet week is normal ` +
           `cadence. Watch the next 2-3 weeks; if no order lands, that's the real signal.`;
  } else if (short_pct < 0 && per_l13 > 0 && per_l4 > 0 &&
             per_l4 / per_l13 <= 0.80 &&
             Math.abs(freq_l4 - freq_l13) / Math.max(freq_l13, 0.01) < 0.30) {
    expl = `Per-order qty dropped from ~${per_l13.toFixed(0)}u (L13W) to ` +
           `~${per_l4.toFixed(0)}u (L4W) while reorder cadence held steady. Smaller POs ` +
           `at the same frequency usually means ${cl} trimmed distribution (lost a few ` +
           `stores), shifted to tighter JIT, or downsized the per-store build  -  worth a ` +
           `quick sales-rep check.`;
  } else if (short_pct < 0 && per_l13 > 0 && per_l4 > 0 &&
             per_l4 / per_l13 >= 0.85 && per_l4 / per_l13 <= 1.20 &&
             freq_l4 < freq_l13 * 0.70) {
    expl = `Fewer orders at the same per-PO size (~${per_l4.toFixed(0)}u). L4 had ` +
           `${l4_nz.length} order(s) vs the typical ${l13_nz.length}/13W cadence. ` +
           `Slower reorders with stable order qty usually means slower turn at retail  -  ` +
           `POS softening, or ${cl} sitting on inventory longer than usual.`;
  } else if (short_pct < 0 && l52_avg && l52_avg > 0 && l26_avg < l52_avg * 0.85) {
    const yoy_pct = (l26_avg / l52_avg - 1.0) * 100;
    expl = `L26W (${l26_avg.toFixed(0)}/wk) is ${yoy_pct >= 0 ? '+' : ''}${yoy_pct.toFixed(0)}% ` +
           `vs L52W (${l52_avg.toFixed(0)}/wk)  -  this isn't a 4-week dip, it's been cooling ` +
           `across multiple quarters at ${cl}. Pattern usually means real demand softening ` +
           `(category contraction, distribution loss) rather than seasonal.`;
  } else if (short_pct > 0 && l52_avg && l52_avg > 0 && l26_avg > l52_avg * 1.10) {
    const yoy_pct = (l26_avg / l52_avg - 1.0) * 100;
    expl = `L26W (${l26_avg.toFixed(0)}/wk) is +${yoy_pct.toFixed(0)}% vs L52W ` +
           `(${l52_avg.toFixed(0)}/wk)  -  momentum has been building at ${cl} across ` +
           `multiple quarters, not a 1-off bump. Plan for the pace to hold or build into ` +
           `Q4 unless POS turns.`;
  } else if (short_pct > 0 && per_l13 > 0 && per_l4 > 0 &&
             per_l4 / per_l13 >= 1.20 &&
             Math.abs(freq_l4 - freq_l13) / Math.max(freq_l13, 0.01) < 0.30) {
    expl = `Per-order qty grew from ~${per_l13.toFixed(0)}u to ~${per_l4.toFixed(0)}u while ` +
           `reorder cadence held steady. Bigger POs at the same rate usually means ${cl} ` +
           `consolidated touchpoints (multi-store builds, fewer ad-hoc replens) or picked ` +
           `up distribution gains.`;
  } else if (short_pct > 0 && lw > 0 && pw === 0 && freq_l13 > 0) {
    expl = `Activity restarting at ${cl}  -  LW ${lw.toFixed(0)}u after a Prior Wk zero. ` +
           `Their typical cadence is ${l13_nz.length} orders/13W, so watch the next 2-3 ` +
           `weeks to see whether they're getting back to baseline or this was a one-off catch-up.`;
  } else if (lw === 0 && pw === 0 && short_pct < 0) {
    expl = `Two consecutive zero weeks at ${cl}. Their L13W cadence ran ` +
           `${l13_nz.length}/13W active, so two zeros in a row is unusual. Could be a ` +
           `stockout on their end, an EDI hiccup, or a real pause in ordering  -  worth a ` +
           `quick check before assuming the account has gone quiet.`;
  } else {
    if (short_pct > 0) {
      expl = `L26W (${l26_avg.toFixed(0)}/wk) still tracks L13W (${l13_avg.toFixed(0)}/wk), ` +
             `so the recent uptick at ${cl} is fresh in the last 4 weeks. Could be a single ` +
             `larger PO, a feature/end-cap, or a retail promo  -  watch the next 2-3 weeks to ` +
             `see whether it sticks.`;
    } else if (medium_flat) {
      expl = `L26W (${l26_avg.toFixed(0)}/wk) ~ L13W (${l13_avg.toFixed(0)}/wk), so ` +
             `${cl}'s medium-term run rate is flat and the recent dip looks like normal ` +
             `cadence variance over a short window. No action unless it persists.`;
    } else {
      expl = `L26W (${l26_avg.toFixed(0)}/wk) and L13W (${l13_avg.toFixed(0)}/wk) are both ` +
             `off baseline  -  this is a broader cooling pattern at ${cl}, not just last 4 ` +
             `weeks. Worth checking POS or distribution for what changed.`;
    }
  }
  return `${header} ${expl}`;
}

function adaptRow(row) {
  const F = CFG.FID;
  const forecast = CFG.AI_PRJ_FIDS.map(fid => num(row, fid));
  const manual   = MAN_PRJ_FIDS  .map(fid => num(row, fid));
  const sug      = CFG.SUG_FIDS  .map(fid => num(row, fid));
  const opn      = CFG.OPN_FIDS  .map(fid => num(row, fid));
  const histOrd  = ORD_HIST_FIDS .map(fid => num(row, fid));
  const histShp  = SHP_HIST_FIDS .map(fid => num(row, fid));
  // LY actuals  -  26 elements W1..W26, aligned to forecast weeks (52 wk shift).
  const lyOrd    = LY_ORD_HIST_FIDS.map(fid => num(row, fid));
  const lyShp    = LY_SHP_HIST_FIDS.map(fid => num(row, fid));
  // DI Ord History (FID 1613): comma-separated L26W DI weekly order quantities.
  // Written by F69 in the forecaster; empty string when no DI orders exist.
  const _diRawAdapt = str(row, CFG.FID.DI_ORD_HIST) || '';
  const diOrd = _diRawAdapt
    ? _diRawAdapt.split(',').map(v => parseInt(v, 10) || 0)
    : [];

  // L4W per-week order rate from the most recent 4 weeks of histOrd
  // (histOrd is oldest>newest, so .slice(-4) is Ord LW-3..Ord LW).
  const _l4 = histOrd.slice(-4);
  const ord_per_wk_l4 = _l4.length ? Math.round((_l4.reduce((a,b)=>a+b,0) / 4) * 10) / 10 : 0;

  // Shpd/Wk L4W and L13W  -  actual shipped units from ship history
  const _shp4       = histShp.slice(-4);
  const shpd_wk_l4  = _shp4.length  ? Math.round((_shp4.reduce((a,b)=>a+b,0)  /  4) * 10) / 10 : 0;
  const _shp13      = histShp.slice(-13);
  const shpd_wk_l13 = _shp13.length ? Math.round((_shp13.reduce((a,b)=>a+b,0) / 13) * 10) / 10 : 0;

  // Last Ord Date: date of most recent week with a non-zero order, formatted MM/DD.
  // histOrd is oldest->newest so the most recent week is at the end. We walk
  // backwards until we find a non-zero entry, then compute its calendar date
  // by going (histOrd.length - 1 - i) full weeks before last Monday.
  let last_ord_date = '';
  for (let _i = histOrd.length - 1; _i >= 0; _i--) {
    if (histOrd[_i] > 0) {
      const _weeksBack = histOrd.length - 1 - _i;   // 0 = last wk, 1 = 2 wks ago, ...
      const _d = new Date();
      const _dow = _d.getDay();   // 0=Sun, 1=Mon, ...
      // Snap back to last Monday, then subtract extra weeks
      _d.setDate(_d.getDate() - (_dow === 0 ? 6 : _dow - 1) - 7 - _weeksBack * 7);
      last_ord_date = `${String(_d.getMonth()+1).padStart(2,'0')}/${String(_d.getDate()).padStart(2,'0')}`;
      break;
    }
  }

  const ai_total     = forecast.reduce((a,b) => a+b, 0);
  const manual_total = manual.reduce((a,b) => a+b, 0);
  const opn_total    = opn.reduce((a,b) => a+b, 0);   // open PO units (zeroed-out wks counted here)
  const ai_per_wk    = (ai_total + opn_total) / 26;   // effective rate including open POs
  const proj_per_wk  = (manual_total + opn_total) / 26;
  // Ord/Wk L13W: numeric formula field "Ord /Wk L13w #" (fid 1593).  The
  // older fid 313 is a rich-text version of the same metric  -  don't use
  // that one, it can't be parsed back into a number reliably.
  const ord_l13      = num(row, F.ORD_WK_L13);
  const pct_diff     = manual_total > 0 ? ((ai_total - manual_total) / manual_total) * 100
                     : (ai_total > 0 ? null : 0);  // null = no plan entered; 0 = both zero

  const pct_abs = pct_diff !== null ? Math.abs(pct_diff) : null;
  // Priority: On-Plan when both are zero OR when AI vs Plan gap is within 7.5%.
  // When manual=0 and AI>0, pct_diff is null (no plan entered) -- tier by AI volume, NOT On-Plan.
  const _both_zero = manual_total === 0 && ai_total === 0;
  let priority;
  if (_both_zero || (pct_diff !== null && pct_abs <= 7.5)) {
    priority = 'On-Plan';
  } else if (ai_per_wk >= 1000) {
    priority = 'CRITICAL';
  } else if (ai_per_wk >= 500) {
    priority = 'HIGH';
  } else if (ai_per_wk >= 200) {
    priority = 'MID';
  } else {
    priority = 'LOW';
  }
  const vol_tier = ai_per_wk >= 1000 ? 'HIGH' : ai_per_wk >= 500 ? 'HIGH' : ai_per_wk >= 200 ? 'MEDIUM' : 'LOW';

  // Seasonal customers (A: Promo, A: OffPrice) order 1-3x per year — zero-weeks
  // between order events are normal and must not generate ALERT flags.
  const is_seasonal = /^A:\s*(Promo|OffPrice)\b/i.test(str(row, F.STATUS_CUST));
  // Off-price is a subset of seasonal: POs ship same/next week so we widen the
  // PO/PRJ conflict window to 4 weeks for this account type.
  const is_offprice = /^A:\s*OffPrice\b/i.test(str(row, F.STATUS_CUST));

  // Per-week severity.  For seasonal records: only alert when we project an order
  // (m > 0) that significantly diverges from AI — not just because a week is 0.
  const weeks_slim = [];
  let any_alert = false;
  for (let i = 0; i < 26; i++) {
    const m = manual[i] || 0;
    const a = forecast[i] || 0;
    let sev = 'OK';
    if (is_seasonal) {
      // Alert only when we project orders but AI strongly disagrees
      if (m > 0 && a === 0) { sev = 'ALERT'; any_alert = true; }
      else if (m > 0 && a > 0 && (a / m > 3 || m / Math.max(a, 1) > 3)) { sev = 'ALERT'; any_alert = true; }
    } else {
      if ((m === 0 && a > 0) || (a === 0 && m > 0)) { sev = 'ALERT'; any_alert = true; }
      else if (m > 0 && (a / m > 3 || m / Math.max(a, 1) > 3)) { sev = 'ALERT'; any_alert = true; }
    }
    weeks_slim.push({ week: i + 1, projection: m, severity: sev });
  }

  const ai_vs_l13  = ord_l13 > 0 ? ((ai_per_wk   - ord_l13) / ord_l13 * 100) : 0;
  const man_vs_l13 = ord_l13 > 0 ? ((proj_per_wk - ord_l13) / ord_l13 * 100) : 0;

  // PO / Manual Projection overlap detection
  const { conflicts: po_prj_conflicts, hasConflict: has_po_prj_conflict } =
    _computePoPrjConflicts(opn, manual, is_offprice);

  // Build narrative.  Primary source is QB AI_ANALYSIS / AI_ALERT (rich text
  // written by scripts/inventory_forecaster.py during the last forecast run).
  // We then *augment* with a client-side order-trend insight to keep the
  // codepage in sync with viewer.py  -  useful when a record has stale
  // narrative or when the planner is reviewing it before the next forecast
  // run lands.  The append is idempotent: skipped if the narrative already
  // contains a "Sales trend" or "Order trend" line.
  let narrative = str(row, F.AI_ANALYSIS) || str(row, F.AI_ALERT);
  const _custLabel = _friendlyCustName(_stripHtml(str(row, F.CUST)));
  // Append order history trend insight if not already in the stored narrative.
  // Idempotent -- skipped if AI_ANALYSIS already contains any of the known
  // header variants (Order History, Order trend, Order Trends, Sales trend).
  const orderTrend = _buildOrderTrendInsight(histOrd, lyOrd, _custLabel);
  if (orderTrend &&
      narrative.indexOf('Order History:') === -1 &&
      narrative.indexOf('Order trend:') === -1 &&
      narrative.indexOf('Order Trends:') === -1 &&
      narrative.indexOf('Sales trend:') === -1) {
    const sep = narrative ? '<br><br>' : '';
    narrative = narrative + sep + orderTrend;
  }

  return {
    key:               str(row, F.KEY),
    mstyle:            str(row, F.MSTYLE),
    cust_sku:          str(row, F.CUST_SKU),
    cust:              _stripHtml(str(row, F.CUST)),
    desc:              str(row, F.DESCRIPTION),
    asin_status:       str(row, F.STATUS_CUST),
    is_seasonal:       is_seasonal,
    is_offprice:       is_offprice,
    has_po_prj_conflict: has_po_prj_conflict,
    po_prj_conflicts:  po_prj_conflicts,
    item_status:       str(row, F.ITEM_STATUS),
    inv_manager:       str(row, F.INV_MGR_NAME),
    inv_manager_email: ((row[F.INV_MGR_USER] && row[F.INV_MGR_USER].value && row[F.INV_MGR_USER].value.email) || ''),
    brand:             str(row, F.BRAND_NAME),
    pattern:           _parseModel(str(row, F.AI_ALERT) || str(row, F.AI_ANALYSIS)),
    ai_model:          _parseModel(str(row, F.AI_ALERT) || str(row, F.AI_ANALYSIS)),
    fcst_status:       _fcstStatus(_parseModel(str(row, F.AI_ALERT) || str(row, F.AI_ANALYSIS)), ai_total, manual_total),
    biweekly:          false,
    proj_wk:           Math.round(proj_per_wk * 10) / 10,
    shp_wk:            ord_l13,
    ord_wk_l4:         ord_per_wk_l4,
    shpd_wk_l4:        shpd_wk_l4,
    shpd_wk_l13:       shpd_wk_l13,
    shpd_wk:           shpd_wk_l13,      // legacy alias kept for CSV export
    last_ord_date:     last_ord_date,
    ai_fcst:           forecast,
    ai_total:          ai_total,
    ai_wk:             Math.round(ai_per_wk * 10) / 10,
    narrative:         narrative,
    max_sev:           any_alert ? 'ALERT' : 'OK',
    priority:          priority,
    vol_tier:          vol_tier,
    n_flags:           weeks_slim.filter(w => w.severity !== 'OK').length,
    proj_total:        manual_total,
    pct_diff:          pct_diff,
    ai_vs_l13:         Math.round(ai_vs_l13  * 10) / 10,
    man_vs_l13:        Math.round(man_vs_l13 * 10) / 10,
    weeks_slim:        weeks_slim,
    suggested:         sug,
    sugg_total:        sug.reduce((a,b) => a + b, 0),
    sugg_wk:           Math.round((sug.reduce((a,b) => a + b, 0) / 26) * 10) / 10,
    hist_shp:          histShp,
    hist_ord:          histOrd,
    ly_ord:            lyOrd,
    ly_shp:            lyShp,
    di_ord:            diOrd,
    last_comment:          str(row, F.LAST_COMMENT),
    last_comment_date:     str(row, F.LAST_COMMENT_DATE),
    flagged:               bool(row, F.FLAGGED),
    planner_reply_pending: bool(row, F.PLANNER_REPLY_PENDING),
    manager_reply_pending: F.MANAGER_REPLY_PENDING ? bool(row, F.MANAGER_REPLY_PENDING) : false,
    master_pack:       num(row, F.MASTER_PACK) || 1,
    // POG / ISO context (added 2026-05-10)  -  used by the Inventory Plan
    // block in the detail panel.  inv_flow_wk attached separately after
    // attachInvFlow() runs.
    store_count:       num(row, F.STORE_COUNT),
    est_iso_qty:       num(row, F.EST_ISO_QTY),
    est_iso_input:     F.EST_ISO_INPUT ? num(row, F.EST_ISO_INPUT) : 0,
    init_upspw:        F.INIT_UPSPW    ? num(row, F.INIT_UPSPW)    : 0,
    pog_launch:        str(row, F.POG_LAUNCH),
    pog_end:           str(row, F.POG_END),
    iso_ship_date:     str(row, F.ISO_SHIP_DATE),
    next_rcpt_dt:      str(row, F.NEXT_RCPT_DT),  // FID 861 — Next Receipt Date
    season_tag:        F.SEASON ? str(row, F.SEASON) : '',  // FID 1583 — Season (e.g. "Holiday")
    auto_project:        F.AUTO_PROJECT         ? bool(row, F.AUTO_PROJECT)         : false,
    switchover_active:   F.SWITCHOVER_ACTIVE    ? bool(row, F.SWITCHOVER_ACTIVE)    : false,
    switchover_to_mstyle:F.SWITCHOVER_TO_MSTYLE ? str(row, F.SWITCHOVER_TO_MSTYLE)  : '',
    switchover_date:     F.SWITCHOVER_DATE      ? str(row, F.SWITCHOVER_DATE)        : '',
    opn_w:             opn,           // [Opn_W1..Opn_W26] open customer PO quantities
    opn_total:         opn_total,    // sum of opn_w; used in proj_wk/ai_wk recalcs
    inv_flow_beg:        null,        // [Wk1..Wk26] beginning balances
    inv_flow_rcv:        null,        // [RcvWk1..RcvWk26] expected receipts
    inv_flow_prj:        null,        // [Prj Wk1..Wk26] projected demand draw
    inv_flow_opn:        null,        // [26] open orders: W1=Wk0+Wk1, W2..W26=Wk2..Wk26
    inv_flow_opt_wos:    0,           // numeric  -  min weeks of supply target
    inv_flow_next_rcpt:  '',          // ISO date  -  when next supplier receipt arrives
    inv_flow_lt_wks:     0,           // numeric  -  lead time in weeks
    inv_flow_moq:        0,           // numeric  -  minimum order quantity
    inv_flow_supp_pos:   '',          // text  -  Open_Supplier_POs (raw multi-line)
    inv_flow_ats_now:    0,           // numeric  -  ATS_Now
    inv_flow_ats_oh:     0,           // numeric  -  ATS_OH_
    inv_flow_ats_oo:     0,           // numeric  -  ATS_OH_OO_
    inv_flow_ats_oh_wos: 0,           // numeric  -  ATS_WOS_OH_
    inv_flow_ats_oo_wos: 0,           // numeric  -  ATS_WOS_OH_OO_
    ats_hist:            null,        // [26] ATS inv history oldest->newest (LW-25..LW)
  };
}

// -- Auto-populate every dropdown from real values present in ALL_RECORDS --
// -- Multi-select dropdown widget ---------------------------------------------
//
// All filters except aiDiffFilter (a numeric threshold range) are checkbox-
// dropdowns: click the button > panel opens with searchable checkboxes >
// pick any combination > applyFilters() runs on each toggle.  The widget is
// mounted into a wrapper <div class="ms" id="...">; the original <select>
// elements are replaced in viewer.html.
//
// Each created widget exposes:
//   wrap._getSelected()    -> Set of checked values  (empty Set == "All")
//   wrap._setSelection([]) -> programmatic check (used by filterVol badges)
//   wrap._clearSelection() -> reset for the global Clear Filters button
function createMultiSelect(id, options, sortFn) {
  const wrap = document.getElementById(id);
  if (!wrap) return null;
  const allLabel = wrap.dataset.allLabel || 'All';
  wrap.classList.add('ms');
  wrap.innerHTML = '';

  const btn = document.createElement('button');
  btn.type = 'button';
  btn.className = 'ms-btn';
  btn.textContent = allLabel;
  btn.title = allLabel;
  wrap.appendChild(btn);

  const panel = document.createElement('div');
  panel.className = 'ms-panel';

  const search = document.createElement('input');
  search.type = 'text';
  search.className = 'ms-search';
  search.placeholder = 'Filter...';   // ASCII dots  -  avoid charset issues with U+2026
  panel.appendChild(search);

  const actions = document.createElement('div');
  actions.className = 'ms-actions';
  const allBtn = document.createElement('button');
  allBtn.type = 'button';
  allBtn.textContent = 'Select all';
  const clrBtn = document.createElement('button');
  clrBtn.type = 'button';
  clrBtn.textContent = 'Clear';
  actions.appendChild(allBtn);
  actions.appendChild(clrBtn);
  panel.appendChild(actions);

  const list = document.createElement('div');
  panel.appendChild(list);
  wrap.appendChild(panel);

  // Each option is either a plain string (value === label) or
  // {value, label, tooltip}.  The label is what the user sees in the menu;
  // the optional tooltip becomes the row's hover title (used for Volume /
  // Priority threshold definitions so the menu itself stays clean).
  const norm = v => (v && typeof v === 'object') ? v : { value: v, label: v };
  const arr = [...options].map(norm).filter(o => o.value != null && o.value !== '');
  arr.sort(sortFn || ((a, b) => String(a.label).localeCompare(String(b.label))));
  arr.forEach(o => {
    const lab = document.createElement('label');
    lab.className = 'ms-opt';
    if (o.tooltip) lab.title = o.tooltip;
    const cb = document.createElement('input');
    cb.type = 'checkbox';
    cb.value = o.value;
    cb.addEventListener('change', () => { update(); window.applyFilters(); });
    const txt = document.createElement('span');
    txt.textContent = o.label;
    // Mirror the tooltip onto the inner span so hover registers whether
    // the cursor is over the row, the checkbox, or the label text.
    if (o.tooltip) txt.title = o.tooltip;
    lab.appendChild(cb);
    lab.appendChild(txt);
    list.appendChild(lab);
  });

  function update() {
    const sels = list.querySelectorAll('input:checked');
    if (sels.length === 0)      { btn.textContent = allLabel; btn.title = allLabel; btn.classList.remove('has-sel'); }
    else if (sels.length === 1) { btn.textContent = sels[0].value; btn.title = sels[0].value; btn.classList.add('has-sel'); }
    else {
      const labels = [...sels].map(s => s.value).join(', ');
      btn.textContent = `${sels.length} selected`;
      btn.title = labels;
      btn.classList.add('has-sel');
    }
  }
  // Position the panel relative to the trigger button each time it opens.
  // Panel is position:fixed (escapes the toolbar's overflow-x:auto clipping
  // rectangle), so we set top/left from the button's viewport coordinates.
  function positionPanel() {
    const r = btn.getBoundingClientRect();
    panel.style.top      = (r.bottom + 2) + 'px';
    panel.style.left     = r.left + 'px';
    panel.style.minWidth = r.width + 'px';
  }
  btn.addEventListener('click', e => {
    e.stopPropagation();
    document.querySelectorAll('.ms.open').forEach(o => { if (o !== wrap) o.classList.remove('open'); });
    const willOpen = !wrap.classList.contains('open');
    if (willOpen) positionPanel();
    wrap.classList.toggle('open');
    if (wrap.classList.contains('open')) { search.focus(); }
  });
  panel.addEventListener('click', e => e.stopPropagation());
  search.addEventListener('input', () => {
    const q = search.value.toLowerCase();
    list.querySelectorAll('.ms-opt').forEach(opt => {
      const t = opt.querySelector('span').textContent.toLowerCase();
      opt.style.display = t.includes(q) ? '' : 'none';
    });
  });
  allBtn.addEventListener('click', () => {
    list.querySelectorAll('.ms-opt').forEach(opt => {
      if (opt.style.display !== 'none') opt.querySelector('input').checked = true;
    });
    update(); window.applyFilters();
  });
  clrBtn.addEventListener('click', () => {
    list.querySelectorAll('input').forEach(cb => { cb.checked = false; });
    update(); window.applyFilters();
  });

  wrap._getSelected = function () {
    const out = new Set();
    list.querySelectorAll('input:checked').forEach(cb => out.add(cb.value));
    return out;
  };
  wrap._setSelection = function (values) {
    const want = new Set(values || []);
    list.querySelectorAll('input').forEach(cb => { cb.checked = want.has(cb.value); });
    update();
  };
  wrap._clearSelection = function () {
    list.querySelectorAll('input').forEach(cb => { cb.checked = false; });
    search.value = '';
    list.querySelectorAll('.ms-opt').forEach(opt => { opt.style.display = ''; });
    update();
  };
  return wrap;
}

// Close any open multi-select panel when the user clicks outside it
document.addEventListener('click', () => {
  document.querySelectorAll('.ms.open').forEach(o => o.classList.remove('open'));
});
// Panels are position:fixed  -  close on PAGE scroll so they don't appear
// stranded over the report once the user scrolls the page or table.
// IMPORTANT: no capture-phase, and skip if the scroll happened inside the
// panel itself  -  otherwise users can't scroll the option list.
window.addEventListener('scroll', (e) => {
  if (e.target && e.target.nodeType === 1 && e.target.closest && e.target.closest('.ms-panel')) return;
  document.querySelectorAll('.ms.open').forEach(o => o.classList.remove('open'));
});

function populateFilters() {
  const sets = {
    brand:       new Set(),
    inv_manager: new Set(),
    cust:        new Set(),
    pattern:     new Set(),
    vol:         new Set(),
    pri:         new Set(),
    fcst_status: new Set(),
  };
  ALL_RECORDS.forEach(r => {
    if (r.brand)        sets.brand.add(r.brand);
    if (r.inv_manager)  sets.inv_manager.add(r.inv_manager);
    if (r.cust)         sets.cust.add(r.cust);
    if (r.pattern)      sets.pattern.add(r.pattern);
    if (r.vol_tier)     sets.vol.add(r.vol_tier);
    if (r.priority)     sets.pri.add(r.priority);
    if (r.fcst_status)  sets.fcst_status.add(r.fcst_status);
  });
  // Volume / priority have a natural rank  -  sort by it.  The dropdown shows
  // just the tier name; the threshold definition lives in a hover tooltip
  // so the menu reads cleanly at a glance.
  const VOL_TIPS = {
    HIGH:   'HIGH: AI forecast >= 1,000 units / week',
    MEDIUM: 'MEDIUM: AI forecast 200 - 999 units / week',
    LOW:    'LOW: AI forecast < 200 units / week',
  };
  const PRI_TIPS = {
    CRITICAL:  'CRITICAL: >= 1,000/wk AND AI vs Plan gap > 7.5%',
    HIGH:      'HIGH: 500-999/wk AND AI vs Plan gap > 7.5%',
    MID:       'MID: 200-499/wk AND AI vs Plan gap > 7.5%',
    LOW:       'LOW: < 200/wk AND AI vs Plan gap > 7.5%',
    'On-Plan': 'On-Plan: AI vs Plan within 7.5% (any volume)',
  };
  const volRank = ['HIGH','MEDIUM','LOW'];
  const priRank = ['CRITICAL','HIGH','MID','LOW','On-Plan'];
  const volOpts = [...sets.vol].map(v => ({ value: v, label: v, tooltip: VOL_TIPS[v] || '' }));
  const priOpts = [...sets.pri].map(v => ({ value: v, label: v, tooltip: PRI_TIPS[v] || '' }));
  const orderBy = ranks => (a, b) => ranks.indexOf(a.value) - ranks.indexOf(b.value);
  const FCST_STATUS_RANK = ['Over-Projected', 'Under-Projected', 'On Plan', 'Inactive'];
  const fcstStatusOpts = [...sets.fcst_status].map(v => ({ value: v, label: v }));
  createMultiSelect('volFilter',        volOpts,         orderBy(volRank));
  createMultiSelect('priFilter',        priOpts,         orderBy(priRank));
  createMultiSelect('patFilter',        sets.pattern);
  createMultiSelect('brandFilter',      sets.brand);
  createMultiSelect('mgrFilter',        sets.inv_manager);
  createMultiSelect('custFilter',       sets.cust);
  createMultiSelect('fcstStatusFilter', fcstStatusOpts,  orderBy(FCST_STATUS_RANK));
}

function _sortKey(v) {
  const s = (v == null ? '' : String(v)).trim();
  return s === '' ? '\uffff' : s.toLowerCase();  // '\uffff' = sort-last sentinel (XML 1.0 safe; literal U+FFFF breaks API_AddReplaceDBPage)
}

// -- Header badge counts -----------------------------------------------------
function refreshHeaderBadges() {
  const total = ALL_RECORDS.length;
  let high = 0, med = 0, low = 0;
  let priCrit = 0, priHigh = 0, priMid = 0, priLow = 0, priOnPlan = 0;
  for (const r of ALL_RECORDS) {
    if (r.vol_tier === 'HIGH') high++;
    else if (r.vol_tier === 'MEDIUM') med++;
    else low++;
    if      (r.priority === 'CRITICAL') priCrit++;
    else if (r.priority === 'HIGH')     priHigh++;
    else if (r.priority === 'MID')      priMid++;
    else if (r.priority === 'On-Plan')  priOnPlan++;
    else                                priLow++;
  }
  document.getElementById('badge-total-n').textContent = total.toLocaleString();
  document.getElementById('badge-high-n' ).textContent = high.toLocaleString();
  document.getElementById('badge-med-n'  ).textContent = med.toLocaleString();
  document.getElementById('badge-low-n'  ).textContent = low.toLocaleString();
  const pc  = document.getElementById('badge-pri-crit-n');
  const ph  = document.getElementById('badge-pri-high-n');
  const pm  = document.getElementById('badge-pri-mid-n');
  const pl  = document.getElementById('badge-pri-low-n');
  const pop = document.getElementById('badge-pri-onplan-n');
  if (pc)  pc.textContent  = priCrit.toLocaleString();
  if (ph)  ph.textContent  = priHigh.toLocaleString();
  if (pm)  pm.textContent  = priMid.toLocaleString();
  if (pl)  pl.textContent  = priLow.toLocaleString();
  if (pop) pop.textContent = priOnPlan.toLocaleString();
}

// -- Display helpers --------------------------------------------------------
function fmtN(n) { return n == null ? '-' : Number(n).toLocaleString(); }
function escHtml(s) { return String(s == null ? '' : s).replace(/[<>&]/g, c => ({'<':'&lt;','>':'&gt;','&':'&amp;'})[c]); }
function priLabel(p) {
  if (p === 'CRITICAL') return '<span class="pri-crit">CRITICAL</span>';
  if (p === 'HIGH')     return '<span class="pri-med">HIGH</span>';
  if (p === 'MID')      return '<span class="pri-med">MID</span>';
  if (p === 'On-Plan')  return '<span class="pri-low" style="color:#2e7d32">On-Plan</span>';
  return '<span class="pri-low">LOW</span>';
}

// -- Snooze helpers (localStorage, 48-hr expiry) ----------------------------
function _getSnoozes() {
  try {
    const s = JSON.parse(localStorage.getItem('viewerSnoozes') || '{}');
    const now = Date.now();
    let pruned = false;
    Object.keys(s).forEach(k => { if (s[k] <= now) { delete s[k]; pruned = true; } });
    if (pruned) localStorage.setItem('viewerSnoozes', JSON.stringify(s));
    return s;
  } catch(_) { return {}; }
}
function _isSnooze(key) { const s = _getSnoozes(); return !!(s[key] && s[key] > Date.now()); }
function _initSnoozeFlags() {
  const s = _getSnoozes();
  const now = Date.now();
  ALL_RECORDS.forEach(r => { r._snoozed = !!(s[r.key] && s[r.key] > now); });
}
function snooze48(key) {
  const s = _getSnoozes();
  s[key] = Date.now() + 48 * 60 * 60 * 1000;
  try { localStorage.setItem('viewerSnoozes', JSON.stringify(s)); } catch(_) {}
  const rec = ALL_RECORDS.find(r => r.key === key);
  if (rec) rec._snoozed = true;
  applyFilters();
}
function unsnooze(key) {
  const s = _getSnoozes();
  delete s[key];
  try { localStorage.setItem('viewerSnoozes', JSON.stringify(s)); } catch(_) {}
  const rec = ALL_RECORDS.find(r => r.key === key);
  if (rec) rec._snoozed = false;
  applyFilters();
}
function _priCell(r) {
  if (r._snoozed) {
    const expMs  = (_getSnoozes()[r.key] || 0) - Date.now();
    const hrsRem = Math.max(0, expMs / 3600000).toFixed(1);
    const safeKey = r.key.replace(/'/g, "\\'");
    return `<span class="pri-snoozed" title="Snoozed - ${hrsRem}h remaining">SNOOZED</span>`
         + `<button class="snooze-btn" onclick="unsnooze('${safeKey}')" title="Remove snooze and restore original priority immediately" style="color:#1565c0;border-color:#1565c0;">UnSnooze</button>`;
  }
  // On-Plan records are already aligned -- no need to snooze them.
  if (r.priority === 'On-Plan') return priLabel(r.priority);
  const safeKey = r.key.replace(/'/g, "\\'");
  return `${priLabel(r.priority)}<button class="snooze-btn" onclick="snooze48('${safeKey}')" title="Snooze this item for 48 hours - priority will be ignored and badge will show as SNOOZED until the period expires">Snooze</button>`;
}

function borderClass(s) {
  if (s === 'CRITICAL') return 'border-crit';
  if (s === 'WARNING')  return 'border-warn';
  return 'border-ok';
}
function weekCellClass(sev) {
  if (sev === 'CRITICAL') return 'wk-crit';
  if (sev === 'WARNING')  return 'wk-warn';
  return 'wk-ok';
}
function weekLabel(i) {
  if (!W1_DATE) return '';
  const d = new Date(W1_DATE);
  d.setDate(d.getDate() + i * 7);
  return (d.getMonth()+1).toString().padStart(2,'0') + '/' + d.getDate().toString().padStart(2,'0');
}

// -- Toggle Flagged in QB ----------------------------------------------------
//
// Click on the [!] icon > POST /v1/records to flip the boolean checkbox field
// (CFG.FID.FLAGGED).  Optimistic UI: toggle the icon immediately; if QB call
// fails, revert + show error tooltip.
// -- Status @ Cust inline edit ----------------------------------------------
// Status_Cust column is editable on-the-fly: click the cell -> swap to <select>
// populated with all unique values seen in the loaded data + a "Custom..."
// escape hatch for entering arbitrary new statuses (e.g. FD MM/YY end-dates
// that don't exist yet in the corpus).  Save merges via the same /records
// upsert path as the flag toggle.

// Cache the dropdown choices so we only walk ALL_RECORDS once per session.
let _STATUS_CHOICES_CACHE = null;
function _statusChoices() {
  if (_STATUS_CHOICES_CACHE) return _STATUS_CHOICES_CACHE;
  const seen = new Map();  // value -> count
  (ALL_RECORDS || []).forEach(r => {
    const v = (r.asin_status || '').trim();
    if (!v) return;
    seen.set(v, (seen.get(v) || 0) + 1);
  });
  // Sort: most common first within each group; keep grouping (A* / FD* / NEW* / other)
  const arr = Array.from(seen.entries());
  const groupOf = v => v.toUpperCase().startsWith('A') ? 0
                     : v.toUpperCase().startsWith('FD') ? 1
                     : v.toUpperCase().startsWith('NEW') ? 2
                     : 3;
  arr.sort((a, b) => {
    const ga = groupOf(a[0]), gb = groupOf(b[0]);
    if (ga !== gb) return ga - gb;
    if (b[1] !== a[1]) return b[1] - a[1];  // higher count first
    return a[0].localeCompare(b[0]);
  });
  _STATUS_CHOICES_CACHE = arr.map(([v]) => v);
  return _STATUS_CHOICES_CACHE;
}

// Render a single status cell as a clickable text element.  Cell stays as
// plain text until the planner clicks it  -  then we swap in a <select>.
function _renderStatusCell(asin_status, key) {
  const safeKey = (key || '').replace(/'/g, "&#39;");
  const safeStatus = (asin_status || '').replace(/"/g, '&quot;');
  const display = asin_status || ' ';  // nbsp keeps cell clickable when empty
  return `<td class="status-cust-cell" data-key="${safeKey}"
              onclick="editStatusCust('${safeKey}', this)"
              title="Click to change Status @ Cust"
              style="font-size:11px;white-space:nowrap;cursor:pointer;
                     padding:2px 6px;border-bottom:1px dashed transparent;"
              onmouseover="this.style.borderBottomColor='#1565c0'"
              onmouseout="this.style.borderBottomColor='transparent'">${display}</td>`;
}

// Swap the static cell into an inline <select> when the planner clicks.
function editStatusCust(key, cellEl) {
  // Avoid swapping when the cell already has a <select> open.
  if (cellEl.querySelector('select')) return;
  const rec = ALL_RECORDS.find(x => x.key === key);
  if (!rec) return;
  const current = rec.asin_status || '';
  const choices = _statusChoices().slice();
  // Ensure the current value is present even if we somehow missed it.
  if (current && !choices.includes(current)) choices.unshift(current);
  // Build options (current value selected; trailing "Custom..." escape hatch).
  let opts = '';
  for (const c of choices) {
    const sel = c === current ? ' selected' : '';
    const safe = c.replace(/"/g, '&quot;');
    opts += `<option value="${safe}"${sel}>${safe}</option>`;
  }
  opts += `<option value="__CUSTOM__" style="font-style:italic;color:#1565c0">+ Custom value...</option>`;
  // Replace cell content with the select; bind change + blur.
  cellEl.innerHTML = `<select style="font-size:11px;padding:1px 3px;border:1px solid #1565c0;
                                     border-radius:3px;max-width:170px;">${opts}</select>`;
  const sel = cellEl.querySelector('select');
  sel.focus();
  sel.addEventListener('change', () => _commitStatusEdit(key, sel.value, cellEl));
  sel.addEventListener('blur',  () => {
    // Re-render the cell as text after a moment if no change happened
    setTimeout(() => {
      if (cellEl.querySelector('select')) {
        cellEl.innerHTML = (rec.asin_status || ' ');
      }
    }, 150);
  });
}

async function _commitStatusEdit(key, newValue, cellEl) {
  const rec = ALL_RECORDS.find(x => x.key === key);
  if (!rec) return;
  if (newValue === '__CUSTOM__') {
    const custom = window.prompt(
      'Enter custom Status @ Cust value (e.g. "FD 09/26"):',
      rec.asin_status || ''
    );
    if (custom == null) {
      // Cancelled  -  restore static cell
      cellEl.innerHTML = (rec.asin_status || ' ');
      return;
    }
    newValue = custom.trim();
  }
  if (newValue === rec.asin_status) {
    cellEl.innerHTML = (rec.asin_status || ' ');
    return;
  }
  const prev = rec.asin_status;
  // Optimistic UI
  rec.asin_status = newValue;
  cellEl.innerHTML = `<span style="color:#1565c0">${(newValue || ' ')}</span>`;
  // Invalidate the choices cache so a freshly-typed Custom value shows up
  // in any subsequently-opened dropdown without a page reload.
  _STATUS_CHOICES_CACHE = null;
  try {
    const fields = {};
    fields[CFG.FID.KEY]         = { value: key };
    fields[CFG.FID.STATUS_CUST] = { value: newValue };
    await qb('/records', {
      to: CFG.PROJECTIONS_TID,
      data: [fields],
      mergeFieldId: CFG.FID.KEY,
    });
    // Settle to plain text after success
    cellEl.innerHTML = (newValue || ' ');
    cellEl.title = 'Click to change Status @ Cust';
  } catch (e) {
    console.error('Status @ Cust save failed:', e);
    rec.asin_status = prev;
    cellEl.innerHTML = `<span style="color:#c62828" title="Save failed: ${(e.message||'').replace(/"/g,'&quot;')}">${prev || ' '} (!)</span>`;
  }
}

// -- Cust SKU# inline edit ---------------------------------------------------
// Click the Cust SKU# cell to swap it for a text input; commit on blur/Enter,
// cancel on Escape.  Writes FID 821 on Projections via mergeFieldId upsert.
function editCustSku(key, cellEl) {
  if (cellEl.querySelector('input')) return;  // already editing
  const rec = ALL_RECORDS.find(x => x.key === key);
  if (!rec) return;
  const current = rec.cust_sku || '';
  // Let the input overflow the cell without clipping
  cellEl.style.overflow  = 'visible';
  cellEl.style.position  = 'relative';
  const inp = document.createElement('input');
  inp.type = 'text';
  inp.value = current;
  inp.style.cssText = 'font-size:11px;padding:1px 4px;border:1px solid #1565c0;border-radius:3px;min-width:200px;max-width:360px;position:absolute;top:0;left:0;z-index:20;background:#fff;box-shadow:0 2px 6px rgba(0,0,0,0.15);';
  cellEl.innerHTML = '';
  cellEl.appendChild(inp);
  inp.focus();
  inp.select();
  const restore = () => { cellEl.style.overflow = 'hidden'; cellEl.style.position = ''; };
  const commit  = () => { restore(); _commitCustSkuEdit(key, inp.value.trim(), cellEl); };
  const cancel  = () => { restore(); cellEl.textContent = current; cellEl.title = current || 'Click to edit Cust SKU#'; };
  inp.addEventListener('blur', commit);
  inp.addEventListener('keydown', ev => {
    if (ev.key === 'Enter')  { ev.preventDefault(); inp.removeEventListener('blur', commit); commit(); }
    if (ev.key === 'Escape') { ev.preventDefault(); inp.removeEventListener('blur', commit); cancel(); }
  });
}

async function _commitCustSkuEdit(key, newValue, cellEl) {
  const rec = ALL_RECORDS.find(x => x.key === key);
  if (!rec) return;
  if (newValue === (rec.cust_sku || '')) { cellEl.textContent = newValue || ''; return; }
  const prev = rec.cust_sku || '';
  rec.cust_sku = newValue;
  cellEl.innerHTML = `<span style="color:#1565c0">${newValue || ''}</span>`;
  try {
    const fields = {};
    fields[CFG.FID.KEY]      = { value: key };
    fields[CFG.FID.CUST_SKU] = { value: newValue || null };
    await qb('/records', { to: CFG.PROJECTIONS_TID, data: [fields], mergeFieldId: CFG.FID.KEY });
    cellEl.textContent = newValue || '';
    cellEl.title = newValue || 'Click to edit Cust SKU#';
  } catch (e) {
    console.error('Cust SKU# save failed:', e);
    rec.cust_sku = prev;
    cellEl.title = prev || 'Click to edit Cust SKU#';
    cellEl.innerHTML = `<span style="color:#c62828" title="Save failed: ${(e.message||'').replace(/"/g,'&quot;')}">${prev || ''} (!)</span>`;
  }
}
window.editCustSku = editCustSku;

// Fires on the FIRST keystroke in the mgr-comment textarea.  If the row
// isn't already flagged, auto-flag it so the comment is visible to mgrs
// in the Show-Flagged-Only view.  Idempotent  -  `_auto_flagged` guard
// prevents repeated toggles.  QB write is deferred to addComment() so that
// merely typing (without saving) never creates a spurious QB flag.
function autoFlagOnComment(key) {
  const rec = ALL_RECORDS.find(x => x.key === key);
  if (!rec) return;
  const txt = document.getElementById('cmt-text-' + key);
  const isEmpty = !txt || !txt.value.trim();
  const safeId = key.replace(/[^a-zA-Z0-9]/g, '_');
  // FYI comments are informational — undo auto-flag when textarea is cleared OR FYI is checked
  const _fyiChkAuto = document.getElementById('cmt-fyi-' + key);
  const flagSel     = document.getElementById('cmt-flag-' + key);
  const _isFyi      = (_fyiChkAuto && _fyiChkAuto.checked) || (flagSel && flagSel.value === 'FYI');
  if (isEmpty || _isFyi) {
    // Comment cleared or marked FYI — undo the pre-flag (UI only; no QB call since we haven't
    // written to QB yet — that only happens on Save).
    if (rec._auto_flagged) {
      rec._auto_flagged = false;
      if (rec.flagged) {
        rec.flagged = false;
        const btn = document.getElementById('flg-' + safeId);
        if (btn) btn.className = 'flag-btn';
        const tr = document.querySelector(`tbody tr[data-key="${CSS.escape(key)}"]`);
        if (tr) tr.classList.remove('row-flagged');
        updateFlagCount();
      }
    }
    return;
  }
  if (rec.flagged) return;       // already flagged (QB or manual) — leave alone
  if (rec._auto_flagged) return; // already pre-flagged this session
  // Update UI immediately so the row tints and counter increments while typing.
  // QB write is deferred to addComment().
  rec._auto_flagged = true;
  rec.flagged = true;
  const btn = document.getElementById('flg-' + safeId);
  if (btn) btn.className = 'flag-btn flagged';
  const tr = document.querySelector(`tbody tr[data-key="${CSS.escape(key)}"]`);
  if (tr) tr.classList.add('row-flagged');
  updateFlagCount();
}

async function toggleFlag(key) {
  const rec = ALL_RECORDS.find(x => x.key === key);
  if (!rec) return;
  const newVal = !rec.flagged;
  // Optimistic UI
  rec.flagged = newVal;
  const safeId = key.replace(/[^a-zA-Z0-9]/g,'_');
  const btn = document.getElementById('flg-' + safeId);
  if (btn) btn.className = 'flag-btn' + (newVal ? ' flagged' : '');
  // Tint the parent row light-red (or remove tint) immediately
  const tr = document.querySelector(`tbody tr[data-key="${CSS.escape(key)}"]`);
  if (tr) tr.classList.toggle('row-flagged', newVal);
  updateFlagCount();
  try {
    const fields = {};
    fields[CFG.FID.KEY]     = { value: key };
    fields[CFG.FID.FLAGGED] = { value: newVal };
    await qb('/records', {
      to: CFG.PROJECTIONS_TID,
      data: [fields],
      mergeFieldId: CFG.FID.KEY,
    });
  } catch (e) {
    // Revert
    console.error('toggleFlag failed:', e);
    rec.flagged = !newVal;
    if (btn) {
      btn.className = 'flag-btn' + (rec.flagged ? ' flagged' : '');
      btn.title = 'Save failed: ' + e.message;
    }
    if (tr) tr.classList.toggle('row-flagged', rec.flagged);
    updateFlagCount();
  }
}

async function toggleAutoProject(key) {
  const rec = ALL_RECORDS.find(x => x.key === key);
  if (!rec || !CFG.FID.AUTO_PROJECT) return;
  const newVal = !rec.auto_project;
  // Optimistic UI
  rec.auto_project = newVal;
  const safeId = key.replace(/[^a-zA-Z0-9]/g,'_');
  const btn = document.getElementById('autoproj-' + safeId);
  if (btn) {
    btn.textContent = '\u{1F504} Auto Project' + (newVal ? ': ON' : ': OFF');
    btn.style.borderColor  = newVal ? '#1b5e20' : '#bbb';
    btn.style.background   = newVal ? '#e8f5e9' : '#f5f5f5';
    btn.style.color        = newVal ? '#1b5e20' : '#555';
    btn.style.fontWeight   = newVal ? '700' : '400';
  }
  // Update [A] badge in table row
  const badgeCell = document.getElementById('row-badges-' + safeId);
  if (badgeCell) {
    let aBadge = badgeCell.querySelector('.auto-proj-badge');
    if (newVal && !aBadge) {
      aBadge = document.createElement('span');
      aBadge.className = 'auto-proj-badge';
      aBadge.title = 'Auto Project: AI projections will replace manual projections on each forecast run';
      aBadge.style.cssText = 'display:inline-block;background:#1b5e20;color:#fff;border-radius:3px;padding:0 4px;font-size:10px;font-weight:700;letter-spacing:0.3px;margin-left:2px;';
      aBadge.textContent = '[A]';
      badgeCell.appendChild(aBadge);
    } else if (!newVal && aBadge) {
      aBadge.remove();
    }
  }
  try {
    const fields = {};
    fields[CFG.FID.KEY]          = { value: key };
    fields[CFG.FID.AUTO_PROJECT]  = { value: newVal };
    await qb('/records', {
      to: CFG.PROJECTIONS_TID,
      data: [fields],
      mergeFieldId: CFG.FID.KEY,
    });
  } catch (e) {
    // Revert on failure
    console.error('toggleAutoProject failed:', e);
    rec.auto_project = !newVal;
    if (btn) {
      btn.textContent = '\u{1F504} Auto Project' + (rec.auto_project ? ': ON' : ': OFF');
      btn.style.borderColor = rec.auto_project ? '#1b5e20' : '#bbb';
      btn.style.background  = rec.auto_project ? '#e8f5e9' : '#f5f5f5';
      btn.style.color       = rec.auto_project ? '#1b5e20' : '#555';
      btn.style.fontWeight  = rec.auto_project ? '700' : '400';
      btn.title = 'Save failed: ' + e.message;
    }
  }
}

function updateFlagCount() {
  const n = ALL_RECORDS.filter(r => r.flagged).length;
  const el = document.getElementById('flagCount');
  if (el) el.textContent = n + ' flagged for manager';
}

// -- Unified attention banner ------------------------------------------------
// Directors → count of planner_reply_pending; Planners → manager_reply_pending
// for their own name.  One banner, role-appropriate message and action button.
function _updateAttnBanner() {
  const banner  = document.getElementById('attnBanner');
  const textEl  = document.getElementById('attnBannerText');
  const btnEl   = document.getElementById('attnBannerViewBtn');
  if (!banner) return;

  // Count only loaded records where an active comment is addressed to me by name —
  // the same set that drives the "For Me" filter button.
  const n = ALL_RECORDS.filter(r => _FOR_ME_KEYS.has(r.key)).length;
  const msg = n === 1 ? 'item needs your attention' : 'items need your attention';

  if (textEl) textEl.textContent = n + ' ' + msg;
  if (btnEl)  { btnEl.textContent = 'View'; window._attnBannerAction = toggleForMe; }
  if (n > 0 && banner.dataset.dismissed !== '1') banner.style.display = 'flex';
  else if (n === 0) { banner.style.display = 'none'; delete banner.dataset.dismissed; }
}

// -- Switchover detection ---------------------------------------------------
// Two maps are maintained:
//
//   SWITCHOVER_MAP (auto-detected COS/EC, Amazon only)
//     baseKey  →  variantMstyle  (string)
//
//   MANUAL_SWITCHOVER_MAP (planner-configured, any customer)
//     baseKey  →  { toMstyle, toKey, date }
//       date = JS Date of the switchover week (null if not yet set)
//
//   MANUAL_SWITCHOVER_REVERSE (indexed by the NEW style's key)
//     newKey   →  { fromKey, fromMstyle, date }
//
// Both are built after ALL_RECORDS loads and rebuilt on forceRefresh.

// Analyzes 26-week order history for a seasonal customer to find order events,
// average gap between events, and when the next order window is likely due.
// histOrd: array of 26 weekly order quantities, oldest first (index 0 = LW-25).
function _analyzeSeasonalPattern(histOrd) {
  const h = (histOrd || []).map(v => v || 0);
  // Identify distinct "order events" = runs of consecutive non-zero weeks
  const events = [];
  let inRun = false, runStart = 0, runTotal = 0;
  for (let i = 0; i < h.length; i++) {
    if (h[i] > 0) {
      if (!inRun) { inRun = true; runStart = i; runTotal = 0; }
      runTotal += h[i];
    } else if (inRun) {
      events.push({ start: runStart, end: i - 1, total: runTotal });
      inRun = false;
    }
  }
  if (inRun) events.push({ start: runStart, end: h.length - 1, total: runTotal });

  if (!events.length) return { events: [], avgGapWks: null, nextExpectedWk: null, avgOrderTotal: 0, wksSinceLast: null };

  // Gaps between event ends and the following event's start
  const gaps = [];
  for (let i = 1; i < events.length; i++) {
    gaps.push(events[i].start - events[i - 1].end - 1);
  }
  const avgGapWks = gaps.length > 0
    ? Math.round(gaps.reduce((a, b) => a + b, 0) / gaps.length)
    : null;

  const lastEvent    = events[events.length - 1];
  const wksSinceLast = h.length - 1 - lastEvent.end;  // 0 = last week had orders
  const nextExpectedWk = avgGapWks !== null ? avgGapWks - wksSinceLast : null;
  const avgOrderTotal  = Math.round(events.reduce((s, e) => s + e.total, 0) / events.length);

  return { events, avgGapWks, nextExpectedWk, avgOrderTotal, wksSinceLast };
}

const SWITCHOVER_MAP            = new Map();  // COS/EC auto-detected
const MANUAL_SWITCHOVER_MAP     = new Map();  // planner-configured base → new
const MANUAL_SWITCHOVER_REVERSE = new Map();  // planner-configured new  → base

function buildSwitchoverMap() {
  SWITCHOVER_MAP.clear();
  MANUAL_SWITCHOVER_MAP.clear();
  MANUAL_SWITCHOVER_REVERSE.clear();

  // -- Auto-detect COS/EC variants (Amazon only) ----------------------------
  for (const r of ALL_RECORDS) {
    if (!/amazon/i.test(r.cust || '')) continue;
    const m = r.mstyle.match(/^(.+?)(COS|EC)$/i);
    if (!m) continue;
    const hasOrders = r.hist_ord  && r.hist_ord.some(v => v > 0);
    const hasProjs  = r.weeks_slim && r.weeks_slim.some(w => (w.projection || 0) > 0);
    if (!hasOrders && !hasProjs) continue;
    const baseMstyle = m[1];
    const baseKey    = r.key.replace(r.mstyle, baseMstyle);
    if (ALL_RECORDS.some(b => b.key === baseKey)) {
      SWITCHOVER_MAP.set(baseKey, r.mstyle);
    }
  }

  // -- Manual switchovers (planner-configured via checkbox + fields) --------
  for (const r of ALL_RECORDS) {
    if (!r.switchover_active || !r.switchover_to_mstyle) continue;
    const toMstyle = r.switchover_to_mstyle.trim().toUpperCase();
    if (!toMstyle) continue;
    // Derive the target key: same account prefix, different mstyle
    // Key format: AcctNum-MStyle  →  replace mstyle part
    const toKey = r.key.replace(r.mstyle, toMstyle);
    const date  = r.switchover_date ? new Date(r.switchover_date) : null;
    MANUAL_SWITCHOVER_MAP.set(r.key, { toMstyle, toKey, date });
    MANUAL_SWITCHOVER_REVERSE.set(toKey, { fromKey: r.key, fromMstyle: r.mstyle, date });
  }

  console.info(
    `[Switchover] ${SWITCHOVER_MAP.size} COS/EC auto | ` +
    `${MANUAL_SWITCHOVER_MAP.size} manual configured`
  );
}

// Returns the index (0-based) of the first week that belongs to the NEW style,
// or -1 if no manual switchover is active for this key.
// weekDates: array of 26 JS Date objects, one per projection week (Mon of that week).
function _switchoverWeekIndex(key, weekDates) {
  const sw = MANUAL_SWITCHOVER_MAP.get(key) || MANUAL_SWITCHOVER_REVERSE.get(key);
  if (!sw || !sw.date) return -1;
  const cutoff = sw.date;
  // First week whose Monday is >= switchover date
  for (let i = 0; i < weekDates.length; i++) {
    if (weekDates[i] >= cutoff) return i;
  }
  return weekDates.length;   // switchover is beyond the 26-week window
}

// Builds an array of 26 JS Date objects (each is the Monday of that proj week).
// Week 0 = current week (last Monday).  Cached for the session.
let _weekDateCache = null;
function _getWeekDates() {
  if (_weekDateCache) return _weekDateCache;
  const today = new Date();
  const dow   = today.getDay();
  const lastMon = new Date(today);
  lastMon.setDate(today.getDate() - (dow === 0 ? 6 : dow - 1));
  lastMon.setHours(0, 0, 0, 0);
  _weekDateCache = Array.from({ length: 26 }, (_, i) => {
    const d = new Date(lastMon);
    d.setDate(lastMon.getDate() + i * 7);
    return d;
  });
  return _weekDateCache;
}

// Saves a single switchover field change (active / mstyle / date) to QB,
// updates the in-memory record, and rebuilds the switchover map so the
// projection week locking and badges refresh without a full page reload.
async function saveSwitchoverField(key, field, value) {
  const safeId  = key.replace(/[^a-zA-Z0-9]/g, '_');
  const statusEl = document.getElementById('sw-status-' + safeId);
  const rec = ALL_RECORDS.find(x => x.key === key);
  if (!rec) return;
  if (statusEl) statusEl.textContent = 'Saving...';
  const fields = {};
  fields[CFG.FID.KEY] = { value: key };
  if (field === 'active') {
    rec.switchover_active = !!value;
    fields[CFG.FID.SWITCHOVER_ACTIVE] = { value: !!value };
  } else if (field === 'mstyle') {
    rec.switchover_to_mstyle = value.trim().toUpperCase();
    fields[CFG.FID.SWITCHOVER_TO_MSTYLE] = { value: rec.switchover_to_mstyle };
    // Normalise the input to uppercase in the UI
    const inp = document.getElementById('sw-mstyle-' + safeId);
    if (inp) inp.value = rec.switchover_to_mstyle;
  } else if (field === 'date') {
    rec.switchover_date = value;   // ISO date string "YYYY-MM-DD" or ''
    fields[CFG.FID.SWITCHOVER_DATE] = { value: value || null };
  }
  try {
    await qb('/records', { to: CFG.PROJECTIONS_TID, data: [fields], mergeFieldId: CFG.FID.KEY });
    // Rebuild the switchover map so week locking reflects the change immediately.
    // Invalidate the week-date cache in case date changed.
    if (field === 'date') _weekDateCache = null;
    buildSwitchoverMap();
    // Refresh the row badge
    const badgeCell = document.getElementById('row-badges-' + safeId);
    if (badgeCell) {
      const sb = badgeCell.querySelector('.switchover-badge');
      const hasSw = MANUAL_SWITCHOVER_MAP.has(key);
      if (!sb && hasSw)  badgeCell.insertAdjacentHTML('beforeend', '<span class="switchover-badge" title="Manual switchover configured">&#x21C4;</span>');
      if (sb  && !hasSw && !SWITCHOVER_MAP.has(key)) sb.remove();
    }
    // Collapse and re-expand to re-render locked weeks and card status line
    const tr = document.getElementById('detail-' + safeId);
    if (tr) { tr.dataset.loaded = ''; }   // force re-render on next open

    // -- Auto-create target Projections record when activating a switchover ------
    // Only fires when the checkbox is being turned ON and a target mstyle is set.
    // A separate insert is safer than merging into the main write above because
    // the new record's KEY is different from the base style's KEY.
    if (field === 'active' && value && rec.switchover_to_mstyle) {
      const toMstyle = rec.switchover_to_mstyle.trim().toUpperCase();
      const acctNum  = rec.key.slice(0, rec.key.length - rec.mstyle.length - 1);
      const newKey   = acctNum + '-' + toMstyle;
      if (!ALL_RECORDS.some(x => x.key === newKey)) {
        if (statusEl) { statusEl.style.color = '#1565c0'; statusEl.textContent = 'Saved - creating record...'; }
        const nf = {};
        nf[CFG.FID.KEY]         = { value: newKey };
        nf[CFG.FID.MSTYLE]      = { value: toMstyle };
        nf[CFG.FID.ACCT_NUM]    = { value: parseInt(acctNum, 10) || 0 };
        nf[CFG.FID.ACCT_TXT]    = { value: acctNum };
        nf[CFG.FID.CUST]        = { value: rec.cust || '' };
        nf[CFG.FID.STATUS_CUST] = { value: 'AUTO ADD' };
        try {
          await qb('/records', { to: CFG.PROJECTIONS_TID, data: [nf], mergeFieldId: CFG.FID.KEY });
          // Add a minimal in-memory stub so the new row is immediately visible in
          // the grid (and the switchover badge links up) without a full page reload.
          ALL_RECORDS.push({
            key:    newKey,  mstyle: toMstyle,  cust: rec.cust || '',
            acct_num: acctNum,
            asin_status:     'AUTO ADD',
            inv_manager:     rec.inv_manager || '',
            severity: '', volume: '', pattern: '',
            brand:    rec.brand    || '',
            description: '', priority: 0, note: '',
            hist_ord:  Array(26).fill(0),
            hist_ship: Array(26).fill(0),
            weeks_slim: Array(26).fill(null).map(() => ({ projection: 0, ai_proj: 0, locked: false })),
            has_comments:          false,
            manager_reply_pending: false,
            planner_reply_pending: false,
            switchover_active:     false,
            switchover_to_mstyle:  '',
            switchover_date:       '',
          });
          buildSwitchoverMap();
          if (statusEl) {
            statusEl.style.color = '#2e7d32';
            statusEl.textContent = '✓ Saved + new record created';
            setTimeout(() => { if (statusEl) statusEl.textContent = ''; }, 3500);
          }
        } catch (createErr) {
          // The checkbox save already succeeded -- surface the create failure separately
          // so the planner knows to add the record manually.
          if (statusEl) {
            statusEl.style.color = '#e65100';
            statusEl.textContent = 'Saved, but auto-create failed: ' + (createErr.message || 'error');
          }
        }
      } else {
        // Target record already exists -- just show a normal save confirmation.
        if (statusEl) { statusEl.style.color = '#2e7d32'; statusEl.textContent = '✓ Saved'; setTimeout(() => { if (statusEl) statusEl.textContent = ''; }, 2500); }
      }
    } else {
      if (statusEl) { statusEl.style.color = '#2e7d32'; statusEl.textContent = '✓ Saved'; setTimeout(() => { if (statusEl) statusEl.textContent = ''; }, 2500); }
    }
  } catch (e) {
    if (statusEl) { statusEl.style.color = '#c62828'; statusEl.textContent = 'Save failed: ' + (e.message || 'error'); }
    // Roll back in-memory
    if (field === 'active')  rec.switchover_active      = !value;
    if (field === 'mstyle') rec.switchover_to_mstyle = '';
    if (field === 'date')   rec.switchover_date      = '';
  }
}

// Writes Status_Cust = 'CLOSED' on the base style after planner confirms.
async function closeBaseStyle(key) {
  const safeId = key.replace(/[^a-zA-Z0-9]/g, '_');
  const alertEl = document.getElementById('switchover-alert-' + safeId);
  const btn     = document.getElementById('close-base-btn-'   + safeId);
  const rec = ALL_RECORDS.find(x => x.key === key);
  if (!rec) return;
  if (btn) { btn.disabled = true; btn.textContent = 'Saving...'; }
  const prev = rec.asin_status;
  rec.asin_status = 'CLOSED';
  _STATUS_CHOICES_CACHE = null;
  try {
    const fields = {};
    fields[CFG.FID.KEY]         = { value: key };
    fields[CFG.FID.STATUS_CUST] = { value: 'CLOSED' };
    await qb('/records', { to: CFG.PROJECTIONS_TID, data: [fields], mergeFieldId: CFG.FID.KEY });
    // Update the status cell in the main table row
    const statusCell = document.getElementById('status-cell-' + safeId);
    if (statusCell) statusCell.textContent = 'CLOSED';
    // Remove the switchover row badge
    const badgeCell = document.getElementById('row-badges-' + safeId);
    if (badgeCell) { const sb = badgeCell.querySelector('.switchover-badge'); if (sb) sb.remove(); }
    // Replace the alert with a success message
    if (alertEl) alertEl.innerHTML = `
      <span style="font-size:13px;font-weight:700;color:#2e7d32;">&#x2713; Marked as CLOSED.</span>
      <span style="font-size:12px;color:#555;margin-left:8px;">Status @ Cust updated successfully.</span>`;
    SWITCHOVER_MAP.delete(key);
  } catch (e) {
    rec.asin_status = prev;
    if (btn) { btn.disabled = false; btn.textContent = 'Mark as CLOSED'; }
    if (alertEl) {
      const errEl = alertEl.querySelector('.switchover-err');
      if (errEl) errEl.textContent = 'Save failed: ' + (e.message || 'unknown error');
    }
  }
}

// -- For-Me count (badge + button) -------------------------------------------
function updateForMeCount() {
  // Count loaded projection records that have an active comment addressed to me
  const n = ALL_RECORDS.filter(r => _FOR_ME_KEYS.has(r.key)).length;
  const el  = document.getElementById('forMeCount');
  const btn = document.getElementById('forMeBtn');
  if (el) { el.textContent = n + ' item' + (n === 1 ? '' : 's') + ' for me'; el.style.display = n ? 'inline' : 'none'; }
  if (btn) btn.style.fontWeight = (SHOW_FOR_ME_ONLY ? '800' : '600');
  _updateAttnBanner();
}

// -- Reply count (badge + button) --------------------------------------------
function updateReplyCount() {
  const n = ALL_RECORDS.filter(r => r.planner_reply_pending).length;
  const el  = document.getElementById('replyCount');
  const btn = document.getElementById('replyOnlyBtn');
  if (el) { el.textContent = n + ' repl' + (n === 1 ? 'y' : 'ies') + ' pending'; el.style.display = n ? 'inline' : 'none'; }
  if (btn) btn.style.fontWeight = (SHOW_REPLY_ONLY ? '800' : '600');
  _updateAttnBanner();
}

let SHOW_REPLY_ONLY = false;
function toggleReplyOnly() {
  SHOW_REPLY_ONLY = !SHOW_REPLY_ONLY;
  const btn = document.getElementById('replyOnlyBtn');
  if (btn) {
    btn.style.background    = SHOW_REPLY_ONLY ? '#00695c' : '#fff';
    btn.style.color         = SHOW_REPLY_ONLY ? '#fff'    : '#00695c';
    btn.style.fontWeight    = '800';
  }
  applyFilters();
}


// "📬 For Me" — driven by SEND_TO on active comments, not boolean flags on the projection.
// _FOR_ME_KEYS is the set of projection keys where at least one non-resolved/non-reviewed
// comment is explicitly addressed to the current user (SEND_TO text contains their name,
// or SEND_TO_USER email matches their email).  Refreshed at load and after any comment action.
let _FOR_ME_KEYS = new Set();

async function refreshForMeKeys() {
  if (!CURRENT_USER.name && !CURRENT_USER.email) return;
  try {
    const F = CFG.COMMENT_FID;
    // Active flags only — skip informational/closed entries
    const activeFlags = ['Needs Action', 'Planner Response', 'Manager Response'];
    const flagClause  = activeFlags.map(f => `{${F.FLAG}.EX.'${f}'}`).join('OR');
    // Match on SEND_TO text (contains, handles comma-separated lists) OR SEND_TO_USER email
    const escName  = (CURRENT_USER.name  || '').replace(/'/g, "''");
    const escEmail = (CURRENT_USER.email || '').replace(/'/g, "''");
    const nameClause  = escName  ? `{${F.SEND_TO}.CT.'${escName}'}`             : null;
    const emailClause = (F.SEND_TO_USER && escEmail) ? `{${F.SEND_TO_USER}.EX.'${escEmail}'}` : null;
    const recipClause = (nameClause && emailClause) ? `(${nameClause}OR${emailClause})`
                      : (nameClause || emailClause);
    if (!recipClause) return;
    const where = `${recipClause}AND(${flagClause})`;
    const resp = await qb('/records/query', {
      from:    CFG.COMMENTS_TID,
      select:  [F.ACCT_MSTYLE],
      where,
      options: { top: 2000 },
    });
    _FOR_ME_KEYS = new Set(
      (resp.data || [])
        .map(row => row[F.ACCT_MSTYLE] && row[F.ACCT_MSTYLE].value)
        .filter(Boolean)
    );
    updateForMeCount();
  } catch (_) { /* non-fatal — filter falls back to empty set */ }
}

let SHOW_FOR_ME_ONLY = false;
function toggleForMe() {
  SHOW_FOR_ME_ONLY = !SHOW_FOR_ME_ONLY;
  _syncForMeButton();
  applyFilters();
}
function _syncForMeButton() {
  const btn = document.getElementById('forMeBtn');
  if (!btn) return;
  btn.style.background = SHOW_FOR_ME_ONLY ? '#e65100' : '#fff';
  btn.style.color      = SHOW_FOR_ME_ONLY ? '#fff'    : '#e65100';
  btn.style.fontWeight = '800';
}

// -- Render: pagination -----------------------------------------------------
function renderPage(page) {
  currentPage = page;
  const start = page * PAGE_SIZE;
  const end   = Math.min(start + PAGE_SIZE, FILTERED_RECORDS.length);
  const pageRecs = FILTERED_RECORDS.slice(start, end);
  const tb = document.getElementById('tbody');
  tb.innerHTML = '';
  pageRecs.forEach(r => {
    const tr = document.createElement('tr');
    tr.className = borderClass(r.max_sev)
      + (r.flagged               ? ' row-flagged'        : '')
      + (r.planner_reply_pending ? ' row-reply-pending'  : '')
      + (r.manager_reply_pending ? ' row-mgr-pending'    : '');
    tr.dataset.key = r.key;

    const aiVsProj = r.proj_total > 0 ? ((r.ai_total - r.proj_total) / r.proj_total * 100)
                   : (r.ai_total > 0 ? null : 0);  // null = no plan entered; 0 = both zero
    const aiVsL13  = (r.ai_vs_l13  == null ? 0 : r.ai_vs_l13);
    const manVsL13 = (r.man_vs_l13 == null ? 0 : r.man_vs_l13);
    const l13Avail = r.shp_wk > 0;

    const _safeId2 = r.key.replace(/[^a-zA-Z0-9]/g,'_');
    tr.innerHTML = `
      <td id="row-badges-${_safeId2}" style="white-space:nowrap;text-align:center;">${r.planner_reply_pending ? '<span class="reply-badge" title="Planner reply awaiting director review">[R]</span>' : ''}${r.manager_reply_pending ? '<span class="mgr-badge" title="Manager flagged - planner action required">[M]</span>' : ''}${SWITCHOVER_MAP.has(r.key) ? '<span class="switchover-badge" title="Switched to ' + (SWITCHOVER_MAP.get(r.key)||'').replace(/"/g,'&quot;') + ' — mark this projection CLOSED">&#x26A0;</span>' : ''}${MANUAL_SWITCHOVER_MAP.has(r.key) ? '<span class="switchover-badge" title="Manual switchover to ' + ((MANUAL_SWITCHOVER_MAP.get(r.key)||{}).toMstyle||'').replace(/"/g,'&quot;') + '">&#x21C4;</span>' : ''}${MANUAL_SWITCHOVER_REVERSE.has(r.key) ? '<span class="switchover-badge" title="Receiving switchover from ' + ((MANUAL_SWITCHOVER_REVERSE.get(r.key)||{}).fromMstyle||'').replace(/"/g,'&quot;') + '" style="color:#1565c0">&#x21C4;</span>' : ''}${r.auto_project ? '<span style="display:inline-block;background:#1b5e20;color:#fff;border-radius:3px;padding:0 4px;font-size:10px;font-weight:700;letter-spacing:0.3px;margin-left:2px;" title="Auto Project: AI projections will replace manual projections on each forecast run">[A]</span>' : ''}</td>
      <td class="clickable" onclick="toggleDetail('${r.key}')"><span id="conflict-badge-${_safeId2}" style="display:${r.has_po_prj_conflict ? 'inline' : 'none'};color:#e65100;font-size:14px;font-weight:700;margin-right:3px;vertical-align:middle;cursor:pointer;" title="Open PO and Manual Projection overlap — potential double-count. Click to see details.">&#x26A0;</span>${r.key}</td>
      <td style="font-size:11px;white-space:nowrap">${r.inv_manager||''}</td>
      <td style="font-size:11px;max-width:90px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${(r.brand||'').replace(/"/g,'&quot;')}">${r.brand||''}</td>
      <td class="clickable" onclick="toggleDetail('${r.key}')">${r.cust}</td>
      <td>${r.mstyle}</td>
      <td class="cust-sku-cell" data-key="${r.key.replace(/"/g,'&quot;')}"
          onclick="editCustSku('${r.key.replace(/'/g,"\\'")}', this)"
          title="${r.cust_sku ? r.cust_sku.replace(/"/g,'&quot;') : 'Click to edit Cust SKU#'}"
          style="font-size:11px;max-width:120px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;cursor:pointer;padding:2px 6px;border-bottom:1px dashed transparent;"
          onmouseover="this.style.borderBottomColor='#1565c0'"
          onmouseout="this.style.borderBottomColor='transparent'">${r.cust_sku || ''}</td>
      <td style="font-size:11px;max-width:180px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${(r.desc||'').replace(/"/g,'&quot;')}">${r.desc||''}</td>
      ${_renderStatusCell(r.asin_status, r.key)}
      <td style="font-size:11px;max-width:100px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${(r.item_status||'').replace(/"/g,'&quot;')}">${r.item_status||''}</td>
      <td style="white-space:nowrap">${_priCell(r)}</td>
      <td style="font-size:11px;color:#888;white-space:nowrap">${r.last_ord_date || ' -'}</td>
      <td style="color:#6a1b9a">${fmtN(Math.round(r.shpd_wk_l4 || 0))}</td>
      <td style="color:#6a1b9a">${fmtN(Math.round(r.shpd_wk_l13 || 0))}</td>
      <td>${fmtN(Math.round(r.ord_wk_l4 || 0))}</td>
      <td>${fmtN(Math.round(r.shp_wk))}</td>
      <td id="metric-projwk-${_safeId2}">${fmtN(Math.round(r.proj_wk))}</td>
      <td id="metric-aiwk-${_safeId2}" style="color:#1565c0;font-weight:600">${fmtN(Math.round(r.ai_wk))}</td>
      <td style="color:#555" title="Average of Suggested W1..W26">${fmtN(Math.round(r.sugg_wk))}</td>
      <td id="metric-aiproj-${_safeId2}" style="font-size:14px;font-weight:800;color:${aiVsProj === null ? '#888' : aiVsProj > 0 ? '#2e7d32' : aiVsProj < 0 ? '#c62828' : '#888'}">${aiVsProj === null ? '-' : (aiVsProj >= 0 ? '+' : '') + aiVsProj.toFixed(1) + '%'}</td>
      <td id="metric-fcst-${_safeId2}" style="text-align:center">${_fcstStatusBadge(r.fcst_status)}</td>
      <td id="metric-ail13-${_safeId2}" style="font-size:13px;font-weight:700;color:${!l13Avail ? '#888' : (aiVsL13 > 0 ? '#2e7d32' : aiVsL13 < 0 ? '#c62828' : '#888')}">${l13Avail ? (aiVsL13 >= 0 ? '+' : '') + aiVsL13.toFixed(1) + '%' : ' - '}</td>
      <td id="metric-manl13-${_safeId2}" style="font-size:13px;font-weight:700;color:${!l13Avail ? '#888' : (manVsL13 > 0 ? '#2e7d32' : manVsL13 < 0 ? '#c62828' : '#888')}">${l13Avail ? (manVsL13 >= 0 ? '+' : '') + manVsL13.toFixed(1) + '%' : ' - '}</td>
    `;
    tb.appendChild(tr);

    const dtr = document.createElement('tr');
    dtr.className = 'detail-pane';
    dtr.id = 'detail-' + r.key;
    dtr.dataset.loaded = '0';
    dtr.innerHTML = `<td colspan="26"></td>`;
    tb.appendChild(dtr);
  });
  updatePageNav();
  updateFlagCount();
  updateReplyCount();
}
function renderTable() { renderPage(0); }

function updatePageNav() {
  const n = FILTERED_RECORDS.length;
  const totalPages = Math.max(1, Math.ceil(n / PAGE_SIZE));
  const navEl = document.getElementById('pageNav');
  if (navEl) {
    navEl.style.display = totalPages > 1 ? 'flex' : 'none';
    document.getElementById('pageInfo').textContent =
      `Page ${currentPage + 1} of ${totalPages} (${n.toLocaleString()} records)`;
    document.getElementById('prevBtn').disabled = currentPage === 0;
    document.getElementById('nextBtn').disabled = currentPage >= totalPages - 1;
  }
  document.getElementById('statLine').textContent = n.toLocaleString() + ' records shown';
}
function changePage(delta) {
  const totalPages = Math.max(1, Math.ceil(FILTERED_RECORDS.length / PAGE_SIZE));
  const newPage = Math.max(0, Math.min(currentPage + delta, totalPages - 1));
  if (newPage !== currentPage) renderPage(newPage);
}

// -- Expand/collapse detail panel -------------------------------------------
async function toggleDetail(key) {
  const el = document.getElementById('detail-' + key);
  if (!el) return;
  if (el.style.display === 'table-row') { el.style.display = 'none'; _openDetailKey = null; return; }
  _openDetailKey = key;
  el.style.display = 'table-row';
  if (el.dataset.loaded === '1') return;

  // If this record was served from cache, the heavy detail arrays were stripped
  // to keep the cache small.  Fetch just this one record's arrays from QB now.
  const _lazyRec = ALL_RECORDS.find(x => x.key === key);
  if (_lazyRec && _lazyRec._needs_detail) {
    el.innerHTML = `<td colspan="25" style="padding:10px 16px;color:#888;font-style:italic;font-size:12px">Loading detail...</td>`;
    try {
      await _lazyLoadDetail(_lazyRec);
    } catch (_le) {
      el.innerHTML = `<td colspan="25" style="padding:12px 16px;background:#fff3e0"><b style="color:#c62828">Could not load detail: ${_le.message}</b> - <a href="?nocache=1" style="color:#1565c0">reload fresh</a></td>`;
      el.dataset.loaded = '1';
      return;
    }
  }

  // Wrap everything from here in a try-catch so a JS error never leaves the
  // panel silently blank.  On failure we at minimum show the error in the pane
  // and log it to the console so the planner can report it.
  try {

  const r      = ALL_RECORDS.find(x => x.key === key) || {};
  const wks    = r.weeks_slim || [];
  const aiFcst = r.ai_fcst    || [];
  const aiMdl  = r.ai_model   || '';
  const sug    = r.suggested  || [];

  // Safety valve: if the record wasn't found in ALL_RECORDS (e.g. key mismatch
  // between the onclick and the cached records), show a diagnostic card instead
  // of a blank panel.  Also logs to the console for easier support.
  if (!r.key) {
    console.warn('[toggleDetail] record not found in ALL_RECORDS for key:', JSON.stringify(key),
                 '| ALL_RECORDS.length:', ALL_RECORDS.length,
                 '| sample keys:', ALL_RECORDS.slice(0,3).map(x => x.key));
    el.innerHTML = `<td colspan="22" style="padding:12px 16px;background:#fff3e0;border-top:2px solid #ffb74d;">
      <b style="color:#e65100">&#x26A0; Detail panel could not load for this record.</b><br>
      <span style="font-size:11px;color:#555;">Key: <code>${key || '(empty)'}</code> was not found in the loaded record set (${ALL_RECORDS.length} records loaded).
      Try refreshing with <a href="?nocache=1" style="color:#1565c0">?nocache=1</a> to force a fresh pull from Quickbase.
      If this is an FD (Future Delete) item, it may have a missing or blank Key field in QB.</span>
    </td>`;
    el.dataset.loaded = '1';
    return;
  }

  let hdrCells  = '<th class="row-label"></th>';
  let projCells = '<td class="row-label">Projection</td>';
  let aiCells   = `<td class="row-label" style="color:#1565c0;font-weight:600">AI Forecast<br><span style="font-weight:normal;font-size:10px">${aiMdl}</span></td>`;
  let sugCells  = '<td class="row-label" style="color:#555">Suggested</td>';
  let opnCells  = '<td class="row-label" style="color:#6d4c00;font-weight:600">Open POs</td>';
  let sugTot = 0, opnTot = 0;

  // Pre-compute the safe-id we use for both the Total cell and the input
  // dataset attributes (key may contain hyphens that are fine in attrs but
  // make CSS-style id selectors awkward).
  const safeIdForTotal = r.key.replace(/[^a-zA-Z0-9]/g, '_');
  // Detect Amazon record so we can fetch DC inventory health live.
  const isAmazonRec = /amazon/i.test(r.cust || '');

  // -- Manual switchover: determine which weeks are locked for this row -----
  // Base style (in MANUAL_SWITCHOVER_MAP): weeks >= cutoff belong to new style → locked
  // New style  (in MANUAL_SWITCHOVER_REVERSE): weeks < cutoff belong to base  → locked
  const _weekDates      = _getWeekDates();
  const _swIdxBase      = _switchoverWeekIndex(r.key, _weekDates); // -1 if not a base
  const _revEntry       = MANUAL_SWITCHOVER_REVERSE.get(r.key);
  const _swIdxNew       = _revEntry ? _switchoverWeekIndex(_revEntry.fromKey, _weekDates) : -1;
  // isLockedWeek(i): true when week i is out of this style's territory
  const _isBaseSide     = MANUAL_SWITCHOVER_MAP.has(r.key);
  const _isNewSide      = !!_revEntry;
  const isLockedWeek    = (i) => {
    if (_isBaseSide && _swIdxBase >= 0) return i >= _swIdxBase;   // base: lock post-cutoff
    if (_isNewSide  && _swIdxNew  >= 0) return i <  _swIdxNew;    // new:  lock pre-cutoff
    return false;
  };

  let liveProjTotal = 0;
  for (let i = 0; i < wks.length; i++) {
    const w      = wks[i];
    const lbl    = weekLabel(i);
    const locked = isLockedWeek(i);
    hdrCells  += `<th${locked ? ' style="opacity:0.4"' : ''}>W${w.week}<br><span style="font-weight:normal;font-size:10px">${lbl}</span></th>`;
    const cls = weekCellClass(w.severity);
    // -- Editable MAN projection cell ------------------------------------
    // Show the unsaved value (from DIRTY_EDITS) if one exists, otherwise the
    // QB-loaded value.  The dirty class is applied so the yellow highlight
    // survives a collapse/re-expand cycle.
    const dirtyKey   = `${r.key}|${i}`;
    const dirtyEdit  = DIRTY_EDITS.get(dirtyKey);
    const cellVal    = (dirtyEdit !== undefined) ? dirtyEdit.newVal : Math.round(w.projection || 0);
    const dirtyAttr  = (dirtyEdit !== undefined) ? ' dirty' : '';
    if (!locked) liveProjTotal += cellVal;
    // Locked weeks: show a read-only grayed cell with a tooltip explaining why
    const lockedTitle = locked
      ? (_isBaseSide
          ? `title="Week transferred to ${(MANUAL_SWITCHOVER_MAP.get(r.key)||{}).toMstyle||'new style'} — edit there"`
          : `title="Week owned by ${_revEntry.fromMstyle||'base style'} — edit there"`)
      : '';
    projCells += locked
      ? `<td class="${cls}" style="background:#f5f5f5;opacity:0.55;" ${lockedTitle}>`
        + `<input type="number" min="0" step="1" class="man-edit" disabled `
        + `value="${cellVal}" style="background:transparent;color:#aaa;border:none;width:52px;text-align:right;"></td>`
      : `<td class="${cls}"><input type="number" min="0" step="1" `
        +  `class="man-edit${dirtyAttr}" `
        +  `data-key="${r.key.replace(/"/g,'&quot;')}" data-week="${i}" `
        +  `data-orig="${Math.round(w.projection || 0)}" `
        +  `value="${cellVal}" oninput="onManEdit(this)" `
        +  `onfocus="markLastEdit(this); this.select();" `
        +  `onpaste="smartPaste(this, event)" `
        +  `onkeydown="manEditKey(this, event)"></td>`;
    const aiVal  = aiFcst[i] || 0;
    const aiDiff = aiVal - w.projection;
    const aiCls  = aiDiff > 0 ? 'color:#2e7d32' : aiDiff < 0 ? 'color:#c62828' : 'color:#888';
    // F37 v2 (2026-05-26): if this week was capped by inventory shortfall,
    // paint a red background and add a tooltip with the cap detail.  The
    // text color (green/red/gray) continues to indicate AI-vs-Manual direction.
    const _wk1Idx = i + 1;
    const _isCapped = r.f37_capped_weeks && r.f37_capped_weeks.has(_wk1Idx);
    let _aiBg = '';
    let _aiTitle = '';
    if (_isCapped) {
      _aiBg = 'background:#ffebee;';   // light red tint
      const _det = r.f37_capped_detail && r.f37_capped_detail[String(_wk1Idx)];
      if (_det) {
        _aiTitle = ` title="Inventory short: AI wanted ${fmtN(_det.orig)}, can ship ${fmtN(_det.adj)} (capacity ${fmtN(_det.cap)} this week). Unmet demand rolls forward, decaying 25%/week vs original; expires at age 4."`;
      } else {
        _aiTitle = ' title="F37 inventory-shortfall cap: AI demand exceeded available inventory this week."';
      }
    }
    aiCells   += `<td style="${_aiBg}${aiCls};font-weight:600"${_aiTitle}>${fmtN(aiVal)}</td>`;
    const sugVal = sug[i] || 0;
    sugTot    += sugVal;
    sugCells  += `<td style="color:#555;font-size:10px">${fmtN(sugVal)}</td>`;
    const opnVal = (r.opn_w || [])[i] || 0;
    opnTot    += opnVal;
    opnCells  += `<td style="${opnVal === 0 ? 'color:#bbb' : 'color:#6d4c00;font-weight:600'};font-size:10px">${fmtN(opnVal)}</td>`;
  }
  hdrCells  += '<th>Total</th>';
  projCells += `<td id="man-total-${safeIdForTotal}" style="font-weight:700">${fmtN(liveProjTotal)}</td>`;
  aiCells   += `<td style="font-weight:700;color:#1565c0">${fmtN(r.ai_total)}</td>`;
  sugCells  += `<td style="font-weight:700;color:#555">${fmtN(sugTot)}</td>`;
  opnCells  += `<td style="font-weight:700;color:#6d4c00">${fmtN(opnTot)}</td>`;

  // Avg/Wk column  -  separate header so the invFlow table (which also reuses
  // hdrCells) is not affected.  Appended only to the projection-table header
  // and its corresponding data rows.
  const _wkCount = wks.length || 26;
  const projHdrCells = hdrCells + '<th style="color:#888;font-weight:600">Avg/Wk</th>';
  projCells += `<td id="man-avgwk-${safeIdForTotal}" style="font-weight:700;color:#555">${fmtN(Math.round(liveProjTotal / _wkCount))}</td>`;
  aiCells   += `<td style="font-weight:700;color:#1565c0">${fmtN(Math.round(r.ai_total / _wkCount))}</td>`;
  sugCells  += `<td style="font-weight:700;color:#555">${fmtN(Math.round(sugTot / _wkCount))}</td>`;
  opnCells  += `<td style="font-weight:700;color:#6d4c00">${fmtN(Math.round(opnTot / _wkCount))}</td>`;

  // LY actuals  -  Ordered LY (green) + Shipped LY (blue), W1..W26 alignment.
  // ly_ord[i] = Ord LW-(51-i) = the calendar week 52 weeks before forecast Wi+1.
  const lyOrd = r.ly_ord || [];
  const lyShp = r.ly_shp || [];
  let lyOrdCells = '<td class="row-label" style="color:#2e7d32;font-weight:600">Ordered LY</td>';
  let lyShpCells = '<td class="row-label" style="color:#1565c0;font-weight:600">Shipped LY</td>';
  let lyOrdTot = 0, lyShpTot = 0;
  for (let i = 0; i < 26; i++) {
    const ov = lyOrd[i] || 0;
    lyOrdTot += ov;
    lyOrdCells += `<td style="${ov === 0 ? 'color:#bbb' : 'color:#2e7d32'};font-size:10px">${fmtN(ov)}</td>`;
    const sv = lyShp[i] || 0;
    lyShpTot += sv;
    lyShpCells += `<td style="${sv === 0 ? 'color:#bbb' : 'color:#1565c0'};font-size:10px">${fmtN(sv)}</td>`;
  }
  lyOrdCells += `<td style="font-weight:700;color:#2e7d32">${fmtN(lyOrdTot)}</td>`;
  lyOrdCells += `<td style="font-weight:700;color:#2e7d32">${fmtN(Math.round(lyOrdTot / 26))}</td>`;
  lyShpCells += `<td style="font-weight:700;color:#1565c0">${fmtN(lyShpTot)}</td>`;
  lyShpCells += `<td style="font-weight:700;color:#1565c0">${fmtN(Math.round(lyShpTot / 26))}</td>`;

  // -- Inventory Flow section ----------------------------------------------
  // Four rows from QB Inventory Flow table, keyed by mstyle:
  //   1) Beg Inv          (Wk1..Wk26 numeric, beginning-of-week balance)
  //   2) Prj Demand       (PrjWk1..PrjWk26 numeric, projected weekly demand)
  //   3) Expected Receipts (RcvWk1..RcvWk26 numeric)
  //   4) WOS OH            = Beg Inv / Prj demand, computed client-side, 1 decimal
  // Always rendered so planners see whether QB has data (dashes when missing).
  const _beg = r.inv_flow_beg || null;
  const _rcv = r.inv_flow_rcv || null;
  const _prj = r.inv_flow_prj || null;
  const _hasInvFlow = !!(_beg || _rcv || _prj);

  // -- Supplier PO helpers (for Expected Receipts hover tooltips) -------------
  function _parseSupplierPOs(text) {
    if (!text) return [];
    return text.split(/[\r\n;]+/).map(l => l.trim()).filter(Boolean).map(line => {
      const poM   = line.match(/^([^-]+)/);
      const suppM = line.match(/^[^-]+-\s*([^-]+?)\s*-\s*I\/T/i);
      const itM   = line.match(/I\/T:\s*([\d,]+)\s*pcs/i);
      const iwM   = line.match(/I\/W:\s*([\d,]+)\s*pcs/i);
      const etdM  = line.match(/ETD:\s*(\d{2}-\d{2}-\d{4})/i);
      const etaM  = line.match(/ETA:\s*(\d{2}-\d{2}-\d{4})/i);
      let etaDate = null;
      if (etaM) {
        const [mm, dd, yyyy] = etaM[1].split('-').map(Number);
        etaDate = new Date(yyyy, mm - 1, dd);
      }
      return {
        po:       ((poM ? poM[1] : '').trim().split('-')[0] || '').trim(),
        supplier: (suppM ? suppM[1] : '').trim(),
        itQty:    itM ? parseInt((itM[1] || '0').replace(/,/g, ''), 10) : 0,
        iwQty:    iwM ? parseInt((iwM[1] || '0').replace(/,/g, ''), 10) : 0,
        etd:      etdM ? etdM[1] : '',
        eta:      etaM ? etaM[1] : '',
        etaDate,
      };
    }).filter(p => p.po || p.supplier);
  }
  function _rcvTooltip(pos, weekIdx) {
    if (!pos.length) return '';
    const weekStart = W1_DATE ? new Date(W1_DATE.getTime() + weekIdx * 7 * 86400000) : null;
    const weekEnd   = weekStart ? new Date(weekStart.getTime() + 6 * 86400000) : null;
    const matched   = weekStart ? pos.filter(p => p.etaDate && p.etaDate >= weekStart && p.etaDate <= weekEnd) : [];
    const display   = matched.length ? matched : pos;
    const prefix    = matched.length ? '' : 'All open supplier POs:\n';
    return prefix + display.map(p => {
      const qty = p.itQty > 0 ? `${p.itQty.toLocaleString()} pcs I/T`
                : p.iwQty > 0 ? `${p.iwQty.toLocaleString()} pcs I/W` : '';
      return `PO ${p.po} | ${p.supplier}${qty ? '\n  ' + qty : ''}${p.etd ? ' | ETD ' + p.etd : ''}${p.eta ? ' -> ETA ' + p.eta : ''}`;
    }).join('\n');
  }
  const _suppPos = _parseSupplierPOs(r.inv_flow_supp_pos || '');
  // Hybrid load: kick off a single-row fetch so this panel doesn't have to wait
  // for the whole-table bulk scan to finish.  Whichever resolves first wins.
  if (!_hasInvFlow && CFG.INV_FLOW_TID && r.mstyle) {
    _loadOneInvFlowRow(r).then(ok => {
      if (ok && _openDetailKey === key) {
        el.dataset.loaded = '';
        el.style.display = 'none';
        toggleDetail(key);
      }
    });
  }
  // If the background bulk load is still running, also re-render once it finishes
  if (!_hasInvFlow && _invFlowPromise) {
    _invFlowPromise.finally(() => {
      if (_openDetailKey !== key) return;
      el.dataset.loaded = '';
      el.style.display = 'none';
      toggleDetail(key);
    });
  }
  const _invFmt1 = n => {
    if (n == null || !Number.isFinite(n)) return ' - ';
    return n.toLocaleString('en-US', { minimumFractionDigits: 1, maximumFractionDigits: 1 });
  };
  const _mpForLow = r.master_pack || 1;
  const _lowThresh = _mpForLow * 2;

  // -- WOS forward simulation -----------------------------------------------
  // WOS = how many weeks the current inventory will last, given the projected
  // demand schedule.  We simulate week-by-week rather than dividing BegInv by
  // a single week's demand, which would give wildly wrong results on spike
  // weeks (e.g. a large PO ships in W9 -> that week shows 3,800u of demand
  // -> bv/pv = 8,000/3,800 = 2.1, even though the remaining 4,200u covers
  // 16 more weeks at the normal run rate).
  //
  // Returns: fractional weeks of coverage from startIdx onwards.
  //          If inventory survives all remaining weeks, returns (26 - startIdx).
  function _wosForward(bv, prj, startIdx) {
    if (!bv || bv <= 0 || !prj) return 0;
    let inv = bv;
    for (let j = startIdx; j < prj.length; j++) {
      const d = prj[j] || 0;
      if (d > 0) {
        if (inv <= d) return (j - startIdx) + (inv / d); // runs out mid-week
        inv -= d;
      }
      // zero-demand week: inventory unchanged, week still "covered"
    }
    return prj.length - startIdx;  // survived through end of 26-week horizon
  }

  // -- Gap analysis pre-compute --------------------------------------------
  // Goal: flag potential OOS BEFORE the next supplier receipt.  OOS = WOS
  // falls below Opt WOS.  Restricted to "Replen" items (PT Item Status
  // contains 'Replen')  -  initial-set / discontinue / phase-out items
  // shouldn't trigger replenishment alerts.
  const _optWos    = Number(r.inv_flow_opt_wos || 0);
  const _nextRcpt  = r.inv_flow_next_rcpt || '';
  const _isReplen  = /\breplen\b/i.test(String(r.item_status || ''));
  const _gap = { weeks: [], nextRcptWeekIdx: -1, nextRcptDate: null };
  if (_isReplen && _optWos > 0 && _beg && _prj && W1_DATE) {
    // Which week does the Next Avl Rcpt date fall in?
    let nrIdx = 25;   // default: assume next receipt is past W26 -> check all 26 weeks
    if (_nextRcpt) {
      const nrDate = new Date(_nextRcpt);
      if (!isNaN(nrDate.getTime())) {
        _gap.nextRcptDate = nrDate;
        const daysFromW1 = Math.floor((nrDate.getTime() - W1_DATE.getTime()) / 86400000);
        nrIdx = Math.floor(daysFromW1 / 7);
      }
    }
    _gap.nextRcptWeekIdx = nrIdx;
    const checkUntil = (nrIdx < 0) ? -1 : Math.min(25, nrIdx);
    for (let i = 0; i <= checkUntil; i++) {
      const bv = _beg[i];
      const wos = _wosForward(bv, _prj, i);
      if (bv <= 0 || wos < _optWos) {
        _gap.weeks.push({ wi: i + 1, wos: wos, deficit: _optWos - wos });
      }
    }
  }

  // Build the four rows.  Reuse the projection table's header (W1..W26) so
  // weeks line up vertically with the projection rows above.
  let invFlowSectionHtml = '';
  {
    // Build an inv-flow-specific header: same week labels as the projection table
    // but with a red * on the week where Next Avl Rcpt Dt lands, so planners can
    // immediately see when they can next receive inventory.
    let _ifNrWkIdx = -1;
    if (r.inv_flow_next_rcpt && W1_DATE) {
      const _nrd = new Date(r.inv_flow_next_rcpt);
      if (!isNaN(_nrd.getTime())) {
        _ifNrWkIdx = Math.floor((_nrd.getTime() - W1_DATE.getTime()) / 86400000 / 7);
      }
    }
    let _ifHdrCells = '<th class="row-label"></th>';
    for (let _hi = 0; _hi < wks.length; _hi++) {
      const _hw     = wks[_hi];
      const _hlbl   = weekLabel(_hi);
      const _locked = isLockedWeek(_hi);
      const _isNrWk = _ifNrWkIdx >= 0 && _ifNrWkIdx < 26 && _ifNrWkIdx === _hi;
      _ifHdrCells += `<th${_locked ? ' style="opacity:0.4"' : ''}>${_isNrWk ? '<span style="color:#c62828;font-size:13px;vertical-align:super;line-height:1">*</span>' : ''}W${_hw.week}<br><span style="font-weight:normal;font-size:10px">${_hlbl}</span></th>`;
    }
    _ifHdrCells += '<th>Total</th>';

    let begCells = `<td class="row-label" style="color:#6d4c00;font-weight:600;background:#fffbea" title="Beginning-of-week projected warehouse inventory (QB Inventory Flow, Wk1..Wk26)">Beg Inv</td>`;
    let prjCells = `<td class="row-label" style="color:#2e7d32;font-weight:600;background:#f1f8e9" title="Projected demand this week (QB Inventory Flow, Prj Wk1..Prj Wk26)">Prj Demand</td>`;
    let rcvCells = `<td class="row-label" style="color:#1565c0;font-weight:600;background:#f0f7ff" title="Expected supplier receipts that week (QB Inventory Flow, RcvWk1..RcvWk26)">Expected Receipts</td>`;
    let opnCells = `<td class="row-label" style="color:#00695c;font-weight:600;background:#e0f2f1" title="Open customer POs (all customers)  -  W1 = past-due (Wk0) + current week (Wk1) combined; W2-W26 = Wk2-Wk26">Open Orders</td>`;
    let wosCells = `<td class="row-label" style="color:#4a148c;font-weight:600;background:#f8f0fb" title="Weeks of Supply Onhand = Beg Inv / Prj demand">WOS OH</td>`;
    let begTot = 0, prjTot = 0, rcvTot = 0, opnTot = 0;
    const _opn = r.inv_flow_opn || null;
    for (let i = 0; i < 26; i++) {
      // Beg Inv
      if (_beg) {
        const bv = _beg[i];
        begTot += bv;
        const aiThisWk = aiFcst[i] || 0;
        let color = '#6d4c00';
        if (bv < 0)                                       color = '#c62828';
        else if (bv === 0 && aiThisWk > 0)                color = '#c62828';
        else if (bv > 0 && bv < _lowThresh && aiThisWk > 0) color = '#e65100';
        else if (bv === 0)                                color = '#bbb';
        begCells += `<td style="color:${color};font-size:10px;background:#fffbea">${fmtN(Math.round(bv))}</td>`;
      } else {
        begCells += `<td style="color:#bbb;font-size:10px;background:#fffbea"> - </td>`;
      }
      // Projected Demand
      if (_prj) {
        const pv = _prj[i] || 0;
        prjTot += pv;
        const color = pv > 0 ? '#2e7d32' : '#bbb';
        // Build per-customer breakdown from ALL_RECORDS for this mstyle
        const custLines = ALL_RECORDS
          .filter(x => x.mstyle === r.mstyle && x.weeks_slim && x.weeks_slim[i])
          .map(x => {
            const wv = Math.round(x.weeks_slim[i].ai_proj || x.weeks_slim[i].projection || 0);
            return wv > 0 ? (x.cust || x.acct_txt || x.key) + ': ' + fmtN(wv) : null;
          })
          .filter(Boolean);
        const tipText = custLines.length
          ? custLines.join('\n') + '\n\nTotal: ' + fmtN(Math.round(pv))
          : fmtN(Math.round(pv)) + ' units projected';
        prjCells += `<td style="color:${color};font-size:10px;background:#f1f8e9;${pv > 0 ? 'cursor:help;' : ''}" title="${tipText.replace(/"/g, '&quot;')}">${pv > 0 ? fmtN(Math.round(pv)) : '&mdash;'}</td>`;
      } else {
        prjCells += `<td style="color:#bbb;font-size:10px;background:#f1f8e9"> - </td>`;
      }
      // Expected Receipts
      if (_rcv) {
        const rv = _rcv[i];
        rcvTot += rv;
        const color = rv > 0 ? '#1565c0' : '#bbb';
        const tipText = rv > 0 ? _rcvTooltip(_suppPos, i) : '';
        const titleAttr = tipText ? ` title="${tipText.replace(/"/g, '&quot;')}"` : '';
        const cursor = tipText ? ` cursor:help;` : '';
        rcvCells += `<td style="color:${color};font-size:10px;background:#f0f7ff;${cursor}"${titleAttr}>${rv > 0 ? fmtN(rv) : '&mdash;'}</td>`;
      } else {
        rcvCells += `<td style="color:#bbb;font-size:10px;background:#f0f7ff"> - </td>`;
      }
      // WOS OH = weeks of coverage via forward simulation (NOT bv/pv for this week
      // alone  -  that blows up on large-order spike weeks).
      if (_beg && _prj) {
        const bv = _beg[i];
        let wos, wosTxt, wosColor;
        let cellBg = '#f8f0fb';
        if (bv > 0) {
          wos = _wosForward(bv, _prj, i);
          const maxWks = 26 - i;   // remaining weeks in the horizon
          if (wos >= maxWks) {
            wosTxt = _invFmt1(wos);
            wosColor = '#1b5e20';                      // covered through horizon
          } else {
            wosTxt = _invFmt1(wos);
            if (wos < 1)             wosColor = '#c62828';
            else if (wos < _optWos && _optWos > 0) wosColor = '#e65100';
            else if (wos < 4)        wosColor = '#e65100';
            else                     wosColor = '#4a148c';
          }
        } else {
          wosTxt = ' - ';
          wosColor = '#bbb';
        }
        // Gap flag  -  week is BEFORE next receipt AND WOS < Opt WOS -> red highlight
        const isGapWeek = _optWos > 0
          && _gap.nextRcptWeekIdx >= 0
          && i <= Math.min(25, _gap.nextRcptWeekIdx)
          && bv > 0
          && wos < _optWos;
        if (isGapWeek) {
          cellBg   = '#ffebee';
          wosColor = '#c62828';
        }
        const bold = (isGapWeek || (wos != null && wos < 4 && bv > 0)) ? 700 : 400;
        wosCells += `<td style="color:${wosColor};font-size:10px;background:${cellBg};font-weight:${bold}"${isGapWeek ? ` title="Gap: WOS ${_invFmt1(wos)} < Opt WOS ${_invFmt1(_optWos)}"` : ''}>${wosTxt}</td>`;
      } else {
        wosCells += `<td style="color:#bbb;font-size:10px;background:#f8f0fb"> - </td>`;
      }
      // Open Orders (all customers)  -  W1 = Wk0+Wk1 merged, W2-W26 = Wk2-Wk26
      if (_opn && _opn.length === 26) {
        const ov = _opn[i];
        opnTot += ov;
        opnCells += ov > 0
          ? `<td id="opn-cell-${safeIdForTotal}-${i}" style="color:#00695c;font-weight:600;font-size:10px;background:#e0f2f1;cursor:help" title="${(r.cust||'').replace(/"/g,'&quot;')}: ${fmtN(ov)} units\n(loading cancel date...)">${fmtN(ov)}</td>`
          : `<td id="opn-cell-${safeIdForTotal}-${i}" style="color:#bbb;font-size:10px;background:#e0f2f1"> - </td>`;
      } else {
        opnCells += `<td id="opn-cell-${safeIdForTotal}-${i}" style="color:#bbb;font-size:10px;background:#e0f2f1"> - </td>`;
      }
    }
    // Total cells (sum for beg/prj/rcv/opn; WOS total is not meaningful so leave as dash)
    begCells += _beg ? `<td style="font-weight:700;color:#6d4c00;background:#fffbea">${fmtN(Math.round(begTot))}</td>` : `<td style="color:#bbb;background:#fffbea"> - </td>`;
    prjCells += _prj ? `<td style="font-weight:700;color:#2e7d32;background:#f1f8e9">${fmtN(Math.round(prjTot))}</td>` : `<td style="color:#bbb;background:#f1f8e9"> - </td>`;
    rcvCells += _rcv ? `<td style="font-weight:700;color:#1565c0;background:#f0f7ff">${fmtN(Math.round(rcvTot))}</td>` : `<td style="color:#bbb;background:#f0f7ff"> - </td>`;
    opnCells += (_opn && _opn.length === 26)
      ? `<td style="font-weight:700;color:#00695c;background:#e0f2f1">${fmtN(opnTot)}</td>`
      : `<td style="color:#bbb;background:#e0f2f1"> - </td>`;
    wosCells += `<td style="color:#bbb;background:#f8f0fb" title="WOS total is not meaningful"> - </td>`;

    // -- Gap analysis banner ---------------------------------------------
    // Only fires for Replen items.  Non-Replen (ISO, phase-out, etc.) skips
    // the banner entirely  -  gap alerts don't apply to those items.
    let gapBannerHtml = '';
    if (_hasInvFlow && _optWos > 0 && _isReplen) {
      const optWosStr = _invFmt1(_optWos);
      const nextRcptStr = _gap.nextRcptDate
        ? _gap.nextRcptDate.toLocaleDateString('en-US', { month:'short', day:'numeric', year:'numeric' })
        : 'unknown';
      const nextRcptWk = (_gap.nextRcptWeekIdx >= 0 && _gap.nextRcptWeekIdx <= 25)
        ? `(W${_gap.nextRcptWeekIdx + 1})`
        : _gap.nextRcptWeekIdx > 25 ? '(beyond W26)' : '';
      if (_gap.weeks.length === 0) {
        gapBannerHtml = `
          <div style="margin-top:6px;padding:6px 10px;background:#e8f5e9;border:1px solid #a5d6a7;border-radius:4px;font-size:11px;color:#1b5e20;">
            \u2713 <b>No gaps:</b> all weeks through next receipt ${nextRcptStr} ${nextRcptWk} maintain  ${optWosStr} WOS (Opt WOS).
          </div>`;
      } else {
        const invMgmtUrl = `https://pim.quickbase.com/db/bpd24h9wy?a=dbpage&pageID=52&mstyle=${encodeURIComponent(r.mstyle || '')}`;
        gapBannerHtml = `
          <div style="margin-top:6px;padding:6px 10px;background:#ffebee;border:1px solid #ef9a9a;border-radius:4px;font-size:11px;color:#b71c1c;">
            &#x26a0;&#xfe0f; <b>Inventory Gap:</b> ${_gap.weeks.length} week${_gap.weeks.length === 1 ? '' : 's'} below Opt WOS (${optWosStr})
            before next receipt ${nextRcptStr} ${nextRcptWk}.
            Moving up open POs may close this gap --
            <a href="${invMgmtUrl}" target="_blank" style="color:#b71c1c;font-weight:600;">View in Inventory Manager &rarr;</a>
          </div>`;
      }
    } else if (_hasInvFlow && !_isReplen) {
      gapBannerHtml = `
        <div style="margin-top:6px;padding:4px 10px;background:#fafafa;border:1px solid #e0e0e0;border-radius:4px;font-size:10px;color:#888;font-style:italic;">
          Gap analysis only runs on Replen items (PT Item Status: ${(r.item_status || 'unknown').replace(/[<>&]/g, c => ({'<':'&lt;','>':'&gt;','&':'&amp;'})[c])}).
        </div>`;
    } else if (_hasInvFlow && _optWos <= 0) {
      gapBannerHtml = `
        <div style="margin-top:6px;padding:4px 10px;background:#fafafa;border:1px solid #e0e0e0;border-radius:4px;font-size:10px;color:#888;font-style:italic;">
          Gap analysis disabled  -  no Opt WOS set for this mstyle in Inventory Flow.
        </div>`;
    }

    const _ltWks  = r.inv_flow_lt_wks  || 0;
    const _moq    = r.inv_flow_moq     || 0;

    // -- Inventory position cards (always from Inv Flow, no QB fetch needed) --
    const _cardQtyOh  = _beg ? Math.round(_beg[0]) : 0;
    const _cardQtyIw  = _suppPos.reduce((s, p) => s + p.iwQty, 0);
    const _cardQtyIt  = _suppPos.reduce((s, p) => s + p.itQty, 0);
    const _cardPrjAvg = _prj ? (_prj.reduce((s, v) => s + v, 0) / 26) : 0;
    const _cardOhWos  = (_beg && _prj) ? _wosForward(_beg[0], _prj, 0) : 0;
    const _cardOoQty  = _cardQtyIw + _cardQtyIt;
    const _cardOoWos  = _cardPrjAvg > 0 ? (_cardQtyOh + _cardOoQty) / _cardPrjAvg : 0;
    const _cardNextRcpt = r.next_rcpt_dt
      ? new Date(r.next_rcpt_dt + 'T12:00:00').toLocaleDateString('en-US', { month:'short', day:'numeric', year:'numeric' })
      : '';
    const _cardNextAvlRcpt = r.inv_flow_next_rcpt
      ? new Date(r.inv_flow_next_rcpt + 'T12:00:00').toLocaleDateString('en-US', { month:'short', day:'numeric', year:'numeric' })
      : '';
    const _cwc = w => w === 0 ? '#bbb' : w < 3 ? '#c62828' : w < 8 ? '#e65100' : w < 16 ? '#1b5e20' : '#f57f17';
    const _cwt = w => w === 0 ? '0' : w.toFixed(1) + ' wks';
    const _cfmt = n => Math.round(n).toLocaleString('en-US');
    const _ccard = (lbl, val, col = '#222', tip = '') =>
      `<div style="background:#fff;border:1px solid #e0e0e0;border-radius:5px;padding:5px 10px;"${tip ? ` title="${tip.replace(/"/g,'&quot;')}"` : ''}>
        <div style="font-size:10px;color:#888;font-weight:600;white-space:nowrap;margin-bottom:2px;">${lbl}</div>
        <div style="font-size:18px;font-weight:700;color:${col};white-space:nowrap;">${val}</div>
      </div>`;
    const _cdivider = `<div style="width:1px;background:#e0e0e0;align-self:stretch;margin:0 4px;flex:none;"></div>`;
    const _atsNow   = r.inv_flow_ats_now    || 0;
    const _atsOh    = r.inv_flow_ats_oh     || 0;
    const _atsOo    = r.inv_flow_ats_oo     || 0;
    const _atsOhWos = r.inv_flow_ats_oh_wos || 0;
    const _atsOoWos = r.inv_flow_ats_oo_wos || 0;
    const _hasAts   = _atsNow > 0 || _atsOh > 0 || _atsOo > 0;
    const _invCardsHtml = _hasInvFlow ? `
      <div style="border-top:1px solid #e0e0e0;padding-top:8px;margin-top:6px;display:flex;gap:6px;flex-wrap:nowrap;align-items:stretch;overflow-x:auto;">
        ${_ccard('Qty OH',    _cfmt(_cardQtyOh), '#37474f', 'P+P warehouse on-hand (Inv Flow Wk1 beginning balance)')}
        ${_ccard('Qty I/W',   _cfmt(_cardQtyIw), '#37474f', 'In production / in-work (from open supplier POs)')}
        ${_ccard('Qty I/T',   _cfmt(_cardQtyIt), '#37474f', 'In transit to warehouse (from open supplier POs)')}
        ${_cardNextRcpt ? _ccard('Next Rcpt', _cardNextRcpt, '#c62828', 'Next scheduled supplier receipt date') : ''}
        ${_cardNextAvlRcpt ? _ccard('Next Avl Rcpt Dt', _cardNextAvlRcpt, '#c62828', 'Next available receipt date (from Inventory Flow)') : ''}
        ${_cdivider}
        ${_ccard('OH WOS',    _cwt(_cardOhWos),  _cwc(_cardOhWos),  'Weeks of supply on-hand only')}
        ${_ccard('OH+OO WOS', _cwt(_cardOoWos),  _cwc(_cardOoWos),  'Weeks of supply: OH + I/T + I/W pipeline')}
        ${_hasAts ? _cdivider : ''}
        ${_hasAts ? _ccard('ATS Now',       _cfmt(_atsNow),   '#1565c0', 'Available to ship today (Inv Flow ATS_Now)') : ''}
        ${_hasAts ? _ccard('ATS OH',        _cfmt(_atsOh),    '#1565c0', 'ATS based on on-hand only (Inv Flow ATS_OH)') : ''}
        ${_hasAts ? _ccard('ATS OH+OO',     _cfmt(_atsOo),    '#1565c0', 'ATS OH + open supplier orders (Inv Flow ATS_OH_OO)') : ''}
        ${_hasAts ? _cdivider : ''}
        ${_hasAts ? _ccard('ATS OH WOS',    _cwt(_atsOhWos),  _cwc(_atsOhWos),  'ATS OH / Prj/Wk (Inv Flow ATS_WOS_OH)') : ''}
        ${_hasAts ? _ccard('ATS OH+OO WOS', _cwt(_atsOoWos),  _cwc(_atsOoWos),  'ATS OH+OO / Prj/Wk (Inv Flow ATS_WOS_OH_OO)') : ''}
      </div>` : '';

    invFlowSectionHtml = `
      <div style="margin:12px 12px 0 12px;">
        <div style="font-weight:700;font-size:12px;color:#333;margin-bottom:4px;padding-left:2px;display:flex;align-items:center;gap:12px;flex-wrap:wrap;">
          <span> Inventory Flow</span>
          ${_hasInvFlow && _optWos > 0 ? `<span style="font-weight:400;font-size:10px;color:#555;">Opt WOS: <b>${_invFmt1(_optWos)}</b></span>` : ''}
          ${_hasInvFlow && _gap.nextRcptDate ? `<span style="font-weight:400;font-size:10px;color:#555;">Next avl receipt date: <b>${_gap.nextRcptDate.toLocaleDateString('en-US', { month:'short', day:'numeric' })}</b></span>` : ''}
          ${_hasInvFlow && _ltWks > 0 ? `<span style="font-weight:400;font-size:10px;color:#555;">LT: <b>${Math.round(_ltWks)} wks</b></span>` : ''}
          ${_hasInvFlow && _moq > 0 ? `<span style="font-weight:400;font-size:10px;color:#555;">MOQ: <b>${_moq.toLocaleString()}</b></span>` : ''}
          ${_hasInvFlow ? '' : (_invFlowPromise ? '<span style="font-weight:400;font-size:10px;color:#888;">Loading inventory balances...</span>' : '<span style="font-weight:400;font-size:10px;color:#888;">(no QB Inventory Flow row for this mstyle)</span>')}
        </div>
        <div style="overflow-x:auto;">
          <table class="dtbl">
            <tr>${_ifHdrCells}</tr>
            <tr>${begCells}</tr>
            <tr>${prjCells}</tr>
            <tr>${rcvCells}</tr>
            <tr>${opnCells}</tr>
            <tr>${wosCells}</tr>
          </table>
        </div>
        ${gapBannerHtml}
        ${_invCardsHtml}
      </div>`;
  }

  // L26W Orders & Shipments history ----------------------------------------
  // W1_DATE is the most recent Sunday (discovered from QB MAN_PRJ field labels
  // "MM DD W1").  Historical week N (1-indexed back from Last Wk) started N
  // weeks before W1.  Display as "M/D"  -  planners think in dates not "N w ago".
  function _fmtHistDate(weeksBeforeW1) {
    if (!W1_DATE) return weeksBeforeW1 + 'w ago';
    const d = new Date(W1_DATE.getTime());
    d.setDate(d.getDate() - weeksBeforeW1 * 7);
    return (d.getMonth() + 1) + '/' + d.getDate();
  }
  const histShp = r.hist_shp || [];
  // -- History stitching for manual switchover new styles --------------------
  // If this row is the RECEIVING side of a manual switchover, blend in the
  // base style's order history for the pre-switchover weeks so the demand
  // signal spans the full window.  hist_ord is oldest→newest (index 0 = 26
  // weeks ago, index 25 = last week).
  let histOrd = (r.hist_ord || []).slice();
  const _swRevEntry = MANUAL_SWITCHOVER_REVERSE.get(r.key);
  if (_swRevEntry) {
    const baseRec = ALL_RECORDS.find(b => b.key === _swRevEntry.fromKey);
    if (baseRec && baseRec.hist_ord) {
      const baseOrd = baseRec.hist_ord;
      if (_swRevEntry.date) {
        // Find the stitch index: how many weeks ago was the switchover date?
        const today    = new Date(); today.setHours(0,0,0,0);
        const dow      = today.getDay();
        const lastMon  = new Date(today); lastMon.setDate(today.getDate() - (dow===0?6:dow-1));
        const cutoff   = new Date(_swRevEntry.date); cutoff.setHours(0,0,0,0);
        const weeksAgo = Math.round((lastMon - cutoff) / (7 * 86400000));
        // stitch_idx: last hist_ord index that belongs to the BASE style
        // hist_ord[25] = last week (weeksAgo=1), hist_ord[25-k] = k+1 weeks ago
        const stitchIdx = Math.min(25, Math.max(-1, 25 - weeksAgo));
        for (let _i = 0; _i <= stitchIdx; _i++) {
          if ((baseOrd[_i] || 0) > 0) histOrd[_i] = baseOrd[_i];
          else if (!histOrd[_i]) histOrd[_i] = 0;
        }
      } else {
        // No date set: fill zeros in new style's history from base style
        for (let _i = 0; _i < histOrd.length; _i++) {
          if (!histOrd[_i] && (baseOrd[_i] || 0) > 0) histOrd[_i] = baseOrd[_i];
        }
      }
    }
  }
  // -- F60 EC-variant history backfill ---------------------------------------
  // EC variant rows have all-zero QB order history because Amazon orders
  // against the parent style (e.g. FF35147), not the EC variant (FF35147EC).
  // The forecaster inherits the parent's history internally (F60), but never
  // writes it back to QB.  Mirror that here so the detail panel shows the
  // same demand signal the AI used rather than a misleading wall of zeros.
  let _ecHistNote = '';   // set below if backfill fires; used in history header
  if (!histOrd.some(v => v > 0) && /EC$/i.test(r.mstyle || '')) {
    const _ecBase    = r.mstyle.replace(/EC$/i, '');
    const _ecBaseKey = r.key.replace(r.mstyle, _ecBase);
    const _ecBaseRec = ALL_RECORDS.find(b => b.key === _ecBaseKey);
    if (_ecBaseRec) {
      if (_ecBaseRec.hist_ord && _ecBaseRec.hist_ord.some(v => v > 0)) {
        histOrd = _ecBaseRec.hist_ord.slice();
        _ecHistNote = _ecBase;
      }
      // Backfill shipments too if the EC variant has none
      if (!histShp.some(v => v > 0) && _ecBaseRec.hist_shp && _ecBaseRec.hist_shp.some(v => v > 0)) {
        _ecBaseRec.hist_shp.forEach((v, i) => { histShp[i] = v; });
      }
    }
  }

  const atsHist = r.ats_hist || [];
  // Fast path: if ATS isn't attached yet, kick off a single-mstyle fetch in
  // parallel and re-render this panel when it lands.  Typical latency ~300 ms
  // vs the 30-120 sec bulk attach.  The bulk attach still runs in the
  // background so other rows fill in passively.
  if (!r.ats_hist) {
    _fetchAtsForMstyle(r).then(arr => {
      if (!arr) return;
      if (_openDetailKey !== key) return;
      el.dataset.loaded = '';
      el.style.display = 'none';
      toggleDetail(key);
    });
  }
  let histHtml  = '';
  if (histShp.length || histOrd.length || atsHist.length) {
    let histHdrCells = '<th class="row-label" style="width:1%;white-space:nowrap"></th>';
    let ordCells = '<td class="row-label" style="color:#e65100;font-weight:600;white-space:nowrap">Orders</td>';
    let shpCells = '<td class="row-label" style="color:#6a1b9a;font-weight:600;white-space:nowrap">Shipments</td>';
    let atsCells = '<td class="row-label" style="color:#00695c;font-weight:600;white-space:nowrap">ATS Inv Hist</td>';
    let shpTot = 0, ordTot = 0, atsTot = 0;
    for (let i = 25; i >= 0; i--) {
      const label = _fmtHistDate(26 - i);
      histHdrCells += `<th style="font-size:10px;font-weight:normal;white-space:normal;min-width:0;padding:2px 3px;width:1%">${label}</th>`;
      const sv = histShp[i] || 0;
      shpCells += `<td style="${sv === 0 ? 'color:#bbb' : 'color:#6a1b9a;font-weight:600'}">${fmtN(sv)}</td>`;
      const ov = histOrd[i] || 0;
      ordCells += `<td style="${ov === 0 ? 'color:#bbb' : 'color:#e65100;font-weight:600'}">${fmtN(ov)}</td>`;
      const av = atsHist[i] || 0;
      atsCells += `<td style="${av === 0 ? 'color:#bbb' : 'color:#00695c'}">${fmtN(av)}</td>`;
      shpTot += sv;  ordTot += ov;  atsTot += av;
    }
    histHdrCells += '<th style="min-width:0;padding:2px 3px;width:1%">Total</th><th style="color:#888;font-weight:600;min-width:0;padding:2px 3px;width:1%">Avg/Wk</th>';
    ordCells += `<td style="font-weight:700;color:#e65100">${fmtN(ordTot)}</td>`;
    ordCells += `<td style="font-weight:700;color:#e65100">${fmtN(Math.round(ordTot / 26))}</td>`;
    shpCells += `<td style="font-weight:700;color:#6a1b9a">${fmtN(shpTot)}</td>`;
    shpCells += `<td style="font-weight:700;color:#6a1b9a">${fmtN(Math.round(shpTot / 26))}</td>`;
    atsCells += `<td style="color:#bbb"> - </td>`;
    atsCells += `<td style="font-weight:700;color:#00695c">${fmtN(Math.round(atsTot / 26))}</td>`;
    // DI Orders row — yellow highlight for Direct Import weeks (FID 1613)
    const _diOrd = r.di_ord || [];
    const _hasDi = _diOrd.some(v => v > 0);
    let diCells = '';
    let diTot = 0;
    if (_hasDi) {
      const _diTip = 'Direct Import order (Amazon DIRECT, acct 61865) — ships ex-factory from overseas. Included in AI forecast baseline.';
      diCells = `<td class="row-label" style="color:#827717;font-weight:600;white-space:nowrap;background:#fffde7">DI Orders</td>`;
      for (let i = 25; i >= 0; i--) {
        const dv = _diOrd[i] || 0;
        diTot += dv;
        if (dv > 0) {
          diCells += `<td style="background:#fffde7;color:#827717;font-weight:700;cursor:default" title="${_diTip}">${fmtN(dv)}</td>`;
        } else {
          diCells += `<td style="background:#fffde7;color:#ccc">-</td>`;
        }
      }
      diCells += `<td style="background:#fffde7;font-weight:700;color:#827717">${fmtN(diTot)}</td>`;
      diCells += `<td style="background:#fffde7;font-weight:700;color:#827717">${fmtN(Math.round(diTot / 26))}</td>`;
    }
    histHtml = `
    <div style="overflow-x:auto;padding:4px 12px 8px 12px;border-top:2px solid #ede7f6;">
      <div style="font-size:11px;color:#555;font-weight:600;padding:4px 0 2px 0;">L26W History${_ecHistNote ? ` <span style="font-weight:400;color:#1565c0;">(inherited from parent style ${_ecHistNote.replace(/[<>&]/g,c=>({'<':'&lt;','>':'&gt;','&':'&amp;'})[c])})</span>` : ''}</div>
      <table class="dtbl">
        <tr>${histHdrCells}</tr>
        <tr>${ordCells}</tr>
        ${_hasDi ? `<tr>${diCells}</tr>` : ''}
        <tr>${shpCells}</tr>
        <tr id="cxld-row-${safeIdForTotal}"></tr>
        <tr>${atsCells}</tr>
      </table>
    </div>`;
  }

  // narrative may be \n-joined (viewer.py local) or <br>/<br><br>-joined (QB ai_analysis field).
  // First collapse double-<br> to a single \n, then convert any remaining single <br> to \n
  // so every bullet point becomes its own list item regardless of how QB serialised the text.
  let _narParts = (r.narrative || '')
    .replace(/<br\s*\/?>\s*<br\s*\/?>/gi, '\n')
    .replace(/<br\s*\/?>/gi, '\n')
    .split('\n').filter(s => s.trim());
  // Seasonal / off-price bullet: inject into AI Analysis for any item that is
  // A: OffPrice OR has a Season tag.  These items have lumpy demand and need
  // cadence context so planners know when to expect the next buy window.
  // A: Promo + Season gets an extra note that a manual seasonal buy is likely.
  const _showSeasonBullet = r.is_offprice || !!r.season_tag;
  if (_showSeasonBullet) {
    const _sp2   = _analyzeSeasonalPattern(r.hist_ord || []);
    const _lyT2  = (r.ly_ord || []).reduce((a, b) => a + (b || 0), 0);
    const _lyWks2 = (r.ly_ord || []).map((v, i) => (v||0) > 0 ? 'W'+(i+1) : null).filter(Boolean);
    // Next-order urgency
    let _nxt2 = '';
    if (_sp2.nextExpectedWk !== null) {
      if (_sp2.nextExpectedWk <= 0)
        _nxt2 = ' -- <b style="color:#c62828">order overdue ~' + Math.abs(_sp2.nextExpectedWk) + ' wk</b>';
      else if (_sp2.nextExpectedWk <= 4)
        _nxt2 = ' -- <b style="color:#e65100">next order ~' + _sp2.nextExpectedWk + ' wks out</b>';
      else
        _nxt2 = ' -- next order ~' + _sp2.nextExpectedWk + ' wks out';
    } else if (_sp2.events.length === 1) {
      _nxt2 = ' -- only 1 event in L26W, need more history';
    }
    const _ly2   = _lyT2 > 0 ? '; LY' + (_lyWks2.length ? ' (' + _lyWks2.join(', ') + ')' : '') + ': ' + _lyT2.toLocaleString() + 'u' : '';
    const _gap2  = _sp2.avgGapWks !== null ? ', ~' + _sp2.avgGapWks + ' wk avg gap' : '';
    const _last2 = _sp2.wksSinceLast !== null ? ', ' + _sp2.wksSinceLast + ' wks since last order' : '';
    // Label: surface season name when available
    let _lbl2;
    if (r.is_offprice && r.season_tag)
      _lbl2 = '<b>Off-price / ' + r.season_tag + ':</b>';
    else if (r.is_offprice)
      _lbl2 = '<b>Off-price account:</b>';
    else
      _lbl2 = '<b>Seasonal item (' + r.season_tag + '):</b>';
    // A: Promo + Season note -- retailer buys manually to cover the in-season window
    const _promoNote = (r.is_seasonal && !r.is_offprice && r.season_tag)
      ? ' A: Promo + ' + r.season_tag + ' season -- retailer will likely place a manual buy to cover the in-season window; project accordingly.'
      : '';
    _narParts.push(_lbl2 + ' ' + _sp2.events.length + ' order event' + (_sp2.events.length !== 1 ? 's' : '') + ' L26W' + _gap2 + _last2 + _nxt2 + _ly2 + '.' + _promoNote);
  }
  // _narUlId: stable element id used by _loadAmzDcInv to inject/replace the
  // live DC inventory bullet after the panel renders.
  const _narUlId = 'ai-bullets-' + safeIdForTotal;
  const _narWrapId = 'ai-narr-wrap-' + safeIdForTotal;
  const _narDivStyle = 'padding:8px 12px;background:#f5f5f5;border-top:1px solid #ddd;font-size:12px;line-height:1.5;color:#333;';
  const _narHdrHtml  = '<div style="font-weight:700;font-size:11px;text-transform:uppercase;letter-spacing:0.5px;color:#555;margin-bottom:5px;">AI Analysis</div>';
  // Season tag chip -- shown at top of detail only when a value is set.
  const seasonHtml = r.season_tag
    ? `<div style="margin:8px 12px 0 12px;">
        <span style="display:inline-block;padding:3px 10px;background:#ede7f6;color:#4a148c;
                     border:1px solid #ce93d8;border-radius:12px;font-size:11px;font-weight:600;
                     letter-spacing:0.3px;">&#127810; Season: ${escHtml(r.season_tag)}</span>
       </div>`
    : '';

  const narrativeHtml = _narParts.length
    ? '<div id="' + _narWrapId + '" style="' + _narDivStyle + '">' +
      _narHdrHtml +
      '<ul id="' + _narUlId + '" style="margin:2px 0;padding-left:16px;">' +
      _narParts.map(p => '<li style="margin-bottom:4px;">' + p + '</li>').join('') +
      '</ul></div>'
    // No narrative yet: render an empty AI Analysis shell so _loadAmzDcInv
    // (Amazon) and _loadRtlPos (retailers) have a <ul> to inject bullets into.
    : '<div id="' + _narWrapId + '" style="' + _narDivStyle + '">' +
      _narHdrHtml +
      '<ul id="' + _narUlId + '" style="margin:2px 0;padding-left:16px;"></ul>' +
      '</div>';

  const safeKey = r.key.replace(/'/g, "&#39;");
  const safeId   = r.key.replace(/[^a-zA-Z0-9]/g, '_');
  const flagCls2 = 'flag-btn' + (r.flagged ? ' flagged' : '');
  // Comment block  -  Flag/Mgr conversation thread (planner <-> inventory mgr).
  // 25% Add-a-Comment input | 75% Comment History (filtered to NON-AI
  // comments only).  Tell-AI dialogue lives in its own block above.
  const autoProjectBtn = CFG.FID.AUTO_PROJECT ? `
  <div style="margin:6px 12px 0 12px;">
    <button id="autoproj-${safeId}" onclick="toggleAutoProject('${safeKey}')"
      title="Auto Project: when ON, manual projections are automatically replaced with AI projections every time a forecast is run"
      style="padding:5px 12px;border:1px solid ${r.auto_project ? '#1b5e20' : '#bbb'};border-radius:4px;background:${r.auto_project ? '#e8f5e9' : '#f5f5f5'};color:${r.auto_project ? '#1b5e20' : '#555'};font-size:11px;font-weight:${r.auto_project ? '700' : '400'};cursor:pointer;">
      &#x1F504; Auto Project${r.auto_project ? ': ON' : ': OFF'}
    </button>
  </div>` : '';


  const commentBlock = `
  <div style="margin:10px 12px 12px 12px;padding:12px;background:#f7f9fc;border:1px solid #d8dce3;border-radius:6px;">
    <div style="margin-bottom:8px;">
      <button id="flg-${safeId}" class="${flagCls2}" onclick="toggleFlag('${safeKey}')" title="Toggle the QB Flagged boolean for this projection">&#x2691; Flag Projection</button>
    </div>
    <div style="display:flex;gap:14px;align-items:flex-start;">
      <!-- LEFT: Add a Comment (25%)  -  for planner <-> mgr dialogue -->
      <div style="flex:0 0 25%;min-width:0;">
        <div style="font-weight:600;color:#8b2252;margin-bottom:6px;font-size:12px;">Add a Comment <span style="font-weight:400;color:#999;font-size:10px;"> -  for inv mgr</span></div>
        <textarea id="cmt-text-${safeKey}" oninput="autoFlagOnComment('${safeKey}')" placeholder="Write a comment for the mgr review..." style="width:100%;min-height:80px;padding:6px 8px;border:1px solid #ccc;border-radius:4px;font-size:12px;font-family:inherit;resize:vertical;box-sizing:border-box;"></textarea>
        <div style="display:flex;align-items:center;gap:6px;margin-top:6px;flex-wrap:wrap;">
          ${_USER_IS_PLANNER
            ? `<label style="font-size:11px;color:#616161;display:flex;align-items:center;gap:4px;cursor:pointer;">
                 <input type="checkbox" id="cmt-fyi-${safeKey}" onchange="autoFlagOnComment('${safeKey}')" style="cursor:pointer;"> Mark as FYI
               </label>`
            : `<label style="font-size:11px;color:#555;">Type:
                 <select id="cmt-flag-${safeKey}" style="font-size:11px;padding:3px 6px;border:1px solid #ccc;border-radius:3px;margin-left:4px;">
                   <option value="Needs Action" ${!r.planner_reply_pending ? 'selected' : ''} style="color:#1565c0;font-weight:600;">Needs Action</option>
                   <option value="Manager Response" ${r.planner_reply_pending ? 'selected' : ''} style="color:#e65100;font-weight:600;">Manager Response</option>
                   <option value="FYI" style="color:#616161;">FYI</option>
                   <option value="Resolved">Resolved</option>
                 </select>
               </label>`
          }
          <button id="cmt-btn-${safeKey}" onclick="addComment('${safeKey}')" style="padding:5px 14px;background:#8b2252;color:#fff;border:none;border-radius:4px;cursor:pointer;font-weight:600;font-size:11px;">Save</button>
        </div>
        <div id="cmt-msg-${safeKey}" style="font-size:11px;color:#666;margin-top:4px;"></div>
      </div>
      <!-- RIGHT: Mgr/Flag comment history (75%)  -  non-AI comments only -->
      <div style="flex:1 1 75%;min-width:0;">
        <div style="font-weight:600;color:#8b2252;margin-bottom:6px;font-size:12px;display:flex;align-items:center;justify-content:space-between;">
          <span> Comment History <span style="font-weight:400;color:#999;font-size:10px;">  -  last 90 days, oldest first  |  planner <-> mgr</span></span>
          <button onclick="loadCommentHistory('${safeKey}', true)" title="Refresh from Quickbase" style="font-size:10px;padding:2px 8px;border:1px solid #ccc;background:#fff;border-radius:3px;cursor:pointer;">&#x21BB;</button>
        </div>
        <div id="cmt-hist-${safeKey}" style="max-height:180px;overflow-y:auto;border:1px solid #e8d5dc;border-radius:4px;background:#fffafd;padding:6px 8px;font-size:11px;color:#999;font-style:italic;">
          Loading...
        </div>
      </div>
    </div>
  </div>`;

  // Per-row edit toolbar  -  gives the planner Excel-style bulk operations on
  // the 26 MAN cells without leaving the detail pane.
  const editToolbar = `
    <div class="edit-tools" data-key="${r.key.replace(/"/g,'&quot;')}">
      <span style="font-weight:600;color:#333;">Edit MAN:</span>
      <button class="et-btn stage-ai"  onclick="stageFromSource('${safeKey}','ai')"
              title="Copy 26 weeks of AI Forecast into the editable cells as unsaved edits (yellow). Tweak as needed, then click Save All to write to Quickbase.">Use AI</button>
      <button class="et-btn stage-sug" onclick="stageFromSource('${safeKey}','suggested')"
              title="Copy 26 weeks of Suggested values into the editable cells as unsaved edits (yellow). Tweak as needed, then click Save All to write to Quickbase.">Use Sugg</button>
      <button class="et-btn fill-all"  onclick="fillRowFromFocused('${safeKey}','all')"
              title="Set every week (W1-W26) to the value of the cell you most recently clicked.">Fill All ></button>
      <button class="et-btn fill-all"  onclick="fillRowFromFocused('${safeKey}','right')"
              title="Set the focused cell and every cell to its right to the focused cell's value. Cells to the left are untouched. (Ctrl+R)">Fill Right ></button>
      <button class="et-btn reset-row" onclick="resetRow('${safeKey}')"
              title="Revert every week back to its original QB-loaded value. Drops all unsaved edits for this record only.">Reset</button>
      <button class="et-btn clear-all" onclick="fillRowConst('${safeKey}', 0)"
              title="Set every week to 0.">Zero All</button>
      <button class="et-btn save-row" onclick="saveRecordEdits('${safeKey}')"
              title="Save only this record's unsaved edits to Quickbase. Other records' edits stay untouched.">Save &#x2713;</button>
      <span class="et-tip">Tip: paste from Excel into any cell to distribute | Ctrl+R = fill right | Enter = next cell</span>
    </div>`;

  //  Tell-AI block: planner explains adjustment, AI proposes 26-week diff,
  // planner reviews + applies to MAN cells.  Two-column layout  -  left 33%
  // is the AI input + preview, right 67% shows ONLY prior AI-Adjusted
  // dialogue (planner <-> AI).  Flag/mgr comments live in the comment block
  // below  -  kept separate per planner UX feedback (different conversations,
  // different audiences).
  const tellAiBlock = `
  <div style="margin:10px 12px 0 12px;padding:10px 12px;background:#f0f7ff;border:1px solid #1565c0;border-radius:6px;">
    <div style="font-weight:600;color:#1565c0;margin-bottom:6px;font-size:12px;display:flex;align-items:center;gap:6px;">
      &#x1F4C5; Event Notification
      <span style="font-weight:400;color:#666;font-size:11px;"> - notify the AI of upcoming events (promos, launches, seasonal pushes) and it will front-load orders in advance</span>
    </div>
    <div style="display:flex;gap:14px;align-items:flex-start;">
      <!-- LEFT: Event notification textarea + preview (25%, matches comment block) -->
      <div style="flex:0 0 25%;min-width:0;">
        <textarea id="ai-adj-text-${safeId}"
                  placeholder="e.g. Zero W13-W26 transitioning to EC Suffix"
                  rows="4"
                  style="width:100%;padding:6px 8px;border:1px solid #1565c0;border-radius:4px;font-size:12px;font-family:inherit;resize:vertical;box-sizing:border-box;"></textarea>
        <div style="display:flex;gap:8px;margin-top:6px;align-items:center;flex-wrap:wrap;">
          <button onclick="previewAiAdjustment('${safeKey}')" style="font-size:11px;padding:5px 14px;background:#1565c0;color:#fff;border:none;border-radius:4px;cursor:pointer;font-weight:600;">Preview Adjustments</button>
        </div>
        <div id="ai-adj-preview-${safeId}"></div>
      </div>
      ${CFG.AI_COMMENTS_TID ? `
        <!-- RIGHT: AI Adjustment History - only rendered when table is configured -->
        <div style="flex:1 1 75%;min-width:0;">
          <div style="font-weight:600;color:#1565c0;margin-bottom:6px;font-size:12px;display:flex;align-items:center;justify-content:space-between;">
            <span>AI Adjustment History <span style="font-weight:400;color:#999;font-size:10px;"> - last 6 months, oldest first | planner <-> AI dialogue</span></span>
            <button onclick="loadCommentHistory('${safeKey}', true)" title="Refresh from Quickbase" style="font-size:10px;padding:2px 8px;border:1px solid #ccc;background:#fff;border-radius:3px;cursor:pointer;">&#x21BB;</button>
          </div>
          <div id="ai-hist-${safeKey}" style="max-height:200px;overflow-y:auto;border:1px solid #bbdefb;border-radius:4px;background:#fafdff;padding:6px 8px;font-size:11px;color:#999;font-style:italic;">
            Loading...
          </div>
        </div>
      ` : `<div id="ai-hist-${safeKey}" style="display:none;"></div>`}
    </div>
  </div>`;

  // -- COS / EC Switchover alert (base style only) ---------------------------
  // Shown when a COS or EC variant of this style has started receiving orders
  // or manual projections, signalling that this base style should be closed.
  const _variantMstyle   = SWITCHOVER_MAP.get(r.key);
  const _cosEcHtml       = _variantMstyle ? `
    <div id="switchover-alert-${safeKey}" style="margin:8px 12px 0 12px;padding:10px 14px;background:#fff8e1;border:2px solid #f9a825;border-radius:6px;font-size:12px;display:flex;align-items:center;gap:14px;flex-wrap:wrap;">
      <span style="font-size:18px;line-height:1;">&#x26A0;&#xFE0F;</span>
      <span style="flex:1;min-width:200px;">
        <b style="font-size:13px;color:#e65100;">Switchover Alert</b><br>
        Orders and projections have moved to <b>${_variantMstyle.replace(/[<>&]/g,c=>({'<':'&lt;','>':'&gt;','&':'&amp;'})[c])}</b>.
        This projection should be marked <b>CLOSED</b>.
        <span class="switchover-err" style="color:#c62828;margin-left:6px;"></span>
      </span>
      <button id="close-base-btn-${safeKey}" onclick="closeBaseStyle('${r.key.replace(/'/g,"\\'")}')"
        style="font-size:12px;padding:5px 14px;background:#e65100;color:#fff;border:none;border-radius:4px;cursor:pointer;font-weight:700;white-space:nowrap;">
        Mark as CLOSED
      </button>
    </div>` : '';

  // -- Manual switchover setup card (any style can be a base) ---------------
  // Shows on ALL rows so planners can configure a switchover from scratch,
  // and shows the current status when one is already configured.
  const _manSw      = MANUAL_SWITCHOVER_MAP.get(r.key);
  const _manSwRev   = MANUAL_SWITCHOVER_REVERSE.get(r.key);
  const _swChecked  = r.switchover_active ? 'checked' : '';
  const _swToVal    = (r.switchover_to_mstyle || '').replace(/"/g,'&quot;');
  const _swDateVal  = r.switchover_date ? r.switchover_date.slice(0,10) : '';
  const _esc        = s => (s||'').replace(/[<>&]/g,c=>({'<':'&lt;','>':'&gt;','&':'&amp;'})[c]);

  // Receiving-side banner (shown on the NEW style's row)
  const _receivingHtml = _manSwRev ? `
    <div style="margin:8px 12px 0 12px;padding:8px 14px;background:#e3f2fd;border:1px solid #90caf9;border-radius:6px;font-size:12px;color:#0d47a1;">
      &#x21C4; <b>Receiving switchover from ${_esc(_manSwRev.fromMstyle)}</b>
      ${_manSwRev.date ? '&mdash; effective ' + _manSwRev.date.toLocaleDateString('en-US',{month:'short',day:'numeric',year:'numeric'}) : ''}
      &mdash; pre-switchover order history is included in demand calculations.
    </div>` : '';

  const _manualSwitchoverCard = `
    <div id="sw-card-${safeKey}" style="margin:8px 12px 0 12px;padding:10px 14px;background:#fafafa;border:1px solid #e0e0e0;border-radius:6px;font-size:12px;">
      <div style="font-weight:700;font-size:12px;color:#444;margin-bottom:8px;">&#x21C4; Style Switchover</div>
      <div style="display:flex;align-items:center;gap:16px;flex-wrap:wrap;">
        <label style="display:flex;align-items:center;gap:6px;cursor:pointer;font-weight:600;color:#e65100;">
          <input type="checkbox" id="sw-active-${safeKey}" ${_swChecked}
            onchange="saveSwitchoverField('${r.key.replace(/'/g,"\\'")}','active',this.checked)"
            style="width:15px;height:15px;cursor:pointer;">
          Switchover Active
        </label>
        <label style="display:flex;align-items:center;gap:6px;">
          <span style="color:#555;">New MStyle:</span>
          <input type="text" id="sw-mstyle-${safeKey}" value="${_swToVal}" placeholder="e.g. BB38259"
            style="font-size:12px;padding:3px 6px;border:1px solid #ccc;border-radius:3px;width:110px;text-transform:uppercase;"
            onblur="saveSwitchoverField('${r.key.replace(/'/g,"\\'")}','mstyle',this.value)"
            onkeydown="if(event.key==='Enter')this.blur()">
        </label>
        <label style="display:flex;align-items:center;gap:6px;">
          <span style="color:#555;">Switch Date:</span>
          <input type="date" id="sw-date-${safeKey}" value="${_swDateVal}"
            style="font-size:12px;padding:3px 6px;border:1px solid #ccc;border-radius:3px;"
            onchange="saveSwitchoverField('${r.key.replace(/'/g,"\\'")}','date',this.value)">
        </label>
        <span id="sw-status-${safeKey}" style="font-size:11px;color:#888;"></span>
      </div>
      ${_manSw ? `<div style="margin-top:6px;font-size:11px;color:#1565c0;">
        &#x2713; Active: projections split at week
        <b>${_manSw.date ? _manSw.date.toLocaleDateString('en-US',{month:'short',day:'numeric'}) : '?'}</b>
        &mdash; base style owns weeks before, <b>${_esc(_manSw.toMstyle)}</b> owns weeks on/after.
      </div>` : ''}
    </div>`;

  // Hide the setup card on the variant/destination side — the planner only needs
  // to manage the switchover from the base style row. The receiving banner still
  // shows so the variant row makes clear where its history came from.
  const switchoverHtml = _cosEcHtml + _receivingHtml + (_manSwRev ? '' : _manualSwitchoverCard);

  // Issue 8: FD Status block  -  Future Development items often have no AI narrative
  // or projections yet; show a prominent metadata card so the panel isn't blank.
  const isFDRecord = (r.asin_status || '').trim().toUpperCase().startsWith('FD');
  const fdStatusHtml = isFDRecord ? `
    <div style="margin:8px 12px 0 12px;padding:10px 12px;background:#fff3f3;border:1px solid #ffcdd2;border-radius:6px;font-size:12px;color:#4a1010;">
      <div style="font-weight:700;font-size:12px;color:#c62828;margin-bottom:6px;">&#x26A0; Future Delete - Status @ Cust: <span style="font-weight:400">${(r.asin_status||'').replace(/[<>&]/g,c=>({'<':'&lt;','>':'&gt;','&':'&amp;'})[c])}</span></div>
      <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:4px 16px;font-size:11px;">
        <div><b>Customer:</b> ${(r.cust||' -').replace(/[<>&]/g,c=>({'<':'&lt;','>':'&gt;','&':'&amp;'})[c])}</div>
        <div><b>Mstyle:</b> ${(r.mstyle||' -').replace(/[<>&]/g,c=>({'<':'&lt;','>':'&gt;','&':'&amp;'})[c])}</div>
        <div><b>Brand:</b> ${(r.brand||' -').replace(/[<>&]/g,c=>({'<':'&lt;','>':'&gt;','&':'&amp;'})[c])}</div>
        <div><b>Item Status:</b> ${(r.item_status||' -').replace(/[<>&]/g,c=>({'<':'&lt;','>':'&gt;','&':'&amp;'})[c])}</div>
        <div><b>Inv Manager:</b> ${(r.inv_manager||' -').replace(/[<>&]/g,c=>({'<':'&lt;','>':'&gt;','&':'&amp;'})[c])}</div>
        <div><b>Last Ord:</b> ${r.last_ord_date||' -'}</div>
      </div>
      <div style="max-width:600px;font-size:11px;margin-top:4px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" title="${(r.desc||'').replace(/"/g,'&quot;')}"><b>Description:</b> ${(r.desc||' -').replace(/[<>&]/g,c=>({'<':'&lt;','>':'&gt;','&':'&amp;'})[c])}</div>
      <div style="margin-top:6px;font-size:10px;color:#c62828;font-style:italic;">&#x26A0; This item is scheduled for deletion. Orders typically stop ~4 weeks before the POG End Date. Review projections carefully - zero out weeks after the last expected order date.</div>
    </div>` : '';

  // -- PO / Manual Projection conflict alert ----------------------------------
  let poPrjAlertHtml = '';
  if (r.has_po_prj_conflict && r.po_prj_conflicts && r.po_prj_conflicts.length) {
    const _isOP = r.is_offprice;
    const _conflictLines = r.po_prj_conflicts.map(c => {
      if (c.poWk === c.prjWk) {
        return `W${c.poWk}: Open PO ${fmtN(c.poQty)} units  |  Manual Prj ${fmtN(c.prjQty)} units  (same week)`;
      } else {
        const gap = Math.abs(c.poWk - c.prjWk);
        return `Open PO ${fmtN(c.poQty)} units (W${c.poWk})  +  Manual Prj ${fmtN(c.prjQty)} units (W${c.prjWk})  -- ${gap} wk apart`;
      }
    });
    const _alertTitle = _isOP
      ? 'DUPLICATE DEMAND: Off-Price Open PO + Manual Projection overlap'
      : 'DUPLICATE DEMAND: Open PO and Manual Projection in the Same Week';
    const _alertDesc = _isOP
      ? 'Off-price POs ship the same or following week. A Manual Projection within 4 weeks of an Open PO double-counts that demand and overstates replenishment requirements. Zero out the overlapping Manual Projection weeks unless you expect a completely separate additional order.'
      : 'One or more weeks have BOTH a confirmed Open Customer PO and a Manual Projection. The PO is already a committed order -- the Manual Projection on top of it double-counts demand. Zero out the Manual Projection for conflicting weeks unless you expect a second independent order in the same week.';
    const _safeKeyForBtn = r.key.replace(/[^a-zA-Z0-9]/g, '_');
    poPrjAlertHtml = `
      <div id="po-prj-alert-${_safeKeyForBtn}" style="margin:10px 12px 0 12px;padding:14px 16px;background:#ffebee;border:3px solid #c62828;border-radius:6px;">
        <div style="font-weight:700;font-size:14px;color:#b71c1c;margin-bottom:6px;">&#9888; ${escHtml(_alertTitle)}</div>
        <div style="font-size:12px;color:#7f0000;line-height:1.6;margin-bottom:10px;">${escHtml(_alertDesc)}</div>
        <div style="font-size:11px;font-family:monospace;line-height:1.8;color:#7f0000;background:#fff8f8;padding:8px 12px;border-radius:4px;border:1px solid #ef9a9a;margin-bottom:10px;">
          ${_conflictLines.map(l => escHtml(l)).join('<br>')}
        </div>
        <button id="zero-dup-btn-${_safeKeyForBtn}"
          onclick="zeroDuplicateManPrj('${r.key.replace(/'/g, "\\'")}', '${_safeKeyForBtn}')"
          style="background:#c62828;color:#fff;border:none;padding:7px 16px;font-size:12px;font-weight:700;border-radius:4px;cursor:pointer;">
          Zero Duplicate MAN PRJ Weeks
        </button>
        <span style="font-size:10px;color:#b71c1c;margin-left:10px;">Changes will be staged -- click Save All to write to QB.</span>
      </div>`;
  }

  el.innerHTML = `<td colspan="22" style="padding:0">
    ${poPrjAlertHtml}
    ${autoProjectBtn}
    ${fdStatusHtml}
    ${seasonHtml}
    ${isAmazonRec ? _buildAmzInfoBlockHtml(r) : _buildPogBlockHtml(r)}
    ${narrativeHtml}
    <div style="overflow-x:auto;padding:8px 12px;">
      ${editToolbar}
      <table class="dtbl">
        <tr>${projHdrCells}</tr>
        <tr>${projCells}</tr>
        <tr>${aiCells}</tr>
        <tr>${sugCells}</tr>
        <tr><td colspan="29" style="padding:0;height:6px;background:transparent;border:none"></td></tr>
        <tr>${opnCells}</tr>
        <tr>${lyOrdCells}</tr>
        <tr>${lyShpCells}</tr>
      </table>
    </div>
    ${histHtml}
    ${invFlowSectionHtml}
    ${switchoverHtml}
    ${tellAiBlock}
    ${commentBlock}
  </td>`;
  el.dataset.loaded = '1';
  // Pull the 30-day comment history from QB and populate the right pane
  loadCommentHistory(r.key);
  // Live Amazon DC inventory health  -  fetches fresh SOH/OPO/WOS from the
  // Amazon Catalog table and injects/refreshes the bullet in AI Analysis.
  // Runs after innerHTML is set so the target <ul> already exists in the DOM.
  if (CFG.AMZ_CATALOG_TID && r.mstyle && isAmazonRec) _loadAmzDcInv(r, safeId);
  // Live retailer POS data  -  fetches POS sales, OH/instock, and distribution
  // metrics from the Retailer Sales table for non-Amazon customers.
  if (CFG.RTL_POS_TID && r.mstyle && !isAmazonRec) _loadRtlPos(r, safeId);
  // Only fetch cxld data once inv flow is done loading (avoids concurrent QB
  // calls that can stall the inv flow bulk scan).  If inv flow is already
  // attached (_hasInvFlow) or already resolved/null, start immediately.
  if (CFG.ORDER_HIST_TID) _loadOrdHistCxld(r, safeIdForTotal);
  if (CFG.ORDER_HIST_TID) _loadOpenOrderDetails(r, safeId);

  } catch (err) {
    // Something threw while building the detail HTML. Surface the error
    // inside the pane instead of leaving it blank.
    console.error('[toggleDetail] render error for key', JSON.stringify(key), err);
    el.innerHTML = `<td colspan="22" style="padding:12px 16px;background:#fff3e0;border-top:2px solid #ffb74d;">
      <b style="color:#e65100">&#x26A0; Detail panel render error</b><br>
      <span style="font-size:11px;color:#555;">Key: <code>${key || '(empty)'}</code><br>
      Error: <code>${(err && err.message) ? err.message.replace(/[<>&]/g, c=>({'<':'&lt;','>':'&gt;','&':'&amp;'})[c]) : String(err)}</code><br>
      Check the browser console (F12) for the full stack trace. Try refreshing with
      <a href="?nocache=1" style="color:#1565c0">?nocache=1</a> to rule out a stale cache.</span>
    </td>`;
    el.dataset.loaded = '1';
  }
}

// -- L26W Qty Cancelled (Exception Approval only) ----------------------------
// Queries Order History for rows where Exception_Approval=false and Qty_Cxld>0
// within the L26W window, buckets by Cancel_Date week, then fills the placeholder
// <tr id="cxld-row-..."> row in the history table.  Row is removed if no data.
// Orders where Exception_Approval=true are excluded (those are pre-approved exceptions
// that should not influence the L26W history view).
async function _loadOrdHistCxld(r, safeId) {
  const rowEl = document.getElementById('cxld-row-' + safeId);
  if (!rowEl || !CFG.ORDER_HIST_TID || !W1_DATE) return;

  // Pre-render an empty cxld row so it always shows even if the query yields
  // no cancellations.  Filled in below; left in-place with zeros otherwise.
  const _emptyCxldRow = () => {
    let cells = '<td class="row-label" style="color:#b71c1c;font-weight:600;white-space:nowrap">Qty Cxld</td>';
    for (let i = 0; i < 26; i++) cells += '<td style="color:#bbb">0</td>';
    cells += '<td style="color:#bbb;font-weight:700">0</td>';
    cells += '<td style="color:#bbb;font-weight:700">0</td>';
    rowEl.innerHTML = cells;
  };
  _emptyCxldRow();

  try {
    await discoverOrdHistFids();
    if (!ORD_HIST_QTY_CXLD_FID || !ORD_HIST_ACCT_MSTYLE_FID || !ORD_HIST_CANCEL_DATE_FID) {
      return;  // leave the empty row in place
    }

    // 26-week window ending at W1_DATE; QB date filter uses MM-DD-YYYY
    const dateFrom = new Date(W1_DATE.getTime());
    dateFrom.setDate(W1_DATE.getDate() - 26 * 7);
    const _qbDate = d => {
      const s = d.toISOString().slice(0, 10);
      return `${s.slice(5, 7)}-${s.slice(8, 10)}-${s.slice(0, 4)}`;
    };

    const select = [ORD_HIST_CANCEL_DATE_FID, ORD_HIST_QTY_CXLD_FID];
    if (ORD_HIST_EXCEP_NOTES_FID) select.push(ORD_HIST_EXCEP_NOTES_FID);

    const escKey = r.key.replace(/'/g, "''");
    // Only include cancellations where Exception_Approval is false (unchecked).
    // Exception-approved orders are pre-sanctioned exceptions and must not appear
    // in the L26W history view per planning policy.
    const where = `{${ORD_HIST_ACCT_MSTYLE_FID}.EX.'${escKey}'}` +
                  `AND{${ORD_HIST_QTY_CXLD_FID}.GT.0}` +
                  `AND{${ORD_HIST_CANCEL_DATE_FID}.OAF.'${_qbDate(dateFrom)}'}` +
                  (ORD_HIST_EXCEP_APPR_FID ? `AND{${ORD_HIST_EXCEP_APPR_FID}.EX.'false'}` : '');

    const resp = await qb('/records/query', {
      from: CFG.ORDER_HIST_TID,
      select,
      where,
      options: { skip: 0, top: 1000 },
    });

    const data = (resp && resp.data) || [];

    // Bucket into the same 26 weekly slots as histOrd/histShp
    // Index 0 = oldest (26 wk ago), index 25 = most recent (LW)
    const cxldByWeek  = new Array(26).fill(0);
    const notesByWeek = Array.from({ length: 26 }, () => []);

    const _sv  = (rec, fid) => (rec[fid] && rec[fid].value != null) ? rec[fid].value : null;

    for (const rec of data) {
      const rawDate = _sv(rec, ORD_HIST_CANCEL_DATE_FID);
      if (!rawDate) continue;
      const cancelDate = new Date(rawDate);
      if (isNaN(cancelDate.getTime())) continue;
      // weeksAgo: 1 = last week (LW), 26 = 26 weeks ago
      const msDiff   = W1_DATE.getTime() - cancelDate.getTime();
      const weeksAgo = Math.ceil(msDiff / (7 * 86400000));
      const idx      = 26 - weeksAgo;  // 0 = oldest, 25 = LW
      if (idx < 0 || idx >= 26) continue;
      const qty = parseFloat(_sv(rec, ORD_HIST_QTY_CXLD_FID)) || 0;
      cxldByWeek[idx] += qty;
      if (ORD_HIST_EXCEP_NOTES_FID) {
        const note = _sv(rec, ORD_HIST_EXCEP_NOTES_FID);
        if (note) notesByWeek[idx].push(String(note));
      }
    }

    const total = cxldByWeek.reduce((s, v) => s + v, 0);
    if (total === 0) return;  // leave empty row in place

    let cells = '<td class="row-label" style="color:#b71c1c;font-weight:600;white-space:nowrap">Qty Cxld *</td>';
    for (let i = 0; i < 26; i++) {
      const v     = cxldByWeek[i];
      const notes = notesByWeek[i];
      const tip   = notes.length ? notes.join(' | ') : '';
      const col   = v === 0 ? 'color:#bbb' : 'color:#b71c1c;font-weight:600';
      const attr  = tip
        ? ` title="${tip.replace(/"/g, '&quot;').replace(/</g, '&lt;')}" style="${col};cursor:help"`
        : ` style="${col}"`;
      cells += `<td${attr}>${fmtN(v)}</td>`;
    }
    cells += `<td style="font-weight:700;color:#b71c1c">${fmtN(total)}</td>`;
    cells += `<td style="font-weight:700;color:#b71c1c">${fmtN(Math.round(total / 26))}</td>`;
    rowEl.innerHTML = cells;

  } catch (e) {
    console.warn('[OrdHist] cxld row load failed:', e.message || e);
    // leave the empty row in place so structure stays consistent
  }
}

// -- Open order hover: per-customer breakdown with cancel date ----------------
// Queries Order History for rows where Qty_Open > 0, groups by customer +
// cancel date, and rewrites the title attr on each opn-cell-* <td> so hovering
// shows a breakdown rather than the generic "all customers combined" fallback.
async function _loadOpenOrderDetails(r, safeId) {
  if (!CFG.ORDER_HIST_TID || !W1_DATE) return;

  try {
    await discoverOrdHistFids();
    if (!ORD_HIST_QTY_OPEN_FID || !ORD_HIST_ACCT_MSTYLE_FID || !ORD_HIST_CANCEL_DATE_FID) return;

    const select = [ORD_HIST_CANCEL_DATE_FID, ORD_HIST_QTY_OPEN_FID];
    if (ORD_HIST_CUST_NAME_FID) select.push(ORD_HIST_CUST_NAME_FID);

    const escKey = r.key.replace(/'/g, "''");
    const where  = `{${ORD_HIST_ACCT_MSTYLE_FID}.EX.'${escKey}'}` +
                   `AND{${ORD_HIST_QTY_OPEN_FID}.GT.0}`;

    const resp = await qb('/records/query', {
      from: CFG.ORDER_HIST_TID,
      select,
      where,
      options: { skip: 0, top: 2000 },
    });

    const data = (resp && resp.data) || [];
    console.info(`[OrdHist] open orders for ${r.key}: ${data.length} rows  FIDs: acct=${ORD_HIST_ACCT_MSTYLE_FID} cancel=${ORD_HIST_CANCEL_DATE_FID} qty_open=${ORD_HIST_QTY_OPEN_FID} cust=${ORD_HIST_CUST_NAME_FID}`);
    if (!data.length) return;

    const _sv = (rec, fid) => (rec[fid] && rec[fid].value != null) ? rec[fid].value : null;
    const _fmtDate = d => {
      const s = new Date(d).toISOString().slice(0, 10);
      return s.slice(5, 7) + '/' + s.slice(8, 10);
    };

    // byWeek[i] = array of { cust, qty, cancelDate } for cell index i (0=W1..25=W26)
    const byWeek = Array.from({ length: 26 }, () => []);

    for (const rec of data) {
      const rawDate = _sv(rec, ORD_HIST_CANCEL_DATE_FID);
      if (!rawDate) continue;
      const cancelDate = new Date(rawDate);
      if (isNaN(cancelDate.getTime())) continue;
      const qty = parseFloat(_sv(rec, ORD_HIST_QTY_OPEN_FID)) || 0;
      if (qty <= 0) continue;

      // Forward-week bucketing: cell 0 = W1 (includes past-due), cell i = W(i+1)
      const daysDiff = Math.floor((cancelDate.getTime() - W1_DATE.getTime()) / 86400000);
      let cellIdx = Math.floor(daysDiff / 7);
      if (cellIdx < 0) cellIdx = 0;   // past-due -> cell 0 (W1)
      if (cellIdx >= 26) continue;    // beyond forecast horizon

      const cust = ORD_HIST_CUST_NAME_FID ? (String(_sv(rec, ORD_HIST_CUST_NAME_FID) || '')).trim() : '';
      byWeek[cellIdx].push({ cust: cust || 'Customer', qty, cancelDate: _fmtDate(rawDate) });
    }

    for (let i = 0; i < 26; i++) {
      const entries = byWeek[i];
      if (!entries.length) continue;
      const cell = document.getElementById('opn-cell-' + safeId + '-' + i);
      if (!cell) continue;

      // Aggregate by customer (a customer may have multiple open POs in the week)
      const custMap = {};
      for (const e of entries) {
        if (!custMap[e.cust]) custMap[e.cust] = { qty: 0, dates: new Set() };
        custMap[e.cust].qty += e.qty;
        custMap[e.cust].dates.add(e.cancelDate);
      }

      const lines = [];
      for (const [cust, info] of Object.entries(custMap)) {
        const dateStr = [...info.dates].sort().join(', ');
        lines.push(`${cust}: ${fmtN(info.qty)} units | cancel ${dateStr}`);
      }
      cell.title = lines.join('\n');
      cell.style.cursor = 'help';
    }
  } catch (e) {
    console.warn('[OrdHist] open order hover load failed:', e.message || e);
  }
}

// -- Amazon DC Inventory Health live fetch ------------------------------------
// Called every time an Amazon detail panel opens.  Queries bqp8vz625 by Mstyle
// and injects/replaces the "Amazon DC inventory" bullet in the AI Analysis <ul>.
// If the stored ai_analysis already has a DC inventory bullet from a previous
// forecast run, this replaces it with a fresh live read so data never goes stale.
async function _loadAmzDcInv(r, safeId) {
  if (!CFG.AMZ_CATALOG_TID) return;
  const AF     = CFG.AMZ_CATALOG_FID;
  const mstyle = (r.mstyle || '').trim();
  if (!mstyle) return;

  let soh = 0, opo = 0, wos = 0;
  let qtyOh = 0, qtyIw = 0, qtyIt = 0, prjWk = 0, custOo = 0;
  let atsNow = 0, atsOh = 0, atsOo = 0;
  let posL4w = 0, posL13w = 0, posL26w = 0, posL52w = 0, posLw = 0;
  let fetchOk = false;
  // AUR fields (fetched from bqkdjaqi7)
  let aurLw = 0, aurL4w = 0, aurL13w = 0, aurL26w = 0, aurL52w = 0;
  let aurFetchOk = false;
  try {
    const selectFids = [AF.MSTYLE, AF.SOH, AF.OPO, AF.WOS_OH,
                        AF.QTY_OH, AF.QTY_IW, AF.QTY_IT, AF.PRJ_WK, AF.CUST_OO,
                        AF.ATS_NOW, AF.ATS_OH, AF.ATS_OO,
                        AF.POS_L4W, AF.POS_L13W, AF.POS_L26W, AF.POS_L52W, AF.POS_LW,
                       ].filter(v => v != null);
    // Try exact mstyle first, then fallbacks in priority order:
    //   1. Strip /N pack-size suffix  (catalog may store bare mstyle without case-size)
    //   2. Strip EC/COS variant suffix so EC and COS styles inherit catalog data from
    //      the base mstyle  (e.g. FF12302/24EC -> FF12302/24 -> FF12302)
    const tryMstyles = [mstyle];
    const stripped = mstyle.replace(/\/\d+$/, '');
    if (stripped !== mstyle) tryMstyles.push(stripped);
    // EC/COS base fallback
    const baseMstyle = mstyle.replace(/(COS|EC)$/i, '');
    if (baseMstyle !== mstyle) {
      if (!tryMstyles.includes(baseMstyle)) tryMstyles.push(baseMstyle);
      const bareBase = baseMstyle.replace(/\/\d+$/, '');
      if (bareBase !== baseMstyle && !tryMstyles.includes(bareBase)) tryMstyles.push(bareBase);
    }

    let row = null;
    for (const ms of tryMstyles) {
      const resp = await qb('/records/query', {
        from:   CFG.AMZ_CATALOG_TID,
        select: selectFids,
        where:  `{${AF.MSTYLE}.EX.'${ms.replace(/'/g, "''")}'}`,
        options: { top: 1 },
      });
      const rows = resp.data || [];
      if (rows.length) { row = rows[0]; break; }
    }
    if (row) {
      const nv  = fid => fid != null ? (parseFloat((row[fid] && row[fid].value) || 0) || 0) : 0;
      soh    = nv(AF.SOH);
      opo    = nv(AF.OPO);
      wos    = nv(AF.WOS_OH);
      qtyOh  = nv(AF.QTY_OH);
      qtyIw  = nv(AF.QTY_IW);
      qtyIt  = nv(AF.QTY_IT);
      prjWk  = nv(AF.PRJ_WK);
      custOo = nv(AF.CUST_OO);
      atsNow = nv(AF.ATS_NOW);
      atsOh  = nv(AF.ATS_OH);
      atsOo  = nv(AF.ATS_OO);
      posL4w  = nv(AF.POS_L4W);
      posL13w = nv(AF.POS_L13W);
      posL26w = nv(AF.POS_L26W);
      posL52w = nv(AF.POS_L52W);
      posLw   = nv(AF.POS_LW);
      fetchOk = true;
    }
  } catch (e) {
    console.warn('[DC Inv] fetch failed for mstyle', mstyle, e);
  }

  // ── AUR fetch (bqkdjaqi7 AdTrack Amazon Catalog) ─────────────────────────
  if (CFG.AMZ_AUR_TID && mstyle) {
    const AA = CFG.AMZ_AUR_FID;
    try {
      const aurSelectFids = [AA.MSTYLE, AA.AUR_L4W, AA.AUR_L13W, AA.AUR_L26W,
                             AA.AUR_L52W, AA.REV_LW, AA.UNITS_LW].filter(v => v != null);
      // Try exact mstyle, then strip /N pack suffix, then strip EC/COS suffix
      const aurTryMstyles = [mstyle];
      const aurStripped = mstyle.replace(/\/\d+$/, '');
      if (aurStripped !== mstyle) aurTryMstyles.push(aurStripped);
      const aurBase = mstyle.replace(/(COS|EC)$/i, '');
      if (aurBase !== mstyle && !aurTryMstyles.includes(aurBase)) {
        aurTryMstyles.push(aurBase);
        const aurBareBase = aurBase.replace(/\/\d+$/, '');
        if (aurBareBase !== aurBase && !aurTryMstyles.includes(aurBareBase)) aurTryMstyles.push(aurBareBase);
      }
      let aurRow = null;
      for (const ms of aurTryMstyles) {
        const aurResp = await qb('/records/query', {
          from:   CFG.AMZ_AUR_TID,
          select: aurSelectFids,
          where:  `{${AA.MSTYLE}.EX.'${ms.replace(/'/g, "''")}'}`,
          options: { top: 1 },
        });
        const aurRows = aurResp.data || [];
        if (aurRows.length) { aurRow = aurRows[0]; break; }
      }
      if (aurRow) {
        const anv = fid => fid != null ? (parseFloat((aurRow[fid] && aurRow[fid].value) || 0) || 0) : 0;
        const revLw   = anv(AA.REV_LW);
        const unitsLw = anv(AA.UNITS_LW);
        aurLw   = (unitsLw > 0) ? revLw / unitsLw : 0;
        aurL4w  = anv(AA.AUR_L4W);
        aurL13w = anv(AA.AUR_L13W);
        aurL26w = anv(AA.AUR_L26W);
        // FALLBACK (2026-05-24): the Catalog's AUR L13w field (FID 1052) is
        // unpopulated for ~45% of mstyles, so when L13W is missing but L4W
        // and L26W are both present, interpolate L13W as their mean. AUR is
        // a slowly-changing metric (consumer price); the linear estimate is
        // accurate within a few percent when L4W and L26W are within ~10%.
        if (aurL13w === 0 && aurL4w > 0 && aurL26w > 0) {
          aurL13w = (aurL4w + aurL26w) / 2;
        }
        aurL52w = anv(AA.AUR_L52W);
        aurFetchOk = true;
      }
    } catch (e) {
      console.warn('[AUR] fetch failed for mstyle', mstyle, e);
    }
  }

  // ── AI Analysis bullets ───────────────────────────────────────────────────
  const fmt    = n => Math.round(n).toLocaleString('en-US');
  const fmtWos = n => n.toFixed(1);
  const fmtPos = n => n % 1 === 0 ? Math.round(n).toLocaleString('en-US') : n.toFixed(1);
  const fmtAur = n => '$' + n.toFixed(2);
  const sep    = ' &nbsp;<span style="color:#bbb">|</span>&nbsp; ';

  // POS bullet
  const hasPos = fetchOk && (posL4w > 0 || posL13w > 0 || posL26w > 0 || posL52w > 0);
  let posBulletHtml;
  if (!fetchOk) {
    posBulletHtml = '<b>Amazon POS sales:</b> <span style="color:#999;font-style:italic">not in catalog (no data)</span>';
  } else if (!hasPos) {
    posBulletHtml = '<b>Amazon POS sales:</b> <span style="color:#999;font-style:italic">no consumer sales data</span>';
  } else {
    const posItems = [];
    if (posLw  > 0) posItems.push(`<b>LW</b> ${fmtPos(posLw)} u`);
    if (posL4w  > 0) posItems.push(`<b>L4W avg</b> ${fmtPos(posL4w)} u/wk`);
    if (posL13w > 0) posItems.push(`<b>L13W avg</b> ${fmtPos(posL13w)} u/wk`);
    if (posL26w > 0) posItems.push(`<b>L26W avg</b> ${fmtPos(posL26w)} u/wk`);
    if (posL52w > 0) posItems.push(`<b>L52W avg</b> ${fmtPos(posL52w)} u/wk`);
    posBulletHtml = '<b>Amazon POS sales:</b> ' + posItems.join(sep);
  }

  // DC inv bullet
  let wosHtml;
  if      (wos < 3)  wosHtml = `<b>WOS</b> <span style="color:#c62828;font-weight:600">${fmtWos(wos)} wks &#9888;</span>`;
  else if (wos < 8)  wosHtml = `<b>WOS</b> <span style="color:#e65100">${fmtWos(wos)} wks</span>`;
  else if (wos < 16) wosHtml = `<b>WOS</b> ${fmtWos(wos)} wks`;
  else               wosHtml = `<b>WOS</b> <span style="color:#f57f17">${fmtWos(wos)} wks (overstocked)</span>`;

  const dcBulletHtml = fetchOk
    ? '<b>Amazon DC inventory:</b> ' +
        `<b>Amazon OH</b> ${fmt(soh)} u` + sep +
        `<b>Open PO</b> ${fmt(opo)} u` + sep +
        wosHtml
    : '<b>Amazon DC inventory:</b> <span style="color:#999;font-style:italic">not in catalog (no data)</span>';

  // AUR bullet -- L13W may be interpolated from L4W + L26W (see fallback above).
  // We track the actual catalog L13W in aurL13wRaw so we can mark the
  // displayed L13W with a trailing asterisk when it was derived.
  let aurBulletHtml;
  if (!aurFetchOk) {
    aurBulletHtml = '<b>Amazon AUR:</b> <span style="color:#999;font-style:italic">no pricing data</span>';
  } else {
    const aurItems = [];
    if (aurL4w  > 0) aurItems.push(`<b>L4W avg</b> ${fmtAur(aurL4w)}`);
    if (aurL13w > 0) {
      // If L13W catalog value was 0/null and we computed it from L4W+L26W,
      // mark it with "*" so planners know it's a derived estimate.
      const catalogL13w = aurRow ? (parseFloat((aurRow[AA.AUR_L13W] && aurRow[AA.AUR_L13W].value) || 0) || 0) : 0;
      const isInterp = (catalogL13w === 0 && aurL4w > 0 && aurL26w > 0);
      const mark = isInterp ? '<span title="interpolated from L4W/L26W (catalog L13W is null)" style="color:#999">*</span>' : '';
      aurItems.push(`<b>L13W avg</b> ${fmtAur(aurL13w)}${mark}`);
    }
    if (aurL26w > 0) aurItems.push(`<b>L26W avg</b> ${fmtAur(aurL26w)}`);
    if (aurL52w > 0) aurItems.push(`<b>L52W avg</b> ${fmtAur(aurL52w)}`);
    aurBulletHtml = aurItems.length
      ? '<b>Amazon AUR:</b> ' + aurItems.join(sep)
      : '<b>Amazon AUR:</b> <span style="color:#999;font-style:italic">no pricing data</span>';
  }

  const ul = document.getElementById('ai-bullets-' + safeId);
  if (ul) {
    // Remove stale versions of all three live bullets
    Array.from(ul.querySelectorAll('li')).forEach(li => {
      if (/amazon dc inventory/i.test(li.textContent)
          || /amazon pos sales/i.test(li.textContent)
          || li.hasAttribute('data-amz-aur')) li.remove();
    });

    const mkLi = (html, attr) => {
      const li = document.createElement('li');
      li.style.marginBottom = '4px';
      li.setAttribute(attr, '1');
      li.innerHTML = html;
      return li;
    };

    const posLi = mkLi(posBulletHtml, 'data-amz-pos');
    const dcLi  = mkLi(dcBulletHtml,  'data-amz-dc-inv');
    const aurLi = mkLi(aurBulletHtml, 'data-amz-aur');

    // Insert order: POS, DC inv, AUR (AUR always immediately below DC inv)
    ul.appendChild(posLi);
    ul.appendChild(dcLi);
    ul.appendChild(aurLi);
  }

  // ATS cards are now sourced from Inventory Flow and rendered inline —
  // nothing left for _loadAmzDcInv to do after the bullets above.
}

// -- Retailer POS live fetch --------------------------------------------------
// Called for non-Amazon records when a detail panel opens.  Queries the
// Retailer Sales table (bv2izcn5b) by Mstyle + Acct# and injects three
// POS bullets into the AI Analysis <ul>.  Each week appears exactly twice in
// the source table so rows are deduplicated by date before computing averages.
async function _loadRtlPos(r, safeId) {
  if (!CFG.RTL_POS_TID) return;
  const RF     = CFG.RTL_POS_FID;
  const mstyle = (r.mstyle || '').trim();
  if (!mstyle) return;
  // Extract acct# from key (format: "ACCT-MSTYLE", e.g. "23011-FF8654")
  const acctStr = r.key ? r.key.slice(0, r.key.length - mstyle.length - 1) : '';
  if (!acctStr) return;

  let rtlPosLw = 0, rtlPosL4w = 0, rtlPosL13w = 0, rtlPosL26w = 0, rtlPosL52w = 0;
  let rtlOhLw = 0, rtlInstockLw = 0;
  let rtlTrtStLw = 0, rtlTrtStPrior = 0;
  let rtlPosStLw = 0, rtlPosStPrior = 0;
  let rtlAurLw = 0;
  let rtlFetchOk = false;

  try {
    const selectFids = [RF.DATE, RF.POS_U, RF.POS_D, RF.OH_U, RF.TRT_ST, RF.POS_ST, RF.INSTOCK];
    const rtlResp = await qb('/records/query', {
      from:    CFG.RTL_POS_TID,
      select:  selectFids,
      where:   `{${RF.MSTYLE}.EX.'${mstyle.replace(/'/g, "''")}'}AND{${RF.ACCT}.EX.${acctStr}}`,
      sortBy:  [{ fieldId: RF.DATE, order: 'DESC' }],
      options: { top: 120 },
    });
    const rawRows = rtlResp.data || [];

    // Deduplicate by date (each week-ending Sunday appears exactly twice)
    const seen = new Set();
    const rows = [];
    for (const row of rawRows) {
      const dateVal = (row[RF.DATE] && row[RF.DATE].value) || '';
      if (!dateVal || seen.has(dateVal)) continue;
      seen.add(dateVal);
      rows.push(row);
    }
    if (!rows.length) return;   // no POS data for this acct-mstyle

    const nv = (row, fid) => parseFloat((row[fid] && row[fid].value) || 0) || 0;

    // Average POS Units over the most-recent N deduplicated weeks
    const avgPosU = (n) => {
      const slice = rows.slice(0, Math.min(n, rows.length));
      if (!slice.length) return 0;
      return slice.reduce((acc, row) => acc + nv(row, RF.POS_U), 0) / slice.length;
    };

    rtlPosLw   = nv(rows[0], RF.POS_U);
    rtlPosL4w  = avgPosU(4);
    rtlPosL13w = avgPosU(13);
    rtlPosL26w = avgPosU(26);
    rtlPosL52w = avgPosU(52);

    rtlOhLw      = nv(rows[0], RF.OH_U);
    rtlInstockLw = nv(rows[0], RF.INSTOCK);

    rtlTrtStLw    = nv(rows[0], RF.TRT_ST);
    rtlTrtStPrior = rows.length > 1 ? nv(rows[1], RF.TRT_ST) : 0;
    rtlPosStLw    = nv(rows[0], RF.POS_ST);
    rtlPosStPrior = rows.length > 1 ? nv(rows[1], RF.POS_ST) : 0;

    const posUlw = nv(rows[0], RF.POS_U);
    const posDlw = nv(rows[0], RF.POS_D);
    rtlAurLw = (posUlw > 0) ? posDlw / posUlw : 0;

    rtlFetchOk = true;
  } catch (e) {
    console.warn('[RTL POS] fetch failed for', mstyle, acctStr, e);
  }

  if (!rtlFetchOk) return;

  const custName = _friendlyCustName(r.cust || '');
  const fmt    = n => Math.round(n).toLocaleString('en-US');
  const fmtPos = n => n % 1 === 0 ? Math.round(n).toLocaleString('en-US') : n.toFixed(1);
  const fmtAur = n => '$' + n.toFixed(2);
  const fmtPct = n => (n * 100).toFixed(1) + '%';
  const sep    = ' &nbsp;<span style="color:#bbb">|</span>&nbsp; ';

  // Show week-over-week store count change: green for gain, red for loss
  const fmtDelta = (delta) => {
    if (delta === 0) return '';
    const sign = delta > 0 ? '+' : '';
    return ` <span style="color:${delta > 0 ? '#2e7d32' : '#c62828'};font-size:11px">(${sign}${fmt(delta)})</span>`;
  };

  // -- Bullet 1: POS Sales --------------------------------------------------
  const posItems = [];
  if (rtlPosLw   > 0) posItems.push(`<b>LW</b> ${fmtPos(rtlPosLw)} u`);
  if (rtlPosL4w  > 0) posItems.push(`<b>L4W avg</b> ${fmtPos(rtlPosL4w)} u/wk`);
  if (rtlPosL13w > 0) posItems.push(`<b>L13W avg</b> ${fmtPos(rtlPosL13w)} u/wk`);
  if (rtlPosL26w > 0) posItems.push(`<b>L26W avg</b> ${fmtPos(rtlPosL26w)} u/wk`);
  if (rtlPosL52w > 0) posItems.push(`<b>L52W avg</b> ${fmtPos(rtlPosL52w)} u/wk`);
  const rtlPosBulletHtml = posItems.length
    ? '<b>' + custName + ' POS sales:</b> ' + posItems.join(sep)
    : '<b>' + custName + ' POS sales:</b> <span style="color:#999;font-style:italic">no POS data</span>';

  // -- Bullet 2: Customer inventory + OH WOS --------------------------------
  const fmtWos = n => n.toFixed(1);
  const ohWos  = rtlPosL4w > 0 ? rtlOhLw / rtlPosL4w : 0;
  const invItems = [];
  if (rtlOhLw > 0) invItems.push(`<b>OH</b> ${fmt(rtlOhLw)} u`);
  if (ohWos > 0) {
    let wosHtml;
    if      (ohWos < 4)  wosHtml = `<b>OH WOS</b> <span style="color:#c62828;font-weight:600">${fmtWos(ohWos)} wks &#9888;</span>`;
    else if (ohWos < 6)  wosHtml = `<b>OH WOS</b> <span style="color:#e65100">${fmtWos(ohWos)} wks</span>`;
    else if (ohWos < 12) wosHtml = `<b>OH WOS</b> ${fmtWos(ohWos)} wks`;
    else                 wosHtml = `<b>OH WOS</b> <span style="color:#f57f17">${fmtWos(ohWos)} wks (overstocked)</span>`;
    invItems.push(wosHtml);
  }
  if (rtlInstockLw > 0) {
    const instPct   = fmtPct(rtlInstockLw);
    const instColor = rtlInstockLw < 0.90 ? '#c62828' : rtlInstockLw < 0.95 ? '#e65100' : '';
    const instHtml  = instColor
      ? `<span style="color:${instColor};font-weight:600">${instPct}</span>`
      : instPct;
    invItems.push(`<b>Instock</b> ${instHtml}`);
  }
  const rtlInvBulletHtml = invItems.length
    ? '<b>' + custName + ' inventory:</b> ' + invItems.join(sep)
    : '<b>' + custName + ' inventory:</b> <span style="color:#999;font-style:italic">no data</span>';

  // -- Bullet 3: Distribution -----------------------------------------------
  const trtDelta = rtlTrtStLw - rtlTrtStPrior;
  const posDelta = rtlPosStLw - rtlPosStPrior;
  const distItems = [];
  if (rtlTrtStLw > 0) distItems.push(`<b>Traited strs</b> ${fmt(rtlTrtStLw)}${fmtDelta(trtDelta)}`);
  if (rtlPosStLw > 0) distItems.push(`<b>POS strs</b> ${fmt(rtlPosStLw)}${fmtDelta(posDelta)}`);
  if (rtlAurLw   > 0) distItems.push(`<b>AUR$ LW</b> ${fmtAur(rtlAurLw)}`);
  const rtlDistBulletHtml = distItems.length
    ? '<b>' + custName + ' distribution:</b> ' + distItems.join(sep)
    : '<b>' + custName + ' distribution:</b> <span style="color:#999;font-style:italic">no data</span>';

  // -- Inject into AI Analysis <ul> -----------------------------------------
  const ul = document.getElementById('ai-bullets-' + safeId);
  if (!ul) return;

  // Remove any stale RTL bullets (handles panel re-open)
  Array.from(ul.querySelectorAll('li')).forEach(li => {
    if (li.hasAttribute('data-rtl-pos') || li.hasAttribute('data-rtl-inv')
        || li.hasAttribute('data-rtl-dist')) li.remove();
  });

  const mkLi = (html, attr) => {
    const li = document.createElement('li');
    li.style.marginBottom = '4px';
    li.setAttribute(attr, '1');
    li.innerHTML = html;
    return li;
  };
  ul.appendChild(mkLi(rtlPosBulletHtml,  'data-rtl-pos'));
  ul.appendChild(mkLi(rtlInvBulletHtml,  'data-rtl-inv'));
  ul.appendChild(mkLi(rtlDistBulletHtml, 'data-rtl-dist'));
}

// -- Comment history loader --------------------------------------------------
//
// Two parallel queries  -  one per table  -  populate the two panes:
//    Projection Comments (CFG.COMMENTS_TID)    -> cmt-hist (planner <-> mgr)  last 90 days
//    AI Comments         (CFG.AI_COMMENTS_TID) -> ai-hist  (planner <-> AI)   last 6 months
// Oldest-first.  AI rows render with x Ignore
// (flips [Ignored]=true on the AI Comments record).
async function loadCommentHistory(key, force) {
  const containerKey = key.replace(/'/g, "&#39;");
  const aiCont  = document.getElementById('ai-hist-'  + containerKey);
  const cmtCont = document.getElementById('cmt-hist-' + containerKey);
  if (!aiCont && !cmtCont) return;
  if (!force) {
    if (aiCont)  aiCont.innerHTML  = 'Loading...';
    if (cmtCont) cmtCont.innerHTML = 'Loading...';
  }

  const cmtCutoff = new Date(Date.now() -  90 * 86400 * 1000).toISOString();  // flag/mgr comments: 90 days
  const aiCutoff  = new Date(Date.now() - 183 * 86400 * 1000).toISOString();  // AI adjustments:    6 months
  const escKey = key.replace(/'/g, "''");

  const fmtTs = ts => {
    try { return new Date(ts).toLocaleString('en-US', { year:'numeric', month:'short', day:'numeric', hour:'2-digit', minute:'2-digit' }); }
    catch (e) { return ts || ''; }
  };

  // -- Mgr/Flag comments  -  Projection Comments table -----------------------
  if (cmtCont) {
    try {
      const F = CFG.COMMENT_FID;
      const where = `{${F.ACCT_MSTYLE}.EX.'${escKey}'}AND{${F.DATE_CREATED}.OAF.'${cmtCutoff}'}`;
      const resp  = await qb('/records/query', {
        from:    CFG.COMMENTS_TID,
        select:  [F.RECORD_ID, F.DATE_CREATED, F.NOTE, F.FLAG, F.AUTHOR, ...(F.AUTHOR_USER ? [F.AUTHOR_USER] : []), ...(F.SEND_TO ? [F.SEND_TO] : []), ...(F.SEND_TO_USER ? [F.SEND_TO_USER] : [])],
        where:   where,
        sortBy:  [{ fieldId: F.DATE_CREATED, order: 'ASC' }],
        options: { top: 200 },
      });
      const rows = resp.data || [];
      if (!rows.length) {
        cmtCont.innerHTML = '<div style="color:#999;font-style:italic;">No mgr/flag comments in the last 90 days.</div>';
      } else {
        cmtCont.innerHTML = rows.map(r => {
          const ts     = (r[F.DATE_CREATED] && r[F.DATE_CREATED].value) || '';
          const note   = (r[F.NOTE]         && r[F.NOTE].value)         || '';
          const flag   = (r[F.FLAG]         && r[F.FLAG].value)         || '';
          const _authorText = (r[F.AUTHOR] && r[F.AUTHOR].value) || '';
          const _authorUser = F.AUTHOR_USER && r[F.AUTHOR_USER] && r[F.AUTHOR_USER].value;
          // AUTHOR (FID 40) is a plain-text field written from CURRENT_USER.name at comment-save
          // time.  For directors/VPs who own no Projections records, that lookup can fail and
          // write nothing (or an old "Unknown" default).  AUTHOR_USER (FID 42) is a QB user
          // field that always carries reliable identity data — use it as fallback.
          const _authorUserName = _authorUser
            ? ((_authorUser.name && _authorUser.name !== 'Unknown') ? _authorUser.name : (_authorUser.email || ''))
            : '';
          const author = (_authorText && _authorText !== 'Unknown') ? _authorText : (_authorUserName || _authorText);
          const _sendToText = (F.SEND_TO && r[F.SEND_TO] && r[F.SEND_TO].value) || '';
          const _sendToUser = F.SEND_TO_USER && r[F.SEND_TO_USER] && r[F.SEND_TO_USER].value;
          const _sendToUserName = _sendToUser
            ? ((_sendToUser.name && _sendToUser.name !== 'Unknown') ? _sendToUser.name : (_sendToUser.email || ''))
            : '';
          const sendTo = _sendToText || _sendToUserName;
          const rid    = (r[F.RECORD_ID]    && r[F.RECORD_ID].value)    || 0;
          const isReply     = flag === 'Planner Response';
          const isToPlanner = flag === 'Needs Action';
          const isMgrResp   = flag === 'Manager Response';
          const isFyi       = flag === 'FYI';
          const isClosed    = flag === 'Resolved' || flag === 'Reviewed' || flag === 'Snoozed';
          const borderColor = isFyi     ? '#9e9e9e'
                            : isReply   ? '#00695c'
                            : isToPlanner ? '#1565c0'
                            : isMgrResp ? '#e65100'
                            : isClosed  ? '#388e3c'
                            : '#8b2252';
          const bgColor     = isFyi     ? '#fafafa'
                            : isReply   ? '#f1faf9'
                            : isToPlanner ? '#e8f0fe'
                            : isMgrResp ? '#fff8f0'
                            : isClosed  ? '#f1f8e9'
                            : '#fdf7fa';
          // "From: Author -> To: Recipient" header line -- omitted for FYI (no directed recipient)
          const fromPart   = (!isFyi && author) ? `<b style="color:${borderColor}">${escHtml(author)}</b>` : (isFyi && author ? `<span style="color:#757575">${escHtml(author)}</span>` : '');
          const toPart     = (!isFyi && sendTo) ? ` <span style="color:#888">-&gt;</span> <b style="color:${borderColor}">${escHtml(sendTo)}</b>` : '';
          const authorLine = (fromPart || toPart) ? `${fromPart}${toPart} &middot; ` : '';
          const flagBadge  = isFyi
            ? `<span style="display:inline-block;font-size:9px;font-weight:700;padding:1px 6px;border-radius:8px;background:#f5f5f5;color:#757575;margin-left:6px;vertical-align:middle;">FYI</span>`
            : isReply
              ? `<span style="display:inline-block;font-size:9px;font-weight:700;padding:1px 6px;border-radius:8px;background:#e0f2f1;color:#00695c;margin-left:6px;vertical-align:middle;">Planner Response</span>`
              : isToPlanner
                ? `<span style="display:inline-block;font-size:9px;font-weight:700;padding:1px 6px;border-radius:8px;background:#e3f0ff;color:#1565c0;margin-left:6px;vertical-align:middle;">Needs Action</span>`
                : isMgrResp
                  ? `<span style="display:inline-block;font-size:9px;font-weight:700;padding:1px 6px;border-radius:8px;background:#fff3e0;color:#e65100;margin-left:6px;vertical-align:middle;">Manager Response</span>`
                  : isClosed
                    ? `<span style="display:inline-block;font-size:9px;font-weight:700;padding:1px 6px;border-radius:8px;background:#e8f5e9;color:#2e7d32;margin-left:6px;vertical-align:middle;">${escHtml(flag)}</span>`
                    : (flag ? `<span style="display:inline-block;font-size:9px;font-weight:700;padding:1px 6px;border-radius:8px;background:#fff3e0;color:#8b2252;margin-left:6px;vertical-align:middle;">${escHtml(flag)}</span>` : '');
          // "Mark Reviewed" only appears on Planner Response bubbles (director action)
          // "Mark Read" appears on Manager Response bubbles (planner acknowledges)
          const reviewBtn = isReply
            ? `<button onclick="markReviewed('${key.replace(/'/g,"\\'")}', ${rid}, this)" style="font-size:10px;padding:2px 8px;background:#e0f2f1;color:#00695c;border:1px solid #00695c;border-radius:3px;cursor:pointer;font-weight:600;margin-left:8px;">Mark Reviewed</button>`
            : isMgrResp
              ? `<button onclick="markMgrResponseRead('${key.replace(/'/g,"\\'")}', ${rid}, this)" style="font-size:10px;padding:2px 8px;background:#fff3e0;color:#e65100;border:1px solid #e65100;border-radius:3px;cursor:pointer;font-weight:600;margin-left:8px;">Mark Read</button>`
              : '';
          return `
            <div style="padding:6px 6px 6px 10px;margin-bottom:4px;border-left:3px solid ${borderColor};background:${bgColor};border-radius:0 4px 4px 0;">
              <div style="font-size:10px;color:#888;font-weight:600;display:flex;align-items:center;flex-wrap:wrap;gap:4px;">
                <span>${authorLine}${escHtml(fmtTs(ts))}</span>${flagBadge}${reviewBtn}
              </div>
              <div style="font-size:11px;color:#333;white-space:pre-wrap;line-height:1.35;margin-top:3px;">${escHtml(note)}</div>
            </div>`;
        }).join('');
        cmtCont.scrollTop = cmtCont.scrollHeight;
      }
    } catch (e) {
      cmtCont.innerHTML = `<div style="color:#c62828;">Failed to load mgr history: ${escHtml(e.message||'')}</div>`;
    }
  }

  // -- AI adjustment history  -  AI Comments table ---------------------------
  if (aiCont) {
    if (!CFG.AI_COMMENTS_TID) {
      aiCont.innerHTML = '<div style="color:#999;font-style:italic;">AI adjustment history not yet configured.</div>';
    } else
    try {
      const A = CFG.AI_COMMENT_FID;
      const where = `{${A.ACCT_MSTYLE}.EX.'${escKey}'}AND{${A.DATE_CREATED}.OAF.'${aiCutoff}'}`;
      const resp  = await qb('/records/query', {
        from:    CFG.AI_COMMENTS_TID,
        select:  [A.RECORD_ID, A.DATE_CREATED, A.NOTE, A.AUTHOR, A.IGNORED],
        where:   where,
        sortBy:  [{ fieldId: A.DATE_CREATED, order: 'ASC' }],
        options: { top: 200 },
      });
      const rows = resp.data || [];
      if (!rows.length) {
        aiCont.innerHTML = '<div style="color:#999;font-style:italic;">No prior AI adjustments in the last 6 months.</div>';
      } else {
        aiCont.innerHTML = rows.map(r => {
          const ts      = (r[A.DATE_CREATED] && r[A.DATE_CREATED].value) || '';
          const note    = (r[A.NOTE]         && r[A.NOTE].value)         || '';
          const author  = (r[A.AUTHOR]       && r[A.AUTHOR].value)       || null;
          const ignored = !!(r[A.IGNORED]    && r[A.IGNORED].value);
          const rid     = (r[A.RECORD_ID]    && r[A.RECORD_ID].value)    || '';
          // Author may be a {name,email,id} object (User field) or plain string
          const authorName = (author && typeof author === 'object')
            ? (author.name || author.email || '')
            : (author || '');
          const authorBadge = authorName
            ? `<span style="display:inline-block;font-size:9px;font-weight:700;padding:1px 6px;border-radius:8px;background:#e3f2fd;color:#1565c0;margin-left:6px;vertical-align:middle;">${escHtml(authorName)}</span>`
            : '';
          const ignoredBadge = ignored
            ? `<span style="display:inline-block;font-size:9px;font-weight:700;padding:1px 6px;border-radius:8px;background:#eeeeee;color:#888;margin-left:6px;vertical-align:middle;">IGNORED</span>`
            : '';
          // Strip [ai-intent ...] machine tag from display
          const noteDisplay = note.replace(/\s*\[ai-intent[^\]]*\]/g, '').trim();
          // x Ignore on active entries; Restore on ignored entries
          const safeKey = key.replace(/'/g, "&#39;");
          const ignoreBtn = (!ignored && rid)
            ? `<button onclick="ignoreAiComment(${rid}, '${safeKey}')"
                       title="Stop applying this adjustment on future forecaster runs (audit trail preserved)"
                       style="font-size:9px;padding:1px 6px;border:1px solid #c62828;background:#fff;color:#c62828;border-radius:3px;cursor:pointer;font-weight:600;">x Ignore</button>`
            : (ignored && rid)
              ? `<button onclick="restoreAiComment(${rid}, '${safeKey}')"
                         title="Re-activate this adjustment so F58 applies it on future forecaster runs"
                         style="font-size:9px;padding:1px 6px;border:1px solid #2e7d32;background:#fff;color:#2e7d32;border-radius:3px;cursor:pointer;font-weight:600;">Restore</button>`
              : '';
          const editBtn = rid
            ? `<button onclick="editAiComment(${rid})"
                       title="Edit comment text"
                       style="font-size:9px;padding:1px 6px;border:1px solid #888;background:#fff;color:#555;border-radius:3px;cursor:pointer;font-weight:600;">Edit</button>`
            : '';
          // Greyed-out style for ignored rows
          const rowStyle = ignored ? 'padding:5px 0;border-bottom:1px solid #f0f0f0;opacity:0.5;' : 'padding:5px 0;border-bottom:1px solid #f0f0f0;';
          // Escape full note for data attribute (preserves [ai-intent] tag for save)
          const noteDataAttr = note.replace(/&/g,'&amp;').replace(/"/g,'&quot;');
          return `
            <div style="${rowStyle}" id="ai-comment-row-${rid}" data-full-note="${noteDataAttr}">
              <div style="font-size:10px;color:#888;font-weight:600;display:flex;justify-content:space-between;align-items:center;">
                <span>${escHtml(fmtTs(ts))}${authorBadge}${ignoredBadge}</span>
                <span style="display:flex;gap:4px;">${editBtn}${ignoreBtn}</span>
              </div>
              <div id="ai-note-display-${rid}" style="font-size:11px;color:#333;white-space:pre-wrap;line-height:1.35;margin-top:2px;">${escHtml(noteDisplay)}</div>
              <div id="ai-note-edit-${rid}" style="display:none;margin-top:4px;">
                <textarea id="ai-note-ta-${rid}" rows="3"
                  style="width:100%;font-size:11px;padding:4px 6px;border:1px solid #1565c0;border-radius:3px;font-family:inherit;resize:vertical;box-sizing:border-box;">${escHtml(noteDisplay)}</textarea>
                <div style="display:flex;gap:6px;margin-top:4px;">
                  <button onclick="saveAiCommentEdit(${rid},'${safeKey}')"
                          style="font-size:10px;padding:3px 10px;background:#1565c0;color:#fff;border:none;border-radius:3px;cursor:pointer;font-weight:600;">Save</button>
                  <button onclick="cancelAiCommentEdit(${rid})"
                          style="font-size:10px;padding:3px 10px;background:#fff;color:#555;border:1px solid #ccc;border-radius:3px;cursor:pointer;">Cancel</button>
                </div>
              </div>
            </div>`;
        }).join('');
        aiCont.scrollTop = aiCont.scrollHeight;
      }
    } catch (e) {
      aiCont.innerHTML = `<div style="color:#c62828;">Failed to load AI history: ${escHtml(e.message||'')}</div>`;
    }
  }
}

// -- Add comment > INSERT into Projection Comments table --------------------
async function addComment(key) {
  const txt    = document.getElementById('cmt-text-'   + key).value.trim();
  // Planners only get a FYI checkbox — flag is always "Planner Response" unless checked
  const _fyiChk = document.getElementById('cmt-fyi-' + key);
  const flag = _fyiChk
    ? (_fyiChk.checked ? 'FYI' : 'Planner Response')
    : (document.getElementById('cmt-flag-' + key) || {value: 'Needs Action'}).value;
  const btn    = document.getElementById('cmt-btn-'    + key);
  const msg    = document.getElementById('cmt-msg-'    + key);
  if (!txt) { msg.textContent = 'Comment cannot be empty.'; msg.style.color = '#c62828'; return; }
  btn.disabled = true; btn.textContent = 'Saving...'; msg.textContent = '';

  // Block until the bootstrap identity call finishes — eliminates the race
  // condition where a fast user submits before fetchCurrentUser() returns,
  // leaving CURRENT_USER.name empty and skipping the author write.
  // This is instant if identity already resolved; only delays on the very
  // first comment of a fresh page load.
  await _USER_READY;

  // --- Step 1: INSERT comment record (fatal if it fails) ---------------------
  let recId = '';
  try {
    const fields = {};
    fields[CFG.COMMENT_FID.NOTE]        = { value: txt };
    fields[CFG.COMMENT_FID.ACCT_MSTYLE] = { value: key };
    if (flag) fields[CFG.COMMENT_FID.FLAG] = { value: flag };
    // AUTHOR — text (FID 40) + user (FID 42)
    if (CFG.COMMENT_FID.AUTHOR && CURRENT_USER.name)
      fields[CFG.COMMENT_FID.AUTHOR] = { value: CURRENT_USER.name };
    if (CFG.COMMENT_FID.AUTHOR_USER && CURRENT_USER.email)
      fields[CFG.COMMENT_FID.AUTHOR_USER] = { value: CURRENT_USER.email };

    // SEND_TO — text (FID 41) + user (FID 43)
    //   "Needs Action"    → planner (inv_manager of the record)
    //   "Planner Response"→ primary manager (Mikey Scott)
    //   "FYI"             → no recipient (informational only)
    if (flag !== 'FYI' && (CFG.COMMENT_FID.SEND_TO || CFG.COMMENT_FID.SEND_TO_USER)) {
      const _recForTo = ALL_RECORDS.find(x => x.key === key);
      let _sendToText  = '';
      let _sendToEmail = '';
      if (flag === 'Needs Action' || flag === 'Manager Response') {
        _sendToText  = (_recForTo && _recForTo.inv_manager)       || 'Planner';
        _sendToEmail = (_recForTo && _recForTo.inv_manager_email) || '';
      } else if (flag === 'Planner Response') {
        _sendToText  = CFG.MANAGER_NAMES ? CFG.MANAGER_NAMES.join(', ') : 'Director';
        _sendToEmail = (CFG.MANAGER_NAMES && CFG.MANAGER_EMAILS && CFG.MANAGER_EMAILS[0]) || '';
      }
      if (_sendToText  && CFG.COMMENT_FID.SEND_TO)      fields[CFG.COMMENT_FID.SEND_TO]      = { value: _sendToText };
      if (_sendToEmail && CFG.COMMENT_FID.SEND_TO_USER) fields[CFG.COMMENT_FID.SEND_TO_USER] = { value: _sendToEmail };
    }

    const resp = await qb('/records', { to: CFG.COMMENTS_TID, data: [fields] });
    recId = (resp && resp.metadata && resp.metadata.createdRecordIds && resp.metadata.createdRecordIds[0]) || '';

    // If CURRENT_USER.name was empty (e.g. manager with no owned Projections
    // records), read the Record Owner (FID 4, user type) back from the comment
    // QB just stamped, cache it, and back-fill FID 40 on the same record.
    if (!CURRENT_USER.name && recId) {
      try {
        const probe = await qb('/records/query', {
          from: CFG.COMMENTS_TID, select: [3, 4],
          where: `{3.EX.${recId}}`, options: { top: 1 },
        });
        const owner = ((probe.data || [])[0] || {})[4] && probe.data[0][4].value;
        if (owner) {
          // owner.name can come back as "Unknown" from QB for users without a display name set;
          // fall back to userName, then derive a readable name from email
          const rawName = (owner.name && owner.name !== 'Unknown' ? owner.name : '') || owner.userName || '';
          const ownerEmail = (owner.email || '').trim();
          CURRENT_USER.name  = rawName.trim() || (ownerEmail ? ownerEmail.split('@')[0].replace(/[._]/g, ' ') : '');
          CURRENT_USER.email = CURRENT_USER.email || ownerEmail;
          // Also update the header freshness badge now that we have a name
          const _ub2 = document.getElementById('current-user-badge');
          if (_ub2 && CURRENT_USER.name) _ub2.textContent = CURRENT_USER.name;
        }
        if (CURRENT_USER.name && CFG.COMMENT_FID.AUTHOR) {
          const upd = {};
          upd[CFG.COMMENT_FID.RECORD_ID] = { value: recId };
          upd[CFG.COMMENT_FID.AUTHOR]    = { value: CURRENT_USER.name };
          await qb('/records', { to: CFG.COMMENTS_TID, data: [upd], mergeFieldId: CFG.COMMENT_FID.RECORD_ID });
        }
      } catch (_) { /* non-fatal */ }
    }
  } catch (e) {
    msg.textContent = 'Failed to save comment: ' + e.message; msg.style.color = '#c62828';
    btn.textContent = 'Save'; btn.disabled = false;
    return;
  }

  // --- Step 2: Comment saved — update UI immediately -----------------------
  msg.textContent = recId ? 'Saved (rec #' + recId + ')' : 'Saved';
  msg.style.color = '#2e7d32';
  document.getElementById('cmt-text-' + key).value = '';
  // Reset comment form flag control after save
  const _fyiChkReset = document.getElementById('cmt-fyi-' + key);
  if (_fyiChkReset) { _fyiChkReset.checked = false; }
  else { const _sel = document.getElementById('cmt-flag-' + key); if (_sel) _sel.value = (rec && rec.planner_reply_pending) ? 'Manager Response' : 'Needs Action'; }
  const rec    = ALL_RECORDS.find(x => x.key === key);
  const safeId = key.replace(/[^a-zA-Z0-9]/g, '_');
  // Deferred auto-flag QB write: only now that the comment is saved do we
  // write Flagged=true to QB (autoFlagOnComment updated UI only, not QB).
  if (rec && rec._auto_flagged && rec.flagged && flag !== 'FYI') {
    try {
      const pf = {};
      pf[CFG.FID.KEY]     = { value: key };
      pf[CFG.FID.FLAGGED] = { value: true };
      await qb('/records', { to: CFG.PROJECTIONS_TID, data: [pf], mergeFieldId: CFG.FID.KEY });
    } catch (_) { /* non-critical — UI already reflects flagged state */ }
  }
  if (rec) {
    const stamp = new Date().toISOString().slice(0,16).replace('T',' ');
    const flagTag = flag ? ' ['+flag+']' : '';
    rec.last_comment = `${stamp}${flagTag}: ${txt.slice(0,200)}`;
    rec.last_comment_date = new Date().toISOString();
  }
  btn.textContent = 'Save'; btn.disabled = false;
  if (typeof loadCommentHistory === 'function') loadCommentHistory(key, true);

  // --- Step 3: Routing — update Projections pending-flags (best-effort) ----
  // FYI comments are informational — no routing, no pending flags.
  // Non-fatal: the comment is already saved.  Badge updates are skipped but
  // comment history still shows on reload.
  if (flag === 'FYI') return;
  try {
    if (flag === 'Needs Action' && CFG.FID.MANAGER_REPLY_PENDING) {
      const pf = {};
      pf[CFG.FID.KEY]                   = { value: key };
      pf[CFG.FID.MANAGER_REPLY_PENDING] = { value: true };
      await qb('/records', { to: CFG.PROJECTIONS_TID, data: [pf], mergeFieldId: CFG.FID.KEY });
      if (rec) rec.manager_reply_pending = true;
      const badgeCell = document.getElementById('row-badges-' + safeId);
      if (badgeCell && !badgeCell.querySelector('.mgr-badge'))
        badgeCell.insertAdjacentHTML('beforeend', '<span class="mgr-badge" title="Manager flagged - planner action required">[M]</span>');
      const tr = document.querySelector(`tbody tr[data-key="${CSS.escape(key)}"]`);
      if (tr) tr.classList.add('row-mgr-pending');
      refreshForMeKeys();
    }

    if (flag === 'Planner Response') {
      const pf = {};
      pf[CFG.FID.KEY]                   = { value: key };
      pf[CFG.FID.PLANNER_REPLY_PENDING] = { value: true };
      if (CFG.FID.MANAGER_REPLY_PENDING) pf[CFG.FID.MANAGER_REPLY_PENDING] = { value: false };
      await qb('/records', { to: CFG.PROJECTIONS_TID, data: [pf], mergeFieldId: CFG.FID.KEY });
      if (rec) { rec.planner_reply_pending = true; rec.manager_reply_pending = false; }

      // Mark all open "Needs Action" comments for this key as "Resolved"
      // so the manager's original thread entries no longer show the action badge.
      // Best-effort — comment is already saved even if this step fails.
      try {
        const escKey = key.replace(/'/g, "''");
        const openNa = await qb('/records/query', {
          from: CFG.COMMENTS_TID,
          select: [CFG.COMMENT_FID.RECORD_ID],
          where: `{${CFG.COMMENT_FID.ACCT_MSTYLE}.EX.'${escKey}'} AND {${CFG.COMMENT_FID.FLAG}.EX.'Needs Action'}`,
          options: { top: 100 },
        });
        const toResolve = (openNa.data || []).map(row => {
          const rid = row[String(CFG.COMMENT_FID.RECORD_ID)] && row[String(CFG.COMMENT_FID.RECORD_ID)].value;
          if (!rid) return null;
          const upd = {};
          upd[CFG.COMMENT_FID.RECORD_ID] = { value: rid };
          upd[CFG.COMMENT_FID.FLAG]       = { value: 'Resolved' };
          return upd;
        }).filter(Boolean);
        if (toResolve.length) {
          await qb('/records', { to: CFG.COMMENTS_TID, data: toResolve, mergeFieldId: CFG.COMMENT_FID.RECORD_ID });
        }
      } catch (_naErr) { /* non-fatal */ }
      const badgeCell = document.getElementById('row-badges-' + safeId);
      if (badgeCell) {
        if (!badgeCell.querySelector('.reply-badge'))
          badgeCell.insertAdjacentHTML('beforeend', '<span class="reply-badge" title="Planner reply awaiting director review">[R]</span>');
        const mb = badgeCell.querySelector('.mgr-badge'); if (mb) mb.remove();
      }
      const tr = document.querySelector(`tbody tr[data-key="${CSS.escape(key)}"]`);
      if (tr) { tr.classList.add('row-reply-pending'); tr.classList.remove('row-mgr-pending'); }
      updateReplyCount();
      refreshForMeKeys();
    }

    if (flag === 'Manager Response' && CFG.FID.MANAGER_REPLY_PENDING) {
      // Director is replying to the planner's response -- planner needs to read it,
      // but PLANNER_REPLY_PENDING is cleared (director already saw the planner's reply).
      const pf = {};
      pf[CFG.FID.KEY]                   = { value: key };
      pf[CFG.FID.MANAGER_REPLY_PENDING] = { value: true };
      pf[CFG.FID.PLANNER_REPLY_PENDING] = { value: false };
      await qb('/records', { to: CFG.PROJECTIONS_TID, data: [pf], mergeFieldId: CFG.FID.KEY });
      if (rec) { rec.manager_reply_pending = true; rec.planner_reply_pending = false; }
      const badgeCell = document.getElementById('row-badges-' + safeId);
      if (badgeCell) {
        if (!badgeCell.querySelector('.mgr-badge'))
          badgeCell.insertAdjacentHTML('beforeend', '<span class="mgr-badge" title="Manager responded - planner action required">[M]</span>');
        const rb = badgeCell.querySelector('.reply-badge'); if (rb) rb.remove();
      }
      const tr = document.querySelector(`tbody tr[data-key="${CSS.escape(key)}"]`);
      if (tr) { tr.classList.add('row-mgr-pending'); tr.classList.remove('row-reply-pending'); }
      updateReplyCount();
      refreshForMeKeys();
    }

    if (flag === 'Resolved') {
      if (rec && rec.flagged) await toggleFlag(key);
      if (rec && (rec.planner_reply_pending || rec.manager_reply_pending)) {
        const pf = {};
        pf[CFG.FID.KEY]                   = { value: key };
        pf[CFG.FID.PLANNER_REPLY_PENDING] = { value: false };
        if (CFG.FID.MANAGER_REPLY_PENDING) pf[CFG.FID.MANAGER_REPLY_PENDING] = { value: false };
        await qb('/records', { to: CFG.PROJECTIONS_TID, data: [pf], mergeFieldId: CFG.FID.KEY });
        if (rec) { rec.planner_reply_pending = false; rec.manager_reply_pending = false; }
        const badgeCell = document.getElementById('row-badges-' + safeId);
        if (badgeCell) {
          const rb = badgeCell.querySelector('.reply-badge'); if (rb) rb.remove();
          const mb = badgeCell.querySelector('.mgr-badge');   if (mb) mb.remove();
        }
        const tr = document.querySelector(`tbody tr[data-key="${CSS.escape(key)}"]`);
        if (tr) tr.classList.remove('row-reply-pending', 'row-mgr-pending');
        updateReplyCount();
        refreshForMeKeys();
      }
    }
  } catch (e) {
    console.warn('[addComment] routing write failed (comment was saved):', e.message);
  }
}

// -- Mark Reviewed > clear Planner_Reply_Pending + FLAGGED on Projections ----
//
// Called from the "Mark Reviewed" button on a Planner Response comment bubble.
// Clears on the Projections record:
//   - Planner_Reply_Pending → false  (removes 💬 badge)
//   - Manager_Reply_Pending → false  (removes 📋 badge — loop fully closed)
//   - FLAGGED               → false  (removes red tint)
async function markReviewed(key, commentRid, btnEl) {
  if (btnEl) { btnEl.disabled = true; btnEl.textContent = '...'; }
  try {
    // 1. Flip the FLAG on the specific comment record to "Reviewed"
    if (commentRid) {
      const cf = {};
      cf[CFG.COMMENT_FID.RECORD_ID] = { value: commentRid };
      cf[CFG.COMMENT_FID.FLAG]      = { value: 'Reviewed' };
      await qb('/records', {
        to: CFG.COMMENTS_TID,
        data: [cf],
        mergeFieldId: CFG.COMMENT_FID.RECORD_ID,
      });
    }
    // 2. Clear pending-reply and flagged state on the Projections record
    const pf = {};
    pf[CFG.FID.KEY]                   = { value: key };
    pf[CFG.FID.PLANNER_REPLY_PENDING] = { value: false };
    pf[CFG.FID.FLAGGED]               = { value: false };
    if (CFG.FID.MANAGER_REPLY_PENDING) pf[CFG.FID.MANAGER_REPLY_PENDING] = { value: false };
    await qb('/records', { to: CFG.PROJECTIONS_TID, data: [pf], mergeFieldId: CFG.FID.KEY });

    // Optimistic UI -- update in-memory record first so detail re-render reads clean state
    const rec = ALL_RECORDS.find(x => x.key === key);
    if (rec) { rec.planner_reply_pending = false; rec.manager_reply_pending = false; rec.flagged = false; rec._auto_flagged = false; }
    const safeId = key.replace(/[^a-zA-Z0-9]/g, '_');
    // Remove only the pending-reply badges -- do NOT wipe innerHTML (would destroy [S], [A], switchover badges)
    const badgeCell = document.getElementById('row-badges-' + safeId);
    if (badgeCell) {
      const rb = badgeCell.querySelector('.reply-badge'); if (rb) rb.remove();
      const mb = badgeCell.querySelector('.mgr-badge');   if (mb) mb.remove();
    }
    // Clear flag button and row tint classes
    const flagBtn = document.getElementById('flg-' + safeId);
    if (flagBtn) flagBtn.className = 'flag-btn';
    const tr = document.querySelector(`tbody tr[data-key="${CSS.escape(key)}"]`);
    if (tr) { tr.classList.remove('row-reply-pending', 'row-mgr-pending', 'row-flagged'); }
    updateFlagCount();
    updateReplyCount();
    updateForMeCount();
    // Force detail panel re-render so comment history shows updated reviewed state
    const detailEl = document.getElementById('detail-' + key);
    if (detailEl && detailEl.style.display === 'table-row') {
      detailEl.dataset.loaded = '0';
      detailEl.style.display = 'none';
      toggleDetail(key);
    } else if (detailEl) {
      // Panel is closed -- just reset so next open picks up fresh comment state
      detailEl.dataset.loaded = '0';
    }
    if (btnEl) { btnEl.textContent = 'Reviewed'; btnEl.style.background = '#e8f5e9'; btnEl.style.color = '#2e7d32'; }
  } catch(e) {
    if (btnEl) { btnEl.disabled = false; btnEl.textContent = 'Mark Reviewed'; }
    alert('Failed to mark reviewed: ' + e.message);
  }
}

// -- Mark Read > planner acknowledges a Manager Response comment -------------
// Mirrors markReviewed but in the opposite direction: planner has read the
// director's follow-up, so MANAGER_REPLY_PENDING clears and the [M] badge goes away.
async function markMgrResponseRead(key, commentRid, btnEl) {
  if (btnEl) { btnEl.disabled = true; btnEl.textContent = '...'; }
  try {
    // 1. Flip the FLAG on the specific comment record to "Reviewed"
    if (commentRid) {
      const cf = {};
      cf[CFG.COMMENT_FID.RECORD_ID] = { value: commentRid };
      cf[CFG.COMMENT_FID.FLAG]      = { value: 'Reviewed' };
      await qb('/records', { to: CFG.COMMENTS_TID, data: [cf], mergeFieldId: CFG.COMMENT_FID.RECORD_ID });
    }
    // 2. Clear manager-reply-pending on the Projections record
    const pf = {};
    pf[CFG.FID.KEY]                   = { value: key };
    pf[CFG.FID.MANAGER_REPLY_PENDING] = { value: false };
    await qb('/records', { to: CFG.PROJECTIONS_TID, data: [pf], mergeFieldId: CFG.FID.KEY });

    // Optimistic UI
    const rec = ALL_RECORDS.find(x => x.key === key);
    if (rec) rec.manager_reply_pending = false;
    const safeId = key.replace(/[^a-zA-Z0-9]/g, '_');
    const badgeCell = document.getElementById('row-badges-' + safeId);
    if (badgeCell) { const mb = badgeCell.querySelector('.mgr-badge'); if (mb) mb.remove(); }
    const tr = document.querySelector(`tbody tr[data-key="${CSS.escape(key)}"]`);
    if (tr) tr.classList.remove('row-mgr-pending');
    updateReplyCount();
    updateForMeCount();
    // Force detail re-render so comment shows updated state
    const detailEl = document.getElementById('detail-' + key);
    if (detailEl && detailEl.style.display === 'table-row') {
      detailEl.dataset.loaded = '0';
      detailEl.style.display = 'none';
      toggleDetail(key);
    } else if (detailEl) {
      detailEl.dataset.loaded = '0';
    }
    if (btnEl) { btnEl.textContent = 'Read'; btnEl.style.background = '#fff3e0'; btnEl.style.color = '#e65100'; }
  } catch(e) {
    if (btnEl) { btnEl.disabled = false; btnEl.textContent = 'Mark Read'; }
    alert('Failed to mark read: ' + e.message);
  }
}

// -- Use AI / Use Suggested > upsert manual prj cols ------------------------
async function copyToMan(key, source, btn) {
  const label = source === 'ai' ? 'AI PRJ' : 'Suggested';
  if (!confirm(`Overwrite 26 weeks of MAN projections with ${label} for ${key}?\n\nThis writes to Quickbase immediately.`)) return;
  const orig = btn.textContent;
  btn.disabled = true; btn.textContent = '...';
  btn.classList.remove('done', 'failed');
  try {
    const rec = ALL_RECORDS.find(x => x.key === key);
    if (!rec) throw new Error('record not found');
    const sourceVals = source === 'ai' ? rec.ai_fcst : rec.suggested;
    if (!sourceVals || sourceVals.length !== 26) throw new Error(`expected 26 ${label} values, got ${(sourceVals||[]).length}`);

    const fields = {};
    fields[CFG.FID.KEY] = { value: key };
    for (let i = 0; i < 26; i++) {
      fields[MAN_PRJ_FIDS[i]] = { value: Math.round(sourceVals[i] || 0) };
    }
    await qb('/records', {
      to: CFG.PROJECTIONS_TID,
      data: [fields],
      mergeFieldId: CFG.FID.KEY,
    });

    rec.proj_total = sourceVals.reduce((a,b) => a+b, 0);
    rec.proj_wk    = Math.round(((rec.proj_total + (rec.opn_total || 0)) / 26) * 10) / 10;
    rec.weeks_slim = rec.weeks_slim.map((w, i) => ({ ...w, projection: Math.round(sourceVals[i] || 0), severity: 'OK' }));
    rec.max_sev    = 'OK';
    rec.n_flags    = 0;
    _refreshRowMetrics(key);

    // copyToMan just overwrote all 26 weeks. Drop any pending unsaved edits
    // on this record (they're meaningless now) and refresh the inputs in any
    // open detail pane to match the freshly-written values.
    for (let i = 0; i < 26; i++) DIRTY_EDITS.delete(`${key}|${i}`);
    document.querySelectorAll('.man-edit').forEach(el => {
      if (el.dataset.key === key) {
        const newVal = Math.round(sourceVals[parseInt(el.dataset.week, 10)] || 0);
        el.value = newVal;
        el.dataset.orig = newVal;
        el.classList.remove('dirty');
      }
    });
    const safeIdAfter = key.replace(/[^a-zA-Z0-9]/g, '_');
    const totElAfter  = document.getElementById('man-total-' + safeIdAfter);
    if (totElAfter) totElAfter.textContent = fmtN(rec.proj_total);
    updateSaveAllBadge();

    btn.classList.add('done');
    btn.textContent = 'Done \u2713';
    setTimeout(() => { btn.classList.remove('done'); btn.textContent = orig; btn.disabled = false; }, 2500);
  } catch (e) {
    console.error('copyToMan failed:', e);
    btn.classList.add('failed');
    btn.textContent = 'Fail';
    btn.title = 'Error: ' + e.message;
    setTimeout(() => { btn.classList.remove('failed'); btn.textContent = orig; btn.disabled = false; }, 3000);
  }
}

// -- Editable MAN projection cells ------------------------------------------
// Wired from the inline `oninput="onManEdit(this)"` on each editable cell.
// Updates DIRTY_EDITS state, paints the cell, recomputes the detail-pane
// total live, and refreshes the Save All / Discard buttons.
function onManEdit(inputEl) {
  const key      = inputEl.dataset.key;
  const weekIdx  = parseInt(inputEl.dataset.week, 10);
  const origVal  = parseInt(inputEl.dataset.orig, 10) || 0;
  // Coerce user input: empty / NaN / negative > 0
  let raw = inputEl.value;
  let newVal = parseInt(raw, 10);
  if (!isFinite(newVal) || newVal < 0) newVal = 0;
  // Don't snap the displayed value while typing; only normalize on blur if
  // they typed something invalid. For now, match what we'll save.
  const dirtyKey = `${key}|${weekIdx}`;
  if (newVal === origVal) {
    // Reverted to original  -  drop from dirty set
    DIRTY_EDITS.delete(dirtyKey);
    inputEl.classList.remove('dirty');
  } else {
    DIRTY_EDITS.set(dirtyKey, { key, weekIdx, oldVal: origVal, newVal });
    inputEl.classList.add('dirty');
  }
  // Live-update the detail-pane Total and Avg/Wk cells for this record
  const safeId = key.replace(/[^a-zA-Z0-9]/g, '_');
  const totEl  = document.getElementById('man-total-' + safeId);
  if (totEl) {
    let sum = 0;
    document.querySelectorAll(`.man-edit[data-key="${key.replace(/"/g,'\\"')}"]`).forEach(el => {
      const v = parseInt(el.value, 10);
      sum += (isFinite(v) && v >= 0) ? v : 0;
    });
    totEl.textContent = fmtN(sum);
    const avgWkEl = document.getElementById('man-avgwk-' + safeId);
    if (avgWkEl) avgWkEl.textContent = fmtN(Math.round(sum / 26));
  }
  updateSaveAllBadge();
}

// Updates the Save All / Discard button labels + enabled state based on the
// current size of DIRTY_EDITS. Counts unique record keys (not individual cells)
// so the badge reads "3 records to save" rather than "9 cells".
function updateSaveAllBadge() {
  const n = new Set([...DIRTY_EDITS.values()].map(e => e.key)).size;
  const saveBtn = document.getElementById('saveAllBtn');
  const discBtn = document.getElementById('discardAllBtn');
  if (saveBtn) {
    saveBtn.textContent = `Save All (${n})`;
    saveBtn.disabled = (n === 0);
    saveBtn.classList.toggle('has-edits', n > 0);
  }
  if (discBtn) discBtn.disabled = (n === 0);
}

// Batch-write every dirty edit to QB. Groups edits by record key (so each
// record turns into one upsert with however many week-fields changed),
// chunks into 100-record batches (well below QB's 20k row limit and gives
// the user progress feedback), uses mergeFieldId on the Key field so QB
// updates rather than inserts.
async function saveAllManEdits() {
  const total = DIRTY_EDITS.size;
  if (total === 0) return;
  // Group by record key
  const byKey = new Map();
  for (const edit of DIRTY_EDITS.values()) {
    if (!byKey.has(edit.key)) byKey.set(edit.key, []);
    byKey.get(edit.key).push(edit);
  }
  const recordCount = byKey.size;

  const saveBtn = document.getElementById('saveAllBtn');
  const discBtn = document.getElementById('discardAllBtn');
  const status  = document.getElementById('saveStatus');
  saveBtn.disabled = true; discBtn.disabled = true;
  if (status) { status.style.color = '#1565c0'; status.textContent = `Writing ${recordCount} record(s)...`; }

  // Build the QB upsert payload. One record per key with all dirty week fids.
  const records = [];
  for (const [key, edits] of byKey.entries()) {
    const fields = {};
    fields[CFG.FID.KEY] = { value: key };
    for (const { weekIdx, newVal } of edits) {
      fields[MAN_PRJ_FIDS[weekIdx]] = { value: Math.round(newVal) };
    }
    records.push({ _key: key, _edits: edits, fields });
  }

  const CHUNK = 100;
  const succeededKeys = new Set();
  const failed        = [];   // [{ key, err }]
  try {
    for (let i = 0; i < records.length; i += CHUNK) {
      const slice = records.slice(i, i + CHUNK);
      const payload = slice.map(r => r.fields);
      try {
        await qb('/records', {
          to: CFG.PROJECTIONS_TID,
          data: payload,
          mergeFieldId: CFG.FID.KEY,
        });
        slice.forEach(r => succeededKeys.add(r._key));
      } catch (e) {
        // Whole batch failed  -  mark every record in it as failed so the user
        // can retry. (QB upserts are atomic per call so partial isn't really
        // a thing here.)
        slice.forEach(r => failed.push({ key: r._key, err: e.message || String(e) }));
      }
      if (status) status.textContent = `Saved ${succeededKeys.size} / ${recordCount}...`;
    }
  } finally {
    // Update in-memory state for successes: clear DIRTY_EDITS entries,
    // update r.weeks_slim[i].projection + r.proj_total + r.proj_wk so the
    // row totals stay correct without a full reload.
    for (const key of succeededKeys) {
      const rec = ALL_RECORDS.find(x => x.key === key);
      const myEdits = byKey.get(key) || [];
      for (const e of myEdits) {
        DIRTY_EDITS.delete(`${e.key}|${e.weekIdx}`);
        if (rec && rec.weeks_slim && rec.weeks_slim[e.weekIdx]) {
          rec.weeks_slim[e.weekIdx].projection = e.newVal;
        }
      }
      if (rec && rec.weeks_slim) {
        rec.proj_total = rec.weeks_slim.reduce((a, w) => a + (w.projection || 0), 0);
        rec.proj_wk    = Math.round(((rec.proj_total + (rec.opn_total || 0)) / 26) * 10) / 10;
        _refreshRowMetrics(key);
      }
    }
    // Refresh the visible inputs: cells that just saved lose `.dirty` and
    // get a fresh data-orig so further edits compare against the new value.
    document.querySelectorAll('.man-edit').forEach(el => {
      if (succeededKeys.has(el.dataset.key)) {
        el.dataset.orig = el.value;
        el.classList.remove('dirty');
      }
    });
    updateSaveAllBadge();
    saveBtn.disabled = (DIRTY_EDITS.size === 0);
    discBtn.disabled = (DIRTY_EDITS.size === 0);
    if (status) {
      if (failed.length === 0) {
        status.style.color = '#2e7d32';
        status.textContent = `\u2713 Saved ${succeededKeys.size} record(s)`;
        setTimeout(() => { if (status.textContent.startsWith('\u2713')) status.textContent = ''; }, 4000);
      } else {
        status.style.color = '#c62828';
        const sample = failed.slice(0, 2).map(f => f.key + ': ' + f.err).join(' | ');
        status.textContent = `Saved ${succeededKeys.size}, failed ${failed.length} (${sample}${failed.length>2?'...':''})`;
        console.error('Save failures:', failed);
      }
    }
  }
}

// Save edits for a single record key only. Writes just the dirty cells for
// this one record to QB; any other records' unsaved edits are left untouched.
// Called by the per-row Save button in the editToolbar.
async function saveRecordEdits(key) {
  const myEdits = [...DIRTY_EDITS.values()].filter(e => e.key === key);
  if (myEdits.length === 0) {
    alert('No unsaved edits for this record.');
    return;
  }
  const fields = {};
  fields[CFG.FID.KEY] = { value: key };
  for (const { weekIdx, newVal } of myEdits) {
    fields[MAN_PRJ_FIDS[weekIdx]] = { value: Math.round(newVal) };
  }
  const saveStatus = document.getElementById('saveStatus');
  if (saveStatus) { saveStatus.style.color = '#1565c0'; saveStatus.textContent = `Saving ${key}...`; }
  try {
    await qb('/records', {
      to: CFG.PROJECTIONS_TID,
      data: [fields],
      mergeFieldId: CFG.FID.KEY,
    });
    // On success  -  clear dirty state and refresh in-memory record
    const rec = ALL_RECORDS.find(x => x.key === key);
    for (const e of myEdits) {
      DIRTY_EDITS.delete(`${e.key}|${e.weekIdx}`);
      if (rec && rec.weeks_slim && rec.weeks_slim[e.weekIdx]) {
        rec.weeks_slim[e.weekIdx].projection = e.newVal;
      }
    }
    if (rec && rec.weeks_slim) {
      rec.proj_total = rec.weeks_slim.reduce((a, w) => a + (w.projection || 0), 0);
      rec.proj_wk    = Math.round(((rec.proj_total + (rec.opn_total || 0)) / 26) * 10) / 10;
      _refreshRowMetrics(key);
    }
    // Remove dirty highlight from inputs belonging to this record
    const safeKeyAttr = key.replace(/"/g, '&quot;');
    document.querySelectorAll(`.man-edit[data-key="${safeKeyAttr}"]`).forEach(el => {
      el.dataset.orig = el.value;
      el.classList.remove('dirty');
    });
    updateSaveAllBadge();
    if (saveStatus) {
      saveStatus.style.color = '#2e7d32';
      saveStatus.textContent = '\u2713 Saved ' + key;
      setTimeout(() => { if ((saveStatus.textContent || '').includes(key)) saveStatus.textContent = ''; }, 4000);
    }
  } catch (e) {
    if (saveStatus) { saveStatus.style.color = '#c62828'; saveStatus.textContent = `Error saving ${key}: ${e.message || e}`; }
    console.error('saveRecordEdits failed:', key, e);
  }
}

// Discard every unsaved edit. No QB call  -  just resets state and visuals.
function discardAllManEdits() {
  const n = DIRTY_EDITS.size;
  if (n === 0) return;
  if (!confirm(`Discard ${n} unsaved edit(s)?\n\nThis reverts every yellow-highlighted cell to the QB-loaded value. Nothing is written to Quickbase.`)) return;
  DIRTY_EDITS.clear();
  document.querySelectorAll('.man-edit.dirty').forEach(el => {
    el.value = el.dataset.orig || '0';
    el.classList.remove('dirty');
  });
  // Recompute totals for any open detail panes
  document.querySelectorAll('[id^="man-total-"]').forEach(totEl => {
    // Find the record key from the first matching input in this pane
    const pane = totEl.closest('tr');
    if (!pane) return;
    const inputs = pane.querySelectorAll('.man-edit');
    let sum = 0;
    inputs.forEach(el => sum += parseInt(el.value, 10) || 0);
    totEl.textContent = fmtN(sum);
  });
  updateSaveAllBadge();
  const status = document.getElementById('saveStatus');
  if (status) { status.style.color = '#666'; status.textContent = `Discarded ${n} edit(s)`; setTimeout(() => { status.textContent = ''; }, 3000); }
}

// -- Excel-style bulk operations on the 26 MAN cells ------------------------
//
// Tracks the most-recently focused MAN input per record so the Fill / Fill
// Right buttons know which cell's value to broadcast. Reset to W1 on detail
// pane (re)render  -  the toolbar buttons fall back to W1 if nothing's been
// clicked yet.
const LAST_FOCUSED_BY_KEY = new Map();

function markLastEdit(inputEl) {
  const key     = inputEl.dataset.key;
  const weekIdx = parseInt(inputEl.dataset.week, 10);
  if (!isNaN(weekIdx)) LAST_FOCUSED_BY_KEY.set(key, weekIdx);
}

// Helper: set the value of a single MAN cell programmatically and run the
// same dirty-bookkeeping as a manual edit (so the yellow highlight, total
// recompute, and Save All badge all stay in sync).
function _setManCell(inputEl, newVal) {
  const v = (isFinite(newVal) && newVal >= 0) ? Math.round(newVal) : 0;
  inputEl.value = v;
  onManEdit(inputEl);  // reuses the per-cell dirty/total bookkeeping
}

// Bulk-write the same value to a contiguous range of cells in a record.
// fromIdx and toIdx are both inclusive (0-based, 0 = W1, 25 = W26).
function _setManRange(key, fromIdx, toIdx, val) {
  document.querySelectorAll(`.man-edit[data-key="${key.replace(/"/g,'\\"')}"]`).forEach(el => {
    const w = parseInt(el.dataset.week, 10);
    if (w >= fromIdx && w <= toIdx) _setManCell(el, val);
  });
}

// VP-Q4 duplicate demand: zero out MAN PRJ weeks that overlap with confirmed
// Open POs.  Stages changes as dirty edits (yellow) -- user still clicks
// Save All to write to QB.  Only fires for weeks listed in po_prj_conflicts.
function zeroDuplicateManPrj(key, safeKey) {
  const r = ALL_RECORDS.find(x => x.key === key);
  if (!r || !r.po_prj_conflicts || !r.po_prj_conflicts.length) return;
  // Collect unique 0-based week indices from the PRJ side of each conflict
  const toZero = new Set(r.po_prj_conflicts.map(c => c.prjWk - 1));
  for (const idx of toZero) {
    _setManRange(key, idx, idx, 0);
  }
  updateSaveAllBadge();
  // Update the button to show action was taken
  const btn = document.getElementById('zero-dup-btn-' + (safeKey || key.replace(/[^a-zA-Z0-9]/g,'_')));
  if (btn) {
    btn.textContent = 'Zeroed -- click Save All to commit';
    btn.disabled = true;
    btn.style.background = '#888';
    btn.style.cursor = 'default';
  }
}

// Stage AI / Suggested: copy 26 source values into the editable inputs as
// unsaved edits. Cells whose new value matches the QB-loaded original stay
// un-dirty (onManEdit handles that automatically).
// -- Tell-AI: planner explains logic, AI proposes a 26-week diff --------------
// Pattern: planner writes plain English ("+25% lift W8-W26 distribution gain"),
// regex parser extracts a deterministic adjustment, side-by-side preview shows
// BEFORE/AFTER, and "Apply to MAN" stages the new values via the existing
// manual-edit + Save All flow.  Free-text input that doesn't match any pattern
// falls back to "saved as comment only"  -  no silent auto-apply.

// Forecast-week calendar mapping.  Forecast horizon W1 = early May
// (anchored at ORIG_PRJ_COLS[0]; here we use the canonical May-W1 -> Oct-W26
// span from the current 26-week window).  Used to translate month names
// into week index ranges so plain English like "boost June" works.
const _MONTH_TO_WEEK_RANGE = {
  // Month name (lowercase): [start_idx (0-based), end_idx (0-based)]
  'may':       [0, 4],     // W1-W5
  'jun':       [5, 8],     // W6-W9
  'june':      [5, 8],
  'jul':       [9, 13],    // W10-W14
  'july':      [9, 13],
  'aug':       [14, 17],   // W15-W18
  'august':    [14, 17],
  'sep':       [18, 21],   // W19-W22
  'sept':      [18, 21],
  'september': [18, 21],
  'oct':       [22, 25],   // W23-W26
  'october':   [22, 25],
};

function _monthRange(monthStr) {
  if (!monthStr) return null;
  return _MONTH_TO_WEEK_RANGE[String(monthStr).toLowerCase().slice(0, 9)] || null;
}

// Dynamic month-to-week-range based on the actual W1_DATE.
// Returns [startIdx, endIdx] (0-based inclusive) for any calendar month
// within the current 26-week horizon, or null if out of range.
// Falls back to the hardcoded _MONTH_TO_WEEK_RANGE when W1_DATE is unavailable.
function _monthRangeDynamic(monthName) {
  const monMap = {jan:1,feb:2,mar:3,apr:4,may:5,jun:6,jul:7,aug:8,sep:9,oct:10,nov:11,dec:12};
  const moKey  = String(monthName).toLowerCase().slice(0, 3);
  const mo     = monMap[moKey];
  if (!mo) return null;
  if (W1_DATE) {
    let startIdx = null, endIdx = null;
    for (let i = 0; i < 26; i++) {
      const d = new Date(W1_DATE.getTime() + i * 7 * 86400000);
      if (d.getMonth() + 1 === mo) {
        if (startIdx === null) startIdx = i;
        endIdx = i;
      }
    }
    if (startIdx !== null) return [startIdx, endIdx];
    return null;  // month not in current horizon
  }
  return _MONTH_TO_WEEK_RANGE[String(monthName).toLowerCase().slice(0, 9)] || null;
}

// Convert "MM/DD" or "Mon DD" or "DD Mon" to forecast-week index (0-25).
// W1 starts roughly May 3.  Returns null if out of horizon or unparseable.
function _dateToWeekIdx(dateStr) {
  if (!dateStr) return null;
  const s = String(dateStr).toLowerCase().trim();
  // Try MM/DD or M/D
  let m = s.match(/^(\d{1,2})\/(\d{1,2})/);
  let mo, dd;
  if (m) { mo = parseInt(m[1], 10); dd = parseInt(m[2], 10); }
  else {
    // Try "Mon DD" / "Month DD"
    m = s.match(/(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\s+(\d{1,2})/);
    if (m) {
      const monMap = {jan:1,feb:2,mar:3,apr:4,may:5,jun:6,jul:7,aug:8,sep:9,oct:10,nov:11,dec:12};
      mo = monMap[m[1]]; dd = parseInt(m[2], 10);
    }
  }
  if (!mo || !dd) return null;
  // Crude mapping: forecast W1 = May 3.  Each week = 7 days.
  // Days since May 3:  (mo-5)*30 + (dd-3) + small adj.  For approximate
  // forecast-week mapping that's accurate within 1 week, good enough for
  // EOL / wind-down decisions.  Use actual day-of-year math:
  const moDays = [0, 31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];
  let dayOfYear = dd;
  for (let i = 1; i < mo; i++) dayOfYear += moDays[i];
  // May 3 = day 123 (in non-leap year; 31+28+31+30+3 = 123)
  const w1Day = 123;
  const weeksOffset = Math.floor((dayOfYear - w1Day) / 7);
  if (weeksOffset < 0 || weeksOffset > 25) return null;
  return weeksOffset;
}

function _parseAiAdjustment(text, currentForecast) {
  // Returns { parsed: bool, newForecast: number[26], summary: string,
  //          deltaTotal: number, type: string }
  if (!text || !Array.isArray(currentForecast) || currentForecast.length !== 26) {
    return { parsed: false, summary: 'No forecast loaded for this record.' };
  }
  const t = String(text).trim();
  const lo = t.toLowerCase();
  const cur = currentForecast.map(v => Number(v) || 0);
  const out = cur.slice();
  const _clamp = (n) => Math.max(0, Math.min(25, n - 1));   // user "W7" -> idx 6
  const _round = (v) => Math.max(0, Math.round(v));

  // -- Layer 0: Promo / event notification with pre-event order ramp ---------
  // Fires when the planner describes an upcoming event (promo, launch, holiday
  // push, seasonal sale, etc.) with an expected demand lift.
  //
  // Pattern:  "[month] [promo/event keyword] [Nx or +N% demand lift]"
  // Examples: "January promo 20% off  -  1.2x lift expected"
  //           "Holiday push Dec  -  1.5x demand"
  //           "Back-to-school promo Aug +30% lift"
  //           "New store launch July, 1.4x lift expected"
  //
  // Behavior: event weeks -> baseline × lift; the 5 weeks BEFORE the event
  // each get an extra (totalExtraDemand / 5) units front-loaded so inventory
  // is built up in time to support the promo.
  //
  // "N% off" language in the text is treated as a price discount and ignored
  // for demand purposes; the planner should state the demand lift explicitly
  // ("1.2x lift" or "+20% lift").
  {
    const RAMP_WKS = 5;
    const isEvent  = /promo(?:tion)?|event\b|sale\b|deal\b|launch\b|push\b|program\b|campaign|holiday|seasonal|back[\s-]+to[\s-]+school/.test(lo);
    if (isEvent) {
      // Extract demand lift   -  "Nx lift" takes priority over "+N% lift"
      let liftMult = null, liftLabel = '';
      let lm = lo.match(/(\d+(?:\.\d+)?)\s*x\s+(?:lift|demand|increase|boost|expect)/);
      if (lm) { liftMult = parseFloat(lm[1]); liftLabel = `${lm[1]}x`; }
      if (!liftMult) {
        lm = lo.match(/([+-]?\d+(?:\.\d+)?)\s*%\s*(?:lift|demand|increase|boost|up|expected)/);
        if (lm) { liftMult = 1 + parseFloat(lm[1]) / 100; liftLabel = `+${Math.abs(parseFloat(lm[1]))}%`; }
      }
      if (liftMult && liftMult > 1.0) {
        // Extract event month(s)  -  use _monthRangeDynamic so any calendar month works
        const _mRe = '(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)';
        lm = lo.match(new RegExp(_mRe + `(?:\\s*(?:to|through|[\\-\\u2013])\\s*` + _mRe + `)?`));
        if (lm) {
          const r1 = _monthRangeDynamic(lm[1]);
          const r2 = lm[2] ? _monthRangeDynamic(lm[2]) : null;
          if (!r1) {
            // Month name found but outside current horizon
            return { parsed: false,
              summary: `"${lm[1]}" is outside the current 26-week forecast horizon. Try a month within W1-W26.` };
          }
          const evtStart = r1[0], evtEnd = r2 ? r2[1] : r1[1];
          const evtLabel = lm[1] + (lm[2] ? `-${lm[2]}` : '');
          // Apply lift during event weeks
          for (let i = evtStart; i <= evtEnd; i++) out[i] = _round(cur[i] * liftMult);
          // Pre-event ramp: distribute the extra demand over RAMP_WKS before event
          let extraTotal = 0;
          for (let i = evtStart; i <= evtEnd; i++) extraTotal += (out[i] - cur[i]);
          const rampStart = Math.max(0, evtStart - RAMP_WKS);
          const rampEnd   = evtStart - 1;
          let rampWksUsed = 0;
          if (rampEnd >= rampStart && rampEnd >= 0 && extraTotal > 0) {
            rampWksUsed = rampEnd - rampStart + 1;
            const extraPerWk = _round(extraTotal / rampWksUsed);
            for (let i = rampStart; i <= rampEnd; i++) out[i] = _round(cur[i] + extraPerWk);
          }
          const rampDesc = rampWksUsed > 0
            ? `pre-event ramp W${rampStart+1}-W${rampEnd+1} (+${_round(extraTotal/rampWksUsed).toLocaleString()}u/wk)`
            : 'no pre-event ramp window (event starts too early in horizon)';
          return {
            parsed: true, newForecast: out, type: 'promo_event',
            summary: `Event: ${liftLabel} lift during ${evtLabel} (W${evtStart+1}-W${evtEnd+1}); ${rampDesc}.`,
            deltaTotal: out.reduce((a,b)=>a+b,0) - cur.reduce((a,b)=>a+b,0),
          };
        }
        // Has lift + event keyword but no recognizable month  -  fall through to other layers
      }
    }
  }

  // -- Layer 1: explicit week-number patterns (existing, fast-path) ----------
  // Falls through to Layer 2 (natural-language) if no explicit Wx is given.

  // Pattern: EOL / wind-down by W{c} (or numeric MM/YY converts to nearest W)
  // e.g. "EOL by W14", "wind down by W12", "discontinued by W10"
  let m = lo.match(/(?:eol|wind[-\s]*down|discontinu(?:e|ed|ing)|phase[-\s]*out|end[-\s]*of[-\s]*life)[^\d]*w?(\d{1,2})/);
  if (m) {
    const tgt = _clamp(parseInt(m[1], 10));
    const taper = { 0: 0.85, 1: 0.65, 2: 0.45, 3: 0.25 };  // dist-from-target -> multiplier
    for (let i = 0; i < 26; i++) {
      if (i > tgt) {
        out[i] = 0;
      } else {
        const d = tgt - i;
        if (d in taper) out[i] = _round(cur[i] * taper[d]);
      }
    }
    return {
      parsed: true,
      newForecast: out,
      type: 'eol',
      summary: `Wind-down: forecast tapers W${tgt - 2}-W${tgt + 1} (85%->25% of current) and zeros W${tgt + 2}-W26.`,
      deltaTotal: out.reduce((a,b)=>a+b,0) - cur.reduce((a,b)=>a+b,0),
    };
  }

  // Pattern: SKU transition / replacement -> zero from W{a} through W26
  // "transitioning to EC Suffix starting W13"
  // "switching to new item from W10"
  // "replacing with FF30755EC starting week 15"
  // "migrating to new sku from W8"
  m = lo.match(/(?:transition(?:ing)?|switch(?:ing)?|migrat(?:e|ing)?|replac(?:e|ing)?)\b[^\d]*(?:starting|from|beginning|at|w(?:eek)?\s*)?[^\d]*w?(\d{1,2})/);
  if (m) {
    const a = _clamp(parseInt(m[1], 10));
    for (let i = a; i <= 25; i++) out[i] = 0;
    return {
      parsed: true,
      newForecast: out,
      type: 'zero_from',
      summary: `Zero out from W${a + 1} through W26 (SKU transition / replacement).`,
      deltaTotal: out.reduce((a,b)=>a+b,0) - cur.reduce((a,b)=>a+b,0),
    };
  }

  // Pattern: zero / cover with PO in W{a}[-W{b}]
  // When "starting / from / onward" language is present with a single week,
  // extend the zero to W26 (e.g. "zero starting W13" = zero W13-W26).
  m = lo.match(/(?:zero|no\s*orders?|po\s*covers?|covered\s*by\s*po)[^\d]*w(?:k|eek)?\s*(\d{1,2})(?:\s*[--]\s*w(?:k|eek)?\s*(\d{1,2}))?/);
  if (m) {
    const a = _clamp(parseInt(m[1], 10));
    const hasFrom = /\b(?:starting|from|onward|forward|onwards|through\s+end)\b/.test(lo);
    const b = m[2] ? _clamp(parseInt(m[2], 10)) : (hasFrom ? 25 : a);
    for (let i = a; i <= b; i++) out[i] = 0;
    return {
      parsed: true,
      newForecast: out,
      type: 'zero_range',
      summary: hasFrom && !m[2]
        ? `Zero out from W${a + 1} through W26 (transition / phase-out).`
        : `Zero out W${a + 1}${b !== a ? `-W${b + 1}` : ''} (PO covers / pause).`,
      deltaTotal: out.reduce((a,b)=>a+b,0) - cur.reduce((a,b)=>a+b,0),
    };
  }

  // Pattern: set baseline to N units/wk for W{a}[-W{b}] (or all 26)
  m = lo.match(/(?:set|baseline|target|hold[\s]+at|run\s*rate)[^\d]*([\d,]+)\s*(?:u(?:nits?)?\s*\/?\s*wk|\/\s*wk|per\s*wk|per\s*week|units|u)?(?:[^\d]*w?(\d{1,2}))?(?:\s*[--]\s*w?(\d{1,2}))?/);
  if (m && parseFloat(m[1].replace(/,/g, '')) > 0) {
    const baseN = Math.round(parseFloat(m[1].replace(/,/g, '')));
    const a = m[2] ? _clamp(parseInt(m[2], 10)) : 0;
    const b = m[3] ? _clamp(parseInt(m[3], 10)) : 25;
    for (let i = a; i <= b; i++) out[i] = baseN;
    return {
      parsed: true,
      newForecast: out,
      type: 'set_baseline',
      summary: `Set forecast to ${baseN.toLocaleString()}/wk for W${a + 1}-W${b + 1}.`,
      deltaTotal: out.reduce((a,b)=>a+b,0) - cur.reduce((a,b)=>a+b,0),
    };
  }

  // Pattern: +/-N pcs/units in/for W{a}[-W{b}]  (one-time absolute add/remove for specific week(s))
  // e.g. "+4385 units in w13",  "-500 units w5-w7",  "add 200 units for w8",
  //      "additional 4385u w13",  "+912 pcs OTB in W22 for holiday",
  //      "OTB 500 pcs W15",  "912 pcs W22",  "+4385 units w13-w14 for PDQ secondary placement"
  {
    // Sign-prefix form: "+4385 units in w13" / "+912 pcs OTB in W22"
    // Unit keyword optional when sign explicit; allow up to 30 chars context before week.
    let _wkM = lo.match(/([+-])\s*(\d+(?:,\d{3})*)\s*(?:pcs?|pieces?|units?|u|cases?|cs)?[^%\d]{0,30}w(?:k|eek)?\s*(\d{1,2})(?:\s*[-–]\s*w(?:k|eek)?\s*(\d{1,2}))?/);
    // Verb / context form: "add 200 units for w8" / "OTB 912 pcs W22" / "additional 4385 units w13"
    if (!_wkM) {
      const _v = lo.match(/(?:add(?:ing|ed)?|additional|extra|otb|order(?:ing)?)\s*(?:by\s+)?(\d+(?:,\d{3})*)\s*(?:pcs?|pieces?|units?|u|cases?|cs)?[^%\d]{0,30}w(?:k|eek)?\s*(\d{1,2})(?:\s*[-–]\s*w(?:k|eek)?\s*(\d{1,2}))?/);
      if (_v) _wkM = [null, '+', _v[1], _v[2], _v[3]];
    }
    // Bare form: "912 pcs W22" / "912 pcs in W22" (unit keyword required to anchor match)
    if (!_wkM) {
      const _b = lo.match(/(?:^|\s)(\d+(?:,\d{3})*)\s*(?:pcs?|pieces?|cases?|cs)[^%\d]{0,20}w(?:k|eek)?\s*(\d{1,2})(?:\s*[-–]\s*w(?:k|eek)?\s*(\d{1,2}))?/);
      if (_b) _wkM = [null, '+', _b[1], _b[2], _b[3]];
    }
    if (_wkM) {
      const sign  = _wkM[1] === '-' ? -1 : 1;
      const delta = parseInt((_wkM[2] || '').replace(/,/g, ''), 10) * sign;
      const a = _clamp(parseInt(_wkM[3], 10));
      const b = _wkM[4] ? _clamp(parseInt(_wkM[4], 10)) : a;
      if (delta !== 0 && !isNaN(a)) {
        for (let i = a; i <= b; i++) out[i] = Math.max(0, cur[i] + delta);
        const beforeTot = cur.slice(a, b + 1).reduce((s, v) => s + v, 0);
        const afterTot  = out.slice(a, b + 1).reduce((s, v) => s + v, 0);
        return {
          parsed: true, newForecast: out, type: 'absolute_units_week',
          summary: `${sign > 0 ? '+' : ''}${delta.toLocaleString()} units in W${a + 1}${b !== a ? `-W${b + 1}` : ''} (${beforeTot.toLocaleString()}u -> ${afterTot.toLocaleString()}u).`,
          deltaTotal: out.reduce((s, v) => s + v, 0) - cur.reduce((s, v) => s + v, 0),
        };
      }
    }
  }

  // Pattern: +/-X% lift|cut|boost in W{a}[-W{b}] (or "starting W{a}" -> W26)
  // Catches: "+25% W8-W12", "lift 30% from W10", "cut 15% W22-W26",
  //          "distribution gain 25% starting W8", etc.
  //
  // First try the "week-first" form: "adjust Wk 14 by 50%" / "W22-W26 +30%" /
  // "Wk 14 50%" / "W14 down 15%".  Verb is optional; week comes before pct.
  // Permissive gap [^\d%]* between week range and pct so words like "down",
  // "lift", "by", "of", etc. can sit there freely; verb-detection in lo
  // handles sign.
  m = lo.match(/(?:adjust|change|update|set|lift|cut|boost|bump|gain|drop|raise|reduce|increase|decrease)?\s*w(?:k|eek)?\s*(\d{1,2})(?:\s*[--]\s*w(?:k|eek)?\s*(\d{1,2}))?[^\d%]*([+-]?)\s*(\d+(?:\.\d+)?)\s*%/);
  if (m) {
    let sign = m[3] === '-' ? -1 : 1;
    if (/cut|drop|decrease|reduction|down|reduce/.test(lo)) sign = -1;
    if (/lift|boost|bump|gain|increase|up|raise/.test(lo)) sign = 1;
    const pct = parseFloat(m[4]);
    const a = _clamp(parseInt(m[1], 10));
    const b = m[2] ? _clamp(parseInt(m[2], 10)) : a;  // single week if no range
    const mult = 1 + sign * (pct / 100);
    for (let i = a; i <= b; i++) out[i] = _round(cur[i] * mult);
    const dir = sign > 0 ? 'lift' : 'cut';
    return {
      parsed: true, newForecast: out, type: 'pct_range',
      summary: `${sign > 0 ? '+' : '-'}${pct}% ${dir} applied to W${a + 1}${b !== a ? `-W${b + 1}` : ''} ` +
               `(${cur.slice(a, b + 1).reduce((x,y)=>x+y,0).toLocaleString()}u -> ` +
               `${out.slice(a, b + 1).reduce((x,y)=>x+y,0).toLocaleString()}u).`,
      deltaTotal: out.reduce((a,b)=>a+b,0) - cur.reduce((a,b)=>a+b,0),
    };
  }
  // Fall back to the original percent-first pattern
  m = lo.match(/([+-]?)\s*(\d+(?:\.\d+)?)\s*%\s*(?:lift|boost|bump|gain|increase|cut|drop|decrease|reduction|down|up)?[^\dwW]*(?:starting|from|in|on|for|across)?[^\dwW]*w(?:k|eek)?\s*(\d{1,2})(?:\s*[--]\s*w(?:k|eek)?\s*(\d{1,2}))?/);
  // Also handle "distribution gain 25% starting W8" where number comes after a word
  if (!m) {
    m = lo.match(/(?:gain(?:ed|ing)?|adding|losing|loss(?:ed|ing)?|drop(?:ped)?)[^\d%]*(\d+(?:\.\d+)?)\s*%[^\dwW]*(?:starting|from|in|on|for)?[^\dwW]*w?(\d{1,2})(?:\s*[--]\s*w?(\d{1,2}))?/);
    if (m) {
      // Synthesize a sign from the verb
      const verb = lo.includes('los') || lo.includes('drop') || lo.includes('cut') ? '-' : '+';
      m = ['', verb, m[1], m[2], m[3]];
    }
  }
  if (m) {
    let sign = m[1] === '-' ? -1 : 1;
    // If the verb is negative, override sign even if user wrote no minus
    if (/cut|drop|decrease|reduction|down|los|reduce|lower|pull[\s]*back|trim|slow|soften/.test(lo)) sign = -1;
    const pct = parseFloat(m[2]);
    const a = _clamp(parseInt(m[3], 10));
    const b = m[4] ? _clamp(parseInt(m[4], 10)) : 25;
    const mult = 1 + sign * (pct / 100);
    for (let i = a; i <= b; i++) out[i] = _round(cur[i] * mult);
    const dir = sign > 0 ? 'lift' : 'cut';
    return {
      parsed: true,
      newForecast: out,
      type: 'pct_range',
      summary: `${sign > 0 ? '+' : '-'}${pct}% ${dir} applied to W${a + 1}-W${b + 1} ` +
               `(${cur.slice(a, b + 1).reduce((x,y)=>x+y,0).toLocaleString()}u -> ` +
               `${out.slice(a, b + 1).reduce((x,y)=>x+y,0).toLocaleString()}u).`,
      deltaTotal: out.reduce((a,b)=>a+b,0) - cur.reduce((a,b)=>a+b,0),
    };
  }

  // -- Layer 2: natural-language patterns (no explicit Wx required) ---------
  // Fired when Layer 1 didn't catch any explicit week-number form.

  // 2a) Date-based EOL: "EOL by Aug 14" / "wind down by 9/15"
  m = lo.match(/(?:eol|wind[-\s]*down|discontinu(?:e|ed|ing)|phase[-\s]*out|end[-\s]*of[-\s]*life)[^\d]*((?:\d{1,2}\/\d{1,2})|(?:(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\s+\d{1,2}))/);
  if (m) {
    const tgt = _dateToWeekIdx(m[1]);
    if (tgt !== null) {
      const taper = { 0: 0.25, 1: 0.45, 2: 0.65, 3: 0.85 };
      for (let i = 0; i < 26; i++) {
        if (i > tgt) out[i] = 0;
        else {
          const d = tgt - i;
          if (d in taper) out[i] = _round(cur[i] * taper[d]);
        }
      }
      return {
        parsed: true, newForecast: out, type: 'eol_date',
        summary: `Wind-down by ${m[1]} (~ W${tgt + 1}): tapers W${Math.max(1, tgt - 1)}-W${tgt + 1} (85%->25%) and zeros after.`,
        deltaTotal: out.reduce((a,b)=>a+b,0) - cur.reduce((a,b)=>a+b,0),
      };
    }
  }

  // 2b) Month + percentage in any order:
  //   "boost June by 30%" / "+25% in May" / "May -15%" / "October orders 20%"
  const monthList = '(may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?)';
  // 2b-i) Pct-first: "+25% in May" / "boost 25% in May" / "May 25%"
  m = lo.match(new RegExp(`(?:adjust|change|boost|lift|cut|drop|raise|reduce|increase|decrease|gain|loss)?\\s*([+-]?)\\s*(\\d+(?:\\.\\d+)?)\\s*%[^a-z]*(?:in|for|across|throughout|during|of)?\\s*` + monthList + `(?:[^a-z]*(?:to|through|until|-|-)[^a-z]*` + monthList + `)?`));
  // 2b-ii) Month-first: "boost June by 30%" / "October orders 20%"
  if (!m) {
    m = lo.match(new RegExp(`(?:adjust|change|boost|lift|cut|drop|raise|reduce|increase|decrease|gain|loss)?\\s*` + monthList + `(?:[^a-z]*(?:to|through|until|-|-)[^a-z]*` + monthList + `)?[^\\d%]*([+-]?)\\s*(\\d+(?:\\.\\d+)?)\\s*%`));
    if (m) {
      // Re-order capture groups to match the pct-first form:
      // [match, sign, pct, month1, month2]
      m = [m[0], m[3], m[4], m[1], m[2]];
    }
  }
  if (m) {
    let sign = m[1] === '-' ? -1 : 1;
    if (/cut|drop|decrease|reduction|down|los|reduce|lower|pull[\s]*back|trim|slow|soften/.test(lo)) sign = -1;
    if (/lift|boost|bump|gain|increase|up|raise|grow|ramp\s*up/.test(lo)) sign = 1;
    const pct = parseFloat(m[2]);
    const r1 = _monthRange(m[3]);
    const r2 = m[4] ? _monthRange(m[4]) : null;
    if (r1) {
      const a = r1[0];
      const b = r2 ? r2[1] : r1[1];
      const mult = 1 + sign * (pct / 100);
      for (let i = a; i <= b; i++) out[i] = _round(cur[i] * mult);
      return {
        parsed: true, newForecast: out, type: 'pct_month',
        summary: `${sign > 0 ? '+' : '-'}${pct}% ${sign > 0 ? 'lift' : 'cut'} applied to ${m[3]}${m[4] ? '-' + m[4] : ''} (W${a + 1}-W${b + 1}, ${cur.slice(a, b + 1).reduce((x,y)=>x+y,0).toLocaleString()}u -> ${out.slice(a, b + 1).reduce((x,y)=>x+y,0).toLocaleString()}u).`,
        deltaTotal: out.reduce((a,b)=>a+b,0) - cur.reduce((a,b)=>a+b,0),
      };
    }
  }

  // 2c) Starting-month pattern (no explicit pct): "ramp up starting June" /
  // "Distribution gain at 200 stores starting July" / "added stores from May"
  // Permissive: any filler text between the verb and the "starting/from"
  // anchor (the verb signals intent; the month signals the window).
  m = lo.match(new RegExp(`(?:ramp\\s*up|increase|grow|boost|build|lift|expand|gain|gained|adding|added|distribution[\\s-]*gain).*?(?:starting|beginning|from|in)\\s+` + monthList));
  if (m) {
    const r = _monthRange(m[1]);
    if (r) {
      const a = r[0];
      // Default +20% lift unless explicit pct elsewhere
      const pct = (lo.match(/(\d+)\s*%/) || [])[1];
      const lift = pct ? parseFloat(pct) / 100 : 0.20;
      for (let i = a; i <= 25; i++) out[i] = _round(cur[i] * (1 + lift));
      return {
        parsed: true, newForecast: out, type: 'ramp_up_month',
        summary: `Ramp up: +${(lift * 100).toFixed(0)}% applied from ${m[1]} (W${a + 1}) through W26.`,
        deltaTotal: out.reduce((a,b)=>a+b,0) - cur.reduce((a,b)=>a+b,0),
      };
    }
  }
  m = lo.match(new RegExp(`(?:ramp\\s*down|decrease|cut|reduce|wind\\s*down|slow)[^a-z]*(?:starting|beginning|from|in)\\s+` + monthList));
  if (m) {
    const r = _monthRange(m[1]);
    if (r) {
      const a = r[0];
      const pct = (lo.match(/(\d+)\s*%/) || [])[1];
      const cut = pct ? parseFloat(pct) / 100 : 0.20;
      for (let i = a; i <= 25; i++) out[i] = _round(cur[i] * (1 - cut));
      return {
        parsed: true, newForecast: out, type: 'ramp_down_month',
        summary: `Ramp down: -${(cut * 100).toFixed(0)}% applied from ${m[1]} (W${a + 1}) through W26.`,
        deltaTotal: out.reduce((a,b)=>a+b,0) - cur.reduce((a,b)=>a+b,0),
      };
    }
  }

  // 2d) "Double" / "triple" / "halve"  -  explicit multipliers.
  // "Double W14" / "triple this account" / "halve June"
  m = lo.match(/(double|triple|quadruple|halve)\s+(?:this[^a-z]*account|the[^a-z]*forecast|w(?:k|eek)?\s*(\d{1,2})|(?:in\s+)?([a-z]+))?/);
  if (m) {
    const verbMul = { double: 2.0, triple: 3.0, quadruple: 4.0, halve: 0.5 };
    const mult = verbMul[m[1]];
    let a = 0, b = 25, label = 'whole 26-week window';
    if (m[2]) {
      a = b = _clamp(parseInt(m[2], 10));
      label = `W${a + 1}`;
    } else if (m[3]) {
      const r = _monthRange(m[3]);
      if (r) { a = r[0]; b = r[1]; label = `${m[3]} (W${a + 1}-W${b + 1})`; }
    }
    for (let i = a; i <= b; i++) out[i] = _round(cur[i] * mult);
    return {
      parsed: true, newForecast: out, type: 'multiplier',
      summary: `${m[1].charAt(0).toUpperCase() + m[1].slice(1)} (x${mult}) applied to ${label}.`,
      deltaTotal: out.reduce((a,b)=>a+b,0) - cur.reduce((a,b)=>a+b,0),
    };
  }

  // 2e) Absolute units: "increase by 500 units/wk" / "add 200 units per week through August"
  m = lo.match(/(?:increase|boost|add|lift|raise|grow)[^\d]*(?:by\s+)?(\d+(?:,\d{3})*)\s*(?:units?\s*\/?\s*wk|\/\s*wk|per\s*wk|per\s*week|units?|u)/);
  let signAbs = 1;
  if (!m) {
    m = lo.match(/(?:decrease|cut|drop|lower|reduce|subtract)[^\d]*(?:by\s+)?(\d+(?:,\d{3})*)\s*(?:units?\s*\/?\s*wk|\/\s*wk|per\s*wk|per\s*week|units?|u)/);
    if (m) signAbs = -1;
  }
  if (m) {
    const incr = parseInt(m[1].replace(/,/g, ''), 10) * signAbs;
    let a = 0, b = 25, label = 'every week';
    // Look for an optional month/range qualifier
    const mm = lo.match(new RegExp(`(?:in|through|across|during)\\s+` + monthList + `(?:[^a-z]*(?:to|through|until|-)[^a-z]*` + monthList + `)?`));
    if (mm) {
      const r1 = _monthRange(mm[1]);
      const r2 = mm[2] ? _monthRange(mm[2]) : null;
      if (r1) { a = r1[0]; b = r2 ? r2[1] : r1[1]; label = `${mm[1]}${mm[2] ? '-' + mm[2] : ''} (W${a + 1}-W${b + 1})`; }
    }
    for (let i = a; i <= b; i++) out[i] = Math.max(0, cur[i] + incr);
    return {
      parsed: true, newForecast: out, type: 'absolute_units',
      summary: `${signAbs > 0 ? 'Add' : 'Remove'} ${Math.abs(incr).toLocaleString()} units/wk to ${label}.`,
      deltaTotal: out.reduce((a,b)=>a+b,0) - cur.reduce((a,b)=>a+b,0),
    };
  }

  // 2f) Whole-period pct without explicit week: "boost this account 30%" /
  // "+25% across the board" / "increase forecast by 15%"
  m = lo.match(/(?:adjust|boost|lift|cut|drop|raise|reduce|increase|decrease|gain|loss|bump|grow)[^\d]*([+-]?)\s*(\d+(?:\.\d+)?)\s*%/);
  if (!m) {
    m = lo.match(/([+-])\s*(\d+(?:\.\d+)?)\s*%(?:\s*(?:across|throughout|all|every|whole))?/);
  }
  if (m) {
    let sign = m[1] === '-' ? -1 : 1;
    if (/cut|drop|decrease|reduction|down|los|reduce|lower|pull[\s]*back|trim|slow|soften/.test(lo)) sign = -1;
    if (/lift|boost|bump|gain|increase|up|raise|grow/.test(lo)) sign = 1;
    const pct = parseFloat(m[2]);
    if (pct > 0) {
      const mult = 1 + sign * (pct / 100);
      for (let i = 0; i < 26; i++) out[i] = _round(cur[i] * mult);
      return {
        parsed: true, newForecast: out, type: 'pct_whole',
        summary: `${sign > 0 ? '+' : '-'}${pct}% ${sign > 0 ? 'lift' : 'cut'} applied across all 26 weeks (${cur.reduce((x,y)=>x+y,0).toLocaleString()}u -> ${out.reduce((x,y)=>x+y,0).toLocaleString()}u).`,
        deltaTotal: out.reduce((a,b)=>a+b,0) - cur.reduce((a,b)=>a+b,0),
      };
    }
  }

  // -- Fall-through: free-form text I can't auto-translate ------------------
  return {
    parsed: false,
    summary: "I couldn't translate that into a specific 26-week diff. Examples I can handle: " +
             "\"+912 pcs OTB in W22\", \"add 200 units/wk through October\", \"boost June by 30%\", " +
             "\"+25% in May for grooming season\", \"EOL by Aug 14\", \"double W14\", " +
             "\"ramp up starting July\", \"-15% across the board\". Save as plain comment instead?",
  };
}

function previewAiAdjustment(key) {
  const safeKey = key.replace(/'/g, "&#39;");
  const safeId  = key.replace(/[^a-zA-Z0-9]/g, '_');
  const ta = document.getElementById('ai-adj-text-' + safeId);
  const previewDiv = document.getElementById('ai-adj-preview-' + safeId);
  if (!ta || !previewDiv) return;
  const text = ta.value.trim();
  if (!text) {
    previewDiv.innerHTML = '<div style="color:#c62828;font-size:11px;">Enter what changed first.</div>';
    return;
  }
  const rec = ALL_RECORDS.find(x => x.key === key);
  if (!rec) return;
  // Use AI forecast as the starting point (not Manual  -  Manual is the planner's
  // pre-existing override).  AI is the model's recommendation; the comment is
  // the planner's adjustment to that recommendation.
  const cur = rec.ai_fcst || [];
  const result = _parseAiAdjustment(text, cur);
  if (!result.parsed) {
    previewDiv.innerHTML =
      `<div style="color:#c62828;font-size:11px;padding:6px 0;">${result.summary}</div>` +
      `<button onclick="saveAiCommentOnly('${safeKey}')" style="font-size:11px;padding:4px 12px;background:#fff;border:1px solid #888;color:#333;border-radius:4px;cursor:pointer;">Save as plain comment</button>`;
    return;
  }
  // Cache the proposal on the record for the apply step.
  rec._ai_adjust_proposal = result.newForecast;
  rec._ai_adjust_text     = text;
  // Side-by-side BEFORE/AFTER table.  Highlight changed cells.
  let tbl = '<table style="font-size:11px;border-collapse:collapse;margin:6px 0;width:100%;">';
  tbl += '<tr style="background:#f5f5f5;"><th style="padding:2px 4px;text-align:left;">Wk</th>';
  for (let i = 0; i < 26; i++) tbl += `<th style="padding:2px 4px;border-bottom:1px solid #ddd;">W${i + 1}</th>`;
  tbl += '<th style="padding:2px 4px;background:#fff;">Total</th></tr>';
  // Current row
  tbl += '<tr><td style="padding:2px 4px;color:#555;">Current AI</td>';
  let curTot = 0;
  for (let i = 0; i < 26; i++) { curTot += cur[i] || 0; tbl += `<td style="padding:2px 4px;color:#888;text-align:right;">${(cur[i] || 0).toLocaleString()}</td>`; }
  tbl += `<td style="padding:2px 4px;text-align:right;font-weight:600;color:#555;">${curTot.toLocaleString()}</td></tr>`;
  // Proposed row
  tbl += '<tr><td style="padding:2px 4px;color:#1565c0;font-weight:600;">Proposed</td>';
  let newTot = 0;
  for (let i = 0; i < 26; i++) {
    const v = result.newForecast[i] || 0;
    newTot += v;
    const changed = v !== (cur[i] || 0);
    const bg = changed ? (v > (cur[i] || 0) ? '#e8f5e9' : '#ffebee') : 'transparent';
    const color = changed ? '#1565c0' : '#888';
    tbl += `<td style="padding:2px 4px;background:${bg};color:${color};text-align:right;font-weight:${changed ? 700 : 400};">${v.toLocaleString()}</td>`;
  }
  tbl += `<td style="padding:2px 4px;text-align:right;font-weight:700;color:#1565c0;background:#e3f2fd;">${newTot.toLocaleString()}</td></tr>`;
  tbl += '</table>';
  const dt = result.deltaTotal;
  const dtColor = dt > 0 ? '#2e7d32' : dt < 0 ? '#c62828' : '#888';
  previewDiv.innerHTML = `
    <div style="background:#f0f7ff;border:1px solid #1565c0;border-radius:4px;padding:8px;margin-top:6px;">
      <div style="font-size:12px;font-weight:600;color:#1565c0;margin-bottom:4px;">
         AI's interpretation: ${result.summary}
        <span style="margin-left:8px;color:${dtColor};"> ${dt > 0 ? '+' : ''}${dt.toLocaleString()}u</span>
      </div>
      <div style="overflow-x:auto;">${tbl}</div>
      <div style="display:flex;gap:8px;margin-top:6px;">
        <button onclick="applyAiAdjustment('${safeKey}')" style="font-size:11px;padding:5px 14px;background:#1565c0;color:#fff;border:none;border-radius:4px;cursor:pointer;font-weight:600;">Apply to AI Forecast</button>
        <button onclick="cancelAiAdjustment('${safeKey}')" style="font-size:11px;padding:5px 14px;background:#fff;color:#888;border:1px solid #ccc;border-radius:4px;cursor:pointer;">Cancel</button>
      </div>
    </div>`;
}

// Encode the parser's resolved adjustment as a CALENDAR-STABLE structured tag.
// Stored alongside the planner's comment so future forecast runs can apply
// the SAME calendar weeks, not the same week-indices (W14 today != W14 in 3
// weeks because the rolling 26-wk horizon shifts).  Format:
//   [ai-intent v=YYYY-MM-DD=N v=YYYY-MM-DD=N ...]
// where each YYYY-MM-DD is the date of W1 of that target week, and N is the
// absolute integer value the planner wanted at that week.  F58 in the
// forecaster reads each date, maps it to the current-horizon week index, and
// writes the value (or skips the date if it's no longer in the horizon).
// Replaces week-number tokens (W7, Wk7, Week 7) in planner-written text with
// their actual calendar date (MM/DD of the Monday that starts that week).
// Keeps the text human-readable in QB history even after the rolling horizon
// has shifted and those week numbers would point to different dates.
// Out-of-range or non-numeric tokens are left untouched.
function _replaceWeekRefsWithDates(text) {
  if (!text || !W1_DATE || isNaN(W1_DATE.getTime())) return text;
  return text.replace(/\bw(?:k|eek)?\s*(\d{1,2})\b/gi, (match, n) => {
    const wkNum = parseInt(n, 10);
    if (wkNum < 1 || wkNum > 26) return match;
    const d = new Date(W1_DATE);
    d.setDate(W1_DATE.getDate() + (wkNum - 1) * 7);
    return `${String(d.getMonth() + 1).padStart(2, '0')}/${String(d.getDate()).padStart(2, '0')}`;
  });
}

function _encodeAiIntent(currentVals, newVals, w1Date) {
  if (!Array.isArray(newVals) || newVals.length !== 26) return '';
  if (!w1Date) return '';
  const parts = [];
  for (let i = 0; i < 26; i++) {
    const cv = Math.round(currentVals[i] || 0);
    const nv = Math.round(newVals[i] || 0);
    if (nv === cv) continue;  // unchanged  -  skip
    const d = new Date(w1Date.getTime());
    d.setDate(d.getDate() + i * 7);
    const iso = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
    parts.push(`${iso}=${nv}`);
  }
  if (!parts.length) return '';
  return `[ai-intent ${parts.join(' ')}]`;
}

async function applyAiAdjustment(key) {
  const rec = ALL_RECORDS.find(x => x.key === key);
  if (!rec || !rec._ai_adjust_proposal) return;
  const vals = rec._ai_adjust_proposal;   // 26-element array, AI-only

  // Update the in-memory AI forecast \u2014 does NOT touch MAN projections at all.
  rec.ai_fcst  = vals.slice();
  rec.ai_total = vals.reduce((a, b) => a + b, 0);
  rec.ai_wk    = Math.round(((rec.ai_total + (rec.opn_total || 0)) / 26) * 10) / 10;
  _refreshRowMetrics(key);

  // Re-render the detail panel (AI row reflects the new values immediately).
  const detEl = document.getElementById('detail-' + key);
  if (detEl && detEl.style.display !== 'none') {
    detEl.dataset.loaded = '0';
    detEl.style.display  = 'none';   // toggleDetail re-opens + rebuilds
    toggleDetail(key);
  }

  // Encode intent for potential future F58 replay (stored in the note).
  const intent   = _encodeAiIntent(rec.ai_fcst || [], vals, W1_DATE);
  const noteText = `${_replaceWeekRefsWithDates(rec._ai_adjust_text)}${intent ? ' ' + intent : ''}`;

  const safeId    = key.replace(/[^a-zA-Z0-9]/g, '_');
  const previewDiv = document.getElementById('ai-adj-preview-' + safeId);

  // Write AI_PRJ_W1-W26 directly to QB Projections table so the change persists
  // (the forecaster will overwrite on next run, but this keeps the viewer
  // consistent until then).
  try {
    if (CFG.AI_PRJ_FIDS && CFG.AI_PRJ_FIDS.length === 26) {
      const fields = {};
      fields[CFG.FID.KEY] = { value: key };
      CFG.AI_PRJ_FIDS.forEach((fid, i) => { fields[fid] = { value: vals[i] || 0 }; });
      await qb('/records', { to: CFG.PROJECTIONS_TID, data: [fields], mergeFieldId: CFG.FID.KEY });
    }
    if (previewDiv) {
      previewDiv.innerHTML = `<div style="color:#2e7d32;font-size:11px;padding:6px 0;">\u2713 AI Forecast updated (W1-W26). Manual projections unchanged.</div>`;
    }
  } catch (e) {
    if (previewDiv) {
      previewDiv.innerHTML = `<div style="color:#c62828;font-size:11px;padding:6px 0;">(!) AI display updated but QB write failed: ${e.message}</div>`;
    }
  }

  // Save to AI Comments table (bv2jirwts)  \u2014  kept separate from Projection
  // Comments (planner <-> mgr) so the AI Event thread stays isolated.
  // loadCommentHistory() reads both tables and shows both in the history panel.
  await _USER_READY;
  try {
    const A = CFG.AI_COMMENT_FID;
    const aiFields = {};
    aiFields[A.ACCT_MSTYLE] = { value: key };
    aiFields[A.NOTE]        = { value: noteText };
    aiFields[A.IGNORED]     = { value: false };
    if (A.AUTHOR && CURRENT_USER.email) aiFields[A.AUTHOR] = { value: CURRENT_USER.email };
    await qb('/records', { to: CFG.AI_COMMENTS_TID, data: [aiFields] });
  } catch (e) {
    if (previewDiv) {
      previewDiv.innerHTML += `<div style="color:#c62828;font-size:11px;padding:6px 0;">(!) AI comment save failed: ${e.message}</div>`;
    }
  }

  // Reload comment history panel so the new AI entry appears immediately.
  if (typeof loadCommentHistory === 'function') loadCommentHistory(key, true);
  const ta = document.getElementById('ai-adj-text-' + safeId);
  if (ta) ta.value = '';
  delete rec._ai_adjust_proposal;
  delete rec._ai_adjust_text;
}

function cancelAiAdjustment(key) {
  const safeId = key.replace(/[^a-zA-Z0-9]/g, '_');
  const previewDiv = document.getElementById('ai-adj-preview-' + safeId);
  if (previewDiv) previewDiv.innerHTML = '';
  const rec = ALL_RECORDS.find(x => x.key === key);
  if (rec) {
    delete rec._ai_adjust_proposal;
    delete rec._ai_adjust_text;
  }
}

// Mark an AI Adjustment History entry as inactive  -  flips [Ignored]=true
// on the AI Comments record so F58 stops re-applying it on future
// forecaster runs.  Audit trail is preserved (the comment row stays);
// pane greys it out and removes the x Ignore button.
async function ignoreAiComment(rid, key) {
  if (!rid) return;
  if (!confirm('Stop applying this adjustment on future forecast runs? The comment stays in your history as an audit trail; F58 just skips it next time.')) return;
  try {
    const A = CFG.AI_COMMENT_FID;
    const fields = {};
    fields[A.RECORD_ID] = { value: rid };
    fields[A.IGNORED]   = { value: true };
    await qb('/records', {
      to: CFG.AI_COMMENTS_TID,
      data: [fields],
      mergeFieldId: A.RECORD_ID,
    });
    if (typeof loadCommentHistory === 'function') loadCommentHistory(key, true);
  } catch (e) {
    alert('Could not mark as ignored: ' + (e.message || e));
  }
}

// Restore an ignored AI adjustment  -  flips [Ignored]=false so F58 resumes
// applying it on future forecaster runs.  Mirrors ignoreAiComment exactly.
async function restoreAiComment(rid, key) {
  if (!rid) return;
  if (!confirm('Re-activate this adjustment? F58 will apply it again on the next forecaster run.')) return;
  try {
    const A = CFG.AI_COMMENT_FID;
    const fields = {};
    fields[A.RECORD_ID] = { value: rid };
    fields[A.IGNORED]   = { value: false };
    await qb('/records', {
      to: CFG.AI_COMMENTS_TID,
      data: [fields],
      mergeFieldId: A.RECORD_ID,
    });
    if (typeof loadCommentHistory === 'function') loadCommentHistory(key, true);
  } catch (e) {
    alert('Could not restore: ' + (e.message || e));
  }
}
window.restoreAiComment = restoreAiComment;

// Inline edit for AI Comment note text.
// editAiComment   — swaps the display div for an editable textarea
// cancelAiCommentEdit — reverts without saving
// saveAiCommentEdit   — writes updated NOTE to QB, preserving the [ai-intent] machine tag
function editAiComment(rid) {
  const displayEl = document.getElementById('ai-note-display-' + rid);
  const editEl    = document.getElementById('ai-note-edit-'    + rid);
  const ta        = document.getElementById('ai-note-ta-'      + rid);
  if (!displayEl || !editEl) return;
  displayEl.style.display = 'none';
  editEl.style.display    = 'block';
  if (ta) { ta.focus(); ta.setSelectionRange(ta.value.length, ta.value.length); }
}
window.editAiComment = editAiComment;

function cancelAiCommentEdit(rid) {
  const displayEl = document.getElementById('ai-note-display-' + rid);
  const editEl    = document.getElementById('ai-note-edit-'    + rid);
  if (!displayEl || !editEl) return;
  displayEl.style.display = '';
  editEl.style.display    = 'none';
}
window.cancelAiCommentEdit = cancelAiCommentEdit;

async function saveAiCommentEdit(rid, key) {
  const ta    = document.getElementById('ai-note-ta-'      + rid);
  const rowEl = document.getElementById('ai-comment-row-'  + rid);
  if (!ta || !rowEl) return;
  const newText = ta.value.trim();
  if (!newText) return;
  // Re-append the [ai-intent ...] machine tag from the original note so
  // the forecaster can still replay the numeric adjustment after the edit.
  const fullNote    = rowEl.dataset.fullNote || '';
  const intentMatch = fullNote.match(/\s*\[ai-intent[^\]]*\]/);
  const intentTag   = intentMatch ? intentMatch[0] : '';
  const finalNote   = newText + intentTag;
  const saveBtn = ta.parentElement && ta.parentElement.querySelector('button');
  if (saveBtn) { saveBtn.disabled = true; saveBtn.textContent = 'Saving...'; }
  try {
    const A = CFG.AI_COMMENT_FID;
    const fields = {};
    fields[A.RECORD_ID] = { value: rid };
    fields[A.NOTE]      = { value: finalNote };
    await qb('/records', { to: CFG.AI_COMMENTS_TID, data: [fields], mergeFieldId: A.RECORD_ID });
    if (typeof loadCommentHistory === 'function') loadCommentHistory(key, true);
  } catch (e) {
    if (saveBtn) { saveBtn.disabled = false; saveBtn.textContent = 'Save'; }
    alert('Save failed: ' + (e.message || e));
  }
}
window.saveAiCommentEdit = saveAiCommentEdit;

async function saveAiCommentOnly(key) {
  // Couldn't parse  -  save the planner's text as an AI Comment anyway, for
  // the audit trail (someone may pick up the thread later).  Writes to
  // AI Comments table directly with [Ignored]=true so F58 won't try to
  // re-apply an unparseable instruction.
  const safeId = key.replace(/[^a-zA-Z0-9]/g, '_');
  const ta = document.getElementById('ai-adj-text-' + safeId);
  const previewDiv = document.getElementById('ai-adj-preview-' + safeId);
  if (!ta || !ta.value.trim()) return;
  await _USER_READY;
  try {
    const A = CFG.AI_COMMENT_FID;
    const fields = {};
    fields[A.ACCT_MSTYLE] = { value: key };
    fields[A.NOTE]        = { value: _replaceWeekRefsWithDates(ta.value.trim()) };
    fields[A.IGNORED]     = { value: true };   // unparseable -> never replay
    if (A.AUTHOR && CURRENT_USER.email) fields[A.AUTHOR] = { value: CURRENT_USER.email };
    await qb('/records', { to: CFG.AI_COMMENTS_TID, data: [fields] });
    ta.value = '';
    if (previewDiv) previewDiv.innerHTML = '<div style="color:#2e7d32;font-size:11px;padding:6px 0;">\u2713 Saved as comment (marked Ignored  -  F58 will not auto-apply).</div>';
    if (typeof loadCommentHistory === 'function') loadCommentHistory(key, true);
  } catch (e) {
    if (previewDiv) previewDiv.innerHTML = `<div style="color:#c62828;font-size:11px;padding:6px 0;">Failed: ${e.message}</div>`;
  }
}

function stageFromSource(key, source) {
  const rec = ALL_RECORDS.find(x => x.key === key);
  if (!rec) { alert('Record not found.'); return; }
  const vals = source === 'ai' ? rec.ai_fcst : rec.suggested;
  if (!vals || vals.length !== 26) {
    alert(`No ${source === 'ai' ? 'AI Forecast' : 'Suggested'} values available for this record.`);
    return;
  }
  document.querySelectorAll(`.man-edit[data-key="${key.replace(/"/g,'\\"')}"]`).forEach(el => {
    const w = parseInt(el.dataset.week, 10);
    _setManCell(el, vals[w] || 0);
  });
}

// Fill operations driven by the most-recently-focused cell. Defaults to W1
// if the user clicked a button before clicking a cell.
function fillRowFromFocused(key, mode) {
  const fromIdx = LAST_FOCUSED_BY_KEY.get(key) ?? 0;
  const sel = `.man-edit[data-key="${key.replace(/"/g,'\\"')}"][data-week="${fromIdx}"]`;
  const srcEl = document.querySelector(sel);
  if (!srcEl) return;
  const v = parseInt(srcEl.value, 10);
  if (!isFinite(v) || v < 0) {
    alert(`The selected cell (W${fromIdx + 1}) doesn't have a valid number.`);
    return;
  }
  if (mode === 'all') {
    _setManRange(key, 0, 25, v);
  } else {
    // 'right'  -  from the focused cell to W26, leave cells to the left alone
    _setManRange(key, fromIdx, 25, v);
  }
}

// Set every cell in the record to a constant (used by Zero All).
function fillRowConst(key, val) {
  _setManRange(key, 0, 25, val);
}

// Reset just THIS record's MAN inputs back to their QB-loaded original
// values. Reads each input's data-orig attribute (set when the row was
// rendered) so it works whether the record had unsaved edits or had been
// modified via Stage AI / Fill / paste / etc. Does NOT touch QB.
function resetRow(key) {
  const inputs = document.querySelectorAll(`.man-edit[data-key="${key.replace(/"/g,'\\"')}"]`);
  if (!inputs.length) return;
  // Count what would actually change so we can skip the confirm if it's a no-op
  let changedCount = 0;
  inputs.forEach(el => {
    const orig = parseInt(el.dataset.orig, 10) || 0;
    if (parseInt(el.value, 10) !== orig) changedCount++;
  });
  if (changedCount === 0) return;
  if (!confirm(`Reset this record? ${changedCount} cell(s) will revert to their original QB values. Nothing is written to Quickbase.`)) return;
  inputs.forEach(el => {
    const orig = parseInt(el.dataset.orig, 10) || 0;
    _setManCell(el, orig);
  });
}

// Smart paste: parse clipboard text as a list of numbers and distribute
// across cells starting at the focused one. Single-number pastes fall back
// to native paste behavior.
function smartPaste(inputEl, ev) {
  const cd = ev.clipboardData || window.clipboardData;
  if (!cd) return;
  const txt = cd.getData('text');
  if (!txt) return;
  // Strip $, commas inside numbers, percent signs, then split on any
  // whitespace / tab / newline.
  const cleaned = txt.replace(/[$%]/g, '').replace(/,(?=\d)/g, '');
  const tokens  = cleaned.split(/[\s,]+/).filter(Boolean);
  // Coerce to numbers; non-numeric tokens become NaN and we filter them out.
  const nums = tokens.map(t => parseFloat(t)).filter(n => isFinite(n));
  if (nums.length < 2) return;  // single value > let native paste do its thing
  ev.preventDefault();
  const key      = inputEl.dataset.key;
  const startIdx = parseInt(inputEl.dataset.week, 10) || 0;
  const room     = 26 - startIdx;
  let writeCount = 0;
  document.querySelectorAll(`.man-edit[data-key="${key.replace(/"/g,'\\"')}"]`).forEach(el => {
    const w = parseInt(el.dataset.week, 10);
    const offset = w - startIdx;
    if (offset >= 0 && offset < nums.length && offset < room) {
      _setManCell(el, nums[offset]);
      writeCount++;
    }
  });
  if (nums.length > room) {
    const status = document.getElementById('saveStatus');
    if (status) {
      status.style.color = '#e65100';
      status.textContent = `Pasted ${writeCount} value(s); ${nums.length - room} extra value(s) didn't fit (started at W${startIdx + 1}).`;
      setTimeout(() => { if (status.textContent.startsWith('Pasted')) status.textContent = ''; }, 5000);
    }
  }
}

// Keyboard shortcuts inside MAN inputs. Returning true from any branch means
// "we handled it"  -  the early `return` is what stops the native input
// behavior from running on top of our action.
function manEditKey(inputEl, ev) {
  const key  = inputEl.dataset.key;
  const idx  = parseInt(inputEl.dataset.week, 10);
  // Ctrl+R / Cmd+R  -  fill right from the focused cell to W26
  if ((ev.ctrlKey || ev.metaKey) && (ev.key === 'r' || ev.key === 'R')) {
    ev.preventDefault();
    LAST_FOCUSED_BY_KEY.set(key, idx);
    fillRowFromFocused(key, 'right');
    return;
  }
  // Enter  -  advance to next cell (or blur on the last one)
  if (ev.key === 'Enter') {
    ev.preventDefault();
    _focusNeighbor(key, idx, +1);
    return;
  }
  // ArrowLeft at the very start of the input > previous cell.
  // ArrowRight at the very end > next cell. Otherwise let the caret move
  // normally inside the value.
  if (ev.key === 'ArrowLeft' && inputEl.selectionStart === 0 && inputEl.selectionEnd === 0) {
    ev.preventDefault();
    _focusNeighbor(key, idx, -1);
    return;
  }
  if (ev.key === 'ArrowRight') {
    const len = inputEl.value.length;
    if (inputEl.selectionStart === len && inputEl.selectionEnd === len) {
      ev.preventDefault();
      _focusNeighbor(key, idx, +1);
      return;
    }
  }
}

function _focusNeighbor(key, idx, delta) {
  const target = idx + delta;
  if (target < 0 || target > 25) return;
  const sel = `.man-edit[data-key="${key.replace(/"/g,'\\"')}"][data-week="${target}"]`;
  const el  = document.querySelector(sel);
  if (el) { el.focus(); el.select(); }
}

// -- Filter functions --------------------------------------------------------
//
// Helper: read selections from a multi-select widget.  Empty Set == "All".
function _msSel(id) {
  const el = document.getElementById(id);
  return (el && typeof el._getSelected === 'function') ? el._getSelected() : new Set();
}

// Click a header volume badge > toggle that single tier in the volFilter
// multi-select (clicking an already-active badge clears the selection).
function filterVol(vol) {
  const sel = document.getElementById('volFilter');
  if (!sel || typeof sel._getSelected !== 'function') return;
  const current = sel._getSelected();
  const isOnlyThisOne = current.size === 1 && current.has(vol);
  sel._setSelection(isOnlyThisOne ? [] : [vol]);
  const btns = {'HIGH':'btn-high','MEDIUM':'btn-med','LOW':'btn-low'};
  Object.entries(btns).forEach(([v, id]) => {
    const b = document.getElementById(id);
    if (b) b.classList.toggle('badge-active', !isOnlyThisOne && v === vol);
  });
  applyFilters();
}

function filterPri(pri) {
  const sel = document.getElementById('priFilter');
  if (!sel || typeof sel._getSelected !== 'function') return;
  const current = sel._getSelected();
  const isOnlyThisOne = current.size === 1 && current.has(pri);
  sel._setSelection(isOnlyThisOne ? [] : [pri]);
  const btns = {
    'CRITICAL': 'btn-pri-crit',
    'HIGH':     'btn-pri-high',
    'MID':      'btn-pri-mid',
    'LOW':      'btn-pri-low',
    'On-Plan':  'btn-pri-onplan',
  };
  Object.entries(btns).forEach(([v, id]) => {
    const b = document.getElementById(id);
    if (b) b.classList.toggle('badge-active', !isOnlyThisOne && v === pri);
  });
  applyFilters();
}

function resetAllFilters() {
  const search = document.getElementById('search');
  if (search) search.value = '';
  ['volFilter','priFilter','patFilter','brandFilter','mgrFilter','custFilter','fcstStatusFilter'].forEach(id => {
    const el = document.getElementById(id);
    if (el && typeof el._clearSelection === 'function') el._clearSelection();
  });
  const ai = document.getElementById('aiDiffFilter');
  if (ai) ai.value = '0';
  ['btn-high','btn-med','btn-low','btn-pri-crit','btn-pri-high','btn-pri-mid','btn-pri-low','btn-pri-onplan'].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.classList.remove('badge-active');
  });
  // Clear per-column quick filters and reset the column sort.
  document.querySelectorAll('.col-filter').forEach(el => { el.value = ''; });
  CURRENT_SORT_KEY = null;
  CURRENT_SORT_DIR = 0;
  _updateSortIndicators();
  // Also clear the Flagged-Only toggle when planners hit "Clear Filters"
  //  -  it's a filter from their perspective, even if it lives in its own button.
  FLAGGED_ONLY = false;
  try { sessionStorage.setItem('flaggedOnly', '0'); } catch (e) { /* ignore */ }
  _syncFlaggedOnlyButton();
  SNOOZED_ONLY = false;
  try { sessionStorage.setItem('snoozedOnly', '0'); } catch (e) { /* ignore */ }
  _syncSnoozedOnlyButton();
  SHOW_FOR_ME_ONLY = false;
  _syncForMeButton();
  applyFilters();
}

// -- Per-column sort + filter state ------------------------------------------
// Click a header to cycle  asc -> desc -> off  (off returns to default sort).
// CURRENT_SORT_KEY is the data-sort-key from the <th>; CURRENT_SORT_DIR is
// +1 (ascending), -1 (descending), or 0 (off  -  use default sort).
let CURRENT_SORT_KEY = null;
let CURRENT_SORT_DIR = 0;

// Priority ordinal so the "Priority" column sorts CRITICAL > MEDIUM > LOW
// (or reversed) instead of alphabetically.
const _PRI_ORDINAL = { CRITICAL: 3, MEDIUM: 2, LOW: 1 };

// ai_vs_proj is computed on the fly (not stored on the record), so the sorter
// + column filter need to compute it on demand.
function _aiVsProjPct(r) {
  if (r.proj_total > 0) return (r.ai_total - r.proj_total) / r.proj_total * 100;
  return r.ai_total > 0 ? null : 0;  // null = no plan entered (sorts last); 0 = both zero
}

// Pull a value out of a record by data-sort-key.  Handles the synthetic
// 'ai_vs_proj' field and returns numbers as numbers, strings as strings.
function _recVal(r, key) {
  if (key === 'ai_vs_proj') return _aiVsProjPct(r);
  if (key === 'priority')   return _PRI_ORDINAL[r.priority] || 0;
  return r[key];
}

// Parse a column-filter expression.  Numeric columns understand operators:
//   >50, <50, >=50, <=50, =50, !=50.   Anything else falls back to substring.
// Returns a predicate fn that takes a record value and returns boolean.
function _buildColFilterPred(expr, colType) {
  expr = (expr || '').trim();
  if (!expr) return null;
  if (colType === 'number') {
    const m = expr.match(/^\s*(>=|<=|!=|>|<|=)\s*(-?\d+(?:\.\d+)?)\s*$/);
    if (m) {
      const op = m[1], n = parseFloat(m[2]);
      return v => {
        const x = (typeof v === 'number') ? v : parseFloat(v);
        if (Number.isNaN(x)) return false;
        switch (op) {
          case '>':  return x > n;
          case '<':  return x < n;
          case '>=': return x >= n;
          case '<=': return x <= n;
          case '=':  return x === n;
          case '!=': return x !== n;
        }
        return false;
      };
    }
    // No operator  -  fall through to substring match against the formatted number
  }
  // Substring (case-insensitive) match
  const needle = expr.toLowerCase();
  return v => String(v == null ? '' : v).toLowerCase().includes(needle);
}

// Read all .col-filter inputs and return an array of {field, pred} predicates
// that any record must satisfy to remain in FILTERED_RECORDS.
function _readColFilters() {
  const out = [];
  document.querySelectorAll('.col-filter').forEach(el => {
    const expr  = el.value;
    if (!expr || !expr.trim()) return;
    const field = el.dataset.field;
    const type  = el.dataset.colType || 'string';
    const pred  = _buildColFilterPred(expr, type);
    if (pred) out.push({ field, pred });
  });
  return out;
}

// Update the ^/v indicator on the active sort header (and clear all others).
function _updateSortIndicators() {
  document.querySelectorAll('thead th.sortable').forEach(th => {
    th.classList.remove('sort-asc', 'sort-desc');
    if (th.dataset.sortKey === CURRENT_SORT_KEY && CURRENT_SORT_DIR !== 0) {
      th.classList.add(CURRENT_SORT_DIR > 0 ? 'sort-asc' : 'sort-desc');
    }
  });
}

// Reset sort only  -  leaves global filters and column filters untouched.
// Returns the table to the default Inv Mgr -> Brand -> Customer -> Mstyle order.
function resetSort() {
  CURRENT_SORT_KEY = null;
  CURRENT_SORT_DIR = 0;
  _updateSortIndicators();
  applyFilters();
}

// Click handler  -  cycles the column through asc -> desc -> off.
function cycleSort(key) {
  if (CURRENT_SORT_KEY !== key) {
    CURRENT_SORT_KEY = key;
    CURRENT_SORT_DIR = 1;        // first click = ascending
  } else if (CURRENT_SORT_DIR === 1) {
    CURRENT_SORT_DIR = -1;       // second click = descending
  } else {
    CURRENT_SORT_KEY = null;     // third click = clear (default sort returns)
    CURRENT_SORT_DIR = 0;
  }
  _updateSortIndicators();
  applyFilters();
}

// Sticky toggle for the "Show Flagged Only" toolbar button.  When true,
// applyFilters() only retains records with r.flagged === true.  Persists in
// sessionStorage so the toggle survives page reloads inside the same QB
// session (planners often refresh the codepage after writing a comment).
let FLAGGED_ONLY = (function () {
  try { return sessionStorage.getItem('flaggedOnly') === '1'; }
  catch (e) { return false; }
})();

function toggleFlaggedOnly() {
  FLAGGED_ONLY = !FLAGGED_ONLY;
  try { sessionStorage.setItem('flaggedOnly', FLAGGED_ONLY ? '1' : '0'); }
  catch (e) { /* ignore */ }
  _syncFlaggedOnlyButton();
  applyFilters();
}

// Visual state of the toolbar button reflects whether the filter is active.
// Active = filled red background; inactive = white background with red border.
function _syncFlaggedOnlyButton() {
  const btn = document.getElementById('flaggedOnlyBtn');
  if (!btn) return;
  if (FLAGGED_ONLY) {
    btn.style.background = '#c62828';
    btn.style.color = '#fff';
    btn.title = 'Currently showing flagged records only  -  click to show all';
  } else {
    btn.style.background = '#fff';
    btn.style.color = '#c62828';
    btn.title = 'Show only records flagged for inventory mgr review (toggle)';
  }
}

// Sticky toggle for the "Show Snoozed Only" toolbar button.  When true,
// applyFilters() only retains records with r._snoozed === true.  Persists in
// sessionStorage so the toggle survives page reloads.
let SNOOZED_ONLY = (function () {
  try { return sessionStorage.getItem('snoozedOnly') === '1'; }
  catch (e) { return false; }
})();

function toggleSnoozedOnly() {
  SNOOZED_ONLY = !SNOOZED_ONLY;
  try { sessionStorage.setItem('snoozedOnly', SNOOZED_ONLY ? '1' : '0'); }
  catch (e) { /* ignore */ }
  _syncSnoozedOnlyButton();
  applyFilters();
}

function _syncSnoozedOnlyButton() {
  const btn = document.getElementById('snoozedOnlyBtn');
  if (!btn) return;
  if (SNOOZED_ONLY) {
    btn.style.background = '#757575';
    btn.style.color = '#fff';
    btn.title = 'Currently showing snoozed records only  -  click to show all';
  } else {
    btn.style.background = '#fff';
    btn.style.color = '#757575';
    btn.title = 'Show only snoozed records (toggle)';
  }
}

function applyFilters() {
  const search    = document.getElementById('search').value.toLowerCase();
  const volSet        = _msSel('volFilter');
  const priSet        = _msSel('priFilter');
  const patSet        = _msSel('patFilter');
  const brandSet      = _msSel('brandFilter');
  const mgrSet        = _msSel('mgrFilter');
  const custSet       = _msSel('custFilter');
  const fcstStatusSet = _msSel('fcstStatusFilter');
  const aiDiffEl  = document.getElementById('aiDiffFilter');
  const aiDiffMin = aiDiffEl ? parseFloat(aiDiffEl.value) : 0;
  const colPreds  = _readColFilters();

  // Reflect sticky-toggle state on the button each time filters re-apply
  // (also covers the initial render after the page hydrates).
  _syncFlaggedOnlyButton();
  _syncSnoozedOnlyButton();

  FILTERED_RECORDS = ALL_RECORDS.filter(r => {
    // Flagged-only toggle (top-priority  -  short-circuit before other checks)
    if (FLAGGED_ONLY       && !r.flagged)               return false;
    if (SNOOZED_ONLY       && !r._snoozed)              return false;
    if (SHOW_REPLY_ONLY    && !r.planner_reply_pending) return false;
    if (SHOW_FOR_ME_ONLY && !_FOR_ME_KEYS.has(r.key)) return false;
    if (search) {
      const txt = (r.key + ' ' + r.cust + ' ' + r.mstyle + ' ' + (r.desc||'') + ' ' + (r.brand||'') + ' ' + (r.inv_manager||'')).toLowerCase();
      if (!txt.includes(search)) return false;
    }
    if (volSet.size        && !volSet.has(r.vol_tier))                  return false;
    if (priSet.size && !SNOOZED_ONLY && (r._snoozed || !priSet.has(r.priority))) return false;
    if (patSet.size        && !patSet.has(r.pattern))                  return false;
    if (brandSet.size      && !brandSet.has(r.brand))                  return false;
    if (mgrSet.size        && !mgrSet.has(r.inv_manager))              return false;
    if (custSet.size       && !custSet.has(r.cust))                    return false;
    if (fcstStatusSet.size && !fcstStatusSet.has(r.fcst_status))       return false;
    if (aiDiffMin > 0) {
      // No-plan + AI has demand = infinite divergence, always passes the filter.
      const aiVsProj = r.proj_total > 0 ? Math.abs((r.ai_total - r.proj_total) / r.proj_total * 100)
                     : (r.ai_total > 0 ? Infinity : 0);
      if (aiVsProj < aiDiffMin) return false;
    }
    // Per-column quick filters (AND with everything above)
    for (const {field, pred} of colPreds) {
      if (!pred(_recVal(r, field))) return false;
    }
    return true;
  });

  if (CURRENT_SORT_KEY && CURRENT_SORT_DIR !== 0) {
    // User-driven column sort.  Numeric vs string compare based on value type.
    const k   = CURRENT_SORT_KEY;
    const dir = CURRENT_SORT_DIR;
    FILTERED_RECORDS.sort((a, b) => {
      const va = _recVal(a, k);
      const vb = _recVal(b, k);
      const an = (typeof va === 'number');
      const bn = (typeof vb === 'number');
      if (an && bn) return dir * (va - vb);
      // String compare  -  empty/null sorts last regardless of direction
      const sa = _sortKey(va);
      const sb = _sortKey(vb);
      if (sa === '' && sb !== '') return 1;
      if (sb === '' && sa !== '') return -1;
      return dir * sa.localeCompare(sb);
    });
  } else {
    // Default sort: Customer > Brand > Mstyle.
    FILTERED_RECORDS.sort((a, b) =>
         _sortKey(a.cust       ).localeCompare(_sortKey(b.cust       ))
      || _sortKey(a.brand      ).localeCompare(_sortKey(b.brand      ))
      || _sortKey(a.mstyle     ).localeCompare(_sortKey(b.mstyle     ))
    );
  }
  renderPage(0);
}

// -- Export Flagged to CSV --------------------------------------------------
function exportFlagged() {
  const rows = [['Key','Customer','Mstyle','Description','Inv Mgr','Brand','Priority','Ord/Wk L13W','Proj/Wk (+Open POs)','AI Fcst/Wk (+Open POs)','AI vs Proj','Proj 26w','Last Comment']];
  ALL_RECORDS.forEach(r => {
    if (!r.flagged) return;
    const last = (r.last_comment || '').replace(/"/g,'""');
    rows.push([r.key, r.cust, r.mstyle, r.desc, r.inv_manager, r.brand, r.priority,
               Math.round(r.shp_wk), Math.round(r.proj_wk), Math.round(r.ai_wk),
               (r.proj_total > 0 ? ((r.ai_total-r.proj_total)/r.proj_total*100).toFixed(1)+'%' : '0%'),
               r.proj_total, last]);
  });
  if (rows.length < 2) { alert('No records flagged yet (Flagged checkbox = true).'); return; }
  const csv = rows.map(r => r.map(c => '"'+c+'"').join(',')).join('\n');
  const blob = new Blob([csv], {type:'text/csv'});
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = 'flagged_for_manager.csv';
  a.click();
}

// -- Export All In View to CSV ----------------------------------------------
// Exports every record currently visible in the table (after filters/search)
// to a CSV. Honors FILTERED_RECORDS so what you see is what you get.
function exportAllInView() {
  if (!FILTERED_RECORDS || FILTERED_RECORDS.length === 0) {
    alert('No records in current view to export.');
    return;
  }
  const header = [
    'Key','Inv Manager','Brand','Customer','Mstyle','Description','Priority',
    'Ord/Wk L13W','Shpd/Wk L13W','Proj/Wk (+Open POs)','AI Fcst/Wk (+Open POs)','AI vs Proj %',
    'Proj 26w','AI 26w','Last Comment','Flagged'
  ];
  const rows = [header];
  FILTERED_RECORDS.forEach(r => {
    const pct = (r.proj_total > 0)
                ? ((r.ai_total - r.proj_total) / r.proj_total * 100).toFixed(1) + '%'
                : '0%';
    rows.push([
      r.key,
      r.inv_manager || '',
      r.brand || '',
      r.cust  || '',
      r.mstyle|| '',
      r.desc  || '',
      r.priority || '',
      Math.round(r.shp_wk  || 0),
      Math.round(r.shpd_wk || 0),
      Math.round(r.proj_wk || 0),
      Math.round(r.ai_wk   || 0),
      pct,
      Math.round(r.proj_total || 0),
      Math.round(r.ai_total   || 0),
      (r.last_comment || '').replace(/"/g,'""').replace(/[\r\n]+/g,' '),
      r.flagged ? 'Y' : ''
    ]);
  });
  const csv = rows.map(r => r.map(c => {
    const s = String(c == null ? '' : c).replace(/"/g, '""');
    return '"' + s + '"';
  }).join(',')).join('\r\n');
  // UTF-8 BOM so Excel opens accented chars cleanly
  const blob = new Blob(['' + csv], {type:'text/csv;charset=utf-8'});
  const stamp = new Date().toISOString().slice(0,10);
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = `forecast_view_${stamp}.csv`;
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}

async function clearAllFlags() {
  const flaggedRecs = ALL_RECORDS.filter(r => r.flagged);
  if (flaggedRecs.length === 0) {
    alert('Nothing to clear - no records are flagged.');
    return;
  }
  const msg = `Clear ALL flags?\n\n  * ${flaggedRecs.length} flagged record(s) will have Flagged=false written back to QB\n\nProjection Comments are NOT touched (they live in the Comments table).\n\nContinue?`;
  if (!confirm(msg)) return;
  // Bulk uncheck Flagged in QB (one upsert per chunk of 100)
  const status = document.getElementById('sendStatus');
  if (status) { status.style.color = '#1565c0'; status.textContent = `Clearing ${flaggedRecs.length} flag(s) in QB...`; }
  try {
    const CHUNK = 100;
    for (let i = 0; i < flaggedRecs.length; i += CHUNK) {
      const slice = flaggedRecs.slice(i, i + CHUNK);
      const data = slice.map(r => {
        const f = {};
        f[CFG.FID.KEY]     = { value: r.key };
        f[CFG.FID.FLAGGED] = { value: false };
        return f;
      });
      await qb('/records', { to: CFG.PROJECTIONS_TID, data, mergeFieldId: CFG.FID.KEY });
    }
    flaggedRecs.forEach(r => { r.flagged = false; });
    document.querySelectorAll('.flag-btn').forEach(b => b.classList.remove('flagged'));
    updateFlagCount();
    if (status) { status.style.color = '#2e7d32'; status.textContent = `\u2713 Cleared ${flaggedRecs.length} flag(s)`; }
  } catch (e) {
    if (status) { status.style.color = '#c62828'; status.textContent = 'Clear failed: ' + e.message; }
  }
}

// Expose handlers the inline onclick attributes call
window.toggleDetail   = toggleDetail;
window.toggleFlag        = toggleFlag;
window.toggleAutoProject = toggleAutoProject;
window.autoFlagOnComment = autoFlagOnComment;
window.editStatusCust = editStatusCust;
window.toggleFlaggedOnly  = toggleFlaggedOnly;
window.toggleForMe        = toggleForMe;
window.closeBaseStyle       = closeBaseStyle;
window.saveSwitchoverField  = saveSwitchoverField;
window.previewAiAdjustment = previewAiAdjustment;
window.applyAiAdjustment   = applyAiAdjustment;
window.cancelAiAdjustment  = cancelAiAdjustment;
window.saveAiCommentOnly   = saveAiCommentOnly;
window.ignoreAiComment     = ignoreAiComment;
window.copyToMan      = copyToMan;
window.addComment     = addComment;
window.loadCommentHistory = loadCommentHistory;
window.applyFilters   = applyFilters;
window.filterVol      = filterVol;
window.filterPri      = filterPri;
window.resetAllFilters= resetAllFilters;
window.cycleSort      = cycleSort;
window.resetSort      = resetSort;
window.exportFlagged  = exportFlagged;
window.exportAllInView = exportAllInView;
window.onManEdit          = onManEdit;
window.saveAllManEdits    = saveAllManEdits;
window.discardAllManEdits = discardAllManEdits;
window.markLastEdit        = markLastEdit;
window.smartPaste          = smartPaste;
window.manEditKey          = manEditKey;
window.stageFromSource     = stageFromSource;
window.fillRowFromFocused  = fillRowFromFocused;
window.fillRowConst        = fillRowConst;
window.resetRow            = resetRow;
window.clearAllFlags  = clearAllFlags;
window.changePage     = changePage;

// -- Bootstrap --------------------------------------------------------------
async function bootstrap() {
  const t0 = performance.now();
  try {
    _setBoot('Authenticating...');
    _setDetail(`Exchanging your QB session for a temp token on ${CFG.PROJECTIONS_TID}`);
    try {
      await getTempToken(CFG.PROJECTIONS_TID);
    } catch (e) {
      throw new Error(
        `Could not authenticate against ${CFG.REALM}.  Make sure you are signed in `
      + `to Quickbase in this browser, then reload this page.\n\nDetails: ${e.message}`
      );
    }
    // Decode who the visitor is from the JWT we just fetched.
    // Done early so the name is available before any UI renders.
    await fetchCurrentUser();

    _setBoot('Loading projections...');
    _setDetail('Discovering rolling weekly column IDs (manual prj + Ord LW + Shp LW)');
    await Promise.all([discoverWeeklyFids(), discoverInvFlowTextFids()]);
    _setDetail(`Found 26 manual prj cols (${MAN_PRJ_LABELS[0]} ... ${MAN_PRJ_LABELS[25]})  |  ${ORD_HIST_FIDS.length} Ord LW  |  ${SHP_HIST_FIDS.length} Shp LW`);
    await new Promise(r => setTimeout(r, 16));

    _setBoot('Loading projections...');
    const _prjCached = !_prjCacheBypassed() && await _loadPrjCache();
    if (_prjCached) {
      ALL_RECORDS = _prjCached.records;
      buildSwitchoverMap();
      const ageStr = _fmtCacheAge(_prjCached.ageMs);
      const src = _prjCached.source === 'session' ? 'session cache' : 'IndexedDB cache';
      _setDetail(`Projections: served from ${src} (${ageStr} old) - append ?nocache=1 to URL for a fresh pull`);
      console.info(`[Prj] loaded ${ALL_RECORDS.length.toLocaleString()} records from ${src} (age ${ageStr})`);
    } else {
      // When the visitor's identity is known, try a manager-filtered fetch first.
      // Planners typically own 400–500 records; this cuts load time proportionally.
      // If 0 records come back the visitor is a director/VP — fall back to full load.
      // Directors/VPs in DIRECTOR_EMAILS always get the full dataset even when
      // they also have brands assigned to them as inv_manager.
      let rawRows;
      if (_isDirector()) {
        _setDetail('Director/VP — loading full dataset');
        rawRows = await fetchAllRecords();
      } else if (CURRENT_USER.name) {
        _setDetail(`Querying QB for "${CURRENT_USER.name}" records...`);
        rawRows = await fetchAllRecords(CURRENT_USER.name);
        if (rawRows.length === 0) {
          _setDetail('No records assigned to this user — loading full dataset');
          rawRows = await fetchAllRecords();
        } else {
          _setDetail(`${rawRows.length.toLocaleString()} records for "${CURRENT_USER.name}" received`);
        }
      } else {
        _setDetail('Querying Quickbase for active records');
        rawRows = await fetchAllRecords();
      }
      _setBoot('Parsing projection data...');
      _setDetail(`${rawRows.length.toLocaleString()} records received | adapting to UI shape`);
      await new Promise(r => setTimeout(r, 16));
      ALL_RECORDS = rawRows.map(adaptRow);
      buildSwitchoverMap();
      await _savePrjCache(ALL_RECORDS);
      console.info(`[Prj] saved ${ALL_RECORDS.length.toLocaleString()} records to IndexedDB cache`);
    }
    _setFreshness('prj-loaded-at', Date.now());
    _initSnoozeFlags();   // stamp r._snoozed from localStorage before first render

    // -- Pull Inventory Flow in the background (non-blocking) ---------------
    // inv_flow_* fields are only used in the detail panel, never in the main
    // table, so we fire this without awaiting.  The table renders immediately.
    //
    // 20-second timeout: if the bulk QB scan stalls, we null _invFlowPromise so
    // "Loading inventory balances..." clears in any open detail panel.  The scan
    // continues silently; when it eventually resolves it re-renders the currently
    // open panel so data appears without the user having to re-click.
    const _invFlowLoad = attachInvFlow(ALL_RECORDS);
    const _invFlowTimer = new Promise((_, rej) =>
      setTimeout(() => rej(new Error('inv-flow-timeout')), 20000));

    // Re-render whatever detail panel is currently open after inv flow settles.
    // Called from every settlement path so no panel is ever left on "Loading...".
    function _reRenderOpenPanel() {
      if (!_openDetailKey) return;
      const openEl = document.getElementById('detail-' + _openDetailKey);
      if (!openEl || openEl.style.display !== 'table-row') return;
      openEl.dataset.loaded = '';
      openEl.style.display = 'none';
      toggleDetail(_openDetailKey);
    }

    _invFlowPromise = Promise.race([_invFlowLoad, _invFlowTimer])
      .then(() => {
        _setFreshness('invflow-loaded-at', Date.now());
        _invFlowPromise = null;
        _reRenderOpenPanel();
      })
      .catch(e => {
        _invFlowPromise = null;
        const el = document.getElementById('invflow-loaded-at');
        if (e.message === 'inv-flow-timeout') {
          console.warn('[InvFlow] still loading after 20s - panel will update when ready');
          if (el) el.textContent = 'loading...';
          // Re-render now so "Loading..." clears (shows "(no row)" or whatever data
          // is already attached).  Background load re-renders again when it finishes.
          _reRenderOpenPanel();
          _invFlowLoad.then(() => {
            _setFreshness('invflow-loaded-at', Date.now());
            _reRenderOpenPanel();
          }).catch(e2 => {
            console.warn('[InvFlow] load failed after timeout:', e2.message);
            if (el) el.textContent = 'unavailable';
            _reRenderOpenPanel();
          });
        } else {
          console.warn('[InvFlow] load failed (non-fatal):', e);
          if (el) el.textContent = 'unavailable';
          _reRenderOpenPanel();
        }
      });

    _atsHistPromise = attachAtsHistory(ALL_RECORDS).then(() => {
      _setFreshness('atshist-loaded-at', Date.now());
      _atsHistPromise = null;  // clear so detail panels don't loop for mstyles with no ATS data
    }).catch(e => {
      console.warn('ATS History load failed (non-fatal):', e);
      _atsHistPromise = null;
    });

    _setBoot('Sorting projections...');
    _setDetail(`Ordering ${ALL_RECORDS.length.toLocaleString()} records by Customer > Brand > Mstyle`);
    await new Promise(r => setTimeout(r, 16));
    ALL_RECORDS.sort((a, b) =>
        _sortKey(a.cust       ).localeCompare(_sortKey(b.cust       ))
     || _sortKey(a.brand      ).localeCompare(_sortKey(b.brand      ))
     || _sortKey(a.mstyle     ).localeCompare(_sortKey(b.mstyle     ))
    );

    _setBoot('Building filters...');
    _setDetail('Auto-populating filter dropdowns from real QB values');
    await new Promise(r => setTimeout(r, 16));
    populateFilters();
    refreshHeaderBadges();

    // -- Role detection: planner vs director/VP ---------------------------------
    // A planner is anyone whose display name appears as an inv_manager in the data.
    // Directors/VPs in DIRECTOR_EMAILS are never treated as planners even when
    // they also manage brands directly.
    {
      const _mgrs = new Set(ALL_RECORDS.map(r => (r.inv_manager || '').toLowerCase()).filter(Boolean));
      _USER_IS_PLANNER = Boolean(CURRENT_USER.name && _mgrs.has(CURRENT_USER.name.toLowerCase()) && !_isDirector());
      if (_USER_IS_PLANNER) {
        console.info(`[Auth] Planner identified: "${CURRENT_USER.name}"`);
      }
      // Update the user badge in the freshness strip
      const _ub = document.getElementById('current-user-badge');
      if (_ub) _ub.textContent = CURRENT_USER.name || '-';
    }

    _setBoot('Rendering review table...');
    _setDetail(`${ALL_RECORDS.length.toLocaleString()} rows | paginated 100 per page`);
    await new Promise(r => setTimeout(r, 16));
    FILTERED_RECORDS = ALL_RECORDS.slice();
    // Pre-populate search from ?search= URL param (e.g. links in email reports)
    try {
      const _urlSearch = new URLSearchParams(location.search).get('search');
      if (_urlSearch) {
        const _si = document.getElementById('search');
        if (_si) _si.value = _urlSearch;
      }
    } catch(e) {}
    renderTable();
    applyFilters();
    updateReplyCount();   // show 💬 banner if planner replies exist (directors)
    refreshForMeKeys();   // async — populates _FOR_ME_KEYS from SEND_TO on active comments, then calls updateForMeCount

    const ms = (performance.now() - t0).toFixed(0);
    console.log(`Codepage viewer bootstrap completed in ${ms}ms`);
    _hideBoot();
  } catch (e) {
    console.error('Bootstrap failed:', e);
    _setBoot('Error loading projections');
    _setDetail((e && e.message ? e.message : String(e)) + ' | check the browser console for details');
  }
}
// Warn the user before leaving the page when there are unsaved MAN PRJ edits.
// The browser shows its own generic "Leave site?" dialog -- we cannot customise
// the message text in modern browsers, but setting returnValue triggers it.
window.addEventListener('beforeunload', function(e) {
  if (DIRTY_EDITS.size > 0) {
    e.preventDefault();
    e.returnValue = '';   // required for Chrome/Edge to show the dialog
  }
});

bootstrap();
