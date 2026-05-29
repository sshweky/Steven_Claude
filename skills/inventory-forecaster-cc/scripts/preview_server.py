"""Live preview server for the AI Training Review HTML page.

Runs on http://localhost:8744. The page POSTs preview-update requests to
/preview and gets back recomputed AI new + impact numbers. No Claude turn
needed for each preview.

Endpoints:
  POST /preview  body: {id, key, rule_fn_id, scope_key, text}
                 returns: {ai_new, item_ai_after, item_gap_after,
                          sys_gap_after, interpretation, ok}

Interpretation handles common modification patterns:
  - Scope narrowing: "only apply to Chewy", "skip Burlington", "only Walmart"
  - Threshold change: "raise floor to 0.95", "lower threshold to 0.5"
  - Week count change: "zero W1-W4 instead of W1-W3", "use 4 weeks"
  - Status filter: "only Active: Replen", "skip new launches"
  - Owned brand exclusion: "skip owned brands"

Run:
  python scripts/preview_server.py
"""
import json
import os
import re
import sys
import http.server
import socketserver
from math import gcd
from functools import reduce
from pathlib import Path

PORT = 8744
HERE = Path(__file__).parent
SKILL_DIR = HERE.parent
SCOPE_FILE = SKILL_DIR / "analysis" / "systemic_data_2026-05-29.json"

OWNED_BRANDS = {
    "ARM & HAMMER", "A&H", "BURT'S BEES", "BIOSILK", "CHI",
    "VIBRANT LIFE", "GLAD FOR PETS", "KINGSFORD", "GLADWARE",
}


def snap(v, mp):
    return round(v / mp) * mp if mp > 0 else round(v)


def infer_mp(arr):
    nz = [x for x in arr if x > 0]
    if not nz:
        return 1
    return max(1, reduce(gcd, nz))


def f92_apply(rec, params):
    mp = infer_mp(rec["ai"])
    floor = snap(rec["l13w"] * params["floor_mult"], mp)
    new_ai = [max(v, floor) if v > 0 else 0 for v in rec["ai"]]
    if (params.get("restore_zeros", True)
            and len(new_ai) > 16
            and new_ai[16] == 0
            and rec["msty_opn"] > params.get("restore_thresh", 4.0) * floor):
        new_ai[16] = floor
    return new_ai


def f93_apply(rec, params):
    new_ai = list(rec["ai"])
    n = min(params.get("num_weeks", 3), len(new_ai))
    mode = params.get("coverage_mode", "greedy")
    if mode == "greedy":
        opn = rec["cust_opn"]
        for i in range(n):
            if opn >= new_ai[i] and new_ai[i] > 0:
                opn -= new_ai[i]
                new_ai[i] = 0
    elif mode == "full":
        s = sum(new_ai[:n])
        if rec["cust_opn"] >= s and s > 0:
            for i in range(n):
                new_ai[i] = 0
    elif mode == "per_week":
        thr = params.get("per_week_threshold", 0.8)
        for i in range(n):
            if rec["cust_opn"] >= thr * new_ai[i] and new_ai[i] > 0:
                new_ai[i] = 0
    elif mode == "force_zero":
        # "Always zero W1-WN regardless of PO size" (per planner literal intent)
        for i in range(n):
            new_ai[i] = 0
    return new_ai


def f94_apply(rec, params):
    new_ai = list(rec["ai"])
    w = params.get("week_to_zero", 10) - 1
    if 0 <= w < len(new_ai):
        new_ai[w] = 0
    return new_ai


RULE_FNS = {"f92": f92_apply, "f93": f93_apply, "f94": f94_apply}
DEFAULT_PARAMS = {
    "f92": {"floor_mult": 0.85, "restore_zeros": True, "restore_thresh": 4.0},
    "f93": {"num_weeks": 3, "coverage_mode": "greedy", "per_week_threshold": 0.8},
    "f94": {"week_to_zero": 10},
}


def interpret(text, rule_fn_id):
    """Parse plain English into (params dict, scope filter dict, summary string).
    Returns (params, scope_filter, summary, errors).
    """
    t = text.lower().strip()
    params = dict(DEFAULT_PARAMS[rule_fn_id])
    scope_filter = {}  # keys: customers_include, customers_exclude, statuses, exclude_owned_brands
    parts = []
    errors = []

    # Scope narrowing / exclusion
    m = re.search(r"only\s+(?:apply\s+to\s+)?(.+?)(?:\s+specifically|\.|,|$)", t)
    if m:
        scope_text = m.group(1).strip()
        # try to extract a customer name
        cust_hint = scope_text.upper()
        # common short names
        cust_map = {
            "CHEWY": "CHEWY.COM", "CHEWY.COM": "CHEWY.COM",
            "WALMART": "WAL MART STORES", "WAL-MART": "WAL MART STORES", "WAL MART": "WAL MART STORES",
            "AMAZON": "AMAZON", "ACE": "ACE HARDWARE CORPORATION",
            "BURLINGTON": "BURLINGTON COAT FACTORY", "C&S": "C & S WHOLESALE",
            "C & S": "C & S WHOLESALE",
        }
        matched = None
        for key, full in cust_map.items():
            if key in cust_hint:
                matched = full
                break
        if matched:
            scope_filter["customers_include"] = [matched]
            parts.append(f"scope narrowed to customer '{matched}'")
        elif "active: replen" in t or "active replen" in t:
            scope_filter["statuses"] = ["Active: Replen"]
            parts.append("scope narrowed to Active: Replen items")
        else:
            errors.append(f"Could not identify the customer/scope in '{scope_text}'.")

    # Skip / exclude
    m = re.search(r"(skip|exclude)\s+(.+?)(?:\.|,|$)", t)
    if m:
        target = m.group(2).strip().upper()
        if "OWNED" in target or "OWN BRAND" in target:
            scope_filter["exclude_owned_brands"] = True
            parts.append("excluding owned brands (A&H, Burt's Bees, BioSilk, CHI, Vibrant Life, Glad for Pets, Kingsford, GladWare)")
        elif "OFF-PRICE" in target or "OFF PRICE" in target or "BURLINGTON" in target:
            scope_filter["exclude_off_price"] = True
            parts.append("excluding off-price accounts (status starts with 'A: Off-Price')")
        elif "NEW LAUNCH" in target or "NEW" in target:
            scope_filter["exclude_status_substrs"] = ["NEW"]
            parts.append("excluding new-launch items (status contains 'NEW')")
        else:
            # Try customer name
            for key, full in {"CHEWY": "CHEWY.COM", "WALMART": "WAL MART STORES",
                              "ACE": "ACE HARDWARE CORPORATION",
                              "BURLINGTON": "BURLINGTON COAT FACTORY",
                              "C&S": "C & S WHOLESALE", "C & S": "C & S WHOLESALE"}.items():
                if key in target:
                    scope_filter.setdefault("customers_exclude", []).append(full)
                    parts.append(f"excluding customer '{full}'")
                    break

    # Threshold / multiplier changes (F92)
    if rule_fn_id == "f92":
        # "raise floor to X" / "set floor to X" / "use floor 0.95"
        m = re.search(r"(?:raise|lower|set|change|use|make).*?floor.*?(?:to|=|at)?\s*(0?\.\d{1,2}|\d+\.\d{1,2}|\d+%)", t)
        if not m:
            m = re.search(r"floor\s+(?:to|=|at|of)\s+(0?\.\d{1,2}|\d+\.\d{1,2}|\d+%)", t)
        if m:
            val_str = m.group(1)
            if "%" in val_str:
                val = float(val_str.replace("%", "")) / 100
            else:
                val = float(val_str)
            if val > 1.5:
                val = val / 100  # interpret e.g. "95" as 0.95
            params["floor_mult"] = val
            parts.append(f"floor_mult = {val:.2f}")
        # "restore zeros off" / "no restore"
        if "no restore" in t or "do not restore" in t or "skip restore" in t:
            params["restore_zeros"] = False
            parts.append("restore_zeros = false")

    # F93: week count, coverage mode
    if rule_fn_id == "f93":
        m = re.search(r"(?:zero|use|apply)\s+w?1?[-\s]+w?(\d+)", t)
        if not m:
            m = re.search(r"(\d+)\s+weeks?", t)
        if m:
            n = int(m.group(1))
            if 1 <= n <= 8:
                params["num_weeks"] = n
                parts.append(f"num_weeks = {n}")
        if "regardless of po" in t or "always zero" in t or "force zero" in t:
            params["coverage_mode"] = "force_zero"
            parts.append("coverage_mode = force_zero (zero regardless of PO size)")
        elif "full coverage" in t or "full only" in t:
            params["coverage_mode"] = "full"
            parts.append("coverage_mode = full")
        elif "per week" in t or "per-week" in t:
            params["coverage_mode"] = "per_week"
            parts.append("coverage_mode = per_week")
        m = re.search(r"threshold\s+(?:to|=|at|of)\s+(0?\.\d{1,2}|\d+\.\d{1,2}|\d+%)", t)
        if m:
            val_str = m.group(1)
            val = float(val_str.replace("%", "")) / 100 if "%" in val_str else float(val_str)
            if val > 1.5:
                val = val / 100
            params["per_week_threshold"] = val
            parts.append(f"per_week_threshold = {val:.2f}")

    # F94: week to zero
    if rule_fn_id == "f94":
        m = re.search(r"w(?:eek)?\s*(\d+)", t)
        if m:
            w = int(m.group(1))
            if 1 <= w <= 26:
                params["week_to_zero"] = w
                parts.append(f"week_to_zero = {w}")

    summary = "; ".join(parts) if parts else "no recognized modifications"
    return params, scope_filter, summary, errors


def filter_scope(records, scope_filter, key_to_cust=None, key_to_status=None):
    """Apply scope filters to the systemic record list."""
    out = records
    if scope_filter.get("customers_include"):
        wanted = set(c.upper() for c in scope_filter["customers_include"])
        out = [r for r in out if r.get("cust", "").upper() in wanted]
    if scope_filter.get("customers_exclude"):
        excl = set(c.upper() for c in scope_filter["customers_exclude"])
        out = [r for r in out if r.get("cust", "").upper() not in excl]
    if scope_filter.get("statuses"):
        wanted = scope_filter["statuses"]
        out = [r for r in out if any(s in (r.get("item_status", "") or "") for s in wanted)]
    if scope_filter.get("exclude_owned_brands"):
        out = [r for r in out if (r.get("brand", "") or "").upper() not in OWNED_BRANDS]
    return out


# Load scope data + augment records with cust/brand/status info (the slim
# JSON dropped them; reload the full file).
def load_full_recs():
    full_file = SKILL_DIR / "analysis" / "systemic_data_2026-05-29.json"
    if not full_file.exists():
        return [], {}
    recs = json.loads(full_file.read_text())
    return recs, {r["key"]: r for r in recs}


FULL_RECS, BY_KEY = load_full_recs()
SCOPE_BY_RULE = {
    "f92": [r for r in FULL_RECS if r.get("model") == "Retailer WOS (POS)" and r.get("l13w", 0) > 0],
    "f93": [r for r in FULL_RECS if r.get("model") in ("Seasonal Baseline", "Sparse Intermittent") and r.get("cust_opn", 0) > 0],
    "f94": [BY_KEY["13640-BB21626"]] if "13640-BB21626" in BY_KEY else [],
}


def compute_preview(req):
    rid = req.get("id")
    key = req.get("key")
    rule_fn_id = req.get("rule_fn_id")
    scope_key = req.get("scope_key") or rule_fn_id
    text = req.get("text", "")

    if rule_fn_id not in RULE_FNS:
        return {"ok": False, "error": f"Unknown rule_fn_id: {rule_fn_id}"}

    fn = RULE_FNS[rule_fn_id]
    params, scope_filter, summary, errors = interpret(text, rule_fn_id)

    if errors:
        return {"ok": False, "errors": errors, "interpretation": summary,
                "hint": "Try more specific text, e.g. 'only apply to Chewy.com' or 'raise floor to 0.95'"}

    # Item recompute
    item_rec = BY_KEY.get(key)
    if not item_rec:
        return {"ok": False, "error": f"Item not found: {key}"}
    new_ai = fn(item_rec, params)

    # Systemic recompute with scope filter
    scope_records = filter_scope(SCOPE_BY_RULE.get(scope_key, []), scope_filter)
    sys_gap_after = 0
    for r in scope_records:
        na = fn(r, params)
        sys_gap_after += r["man_total"] - sum(na)

    return {
        "ok": True,
        "id": rid,
        "ai_new": new_ai,
        "item_ai_after": sum(new_ai),
        "item_gap_after": item_rec["man_total"] - sum(new_ai),
        "sys_scope": len(scope_records),
        "sys_gap_after": sys_gap_after,
        "interpretation": summary,
        "applied_params": params,
        "applied_scope_filter": scope_filter,
    }


class Handler(http.server.BaseHTTPRequestHandler):
    def _send_cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def do_OPTIONS(self):
        self.send_response(200)
        self._send_cors()
        self.end_headers()

    def do_POST(self):
        if self.path != "/preview":
            self.send_response(404)
            self._send_cors()
            self.end_headers()
            return
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode("utf-8")
        try:
            req = json.loads(body)
            result = compute_preview(req)
        except Exception as e:
            result = {"ok": False, "error": str(e)}
        out = json.dumps(result).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self._send_cors()
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, fmt, *args):
        sys.stderr.write(f"  [preview-server] {fmt % args}\n")


if __name__ == "__main__":
    print(f"Loaded {len(FULL_RECS)} systemic records")
    for rid, scope in SCOPE_BY_RULE.items():
        print(f"  {rid}: {len(scope)} records in scope")
    print(f"\nPreview server listening on http://localhost:{PORT}/preview")
    print("POST {id, key, rule_fn_id, scope_key, text} to /preview")
    print("Ctrl-C to stop.\n")
    with socketserver.TCPServer(("", PORT), Handler) as httpd:
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nServer stopped.")
