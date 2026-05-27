"""
ai_training_review.py
---------------------
Daily pipeline: Fetch unreviewed "AI Training" Projection Comments, deep-analyze
the planner's correction vs the current AI model, propose concrete rule changes,
save a markdown report, and email a summary with review instructions.

Usage:
    python scripts/ai_training_review.py [--days N] [--dry-run] [--reset]

    --days N     Look back N days for AI Training comments (default: 30)
    --dry-run    Analyze but skip email and processed-ID update
    --reset      Clear processed-IDs cache so everything is reprocessed

QB connections (all REST API, no CData):
    bpt35zccg  Projection Comments  (source of AI Training flags)
    bpd237tvm  Projections          (AI PRJ, MAN PRJ, model, order history)

Output:
    analysis/ai_training_YYYY-MM-DD.md   Full report
    analysis/ai_training_processed.json  State: processed comment Record IDs
"""

import sys
import os
import re
import json
import time
import argparse
import urllib.request
import urllib.error
from datetime import datetime, timedelta, date
from pathlib import Path
from collections import defaultdict

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
QB_REALM       = "pim.quickbase.com"
QB_TOKEN       = "b39re4_mkf7_du2buby24kr7d4hkcu9cpxn69s"
QB_BASE        = "https://api.quickbase.com/v1"

COMMENTS_TABLE = "bpt35zccg"   # Projection Comments (child)
PROJ_TABLE     = "bpd237tvm"   # Projections (parent)

HEADERS = {
    "QB-Realm-Hostname": QB_REALM,
    "Authorization":     f"QB-USER-TOKEN {QB_TOKEN}",
    "Content-Type":      "application/json",
}

# Projection Comments FIDs (static)
C_RECORD_ID   = 3
C_DATE        = 1
C_ACCT_MSTYLE = 7
C_FLAG        = 31
# C_NOTE discovered at runtime via _discover_note_fid()

# Projections FIDs (static)
P_KEY         = 292   # Acct_MStyle_Key_
P_STATUS      = 10    # Status @ Cust
P_CUSTOMER    = 363
P_BRAND       = 197
P_MSTYLE      = 196
P_ITEM_STATUS = 374
P_INV_MGR     = 936
P_AI_MODEL    = 1580  # AI model written back by forecaster
P_L13W        = 1593  # Ord/Wk L13w (numeric)
P_L4W_FID     = None  # discovered dynamically (label "Ord/Wk L4w")
AI_PRJ_FIDS   = list(range(1511, 1537))           # W1-W26
ORD_FIDS      = [457] + list(range(464, 489))     # Ord_LW + Ord_LW_1..25

# MAN PRJ FIDs discovered at runtime (date-rolling labels like "05 26 W1")
MAN_FID_RE    = re.compile(r'^\d{2} \d{2} W(\d+)$')

SCRIPT_DIR    = Path(__file__).parent
ANALYSIS_DIR  = SCRIPT_DIR.parent / "analysis"
STATE_FILE    = ANALYSIS_DIR / "ai_training_processed.json"
ANALYSIS_DIR.mkdir(exist_ok=True)

RECIPIENT     = "s.shweky@petspeople.com"

# Intent keywords
EOL_RE      = re.compile(r'\beol\b|wind.?down|discontinu|phase.?out|end.of.life|last.order|closing.out|deleting|delete', re.I)
ZERO_RE     = re.compile(r'\bzero\b|no.orders?|covered.by.po|po.covers?|cancel.all|set.to.zero', re.I)
INCREASE_RE = re.compile(r'\bincrease\b|boost|bump|lift|ramp.up|new.customer|new.distribution|dist.gain|adding|expand|grow', re.I)
DECREASE_RE = re.compile(r'\bdecrease\b|cut\b|reduc|lost.customer|lost.distrib|cancel|lower|pull.back|slow|soften|anomaly|one.time|spike', re.I)
LAUNCH_RE   = re.compile(r'\blaunch\b|new.item|new.sku|pre.launch|first.order|initial.order', re.I)
MODEL_RE    = re.compile(r'wrong.model|bad.model|should.be.seasonal|use.pos|use.history|use.amazon|not.seasonal|makes.no.sense|doesn.t.make.sense|look.at.the.order|look.at.history|incorrect.model|flat.demand|flat.forecast', re.I)


# ---------------------------------------------------------------------------
# QB REST helpers
# ---------------------------------------------------------------------------
def _qb_post(path, body, timeout=60):
    url  = f"{QB_BASE}/{path}"
    data = json.dumps(body).encode()
    req  = urllib.request.Request(url, data=data, headers=HEADERS, method="POST")
    for attempt in range(1, 4):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            if attempt == 3 or e.code not in (429, 502, 504):
                raise
        except Exception:
            if attempt == 3:
                raise
        time.sleep(2 ** attempt)


def _qb_get(path, params=None):
    from urllib.parse import urlencode
    url = f"{QB_BASE}/{path}"
    if params:
        url += "?" + urlencode(params)
    req = urllib.request.Request(url, headers=HEADERS, method="GET")
    for attempt in range(1, 4):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            if attempt == 3 or e.code not in (429, 502, 504):
                raise
        except Exception:
            if attempt == 3:
                raise
        time.sleep(2 ** attempt)


def fval(rec, fid):
    v = (rec.get(str(fid)) or {}).get("value")
    if v is None:
        return 0.0
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def sval(rec, fid):
    v = (rec.get(str(fid)) or {}).get("value")
    if isinstance(v, dict):
        return v.get("name") or v.get("email") or v.get("id") or ""
    return str(v) if v not in (None, "") else ""


# ---------------------------------------------------------------------------
# Field discovery
# ---------------------------------------------------------------------------
def _discover_note_fid():
    """Return the FID of the Note field on the Projection Comments table."""
    fields = _qb_get("fields", {"tableId": COMMENTS_TABLE})
    for f in fields:
        if f.get("label", "").strip().lower() == "note":
            return f["id"]
    # Fallback: look for any multi-line text field
    for f in fields:
        if f.get("fieldType", "") in ("text-multi-line", "text"):
            lbl = f.get("label", "")
            if "note" in lbl.lower() or "comment" in lbl.lower():
                return f["id"]
    return None


def _discover_man_fids():
    """Return dict {week_num(1-26): fid} for the current rolling MAN PRJ cols."""
    fields = _qb_get("fields", {"tableId": PROJ_TABLE})
    result = {}
    for f in fields:
        m = MAN_FID_RE.match(f.get("label", ""))
        if m:
            w = int(m.group(1))
            if 1 <= w <= 26:
                result[w] = f["id"]
    return result


def _discover_l4w_fid():
    """Return the FID of the Ord/Wk L4w numeric field on Projections."""
    fields = _qb_get("fields", {"tableId": PROJ_TABLE})
    candidates = []
    for f in fields:
        lbl = f.get("label", "").strip().lower()
        ftype = f.get("fieldType", "")
        # Match any numeric field whose label contains "l4w" and not "ord_lw"
        if "l4w" in lbl and ftype in ("numeric", "duration"):
            candidates.append((f["id"], f.get("label", "")))
    if candidates:
        # Prefer the one that also contains "ord" or "wk"
        for fid, lbl in candidates:
            if "ord" in lbl.lower() or "wk" in lbl.lower():
                return fid
        return candidates[0][0]
    return None


# ---------------------------------------------------------------------------
# Step 1 -- Fetch unreviewed AI Training comments
# ---------------------------------------------------------------------------
def fetch_ai_training_comments(days, note_fid):
    """Page through Projection Comments WHERE Flag = 'AI Training' in last N days.

    Already-reviewed comments have FLAG='Reviewed' in QB and are excluded
    naturally by the query -- no local state file needed.
    """
    cutoff_iso = (date.today() - timedelta(days=days)).isoformat()
    rows, skip = [], 0
    select_fids = [C_RECORD_ID, C_DATE, C_ACCT_MSTYLE, C_FLAG]
    if note_fid:
        select_fids.append(note_fid)

    print(f"  Fetching AI Training comments (last {days} days, cutoff {cutoff_iso})...")
    while True:
        result = _qb_post("records/query", {
            "from":   COMMENTS_TABLE,
            "select": select_fids,
            "where":  f"{{31.EX.'AI Training'}}AND{{{C_DATE}.AF.'{cutoff_iso}'}}",
            "sortBy": [{"fieldId": C_DATE, "order": "ASC"}],
            "options": {"top": 1000, "skip": skip},
        })
        batch = result.get("data", [])
        rows.extend(batch)
        if len(batch) < 1000:
            break
        skip += 1000
        time.sleep(0.2)

    print(f"  Found {len(rows)} unreviewed AI Training comments.")
    return rows, note_fid


# ---------------------------------------------------------------------------
# Step 2 -- Fetch projection records for the comment keys
# ---------------------------------------------------------------------------
def fetch_projections(keys, man_fids, l4w_fid):
    """Batch-fetch projection records for the given Acct_MStyle_Key_ values."""
    if not keys:
        return {}

    select_fids = (
        [P_KEY, P_STATUS, P_CUSTOMER, P_BRAND, P_MSTYLE, P_ITEM_STATUS,
         P_INV_MGR, P_AI_MODEL, P_L13W]
        + AI_PRJ_FIDS
        + ORD_FIDS
        + list(man_fids.values())
    )
    if l4w_fid:
        select_fids.append(l4w_fid)

    print(f"  Fetching {len(keys)} projection records...")
    recs = {}
    batch_size = 100  # conservative per QB WHERE-clause limit
    key_list = list(keys)

    for i in range(0, len(key_list), batch_size):
        batch = key_list[i:i + batch_size]
        in_clause = "OR".join(f"{{{P_KEY}.EX.'{k}'}}" for k in batch)
        where = f"({in_clause})"
        result = _qb_post("records/query", {
            "from":    PROJ_TABLE,
            "select":  select_fids,
            "where":   where,
            "options": {"top": batch_size + 10},
        })
        for rec in result.get("data", []):
            key = sval(rec, P_KEY)
            if key:
                recs[key] = rec
        if i + batch_size < len(key_list):
            time.sleep(0.1)

    print(f"  Loaded {len(recs)} projection records.")
    return recs


# ---------------------------------------------------------------------------
# Step 3 -- Analyze each comment
# ---------------------------------------------------------------------------
def classify_intent(note):
    """Return primary planner intent from comment text."""
    n = note or ""
    if EOL_RE.search(n):
        return "eol"
    if ZERO_RE.search(n):
        return "zero"
    if LAUNCH_RE.search(n):
        return "launch"
    if MODEL_RE.search(n):
        return "wrong_model"
    if INCREASE_RE.search(n):
        return "increase"
    if DECREASE_RE.search(n):
        return "decrease"
    return "unknown"


def assess_model_fit(intent, ai_model, ai_total, man_total, l13w, l4w, item_status):
    """
    Return (fit: str, diagnosis: str) assessing whether the current model
    is appropriate given the planner's comment intent.

    fit values: "correct", "over_projecting", "under_projecting",
                "wrong_model", "missed_lifecycle", "needs_context"
    """
    model_lo = (ai_model or "").lower()
    gap_pct  = ((man_total - ai_total) / max(ai_total, 1)) * 100 if ai_total else 0
    # Only compute trend when we have a real L4W value (FID found + non-zero)
    trend    = (l4w / l13w) if (l13w > 0 and l4w > 0) else None

    trend_str = f"{trend:.2f}x" if trend is not None else "n/a"

    if intent == "eol":
        if ai_total > 0:
            return ("missed_lifecycle",
                    f"Model ({ai_model}) still projects {ai_total:,}u but planner is "
                    f"winding down. L4W/L13W trend = {trend_str}. "
                    f"EOL detection not triggering.")
        return ("correct", "Model already at zero -- may be resolved.")

    if intent == "zero":
        if ai_total > 0:
            return ("over_projecting",
                    f"Model projects {ai_total:,}u; planner wants zero. "
                    f"Likely PO-covered or closeout item.")
        return ("correct", "Model already at zero.")

    if intent == "launch":
        if ai_total < man_total * 0.5:
            return ("under_projecting",
                    f"Pre-launch/ramp: AI={ai_total:,}u vs MAN={man_total:,}u "
                    f"({gap_pct:+.0f}%). Model may lack launch-ramp signal.")
        return ("needs_context",
                f"Launch item. AI={ai_total:,}u vs MAN={man_total:,}u.")

    if intent == "wrong_model":
        return ("wrong_model",
                f"Planner flagged model ({ai_model}) as inappropriate. "
                f"AI={ai_total:,}u vs MAN={man_total:,}u ({gap_pct:+.0f}%).")

    if intent == "increase":
        if ai_total < man_total * 0.85:
            return ("under_projecting",
                    f"AI={ai_total:,}u vs MAN={man_total:,}u ({gap_pct:+.0f}%). "
                    f"Planner boosted -- distribution gain or event not in model.")
        return ("needs_context",
                f"Planner increased slightly (gap {gap_pct:+.0f}%). Within tolerance.")

    if intent == "decrease":
        if ai_total > man_total * 1.15:
            # Check if trend is declining (only when L4W data available)
            if trend is not None and trend < 0.80:
                return ("over_projecting",
                        f"AI={ai_total:,}u vs MAN={man_total:,}u ({gap_pct:+.0f}%). "
                        f"L4W/L13W={trend:.2f} -- declining trend not reflected in model.")
            trend_note = f"L4W/L13W={trend:.2f}" if trend is not None else "L4W unavailable"
            return ("over_projecting",
                    f"AI={ai_total:,}u vs MAN={man_total:,}u ({gap_pct:+.0f}%). "
                    f"Model over-projects. {trend_note}.")
        return ("needs_context",
                f"Small decrease (gap {gap_pct:+.0f}%). May be minor planner adjustment.")

    return ("unknown", f"Intent unclear. AI={ai_total:,}u vs MAN={man_total:,}u.")


def generate_recommendation(intent, fit, ai_model, diagnosis, note):
    """
    Return a dict with keys: change_type, proposed_change, confidence, rationale.
    Maps (intent, fit, model) -> specific rule/threshold change proposal.
    """
    model_lo = (ai_model or "").lower()
    is_amz   = "pos-wos" in model_lo or "amazon" in model_lo
    is_seas  = "seasonal" in model_lo
    is_crost = "croston" in model_lo
    is_heur  = "heuristic" in model_lo or ("seasonal" not in model_lo
                                            and "croston" not in model_lo
                                            and "pos" not in model_lo
                                            and "launch" not in model_lo)

    if intent == "eol" and fit == "missed_lifecycle":
        if is_amz:
            return dict(
                change_type="threshold",
                proposed_change=(
                    "Lower F87 deceleration threshold from 0.80 to 0.65 for EOL detection, "
                    "OR add an explicit EOL gate: if L4W/L13W < 0.50 AND L13W < 200/wk, "
                    "set forecast = L4W (decelerating to zero)."
                ),
                confidence="high",
                rationale="Amazon POS-WOS model anchors to L13W; F87 fires at 0.80 "
                          "but EOL items often need harder suppression at 0.65 or below.",
            )
        return dict(
            change_type="new_rule",
            proposed_change=(
                "Add non-Amazon deceleration guard (mirror of F87): "
                "if L4W/L13W < 0.65 for non-Amazon replen items, anchor base = L4W "
                "instead of L13W. Currently F87 only applies to Amazon POS-WOS path."
            ),
            confidence="high",
            rationale="Heuristic/Seasonal models still anchor to L13W on declining "
                      "non-Amazon items. A symmetric guard would catch EOL earlier.",
        )

    if intent == "zero" and fit == "over_projecting":
        note_lo = (note or "").lower()
        if "po" in note_lo or "covered" in note_lo:
            return dict(
                change_type="suppression",
                proposed_change=(
                    "Confirm VP-Q4 forward-PO zeroing is working for this account. "
                    "If PO exists but W1 is not zeroed, check bucket logic in vp_q4 module. "
                    "Consider extending zero window when PO covers all 26 weeks."
                ),
                confidence="medium",
                rationale="'PO covers' comment suggests VP-Q4 logic may not be "
                          "zeroing the full window or the PO is future-dated beyond lookback.",
            )
        return dict(
            change_type="channel_suppression",
            proposed_change=(
                "Review OFFPRICE_CUST_SUBSTRS list in config.py -- if this customer "
                "is closeout/opportunistic and not already in the list, add it. "
                "Also check if Kill Pattern (planner zeroes AI) should trigger a "
                "permanent suppression flag on this key."
            ),
            confidence="medium",
            rationale="Planner zeroing all weeks is the strongest signal that AI "
                      "should not project for this account/item combination.",
        )

    if intent == "launch" and fit == "under_projecting":
        return dict(
            change_type="model_switch",
            proposed_change=(
                "Verify F72 pre-launch ramp detection is active for this item. "
                "If item has <4 weeks of order history, F72 should be routing it to "
                "Pre-launch model. If bypassed, check Item_Status = 'NEW' gate. "
                "Consider adding a distribution-gain multiplier when L4W/L13W > 1.5x."
            ),
            confidence="medium",
            rationale="Launch items need forward-looking ramp, not history-based "
                      "projection. Pre-launch model should fire when Status = NEW.",
        )

    if intent == "wrong_model":
        note_lo = (note or "").lower()
        if "seasonal" in note_lo:
            return dict(
                change_type="model_switch",
                proposed_change=(
                    f"Planner says model should be Seasonal but current model is {ai_model}. "
                    "Check if item's category matches any key in CATEGORY_PROFILES or "
                    "derived_category_profiles.json. If not matched, add category keyword. "
                    "Also verify item has >= 8 consistent weeks of history (profile gate)."
                ),
                confidence="medium",
                rationale="Seasonal model gate may be excluding the item due to "
                          "insufficient history or missing category keyword.",
            )
        if "flat" in note_lo or "look at" in note_lo or "order history" in note_lo:
            return dict(
                change_type="model_switch",
                proposed_change=(
                    f"Planner says {ai_model} is projecting flat demand when order "
                    "history shows a different pattern. "
                    "Check: (1) Is this a Heuristic fallback that should be Seasonal or "
                    "Croston's? (2) Does the item have seasonal keywords in its description? "
                    "(3) Is the L13W avg being used as a flat baseline when L26/L52 shows "
                    "trend? Consider using max(L4W, L13W, trend_projection) as baseline "
                    "for Heuristic path when growth is detected."
                ),
                confidence="medium",
                rationale="Flat-demand Heuristic is appropriate for stable items but "
                          "misses items with clear cyclical or trending order patterns.",
            )
        return dict(
            change_type="model_switch",
            proposed_change=(
                f"Review why model selected {ai_model} for this item. "
                "Check model selection logic in forecast_record(): "
                "Croston's fires for sparse/intermittent; Seasonal for category-matched; "
                "POS-WOS for Amazon with POS data; Heuristic is the fallback. "
                "Planner's preferred model should be documented in a comment tag."
            ),
            confidence="low",
            rationale="Model selection depends on data availability and item "
                      "classification. Manual override via F58 Tell-AI comment "
                      "is the fastest path while model logic is investigated.",
        )

    if intent == "increase" and fit == "under_projecting":
        return dict(
            change_type="threshold",
            proposed_change=(
                "AI under-projects vs planner boost. Likely causes: "
                "(1) New distribution not yet in L13W history -- check if L4W is spiking. "
                "If L4W/L13W > 1.5x, consider anchoring base to L4W x recent_weeks_ratio. "
                "(2) Event/promo not in model -- planner should use F58 Tell-AI comment "
                "to log the event. "
                "(3) Seasonal model damping -- lower DAMP_NORMAL from 0.30 if seasonal "
                "items are consistently under-predicted."
            ),
            confidence="medium",
            rationale="Consistent planner upward adjustments signal the model is "
                      "systematically conservative on growing items.",
        )

    if intent == "decrease" and fit == "over_projecting":
        note_lo = (note or "").lower()
        if is_crost and ("anomaly" in note_lo or "one.time" in note_lo
                         or "spike" in note_lo or "anomaly" in note_lo):
            return dict(
                change_type="threshold",
                proposed_change=(
                    "Croston's model ingested a one-time spike order as a demand signal. "
                    "Fix: add outlier-trimming to Croston's history input -- if any single "
                    "week >= 3x the L13W mean, cap that week at the L13W mean before "
                    "feeding Croston's. Alternatively, add a post-Croston guard: "
                    "if Croston output > L13W * 2.5x, cap at L13W * 1.5x."
                ),
                confidence="high",
                rationale="Croston's is designed for sparse/intermittent demand. "
                          "A large one-time order inflates the z (demand-per-event) "
                          "estimate for all future forecasts until history rolls off.",
            )
        return dict(
            change_type="threshold",
            proposed_change=(
                "AI over-projects vs planner cut. Check: "
                "(1) L4W/L13W trend -- if < 0.80, F87 should fire (Amazon) or "
                "add symmetric guard for non-Amazon. "
                "(2) If planner is cutting due to lost distribution, add L4W/L13W "
                "< 0.65 check that anchors base = L4W across ALL model paths. "
                "(3) If customer is declining overall, consider customer-level "
                "trend multiplier in the baseline computation."
            ),
            confidence="high",
            rationale="Over-projection on declining items is the most common "
                      "pattern and has the largest inventory risk.",
        )

    return dict(
        change_type="investigate",
        proposed_change=(
            "Pattern requires manual review. Check the comment text, AI model, "
            "and MAN vs AI projection detail in the QB viewer."
        ),
        confidence="low",
        rationale="Insufficient signal to generate specific recommendation.",
    )


def analyze_comment(comment, projection, man_fids, note_fid, l4w_fid):
    """Full deep-analysis of a single AI Training comment. Returns analysis dict."""
    key  = sval(comment, C_ACCT_MSTYLE)
    note = sval(comment, note_fid) if note_fid else ""
    ts   = sval(comment, C_DATE)

    if not projection:
        return {
            "key": key, "note": note, "ts": ts,
            "intent": "unknown", "fit": "no_projection",
            "diagnosis": "No matching projection record found.",
            "recommendation": {"change_type": "investigate",
                               "proposed_change": "Projection not found -- "
                                                  "may be inactive or deleted.",
                               "confidence": "low", "rationale": ""},
            "ai_model": "", "ai_total": 0, "man_total": 0,
            "l13w": 0, "l4w": 0, "unit_gap": 0,
        }

    ai_model = sval(projection, P_AI_MODEL)
    l13w     = fval(projection, P_L13W)
    l4w      = fval(projection, l4w_fid) if l4w_fid else 0.0

    ai_prj   = [fval(projection, fid) for fid in AI_PRJ_FIDS]
    man_prj  = [fval(projection, fid) for w, fid in sorted(man_fids.items())]
    ai_total  = int(sum(ai_prj))
    man_total = int(sum(man_prj))
    unit_gap  = man_total - ai_total

    customer    = sval(projection, P_CUSTOMER)
    brand       = sval(projection, P_BRAND)
    mstyle      = sval(projection, P_MSTYLE)
    item_status = sval(projection, P_ITEM_STATUS)
    mgr         = sval(projection, P_INV_MGR)

    intent = classify_intent(note)
    fit, diagnosis = assess_model_fit(
        intent, ai_model, ai_total, man_total, l13w, l4w, item_status)
    rec = generate_recommendation(intent, fit, ai_model, diagnosis, note)

    return {
        "key": key, "customer": customer, "brand": brand,
        "mstyle": mstyle, "item_status": item_status, "mgr": mgr,
        "note": note, "ts": ts,
        "intent": intent, "fit": fit, "diagnosis": diagnosis,
        "recommendation": rec,
        "ai_model": ai_model,
        "ai_total": ai_total, "man_total": man_total,
        "l13w": round(l13w, 1), "l4w": round(l4w, 1),
        "unit_gap": unit_gap,
    }


# ---------------------------------------------------------------------------
# Step 4 -- Aggregate findings
# ---------------------------------------------------------------------------
def aggregate(analyses):
    """Group analyses by (intent, fit) and sum unit gaps. Returns sorted groups."""
    groups = defaultdict(lambda: {"items": [], "unit_gap": 0, "count": 0})
    for a in analyses:
        k = (a["intent"], a["fit"])
        groups[k]["items"].append(a)
        groups[k]["unit_gap"] += a["unit_gap"]
        groups[k]["count"]    += 1

    # Sort by |unit_gap| descending
    return sorted(groups.items(), key=lambda x: abs(x[1]["unit_gap"]), reverse=True)


# ---------------------------------------------------------------------------
# Step 4b -- Estimate systemic impact across ALL active projections
# ---------------------------------------------------------------------------
def estimate_systemic_impact(all_recs):
    """
    For each unique (model_keyword, change_type, fit) in the recommendations,
    query ALL active Projections with that model and compute:
      - Total record count in scope
      - Total AI PRJ 26-week units across those records
      - Estimated affected count + direction based on fit + detection criteria

    Returns list of dicts keyed by rec_num.
    """
    # Build a map: rec_num -> (model_keyword, fit, change_type, confidence)
    impacts = {}
    queried_models = {}   # cache: model_keyword -> (count, ai_total, criteria_count)

    # Detection criteria per change_type + fit:
    # We fetch L13W (1593) + L4W (1417) + order history to apply criteria.
    # ord_fids[0] = most recent week (Ord_LW); we look for spike weeks.

    def _fetch_model_scope(model_keyword, change_type, fit):
        """Query all active projections where AI_MODEL contains model_keyword."""
        cache_key = model_keyword.lower()
        if cache_key in queried_models:
            return queried_models[cache_key]

        select_fids = [P_KEY, P_AI_MODEL, P_L13W, 1417] + AI_PRJ_FIDS + ORD_FIDS[:13]
        # Filter: active status AND model contains keyword
        # QB: {10.SW.'A'} AND {1580.CT.'<keyword>'}
        kw_safe = model_keyword.replace("'", "''")
        where   = f"{{10.SW.'A'}}AND{{1580.CT.'{kw_safe}'}}"

        rows, skip = [], 0
        try:
            while True:
                result = _qb_post("records/query", {
                    "from":    PROJ_TABLE,
                    "select":  select_fids,
                    "where":   where,
                    "options": {"top": 2000, "skip": skip},
                })
                batch = result.get("data", [])
                rows.extend(batch)
                if len(batch) < 2000:
                    break
                skip += 2000
                time.sleep(0.15)
        except Exception as e:
            print(f"  [WARN] Systemic impact fetch for '{model_keyword}' failed: {e}")
            queried_models[cache_key] = (0, 0, 0)
            return (0, 0, 0)

        total_count = len(rows)
        ai_total    = sum(
            sum(fval(r, fid) for fid in AI_PRJ_FIDS)
            for r in rows
        )

        # Apply change-specific detection criteria to count "directly affected"
        criteria_count = 0
        for r in rows:
            l13w   = fval(r, P_L13W)
            l4w    = fval(r, 1417)   # L4W FID discovered earlier, hardcode 1417 here
            ord_wk = [fval(r, fid) for fid in ORD_FIDS[:13]]
            trend  = l4w / l13w if l13w > 0 else 1.0

            if change_type == "threshold" and fit == "over_projecting":
                # Croston's anomaly: any order week >= 3x L13W
                if l13w > 0 and any(w >= l13w * 3 for w in ord_wk):
                    criteria_count += 1
                # Non-Croston over-projection: L4W/L13W < 0.80 (declining)
                elif "croston" not in model_keyword.lower() and l4w > 0 and trend < 0.80:
                    criteria_count += 1
            elif change_type == "threshold" and fit == "under_projecting":
                # Under-projection: L4W/L13W > 1.15 (growing) with no spike
                if l4w > 0 and trend > 1.15:
                    criteria_count += 1
            elif change_type == "model_switch":
                # Heuristic flat: L4W and L13W diverge meaningfully (trend != 1)
                if l4w > 0 and (trend > 1.20 or trend < 0.80):
                    criteria_count += 1
            elif change_type == "missed_lifecycle":
                # EOL missed: L4W/L13W < 0.65 and AI still projecting
                ai_sum = sum(fval(r, fid) for fid in AI_PRJ_FIDS)
                if l4w > 0 and trend < 0.65 and ai_sum > 0:
                    criteria_count += 1
            else:
                criteria_count = total_count  # investigate -- all in scope

        queried_models[cache_key] = (total_count, int(ai_total), criteria_count)
        return queried_models[cache_key]

    print("  Computing systemic impact across all active projections...", flush=True)
    results = []
    seen_keywords = set()

    for num, intent, fit, rec, unit_impact, count in all_recs:
        # Derive model keyword from the analyses that triggered this rec
        model_keyword = ""
        if "Croston" in rec["proposed_change"]:
            model_keyword = "Croston"
        elif "Heuristic" in rec["proposed_change"] or "heuristic" in rec["proposed_change"].lower():
            model_keyword = "Heuristic"
        elif "POS-WOS" in rec["proposed_change"] or "Amazon POS" in rec["proposed_change"]:
            model_keyword = "POS-WOS"
        elif "Seasonal" in rec["proposed_change"]:
            model_keyword = "Seasonal"

        if not model_keyword:
            results.append({
                "rec_num": num, "model_keyword": "all",
                "scope_count": 0, "scope_ai_total": 0,
                "criteria_count": 0, "direction": "unknown",
            })
            continue

        if model_keyword in seen_keywords:
            # Reuse cached result
            sc, at, cc = queried_models.get(model_keyword.lower(), (0, 0, 0))
        else:
            seen_keywords.add(model_keyword)
            sc, at, cc = _fetch_model_scope(model_keyword, rec["change_type"], fit)
            print(f"    {model_keyword}: {sc:,} records, {at:,} AI units, "
                  f"{cc:,} match detection criteria", flush=True)

        direction = (
            "down" if fit in ("over_projecting", "missed_lifecycle") else
            "up"   if fit == "under_projecting" else
            "mixed"
        )
        results.append({
            "rec_num":       num,
            "model_keyword": model_keyword,
            "scope_count":   sc,
            "scope_ai_total": at,
            "criteria_count": cc,
            "direction":     direction,
        })

    return results


# ---------------------------------------------------------------------------
# Step 5 -- Generate markdown report
# ---------------------------------------------------------------------------
def build_report(analyses, grouped, run_date, days):
    lines = [
        f"# AI Training Comment Review",
        f"**Generated:** {run_date}",
        f"**Lookback:** {days} days",
        f"**Comments analyzed:** {len(analyses)}",
        "",
        "---",
        "",
        "## 1. Executive Summary",
        "",
    ]

    total_gap = sum(a["unit_gap"] for a in analyses)
    over_count  = sum(1 for a in analyses if a["unit_gap"] < -100)
    under_count = sum(1 for a in analyses if a["unit_gap"] > 100)
    eol_count   = sum(1 for a in analyses if a["intent"] == "eol")
    zero_count  = sum(1 for a in analyses if a["intent"] == "zero")

    lines += [
        f"| Metric | Value |",
        f"|---|---|",
        f"| Total comments | {len(analyses)} |",
        f"| Net unit gap (MAN - AI) | {total_gap:+,} |",
        f"| AI over-projects (planner cut >100u) | {over_count} |",
        f"| AI under-projects (planner boosted >100u) | {under_count} |",
        f"| EOL / wind-down signals | {eol_count} |",
        f"| Zero-out signals | {zero_count} |",
        "",
        "## 2. Pattern Groups (sorted by unit impact)",
        "",
        "| Intent | Model Fit | Count | Unit Gap | Primary Recommendation |",
        "|---|---|---|---|---|",
    ]
    for (intent, fit), grp in grouped:
        if grp["items"]:
            rec = grp["items"][0]["recommendation"]
            short = rec["proposed_change"][:80].rstrip() + "..."
            lines.append(
                f"| {intent} | {fit} | {grp['count']} | "
                f"{grp['unit_gap']:+,} | {short} |"
            )

    lines += [
        "",
        "## 3. Proposed Model Changes",
        "",
    ]

    # Deduplicate recommendations by change_type + proposed_change[:60]
    seen_recs = set()
    rec_num   = 0
    all_recs  = []
    for (intent, fit), grp in grouped:
        if not grp["items"]:
            continue
        rec = grp["items"][0]["recommendation"]
        key = rec["proposed_change"][:60]
        if key in seen_recs:
            continue
        seen_recs.add(key)
        rec_num += 1
        impact  = grp["unit_gap"]
        conf    = rec["confidence"].upper()
        all_recs.append((rec_num, intent, fit, rec, impact, grp["count"]))

        lines += [
            f"### [{rec_num}] {rec['change_type'].replace('_', ' ').title()} "
            f"-- {intent.upper()} / {fit.replace('_', ' ')}",
            f"**Impact:** {impact:+,} units across {grp['count']} item(s)  "
            f"| **Confidence:** {conf}",
            "",
            f"**Proposed Change:**  ",
            rec["proposed_change"],
            "",
            f"**Rationale:** {rec['rationale']}",
            "",
            f"**Affected items:**",
        ]
        for a in grp["items"][:5]:
            gap_str = f"{a['unit_gap']:+,}u"
            lines.append(
                f"- `{a['key']}` ({a['customer'][:30]} / {a['brand'][:20]}) "
                f"Model: {a['ai_model']} | Gap: {gap_str}  "
                f'Comment: "{a["note"][:80]}"'
            )
        if len(grp["items"]) > 5:
            lines.append(f"- ... and {len(grp['items']) - 5} more")
        lines.append("")

    lines += [
        "## 4. Comment Detail",
        "",
        "| Key | Customer | Model | Intent | Fit | AI 26w | MAN 26w | Gap | Comment |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for a in sorted(analyses, key=lambda x: abs(x["unit_gap"]), reverse=True):
        note_short = (a["note"] or "")[:60].replace("|", "/")
        lines.append(
            f"| {a['key']} | {a.get('customer','')[:25]} | {a['ai_model'][:20]} | "
            f"{a['intent']} | {a['fit']} | {a['ai_total']:,} | "
            f"{a['man_total']:,} | {a['unit_gap']:+,} | {note_short} |"
        )

    lines += [
        "",
        "---",
        f"*Report generated by `scripts/ai_training_review.py` on {run_date}*",
    ]

    return "\n".join(lines), all_recs


# ---------------------------------------------------------------------------
# Step 6 -- Send email via Outlook COM
# ---------------------------------------------------------------------------
def send_email(subject, body_html, report_path, dry_run):
    if dry_run:
        print("  [DRY RUN] Email not sent.")
        return

    try:
        import win32com.client
        outlook  = win32com.client.Dispatch("Outlook.Application")
        mail     = outlook.CreateItem(0)
        mail.To  = RECIPIENT
        mail.Subject = subject
        mail.HTMLBody = body_html
        mail.Send()
        print(f"  Email sent to {RECIPIENT}")
        return
    except ImportError:
        pass
    except Exception as e:
        print(f"  [WARN] Outlook COM failed: {e}. Trying SMTP fallback.")

    # SMTP fallback (no auth -- relies on local relay or Exchange direct)
    import smtplib
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = RECIPIENT
    msg["To"]      = RECIPIENT
    msg.attach(MIMEText(body_html, "html"))
    try:
        with smtplib.SMTP("localhost", 25, timeout=10) as s:
            s.sendmail(RECIPIENT, [RECIPIENT], msg.as_string())
        print(f"  Email sent via SMTP to {RECIPIENT}")
    except Exception as e:
        print(f"  [WARN] SMTP also failed: {e}")
        print(f"  Report saved at: {report_path}")


def build_email_html(analyses, all_recs, report_path, run_date, days,
                     systemic_impacts=None):
    total_gap = sum(a["unit_gap"] for a in analyses)
    n = len(analyses)
    gap_color = "#c62828" if total_gap < 0 else "#2e7d32"

    # Build one row per comment (main table)
    TD  = "padding:8px 12px;border-bottom:1px solid #e0e0e0;vertical-align:top"
    TDR = TD + ";text-align:right"

    comment_rows = ""
    for a in sorted(analyses, key=lambda x: abs(x["unit_gap"]), reverse=True):
        rec       = a["recommendation"]
        gap       = a["unit_gap"]
        gap_col   = "#c62828" if gap < 0 else "#2e7d32"
        conf      = rec["confidence"].upper()
        conf_col  = {"HIGH": "#1b5e20", "MEDIUM": "#e65100", "LOW": "#757575"}.get(conf, "#000")
        note_full = a["note"] or ""
        # Customer short name (strip INC/LLC/CORP suffixes for brevity)
        cust = re.sub(r'\b(INC\.?|LLC|CORP\.?|LTD\.?|CO\.?)\s*$', '', a.get("customer",""), flags=re.I).strip().rstrip(",.")

        comment_rows += f"""
<tr>
  <td style="{TD}">
    <b style="font-size:13px">{cust}</b><br>
    <span style="color:#616161;font-size:12px">{a.get('mstyle','')} &nbsp;|&nbsp; {a.get('brand','')[:28]}</span>
  </td>
  <td style="{TD};font-size:12px;color:#424242;max-width:220px">
    <i>"{note_full[:120]}{"..." if len(note_full)>120 else ""}"</i>
  </td>
  <td style="{TD}">
    <span style="background:#e3f2fd;color:#0d47a1;padding:2px 7px;border-radius:3px;font-size:12px">{a['ai_model']}</span>
  </td>
  <td style="{TDR};color:{gap_col};font-weight:bold">{gap:+,}u</td>
  <td style="{TD};font-size:12px;max-width:260px">{rec['proposed_change'][:180]}{"..." if len(rec['proposed_change'])>180 else ""}</td>
  <td style="{TD};text-align:center"><span style="color:{conf_col};font-weight:bold;font-size:12px">{conf}</span></td>
</tr>"""

    report_path_str = str(report_path)
    claude_cmd = f'implement ai training recommendations'

    html = f"""<html>
<body style="font-family:Arial,sans-serif;font-size:14px;color:#212121;max-width:980px;margin:0 auto">

<table style="width:100%;border-collapse:collapse;margin-bottom:20px">
<tr>
  <td style="padding:16px 0 8px 0">
    <span style="font-size:20px;font-weight:bold;color:#1565c0">AI Training Review</span>
    &nbsp;&nbsp;<span style="color:#757575;font-size:14px">{run_date} &nbsp;|&nbsp; last {days} days</span>
  </td>
  <td style="text-align:right;padding:16px 0 8px 0">
    <span style="font-size:22px;font-weight:bold;color:{gap_color}">{total_gap:+,} units</span><br>
    <span style="font-size:11px;color:#9e9e9e">net MAN - AI gap</span>
  </td>
</tr>
</table>

<table style="width:100%;border-collapse:collapse;font-size:13px">
  <thead>
    <tr style="background:#1565c0;color:#fff">
      <th style="padding:9px 12px;text-align:left;white-space:nowrap">Customer / Item</th>
      <th style="padding:9px 12px;text-align:left">Planner Comment</th>
      <th style="padding:9px 12px;text-align:left;white-space:nowrap">Model</th>
      <th style="padding:9px 12px;text-align:right;white-space:nowrap">Gap</th>
      <th style="padding:9px 12px;text-align:left">Recommendation</th>
      <th style="padding:9px 12px;text-align:center;white-space:nowrap">Confidence</th>
    </tr>
  </thead>
  <tbody>
    {comment_rows}
  </tbody>
</table>

<p style="margin-top:24px;font-size:13px;color:#424242">
  <b>To approve and implement:</b> Open Claude Code and say
  <code style="background:#f5f5f5;border:1px solid #e0e0e0;padding:3px 8px;border-radius:3px">{claude_cmd}</code>
  &mdash; Claude will read the full report and ask which changes to implement.
</p>

<p style="margin-top:4px;font-size:11px;color:#9e9e9e">
  Full report: {report_path_str}
</p>

</body></html>"""
    return html


# ---------------------------------------------------------------------------
# QB write-back: mark comments as Reviewed
# ---------------------------------------------------------------------------
def mark_reviewed_in_qb(comment_rids, dry_run):
    """Flip FLAG from 'AI Training' -> 'Reviewed' on each processed comment.

    Uses QB REST upsert with mergeFieldId=3 (Record ID#).
    Once FLAG='Reviewed', the next run's query {31.EX.'AI Training'}
    will naturally exclude these records -- no local state file needed.
    """
    if dry_run:
        print(f"  [DRY RUN] Would mark {len(comment_rids)} comments as Reviewed in QB.")
        return

    rids = [int(r) for r in comment_rids if r]
    if not rids:
        return

    batch_size = 500
    total_ok   = 0
    for i in range(0, len(rids), batch_size):
        batch = rids[i:i + batch_size]
        try:
            result = _qb_post("records", {
                "to":          COMMENTS_TABLE,
                "mergeFieldId": C_RECORD_ID,
                "data": [
                    {str(C_RECORD_ID): {"value": rid},
                     str(C_FLAG):      {"value": "Reviewed"}}
                    for rid in batch
                ],
                "fieldsToReturn": [],
            })
            n = result.get("metadata", {}).get("totalNumberOfRecordsProcessed", len(batch))
            total_ok += n
        except Exception as e:
            print(f"  [WARN] QB write-back failed for batch {i//batch_size + 1}: {e}")

    print(f"  Marked {total_ok}/{len(rids)} comments as Reviewed in QB.")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="Fetch AI Training comments, analyze vs model, email report.")
    parser.add_argument("--days",    type=int, default=30,
                        help="Look back N days (default: 30)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Analyze but skip email and QB write-back")
    args = parser.parse_args()

    run_date = datetime.now().strftime("%Y-%m-%d")
    print(f"\n{'='*60}", flush=True)
    print(f"  AI Training Review  |  {run_date}  |  last {args.days} days",
          flush=True)
    print(f"{'='*60}\n", flush=True)

    # Discover dynamic fields
    print("[SETUP] Discovering field IDs...")
    note_fid = _discover_note_fid()
    man_fids = _discover_man_fids()
    l4w_fid  = _discover_l4w_fid()
    print(f"  Note FID = {note_fid}  |  MAN PRJ cols = {len(man_fids)}  "
          f"|  L4W FID = {l4w_fid}")

    if len(man_fids) < 26:
        print(f"  [WARN] Only {len(man_fids)} MAN PRJ FIDs found (expected 26).",
              flush=True)

    # Fetch AI Training comments (FLAG='AI Training' -- already-reviewed ones
    # have FLAG='Reviewed' in QB and will not match this query)
    print("\n[1/4] Fetching AI Training comments...", flush=True)
    comments, note_fid = fetch_ai_training_comments(args.days, note_fid)

    if not comments:
        print("  No new AI Training comments to process.\n")
        print("Done.", flush=True)
        return

    # Fetch projections
    print("\n[2/4] Fetching projection records...", flush=True)
    keys = {sval(c, C_ACCT_MSTYLE) for c in comments if sval(c, C_ACCT_MSTYLE)}
    projections = fetch_projections(keys, man_fids, l4w_fid)

    # Analyze each comment
    print("\n[3/4] Analyzing comments...", flush=True)
    analyses = []
    for c in comments:
        key  = sval(c, C_ACCT_MSTYLE)
        proj = projections.get(key)
        a    = analyze_comment(c, proj, man_fids, note_fid, l4w_fid)
        analyses.append(a)
        print(f"  {key:<35}  intent={a['intent']:<12}  fit={a['fit']:<18}  "
              f"gap={a['unit_gap']:+,}u")

    grouped = aggregate(analyses)

    # Build report
    print("\n[4/4] Building report and sending email...", flush=True)
    report_md, all_recs = build_report(analyses, grouped, run_date, args.days)

    report_path = ANALYSIS_DIR / f"ai_training_{run_date}.md"
    report_path.write_text(report_md, encoding="utf-8")
    print(f"  Report saved -> {report_path}")

    # Send email
    subject    = (f"AI Training Review {run_date} -- "
                  f"{len(analyses)} comments, {len(all_recs)} recommendations")
    email_html = build_email_html(analyses, all_recs, report_path, run_date, args.days)
    send_email(subject, email_html, report_path, args.dry_run)

    # Mark processed comments as Reviewed in QB so they don't re-appear
    comment_rids = [str(int(fval(c, C_RECORD_ID))) for c in comments]
    mark_reviewed_in_qb(comment_rids, args.dry_run)

    # Print summary
    total_gap = sum(a["unit_gap"] for a in analyses)
    print(f"\n{'='*60}", flush=True)
    print(f"  COMPLETE  |  {len(analyses)} comments  |  "
          f"Net gap: {total_gap:+,}u  |  {len(all_recs)} recommendations",
          flush=True)
    print(f"{'='*60}\n", flush=True)


if __name__ == "__main__":
    main()
