#!/usr/bin/env python3
"""
viewer.py
---------
Interactive review UI for inventory forecasts.
Reads forecast_results.json, starts a local HTTP server (port 8765),
and opens the browser with an AI vs Manual comparison table.

Features:
  - Accept individual or all forecasts (writes AI values → manual QB columns)

Usage:
    python scripts/viewer.py [--results forecast_results.json] [--port 8765] [--log changes_log.json]

The "Accept" action writes AI forecast values to the DATE-STAMPED weekly
projection columns (e.g. [04_19_W1] ... [10_11_W26]), NOT the AI_PRJ columns.
This is the way to "approve" the AI numbers as your manual plan.
"""

import os, sys, json, base64, time, re, socket, webbrowser, threading, argparse, subprocess, tempfile
import urllib.request, urllib.error
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime, date, timedelta
from pathlib import Path

# Force UTF-8 on Windows
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# ─── CData auth (same as inventory_forecaster.py) ─────────────────────────────

CDATA_MCP_URL = "https://mcp.cloud.cdata.com/mcp"
CDATA_EMAIL   = os.environ.get("CDATA_EMAIL", "steven@skaffles.com")
CDATA_PAT     = os.environ.get("CDATA_PAT",   "VaTIPqklo14D1yMkfqKRi1punowIvp/6XEHtBSgybad2Jbyl")
MAX_RETRIES   = 3

def _auth():
    return "Basic " + base64.b64encode(f"{CDATA_EMAIL}:{CDATA_PAT}".encode()).decode()

def _mcp_call(method, params, timeout=60):
    payload = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    req = urllib.request.Request(CDATA_MCP_URL, data=payload, method="POST")
    req.add_header("Authorization", _auth())
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "application/json, text/event-stream")
    resp = urllib.request.urlopen(req, timeout=timeout)
    body = resp.read().decode("utf-8")
    for line in body.split("\n"):
        if line.startswith("data: "):
            return json.loads(line[6:])
    raise ValueError(f"No data line in MCP response: {body[:300]}")

def cdata_update(sql, key):
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            result = _mcp_call("tools/call", {"name": "queryData", "arguments": {"query": sql}})
            if result.get("error"):
                raise ValueError(result["error"])
            return True
        except Exception as e:
            if attempt == MAX_RETRIES:
                print(f"  [FAIL] {key}: {e}", flush=True)
                return False
            time.sleep(2 * attempt)
    return False


def cdata_query(sql, description="query"):
    """Run a SELECT via CData MCP and return list[dict]."""
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            result = _mcp_call("tools/call", {"name": "queryData", "arguments": {"query": sql}})
            if result.get("error"):
                raise ValueError(result["error"])
            content = result.get("result", {}).get("content", [])
            text = "".join(c.get("text", "") for c in content if c.get("type") == "text").strip()
            # CData returns JSON like {"results":[{"schema":[...],"rows":[...]}]}.
            # Some responses come back without the "results" wrapper — handle both.
            idx = text.find("{")
            if idx < 0:
                return []
            data = json.loads(text[idx:])
            result_set = data.get("results", [data])[0]
            schema = result_set.get("schema", [])
            cols = [c["columnName"] for c in schema]
            rows = result_set.get("rows", [])
            return [{cols[i]: r[i] for i in range(len(cols))} for r in rows]
        except Exception as e:
            if attempt == MAX_RETRIES:
                print(f"  [FAIL] CData {description}: {e}", flush=True)
                return []
            time.sleep(2 * attempt)
    return []


def fetch_suggested_weeks(key):
    """Pull the 26 Suggested_Projection_Wk* values for a given record key."""
    k = key.replace("'", "''")
    cols = ",".join(f"[Suggested_Projection_Wk{w}]" for w in range(1, 27))
    sql = (f"SELECT {cols} FROM [Quickbase1].[InventoryTrack].[Projections] "
           f"WHERE [Acct_MStyle_Key_] = '{k}'")
    rows = cdata_query(sql, f"suggested for {key}")
    if not rows:
        return [0] * 26
    r = rows[0]
    return [int(r.get(f"Suggested_Projection_Wk{w}") or 0) for w in range(1, 27)]


def build_copy_to_manual_sql(key, source_cols):
    """Build an UPDATE that copies source_cols (list of 26 QB column names)
    into the 26 date-stamped manual projection columns (prj_cols)."""
    k = key.replace("'", "''")
    global prj_cols
    if not prj_cols:
        prj_cols_local = _make_prj_cols()
    else:
        prj_cols_local = prj_cols
    sets = ", ".join(f"[{prj_cols_local[i]}] = [{source_cols[i]}]" for i in range(26))
    return (f"UPDATE [Quickbase1].[InventoryTrack].[Projections] "
            f"SET {sets} WHERE [Acct_MStyle_Key_] = '{k}'")

# ─── Column helpers ────────────────────────────────────────────────────────────

def _make_prj_cols(ref_date=None):
    """
    Compute 26 date-stamped projection column names.
    Week 1 = the most recent Sunday on or before ref_date (today by default).
    If today IS Sunday, Week 1 = today.
    Format: MM_DD_W{n}  e.g. 03_29_W1
    """
    d = ref_date or date.today()
    days_since_sunday = (d.weekday() + 1) % 7   # Sun→0, Mon→1, …, Sat→6
    w1 = d - timedelta(days=days_since_sunday)
    return [
        f"{(w1 + timedelta(weeks=n)).strftime('%m_%d')}_W{n + 1}"
        for n in range(26)
    ]

# ─── State (loaded at startup) ────────────────────────────────────────────────

RESULTS_PATH  = None   # set in main()
records_by_key = {}    # key → record dict
prj_cols       = []    # 26 column names
VIEW_MODE      = "validate"   # "validate" or "forecast" depending on results file loaded
validation_summary = {}       # populated when VIEW_MODE == "validate"


_ENRICH_CACHE_PATH = str(Path(__file__).parent.parent / "viewer_enrichment_cache.json")
_BRAND_CACHE_PATH  = "viewer_brand_cache.json"
SKIP_ENRICH_LIVE = True   # default: rely on disk cache; --enrich-live opts back in


def _load_brand_map(mstyles):
    """
    Return {mstyle: master_brand} for the given mstyles.

    Strategy: read disk cache (`viewer_brand_cache.json`) first; only hit
    CData for mstyles we don't already have. Brand assignments rarely
    change, so the cache is durable and most launches do zero CData work.
    """
    by_mst = {}
    cache_path = Path(_BRAND_CACHE_PATH)
    if not cache_path.is_absolute():
        cache_path = Path(__file__).parent.parent / cache_path
    if cache_path.exists():
        try:
            by_mst = json.load(open(cache_path))
            if not isinstance(by_mst, dict):
                by_mst = {}
        except Exception:
            by_mst = {}

    needed   = sorted({m for m in mstyles if m and m not in by_mst})
    if needed and not SKIP_ENRICH_LIVE:
        BATCH = 200
        for i in range(0, len(needed), BATCH):
            batch = needed[i:i + BATCH]
            in_clause = ", ".join("'" + m.replace("'", "''") + "'" for m in batch)
            sql = (
                "SELECT [Mstyle], [Master_Brand] "
                "FROM [Quickbase1].[ProductTrack].[Styles] "
                f"WHERE [Mstyle] IN ({in_clause})"
            )
            try:
                rows = cdata_query(sql, f"brand-map {i//BATCH+1}")
            except Exception:
                rows = []
            for r in rows or []:
                m = (r.get("Mstyle") or "").strip()
                b = (r.get("Master_Brand") or "").strip()
                if m:
                    by_mst[m] = b
        try:
            json.dump(by_mst, open(cache_path, "w"))
        except Exception:
            pass
    return by_mst

def _enrich_from_quickbase(recs):
    """
    Pull Description, Status_Cust, PT_Item_Status, Inventory_Manager from
    Quickbase via CData and merge onto records.

    Reliability strategy:
      1. Load the previous successful enrichment from disk cache first
         (`viewer_enrichment_cache.json`) — keeps the viewer usable even when
         CData's MCP gateway is dropping connections (IncompleteRead).
      2. Try a fresh CData pull, batched 50 keys at a time, with an early-
         abort threshold: if 5 consecutive batches fail, stop trying and use
         whatever cache + partial results we have.  Saves the user from
         waiting 60+ min on a broken backend.
      3. Persist any newly fetched rows back to the cache for next time.
    """
    if not recs:
        return
    keys = sorted({r.get("key") for r in recs if r.get("key")})

    # ── Step 1: load disk cache so a broken CData doesn't blank the viewer ──
    by_key = {}
    if os.path.exists(_ENRICH_CACHE_PATH):
        try:
            cache = json.load(open(_ENRICH_CACHE_PATH))
            by_key = {k: v for k, v in cache.items() if k in set(keys)}
            print(f"  Cache hit: {len(by_key)}/{len(keys)} keys loaded from "
                  f"{_ENRICH_CACHE_PATH}")
        except Exception as e:
            print(f"  [WARN] Could not read enrichment cache: {e}")

    # ── Step 2: try a fresh CData pull for the keys we don't already have ──
    missing = [k for k in keys if k not in by_key]
    if not missing:
        print(f"  Cache covers all {len(keys)} keys — skipping live CData enrichment.")
    if missing and SKIP_ENRICH_LIVE:
        print(f"  --no-enrich set: skipping live CData query "
              f"({len(missing)} uncached keys remain blank)")
        missing = []
    if missing:
        # Prefer the QB formula field [Inv Mgr (name)] which returns a plain
        # name string ("Stuart Morgan") rather than the User-type field
        # [Inventory_Manager] which returns email/dict.  We still pull
        # [Inventory_Manager] as a fallback for older records where the
        # formula field is null.
        opn_cols = ", ".join(f"[Opn_W{w}]" for w in range(1, 27))
        select_cols = (
            "[Acct_MStyle_Key_], [Description], [Status_Cust], "
            "[PT_Item_Status], [Inventory_Manager], "
            "[Last_Comment], [Last_Comment_Date], [Flagged], "
            f"[Store_Count], [POG_Launch_Date], [POG_End_Date], {opn_cols}"
        )
        BATCH = 150
        n_batches = (len(missing) + BATCH - 1) // BATCH
        consecutive_fails = 0
        for i in range(0, len(missing), BATCH):
            batch = missing[i:i + BATCH]
            in_clause = ", ".join("'" + k.replace("'", "''") + "'" for k in batch)
            sql = (
                f"SELECT {select_cols} "
                "FROM [Quickbase1].[InventoryTrack].[Projections] "
                f"WHERE [Acct_MStyle_Key_] IN ({in_clause})"
            )
            label = f"viewer enrich {i//BATCH+1}/{n_batches}"
            try:
                rows = cdata_query(sql, label)
            except Exception as e:
                print(f"  [WARN] {label} failed: {e}")
                rows = []
            if not rows:
                consecutive_fails += 1
                if consecutive_fails >= 5:
                    print(f"  [WARN] Aborting enrichment after 5 consecutive "
                          f"failed batches — CData backend appears unreachable. "
                          f"Using cache only ({len(by_key)} keys).")
                    break
                continue
            consecutive_fails = 0
            for r in rows:
                k = r.get("Acct_MStyle_Key_")
                if k:
                    by_key[k] = r

        # ── Step 3: persist whatever we have to disk for next run ──
        if by_key:
            try:
                json.dump(by_key, open(_ENRICH_CACHE_PATH, "w"))
            except Exception as e:
                print(f"  [WARN] Could not write enrichment cache: {e}")

    if not by_key:
        print(f"  [WARN] Enrichment has 0 rows for {len(keys)} keys "
              f"(no cache, CData unreachable) — Status_Cust / PT_Item_Status "
              f"will be blank")
    else:
        matched = sum(1 for k in keys if k in by_key)
        print(f"  Enrichment: matched {matched}/{len(keys)} keys "
              f"(cache + live)")
    # Merge metadata fields only.  History columns are no longer pulled here
    # (the heavy 52-column-per-row payload was crashing CData with
    # IncompleteRead).  ord_per_wk_l13 / shpd_per_wk_l13 already exist in
    # validation JSON; for forecast-mode JSONs they'll fall back to 0 via
    # setdefault() in _adapt_forecast_to_validation.
    for rec in recs:
        k = rec.get("key")
        src = by_key.get(k)
        if not src:
            continue
        # User-type fields from QB REST come back as {email, id, name} dicts;
        # CData returns them as plain strings. Normalize to a string both ways,
        # preferring the human-readable NAME so planners see "John Grossi"
        # instead of "JohnG@fetch4pets.com".
        def _as_str(v):
            if isinstance(v, dict):
                return (v.get("name") or v.get("email") or "").strip()
            return (v or "").strip() if isinstance(v, str) else (str(v) if v else "")
        desc = _as_str(src.get("Description"))
        if desc:
            rec["desc"] = desc
            rec["description"] = desc
        rec["asin_status"] = _as_str(src.get("Status_Cust"))
        rec["item_status"] = _as_str(src.get("PT_Item_Status"))
        # "Inv Mgr (name)" (fid 1586) is a formula — UserToName([Inventory
        # Manager]) — that returns the customer-level mgr's plain name (e.g.
        # "John Grossi" for all Amazon records).  Prefer it because it's
        # already a clean string; fall back to "Inventory_Manager" (fid 936,
        # User-type returning a dict / email) only if the formula is empty.
        _mgr_name = _as_str(src.get("Inventory_Manager"))
        if _mgr_name:
            rec["inv_manager"] = _mgr_name
        # Per-row Quickbase comment summary (from Projection Comments child table)
        lc = src.get("Last_Comment") or ""
        if isinstance(lc, list):
            lc = "\n".join(str(s).strip() for s in lc if str(s).strip())
        rec["last_comment"] = str(lc).strip()
        lcd = src.get("Last_Comment_Date") or ""
        rec["last_comment_date"] = lcd.strip() if isinstance(lcd, str) else lcd
        # Flagged: shared QB-backed boolean (fid 1592).  CData returns it as
        # bool, str "true"/"false", or 0/1 depending on driver mood — coerce.
        _fl = src.get("Flagged")
        rec["flagged_qb"] = _fl is True or _fl == 1 or (isinstance(_fl, str) and _fl.lower() in ("true","1","yes"))
        # POG / ISO context — added 2026-05-10.  Strings (dates as ISO),
        # numbers (Store Count).  Used by the Inventory Plan block in the
        # detail panel; never used by forecaster logic.
        def _as_int(v, default=0):
            try: return int(v) if v is not None and v != "" else default
            except (ValueError, TypeError): return default
        def _as_iso_date(v):
            if not v: return ""
            if isinstance(v, str): return v.strip()
            if hasattr(v, "isoformat"): return v.isoformat()[:10]
            return str(v)
        rec["store_count"] = _as_int(src.get("Store_Count"))
        rec["pog_launch"]  = _as_iso_date(src.get("POG_Launch_Date"))
        rec["pog_end"]     = _as_iso_date(src.get("POG_End_Date"))
        rec["opn_w"]       = [_as_int(src.get(f"Opn_W{w}")) for w in range(1, 27)]


# Inventory Flow numeric W1..W26 column fids (stable IDs — see
# inspect_pog_invflow.py).  Three parallel series:
#   BEG: beginning-of-week warehouse inventory  (Wk1..Wk26)
#   RCV: expected supplier receipts that week   (RcvWk1..RcvWk26)
#   PRJ: projected demand draw that week        (Prj Wk1..Wk26)
# WOS OH = BEG / PRJ, computed client-side.
_INV_FLOW_BEG_LABELS = [f"Wk{i}" for i in range(1, 27)]
_INV_FLOW_RCV_LABELS = [f"RcvWk{i}" for i in range(1, 27)]
_INV_FLOW_PRJ_LABELS = [f"Prj Wk{i}" for i in range(1, 27)]
# Gap-analysis scalars (one value per mstyle, not per-week)
_INV_FLOW_GAP_LABELS = ["Opt WOS", "OPT WOS Final", "Next Avl Rcpt Dt"]


def _enrich_inv_flow(recs):
    """Pull three parallel 26-week series from QB Inventory Flow (per-mstyle)
    and attach to each record as inv_flow_beg / inv_flow_rcv / inv_flow_prj.
    All three columns are numeric — no rich-text parsing needed.
    Failures are non-fatal.
    """
    if not recs:
        return
    mstyles = sorted({(r.get("mstyle") or "").strip()
                      for r in recs if r.get("mstyle")})
    mstyles = [m for m in mstyles if m]
    if not mstyles:
        return

    # Build the SELECT — Mstyle + 78 weekly columns (26 × 3 series).  CData
    # accepts QB labels with spaces in brackets.
    all_cols = ["[Mstyle]"] \
             + [f"[{c}]" for c in _INV_FLOW_BEG_LABELS] \
             + [f"[{c}]" for c in _INV_FLOW_RCV_LABELS] \
             + [f"[{c}]" for c in _INV_FLOW_PRJ_LABELS] \
             + [f"[{c}]" for c in _INV_FLOW_GAP_LABELS]
    select_clause = ", ".join(all_cols)

    def _to_num(v):
        if v is None or v == "":
            return 0
        try:
            return int(float(v))
        except (ValueError, TypeError):
            try:
                return int(float(str(v).replace(",", "").strip()))
            except (ValueError, TypeError):
                return 0

    # Batched SELECT — 175 mstyles per IN clause (avoids QB's internal payload
    # limit on /records/query while keeping round trips reasonable).
    BATCH = 175
    by_mstyle = {}
    n_batches = (len(mstyles) + BATCH - 1) // BATCH
    for i in range(0, len(mstyles), BATCH):
        slice_ = mstyles[i:i + BATCH]
        in_clause = ", ".join("'" + m.replace("'", "''") + "'" for m in slice_)
        sql = (f"SELECT {select_clause} "
               f"FROM [Quickbase1].[InventoryTrack].[Inventory Flow] "
               f"WHERE [Mstyle] IN ({in_clause})")
        label = f"inv-flow {i//BATCH+1}/{n_batches}"
        try:
            rows = cdata_query(sql, label)
        except Exception as e:
            print(f"  [InvFlow] {label} failed: {e}")
            continue
        for r in rows or []:
            ms = (r.get("Mstyle") or "").strip()
            if not ms:
                continue
            # CData may sanitize labels (e.g. "Prj Wk1" → "Prj_Wk1") — try both
            def _get(col):
                if col in r: return r[col]
                alt = col.replace(" ", "_")
                if alt in r: return r[alt]
                return None
            # Opt WOS: prefer "Final" (with overrides), fall back to base.
            opt_final = _to_num(_get("OPT WOS Final")) or 0.0
            opt_base  = _to_num(_get("Opt WOS")) or 0.0
            opt_wos = float(opt_final) if opt_final > 0 else float(opt_base)
            next_rcpt = _get("Next Avl Rcpt Dt") or ""
            if next_rcpt and hasattr(next_rcpt, "isoformat"):
                next_rcpt = next_rcpt.isoformat()[:10]
            else:
                next_rcpt = str(next_rcpt).strip()[:10] if next_rcpt else ""
            by_mstyle[ms] = {
                "beg":       [_to_num(_get(c)) for c in _INV_FLOW_BEG_LABELS],
                "rcv":       [_to_num(_get(c)) for c in _INV_FLOW_RCV_LABELS],
                "prj":       [_to_num(_get(c)) for c in _INV_FLOW_PRJ_LABELS],
                "opt_wos":   opt_wos,
                "next_rcpt": next_rcpt,
            }

    n_attached = 0
    for rec in recs:
        ms = (rec.get("mstyle") or "").strip()
        if ms and ms in by_mstyle:
            d = by_mstyle[ms]
            rec["inv_flow_beg"]       = d["beg"]
            rec["inv_flow_rcv"]       = d["rcv"]
            rec["inv_flow_prj"]       = d["prj"]
            rec["inv_flow_opt_wos"]   = d["opt_wos"]
            rec["inv_flow_next_rcpt"] = d["next_rcpt"]
            n_attached += 1
    print(f"  [InvFlow] Loaded 3-series balances + gap scalars for {len(by_mstyle)} mstyles → {n_attached}/{len(recs)} records")


def _adapt_forecast_to_validation(rec):
    """
    Transform a forecast record into a validation-shaped record so the viewer's
    validation renderer (the only renderer now) can display it correctly.

    Forecast schema: {forecast[26], manual[26], model, pct_diff, prior_total,
                      new_total, alert, mp, biweekly, asin_status, item_status,
                      ord_per_wk_l13, ...}
    Validation schema: {ai_forecast, weeks[{week,projection,severity}],
                        priority, max_severity, ai_total, ai_per_wk, ...}

    Priority logic (2026-05-21 revised):
      - On-Plan: both zero (nothing to review) OR (Man > 0 AND AI vs Man gap <= 7.5%)
      - CRITICAL: baseline >= 1,000/wk AND gap > 7.5%
      - HIGH:     baseline 500-999/wk  AND gap > 7.5%
      - MID:      baseline 200-499/wk  AND gap > 7.5%
      - LOW:      baseline < 200/wk    AND gap > 7.5%
    """
    forecast = list(rec.get("forecast") or [])
    manual   = list(rec.get("manual")   or [])
    while len(forecast) < 26: forecast.append(0)
    while len(manual)   < 26: manual.append(0)
    ai_total     = sum(forecast)
    manual_total = sum(manual)
    ai_per_wk   = ai_total / 26.0
    proj_per_wk = manual_total / 26.0

    # Volume tier driven by AI weekly avg (forward-looking)
    if ai_per_wk >= 1000:
        vol_tier = "HIGH"
    elif ai_per_wk >= 500:
        vol_tier = "HIGH"   # 500-999 collapses into HIGH for vol badges
    elif ai_per_wk >= 200:
        vol_tier = "MEDIUM"
    else:
        vol_tier = "LOW"

    # AI vs Man gap (used for On-Plan and tier thresholds)
    ai_vs_man_pct = (abs(ai_total - manual_total) / manual_total * 100
                     if manual_total > 0 else 999.0)

    # Priority: On-Plan wins when AI and Man are aligned.
    # Two cases: (1) both zero -- nothing to review; (2) plan entered and gap <= 7.5%.
    # Otherwise tier by AI weekly rate.
    _both_zero = manual_total == 0 and ai_total == 0
    if _both_zero or (manual_total > 0 and ai_vs_man_pct <= 7.5):
        priority = "On-Plan"
    elif ai_per_wk >= 1000:
        priority = "CRITICAL"
    elif ai_per_wk >= 500:
        priority = "HIGH"
    elif ai_per_wk >= 200:
        priority = "MID"
    else:
        priority = "LOW"

    # Per-week severity: flag weeks where AI vs manual diverges by >3× or
    # where one side is 0 and the other is not.
    weeks = []
    any_alert = False
    for i in range(26):
        m = float(manual[i] or 0)
        a = float(forecast[i] or 0)
        sev = "OK"
        if (m == 0 and a > 0) or (a == 0 and m > 0):
            sev = "ALERT" ; any_alert = True
        elif m > 0 and (a / m > 3 or m / max(a, 1) > 3):
            sev = "ALERT" ; any_alert = True
        weeks.append({"week": i + 1, "projection": m, "severity": sev})

    # L13-based diffs (ord_per_wk_l13 is populated by _enrich_from_quickbase)
    ord_l13 = float(rec.get("ord_per_wk_l13") or 0)
    ai_vs_l13  = ((ai_per_wk   - ord_l13) / ord_l13 * 100) if ord_l13 > 0 else 0
    man_vs_l13 = ((proj_per_wk - ord_l13) / ord_l13 * 100) if ord_l13 > 0 else 0

    rec["ai_forecast"]       = forecast
    rec["ai_total"]          = ai_total
    rec["ai_per_wk"]         = round(ai_per_wk, 1)
    rec["projection_total"]  = manual_total
    rec["proj_per_wk"]       = round(proj_per_wk, 1)
    rec["ai_model"]          = rec.get("model", "")
    rec["weeks"]             = weeks
    rec["priority"]          = priority
    rec["vol_tier"]          = vol_tier
    rec["max_severity"]      = "ALERT" if any_alert else "OK"
    rec["n_flags"]           = sum(1 for w in weeks if w["severity"] != "OK")
    # Build narrative: original alert + deviation explanation (>10% only) +
    # POS context where available (any customer w/ POS feed) or order-history
    # context as fallback.  Generalized 2026-05-08: customer-friendly label
    # replaces hardcoded "Amazon" so Walmart/Petsmart/Petco POS plug in cleanly
    # when their data sources come online.
    def _friendly_cust(c):
        if not c: return "Retailer"
        s = str(c).upper()
        for needle, label in (
            ("AMAZON","Amazon"),("WAL MART","Walmart"),("WALMART","Walmart"),
            ("PETSMART","Petsmart"),("PETCO","Petco"),("CHEWY","Chewy"),
            ("TARGET","Target"),("KROGER","Kroger"),("LOWES","Lowes"),
            ("HOME DEPOT","Home Depot"),("ROSS","Ross"),("BURLINGTON","Burlington"),
            ("CVS","CVS"),("DOLLAR GENERAL","Dollar General"),
            ("DOLLAR TREE","Dollar Tree"),("FAMILY DOLLAR","Family Dollar"),
        ):
            if needle in s: return label
        first = s.split()[0] if s.split() else "Retailer"
        return first.title()
    _cust_label = _friendly_cust(rec.get("cust") or "")
    import re as _re_narr
    narrative_parts = []
    base_alert = (rec.get("alert") or "").strip()
    hist = rec.get("history_l26_ord") or []
    hist_total = sum(float(v or 0) for v in hist) if hist else 0

    # ── Bullet: Forecast vs Plan ─────────────────────────────────────────────
    ai_wk  = round(ai_total      / 26.0)
    man_wk = round(manual_total  / 26.0) if manual_total else 0
    if ai_total > 0 or manual_total > 0:
        if manual_total > 0:
            gap_pct = (ai_total - manual_total) / manual_total * 100.0
            if gap_pct < -1:
                gap_str = f"plan is <b>{abs(gap_pct):.0f}% above AI</b>"
            elif gap_pct > 1:
                gap_str = f"plan is <b>{abs(gap_pct):.0f}% below AI</b>"
            else:
                gap_str = "plan matches AI"
        else:
            gap_str = "no manual plan entered"
        narrative_parts.append(
            f"AI {ai_wk:,.0f}/wk ({ai_total:,.0f} total 26W) | "
            f"Plan {man_wk:,.0f}/wk ({manual_total:,.0f} total) — {gap_str}."
        )

    # ── Bullet: EC supersession ──────────────────────────────────────────────
    if rec.get("_ec_superseded"):
        narrative_parts.append(
            f"⚠ EC variant ({rec.get('mstyle','')}EC) exists for this account — "
            f"this parent SKU is being phased out. AI forecast zeroed; verify in "
            f"Quickbase before accepting."
        )

    # ── Bullet: Zero-history guard ───────────────────────────────────────────
    if hist_total == 0 and ai_total > 0:
        model_lbl = rec.get("model", "model")
        narrative_parts.append(
            f"⚠ Zero L26W order history — AI projects {ai_total:,}u ({model_lbl}). "
            f"Verify item is actively shipping before accepting."
        )

    # ── Bullets: Risk / action sentences from base alert ────────────────────
    # First sentence = vol/gap summary (covered by the Forecast vs Plan bullet).
    # Each remaining sentence becomes its own bullet — one idea per line.
    if base_alert:
        sentences = [s.strip() for s in _re_narr.split(r'(?<=[.!?])\s+', base_alert)
                     if s.strip()]
        for s in sentences[1:]:
            narrative_parts.append(s)

    # ── Bullet: Confirmed-PO context ────────────────────────────────────────
    po_zeroed = rec.get("po_zeroed_weeks") or []
    po_qty    = float(rec.get("po_total_qty") or 0)
    if po_zeroed and po_qty > 0:
        wk_str = ", ".join(f"W{w}" for w in po_zeroed[:5])
        if len(po_zeroed) > 5:
            wk_str += f" +{len(po_zeroed)-5} more"
        true_demand = ai_total + po_qty
        vs_str = (f" ({((true_demand/manual_total-1)*100):+.0f}% vs plan)"
                  if manual_total > 0 else "")
        narrative_parts.append(
            f"{wk_str} zeroed — confirmed POs cover {int(po_qty):,}u. "
            f"True demand = AI {ai_total:,} + POs {int(po_qty):,} = "
            f"<b>{int(true_demand):,}u</b>{vs_str}."
        )

    # ── Bullets: POS data ────────────────────────────────────────────────────
    pos = rec.get("_pos") or {}
    l4 = l13 = l26 = l52 = l13_for_trend = 0.0
    ord_lw = ord_pw = 0.0
    if pos:
        ord_lw = float(pos.get("ordered_lw")       or 0)
        ord_pw = float(pos.get("ordered_prior_wk") or 0)
        l4  = float(pos.get("l4w")  or 0)
        l13 = float(pos.get("l13w") or 0)
        l26 = float(pos.get("l26w") or 0)
        l52 = float(pos.get("l52w") or 0)
        if l4 > 0 or l13 > 0 or l26 > 0 or l52 > 0:
            l13_anomaly = (l13 == 0 and l4 > 0 and l26 > 0)
            l13_str = "(no data)" if l13_anomaly else f"{l13:.0f}/wk"
            l13_for_trend = ((l4 + l26) / 2.0) if l13_anomaly else l13
            trend = ""
            if l13_for_trend > 0:
                if l4 >= l13_for_trend * 1.15:
                    trend = " — accelerating"
                elif l4 <= l13_for_trend * 0.85:
                    trend = " — decelerating"
                else:
                    trend = " — stable"
            narrative_parts.append(
                f"<b>Amazon POS Sales:</b> "
                f"L4W {l4:.0f} | L13W {l13_str} | "
                f"L26W {l26:.0f} | L52W {l52:.0f}/wk{trend}."
            )
        if ord_lw > 0 or ord_pw > 0:
            if ord_pw > 0:
                wow_pct = ((ord_lw - ord_pw) / ord_pw) * 100.0
                wow_str = f"{wow_pct:+.0f}% WoW"
            elif ord_lw > 0:
                wow_str = "n/a (prior wk = 0)"
            else:
                wow_str = "n/a"
            narrative_parts.append(
                f"<b>Recent orders:</b> LW {ord_lw:,.0f}u | Prior Wk {ord_pw:,.0f}u ({wow_str})."
            )

        # Smart POS Sales-trend (mirrors _smart_pos_trend in inventory_forecaster.py)
        if l13_for_trend > 0 and l4 > 0:
            short_pct = (l4 / l13_for_trend - 1.0) * 100.0
            if abs(short_pct) >= 10:
                medium_pct = (l13 / l26 - 1.0) * 100 if (l26 > 0 and l13 > 0) else None
                yoy_pct    = (l26 / l52 - 1.0) * 100 if (l52 > 0 and l26 > 0) else None
                direction = "up" if short_pct > 0 else "down"
                arrow = ('<span style="color:#2e7d32;font-weight:700">&#x25B2;</span>'
                         if short_pct > 0 else
                         '<span style="color:#c62828;font-weight:700">&#x25BC;</span>')
                yoy_str = f"; YoY {yoy_pct:+.0f}%" if yoy_pct is not None else ""
                header = (f"<b>Sales trend:</b> {arrow} {direction} "
                          f"{abs(short_pct):.0f}% L4W vs L13W{yoy_str}.")
                cl = _cust_label or "this account"
                expl = None
                if (yoy_pct is not None and medium_pct is not None and
                    ((short_pct > 0 and medium_pct >= 5 and yoy_pct >= 10) or
                     (short_pct < 0 and medium_pct <= -5 and yoy_pct <= -10))):
                    verb = "growth" if short_pct > 0 else "softening"
                    expl = (f"Consumer demand is moving consistently across windows "
                            f"(L13W {medium_pct:+.0f}% vs L26W, YoY {yoy_pct:+.0f}%) "
                            f"— this is real {verb} at {cl}, not a 1-off blip. "
                            f"Expect ordering pace to track POS unless something changes upstream.")
                elif short_pct < 0 and medium_pct is not None and medium_pct >= 10:
                    expl = (f"POS was running hot through L13W (+{medium_pct:.0f}% vs L26W) "
                            f"and has cooled in the last 4 weeks. Looks like settling off "
                            f"a peak — possibly post-promo normalization or a finished "
                            f"consumer event — rather than broad weakness.")
                elif short_pct > 0 and medium_pct is not None and abs(medium_pct) < 5:
                    expl = (f"L13W ({l13:.0f}/wk) still matches L26W ({l26:.0f}/wk), so "
                            f"the recent uptick is fresh in the last 4 weeks only. "
                            f"Could be a feature placement / end-cap, an ad campaign, "
                            f"or seasonal pickup — give it 2-3 more weeks before "
                            f"treating as the new run rate.")
                elif short_pct < 0 and medium_pct is not None and abs(medium_pct) < 5:
                    expl = (f"L13W ({l13:.0f}/wk) still matches L26W ({l26:.0f}/wk), so "
                            f"the recent dip is concentrated in the last 4 weeks only. "
                            f"Could be retail-side inventory drawdown, weather, or a "
                            f"competing promo — watch L4 to see if it persists or bounces.")
                elif short_pct > 0 and yoy_pct is not None and yoy_pct <= -10:
                    expl = (f"L26W ({l26:.0f}/wk) is still {yoy_pct:+.0f}% vs L52W "
                            f"({l52:.0f}/wk), so the recent uptick is recovering from "
                            f"a softer year rather than net new growth.")
                elif ord_lw > 0 or ord_pw > 0:
                    if ord_pw > 0 and ord_lw > 0:
                        wow = (ord_lw / ord_pw - 1.0) * 100
                        expl = (f"Recent ordering — LW {ord_lw:,.0f}u, Prior Wk "
                                f"{ord_pw:,.0f}u ({wow:+.0f}% WoW). Short-term swing "
                                f"is in the ordering pattern, not consumer demand — "
                                f"watch the L4 POS rate over the next month.")
                    elif ord_lw == 0 and ord_pw > 0:
                        expl = (f"LW=0 after Prior Wk {ord_pw:,.0f}u — {cl} paused "
                                f"ordering even though POS is still moving "
                                f"({l4:.0f}/wk L4W). Likely working through on-hand "
                                f"inventory; expect a replen soon if POS holds.")
                    else:
                        expl = (f"L4W consumer rate {l4:.0f}/wk vs L13W {l13:.0f}/wk. "
                                f"Watch how L13/L26 trend over the next 2-3 weeks.")
                else:
                    if l52 > 0:
                        anchor_pct = (l4 / l52 - 1.0) * 100
                        expl = (f"Annual baseline at {cl} is {l52:.0f}/wk; current L4W "
                                f"is {anchor_pct:+.0f}% vs that baseline. Hard to call "
                                f"direction without medium-term context.")
                    else:
                        expl = (f"L4W {l4:.0f}/wk vs L13W {l13:.0f}/wk — limited "
                                f"history to read multi-window context.")
                narrative_parts.append(f"{header} {expl}")

    # ── Bullets: Amazon DC inventory + AUR (pinned after POS on Amazon records) ─
    amz = rec.get("_amz") or {}
    if amz:
        # DC inventory
        _ih_soh = float(amz.get("inv_soh") or 0)
        _ih_opo = float(amz.get("inv_opo") or 0)
        _ih_wos = float(amz.get("inv_wos") or 0)
        if _ih_soh > 0 or _ih_opo > 0 or _ih_wos > 0:
            _ih_parts = []
            if _ih_soh > 0:
                _ih_parts.append(f"SOH {int(_ih_soh):,}u")
            if _ih_opo > 0:
                _ih_parts.append(f"Open PO {int(_ih_opo):,}u")
            if _ih_wos > 0:
                if _ih_wos < 3:
                    _wos_str = (f"<span style='color:#c62828'><b>WOS "
                                f"{_ih_wos:.1f}wks</b></span>")
                elif _ih_wos < 8:
                    _wos_str = (f"<span style='color:#e65100'>WOS "
                                f"{_ih_wos:.1f}wks</span>")
                elif _ih_wos >= 16:
                    _wos_str = (f"<span style='color:#f57f17'>WOS "
                                f"{_ih_wos:.1f}wks (overstocked)</span>")
                else:
                    _wos_str = f"WOS {_ih_wos:.1f}wks"
                _ih_parts.append(_wos_str)
            if _ih_parts:
                narrative_parts.append(
                    "<b>Amazon DC inventory:</b> " + " | ".join(_ih_parts) + "."
                )
        # AUR
        _aur_l4w  = float(amz.get("aur_l4w")  or 0)
        _aur_l13w = float(amz.get("aur_l13w") or 0)
        _aur_l26w = float(amz.get("aur_l26w") or 0)
        _aur_l52w = float(amz.get("aur_l52w") or 0)
        if _aur_l4w > 0 or _aur_l13w > 0 or _aur_l26w > 0 or _aur_l52w > 0:
            _aur_items = []
            if _aur_l4w  > 0: _aur_items.append(f"L4W ${_aur_l4w:.2f}")
            if _aur_l13w > 0: _aur_items.append(f"L13W ${_aur_l13w:.2f}")
            if _aur_l26w > 0: _aur_items.append(f"L26W ${_aur_l26w:.2f}")
            if _aur_l52w > 0: _aur_items.append(f"L52W ${_aur_l52w:.2f}")
            narrative_parts.append(
                "<b>Amazon AUR:</b> " + " | ".join(_aur_items) + "."
            )

    # Order context for non-POS records — LW/PW WoW merged into the trend header
    # so the planner gets one unified ordering bullet instead of two.
    if not pos and len(hist) >= 2:
        h_v = [float(v or 0) for v in hist]
        lw = h_v[-1]; pw = h_v[-2] if len(h_v) >= 2 else 0
        # Build WoW string once; reused in header and fallback bullet
        if lw > 0 or pw > 0:
            if pw > 0:
                _wow_pct = ((lw - pw) / pw) * 100.0
                _wow_str = f"{_wow_pct:+.0f}% WoW"
            elif lw > 0:
                _wow_str = "n/a (prior wk = 0)"
            else:
                _wow_str = "n/a"
            recent_part = f" LW {lw:.0f}u, Prior Wk {pw:.0f}u (&#x0394; {_wow_str})."
        else:
            recent_part = ""
        trend_fired = False
        if len(hist) >= 4:
            l4_v  = h_v[-4:]
            l13_v = h_v[-13:] if len(h_v) >= 13 else h_v
            l26_v = h_v[-26:] if len(h_v) >= 26 else h_v
            l4_avg_v  = sum(l4_v)  / 4.0
            l13_avg_v = sum(l13_v) / 13.0
            l26_avg_v = sum(l26_v) / max(len(l26_v), 1)
            if l13_avg_v > 0:
                short_pct = (l4_avg_v / l13_avg_v - 1.0) * 100
                # Initialise before the >=10% gate so elif chain can safely reference them
                per_l13 = per_l4 = freq_l13 = freq_l4 = 0.0
                medium_flat = False
                cl = _cust_label or "this account"
                l4_nz_v: list = []
                l13_nz_v: list = []
                l52_avg_v = None
                header = None  # only set inside abs(short_pct) >= 10 block
                if abs(short_pct) >= 10:
                    l13_nz_v = [v for v in l13_v if v > 0]
                    l4_nz_v  = [v for v in l4_v  if v > 0]
                    per_l13 = (sum(l13_nz_v) / len(l13_nz_v)) if l13_nz_v else 0.0
                    per_l4  = (sum(l4_nz_v)  / len(l4_nz_v))  if l4_nz_v  else 0.0
                    freq_l13 = len(l13_nz_v) / 13.0
                    freq_l4  = len(l4_nz_v)  / 4.0
                    medium_flat = (abs(l26_avg_v - l13_avg_v) / max(l13_avg_v, 1)) < 0.15
                    ly_v = [float(v or 0) for v in (rec.get("history_l26_ord_ly") or rec.get("history_ly_ord") or [])]
                    l52_avg_v = None
                    if ly_v and len(ly_v) >= 13:
                        full52 = ly_v + l26_v
                        if len(full52) >= 40:
                            l52_avg_v = sum(full52) / len(full52)
                    direction = "up" if short_pct > 0 else "down"
                    arrow = ('<span style="color:#2e7d32;font-weight:700">&#x25B2;</span>'
                             if short_pct > 0 else
                             '<span style="color:#c62828;font-weight:700">&#x25BC;</span>')
                    # LW/PW WoW appended directly to trend header — one bullet, not two
                    header = (f"<b>Order trend:</b> {arrow} {direction} "
                              f"{abs(short_pct):.0f}% L4W ({l4_avg_v:.0f}/wk) "
                              f"vs L13W ({l13_avg_v:.0f}/wk).{recent_part}")
                    cl = _cust_label or "this account"
                expl = None
                if (short_pct < 0 and lw == 0 and pw > 0 and per_l13 > 0 and
                        pw <= per_l13 * 1.6 and medium_flat and len(l4_nz_v) >= 1):
                    expl = (f"Looks like a gap week, not a step-change — LW was 0 right "
                            f"after a normal {pw:.0f}u order, and the L26W rate "
                            f"({l26_avg_v:.0f}/wk) still tracks L13W. {cl} orders in "
                            f"bursts here, so a single quiet week is normal cadence. "
                            f"Watch the next 2-3 weeks; if no order lands, that's the "
                            f"real signal.")
                elif (short_pct < 0 and per_l13 > 0 and per_l4 > 0 and
                      per_l4 / per_l13 <= 0.80 and
                      abs(freq_l4 - freq_l13) / max(freq_l13, 0.01) < 0.30):
                    expl = (f"Per-order qty dropped from ~{per_l13:.0f}u (L13W) to "
                            f"~{per_l4:.0f}u (L4W) while reorder cadence held steady. "
                            f"Smaller POs at the same frequency usually means {cl} "
                            f"trimmed distribution (lost a few stores), shifted to "
                            f"tighter JIT, or downsized the per-store build — worth a "
                            f"quick sales-rep check.")
                elif (short_pct < 0 and per_l13 > 0 and per_l4 > 0 and
                      0.85 <= per_l4 / per_l13 <= 1.20 and
                      freq_l4 < freq_l13 * 0.70):
                    expl = (f"Fewer orders at the same per-PO size (~{per_l4:.0f}u). "
                            f"L4 had {len(l4_nz_v)} order(s) vs the typical "
                            f"{len(l13_nz_v)}/13W cadence. Slower reorders with stable "
                            f"order qty usually means slower turn at retail — POS "
                            f"softening, or {cl} sitting on inventory longer than usual.")
                elif (short_pct < 0 and l52_avg_v and l52_avg_v > 0 and
                      l26_avg_v < l52_avg_v * 0.85):
                    yoy_pct = (l26_avg_v / l52_avg_v - 1.0) * 100
                    expl = (f"L26W ({l26_avg_v:.0f}/wk) is {yoy_pct:+.0f}% vs L52W "
                            f"({l52_avg_v:.0f}/wk) — this isn't a 4-week dip, it's been "
                            f"cooling across multiple quarters at {cl}. Pattern usually "
                            f"means real demand softening (category contraction, "
                            f"distribution loss) rather than seasonal.")
                elif (short_pct > 0 and l52_avg_v and l52_avg_v > 0 and
                      l26_avg_v > l52_avg_v * 1.10):
                    yoy_pct = (l26_avg_v / l52_avg_v - 1.0) * 100
                    expl = (f"L26W ({l26_avg_v:.0f}/wk) is +{yoy_pct:.0f}% vs L52W "
                            f"({l52_avg_v:.0f}/wk) — momentum has been building at {cl} "
                            f"across multiple quarters, not a 1-off bump. Plan for the "
                            f"pace to hold or build into Q4 unless POS turns.")
                elif (short_pct > 0 and per_l13 > 0 and per_l4 > 0 and
                      per_l4 / per_l13 >= 1.20 and
                      abs(freq_l4 - freq_l13) / max(freq_l13, 0.01) < 0.30):
                    expl = (f"Per-order qty grew from ~{per_l13:.0f}u to ~{per_l4:.0f}u "
                            f"while reorder cadence held steady. Bigger POs at the same "
                            f"rate usually means {cl} consolidated touchpoints (multi-"
                            f"store builds, fewer ad-hoc replens) or picked up "
                            f"distribution gains.")
                elif short_pct > 0 and lw > 0 and pw == 0 and freq_l13 > 0:
                    expl = (f"Activity restarting at {cl} — LW {lw:.0f}u after a Prior "
                            f"Wk zero. Their typical cadence is {len(l13_nz_v)} orders/"
                            f"13W, so watch the next 2-3 weeks to see whether they're "
                            f"getting back to baseline or this was a one-off catch-up.")
                elif lw == 0 and pw == 0 and short_pct < 0:
                    expl = (f"Two consecutive zero weeks at {cl}. Their L13W cadence ran "
                            f"{len(l13_nz_v)}/13W active, so two zeros in a row is "
                            f"unusual. Could be a stockout on their end, an EDI hiccup, "
                            f"or a real pause in ordering — worth a quick check before "
                            f"assuming the account has gone quiet.")
                else:
                    if short_pct > 0:
                        expl = (f"L26W ({l26_avg_v:.0f}/wk) still tracks L13W "
                                f"({l13_avg_v:.0f}/wk), so the recent uptick at {cl} is "
                                f"fresh in the last 4 weeks. Could be a single larger "
                                f"PO, a feature/end-cap, or a retail promo — watch the "
                                f"next 2-3 weeks to see whether it sticks.")
                    else:
                        if medium_flat:
                            expl = (f"L26W ({l26_avg_v:.0f}/wk) ≈ L13W ({l13_avg_v:.0f}/wk), "
                                    f"so {cl}'s medium-term run rate is flat and the "
                                    f"recent dip looks like normal cadence variance "
                                    f"over a short window. No action unless it persists.")
                        else:
                            expl = (f"L26W ({l26_avg_v:.0f}/wk) and L13W ({l13_avg_v:.0f}/wk) "
                                    f"are both off baseline — this is a broader cooling "
                                    f"pattern at {cl}, not just last 4 weeks. Worth "
                                    f"checking POS or distribution for what changed.")
                    if header is not None:
                        narrative_parts.append(f"{header} {expl}" if expl else header)
                        trend_fired = True
        if not trend_fired and recent_part:
            narrative_parts.append(f"<b>Recent ordering:</b>{recent_part}")

    rec["narrative"] = "\n".join(narrative_parts) if narrative_parts else ""
    rec.setdefault("desc",         rec.get("description", ""))
    rec.setdefault("asin_status",  "")
    rec.setdefault("item_status",  "")
    rec.setdefault("inv_manager",  "")
    rec["pattern"]           = rec.get("model", "")
    rec.setdefault("biweekly",     rec.get("biweekly", False))
    rec.setdefault("ord_per_wk_l13", 0)
    rec["ai_vs_l13_pct"]     = round(ai_vs_l13, 1)
    rec["man_vs_l13_pct"]    = round(man_vs_l13, 1)
    rec.setdefault("shpd_per_wk_l13", 0)
    rec.setdefault("history_l26_shp", [])
    rec.setdefault("history_l26_ord", [])
    rec.setdefault("suggested",       [])
    return rec


def _scope_to_cli_flags(scope_desc):
    """Parse meta.scope (e.g. 'customer=AMAZON | acct=1864') into CLI flags."""
    flags = []
    if not scope_desc or scope_desc.strip().lower() == "all active":
        return ["--all"]
    for part in str(scope_desc).split("|"):
        part = part.strip()
        if not part:
            continue
        if part.lower() == "all active":
            return ["--all"]
        if "=" not in part:
            continue
        key, val = part.split("=", 1)
        key = key.strip().lower()
        val = val.strip()
        if key in ("acct", "customer", "mstyle", "brand"):
            flags.extend([f"--{key}", val])
    return flags or ["--all"]


def _refresh_validation(path):
    """Re-run the validator against current Quickbase before loading the viewer.

    Reads the existing validation_results.json's meta.scope to know what to
    re-validate, runs `inventory_forecaster.py --validate <scope>`, which
    overwrites the same file with fresh order history and recomputed flags.
    """
    if not Path(path).exists():
        return
    try:
        with open(path) as f:
            existing = json.load(f)
    except Exception as e:
        print(f"  Could not read existing validation file ({e}); will run validator with --all")
        existing = {}
    meta  = existing.get("meta", {}) if isinstance(existing, dict) else {}
    scope = meta.get("scope", "all active")
    flags = _scope_to_cli_flags(scope)

    cmd = [sys.executable, "-u",
           str(Path(__file__).resolve().parent / "inventory_forecaster.py"),
           "--validate",
           "--out", str(path),
           *flags]
    print(f"  Refreshing validation against current Quickbase (scope: {scope})...")
    print(f"    {' '.join(cmd[1:])}")
    try:
        r = subprocess.run(cmd, cwd=str(Path(__file__).resolve().parent.parent),
                            timeout=3600)
        if r.returncode != 0:
            print(f"  Refresh failed (exit {r.returncode}); falling back to existing snapshot.")
        else:
            print(f"  Refresh complete.")
    except Exception as e:
        print(f"  Refresh error ({e}); falling back to existing snapshot.")


_HTML_TAG_RE = re.compile(r"<[^>]+>")
_HTML_ENTS   = {"&nbsp;": " ", "&amp;": "&", "&lt;": "<", "&gt;": ">",
                "&quot;": '"', "&#39;": "'"}
def _strip_html(s):
    """Strip HTML tags + decode common entities. Customr_Name (QB fid 376) is a
    rich-text formula field that returns markup like
    `<div style='color:#33A7FF' align='Left'><font size=-1><b>WAL MART</b></font></div>`.
    Used at JSON-load time so old results files display cleanly."""
    if not s or not isinstance(s, str): return s
    if "<" not in s and "&" not in s: return s   # fast path
    out = _HTML_TAG_RE.sub(" ", s)
    for ent, ch in _HTML_ENTS.items():
        out = out.replace(ent, ch)
    return " ".join(out.split()).strip()

def load_results(path):
    """Load results JSON -- auto-detects forecast vs validation format."""
    global records_by_key, prj_cols, VIEW_MODE, validation_summary
    with open(path) as f:
        data = json.load(f)
    # Schema check (warn-only; viewer is permissive about older snapshots).
    try:
        from config import check_schema_version
        check_schema_version(data, source_path=path)
    except ImportError:
        pass  # config.py not yet on path
    if isinstance(data, list):
        recs = data
        prj_cols = _make_prj_cols()
        VIEW_MODE = "forecast"
    else:
        recs = data.get("records", [])
        prj_cols = data.get("meta", {}).get("prj_cols") or _make_prj_cols()
        # Auto-detect validation format
        if "summary" in data and "critical_records" in data.get("summary", {}):
            VIEW_MODE = "validate"
            validation_summary = data.get("summary", {})
        else:
            VIEW_MODE = "forecast"
    # Strip rich-text HTML from customer name (QB formula fields return markup).
    for r in recs:
        if r.get("cust"):
            r["cust"] = _strip_html(r["cust"])
    # Adapt forecast records so the (validation-only) renderer can display them.
    if VIEW_MODE == "forecast":
        # Enrich from Quickbase first — pulls Description, Status_ASIN,
        # Status_Item, Inventory_Manager, and 13 weeks of order history.
        print("  Enriching records from Quickbase (Description, Status_ASIN, Status_Item, L13 orders)...")
        _enrich_from_quickbase(recs)
        # Pull warehouse projected inventory balances per mstyle (added 2026-05-10)
        if not SKIP_ENRICH_LIVE:
            print("  Pulling Inventory Flow (warehouse projected balances by week)...")
            try:
                _enrich_inv_flow(recs)
            except Exception as e:
                print(f"  [WARN] Inventory Flow enrichment failed (non-fatal): {e}")
        # Inject L26W order history (Ord LW, Ord LW-1...Ord LW-25) from cache.
        # Cache built by the standalone history-pull script; viewer treats it
        # read-only (rebuild via `python build_history_cache.py` if needed).
        try:
            hist_path = Path(__file__).parent.parent / "viewer_history_cache.json"
            if hist_path.exists():
                hist_cache = json.load(open(hist_path))
                n_hit = 0
                for r in recs:
                    h = hist_cache.get(r.get("key"))
                    if h and isinstance(h, list) and len(h) == 26:
                        r["history_l26_ord"] = h
                        # L13W weekly avg = avg of last 13 weeks (positions 13..25 in chronological)
                        l13 = h[-13:]
                        l13_nz = [v for v in l13 if v > 0]
                        # Use all-weeks avg (matches what L13 cards display)
                        r["ord_per_wk_l13"] = round(sum(l13) / 13.0, 1) if l13 else 0
                        # L4W weekly avg (most recent 4 weeks) — surfaces recent
                        # acceleration/deceleration vs the L13W trend.
                        l4 = h[-4:]
                        r["ord_per_wk_l4"] = round(sum(l4) / 4.0, 1) if l4 else 0
                        n_hit += 1
                print(f"  Order history: {n_hit}/{len(recs)} records loaded from L26W cache")
        except Exception as e:
            print(f"  [WARN] Could not load history cache: {e}")
        # Inject Amazon POS data (mstyle-keyed) for narrative context.
        try:
            pos_path = Path(__file__).parent.parent / "viewer_pos_cache.json"
            if pos_path.exists():
                pos_cache = json.load(open(pos_path))
                n_pos = 0
                for r in recs:
                    if "AMAZON" in (r.get("cust", "") or "").upper():
                        m = r.get("mstyle", "")
                        if m and m in pos_cache:
                            r["_pos"] = pos_cache[m]
                            n_pos += 1
                print(f"  Amazon POS: {n_pos} records enriched")
        except Exception as e:
            print(f"  [WARN] Could not load POS cache: {e}")
        # Inject Amazon catalog data (AUR + DC inv) for narrative context.
        # Written by inventory_forecaster.py after Phase 2.6b.
        try:
            amz_path = Path(__file__).parent.parent / "viewer_amz_cache.json"
            if amz_path.exists():
                amz_cache = json.load(open(amz_path))
                n_amz = 0
                for r in recs:
                    if "AMAZON" in (r.get("cust", "") or "").upper():
                        m = r.get("mstyle", "")
                        if m and m in amz_cache:
                            r["_amz"] = amz_cache[m]
                            n_amz += 1
                        elif m:
                            # EC parent / variant fallback (mirrors forecaster logic)
                            _m_up = m.upper()
                            _parent = (m[:-2] if _m_up.endswith("EC") else
                                       m[:-3] if _m_up.endswith("COS") or _m_up.endswith("AMZ") else m)
                            if _parent != m and _parent in amz_cache:
                                r["_amz"] = amz_cache[_parent]
                                n_amz += 1
                            else:
                                for _sfx in ("AMZ", "EC", "COS", "DS"):
                                    if (m + _sfx) in amz_cache:
                                        r["_amz"] = amz_cache[m + _sfx]
                                        n_amz += 1
                                        break
                print(f"  Amazon catalog (AUR + DC inv): {n_amz} records enriched")
        except Exception as e:
            print(f"  [WARN] Could not load Amazon catalog cache: {e}")
        # EC-supersession guard: when an item has been switched to an EC variant
        # ({mstyle}EC = "prepped in bags & ecomm ready"), the original non-EC
        # mstyle is being phased out.  Zero the AI forecast on the parent so we
        # don't ship two SKUs in parallel.  The phase-out flag is captured for
        # the narrative.
        try:
            mstyles_by_acct = {}
            for r in recs:
                acct = (r.get("key") or "").split("-", 1)[0]
                mstyles_by_acct.setdefault(acct, set()).add(r.get("mstyle") or "")
            n_ec_zeroed = 0
            for r in recs:
                mst = r.get("mstyle") or ""
                if not mst or mst.endswith("EC"):
                    continue
                acct = (r.get("key") or "").split("-", 1)[0]
                if (mst + "EC") in mstyles_by_acct.get(acct, set()):
                    r["_ec_superseded"] = True
                    if sum(r.get("forecast") or []) > 0:
                        r["forecast"] = [0] * 26
                        n_ec_zeroed += 1
            print(f"  EC-supersession: zeroed forecast on {n_ec_zeroed} non-EC parent items "
                  f"(EC variant exists)")
        except Exception as e:
            print(f"  [WARN] EC-supersession check failed: {e}")
        # Brand enrichment — adds Master_Brand for the brand filter dropdown.
        # Cached on disk so most launches do zero CData work.
        try:
            mstyles = {(r.get("mstyle") or "").strip() for r in recs}
            mstyles.discard("")
            brand_map = _load_brand_map(mstyles) if mstyles else {}
            for r in recs:
                m = (r.get("mstyle") or "").strip()
                if m and not r.get("brand"):
                    r["brand"] = brand_map.get(m, "") or ""
        except Exception as e:
            print(f"  [WARN] Brand enrichment skipped: {e}")
        recs = [_adapt_forecast_to_validation(r) for r in recs]
    elif VIEW_MODE == "validate":
        # Validation JSON already has ai_per_wk / proj_per_wk / ord_per_wk_l13,
        # but is missing Status_Cust / PT_Item_Status / 26w history (the
        # validator doesn't write those fields).  Enrich from QB and re-derive
        # ai_vs_l13_pct + man_vs_l13_pct so the viewer's L13 columns aren't 0.
        print("  Enriching validation records from Quickbase (Status_Cust, PT_Item_Status, L26 history)...")
        _enrich_from_quickbase(recs)
        # Brand enrichment (Master_Brand from ProductTrack.Styles, mstyle-keyed,
        # disk-cached). Required by the Brand filter dropdown — without this
        # block validate-mode records have empty `brand` and the filter is empty.
        try:
            mstyles = {(r.get("mstyle") or "").strip() for r in recs}
            mstyles.discard("")
            brand_map = _load_brand_map(mstyles) if mstyles else {}
            for r in recs:
                m = (r.get("mstyle") or "").strip()
                if m and not r.get("brand"):
                    r["brand"] = brand_map.get(m, "") or ""
        except Exception as e:
            print(f"  [WARN] Brand enrichment skipped: {e}")

        # OVERLAY: validation's `ai_forecast` is uncapped/undampened (raw
        # baseline x raw_seasonal x event_lift), so for highly seasonal items
        # it can be 3-4x what the actual forecast pipeline produces.  Always
        # use the real forecast engine's numbers so the viewer reflects exactly
        # what would get written back to Quickbase.
        forecast_overlay = {}
        forecast_path = Path(path).parent / "forecast_results.json"

        def _load_forecast_file(p):
            try:
                with open(p) as f:
                    fd = json.load(f)
                return fd.get("records", []) if isinstance(fd, dict) else fd
            except Exception as e:
                print(f"  Could not read {p}: {e}")
                return []

        def _ingest(f_recs):
            for fr in f_recs:
                k = fr.get("key")
                if not k:
                    continue
                fcst = fr.get("forecast") or fr.get("ai_forecast") or []
                forecast_overlay[k] = {
                    "ai_forecast": fcst,
                    "ai_total":    int(fr.get("new_total")  or sum(fcst) or 0),
                    "ai_per_wk":   round((sum(fcst) / 26.0) if fcst else 0, 1),
                    "ai_model":    fr.get("model") or fr.get("ai_model") or "",
                }

        if forecast_path.exists():
            _ingest(_load_forecast_file(forecast_path))
            print(f"  Loaded AI numbers from forecast_results.json ({len(forecast_overlay)} keys)")

        # Auto-fill: any validation key without a forecast number gets one
        # generated by the real forecast engine before the viewer launches.
        val_keys = {r.get("key") for r in recs if r.get("key")}
        missing = sorted(val_keys - set(forecast_overlay.keys()))
        if missing:
            print(f"  {len(missing)} validation keys are missing forecast numbers — running the forecast engine for them now...")
            import subprocess
            partial_out = Path(path).parent / "forecast_results.partial.json"
            try:
                if partial_out.exists():
                    partial_out.unlink()
                # Chunk to keep CLI args under shell limits
                CHUNK = 200
                merged_partial = []
                for i in range(0, len(missing), CHUNK):
                    chunk = missing[i:i + CHUNK]
                    cmd = [
                        sys.executable, "-u",
                        str(Path(__file__).resolve().parent / "inventory_forecaster.py"),
                        "--keys", ",".join(chunk),
                        "--dry-run",
                        "--out", str(partial_out),
                    ]
                    print(f"    forecasting chunk {i//CHUNK + 1}/{(len(missing)+CHUNK-1)//CHUNK} ({len(chunk)} keys)...")
                    r = subprocess.run(cmd, cwd=str(Path(__file__).resolve().parent.parent),
                                        capture_output=True, text=True, timeout=1800)
                    if r.returncode != 0:
                        print(f"    forecast subprocess failed (exit {r.returncode}): {r.stderr[-500:]}")
                    elif partial_out.exists():
                        merged_partial.extend(_load_forecast_file(partial_out))
                if merged_partial:
                    _ingest(merged_partial)
                    print(f"  Filled {len(merged_partial)} missing forecasts. Overlay now covers {len(forecast_overlay)} keys.")
            except Exception as e:
                print(f"  Could not auto-fill missing forecasts: {e}")

        for r in recs:
            k = r.get("key")
            if k in forecast_overlay:
                ov = forecast_overlay[k]
                r["ai_forecast"] = ov["ai_forecast"]
                r["ai_total"]    = ov["ai_total"]
                r["ai_per_wk"]   = ov["ai_per_wk"]
                if ov["ai_model"]:
                    r["ai_model"] = ov["ai_model"]
                # Regenerate narrative from the now-correct AI vs manual numbers
                fcst = ov["ai_forecast"]
                weeks = r.get("weeks") or []
                gaps  = []
                for i, wk in enumerate(weeks):
                    if i >= len(fcst):
                        break
                    plan = float(wk.get("projection") or 0)
                    ai   = float(fcst[i] or 0)
                    if ai > plan * 1.10 and (ai - plan) > 50:
                        gaps.append((i + 1, plan, ai))
                if gaps:
                    top = gaps[:3]
                    parts = ", ".join(f"W{w} ({int(p):,} plan vs. {int(a):,} AI)" for w, p, a in top)
                    extra = f" and {len(gaps)-3} more weeks" if len(gaps) > 3 else ""
                    total_gap = int(sum((a - p) for _, p, a in gaps))
                    r["narrative"] = (
                        f"AI projects higher than plan in {len(gaps)} of 26 weeks: {parts}{extra}. "
                        f"Roughly {total_gap:,} units of unplanned demand if the account holds pace."
                    )
                else:
                    r["narrative"] = "AI forecast is in line with the manual plan across all 26 weeks."

            ord_l13 = float(r.get("ord_per_wk_l13") or 0)
            ai_wk   = float(r.get("ai_per_wk")     or 0)
            man_wk  = float(r.get("proj_per_wk")   or 0)
            r["ai_vs_l13_pct"]  = round(((ai_wk  - ord_l13) / ord_l13 * 100) if ord_l13 > 0 else 0, 1)
            r["man_vs_l13_pct"] = round(((man_wk - ord_l13) / ord_l13 * 100) if ord_l13 > 0 else 0, 1)

            # Volume tier (forward-looking, driven by AI weekly avg).
            # 2026-05-08: match codepage exactly — strict AI-based, no
            # fallback to manual/L13W.  Inactive items where AI=0 stay LOW
            # (the inactive_with_stale_plan flag below already separates
            # those for the "QB cleanup" report).  Without this, the same
            # record could tier HIGH in validation mode (using manual) but
            # LOW in the codepage (using AI), confusing planners.
            if   ai_wk >= 1000: r["vol_tier"] = "HIGH"
            elif ai_wk >= 500:  r["vol_tier"] = "HIGH"
            elif ai_wk >= 200:  r["vol_tier"] = "MEDIUM"
            else:               r["vol_tier"] = "LOW"

            # F-V (2026-04-26) — Inactive-with-stale-plan flag.  When AI=0 +
            # L13W=0 + manual still has a non-zero number, this is QB cleanup
            # debt rather than a forecast deviation.  Tag separately so the
            # UI can surface it as "stale plan, clean up QB" rather than
            # mixing it into the forecast-quality counts.
            if (ai_wk == 0 and ord_l13 == 0 and man_wk > 0):
                r["inactive_with_stale_plan"] = True
            else:
                r["inactive_with_stale_plan"] = False
        # Build a summary so the header cards show real counts instead of zeros.
        # Volume-tier counts (header badges): HIGH = prj/wk>=1000, MEDIUM 200-999, LOW <200
        n_high = sum(1 for r in recs if r.get("vol_tier") == "HIGH")
        n_med_vol = sum(1 for r in recs if r.get("vol_tier") == "MEDIUM")
        n_low_vol = sum(1 for r in recs if r.get("vol_tier") == "LOW")
        n_stale_inactive = sum(1 for r in recs if r.get("inactive_with_stale_plan"))
        n_crit    = sum(1 for r in recs if r.get("priority") == "CRITICAL")
        n_high    = sum(1 for r in recs if r.get("priority") == "HIGH")
        n_mid     = sum(1 for r in recs if r.get("priority") == "MID")
        n_low     = sum(1 for r in recs if r.get("priority") == "LOW")
        n_onplan  = sum(1 for r in recs if r.get("priority") == "On-Plan")
        validation_summary = {
            "total_records":     len(recs),
            "critical_records":  n_crit,
            "priority_critical": n_crit,
            "priority_high":     n_high,
            "priority_mid":      n_mid,
            "priority_low":      n_low,
            "priority_on_plan":  n_onplan,
            "vol_high":          n_high,
            "vol_medium":        n_med_vol,
            "vol_low":           n_low_vol,
            "stale_inactive":    n_stale_inactive,
        }
    records_by_key = {r["key"]: r for r in recs}
    print(f"  Mode: {VIEW_MODE}")
    print(f"  Loaded {len(records_by_key)} records  |  columns {prj_cols[0]} → {prj_cols[-1]}")
    if VIEW_MODE == "forecast" and validation_summary.get("priority_critical") is not None:
        print(f"  Priority breakdown: CRITICAL={validation_summary.get('priority_critical', 0)} "
              f"HIGH={validation_summary.get('priority_high', 0)} "
              f"MID={validation_summary.get('priority_mid', 0)} "
              f"LOW={validation_summary.get('priority_low', 0)} "
              f"On-Plan={validation_summary.get('priority_on_plan', 0)}")



# ─── Manager email builder ─── REMOVED (email-summary feature deleted) ───────
# build_email_html, build_manager_email_html, create_outlook_draft, the
# /api/send-email-drafts handler, and the "Send to Manager" button were all
# part of an Outlook-draft-generating workflow that's been retired.
# Comments live in the QB Projection Comments table now.
# ──────────────────────────────────────────────────────────────────────────────




# ─── Validation viewer ────────────────────────────────────────────────────────

# Module-global record payload cache.  build_validation_page_html() fills this
# with the full records JSON (gzipped UTF-8 bytes); the /api/records.json
# endpoint serves it directly so the initial HTML stays small (~50KB) and the
# loading overlay is visible the moment the browser parses the page header.
_RECORDS_PAYLOAD_BYTES = b"[]"
_RECORDS_PAYLOAD_GZIP  = None  # bytes when gzip-compressible
_HTML_CACHE            = None  # cached HTML page string

def build_validation_page_html():
    """Generate the validation review page HTML.

    Cached: enrichment + JSON-dump + gzip is heavy (~1-2s for 4k records,
    ~9MB raw → 800KB gzipped).  Once built, subsequent GET / requests
    return the cached string instantly so browser reloads don't pay the
    rebuild cost.  Cache is invalidated only when the process restarts —
    which is fine because the underlying records_by_key is loaded once
    at startup.
    """
    global _RECORDS_PAYLOAD_BYTES, _RECORDS_PAYLOAD_GZIP, _HTML_CACHE
    if _HTML_CACHE is not None:
        return _HTML_CACHE
    _t_build = time.time()
    recs = list(records_by_key.values())
    summ = validation_summary

    # Build per-record JSON for the JS layer.
    # weeks (slim — week/projection/severity only, no per-week reason strings),
    # history_l26_shp, history_l26_ord, and suggested are now included inline so
    # toggleDetail needs zero network round-trips. Total page size ~5-8 MB.
    records_js = []
    for r in recs:
        weeks_slim = [
            {"week": w["week"], "projection": w["projection"], "severity": w["severity"]}
            for w in (r.get("weeks") or [])
        ]
        records_js.append({
            "key":              r["key"],
            "cust":             r.get("cust", ""),
            "mstyle":           r.get("mstyle", ""),
            "desc":             r.get("desc", ""),
            "asin_status":      r.get("asin_status", ""),
            "item_status":      r.get("item_status", ""),
            "inv_manager":      r.get("inv_manager", ""),
            "brand":            r.get("brand", ""),
            "pattern":          r.get("pattern", ""),
            "biweekly":         r.get("biweekly", False),
            "proj_wk":          r.get("proj_per_wk", 0),
            "ord_wk_l4":        r.get("ord_per_wk_l4", 0),
            "shp_wk":           r.get("ord_per_wk_l13", r.get("shp_per_wk_l13", 0)),
            "shpd_wk":          r.get("shpd_per_wk_l13", 0),
            "ai_fcst":          r.get("ai_forecast", []),
            "ai_model":         r.get("ai_model", ""),
            "ai_total":         r.get("ai_total", 0),
            "ai_wk":            r.get("ai_per_wk", 0),
            "narrative":        r.get("narrative", ""),
            "max_sev":          r.get("max_severity", "OK"),
            "priority":         r.get("priority", "LOW"),
            "vol_tier":         r.get("vol_tier", "LOW"),
            "n_flags":          r.get("n_flags", 0),
            "proj_total":       r.get("projection_total", 0),
            "pct_diff":         r.get("pct_diff", 0),
            "ai_vs_l13":        r.get("ai_vs_l13_pct", 0),
            "man_vs_l13":       r.get("man_vs_l13_pct", 0),
            # Detail data — preloaded for instant expand, no CData fetch needed
            "weeks_slim":       weeks_slim,
            "suggested":        r.get("suggested", []),
            # Avg-per-week of the 26 Suggested values, surfaced as a header
            # column so planners can compare AI Fcst/Wk vs Suggested /Wk at a glance.
            "sugg_total":       sum(r.get("suggested", [])) if r.get("suggested") else 0,
            "sugg_wk":          (sum(r.get("suggested", [])) / 26.0) if r.get("suggested") else 0,
            "hist_shp":         r.get("history_l26_shp", r.get("history_l26", [])),
            "hist_ord":         r.get("history_l26_ord", []),
            # LY actuals (weeks 27-52 ago, aligned to W1..W26 of the forecast).
            # Empty array when forecaster output predates this field — viewer
            # gracefully renders zeros in that case.
            "ly_ord":           r.get("history_ly_ord", []),
            "ly_shp":           r.get("history_ly_shp", []),
            "last_comment":     r.get("last_comment", "") or "",
            "last_comment_date":r.get("last_comment_date", "") or "",
            # Flagged is a real QB boolean field (fid 1592).  toggleFlag()
            # writes back via /api/toggle-flag instead of localStorage so
            # the flag is shared across users/browsers.
            "flagged":          bool(r.get("flagged_qb", False)),
            # POG / ISO context (added 2026-05-10) for the Inventory Plan block
            "store_count":      int(r.get("store_count") or 0),
            "pog_launch":       r.get("pog_launch", "") or "",
            "pog_end":          r.get("pog_end", "") or "",
            "master_pack":      int(r.get("master_pack") or r.get("mp") or 1),
            "opn_w":            r.get("opn_w") or [],
            # Three parallel 26-week series from QB Inventory Flow + gap scalars
            "inv_flow_beg":       r.get("inv_flow_beg") or None,
            "inv_flow_rcv":       r.get("inv_flow_rcv") or None,
            "inv_flow_prj":       r.get("inv_flow_prj") or None,
            "inv_flow_opt_wos":   float(r.get("inv_flow_opt_wos") or 0),
            "inv_flow_next_rcpt": r.get("inv_flow_next_rcpt") or "",
        })

    # Serialize records into module-global cache for /api/records.json to
    # serve.  Inline payload in the HTML is now an empty array — the page
    # bootstraps with a visible loading overlay and fetches records over the
    # wire, so the user sees progress within ~50ms instead of waiting 5-15s
    # for the browser to parse a 9MB inline JSON literal.
    import gzip as _gzip
    _records_bytes = json.dumps(records_js, separators=(",", ":")).encode("utf-8")
    _RECORDS_PAYLOAD_BYTES = _records_bytes
    try:
        _RECORDS_PAYLOAD_GZIP = _gzip.compress(_records_bytes, compresslevel=5)
    except Exception:
        _RECORDS_PAYLOAD_GZIP = None
    data_json  = "[]"   # placeholder — populated via fetch on page load
    cols_json  = json.dumps(prj_cols)
    # Compute the real W1 Sunday date (most recent Sunday on or before today)
    from datetime import date as _date, timedelta as _td
    _today = _date.today()
    _days_since_sun = (_today.weekday() + 1) % 7
    _w1_sunday = _today - _td(days=_days_since_sun)
    w1_date_str = _w1_sunday.strftime("%Y-%m-%d")   # e.g. "2026-04-12"
    n_total    = summ.get("total_records", len(recs))
    n_critical = summ.get("critical_records", 0)
    n_warning  = summ.get("warning_records", 0)
    n_clean    = summ.get("clean_records", 0)
    pri_crit   = summ.get("priority_critical", sum(1 for r in recs if r.get("priority") == "CRITICAL"))
    pri_med    = summ.get("priority_medium", sum(1 for r in recs if r.get("priority") == "MEDIUM"))
    pri_low    = summ.get("priority_low", sum(1 for r in recs if r.get("priority") == "LOW"))
    # Volume-tier counts (separate from priority). Header badges reflect volume only.
    vol_high   = summ.get("vol_high",   sum(1 for r in recs if r.get("vol_tier") == "HIGH"))
    vol_med    = summ.get("vol_medium", sum(1 for r in recs if r.get("vol_tier") == "MEDIUM"))
    vol_low    = summ.get("vol_low",    sum(1 for r in recs if r.get("vol_tier") == "LOW"))

    _html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Pets + People Forecast Management</title>
<style>
  *, *::before, *::after {{ box-sizing: border-box; margin:0; padding:0; }}
  body  {{ font-family: "Segoe UI", Arial, sans-serif; font-size: 13px;
           background: #f0f2f5; color: #222; }}
  .topbar {{ background:#4a148c; color:#fff; padding:10px 18px;
             display:flex; align-items:center; gap:12px; flex-wrap:wrap; }}
  .topbar h1 {{ font-size:16px; font-weight:600; flex:1; }}
  .badge {{ padding:2px 10px; border-radius:12px;
            font-size:12px; font-weight:700; }}
  .badge-total    {{ background:#fff; color:#4a148c; }}
  .badge-critical {{ background:#c62828; color:#fff; cursor:pointer; transition:opacity .15s; }}
  .badge-warning  {{ background:#f9a825; color:#333; cursor:pointer; transition:opacity .15s; }}
  .badge-clean    {{ background:#2e7d32; color:#fff; cursor:pointer; transition:opacity .15s; }}
  .badge-critical:hover, .badge-warning:hover, .badge-clean:hover {{ opacity:.8; }}
  .badge-active   {{ outline:3px solid #fff; outline-offset:2px; }}
  .badge-pri-crit {{ border:2px solid #ef9a9a; color:#ef9a9a; background:transparent; cursor:pointer; transition:opacity .15s; }}
  .badge-pri-med  {{ border:2px solid #ffcc80; color:#ffcc80; background:transparent; cursor:pointer; transition:opacity .15s; }}
  .badge-pri-low  {{ border:2px solid #bdbdbd; color:#bdbdbd; background:transparent; cursor:pointer; transition:opacity .15s; }}
  .badge-pri-crit:hover, .badge-pri-med:hover, .badge-pri-low:hover {{ opacity:.75; }}
  .badge-pri-crit.badge-active {{ background:#c62828; color:#fff; border-color:#c62828; }}
  .badge-pri-med.badge-active  {{ background:#e65100; color:#fff; border-color:#e65100; }}
  .badge-pri-low.badge-active  {{ background:#616161; color:#fff; border-color:#616161; }}
  .toolbar {{ background:#fff; border-bottom:1px solid #ddd; padding:6px 12px;
              display:flex; align-items:center; gap:6px; flex-wrap:nowrap;
              overflow-x:auto; }}
  .toolbar input, .toolbar select {{
    font-size:11px; padding:3px 6px; border:1px solid #ccc; border-radius:4px;
    min-width:0; }}
  /* Scope to #search only — `.toolbar input` would also match the filter
     checkboxes inside .ms-opt (the panel is still a DOM-descendant of
     .toolbar even though it's position:fixed) and blow them up to 220px.
     Use width (not flex-basis) because #search lives inside .ms-wrap which
     is column-flex; flex-basis would set HEIGHT and grow the input vertically. */
  .toolbar #search             {{ width:220px; flex:0 0 auto; }}
  .toolbar select              {{ flex:0 1 auto; max-width:135px; }}
  .toolbar button              {{ flex:0 0 auto; }}
  /* ── Filter wrap: stacks a small label above each filter control ─── */
  .ms-wrap {{ display:inline-flex; flex-direction:column; gap:1px; flex:0 1 auto; }}
  .ms-wrap > .ms-label {{
    font-size:9px; color:#6a6a6a; padding:0 2px;
    text-transform:uppercase; letter-spacing:.5px; font-weight:700;
    line-height:1; }}
  .ms-wrap > input,
  .ms-wrap > select {{ margin:0; }}
  /* ── Multi-select dropdown widget ──────────────────────────────────── */
  .ms {{ position:relative; display:inline-block; flex:0 1 auto; }}
  .ms-btn {{ font-size:11px; padding:3px 8px; border:1px solid #ccc; border-radius:4px;
             background:#fff; cursor:pointer; min-width:120px; max-width:180px;
             text-align:left; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }}
  /* CSS-border triangle (no Unicode glyph) — escape sequences like \25BE
     get mojibake'd in some encoding paths and end up rendering as "□BE". */
  .ms-btn::after {{
    content:''; display:inline-block; margin-left:6px;
    border-left:4px solid transparent; border-right:4px solid transparent;
    border-top:5px solid #999; vertical-align:middle;
  }}
  .ms-btn:hover::after {{ border-top-color:#555; }}
  .ms-btn:hover {{ background:#f0f4f8; }}
  .ms-btn.has-sel {{ border-color:#1565c0; color:#1565c0; font-weight:600; }}
  /* Panel uses position:fixed so it escapes the toolbar's overflow-x:auto
     clipping rectangle and lays over the report below.  Coordinates are
     computed in JS from the trigger button's getBoundingClientRect() each
     time the panel opens. */
  .ms-panel {{ display:none; position:fixed; z-index:9999;
               background:#fff; border:1px solid #bbb; border-radius:4px;
               box-shadow:0 2px 12px rgba(0,0,0,0.18); min-width:200px; max-width:340px;
               max-height:340px; overflow-y:auto; padding:6px 0; }}
  .ms.open .ms-panel {{ display:block; }}
  .ms-search {{ width:calc(100% - 14px); margin:0 7px 4px 7px; padding:3px 6px;
                border:1px solid #ddd; border-radius:3px; font-size:11px; box-sizing:border-box; }}
  .ms-actions {{ padding:0 7px 5px 7px; display:flex; gap:6px;
                  border-bottom:1px solid #eee; margin-bottom:4px; }}
  .ms-actions button {{ font-size:10px; padding:2px 8px; border:1px solid #ccc;
                         background:#fff; border-radius:3px; cursor:pointer; }}
  .ms-actions button:hover {{ background:#eef4ff; }}
  /* Options sit flush left (4px breathing room only); checkbox sits 1px
     from the label text. */
  .ms-opt {{ display:flex; align-items:center; justify-content:flex-start; text-align:left;
             padding:3px 4px; font-size:11px; cursor:pointer; user-select:none;
             line-height:1.3; white-space:nowrap; }}
  .ms-opt:hover {{ background:#eef4ff; }}
  .ms-opt input {{ margin:0 1px 0 0; flex:0 0 auto; }}
  .ms-opt span  {{ flex:1 1 auto; min-width:0; overflow:hidden; text-overflow:ellipsis;
                    text-align:left; }}
  .stat {{ font-size:12px; color:#666; margin-left:auto; flex-shrink:0;
           white-space:nowrap; }}
  /* border-collapse:separate is REQUIRED for sticky thead to work reliably
     in Chrome/Firefox — collapse mode disables position:sticky on <th>.
     box-shadow replaces the visual borders we'd lose. */
  table {{ width:100%; border-collapse:separate; border-spacing:0; }}
  thead th {{ position:sticky; top:0; background:#f8f9fa;
              box-shadow: inset 0 -2px 0 #ddd;
              padding:6px 8px; text-align:left; font-size:12px; font-weight:600; cursor:pointer;
              user-select:none; white-space:nowrap; z-index:5; }}
  thead th.sortable:hover {{ background:#eef4ff; }}
  thead th .sort-ind {{ display:inline-block; width:10px; color:#1565c0; font-size:10px;
                         margin-left:3px; opacity:0.85; }}
  thead th.sort-asc  .sort-ind::after {{ content:"\\25B2"; }}   /* ▲ */
  thead th.sort-desc .sort-ind::after {{ content:"\\25BC"; }}   /* ▼ */
  /* Filter row sticks just below the main header.  top:32px matches the
     rendered height of the main thead (padding:6px×2 + ~20px line-height). */
  thead tr.col-filter-row th {{ position:sticky; top:32px; background:#fafbfc;
                                 box-shadow: inset 0 -1px 0 #e0e0e0; padding:3px 4px;
                                 cursor:default; font-weight:normal; z-index:4; }}
  thead tr.col-filter-row th:hover {{ background:#fafbfc; }}
  .col-filter {{ width:100%; box-sizing:border-box; padding:2px 5px;
                  border:1px solid #ccc; border-radius:3px; font-size:11px;
                  background:#fff; color:#333; }}
  .col-filter:focus {{ border-color:#1565c0; outline:none;
                        box-shadow:0 0 0 2px rgba(21,101,192,0.15); }}
  .col-filter::placeholder {{ color:#aaa; font-style:italic; }}
  tbody td {{ padding:5px 8px; border-bottom:1px solid #eee; font-size:12px; }}
  tbody tr:hover {{ background:#eef4ff; }}
  .clickable {{ cursor:pointer; color:#1565c0; text-decoration:underline; }}
  .sev-crit {{ color:#c62828; font-weight:700; }}
  .sev-warn {{ color:#e65100; font-weight:700; }}
  .sev-ok   {{ color:#2e7d32; }}
  .border-crit {{ border-left: 4px solid #c62828; }}
  .border-warn {{ border-left: 4px solid #f9a825; }}
  .border-ok   {{ border-left: 4px solid #e0e0e0; }}
  .pct-up   {{ color:#2e7d32; font-weight:600; }}
  .pct-down {{ color:#c62828; font-weight:600; }}
  .pct-flat {{ color:#888; }}
  .detail-pane {{ display:none; background:#fffbe6; }}
  .detail-pane td {{ padding:8px 12px; }}
  .dtbl {{ width:100%; border-collapse:collapse; font-size:11px; }}
  .dtbl th, .dtbl td {{ border:1px solid #ddd; padding:3px 6px; text-align:right;
                        min-width:60px; }}
  .dtbl th {{ background:#f0f4f8; text-align:center; font-weight:600; }}
  .dtbl .row-label {{ text-align:left; font-weight:600; background:#fafafa; min-width:80px; }}
  .wk-crit {{ background:#ffcdd2; font-weight:700; }}
  .wk-warn {{ background:#fff9c4; font-weight:600; }}
  .wk-ok   {{ background:#fff; }}
  .reason-tip {{ font-size:10px; color:#666; display:block; max-width:120px;
                 white-space:normal; line-height:1.2; }}
  .tag {{ display:inline-block; font-size:10px; padding:1px 6px; border-radius:8px;
          margin-left:4px; }}
  .tag-bw {{ background:#e3f2fd; color:#1565c0; }}
  /* Priority badges */
  .pri-crit {{ background:#c62828; color:#fff; padding:2px 8px; border-radius:10px;
               font-size:11px; font-weight:700; }}
  .pri-med  {{ background:#e65100; color:#fff; padding:2px 8px; border-radius:10px;
               font-size:11px; font-weight:700; }}
  .pri-low  {{ background:#9e9e9e; color:#fff; padding:2px 8px; border-radius:10px;
               font-size:11px; font-weight:600; }}
  /* Flag button */
  .flag-btn {{ background:none; border:1px solid #ccc; border-radius:4px; cursor:pointer;
               padding:2px 6px; font-size:16px; line-height:1; color:#555; }}
  .flag-btn:hover {{ background:#ffebee; border-color:#c62828; }}
  .flag-btn.flagged {{ color:#c62828; border-color:#ccc; background:none; }}
  /* Comments */
  .comment-input {{ font-size:11px; width:160px; padding:2px 5px; border:1px solid #ddd;
                    border-radius:3px; resize:vertical; min-height:24px; }}
  .comment-input:focus {{ border-color:#1565c0; outline:none; }}
  /* Use AI / Use Suggested buttons */
  .use-btn {{ font-size:10px; padding:3px 6px; border:1px solid #ccc; border-radius:3px;
              background:#fff; cursor:pointer; font-weight:600; white-space:nowrap; }}
  .use-btn.use-ai  {{ color:#1565c0; border-color:#90caf9; }}
  .use-btn.use-ai:hover  {{ background:#e3f2fd; }}
  .use-btn.use-sug {{ color:#555;    border-color:#ccc; }}
  .use-btn.use-sug:hover {{ background:#eeeeee; }}
  .use-btn:disabled {{ opacity:0.6; cursor:wait; }}
  .use-btn.done {{ background:#c8e6c9; color:#1b5e20; border-color:#81c784; }}
  .use-btn.failed {{ background:#ffcdd2; color:#b71c1c; border-color:#ef9a9a; }}
  /* Export bar */
  .export-bar {{ background:#fff; border-top:1px solid #ddd; padding:8px 18px;
                 position:sticky; bottom:0; display:flex; align-items:center; gap:10px; }}
  .export-btn {{ background:#2c5f8a; color:#fff; border:none; padding:6px 16px; border-radius:4px;
                 cursor:pointer; font-size:12px; font-weight:600; }}
  .export-btn:hover {{ background:#1a4060; }}
  .export-count {{ font-size:12px; color:#666; }}
</style>
</head>
<body>

<div id="bootOverlay" style="position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(15,15,30,0.92);color:#fff;z-index:9999;display:flex;flex-direction:column;justify-content:center;align-items:center;font-family:'Segoe UI',Arial,sans-serif;">
  <div style="font-size:22px;font-weight:600;margin-bottom:14px;">Pets + People Forecast Management</div>
  <div id="bootSpinner" style="border:5px solid #444;border-top:5px solid #f9a825;border-radius:50%;width:46px;height:46px;animation:spin 1s linear infinite;margin-bottom:18px;"></div>
  <div id="bootStatus" style="font-size:15px;color:#ddd;max-width:560px;text-align:center;line-height:1.5;font-weight:500;">Loading projections…</div>
  <div id="bootDetail" style="font-size:11px;color:#888;margin-top:14px;text-align:center;max-width:560px;">Streaming {n_total} records from viewer backend</div>
</div>
<style>@keyframes spin {{ to {{ transform: rotate(360deg); }} }}</style>

<div class="topbar">
  <h1>Projection Validation Review</h1>
  <span class="badge badge-total" id="btn-total" onclick="resetAllFilters()" title="Click to clear all filters and show all active projections" style="cursor:pointer;">{n_total} records</span>
  <span class="badge badge-critical" id="btn-high" onclick="filterVol('HIGH')" title="Filter to high-volume records">{vol_high} high vol</span>
  <span class="badge badge-warning"  id="btn-med"  onclick="filterVol('MEDIUM')" title="Filter to medium-volume records">{vol_med} med vol</span>
  <span class="badge badge-clean"    id="btn-low"  onclick="filterVol('LOW')" title="Filter to low-volume records">{vol_low} low vol</span>
  <span style="color:rgba(255,255,255,0.25);font-size:18px;margin:0 2px;line-height:1;">|</span>
  <span class="badge badge-pri-crit" id="btn-pri-crit" onclick="filterPri('CRITICAL')" title="Filter to critical-priority records">{pri_crit} critical</span>
  <span class="badge badge-pri-med"  id="btn-pri-med"  onclick="filterPri('MEDIUM')"   title="Filter to medium-priority records">{pri_med} medium</span>
  <span class="badge badge-pri-low"  id="btn-pri-low"  onclick="filterPri('LOW')"      title="Filter to low-priority records">{pri_low} low pri</span>
</div>

<div class="toolbar">
  <div class="ms-wrap"><span class="ms-label">Search</span>
    <input id="search" type="text" placeholder="key / customer / mstyle ..." oninput="applyFilters()">
  </div>
  <div class="ms-wrap"><span class="ms-label">Customer</span>
    <div id="custFilter" class="ms" data-all-label="All Customers"></div>
  </div>
  <div class="ms-wrap"><span class="ms-label">Brand</span>
    <div id="brandFilter" class="ms" data-all-label="All Brand Names"></div>
  </div>
  <div class="ms-wrap"><span class="ms-label">Inv Mgr</span>
    <div id="mgrFilter" class="ms" data-all-label="All Inventory Mgrs"></div>
  </div>
  <div class="ms-wrap"><span class="ms-label">AI vs Proj</span>
    <select id="aiDiffFilter" onchange="applyFilters()">
      <option value="0">All (any difference)</option>
      <option value="10">AI vs Proj &gt; 10%</option>
      <option value="25">AI vs Proj &gt; 25%</option>
      <option value="50">AI vs Proj &gt; 50%</option>
    </select>
  </div>
  <button type="button" onclick="resetSort()" title="Restore the default order (Inv Mgr → Brand → Customer → Mstyle); leaves filters alone" style="font-size:11px;padding:4px 10px;border:1px solid #1565c0;background:#fff;color:#1565c0;border-radius:4px;cursor:pointer;font-weight:600;">Reset Sort</button>
  <button type="button" id="flaggedOnlyBtn" onclick="toggleFlaggedOnly()" title="Show only records flagged for inventory mgr review (toggle)" style="font-size:11px;padding:4px 10px;border:1px solid #c62828;background:#fff;color:#c62828;border-radius:4px;cursor:pointer;font-weight:600;">⚑ Show Flagged Only</button>
  <button type="button" onclick="resetAllFilters()" title="Clear search box and reset every filter to its default" style="font-size:11px;padding:4px 10px;border:1px solid #c62828;background:#fff;color:#c62828;border-radius:4px;cursor:pointer;font-weight:600;">✕ Clear Filters</button>
  <span class="stat" id="statLine">{n_total} records shown</span>
  <!-- Hidden multiselects keep filterVol/filterPri/applyFilters JS working -->
  <div style="display:none">
    <div id="volFilter" class="ms" data-all-label="All volumes"></div>
    <div id="priFilter" class="ms" data-all-label="All priorities"></div>
    <div id="patFilter" class="ms" data-all-label="All models"></div>
  </div>
</div>
<div id="pageNav" style="display:none;padding:6px 12px;background:#f8f9fa;border-bottom:1px solid #ddd;font-size:13px;display:flex;align-items:center;gap:10px;">
  <button onclick="changePage(-1)" id="prevBtn" style="padding:3px 10px;">&#8592; Prev</button>
  <span id="pageInfo"></span>
  <button onclick="changePage(1)" id="nextBtn" style="padding:3px 10px;">Next &#8594;</button>
</div>

<table>
<thead>
  <!-- Click any header to cycle sort: asc → desc → off (default).
       data-col-type='number' columns understand >N <N >=N <=N =N != N
       in their per-column filter input. -->
  <tr>
    <th style="width:28px;"></th>
    <th class="sortable" data-sort-key="key"          data-col-type="string"  onclick="cycleSort('key')">Key <span class="sort-ind"></span></th>
    <th class="sortable" data-sort-key="inv_manager"  data-col-type="string"  onclick="cycleSort('inv_manager')">Inv Mgr <span class="sort-ind"></span></th>
    <th class="sortable" data-sort-key="brand"        data-col-type="string"  onclick="cycleSort('brand')">Brand Name <span class="sort-ind"></span></th>
    <th class="sortable" data-sort-key="cust"         data-col-type="string"  onclick="cycleSort('cust')">Customer <span class="sort-ind"></span></th>
    <th class="sortable" data-sort-key="mstyle"       data-col-type="string"  onclick="cycleSort('mstyle')">Mstyle <span class="sort-ind"></span></th>
    <th class="sortable" data-sort-key="desc"         data-col-type="string"  onclick="cycleSort('desc')">Description <span class="sort-ind"></span></th>
    <th class="sortable" data-sort-key="asin_status"  data-col-type="string"  onclick="cycleSort('asin_status')">Status @ Cust <span class="sort-ind"></span></th>
    <th class="sortable" data-sort-key="item_status"  data-col-type="string"  onclick="cycleSort('item_status')">Item Status <span class="sort-ind"></span></th>
    <th class="sortable" data-sort-key="priority"     data-col-type="priority" onclick="cycleSort('priority')">Priority <span class="sort-ind"></span></th>
    <th class="sortable" data-sort-key="ord_wk_l4"   data-col-type="number"  onclick="cycleSort('ord_wk_l4')">Ord/Wk L4W <span class="sort-ind"></span></th>
    <th class="sortable" data-sort-key="shp_wk"       data-col-type="number"  onclick="cycleSort('shp_wk')">Ord/Wk L13W <span class="sort-ind"></span></th>
    <th class="sortable" data-sort-key="proj_wk"      data-col-type="number"  onclick="cycleSort('proj_wk')">Proj/Wk <span class="sort-ind"></span></th>
    <th class="sortable" data-sort-key="ai_wk"        data-col-type="number"  onclick="cycleSort('ai_wk')">AI Fcst/Wk <span class="sort-ind"></span></th>
    <th class="sortable" data-sort-key="sugg_wk"      data-col-type="number"  onclick="cycleSort('sugg_wk')" title="Average of Suggested W1..W26">Sugg /Wk <span class="sort-ind"></span></th>
    <th class="sortable" data-sort-key="ai_vs_proj"   data-col-type="number"  onclick="cycleSort('ai_vs_proj')">AI vs Proj <span class="sort-ind"></span></th>
    <th class="sortable" data-sort-key="ai_vs_l13"    data-col-type="number"  onclick="cycleSort('ai_vs_l13')">AI vs L13 <span class="sort-ind"></span></th>
    <th class="sortable" data-sort-key="man_vs_l13"   data-col-type="number"  onclick="cycleSort('man_vs_l13')">Man vs L13 <span class="sort-ind"></span></th>
    <th style="width:70px;">Use AI</th>
    <th style="width:90px;">Use Sugg</th>
  </tr>
  <!-- Per-column quick-filter row.  Numeric columns understand operators. -->
  <tr class="col-filter-row">
    <th></th>
    <th><input class="col-filter" data-field="key"          data-col-type="string"  oninput="applyFilters()" placeholder="filter..."></th>
    <th><input class="col-filter" data-field="inv_manager"  data-col-type="string"  oninput="applyFilters()" placeholder="filter..."></th>
    <th><input class="col-filter" data-field="brand"        data-col-type="string"  oninput="applyFilters()" placeholder="filter..."></th>
    <th><input class="col-filter" data-field="cust"         data-col-type="string"  oninput="applyFilters()" placeholder="filter..."></th>
    <th><input class="col-filter" data-field="mstyle"       data-col-type="string"  oninput="applyFilters()" placeholder="filter..."></th>
    <th><input class="col-filter" data-field="desc"         data-col-type="string"  oninput="applyFilters()" placeholder="filter..."></th>
    <th><input class="col-filter" data-field="asin_status"  data-col-type="string"  oninput="applyFilters()" placeholder="filter..."></th>
    <th><input class="col-filter" data-field="item_status"  data-col-type="string"  oninput="applyFilters()" placeholder="filter..."></th>
    <th><input class="col-filter" data-field="priority"     data-col-type="string"  oninput="applyFilters()" placeholder="CRIT/MED/LOW"></th>
    <th><input class="col-filter" data-field="ord_wk_l4"    data-col-type="number"  oninput="applyFilters()" placeholder=">100, <50..."></th>
    <th><input class="col-filter" data-field="shp_wk"       data-col-type="number"  oninput="applyFilters()" placeholder=">100, <50..."></th>
    <th><input class="col-filter" data-field="proj_wk"      data-col-type="number"  oninput="applyFilters()" placeholder=">100, <50..."></th>
    <th><input class="col-filter" data-field="ai_wk"        data-col-type="number"  oninput="applyFilters()" placeholder=">100, <50..."></th>
    <th><input class="col-filter" data-field="sugg_wk"      data-col-type="number"  oninput="applyFilters()" placeholder=">100, <50..."></th>
    <th><input class="col-filter" data-field="ai_vs_proj"   data-col-type="number"  oninput="applyFilters()" placeholder=">10, <-5..."></th>
    <th><input class="col-filter" data-field="ai_vs_l13"    data-col-type="number"  oninput="applyFilters()" placeholder=">10, <-5..."></th>
    <th><input class="col-filter" data-field="man_vs_l13"   data-col-type="number"  oninput="applyFilters()" placeholder=">10, <-5..."></th>
    <th></th>
    <th></th>
  </tr>
</thead>
<tbody id="tbody"></tbody>
</table>

<script>
// ALL_RECORDS starts empty; populated by _bootstrap() via /api/records.json.
// Kept as `let` so the bootstrap fetcher can swap in the real array.
let ALL_RECORDS   = {data_json};
const PRJ_COLS    = {cols_json};

const W1_DATE     = new Date('{w1_date_str}T00:00:00');
function weekLabel(i) {{
  const d = new Date(W1_DATE);
  d.setDate(d.getDate() + i * 7);
  return (d.getMonth()+1).toString().padStart(2,'0') + '/' + d.getDate().toString().padStart(2,'0');
}}

function fmtN(n) {{ return n == null ? '-' : Number(n).toLocaleString(); }}

function sevIcon(s) {{
  if (s === 'CRITICAL') return '<span class="sev-crit">\u2622 CRITICAL</span>';
  if (s === 'WARNING')  return '<span class="sev-warn">\u26a0 WARNING</span>';
  return '<span class="sev-ok">\u2714 OK</span>';
}}

function pctClass(p) {{
  if (p >= 5)  return 'pct-up';
  if (p <= -5) return 'pct-down';
  return 'pct-flat';
}}

function borderClass(s) {{
  if (s === 'CRITICAL') return 'border-crit';
  if (s === 'WARNING')  return 'border-warn';
  return 'border-ok';
}}

function weekCellClass(sev) {{
  if (sev === 'CRITICAL') return 'wk-crit';
  if (sev === 'WARNING')  return 'wk-warn';
  return 'wk-ok';
}}

// ── localStorage persistence (per-row note only — Flag is now QB-backed) ──
//
// The ⚑ icon used to flip a localStorage boolean.  Now it writes the
// `Flagged` checkbox on Projections (fid 1592) via /api/toggle-flag, so a
// flag set in one browser is visible to every user who opens the viewer
// or codepage.  The per-row note textarea is still local — it's a draft
// scratchpad, not a shared signal.
const STORAGE_KEY = 'validation_review';
let userData = JSON.parse(localStorage.getItem(STORAGE_KEY) || '{{}}');

// Seed userData[key].flagged from the QB-loaded `flagged` boolean so the
// header count and ⚑ icon state reflect the current QB value at startup.
function _seedFlagsFromQB() {{
  ALL_RECORDS.forEach(r => {{
    if (r.flagged) {{
      if (!userData[r.key]) userData[r.key] = {{}};
      userData[r.key].flagged = true;
    }} else if (userData[r.key]) {{
      delete userData[r.key].flagged;  // QB says not flagged → drop stale local flag
    }}
  }});
}}

function saveUserData() {{
  localStorage.setItem(STORAGE_KEY, JSON.stringify(userData));
  updateFlagCount();
}}

// ── Status @ Cust inline edit (mirrors codepage/viewer.js) ────────────────
let _STATUS_CHOICES_CACHE = null;
function _statusChoices() {{
  if (_STATUS_CHOICES_CACHE) return _STATUS_CHOICES_CACHE;
  const seen = new Map();
  (ALL_RECORDS || []).forEach(r => {{
    const v = (r.asin_status || '').trim();
    if (!v) return;
    seen.set(v, (seen.get(v) || 0) + 1);
  }});
  const arr = Array.from(seen.entries());
  const groupOf = v => v.toUpperCase().startsWith('A') ? 0
                     : v.toUpperCase().startsWith('FD') ? 1
                     : v.toUpperCase().startsWith('NEW') ? 2 : 3;
  arr.sort((a, b) => {{
    const ga = groupOf(a[0]), gb = groupOf(b[0]);
    if (ga !== gb) return ga - gb;
    if (b[1] !== a[1]) return b[1] - a[1];
    return a[0].localeCompare(b[0]);
  }});
  _STATUS_CHOICES_CACHE = arr.map(([v]) => v);
  return _STATUS_CHOICES_CACHE;
}}

function _renderStatusCell(asin_status, key) {{
  const safeKey = (key || '').replace(/'/g, "&#39;");
  const display = asin_status || ' ';
  return `<td class="status-cust-cell" data-key="${{safeKey}}"
              onclick="editStatusCust('${{safeKey}}', this)"
              title="Click to change Status @ Cust"
              style="font-size:11px;white-space:nowrap;cursor:pointer;
                     padding:2px 6px;border-bottom:1px dashed transparent;"
              onmouseover="this.style.borderBottomColor='#1565c0'"
              onmouseout="this.style.borderBottomColor='transparent'">${{display}}</td>`;
}}

function editStatusCust(key, cellEl) {{
  if (cellEl.querySelector('select')) return;
  const rec = ALL_RECORDS.find(x => x.key === key);
  if (!rec) return;
  const current = rec.asin_status || '';
  const choices = _statusChoices().slice();
  if (current && !choices.includes(current)) choices.unshift(current);
  let opts = '';
  for (const c of choices) {{
    const sel = c === current ? ' selected' : '';
    const safe = c.replace(/"/g, '&quot;');
    opts += `<option value="${{safe}}"${{sel}}>${{safe}}</option>`;
  }}
  opts += `<option value="__CUSTOM__" style="font-style:italic;color:#1565c0">+ Custom value...</option>`;
  cellEl.innerHTML = `<select style="font-size:11px;padding:1px 3px;border:1px solid #1565c0;
                                     border-radius:3px;max-width:170px;">${{opts}}</select>`;
  const sel = cellEl.querySelector('select');
  sel.focus();
  sel.addEventListener('change', () => _commitStatusEdit(key, sel.value, cellEl));
  sel.addEventListener('blur', () => {{
    setTimeout(() => {{
      if (cellEl.querySelector('select')) {{
        cellEl.innerHTML = (rec.asin_status || ' ');
      }}
    }}, 150);
  }});
}}

async function _commitStatusEdit(key, newValue, cellEl) {{
  const rec = ALL_RECORDS.find(x => x.key === key);
  if (!rec) return;
  if (newValue === '__CUSTOM__') {{
    const custom = window.prompt(
      'Enter custom Status @ Cust value (e.g. "FD 09/26"):',
      rec.asin_status || ''
    );
    if (custom == null) {{
      cellEl.innerHTML = (rec.asin_status || ' ');
      return;
    }}
    newValue = custom.trim();
  }}
  if (newValue === rec.asin_status) {{
    cellEl.innerHTML = (rec.asin_status || ' ');
    return;
  }}
  const prev = rec.asin_status;
  rec.asin_status = newValue;
  cellEl.innerHTML = `<span style="color:#1565c0">${{newValue || ' '}}</span>`;
  _STATUS_CHOICES_CACHE = null;
  try {{
    const res = await fetch('/api/update-status-cust', {{
      method: 'POST',
      headers: {{'Content-Type':'application/json'}},
      body: JSON.stringify({{key: key, value: newValue}}),
    }});
    if (!res.ok) throw new Error('HTTP ' + res.status);
    const data = await res.json();
    if (data.error) throw new Error(data.error);
    cellEl.innerHTML = (newValue || ' ');
    cellEl.title = 'Click to change Status @ Cust';
  }} catch (e) {{
    console.error('Status @ Cust save failed:', e);
    rec.asin_status = prev;
    cellEl.innerHTML = `<span style="color:#c62828" title="Save failed: ${{(e.message||'').replace(/"/g,'&quot;')}}">${{prev || ' '}} ⚠</span>`;
  }}
}}

// ── Tell-AI: planner explains logic, AI proposes a 26-week diff ──────────────
// Same parser + flow as codepage/viewer.js.  When applied, stages MAN cells
// via the existing manual-edit pipeline and saves the comment via /api/add-
// comment so the audit trail lives in the same Projection Comments table.

// Forecast-week calendar mapping for plain-English month references.
const _MONTH_TO_WEEK_RANGE = {{
  'may':[0,4], 'jun':[5,8], 'june':[5,8], 'jul':[9,13], 'july':[9,13],
  'aug':[14,17], 'august':[14,17], 'sep':[18,21], 'sept':[18,21],
  'september':[18,21], 'oct':[22,25], 'october':[22,25],
}};
function _monthRange(monthStr) {{
  if (!monthStr) return null;
  return _MONTH_TO_WEEK_RANGE[String(monthStr).toLowerCase().slice(0, 9)] || null;
}}
function _dateToWeekIdx(dateStr) {{
  if (!dateStr) return null;
  const s = String(dateStr).toLowerCase().trim();
  let m = s.match(/^(\\d{{1,2}})\\/(\\d{{1,2}})/);
  let mo, dd;
  if (m) {{ mo = parseInt(m[1], 10); dd = parseInt(m[2], 10); }}
  else {{
    m = s.match(/(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\\s+(\\d{{1,2}})/);
    if (m) {{ const mp = {{jan:1,feb:2,mar:3,apr:4,may:5,jun:6,jul:7,aug:8,sep:9,oct:10,nov:11,dec:12}}; mo = mp[m[1]]; dd = parseInt(m[2], 10); }}
  }}
  if (!mo || !dd) return null;
  const moDays = [0,31,28,31,30,31,30,31,31,30,31,30,31];
  let dayOfYear = dd;
  for (let i = 1; i < mo; i++) dayOfYear += moDays[i];
  const w1Day = 123;
  const w = Math.floor((dayOfYear - w1Day) / 7);
  if (w < 0 || w > 25) return null;
  return w;
}}

function _parseAiAdjustment(text, currentForecast) {{
  if (!text || !Array.isArray(currentForecast) || currentForecast.length !== 26) {{
    return {{ parsed: false, summary: 'No forecast loaded for this record.' }};
  }}
  const t = String(text).trim();
  const lo = t.toLowerCase();
  const cur = currentForecast.map(v => Number(v) || 0);
  const out = cur.slice();
  const _clamp = (n) => Math.max(0, Math.min(25, n - 1));
  const _round = (v) => Math.max(0, Math.round(v));

  let m = lo.match(/(?:eol|wind[-\\s]*down|discontinu(?:e|ed|ing)|phase[-\\s]*out|end[-\\s]*of[-\\s]*life)[^\\d]*w?(\\d{{1,2}})/);
  if (m) {{
    const tgt = _clamp(parseInt(m[1], 10));
    const taper = {{ 0: 0.25, 1: 0.45, 2: 0.65, 3: 0.85 }};
    for (let i = 0; i < 26; i++) {{
      if (i > tgt) {{ out[i] = 0; }}
      else {{
        const d = tgt - i;
        if (d in taper) out[i] = _round(cur[i] * taper[d]);
      }}
    }}
    return {{
      parsed: true, newForecast: out, type: 'eol',
      summary: `Wind-down: forecast tapers W${{tgt - 2}}-W${{tgt + 1}} (85%→25% of current) and zeros W${{tgt + 2}}-W26.`,
      deltaTotal: out.reduce((a,b)=>a+b,0) - cur.reduce((a,b)=>a+b,0),
    }};
  }}
  m = lo.match(/(?:zero|no\\s*orders?|po\\s*covers?|covered\\s*by\\s*po)[^\\d]*w?(\\d{{1,2}})(?:\\s*[-–]\\s*w?(\\d{{1,2}}))?/);
  if (m) {{
    const a = _clamp(parseInt(m[1], 10));
    const b = m[2] ? _clamp(parseInt(m[2], 10)) : a;
    for (let i = a; i <= b; i++) out[i] = 0;
    return {{
      parsed: true, newForecast: out, type: 'zero_range',
      summary: `Zero out W${{a + 1}}${{b !== a ? `-W${{b + 1}}` : ''}} (PO covers / pause).`,
      deltaTotal: out.reduce((a,b)=>a+b,0) - cur.reduce((a,b)=>a+b,0),
    }};
  }}
  m = lo.match(/(?:set|baseline|target|hold[\\s]+at|run\\s*rate)[^\\d]*([\\d,]+)\\s*(?:u(?:nits?)?\\s*\\/?\\s*wk|\\/\\s*wk|per\\s*wk|per\\s*week|units|u)?(?:[^\\d]*w?(\\d{{1,2}}))?(?:\\s*[-–]\\s*w?(\\d{{1,2}}))?/);
  if (m && parseFloat(m[1].replace(/,/g, '')) > 0) {{
    const baseN = Math.round(parseFloat(m[1].replace(/,/g, '')));
    const a = m[2] ? _clamp(parseInt(m[2], 10)) : 0;
    const b = m[3] ? _clamp(parseInt(m[3], 10)) : 25;
    for (let i = a; i <= b; i++) out[i] = baseN;
    return {{
      parsed: true, newForecast: out, type: 'set_baseline',
      summary: `Set forecast to ${{baseN.toLocaleString()}}/wk for W${{a + 1}}-W${{b + 1}}.`,
      deltaTotal: out.reduce((a,b)=>a+b,0) - cur.reduce((a,b)=>a+b,0),
    }};
  }}
  // Try "week-first" form first: "adjust Wk 14 by 50%" / "W22-W26 +30%" /
  // "Wk 14 50%" / "W14 down 15%".  Verb is optional; week comes before pct.
  m = lo.match(/(?:adjust|change|update|set|lift|cut|boost|bump|gain|drop|raise|reduce|increase|decrease)?\\s*w(?:k|eek)?\\s*(\\d{{1,2}})(?:\\s*[-–]\\s*w(?:k|eek)?\\s*(\\d{{1,2}}))?[^\\d%]*([+-]?)\\s*(\\d+(?:\\.\\d+)?)\\s*%/);
  if (m) {{
    let sign = m[3] === '-' ? -1 : 1;
    if (/cut|drop|decrease|reduction|down|reduce/.test(lo)) sign = -1;
    if (/lift|boost|bump|gain|increase|up|raise/.test(lo)) sign = 1;
    const pct = parseFloat(m[4]);
    const a = _clamp(parseInt(m[1], 10));
    const b = m[2] ? _clamp(parseInt(m[2], 10)) : a;
    const mult = 1 + sign * (pct / 100);
    for (let i = a; i <= b; i++) out[i] = _round(cur[i] * mult);
    const dir = sign > 0 ? 'lift' : 'cut';
    return {{
      parsed: true, newForecast: out, type: 'pct_range',
      summary: `${{sign > 0 ? '+' : '-'}}${{pct}}% ${{dir}} applied to W${{a + 1}}${{b !== a ? `-W${{b + 1}}` : ''}} (${{cur.slice(a, b + 1).reduce((x,y)=>x+y,0).toLocaleString()}}u → ${{out.slice(a, b + 1).reduce((x,y)=>x+y,0).toLocaleString()}}u).`,
      deltaTotal: out.reduce((a,b)=>a+b,0) - cur.reduce((a,b)=>a+b,0),
    }};
  }}
  m = lo.match(/([+-]?)\\s*(\\d+(?:\\.\\d+)?)\\s*%\\s*(?:lift|boost|bump|gain|increase|cut|drop|decrease|reduction|down|up)?[^\\dwW]*(?:starting|from|in|on|for|across)?[^\\dwW]*w?(\\d{{1,2}})(?:\\s*[-–]\\s*w?(\\d{{1,2}}))?/);
  if (!m) {{
    m = lo.match(/(?:gain(?:ed|ing)?|adding|losing|loss(?:ed|ing)?|drop(?:ped)?)[^\\d%]*(\\d+(?:\\.\\d+)?)\\s*%[^\\dwW]*(?:starting|from|in|on|for)?[^\\dwW]*w?(\\d{{1,2}})(?:\\s*[-–]\\s*w?(\\d{{1,2}}))?/);
    if (m) {{
      const verb = lo.includes('los') || lo.includes('drop') || lo.includes('cut') ? '-' : '+';
      m = ['', verb, m[1], m[2], m[3]];
    }}
  }}
  if (m) {{
    let sign = m[1] === '-' ? -1 : 1;
    if (/cut|drop|decrease|reduction|down|los|reduce|lower|pull[\\s]*back|trim|slow|soften/.test(lo)) sign = -1;
    const pct = parseFloat(m[2]);
    const a = _clamp(parseInt(m[3], 10));
    const b = m[4] ? _clamp(parseInt(m[4], 10)) : 25;
    const mult = 1 + sign * (pct / 100);
    for (let i = a; i <= b; i++) out[i] = _round(cur[i] * mult);
    const dir = sign > 0 ? 'lift' : 'cut';
    return {{
      parsed: true, newForecast: out, type: 'pct_range',
      summary: `${{sign > 0 ? '+' : '-'}}${{pct}}% ${{dir}} applied to W${{a + 1}}-W${{b + 1}} (${{cur.slice(a, b + 1).reduce((x,y)=>x+y,0).toLocaleString()}}u → ${{out.slice(a, b + 1).reduce((x,y)=>x+y,0).toLocaleString()}}u).`,
      deltaTotal: out.reduce((a,b)=>a+b,0) - cur.reduce((a,b)=>a+b,0),
    }};
  }}
  // ── Layer 2: natural-language patterns (no explicit Wx required) ─────────
  const monthList = '(may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?)';

  // 2a) Date-based EOL
  m = lo.match(/(?:eol|wind[-\\s]*down|discontinu(?:e|ed|ing)|phase[-\\s]*out|end[-\\s]*of[-\\s]*life)[^\\d]*((?:\\d{{1,2}}\\/\\d{{1,2}})|(?:(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\\s+\\d{{1,2}}))/);
  if (m) {{
    const tgt = _dateToWeekIdx(m[1]);
    if (tgt !== null) {{
      const taper = {{ 0:0.25, 1:0.45, 2:0.65, 3:0.85 }};
      for (let i = 0; i < 26; i++) {{
        if (i > tgt) out[i] = 0;
        else {{ const d = tgt - i; if (d in taper) out[i] = _round(cur[i] * taper[d]); }}
      }}
      return {{
        parsed: true, newForecast: out, type: 'eol_date',
        summary: `Wind-down by ${{m[1]}} (≈ W${{tgt + 1}}): tapers W${{Math.max(1, tgt - 1)}}-W${{tgt + 1}} (85%→25%) and zeros after.`,
        deltaTotal: out.reduce((a,b)=>a+b,0) - cur.reduce((a,b)=>a+b,0),
      }};
    }}
  }}

  // 2b-i) Pct-first month: "+25% in May"
  m = lo.match(new RegExp(`(?:adjust|change|boost|lift|cut|drop|raise|reduce|increase|decrease|gain|loss)?\\\\s*([+-]?)\\\\s*(\\\\d+(?:\\\\.\\\\d+)?)\\\\s*%[^a-z]*(?:in|for|across|throughout|during|of)?\\\\s*` + monthList + `(?:[^a-z]*(?:to|through|until|–|-)[^a-z]*` + monthList + `)?`));
  // 2b-ii) Month-first: "boost June by 30%"
  if (!m) {{
    m = lo.match(new RegExp(`(?:adjust|change|boost|lift|cut|drop|raise|reduce|increase|decrease|gain|loss)?\\\\s*` + monthList + `(?:[^a-z]*(?:to|through|until|–|-)[^a-z]*` + monthList + `)?[^\\\\d%]*([+-]?)\\\\s*(\\\\d+(?:\\\\.\\\\d+)?)\\\\s*%`));
    if (m) m = [m[0], m[3], m[4], m[1], m[2]];
  }}
  if (m) {{
    let sign = m[1] === '-' ? -1 : 1;
    if (/cut|drop|decrease|reduction|down|los|reduce|lower|pull[\\s]*back|trim|slow|soften/.test(lo)) sign = -1;
    if (/lift|boost|bump|gain|increase|up|raise|grow|ramp\\s*up/.test(lo)) sign = 1;
    const pct = parseFloat(m[2]);
    const r1 = _monthRange(m[3]);
    const r2 = m[4] ? _monthRange(m[4]) : null;
    if (r1) {{
      const a = r1[0]; const b = r2 ? r2[1] : r1[1];
      const mult = 1 + sign * (pct / 100);
      for (let i = a; i <= b; i++) out[i] = _round(cur[i] * mult);
      return {{
        parsed: true, newForecast: out, type: 'pct_month',
        summary: `${{sign > 0 ? '+' : '-'}}${{pct}}% ${{sign > 0 ? 'lift' : 'cut'}} applied to ${{m[3]}}${{m[4] ? '-' + m[4] : ''}} (W${{a + 1}}-W${{b + 1}}, ${{cur.slice(a, b + 1).reduce((x,y)=>x+y,0).toLocaleString()}}u → ${{out.slice(a, b + 1).reduce((x,y)=>x+y,0).toLocaleString()}}u).`,
        deltaTotal: out.reduce((a,b)=>a+b,0) - cur.reduce((a,b)=>a+b,0),
      }};
    }}
  }}

  // 2c) Ramp-up / ramp-down starting month
  m = lo.match(new RegExp(`(?:ramp\\\\s*up|increase|grow|boost|build|lift|expand|gain|gained|adding|added|distribution[\\\\s-]*gain).*?(?:starting|beginning|from|in)\\\\s+` + monthList));
  if (m) {{
    const r = _monthRange(m[1]);
    if (r) {{
      const pct = (lo.match(/(\\d+)\\s*%/) || [])[1];
      const lift = pct ? parseFloat(pct) / 100 : 0.20;
      for (let i = r[0]; i <= 25; i++) out[i] = _round(cur[i] * (1 + lift));
      return {{
        parsed: true, newForecast: out, type: 'ramp_up_month',
        summary: `Ramp up: +${{(lift * 100).toFixed(0)}}% applied from ${{m[1]}} (W${{r[0] + 1}}) through W26.`,
        deltaTotal: out.reduce((a,b)=>a+b,0) - cur.reduce((a,b)=>a+b,0),
      }};
    }}
  }}
  m = lo.match(new RegExp(`(?:ramp\\\\s*down|decrease|cut|reduce|wind\\\\s*down|slow)[^a-z]*(?:starting|beginning|from|in)\\\\s+` + monthList));
  if (m) {{
    const r = _monthRange(m[1]);
    if (r) {{
      const pct = (lo.match(/(\\d+)\\s*%/) || [])[1];
      const cut = pct ? parseFloat(pct) / 100 : 0.20;
      for (let i = r[0]; i <= 25; i++) out[i] = _round(cur[i] * (1 - cut));
      return {{
        parsed: true, newForecast: out, type: 'ramp_down_month',
        summary: `Ramp down: -${{(cut * 100).toFixed(0)}}% applied from ${{m[1]}} (W${{r[0] + 1}}) through W26.`,
        deltaTotal: out.reduce((a,b)=>a+b,0) - cur.reduce((a,b)=>a+b,0),
      }};
    }}
  }}

  // 2d) Multipliers: double / triple / halve
  m = lo.match(/(double|triple|quadruple|halve)\\s+(?:this[^a-z]*account|the[^a-z]*forecast|w(?:k|eek)?\\s*(\\d{{1,2}})|(?:in\\s+)?([a-z]+))?/);
  if (m) {{
    const verbMul = {{ double:2.0, triple:3.0, quadruple:4.0, halve:0.5 }};
    const mult = verbMul[m[1]];
    let a = 0, b = 25, label = 'whole 26-week window';
    if (m[2]) {{ a = b = _clamp(parseInt(m[2], 10)); label = `W${{a + 1}}`; }}
    else if (m[3]) {{ const r = _monthRange(m[3]); if (r) {{ a = r[0]; b = r[1]; label = `${{m[3]}} (W${{a + 1}}-W${{b + 1}})`; }} }}
    for (let i = a; i <= b; i++) out[i] = _round(cur[i] * mult);
    return {{
      parsed: true, newForecast: out, type: 'multiplier',
      summary: `${{m[1].charAt(0).toUpperCase() + m[1].slice(1)}} (×${{mult}}) applied to ${{label}}.`,
      deltaTotal: out.reduce((a,b)=>a+b,0) - cur.reduce((a,b)=>a+b,0),
    }};
  }}

  // 2e) Absolute units: "increase by 500 units/wk"
  let signAbs = 1;
  m = lo.match(/(?:increase|boost|add|lift|raise|grow)[^\\d]*(?:by\\s+)?(\\d+(?:,\\d{{3}})*)\\s*(?:units?\\s*\\/?\\s*wk|\\/\\s*wk|per\\s*wk|per\\s*week|units?|u)/);
  if (!m) {{
    m = lo.match(/(?:decrease|cut|drop|lower|reduce|subtract)[^\\d]*(?:by\\s+)?(\\d+(?:,\\d{{3}})*)\\s*(?:units?\\s*\\/?\\s*wk|\\/\\s*wk|per\\s*wk|per\\s*week|units?|u)/);
    if (m) signAbs = -1;
  }}
  if (m) {{
    const incr = parseInt(m[1].replace(/,/g, ''), 10) * signAbs;
    let a = 0, b = 25, label = 'every week';
    const mm = lo.match(new RegExp(`(?:in|through|across|during)\\\\s+` + monthList + `(?:[^a-z]*(?:to|through|until|-)[^a-z]*` + monthList + `)?`));
    if (mm) {{
      const r1 = _monthRange(mm[1]); const r2 = mm[2] ? _monthRange(mm[2]) : null;
      if (r1) {{ a = r1[0]; b = r2 ? r2[1] : r1[1]; label = `${{mm[1]}}${{mm[2] ? '-' + mm[2] : ''}} (W${{a + 1}}-W${{b + 1}})`; }}
    }}
    for (let i = a; i <= b; i++) out[i] = Math.max(0, cur[i] + incr);
    return {{
      parsed: true, newForecast: out, type: 'absolute_units',
      summary: `${{signAbs > 0 ? 'Add' : 'Remove'}} ${{Math.abs(incr).toLocaleString()}} units/wk to ${{label}}.`,
      deltaTotal: out.reduce((a,b)=>a+b,0) - cur.reduce((a,b)=>a+b,0),
    }};
  }}

  // 2f) Whole-period pct
  m = lo.match(/(?:adjust|boost|lift|cut|drop|raise|reduce|increase|decrease|gain|loss|bump|grow)[^\\d]*([+-]?)\\s*(\\d+(?:\\.\\d+)?)\\s*%/);
  if (!m) m = lo.match(/([+-])\\s*(\\d+(?:\\.\\d+)?)\\s*%(?:\\s*(?:across|throughout|all|every|whole))?/);
  if (m) {{
    let sign = m[1] === '-' ? -1 : 1;
    if (/cut|drop|decrease|reduction|down|los|reduce|lower|pull[\\s]*back|trim|slow|soften/.test(lo)) sign = -1;
    if (/lift|boost|bump|gain|increase|up|raise|grow/.test(lo)) sign = 1;
    const pct = parseFloat(m[2]);
    if (pct > 0) {{
      const mult = 1 + sign * (pct / 100);
      for (let i = 0; i < 26; i++) out[i] = _round(cur[i] * mult);
      return {{
        parsed: true, newForecast: out, type: 'pct_whole',
        summary: `${{sign > 0 ? '+' : '-'}}${{pct}}% ${{sign > 0 ? 'lift' : 'cut'}} applied across all 26 weeks (${{cur.reduce((x,y)=>x+y,0).toLocaleString()}}u → ${{out.reduce((x,y)=>x+y,0).toLocaleString()}}u).`,
        deltaTotal: out.reduce((a,b)=>a+b,0) - cur.reduce((a,b)=>a+b,0),
      }};
    }}
  }}

  return {{
    parsed: false,
    summary: "I couldn't translate that into a specific 26-week diff. Examples I can handle: \\"boost June by 30%\\", \\"+25% in May for grooming season\\", \\"EOL by Aug 14\\", \\"double W14\\", \\"add 200 units/wk through October\\", \\"ramp up starting July\\", \\"-15% across the board\\". Save as plain comment instead?",
  }};
}}

function previewAiAdjustment(key) {{
  const safeId = key.replace(/[^a-zA-Z0-9]/g, '_');
  const ta = document.getElementById('ai-adj-text-' + safeId);
  const previewDiv = document.getElementById('ai-adj-preview-' + safeId);
  if (!ta || !previewDiv) return;
  const text = ta.value.trim();
  if (!text) {{
    previewDiv.innerHTML = '<div style="color:#c62828;font-size:11px;">Enter what changed first.</div>';
    return;
  }}
  const rec = ALL_RECORDS.find(x => x.key === key);
  if (!rec) return;
  const cur = rec.ai_forecast || rec.ai_fcst || [];
  const result = _parseAiAdjustment(text, cur);
  if (!result.parsed) {{
    previewDiv.innerHTML =
      `<div style="color:#c62828;font-size:11px;padding:6px 0;">${{result.summary}}</div>` +
      `<button onclick="saveAiCommentOnly('${{key.replace(/'/g, '&#39;')}}')" style="font-size:11px;padding:4px 12px;background:#fff;border:1px solid #888;color:#333;border-radius:4px;cursor:pointer;">Save as plain comment</button>`;
    return;
  }}
  rec._ai_adjust_proposal = result.newForecast;
  rec._ai_adjust_text     = text;
  let tbl = '<table style="font-size:11px;border-collapse:collapse;margin:6px 0;width:100%;">';
  tbl += '<tr style="background:#f5f5f5;"><th style="padding:2px 4px;text-align:left;">Wk</th>';
  for (let i = 0; i < 26; i++) tbl += `<th style="padding:2px 4px;border-bottom:1px solid #ddd;">W${{i + 1}}</th>`;
  tbl += '<th style="padding:2px 4px;background:#fff;">Total</th></tr>';
  tbl += '<tr><td style="padding:2px 4px;color:#555;">Current AI</td>';
  let curTot = 0;
  for (let i = 0; i < 26; i++) {{ curTot += cur[i] || 0; tbl += `<td style="padding:2px 4px;color:#888;text-align:right;">${{(cur[i] || 0).toLocaleString()}}</td>`; }}
  tbl += `<td style="padding:2px 4px;text-align:right;font-weight:600;color:#555;">${{curTot.toLocaleString()}}</td></tr>`;
  tbl += '<tr><td style="padding:2px 4px;color:#1565c0;font-weight:600;">Proposed</td>';
  let newTot = 0;
  for (let i = 0; i < 26; i++) {{
    const v = result.newForecast[i] || 0;
    newTot += v;
    const changed = v !== (cur[i] || 0);
    const bg = changed ? (v > (cur[i] || 0) ? '#e8f5e9' : '#ffebee') : 'transparent';
    const color = changed ? '#1565c0' : '#888';
    tbl += `<td style="padding:2px 4px;background:${{bg}};color:${{color}};text-align:right;font-weight:${{changed ? 700 : 400}};">${{v.toLocaleString()}}</td>`;
  }}
  tbl += `<td style="padding:2px 4px;text-align:right;font-weight:700;color:#1565c0;background:#e3f2fd;">${{newTot.toLocaleString()}}</td></tr>`;
  tbl += '</table>';
  const dt = result.deltaTotal;
  const dtColor = dt > 0 ? '#2e7d32' : dt < 0 ? '#c62828' : '#888';
  const safeKey = key.replace(/'/g, '&#39;');
  previewDiv.innerHTML = `
    <div style="background:#f0f7ff;border:1px solid #1565c0;border-radius:4px;padding:8px;margin-top:6px;">
      <div style="font-size:12px;font-weight:600;color:#1565c0;margin-bottom:4px;">
        🤖 AI's interpretation: ${{result.summary}}
        <span style="margin-left:8px;color:${{dtColor}};">Δ ${{dt > 0 ? '+' : ''}}${{dt.toLocaleString()}}u</span>
      </div>
      <div style="overflow-x:auto;">${{tbl}}</div>
      <div style="display:flex;gap:8px;margin-top:6px;">
        <button onclick="applyAiAdjustment('${{safeKey}}')" style="font-size:11px;padding:5px 14px;background:#1565c0;color:#fff;border:none;border-radius:4px;cursor:pointer;font-weight:600;">Apply to MAN cells</button>
        <button onclick="cancelAiAdjustment('${{safeKey}}')" style="font-size:11px;padding:5px 14px;background:#fff;color:#888;border:1px solid #ccc;border-radius:4px;cursor:pointer;">Cancel</button>
      </div>
    </div>`;
}}

// Calendar-stable encoding (mirror of codepage _encodeAiIntent).  Stores each
// changed week's NEW value tagged with its W1-of-week ISO date.  F58 maps
// each date to the current 26-week horizon at replay time.
function _encodeAiIntent(currentVals, newVals, w1Date) {{
  if (!Array.isArray(newVals) || newVals.length !== 26) return '';
  if (!w1Date) return '';
  const parts = [];
  for (let i = 0; i < 26; i++) {{
    const cv = Math.round(currentVals[i] || 0);
    const nv = Math.round(newVals[i] || 0);
    if (nv === cv) continue;
    const d = new Date(w1Date.getTime());
    d.setDate(d.getDate() + i * 7);
    const iso = `${{d.getFullYear()}}-${{String(d.getMonth() + 1).padStart(2, '0')}}-${{String(d.getDate()).padStart(2, '0')}}`;
    parts.push(`${{iso}}=${{nv}}`);
  }}
  if (!parts.length) return '';
  return `[ai-intent ${{parts.join(' ')}}]`;
}}

async function applyAiAdjustment(key) {{
  const rec = ALL_RECORDS.find(x => x.key === key);
  if (!rec || !rec._ai_adjust_proposal) return;
  const vals = rec._ai_adjust_proposal;
  document.querySelectorAll(`.man-edit[data-key="${{key.replace(/"/g,'\\\\"')}}"]`).forEach(el => {{
    const w = parseInt(el.dataset.week, 10);
    if (w >= 0 && w < 26) _setManCell(el, vals[w] || 0);
  }});
  const intent = _encodeAiIntent(rec.ai_forecast || rec.ai_fcst || [], vals, W1_DATE);
  const noteText = `${{rec._ai_adjust_text}}${{intent ? ' ' + intent : ''}}`;
  const safeId = key.replace(/[^a-zA-Z0-9]/g, '_');
  const previewDiv = document.getElementById('ai-adj-preview-' + safeId);
  // POST to the dedicated AI Comments table — separate from mgr-facing
  // Projection Comments.  [Author] auto-stamps via QB user; [Ignored] starts false.
  try {{
    const res = await fetch('/api/ai-comment-add', {{
      method:  'POST',
      headers: {{'Content-Type':'application/json'}},
      body:    JSON.stringify({{key: key, note: noteText, ignored: false}})
    }});
    const data = await res.json();
    if (!res.ok || data.error) throw new Error(data.error || ('HTTP ' + res.status));
    if (previewDiv) {{
      previewDiv.innerHTML = `<div style="color:#2e7d32;font-size:11px;padding:6px 0;">✓ Staged ${{vals.length}} cells. Review the highlighted yellow cells, then click <b>Save All</b> at the top.</div>`;
    }}
    if (typeof loadCommentHistory === 'function') loadCommentHistory(key, true);
  }} catch (e) {{
    if (previewDiv) {{
      previewDiv.innerHTML = `<div style="color:#c62828;font-size:11px;padding:6px 0;">⚠ Cells staged, but couldn't save AI Comment: ${{e.message}}</div>`;
    }}
  }}
  const ta = document.getElementById('ai-adj-text-' + safeId);
  if (ta) ta.value = '';
  delete rec._ai_adjust_proposal; delete rec._ai_adjust_text;
}}

function cancelAiAdjustment(key) {{
  const safeId = key.replace(/[^a-zA-Z0-9]/g, '_');
  const previewDiv = document.getElementById('ai-adj-preview-' + safeId);
  if (previewDiv) previewDiv.innerHTML = '';
  const rec = ALL_RECORDS.find(x => x.key === key);
  if (rec) {{ delete rec._ai_adjust_proposal; delete rec._ai_adjust_text; }}
}}

async function saveAiCommentOnly(key) {{
  const safeId = key.replace(/[^a-zA-Z0-9]/g, '_');
  const ta = document.getElementById('ai-adj-text-' + safeId);
  const previewDiv = document.getElementById('ai-adj-preview-' + safeId);
  if (!ta || !ta.value.trim()) return;
  // Save to AI Comments with [Ignored]=true since the parser couldn't act on it.
  try {{
    const res = await fetch('/api/ai-comment-add', {{
      method:  'POST',
      headers: {{'Content-Type':'application/json'}},
      body:    JSON.stringify({{key: key, note: ta.value.trim(), ignored: true}})
    }});
    const data = await res.json();
    if (!res.ok || data.error) throw new Error(data.error || ('HTTP ' + res.status));
    ta.value = '';
    if (previewDiv) previewDiv.innerHTML = '<div style="color:#2e7d32;font-size:11px;padding:6px 0;">✓ Saved as comment (marked Ignored — F58 will not auto-apply).</div>';
    if (typeof loadCommentHistory === 'function') loadCommentHistory(key, true);
  }} catch (e) {{
    if (previewDiv) previewDiv.innerHTML = `<div style="color:#c62828;font-size:11px;padding:6px 0;">Failed: ${{e.message}}</div>`;
  }}
}}

// Inline POG date save — fires on every <input type="date"> change.
// POSTs to /api/update-pog which writes [POG Launch Date] (fid 1594) or
// [POG End Date] (fid 1595) on the Projections table.
async function savePogDate(key, which, isoValue, el) {{
  const safeKey = key.replace(/'/g, '&#39;');
  const msg = document.getElementById('pog-msg-' + safeKey);
  const label = which === 'launch' ? 'POG Launch' : 'POG End';
  if (msg) {{ msg.textContent = 'Saving...'; msg.style.color = '#888'; }}
  if (el) el.style.background = '#fff9c4';
  try {{
    const res = await fetch('/api/update-pog', {{
      method:  'POST',
      headers: {{'Content-Type':'application/json'}},
      body:    JSON.stringify({{key: key, which: which, value: isoValue || ''}})
    }});
    const data = await res.json();
    if (!res.ok || data.error) throw new Error(data.error || ('HTTP ' + res.status));
    const rec = ALL_RECORDS.find(x => x.key === key);
    if (rec) {{
      if (which === 'launch') rec.pog_launch = isoValue || '';
      else                    rec.pog_end    = isoValue || '';
    }}
    if (msg) {{ msg.textContent = `✓ ${{label}} saved`; msg.style.color = '#2e7d32'; setTimeout(() => {{ if (msg) msg.textContent = ''; }}, 2500); }}
    if (el) el.style.background = '#e8f5e9';
    setTimeout(() => {{ if (el) el.style.background = '#fff'; }}, 2000);
  }} catch (e) {{
    if (msg) {{ msg.textContent = `Save failed: ${{e.message || e}}`; msg.style.color = '#c62828'; }}
    if (el) el.style.background = '#ffebee';
  }}
}}

// Inline Store Count save — fires on every <input type="number"> change.
// POSTs to /api/update-store-count which writes [Store Count] (fid 14) on Projections.
async function saveStoreCount(key, rawValue, el) {{
  const safeKey = key.replace(/'/g, '&#39;');
  const msg = document.getElementById('pog-msg-' + safeKey);
  const trimmed = String(rawValue || '').trim();
  const value = trimmed === '' ? null : Math.max(0, parseInt(trimmed, 10) || 0);
  if (msg) {{ msg.textContent = 'Saving...'; msg.style.color = '#888'; }}
  if (el) el.style.background = '#fff9c4';
  try {{
    const res = await fetch('/api/update-store-count', {{
      method:  'POST',
      headers: {{'Content-Type':'application/json'}},
      body:    JSON.stringify({{key: key, value: value}})
    }});
    const data = await res.json();
    if (!res.ok || data.error) throw new Error(data.error || ('HTTP ' + res.status));
    const rec = ALL_RECORDS.find(x => x.key === key);
    if (rec) rec.store_count = value || 0;
    if (msg) {{ msg.textContent = `✓ Store count saved`; msg.style.color = '#2e7d32'; setTimeout(() => {{ if (msg) msg.textContent = ''; }}, 2500); }}
    if (el) el.style.background = '#e8f5e9';
    setTimeout(() => {{ if (el) el.style.background = '#fff'; }}, 2000);
  }} catch (e) {{
    if (msg) {{ msg.textContent = `Save failed: ${{e.message || e}}`; msg.style.color = '#c62828'; }}
    if (el) el.style.background = '#ffebee';
  }}
}}

// Auto-flag on first comment keystroke; auto-unflag when box is cleared.
function autoFlagOnComment(key) {{
  const rec = ALL_RECORDS.find(x => x.key === key);
  if (!rec) return;
  const txt = document.getElementById('cmt-text-' + key);
  const isEmpty = !txt || !txt.value.trim();
  if (isEmpty) {{
    // Comment cleared — undo the auto-flag if we set it (don't touch manual flags)
    if (rec._auto_flagged && rec.flagged) {{
      rec._auto_flagged = false;
      toggleFlag(key);
    }}
    return;
  }}
  if (rec.flagged) return;
  if (rec._auto_flagged) return;
  rec._auto_flagged = true;
  toggleFlag(key);
}}

async function toggleFlag(key) {{
  const rec = ALL_RECORDS.find(x => x.key === key);
  const newVal = !(rec && rec.flagged);
  // Optimistic UI
  if (rec) rec.flagged = newVal;
  if (!userData[key]) userData[key] = {{}};
  if (newVal) userData[key].flagged = true; else delete userData[key].flagged;
  const safeId = key.replace(/[^a-zA-Z0-9]/g,'_');
  const btn = document.getElementById('flg-' + safeId);
  if (btn) btn.className = 'flag-btn' + (newVal ? ' flagged' : '');
  saveUserData();
  // Persist to QB
  try {{
    const res = await fetch('/api/toggle-flag', {{
      method: 'POST',
      headers: {{'Content-Type':'application/json'}},
      body: JSON.stringify({{key: key, flagged: newVal}}),
    }});
    if (!res.ok) throw new Error('HTTP ' + res.status);
    const data = await res.json();
    if (data.error) throw new Error(data.error);
  }} catch (e) {{
    // Revert
    if (rec) rec.flagged = !newVal;
    if (!newVal) userData[key].flagged = true; else delete userData[key].flagged;
    if (btn) {{
      btn.className = 'flag-btn' + (!newVal ? ' flagged' : '');
      btn.title = 'Save failed: ' + e.message;
    }}
    saveUserData();
    console.error('toggleFlag failed:', e);
  }}
}}

function saveComment(key, val) {{
  if (!userData[key]) userData[key] = {{}};
  userData[key].comment = val;
  saveUserData();
}}

function updateFlagCount() {{
  const n = Object.values(userData).filter(d => d.flagged).length;
  const el = document.getElementById('flagCount');
  if (el) el.textContent = n + ' flagged for manager';
}}

function clearFlaggedKeys(keys) {{
  keys.forEach(key => {{
    if (userData[key]) {{
      delete userData[key].flagged;
      delete userData[key].comment;
      if (Object.keys(userData[key]).length === 0) delete userData[key];
    }}
    // Update UI
    const safeId = key.replace(/[^a-zA-Z0-9]/g,'_');
    const btn = document.getElementById('flg-' + safeId);
    if (btn) btn.className = 'flag-btn';
    const cmt = document.querySelector(`textarea[onblur*="${{key}}"]`);
    if (cmt) cmt.value = '';
  }});
  saveUserData();
}}

function clearAllFlags() {{
  // Count what would be cleared
  const flaggedKeys  = Object.keys(userData).filter(k => userData[k].flagged);
  const commentKeys  = Object.keys(userData).filter(k => userData[k].comment);
  const allKeys      = Array.from(new Set([...flaggedKeys, ...commentKeys]));
  if (allKeys.length === 0) {{
    alert('Nothing to clear - no flags or comments are set.');
    return;
  }}
  const msg =
    `Clear ALL flags and comments?\\n\\n` +
    `  • ${{flaggedKeys.length}} flagged record(s)\\n` +
    `  • ${{commentKeys.length}} comment(s)\\n\\n` +
    `This cannot be undone. Continue?`;
  if (!confirm(msg)) return;
  clearFlaggedKeys(allKeys);
  updateFlagCount();
  const status = document.getElementById('sendStatus');
  if (status) {{
    status.style.color = '#2e7d32';
    status.textContent = `✓ Cleared ${{allKeys.length}} record(s)`;
  }}
}}

function exportFlagged() {{
  // Build header with summary columns + 26-week detail columns (5 series × 26
  // weeks each = 130 weekly columns).  Mirrors the data shown in the
  // expandable detail pane so the flagged-export captures everything you'd see
  // by clicking through the table.
  const wkLabels = (prefix) => Array.from({{length:26}}, (_, i) => `${{prefix}} W${{i+1}}`);
  // Pad/truncate any list to exactly 26 entries so CSV columns line up even
  // when a record has missing detail data.
  const pad26 = (arr) => {{
    const a = Array.isArray(arr) ? arr.slice(0, 26) : [];
    while (a.length < 26) a.push(0);
    return a.map(v => Math.round(Number(v) || 0));
  }};
  // Manual W1..W26 lives in weeks_slim[i].projection.
  const manFromWeeks = (wks) => {{
    const out = new Array(26).fill(0);
    if (Array.isArray(wks)) {{
      wks.forEach(w => {{
        const idx = (w && w.week ? w.week : 0) - 1;
        if (idx >= 0 && idx < 26) out[idx] = Math.round(Number(w.projection) || 0);
      }});
    }}
    return out;
  }};
  const header = [
    'Key','Inv Manager','Brand','Customer','Mstyle','Description','Priority',
    'Ord/Wk L4W','Ord/Wk L13W','Shpd/Wk L13W','Proj/Wk','AI Fcst/Wk','Sugg /Wk','AI vs Proj %','AI vs L13 %','Man vs L13 %',
    'Flags','Max Sev','Proj 26w','AI 26w','Sugg 26w','Model','Pattern','Biweekly',
    'Last Comment','Last Comment Date','Narrative',
    ...wkLabels('AI'),
    ...wkLabels('Man'),
    ...wkLabels('Sugg'),
    ...wkLabels('Hist Ord'),
    ...wkLabels('Hist Shp'),
    'Comment','Flagged'
  ];
  const rows = [header];
  ALL_RECORDS.forEach(r => {{
    const ud = userData[r.key] || {{}};
    if (!ud.flagged) return;
    const pct  = (r.proj_total > 0)
                 ? ((r.ai_total - r.proj_total) / r.proj_total * 100).toFixed(1) + '%'
                 : '0%';
    const ai   = pad26(r.ai_fcst);
    const man  = manFromWeeks(r.weeks_slim);
    const sugg = pad26(r.suggested);
    const hOrd = pad26(r.hist_ord);
    const hShp = pad26(r.hist_shp);
    const aiVsL13Str  = (r.ai_vs_l13  != null) ? Number(r.ai_vs_l13).toFixed(1) + '%'  : '';
    const manVsL13Str = (r.man_vs_l13 != null) ? Number(r.man_vs_l13).toFixed(1) + '%' : '';
    rows.push([
      r.key,
      r.inv_manager || '',
      r.brand || '',
      r.cust || '',
      r.mstyle || '',
      r.desc || '',
      r.priority || '',
      Math.round(r.ord_wk_l4 || 0),
      Math.round(r.shp_wk  || 0),
      Math.round(r.shpd_wk || 0),
      Math.round(r.proj_wk || 0),
      Math.round(r.ai_wk   || 0),
      Math.round(r.sugg_wk || 0),
      pct,
      aiVsL13Str,
      manVsL13Str,
      r.n_flags  || 0,
      r.max_sev  || '',
      Math.round(r.proj_total || 0),
      Math.round(r.ai_total   || 0),
      Math.round(r.sugg_total || 0),
      r.ai_model || '',
      r.pattern  || '',
      r.biweekly ? 'Y' : '',
      (r.last_comment || '').replace(/[\\r\\n]+/g,' '),
      r.last_comment_date || '',
      (r.narrative || '').replace(/[\\r\\n]+/g,' '),
      ...ai,
      ...man,
      ...sugg,
      ...hOrd,
      ...hShp,
      (ud.comment || '').replace(/[\\r\\n]+/g,' '),
      ud.flagged ? 'Y' : ''
    ]);
  }});
  if (rows.length < 2) {{ alert('No records flagged yet.'); return; }}
  // Quote every field, escape embedded quotes, normalize newlines (matches
  // exportAllInView format so both CSVs open cleanly in Excel).
  const csv = rows.map(r => r.map(c => {{
    const s = String(c == null ? '' : c).replace(/"/g, '""');
    return '"' + s + '"';
  }}).join(',')).join('\\r\\n');
  const blob = new Blob(['\\uFEFF' + csv], {{type:'text/csv;charset=utf-8'}});
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = 'flagged_for_manager.csv';
  a.click();
}}

// Export every record currently visible in the table (after filters/search)
// to CSV. Honors FILTERED_RECORDS so what you see is what you get.
function exportAllInView() {{
  if (!FILTERED_RECORDS || FILTERED_RECORDS.length === 0) {{
    alert('No records in current view to export.');
    return;
  }}
  // Build header with summary columns + 26-week detail columns (5 series × 26
  // weeks each = 130 weekly columns).  This mirrors the data shown in the
  // expandable detail pane so a single CSV captures everything you'd see by
  // clicking through the table.
  const wkLabels = (prefix) => Array.from({{length:26}}, (_, i) => `${{prefix}} W${{i+1}}`);
  const header = [
    'Key','Inv Manager','Brand','Customer','Mstyle','Description','Priority',
    'Ord/Wk L4W','Ord/Wk L13W','Shpd/Wk L13W','Proj/Wk','AI Fcst/Wk','Sugg /Wk','AI vs Proj %','AI vs L13 %','Man vs L13 %',
    'Flags','Max Sev','Proj 26w','AI 26w','Sugg 26w','Model','Pattern','Biweekly',
    'Last Comment','Last Comment Date','Narrative',
    ...wkLabels('AI'),
    ...wkLabels('Man'),
    ...wkLabels('Sugg'),
    ...wkLabels('Hist Ord'),
    ...wkLabels('Hist Shp'),
    'Comment','Flagged'
  ];
  // Pad/truncate any list to exactly 26 entries so CSV columns line up even
  // when a record has missing detail data.
  const pad26 = (arr) => {{
    const a = Array.isArray(arr) ? arr.slice(0, 26) : [];
    while (a.length < 26) a.push(0);
    return a.map(v => Math.round(Number(v) || 0));
  }};
  // Manual W1..W26 lives in weeks_slim[i].projection.
  const manFromWeeks = (wks) => {{
    const out = new Array(26).fill(0);
    if (Array.isArray(wks)) {{
      wks.forEach(w => {{
        const idx = (w && w.week ? w.week : 0) - 1;
        if (idx >= 0 && idx < 26) out[idx] = Math.round(Number(w.projection) || 0);
      }});
    }}
    return out;
  }};
  const rows = [header];
  FILTERED_RECORDS.forEach(r => {{
    const ud   = userData[r.key] || {{}};
    const pct  = (r.proj_total > 0)
                 ? ((r.ai_total - r.proj_total) / r.proj_total * 100).toFixed(1) + '%'
                 : '0%';
    const ai   = pad26(r.ai_fcst);
    const man  = manFromWeeks(r.weeks_slim);
    const sugg = pad26(r.suggested);
    const hOrd = pad26(r.hist_ord);
    const hShp = pad26(r.hist_shp);
    const aiVsL13Str  = (r.ai_vs_l13  != null) ? Number(r.ai_vs_l13).toFixed(1) + '%'  : '';
    const manVsL13Str = (r.man_vs_l13 != null) ? Number(r.man_vs_l13).toFixed(1) + '%' : '';
    rows.push([
      r.key,
      r.inv_manager || '',
      r.brand || '',
      r.cust || '',
      r.mstyle || '',
      r.desc || '',
      r.priority || '',
      Math.round(r.ord_wk_l4 || 0),
      Math.round(r.shp_wk  || 0),
      Math.round(r.shpd_wk || 0),
      Math.round(r.proj_wk || 0),
      Math.round(r.ai_wk   || 0),
      Math.round(r.sugg_wk || 0),
      pct,
      aiVsL13Str,
      manVsL13Str,
      r.n_flags  || 0,
      r.max_sev  || '',
      Math.round(r.proj_total || 0),
      Math.round(r.ai_total   || 0),
      Math.round(r.sugg_total || 0),
      r.ai_model || '',
      r.pattern  || '',
      r.biweekly ? 'Y' : '',
      (r.last_comment || '').replace(/[\\r\\n]+/g,' '),
      r.last_comment_date || '',
      (r.narrative || '').replace(/[\\r\\n]+/g,' '),
      ...ai,
      ...man,
      ...sugg,
      ...hOrd,
      ...hShp,
      (ud.comment || '').replace(/[\\r\\n]+/g,' '),
      ud.flagged ? 'Y' : ''
    ]);
  }});
  // Quote every field, escape embedded quotes, normalize newlines
  const csv = rows.map(r => r.map(c => {{
    const s = String(c == null ? '' : c).replace(/"/g, '""');
    return '"' + s + '"';
  }}).join(',')).join('\\r\\n');
  // Prepend UTF-8 BOM so Excel opens special characters cleanly
  const blob = new Blob(['\\uFEFF' + csv], {{type:'text/csv;charset=utf-8'}});
  const stamp = new Date().toISOString().slice(0,10);
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = `forecast_view_${{stamp}}.csv`;
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}}

function priLabel(p) {{
  if (p === 'CRITICAL') return '<span class="pri-crit">CRITICAL</span>';
  if (p === 'MEDIUM')  return '<span class="pri-med">MEDIUM</span>';
  return '<span class="pri-low">LOW</span>';
}}

async function copyToMan(key, source, btn) {{
  const label = source === 'ai' ? 'AI PRJ' : 'Suggested';
  if (!confirm(`Overwrite 26 weeks of MAN projections with ${{label}} for ${{key}}?\\n\\nThis writes to Quickbase immediately.`)) return;
  const orig = btn.textContent;
  btn.disabled = true;
  btn.textContent = '…';
  btn.classList.remove('done', 'failed');
  try {{
    const endpoint = source === 'ai' ? '/api/use-ai' : '/api/use-suggested';
    const res = await fetch(endpoint, {{
      method: 'POST',
      headers: {{'Content-Type':'application/json'}},
      body: JSON.stringify({{key}}),
    }});
    const data = await res.json();
    if (data.ok) {{
      btn.classList.add('done');
      btn.textContent = 'Done ✓';
      setTimeout(() => {{ btn.classList.remove('done'); btn.textContent = orig; btn.disabled = false; }}, 2500);
    }} else {{
      btn.classList.add('failed');
      btn.textContent = 'Fail';
      setTimeout(() => {{ btn.classList.remove('failed'); btn.textContent = orig; btn.disabled = false; }}, 3000);
    }}
  }} catch (e) {{
    btn.classList.add('failed');
    btn.textContent = 'Err';
    btn.title = 'Error: ' + e;
    setTimeout(() => {{ btn.classList.remove('failed'); btn.textContent = orig; btn.disabled = false; }}, 3000);
  }}
}}

// ── Pagination state ────────────────────────────────────────────────────────
let FILTERED_RECORDS = ALL_RECORDS.slice();
let currentPage = 0;
const PAGE_SIZE  = 100;

function renderPage(page) {{
  currentPage = page;
  const start  = page * PAGE_SIZE;
  const end    = Math.min(start + PAGE_SIZE, FILTERED_RECORDS.length);
  const pageRecs = FILTERED_RECORDS.slice(start, end);
  const tb = document.getElementById('tbody');
  tb.innerHTML = '';
  pageRecs.forEach(r => {{
    const ud = userData[r.key] || {{}};
    const safeId = r.key.replace(/[^a-zA-Z0-9]/g,'_');
    const tr = document.createElement('tr');
    tr.className = borderClass(r.max_sev);
    tr.dataset.key = r.key;
    tr.dataset.sev = r.max_sev;
    tr.dataset.pri = r.priority;
    tr.dataset.vol = r.vol_tier;
    tr.dataset.pat = r.pattern;
    tr.dataset.flags = r.n_flags;

    const bwTag = r.biweekly ? '<span class="tag tag-bw">BW</span>' : '';
    const aiVsProj = r.proj_total > 0 ? ((r.ai_total - r.proj_total) / r.proj_total * 100) : 0;
    tr.dataset.aidiff = Math.abs(aiVsProj).toFixed(1);
    const aiVsShpd = r.shpd_wk > 0 ? ((r.ai_wk - r.shpd_wk) / r.shpd_wk * 100) : 0;
    const flagCls = ud.flagged ? 'flag-btn flagged' : 'flag-btn';
    const cmt = (ud.comment || '').replace(/"/g,'&quot;');

    const aiVsL13  = (r.ai_vs_l13  == null ? 0 : r.ai_vs_l13);
    const manVsL13 = (r.man_vs_l13 == null ? 0 : r.man_vs_l13);
    const l13Avail = r.shp_wk > 0;
    tr.innerHTML = `
      <td></td>
      <td class="clickable" onclick="toggleDetail('${{r.key}}')">${{r.key}}${{bwTag}}</td>
      <td style="font-size:11px;white-space:nowrap">${{r.inv_manager||''}}</td>
      <td style="font-size:11px;white-space:nowrap">${{r.brand||''}}</td>
      <td class="clickable" onclick="toggleDetail('${{r.key}}')">${{r.cust}}</td>
      <td>${{r.mstyle}}</td>
      <td style="font-size:11px;max-width:180px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${{r.desc||''}}">${{r.desc||''}}</td>
      ${{_renderStatusCell(r.asin_status, r.key)}}
      <td style="font-size:11px;white-space:nowrap">${{r.item_status||''}}</td>
      <td>${{priLabel(r.priority)}}</td>
      <td>${{fmtN(Math.round(r.ord_wk_l4 || 0))}}</td>
      <td>${{fmtN(Math.round(r.shp_wk))}}</td>
      <td>${{fmtN(Math.round(r.proj_wk))}}</td>
      <td style="color:#1565c0;font-weight:600">${{fmtN(Math.round(r.ai_wk))}}</td>
      <td style="color:#555" title="Average of Suggested W1..W26">${{fmtN(Math.round(r.sugg_wk || 0))}}</td>
      <td style="font-size:14px;font-weight:800;color:${{aiVsProj > 0 ? '#2e7d32' : aiVsProj < 0 ? '#c62828' : '#888'}}">${{aiVsProj >= 0 ? '+' : ''}}${{aiVsProj.toFixed(1)}}%</td>
      <td style="font-size:13px;font-weight:700;color:${{!l13Avail ? '#888' : (aiVsL13 > 0 ? '#2e7d32' : aiVsL13 < 0 ? '#c62828' : '#888')}}">${{l13Avail ? (aiVsL13 >= 0 ? '+' : '') + aiVsL13.toFixed(1) + '%' : '—'}}</td>
      <td style="font-size:13px;font-weight:700;color:${{!l13Avail ? '#888' : (manVsL13 > 0 ? '#2e7d32' : manVsL13 < 0 ? '#c62828' : '#888')}}">${{l13Avail ? (manVsL13 >= 0 ? '+' : '') + manVsL13.toFixed(1) + '%' : '—'}}</td>
      <td style="text-align:center"><button class="use-btn use-ai" onclick="event.stopPropagation();copyToMan('${{r.key}}','ai',this)" title="Overwrite MAN weeks with AI PRJ">Use AI</button></td>
      <td style="text-align:center"><button class="use-btn use-sug" onclick="event.stopPropagation();copyToMan('${{r.key}}','suggested',this)" title="Overwrite MAN weeks with Suggested PRJ">Use Sugg</button></td>
    `;
    tb.appendChild(tr);

    // Detail row (pre-populated from inline data — no fetch needed)
    const dtr = document.createElement('tr');
    dtr.className = 'detail-pane';
    dtr.id = 'detail-' + r.key;
    dtr.dataset.loaded = '0';
    dtr.innerHTML = `<td colspan="20"></td>`;
    tb.appendChild(dtr);
  }});
  updatePageNav();
  updateFlagCount();
}}

function renderTable() {{ renderPage(0); }}

function updatePageNav() {{
  const n = FILTERED_RECORDS.length;
  const totalPages = Math.max(1, Math.ceil(n / PAGE_SIZE));
  const navEl = document.getElementById('pageNav');
  if (navEl) {{
    navEl.style.display = totalPages > 1 ? 'flex' : 'none';
    document.getElementById('pageInfo').textContent =
      `Page ${{currentPage + 1}} of ${{totalPages}} (${{n.toLocaleString()}} records)`;
    document.getElementById('prevBtn').disabled = currentPage === 0;
    document.getElementById('nextBtn').disabled = currentPage >= totalPages - 1;
  }}
  document.getElementById('statLine').textContent = n.toLocaleString() + ' records shown';
}}

function changePage(delta) {{
  const totalPages = Math.max(1, Math.ceil(FILTERED_RECORDS.length / PAGE_SIZE));
  const newPage = Math.max(0, Math.min(currentPage + delta, totalPages - 1));
  if (newPage !== currentPage) renderPage(newPage);
}}

function toggleDetail(key) {{
  const el = document.getElementById('detail-' + key);
  if (!el) return;
  if (el.style.display === 'table-row') {{
    el.style.display = 'none';
    return;
  }}
  el.style.display = 'table-row';
  if (el.dataset.loaded === '1') return;

  // All detail data is preloaded in ALL_RECORDS — zero network round-trip
  const r      = ALL_RECORDS.find(x => x.key === key) || {{}};
  const wks    = r.weeks_slim || [];   // {{week, projection, severity}}
  const aiFcst = r.ai_fcst   || [];
  const aiMdl  = r.ai_model  || '';
  const sug    = r.suggested  || [];

  // LY actuals — weeks 27-52 ago, aligned to W1..W26.  Rendered as two
  // additional rows under "Suggested": Ordered LY (green) and Shipped LY
  // (blue).  Empty arrays render zeros gracefully when forecast_results.json
  // predates this field (re-run forecast to populate).
  const lyOrd = r.ly_ord || [];
  const lyShp = r.ly_shp || [];

  let hdrCells  = '<th class="row-label"></th>';
  let projCells = '<td class="row-label">Projection</td>';
  let aiCells   = `<td class="row-label" style="color:#1565c0;font-weight:600">AI Forecast<br><span style="font-weight:normal;font-size:10px">${{aiMdl}}</span></td>`;
  let sugCells  = '<td class="row-label" style="color:#555">Suggested</td>';
  let opnCells  = '<td class="row-label" style="color:#6d4c00;font-weight:600">Open Customer POs</td>';
  let lyOrdCells = '<td class="row-label" style="color:#2e7d32;font-weight:600">Ordered LY</td>';
  let lyShpCells = '<td class="row-label" style="color:#1565c0;font-weight:600">Shipped LY</td>';
  let sugTot = 0, opnTot = 0, lyOrdTot = 0, lyShpTot = 0;

  for (let i = 0; i < wks.length; i++) {{
    const w   = wks[i];
    const lbl = weekLabel(i);
    hdrCells  += `<th>W${{w.week}}<br><span style="font-weight:normal;font-size:10px">${{lbl}}</span></th>`;
    const cls  = weekCellClass(w.severity);
    projCells  += `<td class="${{cls}}">${{fmtN(w.projection)}}</td>`;
    const aiVal  = aiFcst[i] || 0;
    const aiDiff = aiVal - w.projection;
    const aiCls  = aiDiff > 0 ? 'color:#2e7d32' : aiDiff < 0 ? 'color:#c62828' : 'color:#888';
    aiCells   += `<td style="${{aiCls}};font-weight:600">${{fmtN(aiVal)}}</td>`;
    const sugVal = sug[i] || 0;
    sugTot    += sugVal;
    sugCells  += `<td style="color:#555;font-size:10px">${{fmtN(sugVal)}}</td>`;
    const opnVal = (r.opn_w || [])[i] || 0;
    opnTot    += opnVal;
    opnCells  += `<td style="${{opnVal === 0 ? 'color:#bbb' : 'color:#6d4c00;font-weight:600'}};font-size:10px">${{fmtN(opnVal)}}</td>`;
    const lyOrdVal = lyOrd[i] || 0;
    lyOrdTot += lyOrdVal;
    lyOrdCells += `<td style="${{lyOrdVal === 0 ? 'color:#bbb' : 'color:#2e7d32'}};font-size:10px">${{fmtN(lyOrdVal)}}</td>`;
    const lyShpVal = lyShp[i] || 0;
    lyShpTot += lyShpVal;
    lyShpCells += `<td style="${{lyShpVal === 0 ? 'color:#bbb' : 'color:#1565c0'}};font-size:10px">${{fmtN(lyShpVal)}}</td>`;
  }}

  hdrCells  += '<th>Total</th>';
  projCells += `<td style="font-weight:700">${{fmtN(r.proj_total)}}</td>`;
  aiCells   += `<td style="font-weight:700;color:#1565c0">${{fmtN(r.ai_total)}}</td>`;
  sugCells  += `<td style="font-weight:700;color:#555">${{fmtN(sugTot)}}</td>`;
  opnCells  += `<td style="font-weight:700;color:#6d4c00">${{fmtN(opnTot)}}</td>`;
  lyOrdCells += `<td style="font-weight:700;color:#2e7d32">${{fmtN(lyOrdTot)}}</td>`;
  lyShpCells += `<td style="font-weight:700;color:#1565c0">${{fmtN(lyShpTot)}}</td>`;

  // ── Inventory Flow section ──────────────────────────────────────────────
  const _beg = r.inv_flow_beg || null;
  const _rcv = r.inv_flow_rcv || null;
  const _prj = r.inv_flow_prj || null;
  const _hasInvFlow = !!(_beg || _rcv || _prj);
  const _invFmt1 = n => {{
    if (n == null || !Number.isFinite(n)) return '—';
    return n.toLocaleString('en-US', {{ minimumFractionDigits: 1, maximumFractionDigits: 1 }});
  }};
  const _mpForLow = r.master_pack || r.mp || 1;
  const _lowThresh = _mpForLow * 2;

  // ── Gap analysis pre-compute ────────────────────────────────────────────
  // Only runs for Replen items.  Non-Replen statuses (ISO, phase-out, etc.)
  // skip — replenishment alerts don't apply to those items.
  const _optWos   = Number(r.inv_flow_opt_wos || 0);
  const _nextRcpt = r.inv_flow_next_rcpt || '';
  const _isReplen = /\breplen\b/i.test(String(r.item_status || ''));
  const _gap = {{ weeks: [], nextRcptWeekIdx: -1, nextRcptDate: null }};
  if (_isReplen && _optWos > 0 && _beg && _prj && typeof _W1_DATE !== 'undefined' && _W1_DATE) {{
    let nrIdx = 25;
    if (_nextRcpt) {{
      const nrDate = new Date(_nextRcpt);
      if (!isNaN(nrDate.getTime())) {{
        _gap.nextRcptDate = nrDate;
        const daysFromW1 = Math.floor((nrDate.getTime() - _W1_DATE.getTime()) / 86400000);
        nrIdx = Math.floor(daysFromW1 / 7);
      }}
    }}
    _gap.nextRcptWeekIdx = nrIdx;
    const checkUntil = (nrIdx < 0) ? -1 : Math.min(25, nrIdx);
    for (let i = 0; i <= checkUntil; i++) {{
      const bv = _beg[i], pv = _prj[i];
      if (pv > 0) {{
        const wos = bv / pv;
        if (wos < _optWos) {{
          _gap.weeks.push({{ wi: i + 1, wos: wos, deficit: _optWos - wos }});
        }}
      }}
    }}
  }}

  let invFlowSectionHtml = '';
  {{
    let begCells = `<td class="row-label" style="color:#6d4c00;font-weight:600;background:#fffbea" title="Beginning-of-week projected warehouse inventory">Beg Inv</td>`;
    let rcvCells = `<td class="row-label" style="color:#1565c0;font-weight:600;background:#f0f7ff" title="Expected supplier receipts that week">Expected Receipts</td>`;
    let wosCells = `<td class="row-label" style="color:#4a148c;font-weight:600;background:#f8f0fb" title="Weeks of Supply Onhand = Beg Inv / Prj demand">WOS OH</td>`;
    let begTot = 0, rcvTot = 0;
    for (let i = 0; i < 26; i++) {{
      if (_beg) {{
        const bv = _beg[i];
        begTot += bv;
        const aiThisWk = aiFcst[i] || 0;
        let color = '#6d4c00';
        if (bv < 0)                                       color = '#c62828';
        else if (bv === 0 && aiThisWk > 0)                color = '#c62828';
        else if (bv > 0 && bv < _lowThresh && aiThisWk > 0) color = '#e65100';
        else if (bv === 0)                                color = '#bbb';
        begCells += `<td style="color:${{color}};font-size:10px;background:#fffbea">${{fmtN(bv)}}</td>`;
      }} else {{
        begCells += `<td style="color:#bbb;font-size:10px;background:#fffbea">—</td>`;
      }}
      if (_rcv) {{
        const rv = _rcv[i];
        rcvTot += rv;
        const color = rv > 0 ? '#1565c0' : '#bbb';
        rcvCells += `<td style="color:${{color}};font-size:10px;background:#f0f7ff">${{rv > 0 ? fmtN(rv) : '&mdash;'}}</td>`;
      }} else {{
        rcvCells += `<td style="color:#bbb;font-size:10px;background:#f0f7ff">—</td>`;
      }}
      if (_beg && _prj) {{
        const bv = _beg[i];
        const pv = _prj[i];
        let wosTxt, wosColor;
        let cellBg = '#f8f0fb';
        if (pv > 0) {{
          const wos = bv / pv;
          wosTxt = _invFmt1(wos);
          if (wos < 1)        wosColor = '#c62828';
          else if (wos < 4)   wosColor = '#e65100';
          else if (wos > 26)  wosColor = '#1b5e20';
          else                wosColor = '#4a148c';
        }} else if (bv > 0) {{
          wosTxt = '∞'; wosColor = '#1b5e20';
        }} else {{
          wosTxt = '—'; wosColor = '#bbb';
        }}
        const isGapWeek = _optWos > 0
          && _gap.nextRcptWeekIdx >= 0
          && i <= Math.min(25, _gap.nextRcptWeekIdx)
          && pv > 0
          && (bv / pv) < _optWos;
        if (isGapWeek) {{ cellBg = '#ffebee'; wosColor = '#c62828'; }}
        const bold = isGapWeek || (pv > 0 && bv / pv < 4) ? 700 : 400;
        const tip = isGapWeek ? ` title="Gap: WOS ${{_invFmt1(bv/pv)}} < Opt WOS ${{_invFmt1(_optWos)}}"` : '';
        wosCells += `<td style="color:${{wosColor}};font-size:10px;background:${{cellBg}};font-weight:${{bold}}"${{tip}}>${{wosTxt}}</td>`;
      }} else {{
        wosCells += `<td style="color:#bbb;font-size:10px;background:#f8f0fb">—</td>`;
      }}
    }}
    begCells += _beg ? `<td style="font-weight:700;color:#6d4c00;background:#fffbea">${{fmtN(begTot)}}</td>` : `<td style="color:#bbb;background:#fffbea">—</td>`;
    rcvCells += _rcv ? `<td style="font-weight:700;color:#1565c0;background:#f0f7ff">${{fmtN(rcvTot)}}</td>` : `<td style="color:#bbb;background:#f0f7ff">—</td>`;
    wosCells += `<td style="color:#bbb;background:#f8f0fb" title="WOS total is not meaningful">—</td>`;

    // Gap banner — Replen items only
    let gapBannerHtml = '';
    if (_hasInvFlow && _optWos > 0 && _isReplen) {{
      const optWosStr = _invFmt1(_optWos);
      const nextRcptStr = _gap.nextRcptDate
        ? _gap.nextRcptDate.toLocaleDateString('en-US', {{ month:'short', day:'numeric', year:'numeric' }})
        : 'unknown';
      const nextRcptWk = (_gap.nextRcptWeekIdx >= 0 && _gap.nextRcptWeekIdx <= 25)
        ? `(W${{_gap.nextRcptWeekIdx + 1}})`
        : _gap.nextRcptWeekIdx > 25 ? '(beyond W26)' : '';
      if (_gap.weeks.length === 0) {{
        gapBannerHtml = `
          <div style="margin-top:6px;padding:6px 10px;background:#e8f5e9;border:1px solid #a5d6a7;border-radius:4px;font-size:11px;color:#1b5e20;">
            ✓ <b>No gaps:</b> all weeks through next receipt ${{nextRcptStr}} ${{nextRcptWk}} maintain ≥ ${{optWosStr}} WOS (Opt WOS).
          </div>`;
      }} else {{
        const mstyle = encodeURIComponent(r.mstyle || '');
        const invMgmtUrl = `https://pim.quickbase.com/db/bpsaju5pm?a=q&query=%7B20.EX.'${{mstyle}}'%7D`;
        gapBannerHtml = `
          <div style="margin-top:6px;padding:6px 10px;background:#ffebee;border:1px solid #ef9a9a;border-radius:4px;font-size:11px;color:#b71c1c;">
            &#x26a0;&#xfe0f; <b>Inventory Gap:</b> ${{_gap.weeks.length}} week${{_gap.weeks.length === 1 ? '' : 's'}} below Opt WOS (${{optWosStr}})
            before next receipt ${{nextRcptStr}} ${{nextRcptWk}}.
            Moving up open POs may close this gap &mdash;
            <a href="${{invMgmtUrl}}" target="_blank" style="color:#b71c1c;font-weight:600;">View in Inventory Manager &rarr;</a>
          </div>`;
      }}
    }} else if (_hasInvFlow && !_isReplen) {{
      const escStatus = String(r.item_status || 'unknown').replace(/[<>&]/g, c => ({{'<':'&lt;','>':'&gt;','&':'&amp;'}}[c]));
      gapBannerHtml = `
        <div style="margin-top:6px;padding:4px 10px;background:#fafafa;border:1px solid #e0e0e0;border-radius:4px;font-size:10px;color:#888;font-style:italic;">
          Gap analysis only runs on Replen items (PT Item Status: ${{escStatus}}).
        </div>`;
    }} else if (_hasInvFlow && _optWos <= 0) {{
      gapBannerHtml = `
        <div style="margin-top:6px;padding:4px 10px;background:#fafafa;border:1px solid #e0e0e0;border-radius:4px;font-size:10px;color:#888;font-style:italic;">
          Gap analysis disabled — no Opt WOS set for this mstyle in Inventory Flow.
        </div>`;
    }}

    invFlowSectionHtml = `
      <div style="margin:12px 12px 0 12px;">
        <div style="font-weight:700;font-size:12px;color:#333;margin-bottom:4px;padding-left:2px;display:flex;align-items:center;gap:12px;flex-wrap:wrap;">
          <span>📦 Inventory Flow</span>
          ${{_hasInvFlow && _optWos > 0 ? `<span style="font-weight:400;font-size:10px;color:#555;">Opt WOS: <b>${{_invFmt1(_optWos)}}</b></span>` : ''}}
          ${{_hasInvFlow && _gap.nextRcptDate ? `<span style="font-weight:400;font-size:10px;color:#555;">Next Receipt: <b>${{_gap.nextRcptDate.toLocaleDateString('en-US', {{ month:'short', day:'numeric' }})}}</b></span>` : ''}}
          ${{_hasInvFlow ? '' : '<span style="font-weight:400;font-size:10px;color:#888;">(no QB Inventory Flow row for this mstyle)</span>'}}
        </div>
        <div style="overflow-x:auto;">
          <table class="dtbl">
            <tr>${{hdrCells}}</tr>
            <tr>${{begCells}}</tr>
            <tr>${{rcvCells}}</tr>
            <tr>${{wosCells}}</tr>
          </table>
        </div>
        ${{gapBannerHtml}}
      </div>`;
  }}

  // POG / ISO Inventory Plan block (rendered below the table) — POG dates
  // are inline-editable (<input type="date">), autosave to QB on change.
  function _pogBlockHtml(rec) {{
    const pogLaunch = rec.pog_launch || '';
    const pogEnd    = rec.pog_end    || '';
    const stores    = Number(rec.store_count || 0);
    const mp        = Number(rec.master_pack || rec.mp || 1);
    // Always render — planners need the editable inputs even on items
    // with no POG/Store data yet (e.g. new setups).
    const toIsoDate = s => {{
      if (!s) return '';
      const m = String(s).match(/^(\\d{{4}}-\\d{{2}}-\\d{{2}})/);
      return m ? m[1] : '';
    }};
    const addDays = (iso, days) => {{
      if (!iso) return null;
      const d = new Date(iso); if (isNaN(d.getTime())) return null;
      d.setDate(d.getDate() + days); return d;
    }};
    const fmtRange = (iso, dA, dB) => {{
      const a = addDays(iso, dA), b = addDays(iso, dB);
      if (!a || !b) return '—';
      const opt = {{ month:'short', day:'numeric' }};
      return `${{a.toLocaleDateString('en-US', opt)}}–${{b.toLocaleDateString('en-US', opt)}}`;
    }};
    let pogDur = '';
    if (pogLaunch && pogEnd) {{
      const a = new Date(pogLaunch), b = new Date(pogEnd);
      if (!isNaN(a.getTime()) && !isNaN(b.getTime())) {{
        const wks = Math.round((b - a) / (7 * 86400 * 1000));
        pogDur = ` <span style="color:#888;font-weight:normal">(${{wks}} wks)</span>`;
      }}
    }}
    const isoLow  = stores * mp * 1.0;
    const isoMid  = stores * mp * 1.5;
    const isoHigh = stores * mp * 2.0;
    const fN = n => Math.round(n).toLocaleString();
    const orderWindow  = pogLaunch ? fmtRange(pogLaunch, -42, -28) : '—';
    const cancelWindow = pogLaunch ? fmtRange(pogLaunch, -28, -14) : '—';
    const safeKey      = (rec.key || '').replace(/'/g, '&#39;');
    return `
      <div style="margin:8px 12px 0 12px;padding:10px 12px;background:#f5fbf3;border:1px solid #c7e2bf;border-radius:6px;font-size:11px;color:#2e4f24;">
        <div style="font-weight:700;margin-bottom:6px;color:#1b5e20;">📅 POG Information</div>
        <div style="display:flex;flex-wrap:wrap;gap:18px 24px;align-items:center;">
          <div>
            <b>POG Launch:</b>
            <input type="date" id="pog-launch-${{safeKey}}" value="${{toIsoDate(pogLaunch)}}"
                   onchange="savePogDate('${{safeKey}}','launch',this.value,this)"
                   title="Click to edit — autosaves on change"
                   style="font-size:11px;padding:2px 6px;border:1px solid #c7e2bf;border-radius:3px;background:#fff;color:#2e4f24;font-family:inherit;margin-left:4px;">
            ${{pogDur}}
          </div>
          <div>
            <b>POG End:</b>
            <input type="date" id="pog-end-${{safeKey}}" value="${{toIsoDate(pogEnd)}}"
                   onchange="savePogDate('${{safeKey}}','end',this.value,this)"
                   title="Click to edit — autosaves on change"
                   style="font-size:11px;padding:2px 6px;border:1px solid #c7e2bf;border-radius:3px;background:#fff;color:#2e4f24;font-family:inherit;margin-left:4px;">
          </div>
          <div>
            <b>Store count:</b>
            <input type="number" min="0" step="1" id="store-count-${{safeKey}}" value="${{stores || ''}}"
                   onchange="saveStoreCount('${{safeKey}}',this.value,this)"
                   title="Click to edit — autosaves on change"
                   placeholder="0"
                   style="font-size:11px;padding:2px 6px;border:1px solid #c7e2bf;border-radius:3px;background:#fff;color:#2e4f24;font-family:inherit;margin-left:4px;width:80px;">
          </div>
          <div><b>Master pack:</b> ${{mp.toLocaleString()}}/case</div>
          <div id="pog-msg-${{safeKey}}" style="font-size:10px;color:#1b5e20;"></div>
        </div>
        ${{stores > 0 ? `
        <div style="margin-top:8px;padding-top:6px;border-top:1px solid #d4ead0;">
          <b>Expected ISO order:</b> ~${{fN(isoMid)}} units (${{stores.toLocaleString()}} stores × 1.5 MP @ ${{mp}}/case).
          Range ${{fN(isoLow)}}–${{fN(isoHigh)}} (1–2 MP/store).
        </div>` : ''}}
        ${{pogLaunch ? `
        <div style="margin-top:4px;">
          <b>Likely order window:</b> ${{orderWindow}} <span style="color:#888;">(4–6 wks before POG launch)</span> ·
          <b>Cancel→in-store lead time:</b> 2–4 wks (cancel ${{cancelWindow}}).
          After ISO, expect a ~4-wk pause before replenishment orders begin.
        </div>` : ''}}
      </div>`;
  }}
  const pogBlockHtml = _pogBlockHtml(r);

  // ── L26W Orders & Shipments history ─────────────────────────────────────
  // Compute the actual start-of-week date for each historical week.  PRJ_COLS[0]
  // is "MM DD W1" — the most recent Sunday — so historical week N (1-indexed
  // back from Last Wk) started (N) weeks before W1.  Display as "M/D" (e.g. "4/27")
  // since planners think in calendar dates, not "N weeks ago".
  const _w1Match = (PRJ_COLS && PRJ_COLS[0] || '').match(/(\\d{{2}})\\s+(\\d{{2}})/);
  const _w1Year  = new Date().getFullYear();
  const _W1_DATE = _w1Match
    ? new Date(_w1Year, parseInt(_w1Match[1], 10) - 1, parseInt(_w1Match[2], 10))
    : null;
  function _fmtHistDate(weeksBeforeW1) {{
    if (!_W1_DATE) return weeksBeforeW1 + 'w ago';
    const d = new Date(_W1_DATE.getTime());
    d.setDate(d.getDate() - weeksBeforeW1 * 7);
    return (d.getMonth() + 1) + '/' + d.getDate();
  }}
  const histShp = r.hist_shp || [];
  const histOrd = r.hist_ord || [];
  let histHtml  = '';
  if (histShp.length || histOrd.length) {{
    let histHdrCells = '<th class="row-label"></th>';
    let shpCells     = '<td class="row-label" style="color:#6a1b9a;font-weight:600">Shipments</td>';
    let ordCells     = '<td class="row-label" style="color:#e65100;font-weight:600">Orders</td>';
    let shpTot = 0, ordTot = 0;
    for (let i = 25; i >= 0; i--) {{
      // i=25 → Last Wk → 1 week before W1; i=0 → 26 weeks before W1
      const label = _fmtHistDate(26 - i);
      histHdrCells += `<th style="font-size:10px;font-weight:normal">${{label}}</th>`;
      const sv = histShp[i] || 0;
      shpCells += `<td style="${{sv === 0 ? 'color:#bbb' : 'color:#6a1b9a;font-weight:600'}}">${{fmtN(sv)}}</td>`;
      const ov = histOrd[i] || 0;
      ordCells += `<td style="${{ov === 0 ? 'color:#bbb' : 'color:#e65100;font-weight:600'}}">${{fmtN(ov)}}</td>`;
      shpTot += sv;  ordTot += ov;
    }}
    histHdrCells += '<th>Total</th>';
    shpCells     += `<td style="font-weight:700;color:#6a1b9a">${{fmtN(shpTot)}}</td>`;
    ordCells     += `<td style="font-weight:700;color:#e65100">${{fmtN(ordTot)}}</td>`;
    histHtml = `
    <div style="overflow-x:auto;padding:4px 12px 8px 12px;border-top:2px solid #ede7f6;">
      <div style="font-size:11px;color:#555;font-weight:600;padding:4px 0 2px 0;">L26W History</div>
      <table class="dtbl">
        <tr>${{histHdrCells}}</tr>
        <tr>${{shpCells}}</tr>
        <tr>${{ordCells}}</tr>
      </table>
    </div>`;
  }}

  const _narParts = (r.narrative || '').split('\n').filter(s => s.trim());
  const narrativeHtml = _narParts.length
    ? '<div style="padding:8px 12px;background:#f5f5f5;border-top:1px solid #ddd;font-size:12px;line-height:1.5;color:#333;">' +
      '<div style="font-weight:700;font-size:11px;text-transform:uppercase;letter-spacing:0.5px;color:#555;margin-bottom:5px;">AI Analysis</div>' +
      '<ul style="margin:2px 0;padding-left:16px;">' +
      _narParts.map(p => '<li style="margin-bottom:4px;">' + p + '</li>').join('') +
      '</ul></div>'
    : '';

  // ── New Comment box + 30-day comment history (25/75 split) ──────────────
  const safeKey  = r.key.replace(/'/g, "&#39;");
  const safeId2  = r.key.replace(/[^a-zA-Z0-9]/g, '_');
  const flagCls2 = 'flag-btn' + (r.flagged ? ' flagged' : '');
  // 🤖 Tell-AI block — 33% AI input / 67% AI-only history (planner ↔ AI dialogue).
  // Mgr/flag comments live in their own pane in the comment block below.
  const tellAiBlock = `
  <div style="margin:10px 12px 0 12px;padding:10px 12px;background:#f0f7ff;border:1px solid #1565c0;border-radius:6px;">
    <div style="font-weight:600;color:#1565c0;margin-bottom:6px;font-size:12px;display:flex;align-items:center;gap:6px;">
      🤖 Adjust AI Forecast
      <span style="font-weight:400;color:#666;font-size:11px;">— explain the change in plain English, AI proposes a 26-week diff, you review &amp; apply</span>
    </div>
    <div style="display:flex;gap:14px;align-items:flex-start;">
      <!-- LEFT: Tell-AI textarea + preview (25%, matches comment block) -->
      <div style="flex:0 0 25%;min-width:0;">
        <textarea id="ai-adj-text-${{safeId2}}"
                  placeholder="Tell me in plain English what changed and I'll propose a 26-week diff. Examples:&#10;  • Boost Petsmart 25% in May for grooming season&#10;  • Distribution gain at 200 stores starting July&#10;  • EOL by Aug 14 — phase out gradually&#10;  • Double W14 for back-to-school&#10;  • Increase by 500 units/wk through holiday&#10;  • Cut October orders 20% — slow Q4 expected"
                  rows="4"
                  style="width:100%;padding:6px 8px;border:1px solid #1565c0;border-radius:4px;font-size:12px;font-family:inherit;resize:vertical;box-sizing:border-box;"></textarea>
        <div style="display:flex;gap:8px;margin-top:6px;align-items:center;flex-wrap:wrap;">
          <button onclick="previewAiAdjustment('${{safeKey}}')" style="font-size:11px;padding:5px 14px;background:#1565c0;color:#fff;border:none;border-radius:4px;cursor:pointer;font-weight:600;">Preview Adjustments</button>
        </div>
        <div style="font-size:10px;color:#666;margin-top:4px;line-height:1.4;">Plain English works — I'll translate. Months (May-Oct), dates (Aug 14), "ramp up", "double", "boost X% in W7", "EOL by W14", and free-form phrasings all welcome.</div>
        <div id="ai-adj-preview-${{safeId2}}"></div>
      </div>
      <!-- RIGHT: AI Adjustment History (75%) — only "AI Adjusted" comments -->
      <div style="flex:1 1 75%;min-width:0;">
        <div style="font-weight:600;color:#1565c0;margin-bottom:6px;font-size:12px;display:flex;align-items:center;justify-content:space-between;">
          <span>🤖 AI Adjustment History <span style="font-weight:400;color:#999;font-size:10px;">— last 30 days, oldest first · planner ↔ AI dialogue</span></span>
          <button onclick="loadCommentHistory('${{safeKey}}', true)" title="Refresh from Quickbase" style="font-size:10px;padding:2px 8px;border:1px solid #ccc;background:#fff;border-radius:3px;cursor:pointer;">↻</button>
        </div>
        <div id="ai-hist-${{safeKey}}" style="max-height:200px;overflow-y:auto;border:1px solid #bbdefb;border-radius:4px;background:#fafdff;padding:6px 8px;font-size:11px;color:#999;font-style:italic;">
          Loading…
        </div>
      </div>
    </div>
  </div>`;

  // Comment block — Flag/Mgr conversation thread (planner ↔ inventory mgr).
  // 25% Add-a-Comment | 75% Comment History (filtered to NON-AI comments).
  const commentBlock = `
  <div style="margin:10px 12px 12px 12px;padding:12px;background:#f7f9fc;border:1px solid #d8dce3;border-radius:6px;">
    <label style="display:flex;align-items:center;gap:6px;margin-bottom:8px;font-size:11px;color:#555;cursor:pointer;">
      <span>Flag for inv mgr review:</span>
      <button id="flg-${{safeId2}}" class="${{flagCls2}}" onclick="toggleFlag('${{safeKey}}')" title="Toggle the QB Flagged boolean for this projection">⚑</button>
    </label>
    <div style="display:flex;gap:14px;align-items:flex-start;">
      <!-- LEFT: Add a Comment (25%) — for planner ↔ mgr -->
      <div style="flex:0 0 25%;min-width:0;">
    <div style="font-weight:600;color:#8b2252;margin-bottom:6px;font-size:12px;">Add a Comment <span style="font-weight:400;color:#999;font-size:10px;">— for inv mgr</span></div>
    <textarea id="cmt-text-${{safeKey}}" oninput="autoFlagOnComment('${{safeKey}}')" placeholder="Write a comment for the mgr review..." style="width:100%;min-height:80px;padding:6px 8px;border:1px solid #ccc;border-radius:4px;font-size:12px;font-family:inherit;resize:vertical;box-sizing:border-box;"></textarea>
    <div style="display:flex;align-items:center;gap:6px;margin-top:6px;flex-wrap:wrap;">
      <label style="font-size:11px;color:#555;">Status:
        <select id="cmt-flag-${{safeKey}}" style="font-size:11px;padding:3px 6px;border:1px solid #ccc;border-radius:3px;margin-left:4px;">
          <option value="Needs Action" selected>Needs Action</option>
          <option value="Investigating">Investigating</option>
          <option value="In Progress">In Progress</option>
          <option value="Resolved">Resolved</option>
          <option value="Dismissed">Dismissed</option>
        </select>
      </label>
      <button id="cmt-btn-${{safeKey}}" onclick="addComment('${{safeKey}}')" style="padding:5px 14px;background:#8b2252;color:#fff;border:none;border-radius:4px;cursor:pointer;font-weight:600;font-size:11px;">Save</button>
    </div>
    <div id="cmt-msg-${{safeKey}}" style="font-size:11px;color:#666;margin-top:4px;"></div>
      </div>
      <!-- RIGHT: Mgr/Flag comment history (75%) — non-AI comments only -->
      <div style="flex:1 1 75%;min-width:0;">
        <div style="font-weight:600;color:#8b2252;margin-bottom:6px;font-size:12px;display:flex;align-items:center;justify-content:space-between;">
          <span>📋 Comment History <span style="font-weight:400;color:#999;font-size:10px;">— last 30 days, oldest first · planner ↔ mgr</span></span>
          <button onclick="loadCommentHistory('${{safeKey}}', true)" title="Refresh from Quickbase" style="font-size:10px;padding:2px 8px;border:1px solid #ccc;background:#fff;border-radius:3px;cursor:pointer;">↻</button>
        </div>
        <div id="cmt-hist-${{safeKey}}" style="max-height:180px;overflow-y:auto;border:1px solid #e8d5dc;border-radius:4px;background:#fffafd;padding:6px 8px;font-size:11px;color:#999;font-style:italic;">
          Loading…
        </div>
      </div>
    </div>
  </div>`;

  el.innerHTML = `<td colspan="20" style="padding:0">
    ${{pogBlockHtml}}
    ${{narrativeHtml}}
    <div style="overflow-x:auto;padding:8px 12px;">
      <table class="dtbl">
        <tr>${{hdrCells}}</tr>
        <tr>${{projCells}}</tr>
        <tr>${{aiCells}}</tr>
        <tr>${{sugCells}}</tr>
        <tr><td colspan="28" style="padding:0;height:6px;background:transparent;border:none"></td></tr>
        <tr>${{opnCells}}</tr>
        <tr>${{lyOrdCells}}</tr>
        <tr>${{lyShpCells}}</tr>
      </table>
    </div>
    ${{histHtml}}
    ${{invFlowSectionHtml}}
    ${{tellAiBlock}}
    ${{commentBlock}}
  </td>`;
  el.dataset.loaded = '1';
  // Pull the 30-day comment history and populate the right pane
  loadCommentHistory(r.key);
}}

// ── 30-day comment history loader ───────────────────────────────────────────
//
// Fetches the last 30 days of comments for this acct-mstyle key from the
// Flask backend (/api/comment-history) and renders them oldest-first in the
// right pane of the comment block.
async function loadCommentHistory(key, force) {{
  // Renders two distinct panes from a single backend call:
  //   ai-hist-{{key}}   ← AI Comments table (planner ↔ AI thread)
  //   cmt-hist-{{key}}  ← Projection Comments table (planner ↔ mgr thread)
  // Backend returns mgr_comments + ai_comments as separate arrays.
  const containerKey = key.replace(/'/g, "&#39;");
  const aiCont  = document.getElementById('ai-hist-'  + containerKey);
  const cmtCont = document.getElementById('cmt-hist-' + containerKey);
  if (!aiCont && !cmtCont) return;
  if (!force) {{
    if (aiCont)  aiCont.innerHTML  = 'Loading…';
    if (cmtCont) cmtCont.innerHTML = 'Loading…';
  }}
  try {{
    const res  = await fetch('/api/comment-history?key=' + encodeURIComponent(key) + '&days=30');
    const data = await res.json();
    if (!res.ok || data.error) throw new Error(data.error || ('HTTP ' + res.status));
    const escHtml = s => String(s == null ? '' : s).replace(/[<>&]/g, c => ({{'<':'&lt;','>':'&gt;','&':'&amp;'}})[c]);
    const fmtTs  = ts => {{
      try {{ return new Date(ts).toLocaleString('en-US', {{ year:'numeric', month:'short', day:'numeric', hour:'2-digit', minute:'2-digit' }}); }}
      catch (e) {{ return ts || ''; }}
    }};
    // Mgr/flag pane
    if (cmtCont) {{
      const rows = data.mgr_comments || [];
      if (!rows.length) {{
        cmtCont.innerHTML = '<div style="color:#999;font-style:italic;">No mgr/flag comments in the last 30 days.</div>';
      }} else {{
        cmtCont.innerHTML = rows.map(c => {{
          const flag = c.flag || '';
          const flagBadge = flag
            ? `<span style="display:inline-block;font-size:9px;font-weight:700;padding:1px 6px;border-radius:8px;background:#fff3e0;color:#8b2252;margin-left:6px;vertical-align:middle;">${{escHtml(flag)}}</span>`
            : '';
          return `
            <div style="padding:5px 0;border-bottom:1px solid #f0f0f0;">
              <div style="font-size:10px;color:#888;font-weight:600;">${{escHtml(fmtTs(c.ts || ''))}}${{flagBadge}}</div>
              <div style="font-size:11px;color:#333;white-space:pre-wrap;line-height:1.35;margin-top:2px;">${{escHtml(c.note || '')}}</div>
            </div>`;
        }}).join('');
        cmtCont.scrollTop = cmtCont.scrollHeight;
      }}
    }}
    // AI pane
    if (aiCont) {{
      const rows = data.ai_comments || [];
      if (!rows.length) {{
        aiCont.innerHTML = '<div style="color:#999;font-style:italic;">No prior AI adjustments in the last 30 days.</div>';
      }} else {{
        aiCont.innerHTML = rows.map(c => {{
          const noteRaw = c.note || '';
          const note    = noteRaw.replace(/\\s*\[ai-intent[^\]]*\]/g, '').trim();
          const author  = c.author || '';
          const ignored = !!c.ignored;
          const rid     = c.rid || 0;
          const authorBadge = author
            ? `<span style="display:inline-block;font-size:9px;font-weight:700;padding:1px 6px;border-radius:8px;background:#e3f2fd;color:#1565c0;margin-left:6px;vertical-align:middle;">${{escHtml(author)}}</span>`
            : '';
          const ignoredBadge = ignored
            ? `<span style="display:inline-block;font-size:9px;font-weight:700;padding:1px 6px;border-radius:8px;background:#eeeeee;color:#888;margin-left:6px;vertical-align:middle;">IGNORED</span>`
            : '';
          const ignoreBtn = (!ignored && rid)
            ? `<button onclick="ignoreAiComment(${{rid}}, '${{key.replace(/'/g, "&#39;")}}')"
                       title="Stop applying this adjustment on future forecaster runs (audit trail preserved)"
                       style="float:right;font-size:9px;padding:1px 6px;border:1px solid #c62828;background:#fff;color:#c62828;border-radius:3px;cursor:pointer;font-weight:600;margin-left:6px;">× Ignore</button>`
            : '';
          const rowStyle = ignored ? 'padding:5px 0;border-bottom:1px solid #f0f0f0;opacity:0.5;' : 'padding:5px 0;border-bottom:1px solid #f0f0f0;';
          return `
            <div style="${{rowStyle}}">
              <div style="font-size:10px;color:#888;font-weight:600;">${{escHtml(fmtTs(c.ts || ''))}}${{authorBadge}}${{ignoredBadge}}${{ignoreBtn}}</div>
              <div style="font-size:11px;color:#333;white-space:pre-wrap;line-height:1.35;margin-top:2px;">${{escHtml(note)}}</div>
            </div>`;
        }}).join('');
        aiCont.scrollTop = aiCont.scrollHeight;
      }}
    }}
  }} catch (e) {{
    const errHtml = `<div style="color:#c62828;">Failed to load history: ${{(e.message||'')}}</div>`;
    if (aiCont)  aiCont.innerHTML  = errHtml;
    if (cmtCont) cmtCont.innerHTML = errHtml;
  }}
}}

// Mark a single AI Adjustment History entry as Resolved so F58 stops
// replaying it on future forecaster runs.  The comment row itself is
// preserved in QB as audit trail; only the [Flag] field flips
// 'AI Adjusted' → 'Resolved'.
async function ignoreAiComment(rid, key) {{
  if (!rid) return;
  if (!confirm('Stop applying this adjustment on future forecast runs? The comment stays in your history as an audit trail; F58 just skips it next time.')) return;
  try {{
    const res = await fetch('/api/ignore-ai-comment', {{
      method:  'POST',
      headers: {{'Content-Type':'application/json'}},
      body:    JSON.stringify({{rid: rid}})
    }});
    const data = await res.json();
    if (!res.ok || data.error) throw new Error(data.error || ('HTTP ' + res.status));
    if (typeof loadCommentHistory === 'function') loadCommentHistory(key, true);
  }} catch (e) {{
    alert('Could not mark as ignored: ' + (e.message || e));
  }}
}}
window.ignoreAiComment = ignoreAiComment;

async function addComment(key) {{
  const txt  = document.getElementById('cmt-text-' + key).value.trim();
  const flag = document.getElementById('cmt-flag-' + key).value;
  const btn  = document.getElementById('cmt-btn-' + key);
  const msg  = document.getElementById('cmt-msg-' + key);
  if (!txt) {{ msg.textContent = 'Comment cannot be empty.'; msg.style.color = '#c62828'; return; }}
  btn.disabled = true; btn.textContent = 'Saving...'; msg.textContent = '';
  try {{
    const res = await fetch('/api/add-comment', {{
      method: 'POST',
      headers: {{'Content-Type':'application/json'}},
      body: JSON.stringify({{key: key, note: txt, flag: flag}})
    }});
    const data = await res.json();
    if (!res.ok || data.error) throw new Error(data.error || ('HTTP ' + res.status));
    msg.textContent = '✓ Saved'; msg.style.color = '#2e7d32';
    document.getElementById('cmt-text-' + key).value = '';
    document.getElementById('cmt-flag-' + key).value = 'Needs Action';
    // Update local copy of the record so re-expand shows the new comment immediately
    const rec = ALL_RECORDS.find(x => x.key === key);
    if (rec) {{
      const stamp = new Date().toISOString().slice(0,16).replace('T',' ');
      const flagTag = flag ? ' ['+flag+']' : '';
      rec.last_comment = `${{stamp}} - you${{flagTag}}: ${{txt.slice(0,200)}}`;
      rec.last_comment_date = new Date().toISOString();
    }}
    btn.textContent = 'Save'; btn.disabled = false;
    // Refresh the 30-day history pane in place so the new comment shows up
    if (typeof loadCommentHistory === 'function') loadCommentHistory(key, true);
  }} catch (e) {{
    msg.textContent = 'Failed: ' + e.message; msg.style.color = '#c62828';
    btn.textContent = 'Save'; btn.disabled = false;
  }}
}}

// ── Multi-select dropdown widget ─────────────────────────────────────────────
//
// All filters except aiDiffFilter (a numeric threshold range) are checkbox
// dropdowns: click the button → panel opens with searchable checkboxes →
// pick any combination → applyFilters() runs on each toggle.  The widget is
// mounted into a <div class="ms" id="..."> wrapper.  Each created widget
// exposes _getSelected() / _setSelection() / _clearSelection() helpers.
function createMultiSelect(id, options, sortFn) {{
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
  search.placeholder = 'Filter...';   // ASCII dots — avoid charset issues with U+2026
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
  // {{value, label, tooltip}}.  The label is what the user sees in the menu;
  // the optional tooltip becomes the row's hover title (used for Volume /
  // Priority threshold definitions so the menu itself stays clean).
  const norm = v => (v && typeof v === 'object') ? v : {{ value: v, label: v }};
  const arr = [...options].map(norm).filter(o => o.value != null && o.value !== '');
  arr.sort(sortFn || ((a, b) => String(a.label).localeCompare(String(b.label))));
  arr.forEach(o => {{
    const lab = document.createElement('label');
    lab.className = 'ms-opt';
    if (o.tooltip) lab.title = o.tooltip;
    const cb = document.createElement('input');
    cb.type = 'checkbox';
    cb.value = o.value;
    cb.addEventListener('change', () => {{ update(); window.applyFilters(); }});
    const txt = document.createElement('span');
    txt.textContent = o.label;
    // Mirror the tooltip onto the inner span so hover registers whether
    // the cursor is over the row, the checkbox, or the label text.
    if (o.tooltip) txt.title = o.tooltip;
    lab.appendChild(cb);
    lab.appendChild(txt);
    list.appendChild(lab);
  }});

  function update() {{
    const sels = list.querySelectorAll('input:checked');
    if (sels.length === 0) {{ btn.textContent = allLabel; btn.title = allLabel; btn.classList.remove('has-sel'); }}
    else if (sels.length === 1) {{ btn.textContent = sels[0].value; btn.title = sels[0].value; btn.classList.add('has-sel'); }}
    else {{
      const labels = [...sels].map(s => s.value).join(', ');
      btn.textContent = `${{sels.length}} selected`;
      btn.title = labels;
      btn.classList.add('has-sel');
    }}
  }}
  // Position the panel relative to the trigger button each time it opens.
  // Panel is position:fixed (escapes the toolbar's overflow-x:auto clipping
  // rectangle), so we set top/left from the button's viewport coordinates.
  function positionPanel() {{
    const r = btn.getBoundingClientRect();
    panel.style.top      = (r.bottom + 2) + 'px';
    panel.style.left     = r.left + 'px';
    panel.style.minWidth = r.width + 'px';
  }}
  btn.addEventListener('click', e => {{
    e.stopPropagation();
    document.querySelectorAll('.ms.open').forEach(o => {{ if (o !== wrap) o.classList.remove('open'); }});
    const willOpen = !wrap.classList.contains('open');
    if (willOpen) positionPanel();
    wrap.classList.toggle('open');
    if (wrap.classList.contains('open')) {{ search.focus(); }}
  }});
  panel.addEventListener('click', e => e.stopPropagation());
  search.addEventListener('input', () => {{
    const q = search.value.toLowerCase();
    list.querySelectorAll('.ms-opt').forEach(opt => {{
      const t = opt.querySelector('span').textContent.toLowerCase();
      opt.style.display = t.includes(q) ? '' : 'none';
    }});
  }});
  allBtn.addEventListener('click', () => {{
    list.querySelectorAll('.ms-opt').forEach(opt => {{
      if (opt.style.display !== 'none') opt.querySelector('input').checked = true;
    }});
    update(); window.applyFilters();
  }});
  clrBtn.addEventListener('click', () => {{
    list.querySelectorAll('input').forEach(cb => {{ cb.checked = false; }});
    update(); window.applyFilters();
  }});

  wrap._getSelected = function () {{
    const out = new Set();
    list.querySelectorAll('input:checked').forEach(cb => out.add(cb.value));
    return out;
  }};
  wrap._setSelection = function (values) {{
    const want = new Set(values || []);
    list.querySelectorAll('input').forEach(cb => {{ cb.checked = want.has(cb.value); }});
    update();
  }};
  wrap._clearSelection = function () {{
    list.querySelectorAll('input').forEach(cb => {{ cb.checked = false; }});
    search.value = '';
    list.querySelectorAll('.ms-opt').forEach(opt => {{ opt.style.display = ''; }});
    update();
  }};
  return wrap;
}}

// Close any open multi-select panel when clicking outside it
document.addEventListener('click', () => {{
  document.querySelectorAll('.ms.open').forEach(o => o.classList.remove('open'));
}});
// Panels are position:fixed — close on PAGE scroll so they don't appear
// stranded over the report once the user scrolls the page or table.
// IMPORTANT: no capture-phase, and skip if the scroll happened inside the
// panel itself — otherwise users can't scroll the option list.
window.addEventListener('scroll', (e) => {{
  if (e.target && e.target.nodeType === 1 && e.target.closest && e.target.closest('.ms-panel')) return;
  document.querySelectorAll('.ms.open').forEach(o => o.classList.remove('open'));
}});

// Read selections from a multi-select widget — empty Set == "All"
function _msSel(id) {{
  const el = document.getElementById(id);
  return (el && typeof el._getSelected === 'function') ? el._getSelected() : new Set();
}}

// Click a header volume badge → toggle that single tier in the volFilter
// multi-select (clicking an already-active badge clears the selection).
function filterVol(vol) {{
  const sel = document.getElementById('volFilter');
  if (!sel || typeof sel._getSelected !== 'function') return;
  const current = sel._getSelected();
  const isOnlyThisOne = current.size === 1 && current.has(vol);
  sel._setSelection(isOnlyThisOne ? [] : [vol]);
  const btns = {{'HIGH':'btn-high','MEDIUM':'btn-med','LOW':'btn-low'}};
  Object.entries(btns).forEach(([v, id]) => {{
    const b = document.getElementById(id);
    if (b) b.classList.toggle('badge-active', !isOnlyThisOne && v === vol);
  }});
  applyFilters();
}}

function filterPri(pri) {{
  const sel = document.getElementById('priFilter');
  if (!sel || typeof sel._getSelected !== 'function') return;
  const current = sel._getSelected();
  const isOnlyThisOne = current.size === 1 && current.has(pri);
  sel._setSelection(isOnlyThisOne ? [] : [pri]);
  const btns = {{'CRITICAL':'btn-pri-crit','MEDIUM':'btn-pri-med','LOW':'btn-pri-low'}};
  Object.entries(btns).forEach(([v, id]) => {{
    const b = document.getElementById(id);
    if (b) b.classList.toggle('badge-active', !isOnlyThisOne && v === pri);
  }});
  applyFilters();
}}

function resetAllFilters() {{
  // Clear every filter so the table shows all active projections.
  // Triggered by clicking the "X records" badge or the Clear Filters button.
  const search = document.getElementById('search');
  if (search) search.value = '';
  ['volFilter','priFilter','patFilter','brandFilter','mgrFilter','custFilter'].forEach(id => {{
    const el = document.getElementById(id);
    if (el && typeof el._clearSelection === 'function') el._clearSelection();
  }});
  const ai = document.getElementById('aiDiffFilter');
  if (ai) ai.value = '0';
  ['btn-high','btn-med','btn-low','btn-pri-crit','btn-pri-med','btn-pri-low'].forEach(id => {{
    const el = document.getElementById(id);
    if (el) el.classList.remove('badge-active');
  }});
  // Clear per-column quick filters and reset the column sort.
  document.querySelectorAll('.col-filter').forEach(el => {{ el.value = ''; }});
  CURRENT_SORT_KEY = null;
  CURRENT_SORT_DIR = 0;
  _updateSortIndicators();
  // Also clear the Flagged-Only toggle.
  FLAGGED_ONLY = false;
  try {{ sessionStorage.setItem('flaggedOnly', '0'); }} catch (e) {{ /* ignore */ }}
  _syncFlaggedOnlyButton();
  applyFilters();
}}

// ── Per-column sort + filter state ──────────────────────────────────────────
let CURRENT_SORT_KEY = null;
let CURRENT_SORT_DIR = 0;
const _PRI_ORDINAL = {{ CRITICAL: 3, MEDIUM: 2, LOW: 1 }};

function _aiVsProjPct(r) {{
  return r.proj_total > 0 ? ((r.ai_total - r.proj_total) / r.proj_total * 100) : 0;
}}
function _recVal(r, key) {{
  if (key === 'ai_vs_proj') return _aiVsProjPct(r);
  if (key === 'priority')   return _PRI_ORDINAL[r.priority] || 0;
  return r[key];
}}
function _buildColFilterPred(expr, colType) {{
  expr = (expr || '').trim();
  if (!expr) return null;
  if (colType === 'number') {{
    const m = expr.match(/^\\s*(>=|<=|!=|>|<|=)\\s*(-?\\d+(?:\\.\\d+)?)\\s*$/);
    if (m) {{
      const op = m[1], n = parseFloat(m[2]);
      return v => {{
        const x = (typeof v === 'number') ? v : parseFloat(v);
        if (Number.isNaN(x)) return false;
        switch (op) {{
          case '>':  return x > n;
          case '<':  return x < n;
          case '>=': return x >= n;
          case '<=': return x <= n;
          case '=':  return x === n;
          case '!=': return x !== n;
        }}
        return false;
      }};
    }}
  }}
  const needle = expr.toLowerCase();
  return v => String(v == null ? '' : v).toLowerCase().includes(needle);
}}
function _readColFilters() {{
  const out = [];
  document.querySelectorAll('.col-filter').forEach(el => {{
    const expr = el.value;
    if (!expr || !expr.trim()) return;
    const field = el.dataset.field;
    const type  = el.dataset.colType || 'string';
    const pred  = _buildColFilterPred(expr, type);
    if (pred) out.push({{ field, pred }});
  }});
  return out;
}}
function _updateSortIndicators() {{
  document.querySelectorAll('thead th.sortable').forEach(th => {{
    th.classList.remove('sort-asc', 'sort-desc');
    if (th.dataset.sortKey === CURRENT_SORT_KEY && CURRENT_SORT_DIR !== 0) {{
      th.classList.add(CURRENT_SORT_DIR > 0 ? 'sort-asc' : 'sort-desc');
    }}
  }});
}}
function cycleSort(key) {{
  if (CURRENT_SORT_KEY !== key) {{
    CURRENT_SORT_KEY = key;
    CURRENT_SORT_DIR = 1;
  }} else if (CURRENT_SORT_DIR === 1) {{
    CURRENT_SORT_DIR = -1;
  }} else {{
    CURRENT_SORT_KEY = null;
    CURRENT_SORT_DIR = 0;
  }}
  _updateSortIndicators();
  applyFilters();
}}
// Reset sort only — leaves global + column filters untouched.
function resetSort() {{
  CURRENT_SORT_KEY = null;
  CURRENT_SORT_DIR = 0;
  _updateSortIndicators();
  applyFilters();
}}
window.cycleSort = cycleSort;
window.resetSort = resetSort;

function populateFilters() {{
  // Auto-populate every multi-select widget from the values actually
  // present in ALL_RECORDS — no hardcoded option lists.
  const sets = {{
    brand:       new Set(),
    inv_manager: new Set(),
    cust:        new Set(),
    pattern:     new Set(),
    vol:         new Set(),
    pri:         new Set(),
  }};
  ALL_RECORDS.forEach(r => {{
    if (r.brand)       sets.brand.add(r.brand);
    if (r.inv_manager) sets.inv_manager.add(r.inv_manager);
    if (r.cust)        sets.cust.add(r.cust);
    if (r.pattern)     sets.pattern.add(r.pattern);
    if (r.vol_tier)    sets.vol.add(r.vol_tier);
    if (r.priority)    sets.pri.add(r.priority);
  }});
  // Volume / priority have a natural rank — sort by it.  The dropdown shows
  // just the tier name; the threshold definition lives in a hover tooltip
  // so the menu reads cleanly at a glance.
  const VOL_TIPS = {{
    HIGH:   'HIGH: AI forecast >= 1,000 units / week',
    MEDIUM: 'MEDIUM: AI forecast 200 - 999 units / week',
    LOW:    'LOW: AI forecast < 200 units / week',
  }};
  const PRI_TIPS = {{
    CRITICAL: 'CRITICAL: HIGH volume AND |AI vs Proj| > 10%',
    MEDIUM:   'MEDIUM: MEDIUM volume AND |AI vs Proj| > 10%',
    LOW:      'LOW: everything else',
  }};
  const volRank = ['HIGH','MEDIUM','LOW'];
  const priRank = ['CRITICAL','MEDIUM','LOW'];
  const volOpts = [...sets.vol].map(v => ({{ value: v, label: v, tooltip: VOL_TIPS[v] || '' }}));
  const priOpts = [...sets.pri].map(v => ({{ value: v, label: v, tooltip: PRI_TIPS[v] || '' }}));
  const orderBy = ranks => (a, b) => ranks.indexOf(a.value) - ranks.indexOf(b.value);
  createMultiSelect('volFilter',   volOpts,         orderBy(volRank));
  createMultiSelect('priFilter',   priOpts,         orderBy(priRank));
  createMultiSelect('patFilter',   sets.pattern);
  createMultiSelect('brandFilter', sets.brand);
  createMultiSelect('mgrFilter',   sets.inv_manager);
  createMultiSelect('custFilter',  sets.cust);
}}

// Sticky toggle for the "Show Flagged Only" toolbar button.
let FLAGGED_ONLY = (function () {{
  try {{ return sessionStorage.getItem('flaggedOnly') === '1'; }}
  catch (e) {{ return false; }}
}})();

function toggleFlaggedOnly() {{
  FLAGGED_ONLY = !FLAGGED_ONLY;
  try {{ sessionStorage.setItem('flaggedOnly', FLAGGED_ONLY ? '1' : '0'); }}
  catch (e) {{ /* ignore */ }}
  _syncFlaggedOnlyButton();
  applyFilters();
}}

function _syncFlaggedOnlyButton() {{
  const btn = document.getElementById('flaggedOnlyBtn');
  if (!btn) return;
  if (FLAGGED_ONLY) {{
    btn.style.background = '#c62828';
    btn.style.color = '#fff';
    btn.title = 'Currently showing flagged records only — click to show all';
  }} else {{
    btn.style.background = '#fff';
    btn.style.color = '#c62828';
    btn.title = 'Show only records flagged for inventory mgr review (toggle)';
  }}
}}

function applyFilters() {{
  const search    = document.getElementById('search').value.toLowerCase();
  const volSet    = _msSel('volFilter');
  const priSet    = _msSel('priFilter');
  const patSet    = _msSel('patFilter');
  const brandSet  = _msSel('brandFilter');
  const mgrSet    = _msSel('mgrFilter');
  const custSet   = _msSel('custFilter');
  const aiDiffEl  = document.getElementById('aiDiffFilter');
  const aiDiffMin = aiDiffEl ? parseFloat(aiDiffEl.value) : 0;
  const colPreds  = _readColFilters();

  _syncFlaggedOnlyButton();

  FILTERED_RECORDS = ALL_RECORDS.filter(r => {{
    if (FLAGGED_ONLY && !r.flagged) return false;
    if (search) {{
      const txt = (r.key + ' ' + r.cust + ' ' + r.mstyle + ' ' + (r.desc||'') + ' ' + (r.brand||'') + ' ' + (r.inv_manager||'')).toLowerCase();
      if (!txt.includes(search)) return false;
    }}
    if (volSet.size   && !volSet.has(r.vol_tier))      return false;
    if (priSet.size   && !priSet.has(r.priority))      return false;
    if (patSet.size   && !patSet.has(r.pattern))       return false;
    if (brandSet.size && !brandSet.has(r.brand))       return false;
    if (mgrSet.size   && !mgrSet.has(r.inv_manager))   return false;
    if (custSet.size  && !custSet.has(r.cust))         return false;
    if (aiDiffMin > 0) {{
      const aiVsProj = r.proj_total > 0 ? Math.abs((r.ai_total - r.proj_total) / r.proj_total * 100) : 0;
      if (aiVsProj < aiDiffMin) return false;
    }}
    // Per-column quick filters
    for (const fp of colPreds) {{
      if (!fp.pred(_recVal(r, fp.field))) return false;
    }}
    return true;
  }});

  if (CURRENT_SORT_KEY && CURRENT_SORT_DIR !== 0) {{
    const k   = CURRENT_SORT_KEY;
    const dir = CURRENT_SORT_DIR;
    FILTERED_RECORDS.sort((a, b) => {{
      const va = _recVal(a, k);
      const vb = _recVal(b, k);
      const an = (typeof va === 'number');
      const bn = (typeof vb === 'number');
      if (an && bn) return dir * (va - vb);
      const sa = _sortKey(va);
      const sb = _sortKey(vb);
      if (sa === '￿' && sb !== '￿') return 1;
      if (sb === '￿' && sa !== '￿') return -1;
      return dir * sa.localeCompare(sb);
    }});
  }} else {{
    // Default sort: Inv Mgr → Brand → Customer → Mstyle.
    FILTERED_RECORDS.sort((a, b) =>
         _sortKey(a.inv_manager).localeCompare(_sortKey(b.inv_manager))
      || _sortKey(a.brand      ).localeCompare(_sortKey(b.brand      ))
      || _sortKey(a.cust       ).localeCompare(_sortKey(b.cust       ))
      || _sortKey(a.mstyle     ).localeCompare(_sortKey(b.mstyle     ))
    );
  }}
  renderPage(0);
}}

// Default sort: Inventory Manager → Brand Name → Customer → Mstyle.
// Blank values sort last within each tier so well-populated rows surface first.
function _sortKey(v) {{
  const s = (v == null ? '' : String(v)).trim();
  return s === '' ? '￿' : s.toLowerCase();
}}

function _setBoot(msg) {{
  const el = document.getElementById('bootStatus');
  if (el) el.textContent = msg;
}}
function _hideBoot() {{
  const el = document.getElementById('bootOverlay');
  if (el) el.style.display = 'none';
}}

// Progressive bootstrap.  The HTML payload is now small (no embedded record
// JSON), so the loading overlay paints in <100ms.  We fetch the records from
// /api/records.json with a real progress indicator so the user can see what's
// happening while ~9MB streams over localhost.
function _setDetail(msg) {{
  const el = document.getElementById('bootDetail');
  if (el) el.textContent = msg;
}}

async function _bootstrap() {{
  const _t0 = performance.now();
  try {{
    _setBoot('Loading projections…');
    _setDetail('Requesting record set from viewer backend');
    const res = await fetch('/api/records.json');
    if (!res.ok) throw new Error(`HTTP ${{res.status}}`);

    // Stream the response with a progress meter.  Browser decompresses gzip
    // transparently — chunk sizes here are the *decoded* bytes, which is
    // the right metric for "how close to finished?"
    const reader = res.body && res.body.getReader ? res.body.getReader() : null;
    let bytes = 0;
    const chunks = [];
    if (reader) {{
      const decoder = new TextDecoder('utf-8');
      let lastTick = 0;
      while (true) {{
        const {{value, done}} = await reader.read();
        if (done) break;
        chunks.push(value);
        bytes += value.length;
        const now = Date.now();
        if (now - lastTick > 80) {{
          _setBoot('Loading projections…');
          _setDetail(`Downloaded ${{(bytes/1024/1024).toFixed(2)}} MB`);
          lastTick = now;
        }}
      }}
      _setBoot('Parsing projection data…');
      _setDetail(`${{(bytes/1024/1024).toFixed(2)}} MB received · decoding JSON`);
      await new Promise(r => setTimeout(r, 16));
      const merged = new Uint8Array(bytes);
      let off = 0;
      for (const c of chunks) {{ merged.set(c, off); off += c.length; }}
      const text = decoder.decode(merged);
      ALL_RECORDS = JSON.parse(text);
    }} else {{
      _setBoot('Loading projections…');
      _setDetail('Downloading record set');
      ALL_RECORDS = await res.json();
    }}

    _setBoot('Sorting projections…');
    _setDetail(`Ordering ${{ALL_RECORDS.length.toLocaleString()}} records by Inv Mgr → Brand → Customer → Mstyle`);
    await new Promise(r => setTimeout(r, 16));
    ALL_RECORDS.sort((a, b) => {{
      return _sortKey(a.inv_manager).localeCompare(_sortKey(b.inv_manager))
          || _sortKey(a.brand      ).localeCompare(_sortKey(b.brand      ))
          || _sortKey(a.cust       ).localeCompare(_sortKey(b.cust       ))
          || _sortKey(a.mstyle     ).localeCompare(_sortKey(b.mstyle     ));
    }});

    _setBoot('Building filters…');
    _setDetail('Indexing inventory managers, brands, and customers');
    await new Promise(r => setTimeout(r, 16));
    populateFilters();
    _seedFlagsFromQB();

    _setBoot('Rendering review table…');
    _setDetail(`${{ALL_RECORDS.length.toLocaleString()}} rows · paginated 100 per page`);
    await new Promise(r => setTimeout(r, 16));
    renderTable();

    const _ms = (performance.now() - _t0).toFixed(0);
    console.log(`Viewer bootstrap completed in ${{_ms}}ms`);
    _hideBoot();
  }} catch (e) {{
    _setBoot('Error loading projections');
    _setDetail((e && e.message ? e.message : String(e)) + ' · check the terminal running viewer.py');
    console.error('Bootstrap failed:', e);
  }}
}}
_bootstrap();
</script>

<div class="export-bar">
  <button class="export-btn" onclick="exportAllInView()" style="background:#1565c0;color:#fff;border-color:#1565c0">Export All in View to CSV</button>
  <button class="export-btn" onclick="exportFlagged()">Export Flagged to CSV</button>
  <button class="export-btn" onclick="clearAllFlags()" style="background:#fff;color:#c62828;border:1px solid #c62828">Clear Flags/Comments</button>
  <span class="export-count" id="flagCount">0 flagged</span>
  <span id="sendStatus" style="font-size:12px;color:#2e7d32;margin-left:10px"></span>
</div>

</body>
</html>"""
    # Cache the built HTML so subsequent GET / requests are instant.
    # Without this, every browser reload pays the full enrichment +
    # JSON dump + gzip cost (~1-2s for 4k records).
    print(f"  build_validation_page_html: built in {time.time() - _t_build:.2f}s "
          f"(html={len(_html)/1024:.0f}KB, records={len(_RECORDS_PAYLOAD_BYTES)/1024:.0f}KB raw, "
          f"{len(_RECORDS_PAYLOAD_GZIP)/1024 if _RECORDS_PAYLOAD_GZIP else 0:.0f}KB gzip)",
          flush=True)
    _HTML_CACHE = _html
    return _html


# ─── HTTP handler ─────────────────────────────────────────────────────────────

class Handler(BaseHTTPRequestHandler):

    def log_message(self, fmt, *args):
        # Suppress default per-request logging; only print errors
        if args and str(args[1]) not in ("200", "304"):
            print(f"  HTTP {args[1]} {args[0]}")

    def _send(self, code, content_type, body):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, data, code=200):
        self._send(code, "application/json", json.dumps(data))

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            # Always open the Projection Validation Viewer.
            # AI Forecast Review Viewer has been removed.
            self._send(200, "text/html; charset=utf-8", build_validation_page_html())
        elif self.path == "/api/records.json":
            # Serve the full record payload separately from the HTML so the
            # loading overlay paints instantly.  Prefer gzip when the client
            # accepts it (typical browsers do; ~80% size reduction on this
            # JSON shape).
            payload = _RECORDS_PAYLOAD_BYTES
            accept = (self.headers.get("Accept-Encoding") or "").lower()
            if _RECORDS_PAYLOAD_GZIP and "gzip" in accept:
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Encoding", "gzip")
                self.send_header("Content-Length", str(len(_RECORDS_PAYLOAD_GZIP)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(_RECORDS_PAYLOAD_GZIP)
            else:
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(payload)
        elif self.path == "/api/status":
            self._json({"accepted": [], "total": len(records_by_key)})
        elif self.path.startswith("/api/detail"):
            # Return detail for one record (fallback for records not preloaded in page JS)
            from urllib.parse import urlparse, parse_qs
            qs = parse_qs(urlparse(self.path).query)
            key = qs.get("key", [""])[0]
            rec = records_by_key.get(key)
            if rec is None:
                self._json({"error": "not found"}, 404)
            else:
                # suggested is now stored in validation_results.json — no CData call needed
                sug = rec.get("suggested")
                if sug is None:
                    sug = fetch_suggested_weeks(key)
                    rec["suggested"] = sug   # cache for session
                self._json({
                    "weeks":           rec.get("weeks", []),
                    "suggested":       sug,
                    "history_l26_shp": rec.get("history_l26_shp", rec.get("history_l26", [])),
                    "history_l26_ord": rec.get("history_l26_ord", []),
                })
        elif self.path.startswith("/api/suggested"):
            from urllib.parse import urlparse, parse_qs
            qs = parse_qs(urlparse(self.path).query)
            key = qs.get("key", [""])[0]
            self._json({"suggested": fetch_suggested_weeks(key)})
        elif self.path.startswith("/api/explain"):
            # D5: Anomaly explain endpoint -- returns the full rule firing trace
            # for one record so a planner can self-serve "why is W7 = 2,760?"
            from urllib.parse import urlparse, parse_qs
            qs = parse_qs(urlparse(self.path).query)
            key = qs.get("key", [""])[0]
            rec = records_by_key.get(key)
            if not rec:
                self._json({"error": f"no record for key {key}"})
            else:
                # Pull both the structured drivers (new fire() API) and the
                # legacy text drivers + rule_fires set for back-compat.
                meta = rec.get("meta", {})
                explain = {
                    "key":                key,
                    "model":              rec.get("model"),
                    "baseline_mode":      rec.get("baseline_mode") or meta.get("baseline_mode"),
                    "rule_fires":         rec.get("rule_fires", []),
                    "structured_drivers": meta.get("structured_drivers", []),
                    "drivers":            meta.get("drivers", []),
                    "fcst":               rec.get("fcst", []),
                    "manual":             rec.get("manual", []),
                    "alert":              rec.get("alert"),
                    "history_l13_ord":    (rec.get("history_l26_ord") or [])[-13:],
                    "history_l4_ord":     (rec.get("history_l26_ord") or [])[-4:],
                }
                self._json(explain)
        elif self.path.startswith("/api/comment-history"):
            # Two-table fetch: mgr/flag comments from [Projection Comments]
            # plus AI-adjustment comments from [AI Comments] (separate table).
            # Both within the lookback window, oldest-first.  The viewer JS
            # routes mgr_comments -> cmt-hist pane, ai_comments -> ai-hist pane.
            from urllib.parse import urlparse, parse_qs
            qs   = parse_qs(urlparse(self.path).query)
            key  = (qs.get("key",  [""])[0] or "").strip()
            try:
                days = int(qs.get("days", ["30"])[0])
            except Exception:
                days = 30
            if not key:
                self._json({"error": "missing key"}, 400); return
            key_esc = key.replace("'", "''")

            # Mgr/flag comments — Projection Comments
            sql_mgr = (
                "SELECT [Record ID#], [Date Created], [Note], [Flag] "
                "FROM [Quickbase1].[InventoryTrack].[Projection Comments] "
                f"WHERE [Acct#-MStyle] = '{key_esc}' "
                f"AND [Date Created] >= DATEADD(day, -{days}, GETDATE()) "
                "ORDER BY [Date Created] ASC"
            )
            # AI-adjustment comments — separate AI Comments table
            sql_ai = (
                "SELECT [Record ID#], [Date Created], [Note], [Author], [Ignored] "
                "FROM [Quickbase1].[InventoryTrack].[AI Comments] "
                f"WHERE [Acct#-MStyle] = '{key_esc}' "
                f"AND [Date Created] >= DATEADD(day, -{days}, GETDATE()) "
                "ORDER BY [Date Created] ASC"
            )

            mgr_comments, ai_comments = [], []
            try:
                rows = cdata_query(sql_mgr, f"comment-history mgr {key}")
                for r in rows or []:
                    mgr_comments.append({
                        "rid":  r.get("Record ID#") or r.get("Record_ID_") or r.get("Record ID") or "",
                        "ts":   r.get("Date Created") or r.get("Date_Created") or "",
                        "note": r.get("Note") or "",
                        "flag": r.get("Flag") or "",
                    })
            except Exception as e:
                # Don't 500 — surface partial data so the AI pane still works
                mgr_comments = [{"_error": str(e)}]
            try:
                rows = cdata_query(sql_ai, f"comment-history ai {key}")
                for r in rows or []:
                    author = r.get("Author") or ""
                    # Author may come back as a dict (User field) or string
                    if isinstance(author, dict):
                        author = author.get("name") or author.get("email") or ""
                    ignored_raw = r.get("Ignored")
                    ignored = (ignored_raw is True or ignored_raw == 1
                               or (isinstance(ignored_raw, str) and ignored_raw.lower() in ("true","1","yes")))
                    ai_comments.append({
                        "rid":     r.get("Record ID#") or r.get("Record_ID_") or r.get("Record ID") or "",
                        "ts":      r.get("Date Created") or r.get("Date_Created") or "",
                        "note":    r.get("Note") or "",
                        "author":  author,
                        "ignored": ignored,
                    })
            except Exception as e:
                ai_comments = [{"_error": str(e)}]
            # Backwards-compatible: keep "comments" key (combined) so older
            # frontend code doesn't break, but new fields are preferred.
            combined = []
            for c in mgr_comments:
                if "_error" not in c:
                    combined.append({**c, "kind": "mgr"})
            for c in ai_comments:
                if "_error" not in c:
                    combined.append({**c, "kind": "ai"})
            self._json({
                "comments":      combined,
                "mgr_comments":  mgr_comments,
                "ai_comments":   ai_comments,
            })
        else:
            self._send(404, "text/plain", "Not found")

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body   = self.rfile.read(length)
        try:
            payload = json.loads(body)
        except Exception:
            self._json({"error": "bad JSON"}, 400)
            return

        if self.path == "/api/use-ai":
            # Copy AI PRJ values → MAN (date-stamped) projection columns for one key
            key = payload.get("key", "")
            if not key:
                self._json({"error": "missing key"}, 400); return
            ai_cols = [f"AI_PRJ_W{w}" for w in range(1, 27)]
            sql = build_copy_to_manual_sql(key, ai_cols)
            ok  = cdata_update(sql, key)
            self._json({"ok": ok, "key": key, "source": "ai"})
        elif self.path == "/api/use-suggested":
            # Copy Suggested Projection values → MAN (date-stamped) columns
            key = payload.get("key", "")
            if not key:
                self._json({"error": "missing key"}, 400); return
            sug_cols = [f"Suggested_Projection_Wk{w}" for w in range(1, 27)]
            sql = build_copy_to_manual_sql(key, sug_cols)
            ok  = cdata_update(sql, key)
            self._json({"ok": ok, "key": key, "source": "suggested"})
        elif self.path == "/api/toggle-flag":
            # Write the boolean Flagged field (fid 1592) on Projections.
            # Replaces the localStorage-only flag — now shared across users.
            key  = (payload.get("key") or "").strip()
            flag = bool(payload.get("flagged"))
            if not key:
                self._json({"error": "missing key"}, 400); return
            sql = (f"UPDATE [Quickbase1].[InventoryTrack].[Projections] "
                   f"SET [Flagged] = {1 if flag else 0} "
                   f"WHERE [Acct_MStyle_Key_] = '{key.replace(chr(39), chr(39)+chr(39))}'")
            ok = cdata_update(sql, f"toggle-flag {key}")
            if not ok:
                self._json({"error": "Quickbase update failed"}, 500); return
            # Mirror to in-memory record so subsequent page loads reflect the new state
            rec = records_by_key.get(key)
            if rec is not None:
                rec["flagged_qb"] = flag
            self._json({"ok": True, "key": key, "flagged": flag})
        elif self.path == "/api/update-status-cust":
            # Write the Status_Cust field (fid 53) on Projections.  Used by the
            # inline-editable Status @ Cust dropdown in the table view.
            key   = (payload.get("key")   or "").strip()
            value = (payload.get("value") or "").strip()
            if not key:
                self._json({"error": "missing key"}, 400); return
            if len(value) > 80:
                self._json({"error": "value too long (>80 chars)"}, 400); return
            sql = (f"UPDATE [Quickbase1].[InventoryTrack].[Projections] "
                   f"SET [Status_Cust] = '{value.replace(chr(39), chr(39)+chr(39))}' "
                   f"WHERE [Acct_MStyle_Key_] = '{key.replace(chr(39), chr(39)+chr(39))}'")
            ok = cdata_update(sql, f"update-status-cust {key}")
            if not ok:
                self._json({"error": "Quickbase update failed"}, 500); return
            rec = records_by_key.get(key)
            if rec is not None:
                rec["asin_status"] = value
            self._json({"ok": True, "key": key, "value": value})
        elif self.path == "/api/update-pog":
            # Write either [POG Launch Date] or [POG End Date] on the
            # Projections table.  Empty value clears the date.  Used by the
            # inline-editable date inputs in the POG/ISO context block.
            key   = (payload.get("key")   or "").strip()
            which = (payload.get("which") or "").strip().lower()
            value = (payload.get("value") or "").strip()
            if not key:
                self._json({"error": "missing key"}, 400); return
            if which not in ("launch", "end"):
                self._json({"error": "which must be 'launch' or 'end'"}, 400); return
            # Validate the date if provided (ISO YYYY-MM-DD)
            if value:
                import re as _re_pog
                if not _re_pog.match(r"^\d{4}-\d{2}-\d{2}$", value):
                    self._json({"error": f"invalid date '{value}' — expected YYYY-MM-DD"}, 400); return
            col = "[POG Launch Date]" if which == "launch" else "[POG End Date]"
            set_clause = f"{col} = NULL" if not value else f"{col} = '{value}'"
            sql = (f"UPDATE [Quickbase1].[InventoryTrack].[Projections] "
                   f"SET {set_clause} "
                   f"WHERE [Acct_MStyle_Key_] = '{key.replace(chr(39), chr(39)+chr(39))}'")
            ok = cdata_update(sql, f"update-pog {which} {key}")
            if not ok:
                self._json({"error": "Quickbase update failed"}, 500); return
            rec = records_by_key.get(key)
            if rec is not None:
                if which == "launch": rec["pog_launch"] = value
                else:                 rec["pog_end"]    = value
            self._json({"ok": True, "key": key, "which": which, "value": value})
        elif self.path == "/api/update-store-count":
            # Write [Store Count] (fid 14) on the Projections table.  Empty
            # value (None) clears the cell; numeric values are clamped to >=0.
            key   = (payload.get("key") or "").strip()
            raw   = payload.get("value")
            if not key:
                self._json({"error": "missing key"}, 400); return
            if raw is None or raw == "":
                set_clause = "[Store Count] = NULL"
            else:
                try:
                    value = max(0, int(raw))
                except (TypeError, ValueError):
                    self._json({"error": f"invalid store count '{raw}'"}, 400); return
                set_clause = f"[Store Count] = {value}"
            sql = (f"UPDATE [Quickbase1].[InventoryTrack].[Projections] "
                   f"SET {set_clause} "
                   f"WHERE [Acct_MStyle_Key_] = '{key.replace(chr(39), chr(39)+chr(39))}'")
            ok = cdata_update(sql, f"update-store-count {key}")
            if not ok:
                self._json({"error": "Quickbase update failed"}, 500); return
            rec = records_by_key.get(key)
            if rec is not None:
                rec["store_count"] = 0 if (raw is None or raw == "") else int(raw)
            self._json({"ok": True, "key": key, "value": rec.get("store_count") if rec else None})
        elif self.path == "/api/ignore-ai-comment":
            # Set [Ignored]=true on a single AI Comments row so F58 stops
            # replaying it on future forecaster runs.  Audit trail preserved.
            try:
                rid = int(payload.get("rid") or 0)
            except Exception:
                rid = 0
            if not rid:
                self._json({"error": "missing rid"}, 400); return
            sql = (f"UPDATE [Quickbase1].[InventoryTrack].[AI Comments] "
                   f"SET [Ignored] = 1 "
                   f"WHERE [Record ID#] = {rid}")
            ok = cdata_update(sql, f"ignore-ai-comment {rid}")
            if not ok:
                self._json({"error": "Quickbase update failed"}, 500); return
            self._json({"ok": True, "rid": rid})
        elif self.path == "/api/ai-comment-add":
            # INSERT into the dedicated AI Comments table (NOT Projection
            # Comments).  Used by the codepage Apply-to-MAN button and by
            # viewer.py's Tell-AI flow.  [Ignored] defaults to false so F58
            # picks the row up on the next forecaster run.
            key   = (payload.get("key")   or "").strip()
            note  = (payload.get("note")  or "").strip()
            ignored_in = bool(payload.get("ignored", False))
            if not key or not note:
                self._json({"error": "missing key or note"}, 400); return
            note_esc = note.replace("'", "''")
            key_esc  = key.replace("'", "''")
            cols = "[Acct#-MStyle], [Note], [Ignored]"
            vals = f"'{key_esc}', '{note_esc}', {1 if ignored_in else 0}"
            sql  = (f"INSERT INTO [Quickbase1].[InventoryTrack].[AI Comments] "
                    f"({cols}) VALUES ({vals})")
            ok = cdata_update(sql, f"ai-comment-add {key}")
            if not ok:
                self._json({"error": "Quickbase insert failed"}, 500); return
            self._json({"ok": True, "key": key})
        elif self.path == "/api/add-comment":
            # INSERT a row into the Quickbase Projection Comments table.
            # Schema reference: bpt35zccg has columns Acct#-MStyle (text fk),
            #   Note (multi-line text), Flag (multi-choice), Date of Week (date).
            key  = (payload.get("key")  or "").strip()
            note = (payload.get("note") or "").strip()
            flag = (payload.get("flag") or "").strip()
            if not key or not note:
                self._json({"error": "missing key or note"}, 400); return
            # "Date of Week" defaults to today's Saturday (matches W1 convention)
            from datetime import date as _date, timedelta as _td
            _today = _date.today()
            _days_to_sat = (5 - _today.weekday()) % 7
            _dow = _today + _td(days=_days_to_sat)
            dow_str = _dow.strftime("%Y-%m-%d")
            # CData INSERT — escape single quotes in note text
            note_esc = note.replace("'", "''")
            key_esc  = key.replace("'", "''")
            cols = '[Acct#-MStyle], [Note], [Date of Week]'
            vals = f"'{key_esc}', '{note_esc}', '{dow_str}'"
            if flag:
                flag_esc = flag.replace("'", "''")
                cols += ', [Flag]'
                vals += f", '{flag_esc}'"
            sql = (f"INSERT INTO [Quickbase1].[InventoryTrack].[Projection Comments] "
                   f"({cols}) VALUES ({vals})")
            ok = cdata_update(sql, f"add-comment {key}")
            if not ok:
                self._json({"error": "Quickbase insert failed"}, 500); return
            # Update in-memory record so subsequent /api/detail or page reload
            # reflects the new comment without a full re-enrichment
            rec = records_by_key.get(key)
            if rec is not None:
                stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
                tag   = f" [{flag}]" if flag else ""
                rec["last_comment"] = f"{stamp} - you{tag}: {note[:200]}"
                rec["last_comment_date"] = datetime.now().isoformat()
            self._json({"ok": True, "key": key})
        else:
            self._send(404, "text/plain", "Not found")


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    global RESULTS_PATH

    p = argparse.ArgumentParser(description="Inventory Forecast Viewer")
    p.add_argument("--results", default="forecast_results.json",
                   help="Forecast JSON file (default: forecast_results.json)")
    p.add_argument("--port",    type=int, default=8765,
                   help="HTTP port (default: 8765)")
    p.add_argument("--no-browser", action="store_true",
                   help="Don't auto-open browser")
    p.add_argument("--no-enrich", action="store_true",
                   help="(deprecated, now default) Skip live CData enrichment — use cache only")
    p.add_argument("--enrich-live", action="store_true",
                   help="Opt back into live CData enrichment for missing-from-cache keys "
                        "(slow when CData is throttling — only run when needed)")
    p.add_argument("--no-refresh", action="store_true",
                   help="(deprecated, now default) Skip the auto re-validate-against-current-QB step")
    p.add_argument("--refresh", action="store_true",
                   help="Re-run the validator against current Quickbase before serving "
                        "(slow — only needed if the on-disk results file is stale)")
    args = p.parse_args()
    # Expose flag globally so load_results() / _enrich_from_quickbase() see it
    global SKIP_ENRICH_LIVE
    # Default = skip live (cache only). --enrich-live opts in. --no-enrich kept as no-op.
    SKIP_ENRICH_LIVE = not bool(args.enrich_live)

    # Resolve paths relative to the skill directory (parent of scripts/)
    skill_dir = Path(__file__).parent.parent
    RESULTS_PATH = str(Path(args.results) if Path(args.results).is_absolute()
                        else skill_dir / args.results)

    if not Path(RESULTS_PATH).exists():
        sys.exit(f"ERROR: Results file not found: {RESULTS_PATH}")

    # Refresh against current Quickbase only when --refresh is passed.
    # The forecaster pipeline writes a fresh forecast_results.json, so most
    # viewer launches don't need to re-pull. Default = skip (fast launch).
    # --no-refresh kept as a backwards-compatible no-op.
    if args.refresh and not args.no_refresh:
        _refresh_validation(RESULTS_PATH)

    load_results(RESULTS_PATH)

    # Eagerly build + cache the HTML page and gzipped record payload.  Without
    # this, the FIRST GET / request from the browser would block on enrichment
    # + JSON dump + gzip (~1-2s for 4k records), giving the user a long blank
    # tab before even the loading overlay paints.  Building now means the
    # overlay paints in <100ms on first load.
    print("  Pre-building HTML page and records payload...")
    _t0 = time.time()
    build_validation_page_html()
    print(f"  Pre-build complete in {time.time() - _t0:.2f}s")

    print("\n" + "=" * 60)
    print("  Projection Validation Viewer")
    print("=" * 60)

    # If the requested port is already in use, kill the prior process so we
    # don't end up with multiple viewer windows fighting for it.
    port = args.port
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        in_use = s.connect_ex(("127.0.0.1", port)) == 0
    if in_use:
        print(f"  Port {port} is in use — killing existing process...")
        try:
            out = subprocess.check_output(f"netstat -ano | findstr :{port}",
                                          shell=True, text=True)
            pids = set()
            for line in out.splitlines():
                parts = line.split()
                if parts and parts[-1].isdigit() and "LISTENING" in line:
                    pids.add(parts[-1])
            for pid in pids:
                subprocess.run(["taskkill", "/F", "/PID", pid],
                               capture_output=True)
            time.sleep(1)
        except Exception as e:
            print(f"  (Could not free port: {e})")

    url = f"http://127.0.0.1:{port}"
    print(f"  Serving      → {url}")
    print(f"  Press Ctrl+C to stop\n")

    # Open browser once after server is up (unless --no-browser)
    if not args.no_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()

    server = HTTPServer(("127.0.0.1", port), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  Server stopped.")


if __name__ == "__main__":
    main()
