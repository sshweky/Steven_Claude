"""
proj_comment_router.py

Automatically routes Projection Comments in Quickbase:
  - First comment by an exec (CEO/VP/Director) -> Send To = planner, Flag = "Needs Action"
  - Any reply -> Send To = author of the previous comment
    - Planner replying  -> Flag = "Planner Response"
    - Exec replying     -> Flag = "Manager Response"

Run every 5 minutes via Windows Task Scheduler.
"""

import requests
import sys
from datetime import datetime, timedelta, timezone

BASE_URL = "https://api.quickbase.com/v1"
HEADERS = {
    "QB-Realm-Hostname": "pim.quickbase.com",
    "Authorization": "QB-USER-TOKEN b39re4_mkf7_du2buby24kr7d4hkcu9cpxn69s",
    "Content-Type": "application/json"
}

COMMENTS_TABLE = "bpt35zccg"

# Projection Comments FIDs
FID_RID           = 3
FID_DATE_CREATED  = 1
FID_FLAG          = 31
FID_ACCT_MSTYLE   = 7
FID_AUTHOR_TEXT   = 40
FID_AUTHOR_USER   = 42   # user field for comment author
FID_SEND_TO_USER  = 43   # user field we are routing into
FID_INV_MGR_USER  = 39   # lookup of planner (Projections FID 1587)

# Executives matched by QB display name
EXECUTIVES = {"Steven Shweky", "Nancy Lee", "Mikey Scott"}


# ---------------------------------------------------------------------------
# QB helpers
# ---------------------------------------------------------------------------

def qb_query(payload):
    r = requests.post(f"{BASE_URL}/records/query", headers=HEADERS, json=payload, timeout=30)
    r.raise_for_status()
    return r.json().get("data", [])


def qb_update(record_id, send_to_id, flag):
    payload = {
        "to": COMMENTS_TABLE,
        "data": [{
            str(FID_RID):          {"value": record_id},
            str(FID_SEND_TO_USER): {"value": {"id": send_to_id}},
            str(FID_FLAG):         {"value": flag}
        }],
        "fieldsToReturn": [FID_RID]
    }
    r = requests.post(f"{BASE_URL}/records", headers=HEADERS, json=payload, timeout=30)
    r.raise_for_status()
    return r.json()


# ---------------------------------------------------------------------------
# Query helpers
# ---------------------------------------------------------------------------

def get_unrouted_comments():
    """Recent comments (last 10 min) where Send To (User) is still blank."""
    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=10)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return qb_query({
        "from": COMMENTS_TABLE,
        "select": [FID_RID, FID_DATE_CREATED, FID_FLAG,
                   FID_ACCT_MSTYLE, FID_AUTHOR_TEXT,
                   FID_AUTHOR_USER, FID_SEND_TO_USER, FID_INV_MGR_USER],
        "where": f"{{43.TV.}} AND {{1.AF.{cutoff}}}",
        "sortBy": [{"fieldId": FID_DATE_CREATED, "order": "ASC"}],
        "options": {"top": 100}
    })


def get_thread(acct_mstyle):
    """All comments for one projection, oldest first."""
    return qb_query({
        "from": COMMENTS_TABLE,
        "select": [FID_RID, FID_DATE_CREATED, FID_AUTHOR_USER, FID_FLAG],
        "where": "{7.EX.'" + acct_mstyle + "'}",
        "sortBy": [{"fieldId": FID_DATE_CREATED, "order": "ASC"}],
        "options": {"top": 500}
    })


# ---------------------------------------------------------------------------
# Routing logic
# ---------------------------------------------------------------------------

def determine_routing(comment, thread):
    """
    Returns (send_to_user_id, flag) or (None, None) if no routing applies.
    """
    my_rid       = comment[str(FID_RID)]["value"]
    author_user  = comment[str(FID_AUTHOR_USER)]["value"] or {}
    author_name  = author_user.get("name", "")
    author_id    = author_user.get("id")
    planner      = comment[str(FID_INV_MGR_USER)]["value"] or {}
    planner_id   = planner.get("id")

    # Find this comment's position in the full thread
    my_index = next(
        (i for i, r in enumerate(thread) if r[str(FID_RID)]["value"] == my_rid),
        None
    )
    if my_index is None:
        return None, None

    is_exec    = author_name in EXECUTIVES
    is_planner = bool(planner_id and author_id and planner_id == author_id)

    # ---- FIRST comment in thread ----------------------------------------
    if my_index == 0:
        if is_exec and planner_id:
            return planner_id, "Needs Action"
        return None, None

    # ---- Reply: Send To = previous commenter ----------------------------
    prev_author = thread[my_index - 1][str(FID_AUTHOR_USER)]["value"] or {}
    prev_id     = prev_author.get("id")
    if not prev_id:
        return None, None

    if is_planner:
        flag = "Planner Response"
    elif is_exec:
        flag = "Manager Response"
    else:
        # Non-exec, non-planner reply — keep existing flag or use FYI
        flag = comment[str(FID_FLAG)]["value"] or "FYI"

    return prev_id, flag


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    comments = get_unrouted_comments()

    if not comments:
        print(f"{now}: No unrouted comments.")
        return

    print(f"{now}: {len(comments)} unrouted comment(s) found.")

    # Fetch each thread once, keyed by Acct#-MStyle
    threads = {}
    for c in comments:
        key = c[str(FID_ACCT_MSTYLE)]["value"]
        if key not in threads:
            threads[key] = get_thread(key)

    for c in comments:
        rid   = c[str(FID_RID)]["value"]
        key   = c[str(FID_ACCT_MSTYLE)]["value"]
        thread = threads.get(key, [])

        send_to_id, flag = determine_routing(c, thread)

        if send_to_id:
            try:
                qb_update(rid, send_to_id, flag)
                print(f"  Record {rid}: -> {send_to_id} [{flag}]")
            except Exception as e:
                print(f"  ERROR on record {rid}: {e}", file=sys.stderr)
        else:
            print(f"  Record {rid}: no routing rule matched — skipped.")


if __name__ == "__main__":
    main()
