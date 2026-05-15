"""Quick before/after comparison of deviation buckets."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent

def load_pair(val_path, fcst_path):
    val  = json.load(open(ROOT / val_path))
    fcst = json.load(open(ROOT / fcst_path))
    fb   = {r["key"]: r for r in fcst.get("records", [])}
    counts = {"manual_high": 0, "manual_low": 0, "l13_high": 0, "l13_low": 0,
              "stale_inactive": 0, "total": 0, "any_dev": 0}
    for r in val.get("records", []):
        f = fb.get(r["key"], {})
        ai_fcst = f.get("forecast") or r.get("ai_forecast") or []
        if not ai_fcst:
            continue
        counts["total"] += 1
        ai_wk  = sum(ai_fcst) / 26.0
        man_wk = float(r.get("proj_per_wk") or 0)
        ord_l13= float(r.get("ord_per_wk_l13") or 0)
        if ai_wk == 0 and ord_l13 == 0 and man_wk > 0:
            counts["stale_inactive"] += 1
            continue   # exclude from forecast-quality counts
        flagged = False
        if man_wk > 0:
            d = (ai_wk - man_wk) / man_wk
            if d >  0.25: counts["manual_high"] += 1; flagged = True
            if d < -0.25: counts["manual_low"]  += 1; flagged = True
        if ord_l13 > 0:
            d = (ai_wk - ord_l13) / ord_l13
            if d >  0.25: counts["l13_high"] += 1; flagged = True
            if d < -0.25: counts["l13_low"]  += 1; flagged = True
        if flagged:
            counts["any_dev"] += 1
    return counts

before = load_pair("validation_results.before_F25_F26_F27.json",
                   "forecast_results.before_F25_F26_F27.json")
after  = load_pair("validation_results.json", "forecast_results.json")

print(f"{'Bucket':<22s}  {'Before':>8s}  {'After':>8s}  {'change':>8s}")
print("-" * 52)
for k in ("total","stale_inactive","any_dev","manual_high","manual_low","l13_high","l13_low"):
    b, a = before[k], after[k]
    delta = a - b
    sign = "+" if delta > 0 else ""
    print(f"{k:<22s}  {b:>8d}  {a:>8d}  {sign}{delta:>7d}")

print()
print(f"Forecast-quality deviation rate (excludes stale_inactive):")
b_rate = before["any_dev"] / max(1, before["total"] - before["stale_inactive"]) * 100
a_rate = after ["any_dev"] / max(1, after ["total"] - after ["stale_inactive"]) * 100
print(f"  Before: {b_rate:.1f}%   After: {a_rate:.1f}%   change {a_rate - b_rate:+.1f}pp")
