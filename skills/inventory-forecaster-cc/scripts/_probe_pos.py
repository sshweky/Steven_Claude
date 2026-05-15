"""Quick diagnostic: pull Amazon Catalog POS data for watch-list mstyles."""
import os, sys, json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from inventory_forecaster import cdata_query

WATCHLIST = [
    "FF22031", "FF15589", "FF12689", "FF10479EC", "FF10530",
    "BB22116", "FF7120EC", "FF16704", "FF15584", "BB14812",
    "FF5766AMZ", "FF8654", "BB0466", "FF12655EC", "FF10159EC",
    "BB13437",
]

POS_COLS = ["Mstyle", "Ordered_Units_LW",
            "Avg_Units_Wk_L4w", "Avg_Units_Wk_L13w",
            "Avg_Units_Wk_L26w", "Avg_Units_Wk_L52w"]
pos_sel = ", ".join(f"[{c}]" for c in POS_COLS)
in_clause = ", ".join(f"'{m}'" for m in WATCHLIST)

rows = cdata_query(
    f"SELECT {pos_sel} FROM [Quickbase1].[InventoryTrack].[Amazon_Catalog]"
    f" WHERE [Mstyle] IN ({in_clause})",
    "probe_watchlist_pos")

print()
print(f"{'Mstyle':<12} {'LW':>8} {'L4W':>8} {'L13W':>8} {'L26W':>8} {'L52W':>8}   L4/L13   L4/L52   Health")
print("-" * 100)

by_mstyle = {r["Mstyle"]: r for r in rows if r.get("Mstyle")}
for ms in WATCHLIST:
    r = by_mstyle.get(ms)
    if not r:
        print(f"{ms:<12}  [no POS record]")
        continue
    lw  = float(r.get("Ordered_Units_LW")   or 0)
    l4  = float(r.get("Avg_Units_Wk_L4w")   or 0)
    l13 = float(r.get("Avg_Units_Wk_L13w")  or 0)
    l26 = float(r.get("Avg_Units_Wk_L26w")  or 0)
    l52 = float(r.get("Avg_Units_Wk_L52w")  or 0)
    r_413  = (l4 / l13) if l13 > 0 else 0
    r_452  = (l4 / l52) if l52 > 0 else 0
    healthy = "HEALTHY" if (l13 > 0 and l4 >= l13 * 0.5) else "COLLAPSED"
    print(f"{ms:<12} {lw:>8.0f} {l4:>8.0f} {l13:>8.0f} {l26:>8.0f} {l52:>8.0f}   "
          f"{r_413:>5.2f}    {r_452:>5.2f}   {healthy}")

print()
print(f"Pulled {len(rows)} rows for {len(WATCHLIST)} requested mstyles")
