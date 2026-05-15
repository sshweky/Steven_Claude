"""
One-shot probe: discover the right invoice table + field names for building
category seasonality profiles. Tries InventoryTrack.Invoice_Detail and
ProductTrack.Invoices, returning column lists for each. Single TOP 1 per table.
"""
import sys, json
sys.path.insert(0, "scripts")
from inventory_forecaster import cdata_query

CANDIDATES = [
    ("Quickbase1", "InventoryTrack", "Invoice_Detail"),
    ("Quickbase1", "ProductTrack",   "Invoices"),
]

for cat, sch, tbl in CANDIDATES:
    fqn = f"[{cat}].[{sch}].[{tbl}]"
    print(f"\n=== {fqn} ===", flush=True)
    rows = cdata_query(f"SELECT TOP 1 * FROM {fqn}", f"probe_{tbl}")
    if not rows:
        print("  (no rows / table not found)", flush=True)
        continue
    cols = sorted(rows[0].keys())
    print(f"  {len(cols)} columns:", flush=True)
    for c in cols:
        v = rows[0][c]
        sv = str(v)[:40] if v is not None else ""
        print(f"    {c:40s}  ={sv}", flush=True)
