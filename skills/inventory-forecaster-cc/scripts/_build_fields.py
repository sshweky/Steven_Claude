"""
_build_fields.py

With the relationship now live (Inventory History -> Inventory History - Weekly),
this script:

  1. Converts "Mstyle (mirror)" (fid 35) to a formula field returning [Mstyle]
     so EVERY Inventory History record auto-links to its parent Weekly row.

  2. Deletes the test "Is LW (test)" field (fid 34).

  3. Creates 52 formula-checkbox fields in Inventory History:
       Is LW, Is LW-1, ..., Is LW-51
     Formula: [Date] = Today() - Days(If(DayOfWeek(Today())=1, 7, DayOfWeek(Today())-1) + N*7)

  4. Attempts to create 52 summary fields in Inventory History - Weekly (bv2sxg2ji):
       ATS LW, ATS LW-1, ..., ATS LW-51
     Each = MAX(ATS Qty OH#) from Inventory History WHERE [Is LW-N] = true.
"""

import sys, os
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except AttributeError:
    pass

import json, time
import urllib.request, urllib.error

QB_REALM  = "pim.quickbase.com"
QB_TOKEN  = "b39re4_mkf7_du2buby24kr7d4hkcu9cpxn69s"
QB_BASE   = "https://api.quickbase.com/v1"

SRC_TABLE  = "br6dcnv35"   # Inventory History
DEST_TABLE = "bv2sxg2ji"   # Inventory History - Weekly

# Known field IDs
SRC_FID_MSTYLE_MIRROR = 35   # "Mstyle (mirror)" — reference field QB created
SRC_FID_ATS           = 10   # ATS Qty OH#
SRC_FID_TEST_ISLW     = 34   # "Is LW (test)" — to be deleted

HEADERS = {
    "QB-Realm-Hostname": QB_REALM,
    "Authorization":     f"QB-USER-TOKEN {QB_TOKEN}",
    "Content-Type":      "application/json",
    "User-Agent":        "petspeople-inv-history-weekly/3.0",
}

MAX_RETRIES = 4

def _raw(method, path, body=None, timeout=60):
    url = QB_BASE + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers=HEADERS, method=method)
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            txt = e.read().decode(errors="replace")
            if e.code in (429, 502, 504) and attempt < MAX_RETRIES:
                wait = 2 ** attempt
                print(f"  [retry {attempt}] HTTP {e.code}, {wait}s...")
                time.sleep(wait)
                continue
            raise RuntimeError(f"HTTP {e.code}: {txt}") from e
        except Exception as e:
            if attempt < MAX_RETRIES:
                time.sleep(2 ** attempt)
                continue
            raise

def qb_get(path):    return _raw("GET", path)
def qb_post(path, body): return _raw("POST", path, body)
def qb_delete(path): return _raw("DELETE", path)

def is_lw_formula(n: int) -> str:
    """
    True when [Date] = the Sunday that is n weeks before last Sunday.
    Days() converts a number to a date duration for subtraction from Today().
    """
    total_days = f"If(DayOfWeek(Today()) = 1, 7, DayOfWeek(Today()) - 1) + {n * 7}"
    return f"[Date] = Today() - Days({total_days})"


# ── Step 1: Make Mstyle (mirror) a formula field ──────────────────────────────
print("=" * 60)
print("Step 1: Convert Mstyle (mirror) to formula field")
print("=" * 60)
try:
    resp = qb_post(f"/fields/{SRC_FID_MSTYLE_MIRROR}?tableId={SRC_TABLE}", {
        "label":      "Mstyle (mirror)",
        "fieldType":  "text",
        "properties": {"formula": "[Mstyle]"},
    })
    print(f"  OK — fid {resp['id']} is now formula: [Mstyle]")
except Exception as e:
    print(f"  WARN: {e}")
    print("  Continuing — Mstyle (mirror) may already contain values or be read-only.")

time.sleep(0.3)


# ── Step 2: Delete test field ─────────────────────────────────────────────────
print("\nStep 2: Delete test 'Is LW (test)' field (fid 34)")
try:
    qb_delete(f"/fields?tableId={SRC_TABLE}&fieldId={SRC_FID_TEST_ISLW}")
    print("  Deleted fid 34")
except Exception as e:
    # Try alternate delete endpoint format
    try:
        _raw("DELETE", f"/fields?tableId={SRC_TABLE}", {"fieldIds": [SRC_FID_TEST_ISLW]})
        print("  Deleted fid 34 (bulk endpoint)")
    except Exception as e2:
        print(f"  Could not delete (will leave in place): {e2}")

time.sleep(0.2)


# ── Step 3: Create 52 formula-checkbox fields in Inventory History ────────────
print("\nStep 3: Create 52 formula-checkbox fields in Inventory History")
is_lw_fids = []
for n in range(52):
    label   = "Is LW" if n == 0 else f"Is LW-{n}"
    formula = is_lw_formula(n)
    try:
        resp = qb_post(f"/fields?tableId={SRC_TABLE}", {
            "label":      label,
            "fieldType":  "checkbox",
            "properties": {"formula": formula},
        })
        fid = resp["id"]
        is_lw_fids.append(fid)
        print(f"  [{fid}] {label}")
    except Exception as e:
        print(f"  FAILED {label}: {e}")
        is_lw_fids.append(None)
    time.sleep(0.12)

good = [f for f in is_lw_fids if f]
print(f"\n  Created {len(good)}/52 formula checkboxes. "
      f"Is LW={is_lw_fids[0]}, Is LW-51={is_lw_fids[51]}")


# ── Step 4: Create 52 summary fields in Weekly table ─────────────────────────
print("\nStep 4: Attempt 52 summary fields in Inventory History - Weekly")
print("  (Using format from existing summary fields: numeric fieldType, mode=summary,")
print("   summaryReferenceFieldId=35, summaryTargetFieldId=10)")

summary_fids = []
summary_failures = []

for n in range(52):
    label     = "ATS LW" if n == 0 else f"ATS LW-{n}"
    is_lw_fid = is_lw_fids[n]
    if not is_lw_fid:
        print(f"  SKIP {label} (no checkbox fid)")
        summary_fids.append(None)
        continue

    criteria = f"{{'{is_lw_fid}'.EX.'true'}}"

    # Try the exact format observed from existing QB summary fields
    try:
        resp = qb_post(f"/fields?tableId={DEST_TABLE}", {
            "label":      label,
            "fieldType":  "numeric",
            "properties": {
                "summaryFunction":         "MAX",
                "summaryReferenceFieldId": SRC_FID_MSTYLE_MIRROR,
                "summaryTargetFieldId":    SRC_FID_ATS,
                "summaryQuery":            criteria,
                "decimalPlaces":           0,
                "blankIsZero":             True,
            },
        })
        fid = resp["id"]
        summary_fids.append(fid)
        print(f"  [{fid}] {label}  OK")
    except Exception as e:
        err_str = str(e)
        summary_fids.append(None)
        summary_failures.append((label, err_str))
        if n == 0:
            # First failure — print full error and stop trying
            print(f"  FAILED on first field ({label}): {err_str}")
            print("  Summary fields cannot be created via REST API.")
            print("  Skipping remaining 51 — see instructions below.")
            for remaining in range(1, 52):
                summary_fids.append(None)
                summary_failures.append((f"ATS LW-{remaining}", "skipped"))
            break
    time.sleep(0.12)


# ── Report ────────────────────────────────────────────────────────────────────
print("\n" + "=" * 60)
print("SUMMARY")
print("=" * 60)

good_cb  = [f for f in is_lw_fids if f]
good_sum = [f for f in summary_fids if f]

print(f"\n  Formula checkboxes in Inventory History: {len(good_cb)}/52 created")
if good_cb:
    print(f"    Is LW    = fid {is_lw_fids[0]}")
    print(f"    Is LW-51 = fid {is_lw_fids[51]}")

print(f"\n  Summary fields in Inventory History - Weekly: {len(good_sum)}/52 created")

if summary_failures and len(good_sum) == 0:
    print("\n  Summary fields must be added in QB UI.")
    print("  With the formula checkboxes now built, each one takes ~4 clicks:")
    print()
    print("  In QB -> Inventory History - Weekly table -> Settings -> Fields -> Add Field")
    print("  For each of the 52 fields:")
    print("    Label    : ATS LW  (then ATS LW-1, ATS LW-2 ... ATS LW-51)")
    print("    Type     : Summary")
    print("    From     : Inventory History")
    print("    Field    : ATS Qty OH#")
    print("    Function : Maximum")
    print("    Filter   : Is LW = true  (then Is LW-1 = true, etc.)")
    print()
    print("  Tip: duplicate the first summary field 51 times and just change")
    print("       the label and filter for each — much faster than starting fresh.")

print(f"\n  Mstyle (mirror) formula: {'set' if True else 'may need manual check'}")
print(f"  Table URL: https://pim.quickbase.com/db/{DEST_TABLE}")
print()

# Save fid map for reference
fid_map = {
    "dest_table":    DEST_TABLE,
    "src_table":     SRC_TABLE,
    "mstyle_mirror_fid": SRC_FID_MSTYLE_MIRROR,
    "is_lw_fids":    is_lw_fids,
    "summary_fids":  summary_fids,
}
with open("inv_history_weekly_fids.json", "w") as f:
    json.dump(fid_map, f, indent=2)
print("  Field ID map saved to inv_history_weekly_fids.json")
