"""
ai_training_review.py
---------------------
Daily pipeline: Fetch unreviewed "AI Training" Projection Comments, deep-analyze
the planner's correction vs the current AI model, propose concrete rule changes,
save a markdown report, and email a summary with review instructions.

Usage:
    python scripts/ai_training_review.py [--days N] [--dry-run] [--reset]

    --days N     Look back N days for AI Training comments (default: 30)
    --dry-run    Analyze but skip email and QB write-back
    --reset      Clear processed-IDs cache so everything is reprocessed

QB connections (all REST API, no CData):
    bpt35zccg  Projection Comments  (source of AI Training flags)
    bpd237tvm  Projections          (AI PRJ, MAN PRJ, model, order history)

Output:
    analysis/ai_training_YYYY-MM-DD.md   Full report
    analysis/ai_training_processed.json  State: processed comment Record IDs

Pipeline order (as of 2026-05-27):
  1. Fetch unreviewed AI Training comments (FLAG='AI Training')
  2. Fetch projection records for the comment keys
  3. Analyze each comment (intent, fit, raw recommendation)
  4a. Build flat recommendation list (_build_all_recs)
  4b. Estimate systemic impact FIRST (before finalizing recommendations)
  4c. Validate/override recommendations against systemic impact
       -- VALIDATED:  fix narrows MAN-AI gap, keep it
       -- REJECTED:   fix widens gap, replace with directional-guard guidance
       -- ISOLATED:   0 flagged records, address individually
  5.  Build report with validated recommendations
  6.  Email report
  7.  Mark comments Reviewed in QB
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
    NOTE: This generates the RAW recommendation before systemic validation.
          validate_and_override_recs() may replace proposed_change after checking
          whether the fix actually narrows the MAN-AI gap in the broader population.
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
# Step 4a -- Build flat recommendation list (extracted for early pipeline use)
# ---------------------------------------------------------------------------
def _build_all_recs(grouped):
    """Build the deduplicated recommendation list from grouped analyses.

    Returns list of 7-tuples:
      (rec_num, intent, fit, rec_dict, unit_impact, count, ai_model)

    This is extracted from build_report() so it can be called BEFORE systemic
    impact estimation -- allowing recommendations to be validated and overridden
    based on real data before the final report is built.
    """
    seen_recs = set()
    rec_num   = 0
    all_recs  = []
    for (intent, fit), grp in grouped:
        if not grp["items"]:
            continue
        rec = grp["items"][0]["recommendation"]
        dedup_key = rec["proposed_change"][:60]
        if dedup_key in seen_recs:
            continue
        seen_recs.add(dedup_key)
        rec_num  += 1
        impact    = grp["unit_gap"]
        _ai_model = grp["items"][0].get("ai_model", "") if grp["items"] else ""
        all_recs.append((rec_num, intent, fit, dict(rec), impact, grp["count"], _ai_model))
    return all_recs


# ---------------------------------------------------------------------------
# Step 4b -- Estimate systemic impact across ALL active projections
# ---------------------------------------------------------------------------
def estimate_systemic_impact(all_recs, man_fids):
    """
    For each recommendation, query ALL active Projections with that AI model and:
      - Count total records in scope
      - Sum AI PRJ 26w across scope
      - For records that match detection criteria, compute:
          variance_before = sum(MAN 26w) - sum(AI 26w)
          variance_after  = sum(MAN 26w) - sum(estimated_new_AI 26w)
        so we can show how much the fix closes the MAN vs AI gap.

    Returns list of dicts keyed by rec_num.
    """
    queried_models = {}   # cache: model_keyword -> 6-tuple
    man_fid_list   = sorted(man_fids.values()) if man_fids else []

    # -----------------------------------------------------------------------
    def _estimate_new_ai_26w(change_type, fit, ai_26w, l13w, l4w):
        """Approximate AI 26w total AFTER the proposed fix is applied."""
        if change_type == "threshold" and fit == "over_projecting":
            # Croston post-guard: cap at L13W * 1.5 * 26
            cap = l13w * 1.5 * 26
            return min(ai_26w, cap) if cap > 0 else ai_26w
        if fit == "under_projecting":
            # Anchor to max(L4W, L13W) -- picks up growing trend
            base = max(l4w, l13w) if l4w > 0 else l13w
            return base * 26
        if fit == "missed_lifecycle":
            # EOL: anchor to L4W (decelerating toward zero)
            return l4w * 26 if l4w > 0 else 0.0
        if change_type == "model_switch" and fit == "wrong_model":
            # Switch from flat Heuristic to trend-aware: use max(L4W, L13W)
            base = max(l4w, l13w) if l4w > 0 else l13w
            return base * 26
        return ai_26w  # no change for investigate / unknown

    # -----------------------------------------------------------------------
    def _fetch_model_scope(model_keyword, change_type, fit):
        """Query all active projections where AI_MODEL contains model_keyword.

        Returns 6-tuple:
          (scope_count, scope_ai_total, criteria_count,
           flagged_man_total, flagged_ai_total, flagged_ai_estimate)
        """
        cache_key = model_keyword.lower()
        if cache_key in queried_models:
            return queried_models[cache_key]

        EMPTY = (0, 0, 0, 0, 0, 0)
        select_fids = (
            [P_KEY, P_AI_MODEL, P_L13W, 1417]
            + AI_PRJ_FIDS
            + ORD_FIDS[:13]
            + man_fid_list          # MAN PRJ weeks for before/after variance
        )
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
            queried_models[cache_key] = EMPTY
            return EMPTY

        total_count    = len(rows)
        scope_ai_total = sum(sum(fval(r, fid) for fid in AI_PRJ_FIDS) for r in rows)

        criteria_count      = 0
        flagged_man_total   = 0.0
        flagged_ai_total    = 0.0
        flagged_ai_estimate = 0.0

        for r in rows:
            l13w   = fval(r, P_L13W)
            l4w    = fval(r, 1417)
            ord_wk = [fval(r, fid) for fid in ORD_FIDS[:13]]
            ai_26w = sum(fval(r, fid) for fid in AI_PRJ_FIDS)
            man_26w = sum(fval(r, fid) for fid in man_fid_list) if man_fid_list else 0.0
            trend  = l4w / l13w if l13w > 0 else 1.0

            flagged = False
            if change_type == "threshold" and fit == "over_projecting":
                if l13w > 0 and any(w >= l13w * 3 for w in ord_wk):
                    flagged = True
                elif "croston" not in model_keyword.lower() and l4w > 0 and trend < 0.80:
                    flagged = True
            elif change_type == "threshold" and fit == "under_projecting":
                if l4w > 0 and trend > 1.15:
                    flagged = True
            elif change_type == "model_switch":
                if l4w > 0 and (trend > 1.20 or trend < 0.80):
                    flagged = True
            elif change_type == "missed_lifecycle":
                if l4w > 0 and trend < 0.65 and ai_26w > 0:
                    flagged = True
            else:
                flagged = True  # investigate -- all in scope

            if flagged:
                criteria_count      += 1
                flagged_man_total   += man_26w
                flagged_ai_total    += ai_26w
                new_ai = _estimate_new_ai_26w(change_type, fit, ai_26w, l13w, l4w)
                flagged_ai_estimate += new_ai

        result_tuple = (
            total_count,
            int(scope_ai_total),
            criteria_count,
            int(flagged_man_total),
            int(flagged_ai_total),
            int(flagged_ai_estimate),
        )
        queried_models[cache_key] = result_tuple
        return result_tuple

    # -----------------------------------------------------------------------
    print("  Computing systemic impact across all active projections...", flush=True)
    results       = []
    seen_keywords = set()

    for num, intent, fit, rec, unit_impact, count, *_rest in all_recs:
        # Use the actual ai_model from the grouped items (7th tuple element)
        # rather than scanning proposed_change text (which may mention other models)
        ai_model_raw = _rest[0] if _rest else ""
        model_lo = ai_model_raw.lower()
        if "croston" in model_lo:
            model_keyword = "Croston"
        elif "pos-wos" in model_lo or "amazon" in model_lo:
            model_keyword = "POS-WOS"
        elif "seasonal" in model_lo:
            model_keyword = "Seasonal"
        elif "heuristic" in model_lo or model_lo == "":
            # Empty model name also falls to Heuristic (the fallback path)
            model_keyword = "Heuristic"
        else:
            model_keyword = ai_model_raw  # use raw name as keyword

        if not model_keyword:
            results.append({
                "rec_num": num, "model_keyword": "all",
                "scope_count": 0, "scope_ai_total": 0,
                "criteria_count": 0,
                "flagged_man_total": 0, "flagged_ai_total": 0,
                "flagged_ai_estimate": 0,
                "variance_before": 0, "variance_after": 0,
                "direction": "unknown",
            })
            continue

        if model_keyword in seen_keywords:
            sc, at, cc, fmt, fat, fae = queried_models.get(
                model_keyword.lower(), (0, 0, 0, 0, 0, 0))
        else:
            seen_keywords.add(model_keyword)
            sc, at, cc, fmt, fat, fae = _fetch_model_scope(
                model_keyword, rec["change_type"], fit)
            var_b = fmt - fat
            var_a = fmt - fae
            print(f"    [{num}] {model_keyword}: {sc:,} records, {cc:,} flagged | "
                  f"MAN-AI before={var_b:+,}u  after={var_a:+,}u", flush=True)

        direction = (
            "down" if fit in ("over_projecting", "missed_lifecycle") else
            "up"   if fit == "under_projecting" else
            "mixed"
        )
        results.append({
            "rec_num":            num,
            "model_keyword":      model_keyword,
            "scope_count":        sc,
            "scope_ai_total":     at,
            "criteria_count":     cc,
            "flagged_man_total":  fmt,
            "flagged_ai_total":   fat,
            "flagged_ai_estimate": fae,
            "variance_before":    fmt - fat,
            "variance_after":     fmt - fae,
            "direction":          direction,
        })

    # ------------------------------------------------------------------
    # Combined row: query the union of all unique model keywords so the
    # user can see the total impact of implementing all fixes together.
    unique_kws = list(dict.fromkeys(
        r["model_keyword"] for r in results
        if r["model_keyword"] and r["model_keyword"] != "all"
    ))
    if len(unique_kws) > 1:
        print(f"    Combined ({' + '.join(unique_kws)}): fetching union scope...",
              flush=True)
        _comb_kw_filter = "OR".join(
            f"{{1580.CT.'{kw}'}}" for kw in unique_kws)
        _comb_where = f"{{10.SW.'A'}}AND({_comb_kw_filter})"

        _comb_select = (
            [P_KEY, P_AI_MODEL, P_L13W, 1417]
            + AI_PRJ_FIDS + ORD_FIDS[:13] + man_fid_list
        )
        _comb_rows, _comb_skip = [], 0
        try:
            while True:
                _r = _qb_post("records/query", {
                    "from":    PROJ_TABLE,
                    "select":  _comb_select,
                    "where":   _comb_where,
                    "options": {"top": 2000, "skip": _comb_skip},
                })
                _b = _r.get("data", [])
                _comb_rows.extend(_b)
                if len(_b) < 2000:
                    break
                _comb_skip += 2000
                time.sleep(0.15)
        except Exception as _e:
            print(f"  [WARN] Combined scope fetch failed: {_e}", flush=True)
            _comb_rows = []

        if _comb_rows:
            _comb_ai   = sum(sum(fval(r, fid) for fid in AI_PRJ_FIDS) for r in _comb_rows)
            _comb_cc = _comb_fmt = _comb_fat = _comb_fae = 0
            for r in _comb_rows:
                l13w   = fval(r, P_L13W)
                l4w    = fval(r, 1417)
                ord_wk = [fval(r, fid) for fid in ORD_FIDS[:13]]
                ai_26w = sum(fval(r, fid) for fid in AI_PRJ_FIDS)
                man_26w = sum(fval(r, fid) for fid in man_fid_list) if man_fid_list else 0.0
                trend  = l4w / l13w if l13w > 0 else 1.0
                # Flag if matches ANY individual recommendation's criteria
                flagged = False
                for r2 in results:
                    ct2 = next(
                        (rec["change_type"] for _n, _i, _f, rec, *_ in all_recs
                         if _n == r2["rec_num"]),
                        "threshold"
                    )
                    fit2 = r2["direction"]
                    if ct2 == "threshold" and fit2 == "down":
                        if l13w > 0 and any(w >= l13w * 3 for w in ord_wk):
                            flagged = True; break
                        if l4w > 0 and trend < 0.80:
                            flagged = True; break
                    elif fit2 == "up" and l4w > 0 and trend > 1.15:
                        flagged = True; break
                    elif ct2 == "model_switch" and l4w > 0 and (trend > 1.20 or trend < 0.80):
                        flagged = True; break
                if flagged:
                    _comb_cc  += 1
                    _comb_fmt += man_26w
                    _comb_fat += ai_26w
                    _comb_fae += _estimate_new_ai_26w("threshold", "over_projecting",
                                                      ai_26w, l13w, l4w)

            _comb_vb = int(_comb_fmt) - int(_comb_fat)
            _comb_va = int(_comb_fmt) - int(_comb_fae)
            print(f"    Combined: {len(_comb_rows):,} records, {_comb_cc:,} flagged | "
                  f"MAN-AI before={_comb_vb:+,}u  after={_comb_va:+,}u", flush=True)
            results.append({
                "rec_num":            "ALL",
                "model_keyword":      " + ".join(unique_kws),
                "scope_count":        len(_comb_rows),
                "scope_ai_total":     int(_comb_ai),
                "criteria_count":     _comb_cc,
                "flagged_man_total":  int(_comb_fmt),
                "flagged_ai_total":   int(_comb_fat),
                "flagged_ai_estimate": int(_comb_fae),
                "variance_before":    _comb_vb,
                "variance_after":     _comb_va,
                "direction":          "mixed",
                "is_combined":        True,
            })

    return results


# ---------------------------------------------------------------------------
# Step 4c -- Validate recommendations against systemic impact
# ---------------------------------------------------------------------------
def validate_and_override_recs(all_recs, systemic_impacts, grouped):
    """
    Check each recommendation against its computed systemic impact BEFORE
    finalizing it. Generates a NEW, directionally-correct recommendation for
    every case -- REJECTED and ISOLATED cases get genuinely new proposals
    (not just "fix rejected") that ARE expected to close the MAN-AI gap.

    Status values (stored in rec["systemic_status"]):
      VALIDATED -- original fix narrows gap, keep it (with a confirmation banner)
      REJECTED  -- original fix widens gap; replaced with directional-guard rec
      ISOLATED  -- 0 flagged records; replaced with item-specific model fix rec
      NEUTRAL   -- gap unchanged or no systemic data available

    grouped : output of aggregate() -- needed to access the specific items
              affected by each recommendation so we can write targeted guidance.
    """
    si_lookup = {
        si["rec_num"]: si
        for si in (systemic_impacts or [])
        if not si.get("is_combined")
    }
    # Items lookup: (intent, fit) -> list of analysis dicts for that group
    items_by_key = {(intent, fit): grp["items"] for (intent, fit), grp in grouped}

    # -----------------------------------------------------------------------
    def _item_list_str(items, max_show=3):
        """Build a short item description string for embedding in recommendations."""
        parts = []
        for a in items[:max_show]:
            cust_short = re.sub(r'\b(INC\.?|LLC|CORP\.?|LTD\.?|CO\.?)\s*$',
                                '', a.get("customer", ""), flags=re.I).strip().rstrip(",.")
            parts.append(
                f"{a['key']} ({cust_short[:20]}, "
                f"AI {a['ai_total']:,}u vs MAN {a['man_total']:,}u, gap {a['unit_gap']:+,}u)"
            )
        if len(items) > max_show:
            parts.append(f"+{len(items) - max_show} more")
        return "; ".join(parts)

    # -----------------------------------------------------------------------
    def _new_rec_for_rejected(rec, fit, kw, sc, cc, vb, va, items):
        """Generate a new, directionally-correct recommendation for a REJECTED fix."""
        change_type = rec["change_type"]
        delta_bad   = va - vb
        item_str    = _item_list_str(items)
        safe_zone   = sc - cc   # records that already have AI < MAN -- must not be harmed

        if change_type == "threshold" and fit == "over_projecting":
            # Croston / over-projection cap that fired on wrong population.
            # New rec: add a MAN-PRJ comparison guard so cap only fires when AI > MAN.
            return dict(
                change_type="threshold",
                proposed_change=(
                    f"Add a MAN-PRJ-aware directional cap to {kw}: run the existing spike "
                    f"detection (week >= L13W * 3x), but only apply the cap when the model's "
                    f"26w output is ALSO above MAN PRJ * 1.10. This two-gate approach ensures "
                    f"the cap only fires on true over-projectors (AI >> MAN), leaving the "
                    f"{safe_zone:,} records where AI is already below MAN completely untouched. "
                    f"Implementation: in the Croston post-processing block, add: "
                    f"if spike_detected and croston_26w > man_prj_26w * 1.10: apply_cap(). "
                    f"Items driving this recommendation: {item_str}."
                ),
                confidence="medium",
                rationale=(
                    f"The original cap (spike detection alone) affected {cc:,} records and "
                    f"widened MAN-AI from {vb:+,}u to {va:+,}u ({delta_bad:+,}u worse) because "
                    f"most flagged records already have AI < MAN. Adding the MAN * 1.10 gate "
                    f"restricts the cap to only the outliers where AI genuinely over-projects "
                    f"vs the planner's target, which is the actual problem these comments describe."
                ),
            )

        if change_type == "threshold" and fit == "under_projecting":
            # Under-projecting cap / boost that fired on wrong population.
            # New rec: only boost when AI is below MAN by a meaningful margin.
            return dict(
                change_type="threshold",
                proposed_change=(
                    f"Add a MAN-PRJ-aware floor to {kw}: run the existing growth detection "
                    f"(L4W/L13W > 1.15x), but only apply the boost when AI 26w is ALSO below "
                    f"MAN PRJ * 0.90 (meaning AI is meaningfully under the planner's target). "
                    f"This prevents inflating records where AI is already at or above MAN. "
                    f"Items driving this recommendation: {item_str}."
                ),
                confidence="medium",
                rationale=(
                    f"The original boost affected {cc:,} records and widened MAN-AI from "
                    f"{vb:+,}u to {va:+,}u ({delta_bad:+,}u worse). Adding the MAN * 0.90 "
                    f"gate restricts the boost to only items where the model is meaningfully "
                    f"below the planner's target."
                ),
            )

        if change_type == "model_switch":
            # Model switch that fired on wrong population.
            # New rec: switch only items where the trend diverges from the current model's output.
            return dict(
                change_type="model_switch",
                proposed_change=(
                    f"Targeted model switch for {kw}: instead of switching all trend-divergent "
                    f"records, add a combined gate -- switch the model only when "
                    f"(1) L4W/L13W trend is outside 0.80-1.20 AND "
                    f"(2) current AI is on the wrong side of MAN PRJ by >15%. "
                    f"This avoids switching records where the trend diverges but AI is already "
                    f"close to MAN. Items to review first: {item_str}."
                ),
                confidence="medium",
                rationale=(
                    f"The original model-switch criterion matched {cc:,} records but widened "
                    f"MAN-AI from {vb:+,}u to {va:+,}u. A combined trend + MAN-proximity gate "
                    f"targets only the records where both the model AND its output direction "
                    f"disagree with the planner."
                ),
            )

        # Fallback for other change types
        return dict(
            change_type=change_type,
            proposed_change=(
                f"Original {change_type} fix would widen MAN-AI gap. "
                f"New approach: add a MAN PRJ comparison gate so the fix only fires when "
                f"AI is already on the wrong side of MAN by >10%. "
                f"Items to address directly: {item_str}."
            ),
            confidence="low",
            rationale=(
                f"Systemic check showed the original fix widened MAN-AI from {vb:+,}u "
                f"to {va:+,}u across {cc:,} flagged {kw} records."
            ),
        )

    # -----------------------------------------------------------------------
    def _new_rec_for_isolated(rec, intent, fit, kw, sc, items):
        """Generate an item-specific fix recommendation when no systemic pattern exists."""
        change_type = rec["change_type"]
        item_str    = _item_list_str(items)

        if fit == "wrong_model" or change_type == "model_switch":
            # Item is using the wrong model but no systemic pattern -- item-level override
            first = items[0] if items else {}
            man_target = first.get("man_total", 0)
            mstyle     = first.get("mstyle", "")
            cat_hint   = mstyle.split("-")[0] if "-" in mstyle else mstyle[:4]
            return dict(
                change_type="model_switch",
                proposed_change=(
                    f"Item-level model fix for {count} item(s) -- no systemic pattern in "
                    f"{sc:,} {kw} records checked, so this is a targeted override: "
                    f"(1) Check if the item's category keyword (search '{cat_hint}' in "
                    f"derived_category_profiles.json) is registered -- if missing, add it "
                    f"to route this item to Seasonal or Croston's instead of flat Heuristic. "
                    f"(2) If the item is intermittent/sparse (many zero-order weeks), switch "
                    f"to Croston's model via the item's category profile. "
                    f"(3) Immediate fix while model logic is updated: add a Tell-AI comment "
                    f"on each affected record targeting MAN PRJ level. "
                    f"Affected: {item_str}."
                ),
                confidence="medium",
                rationale=(
                    f"Systemic scan of {sc:,} active {kw} projections found 0 records "
                    f"matching the detection criteria -- this is an item-specific model "
                    f"selection issue. Fixing the category registration for these specific "
                    f"mstyles closes the gap without touching the broader {kw} population."
                ),
            )

        if fit == "over_projecting":
            return dict(
                change_type="threshold",
                proposed_change=(
                    f"Item-level correction for {count} over-projecting item(s) -- no "
                    f"matching pattern found in {sc:,} {kw} records, so a targeted fix "
                    f"is appropriate: "
                    f"(1) Add a Tell-AI comment on each affected record with a target "
                    f"projection at or near MAN PRJ level to override the AI for the next "
                    f"forecast run. "
                    f"(2) Check if this item's order history has a one-time spike that "
                    f"inflated the model -- if so, flag the spike week in the history table "
                    f"as non-recurring. "
                    f"Affected: {item_str}."
                ),
                confidence="medium",
                rationale=(
                    f"No other {kw} records show this over-projection pattern ({sc:,} checked). "
                    f"The root cause is item-specific (e.g., a one-time order in history). "
                    f"A Tell-AI comment is the fastest path to alignment."
                ),
            )

        if fit == "under_projecting":
            return dict(
                change_type="threshold",
                proposed_change=(
                    f"Item-level correction for {count} under-projecting item(s) -- no "
                    f"matching growth pattern found in {sc:,} {kw} records: "
                    f"(1) Add a Tell-AI comment on each affected record targeting MAN PRJ level. "
                    f"(2) If the item has a known distribution gain, verify that the new "
                    f"account/channel is included in the order history window (check if recent "
                    f"orders from the new channel have posted to this key). "
                    f"Affected: {item_str}."
                ),
                confidence="medium",
                rationale=(
                    f"No other {kw} records show this growth pattern ({sc:,} checked). "
                    f"The distribution gain or ramp is item-specific and not visible to "
                    f"the model yet -- a Tell-AI override bridges the gap."
                ),
            )

        # Generic isolated fallback
        return dict(
            change_type=change_type,
            proposed_change=(
                f"Item-specific fix required for {count} item(s) -- no systemic pattern "
                f"found in {sc:,} {kw} records. Add a Tell-AI comment on each affected "
                f"record with the target projection: {item_str}."
            ),
            confidence="low",
            rationale=(
                f"Systemic scan found 0 matching records out of {sc:,} active {kw} "
                f"projections. This is a one-off item requiring individual attention."
            ),
        )

    # -----------------------------------------------------------------------
    updated = []
    for tup in all_recs:
        num, intent, fit, rec, impact, count, *_rest = tup
        ai_model = _rest[0] if _rest else ""
        rec      = dict(rec)   # copy -- do not mutate the original
        items    = items_by_key.get((intent, fit), [])

        si = si_lookup.get(num)
        if si is None:
            rec["systemic_status"] = "NEUTRAL"
            updated.append((num, intent, fit, rec, impact, count, ai_model))
            continue

        cc  = si["criteria_count"]
        vb  = si["variance_before"]
        va  = si["variance_after"]
        kw  = si["model_keyword"] or "model"
        sc  = si["scope_count"]

        if cc == 0:
            # ISOLATED: generate a targeted item-level fix
            rec["systemic_status"] = "ISOLATED"
            new_rec = _new_rec_for_isolated(rec, intent, fit, kw, sc, items)
            rec["change_type"]    = new_rec["change_type"]
            rec["proposed_change"] = new_rec["proposed_change"]
            rec["rationale"]      = new_rec["rationale"]
            rec["confidence"]     = new_rec["confidence"]

        elif abs(va) > abs(vb):
            # REJECTED: generate a directional-guard version of the fix
            rec["systemic_status"] = "REJECTED"
            new_rec = _new_rec_for_rejected(rec, fit, kw, sc, cc, vb, va, items)
            rec["change_type"]    = new_rec["change_type"]
            rec["proposed_change"] = new_rec["proposed_change"]
            rec["rationale"]      = new_rec["rationale"]
            rec["confidence"]     = new_rec["confidence"]

        elif abs(va) < abs(vb):
            # VALIDATED: original fix is good, prepend a confirmation note
            gap_closed = abs(vb) - abs(va)
            rec["systemic_status"] = "VALIDATED"
            orig = rec["proposed_change"]
            rec["proposed_change"] = (
                f"[VALIDATED: narrows MAN-AI gap from {vb:+,}u to {va:+,}u, "
                f"closing {gap_closed:,}u across {cc:,} {kw} records] "
                f"{orig}"
            )

        else:
            rec["systemic_status"] = "NEUTRAL"

        updated.append((num, intent, fit, rec, impact, count, ai_model))

    return updated


# ---------------------------------------------------------------------------
# Step 5 -- Generate markdown report (uses pre-validated all_recs)
# ---------------------------------------------------------------------------
def build_report(analyses, grouped, all_recs, run_date, days):
    """Build the full markdown report.

    Parameters
    ----------
    analyses   : list of per-comment analysis dicts
    grouped    : output of aggregate(analyses) -- used for pattern-groups table
                 and for fetching affected items per recommendation
    all_recs   : pre-built and pre-validated 7-tuples from _build_all_recs()
                 followed by validate_and_override_recs(). Each rec dict already
                 has 'systemic_status' and possibly overridden 'proposed_change'.
    run_date   : str date label
    days       : int lookback window

    Returns
    -------
    str  -- full markdown text (save to file; systemic section appended in main)
    """
    lines = [
        "# AI Training Comment Review",
        f"**Generated:** {run_date}",
        f"**Lookback:** {days} days",
        f"**Comments analyzed:** {len(analyses)}",
        "",
        "---",
        "",
        "## 1. Executive Summary",
        "",
    ]

    total_gap   = sum(a["unit_gap"] for a in analyses)
    over_count  = sum(1 for a in analyses if a["unit_gap"] < -100)
    under_count = sum(1 for a in analyses if a["unit_gap"] > 100)
    eol_count   = sum(1 for a in analyses if a["intent"] == "eol")
    zero_count  = sum(1 for a in analyses if a["intent"] == "zero")

    lines += [
        "| Metric | Value |",
        "|---|---|",
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
        "(NOTE: Recommendations below have been validated against systemic impact.",
        "REJECTED = fix would widen MAN-AI gap. ISOLATED = no systemic pattern.)",
        "",
    ]

    # Build a lookup from (intent, fit) -> group items for affected-item display
    items_by_key = {(intent, fit): grp["items"] for (intent, fit), grp in grouped}

    for rec_num, intent, fit, rec, impact, count, *_rest in all_recs:
        conf          = rec["confidence"].upper()
        status        = rec.get("systemic_status", "")
        status_str    = f" [{status}]" if status else ""
        change_label  = rec["change_type"].replace("_", " ").title()
        grp_items     = items_by_key.get((intent, fit), [])

        lines += [
            f"### [{rec_num}]{status_str} {change_label} -- "
            f"{intent.upper()} / {fit.replace('_', ' ')}",
            f"**Impact:** {impact:+,} units across {count} item(s)  "
            f"| **Confidence:** {conf}  | **Systemic Status:** {status}",
            "",
            "**Proposed Change:**  ",
            rec["proposed_change"],
            "",
            f"**Rationale:** {rec['rationale']}",
            "",
            "**Affected items:**",
        ]
        for a in grp_items[:5]:
            gap_str = f"{a['unit_gap']:+,}u"
            lines.append(
                f"- `{a['key']}` ({a['customer'][:30]} / {a['brand'][:20]}) "
                f"Model: {a['ai_model']} | Gap: {gap_str}  "
                f'Comment: "{a["note"][:80]}"'
            )
        if len(grp_items) > 5:
            lines.append(f"- ... and {len(grp_items) - 5} more")
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

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Step 5b -- Systemic impact HTML block (injected into email)
# ---------------------------------------------------------------------------
def _build_systemic_html(systemic_impacts, all_recs):
    """Return an HTML block (string) with the systemic impact table, or '' if no data."""
    if not systemic_impacts:
        return ""

    # Build lookups from all_recs
    rec_labels = {}
    status_lookup = {}
    for num, intent, fit, rec, impact, count, *_rest in all_recs:
        label = rec["change_type"].replace("_", " ").title()
        rec_labels[num]   = (label, intent, impact)
        status_lookup[num] = rec.get("systemic_status", "NEUTRAL")

    TH  = ("padding:8px 12px;text-align:left;white-space:nowrap;"
           "background:#37474f;color:#fff;font-size:12px")
    THR = TH + ";text-align:right"
    THC = TH + ";text-align:center"
    TD  = "padding:7px 12px;border-bottom:1px solid #eceff1;font-size:12px;vertical-align:top"
    TDR = TD + ";text-align:right"
    TDC = TD + ";text-align:center"

    dir_badge = {
        "down":  ("background:#ffebee;color:#c62828", "DOWN"),
        "up":    ("background:#e8f5e9;color:#2e7d32", "UP"),
        "mixed": ("background:#fff8e1;color:#e65100", "MIXED"),
    }
    status_badge = {
        "VALIDATED": ("background:#e8f5e9;color:#1b5e20", "VALIDATED"),
        "REJECTED":  ("background:#ffebee;color:#b71c1c", "REJECTED"),
        "ISOLATED":  ("background:#fff8e1;color:#e65100", "ISOLATED"),
        "NEUTRAL":   ("background:#f5f5f5;color:#616161", "NEUTRAL"),
        "":          ("background:#f5f5f5;color:#616161", "--"),
    }

    def _pct(num, denom):
        if denom and denom != 0:
            return f"{num / abs(denom) * 100:+.1f}%"
        return "n/a"

    def _render_row(num_label, kw, sc, cc, vb, va, fat, di, is_combined=False):
        delta       = va - vb
        flagged_pct = f"{cc/sc*100:.0f}%" if sc > 0 else "n/a"
        pct_b       = _pct(vb, fat)
        pct_a_denom = fat + (vb - va)
        pct_a       = _pct(va, pct_a_denom)
        badge_style, badge_txt = dir_badge.get(di, ("", di.upper()))
        vb_col    = "#c62828" if vb < 0 else "#2e7d32"
        va_col    = "#c62828" if va < 0 else "#2e7d32"
        delta_col = "#2e7d32" if abs(va) < abs(vb) else "#c62828"
        delta_str = f"{delta:+,}" if cc > 0 else "n/a"
        row_style = 'style="background:#f5f5f5"' if is_combined else ""
        label, intent, impact = rec_labels.get(num_label, ("", "", 0))
        display_label = ("<b>Combined</b>" if is_combined
                        else f"<b>[{num_label}]</b> {label}")

        # Status badge (only for individual rows, not combined)
        if is_combined:
            st_html = ""
        else:
            st = status_lookup.get(num_label, "NEUTRAL")
            st_style, st_txt = status_badge.get(st, ("", st))
            st_html = (f'<td style="{TDC}"><span style="padding:2px 8px;border-radius:3px;'
                       f'font-weight:bold;font-size:11px;{st_style}">{st_txt}</span></td>')

        return f"""
<tr {row_style}>
  <td style="{TD}">{display_label}</td>
  <td style="{TD}">{kw}</td>
  <td style="{TDR}">{sc:,}</td>
  <td style="{TDR}">{cc:,} <span style="color:#9e9e9e">({flagged_pct})</span></td>
  <td style="{TDR};color:{vb_col}">{vb:+,} <span style="color:#9e9e9e;font-size:11px">({pct_b})</span></td>
  <td style="{TDR};color:{va_col}">{va:+,} <span style="color:#9e9e9e;font-size:11px">({pct_a})</span></td>
  <td style="{TDR};color:{delta_col};font-weight:bold">{delta_str}</td>
  <td style="{TDC}"><span style="padding:2px 8px;border-radius:3px;font-weight:bold;font-size:11px;{badge_style}">{badge_txt}</span></td>
  {st_html}
</tr>"""

    rows = ""
    for si in systemic_impacts:
        if si.get("is_combined"):
            continue
        rows += _render_row(
            si["rec_num"], si["model_keyword"] or "all",
            si["scope_count"], si["criteria_count"],
            si["variance_before"], si["variance_after"],
            si["flagged_ai_total"], si["direction"],
        )

    # Combined row (if present)
    for si in systemic_impacts:
        if si.get("is_combined"):
            rows += _render_row(
                "ALL", si["model_keyword"],
                si["scope_count"], si["criteria_count"],
                si["variance_before"], si["variance_after"],
                si["flagged_ai_total"], si["direction"],
                is_combined=True,
            )
            break

    return f"""
<h3 style="margin:28px 0 6px 0;font-size:15px;color:#37474f;border-top:2px solid #eceff1;
           padding-top:18px">Systemic Impact Estimate</h3>
<p style="margin:0 0 10px 0;font-size:12px;color:#757575">
  Systemic impact is computed <b>before</b> recommendations are finalized.
  VALIDATED = fix narrows MAN-AI gap and is recommended.
  REJECTED = fix widens gap; recommendation has been replaced with directional-guard guidance.
  ISOLATED = 0 records match criteria; individual fix only.
  Each row tests its fix <i>in isolation</i>. The shaded Combined row shows simultaneous effect.
  <i>Variance = MAN PRJ minus AI PRJ (flagged records only). % = gap as % of current AI.</i>
</p>
<table style="width:100%;border-collapse:collapse;font-size:13px">
  <thead>
    <tr>
      <th style="{TH}">Change</th>
      <th style="{TH}">Model</th>
      <th style="{THR}">In Scope</th>
      <th style="{THR}">Flagged</th>
      <th style="{THR}">MAN-AI Before</th>
      <th style="{THR}">MAN-AI After</th>
      <th style="{THR}">AI Change</th>
      <th style="{THC}">AI Impact</th>
      <th style="{THC}">Status</th>
    </tr>
  </thead>
  <tbody>{rows}
  </tbody>
</table>"""


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
    gap_color = "#c62828" if total_gap < 0 else "#2e7d32"

    # Build status lookup: (intent, fit) -> systemic_status
    status_by_group = {}
    for num, intent, fit, rec, impact, count, *_rest in all_recs:
        status_by_group[(intent, fit)] = rec.get("systemic_status", "NEUTRAL")

    status_badge_style = {
        "VALIDATED": ("background:#e8f5e9;color:#1b5e20", "VALIDATED"),
        "REJECTED":  ("background:#ffebee;color:#b71c1c", "REJECTED"),
        "ISOLATED":  ("background:#fff8e1;color:#e65100", "ISOLATED"),
        "NEUTRAL":   ("background:#f0f0f0;color:#616161", "NEUTRAL"),
    }

    # Build comment rows
    TD  = "padding:8px 12px;border-bottom:1px solid #e0e0e0;vertical-align:top"
    TDR = TD + ";text-align:right"

    comment_rows = ""
    for a in sorted(analyses, key=lambda x: abs(x["unit_gap"]), reverse=True):
        rec      = a["recommendation"]
        gap      = a["unit_gap"]
        gap_col  = "#c62828" if gap < 0 else "#2e7d32"
        conf     = rec["confidence"].upper()
        conf_col = {"HIGH": "#1b5e20", "MEDIUM": "#e65100", "LOW": "#757575"}.get(conf, "#000")
        note_full = a["note"] or ""
        cust = re.sub(r'\b(INC\.?|LLC|CORP\.?|LTD\.?|CO\.?)\s*$', '',
                      a.get("customer", ""), flags=re.I).strip().rstrip(",.")

        # Per-comment systemic status badge
        st     = status_by_group.get((a["intent"], a["fit"]), "NEUTRAL")
        st_sty, st_txt = status_badge_style.get(st, ("background:#f0f0f0;color:#616161", st))
        st_badge = (f'<span style="{st_sty};padding:1px 6px;border-radius:3px;'
                    f'font-weight:bold;font-size:10px;margin-bottom:4px;'
                    f'display:inline-block">{st_txt}</span><br>')

        comment_rows += f"""
<tr>
  <td style="{TD}">
    <b style="font-size:13px">{cust}</b><br>
    <span style="color:#616161;font-size:12px">{a.get('mstyle','')} &nbsp;|&nbsp; {a.get('brand','')[:28]}</span>
  </td>
  <td style="{TD};font-size:12px;color:#424242;max-width:220px">
    <i>"{note_full[:120]}{"..." if len(note_full) > 120 else ""}"</i>
  </td>
  <td style="{TD}">
    <span style="background:#e3f2fd;color:#0d47a1;padding:2px 7px;border-radius:3px;font-size:12px">{a['ai_model']}</span>
  </td>
  <td style="{TDR};color:{gap_col};font-weight:bold">{gap:+,}u</td>
  <td style="{TD};font-size:12px;max-width:300px;word-wrap:break-word">
    {st_badge}{rec['proposed_change']}
  </td>
  <td style="{TD};text-align:center"><span style="color:{conf_col};font-weight:bold;font-size:12px">{conf}</span></td>
</tr>"""

    report_path_str = str(report_path)
    claude_cmd = "implement ai training recommendations"

    html = f"""<html>
<body style="font-family:Arial,sans-serif;font-size:14px;color:#212121;max-width:1000px;margin:0 auto">

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

<p style="margin:0 0 10px 0;font-size:12px;color:#616161;background:#f5f5f5;padding:8px 12px;border-radius:4px">
  <b>How to read this:</b> Recommendations are validated against systemic impact before being shown.
  <span style="background:#e8f5e9;color:#1b5e20;padding:1px 6px;border-radius:3px;font-weight:bold;font-size:10px">VALIDATED</span>
  = fix narrows MAN-AI gap.
  <span style="background:#ffebee;color:#b71c1c;padding:1px 6px;border-radius:3px;font-weight:bold;font-size:10px">REJECTED</span>
  = fix would widen gap; see directional-guard alternative.
  <span style="background:#fff8e1;color:#e65100;padding:1px 6px;border-radius:3px;font-weight:bold;font-size:10px">ISOLATED</span>
  = one-off item, no systemic pattern.
</p>

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

{_build_systemic_html(systemic_impacts, all_recs)}

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

    # Step 1: Fetch AI Training comments
    print("\n[1/5] Fetching AI Training comments...", flush=True)
    comments, note_fid = fetch_ai_training_comments(args.days, note_fid)

    if not comments:
        print("  No new AI Training comments to process.\n")
        print("Done.", flush=True)
        return

    # Step 2: Fetch projections
    print("\n[2/5] Fetching projection records...", flush=True)
    keys = {sval(c, C_ACCT_MSTYLE) for c in comments if sval(c, C_ACCT_MSTYLE)}
    projections = fetch_projections(keys, man_fids, l4w_fid)

    # Step 3: Analyze each comment (raw recommendations, not yet validated)
    print("\n[3/5] Analyzing comments...", flush=True)
    analyses = []
    for c in comments:
        key  = sval(c, C_ACCT_MSTYLE)
        proj = projections.get(key)
        a    = analyze_comment(c, proj, man_fids, note_fid, l4w_fid)
        analyses.append(a)
        print(f"  {key:<35}  intent={a['intent']:<12}  fit={a['fit']:<18}  "
              f"gap={a['unit_gap']:+,}u")

    grouped = aggregate(analyses)

    # Step 4a: Build flat recommendation list FIRST (before systemic check)
    print("\n[4a/5] Building recommendation list...", flush=True)
    all_recs = _build_all_recs(grouped)
    print(f"  {len(all_recs)} unique recommendation(s) identified.")

    # Step 4b: Estimate systemic impact across ALL active projections
    print("\n[4b/5] Estimating systemic impact across all active projections...",
          flush=True)
    systemic_impacts = estimate_systemic_impact(all_recs, man_fids)

    # Step 4c: Validate -- replace any fix that widens the gap with a new rec
    print("\n[4c/5] Validating recommendations against systemic impact...", flush=True)
    all_recs = validate_and_override_recs(all_recs, systemic_impacts, grouped)
    for num, intent, fit, rec, impact, count, *_ in all_recs:
        status = rec.get("systemic_status", "NEUTRAL")
        print(f"  [{num}] {intent}/{fit} -> {status}")

    # Step 5: Build report with validated recommendations
    print("\n[5/5] Building report and sending email...", flush=True)
    report_md = build_report(analyses, grouped, all_recs, run_date, args.days)

    # Append systemic impact section to the markdown report
    if systemic_impacts:
        sys_lines = [
            "",
            "## 5. Systemic Impact Estimate",
            "",
            ("*Systemic impact was computed BEFORE recommendations were finalized. "
             "VALIDATED = fix narrows MAN-AI gap. REJECTED = fix widens gap. "
             "ISOLATED = 0 records match criteria. "
             "Variance = MAN PRJ 26w - AI PRJ 26w (flagged records only). "
             "After = MAN - estimated new AI once fix is applied.*"),
            "",
            ("| Change # | Model | In Scope | Flagged | "
             "MAN-AI Before | Before% | MAN-AI After | After% | AI Change | Direction | Status |"),
            "|---|---|---|---|---|---|---|---|---|---|---|",
        ]
        for si in systemic_impacts:
            num   = si["rec_num"]
            kw    = si["model_keyword"] or "all"
            sc    = si["scope_count"]
            cc    = si["criteria_count"]
            vb    = si["variance_before"]
            va    = si["variance_after"]
            fat   = si["flagged_ai_total"]
            delta = va - vb
            di    = si["direction"].upper()
            pct_flagged = f" ({cc/sc*100:.0f}%)" if sc > 0 else ""
            pct_b = f"{vb/fat*100:+.1f}%" if fat else "n/a"
            new_ai_est = fat + (vb - va)
            pct_a = f"{va/new_ai_est*100:+.1f}%" if new_ai_est else "n/a"
            is_comb = si.get("is_combined", False)
            label = "**Combined**" if is_comb else f"[{num}]"
            # Get status for this rec_num
            st = next(
                (rec.get("systemic_status", "") for n, i, f, rec, *_ in all_recs
                 if n == num),
                "COMBINED" if is_comb else ""
            )
            sys_lines.append(
                f"| {label} | {kw} | {sc:,} | {cc:,}{pct_flagged} | "
                f"{vb:+,} | {pct_b} | {va:+,} | {pct_a} | {delta:+,} | {di} | {st} |"
            )
        report_md += "\n" + "\n".join(sys_lines) + "\n"

    report_path = ANALYSIS_DIR / f"ai_training_{run_date}.md"
    report_path.write_text(report_md, encoding="utf-8")
    print(f"  Report saved -> {report_path}")

    # Send email
    subject    = (f"AI Training Review {run_date} -- "
                  f"{len(analyses)} comments, {len(all_recs)} recommendations")
    email_html = build_email_html(
        analyses, all_recs, report_path, run_date, args.days,
        systemic_impacts=systemic_impacts,
    )
    send_email(subject, email_html, report_path, args.dry_run)

    # Mark processed comments as Reviewed in QB so they don't re-appear
    comment_rids = [str(int(fval(c, C_RECORD_ID))) for c in comments]
    mark_reviewed_in_qb(comment_rids, args.dry_run)

    # Print summary
    total_gap = sum(a["unit_gap"] for a in analyses)
    statuses  = [rec.get("systemic_status", "NEUTRAL")
                 for _, _, _, rec, *_ in all_recs]
    print(f"\n{'='*60}", flush=True)
    print(f"  COMPLETE  |  {len(analyses)} comments  |  "
          f"Net gap: {total_gap:+,}u  |  {len(all_recs)} recommendations",
          flush=True)
    for s in ["VALIDATED", "REJECTED", "ISOLATED", "NEUTRAL"]:
        n = statuses.count(s)
        if n:
            print(f"    {s}: {n}", flush=True)
    print(f"{'='*60}\n", flush=True)


if __name__ == "__main__":
    main()
