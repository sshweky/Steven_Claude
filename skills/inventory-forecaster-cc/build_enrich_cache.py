"""
Standalone enrichment cache builder.

Pulls Status_Cust / PT_Item_Status / Description / Inventory_Manager for every
key in validation_results.json (or forecast_results.json) and writes
viewer_enrichment_cache.json.  Uses small batches with retries and progress
output, so the user can see live progress instead of a silent hang.

Run separately from the viewer when CData has been flaky:
    python build_enrich_cache.py
Once the cache exists, restart the viewer (drop --no-enrich) and Status @
Cust / Item Status will populate from the cache.
"""
import json
import os
import sys
import time

sys.path.insert(0, "scripts")
from inventory_forecaster import cdata_query

CACHE_PATH = "viewer_enrichment_cache.json"
RESULTS_FILES = ["validation_results.json", "forecast_results.json"]

# Collect keys from whichever results files exist
keys = set()
for path in RESULTS_FILES:
    if os.path.exists(path):
        d = json.load(open(path))
        recs = d.get("records", []) if isinstance(d, dict) else d
        for r in recs:
            k = r.get("key")
            if k:
                keys.add(k)
keys = sorted(keys)
print(f"Found {len(keys)} unique keys across {RESULTS_FILES}")

# Load existing cache
cache = {}
if os.path.exists(CACHE_PATH):
    try:
        cache = json.load(open(CACHE_PATH))
        print(f"Loaded existing cache: {len(cache)} entries")
    except Exception as e:
        print(f"Could not read existing cache: {e}")

missing = [k for k in keys if k not in cache]
print(f"Need to fetch: {len(missing)} keys")

if not missing:
    print("Cache already complete. Nothing to do.")
    sys.exit(0)

select_cols = (
    "[Acct_MStyle_Key_], [Description], [Status_Cust], "
    "[PT_Item_Status], [Inventory_Manager]"
)

BATCH = 20
n_batches = (len(missing) + BATCH - 1) // BATCH
fetched = 0
failed = 0

for i in range(0, len(missing), BATCH):
    batch = missing[i:i + BATCH]
    in_clause = ", ".join("'" + k.replace("'", "''") + "'" for k in batch)
    sql = (f"SELECT {select_cols} "
           "FROM [Quickbase1].[InventoryTrack].[Projections] "
           f"WHERE [Acct_MStyle_Key_] IN ({in_clause})")
    label = f"batch {i//BATCH+1}/{n_batches}"
    rows = []
    try:
        rows = cdata_query(sql, label)
    except Exception as e:
        print(f"  [FAIL] {label}: {e}")
    if not rows:
        failed += 1
        # Persist whatever we have every 10 batches so partial progress isn't lost
        if (i // BATCH) % 10 == 0 and cache:
            json.dump(cache, open(CACHE_PATH, "w"))
        continue
    for r in rows:
        k = r.get("Acct_MStyle_Key_")
        if k:
            cache[k] = r
            fetched += 1
    # Persist every 5 successful batches
    if (i // BATCH) % 5 == 0:
        json.dump(cache, open(CACHE_PATH, "w"))
        print(f"  {label}: +{len(rows)} rows  (cache size: {len(cache)})")
    else:
        print(f"  {label}: +{len(rows)} rows")

# Final persist
json.dump(cache, open(CACHE_PATH, "w"))
print()
print(f"Done.  Fetched {fetched} new records, {failed} batches failed.")
print(f"Cache size: {len(cache)} / {len(keys)} keys "
      f"({len(cache)/len(keys)*100:.0f}% complete)")
print(f"Saved to {CACHE_PATH}")
print()
print("Restart the viewer (without --no-enrich) to pick up the new cache.")
