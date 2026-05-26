"""
dismiss_reviewed_alerts.py

Finds Projection records where the most recent flag comment has FLAG =
"Reviewed" or "FYI", then clears the AI_ALERT field on those records.

Run daily at 7:20am via daily_alert_dismiss.ps1.

Logic:
  1. Fetch all Projection Comments sorted newest-first.
  2. For each Acct_MStyle_Key_ keep only the most recent comment's FLAG.
  3. Collect keys where that FLAG is "Reviewed" or "FYI".
  4. Bulk-clear AI_ALERT on those Projections via REST.
"""

import json, sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from config import QB_REALM, QB_USER_TOKEN, QB_PROJ_TABLE

COMMENTS_TABLE = "bpt35zccg"

# Field IDs -- Projection Comments
C_ACCT_MSTYLE = 7    # text key linking back to Projections
C_FLAG        = 31   # text-multiple-choice
C_DATE        = 1    # QB built-in DATE_CREATED (sort key)

# Field IDs -- Projections
P_KEY      = 292    # Acct_MStyle_Key_  (merge key for bulk upsert)
P_AI_ALERT = 1538   # AI_ALERT text field

CLEAR_FLAGS = {"Reviewed", "FYI"}
BATCH_SIZE  = 500

HEADERS = {
    "QB-Realm-Hostname": QB_REALM,
    "Authorization":     f"QB-USER-TOKEN {QB_USER_TOKEN}",
    "Content-Type":      "application/json",
}


def _qb_post(path, body):
    import urllib.request, urllib.error
    url  = f"https://api.quickbase.com/v1/{path}"
    data = json.dumps(body).encode()
    req  = urllib.request.Request(url, data=data, headers=HEADERS, method="POST")
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


def fetch_comments():
    """Page through all Projection Comments, newest first. Returns list of QB rows."""
    rows, skip = [], 0
    while True:
        result = _qb_post("records/query", {
            "from":   COMMENTS_TABLE,
            "select": [C_ACCT_MSTYLE, C_FLAG, C_DATE],
            "sortBy": [{"fieldId": C_DATE, "order": "DESC"}],
            "options": {"top": 1000, "skip": skip},
        })
        batch = result.get("data", [])
        rows.extend(batch)
        if len(batch) < 1000:
            break
        skip += 1000
        time.sleep(0.2)
    return rows


def latest_flag_per_key(rows):
    """Walk sorted-desc rows; first occurrence per key = most recent comment."""
    seen = {}
    for row in rows:
        key  = (row.get(str(C_ACCT_MSTYLE)) or {}).get("value", "")
        flag = (row.get(str(C_FLAG))         or {}).get("value", "")
        if key and key not in seen:
            seen[key] = flag
    return seen


def clear_alerts(keys):
    """Bulk-set AI_ALERT = '' for the given Acct_MStyle_Key_ values."""
    total = 0
    for i in range(0, len(keys), BATCH_SIZE):
        batch = keys[i : i + BATCH_SIZE]
        _qb_post("records", {
            "to":   QB_PROJ_TABLE,
            "data": [
                {str(P_KEY): {"value": k}, str(P_AI_ALERT): {"value": ""}}
                for k in batch
            ],
            "mergeFieldId":   P_KEY,
            "fieldsToReturn": [],
        })
        total += len(batch)
        print(f"  cleared {total}/{len(keys)}", flush=True)
        time.sleep(0.1)
    return total


def main():
    print("[1/3] Fetching Projection Comments...", flush=True)
    rows = fetch_comments()
    print(f"      {len(rows):,} comments", flush=True)

    print("[2/3] Finding latest flag per key...", flush=True)
    flag_map  = latest_flag_per_key(rows)
    to_clear  = [k for k, f in flag_map.items() if f in CLEAR_FLAGS]
    print(f"      {len(flag_map):,} keys total, {len(to_clear):,} to clear "
          f"({', '.join(sorted(CLEAR_FLAGS))})", flush=True)

    if not to_clear:
        print("[3/3] Nothing to clear.", flush=True)
        return

    print(f"[3/3] Clearing AI_ALERT for {len(to_clear):,} projections...", flush=True)
    n = clear_alerts(to_clear)
    print(f"      Done -- {n:,} records updated.", flush=True)


if __name__ == "__main__":
    main()
