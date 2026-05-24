#!/usr/bin/env python3
"""
Compute Season-specific T5/Holiday order boost curves from LY order data.

Approach:
 - For each Amazon item, combine history_ly_ord (ORD_COLS[:26]) and
   history_l26_ord (ORD_COLS[-26:]) into a 52-week sequence.
 - Compute 'active-week baseline' = mean of NON-ZERO weeks in the combined
   history (excluding tariff-OOS period May-Sep 2025, LY indices 0-16).
 - For each week in scope, compute multiplier = order / baseline.
   Zero weeks excluded from the analysis (item didn't order that week = no signal).
 - Report median multiplier across items that actually ordered in each week.

Calendar mapping (W1 = May 17, 2026):
  LY history_ly_ord[i]   = ORD_COLS[i] = (52-i) weeks ago
  projection W(n) LY equiv = history_ly_ord[n-1]
  Key T5 weeks in LY:
    LY[22] = Oct 19, 2025 (T5 ramp start, W23 equiv)
    LY[23] = Oct 26 (W24)
    LY[24] = Nov 02 (W25)
    LY[25] = Nov 09 (W26)
  T5 peak in L26W:
    L26W[0] = Nov 16, 2025  L26W[1] = Nov 23 (T5/Thanksgiving!)
    L26W[2] = Nov 30        L26W[3] = Dec 07
    L26W[4] = Dec 14        L26W[5] = Dec 21

Halloween in LY:
    LY[11] = Aug 24 (12w before Oct 31)
    LY[16] = Sep 28 (last tariff week)
    LY[17] = Oct 05 (first clean pre-Halloween week)
    LY[18] = Oct 12
    LY[19] = Oct 19 (overlaps T5 ramp)
    LY[20] = Oct 26
    LY[21] = Nov 02 (post-Halloween)
"""

import sys, json, os, base64, urllib.request, statistics
from datetime import date, timedelta
from collections import defaultdict

SKILL_DIR  = r"C:\Users\steven\.claude\skills\inventory-forecaster-cc"
SCRIPTS    = SKILL_DIR + r"\scripts"
CDATA_URL  = "https://mcp.cloud.cdata.com/mcp"
CDATA_EMAIL = os.environ.get("CDATA_EMAIL", "steven@skaffles.com")
CDATA_PAT   = os.environ.get("CDATA_PAT",   "VaTIPqklo14D1yMkfqKRi1punowIvp/6XEHtBSgybad2Jbyl")

def _auth():
    return "Basic " + base64.b64encode(f"{CDATA_EMAIL}:{CDATA_PAT}".encode()).decode()

def _mcp(method, params):
    payload = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method,
                          "params": params}).encode()
    req = urllib.request.Request(CDATA_URL, data=payload, method="POST")
    req.add_header("Authorization", _auth())
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "application/json, text/event-stream")
    resp = urllib.request.urlopen(req, timeout=90)
    body = resp.read().decode("utf-8")
    for line in body.split("\n"):
        if line.startswith("data: "):
            return json.loads(line[6:])
    raise ValueError(f"No data: {body[:200]}")

def cdata_query(sql, desc=""):
    print(f"  [QB] {desc or sql[:60]} ...", flush=True)
    r = _mcp("tools/call", {"name": "queryData", "arguments": {"query": sql}})
    if r.get("error"):
        raise RuntimeError(r["error"])
    content = r.get("result", {}).get("content", [{}])[0].get("text", "[]")
    data = json.loads(content)
    results = data.get("results", [data])[0]
    schema  = results.get("schema", [])
    cols    = [c["columnName"] for c in schema]
    rows    = results.get("rows", [])
    return [{cols[i]: rows[j][i] for i in range(len(cols))} for j in range(len(rows))]

# Prime session
print("[init] Priming CData session ...", flush=True)
_mcp("tools/call", {"name": "getInstructions",
                    "arguments": {"driverName": "Quickbase1"}})
print("  Ready.", flush=True)

# -----------------------------------------------------------------
# Load forecast_results
# -----------------------------------------------------------------
with open(f"{SCRIPTS}/forecast_results.json") as f:
    fdata = json.load(f)
records  = fdata["records"]
prj_cols = fdata["meta"]["prj_cols"]
W1_m, W1_d = int(prj_cols[0][:2]), int(prj_cols[0][3:5])
W1_date = date(2026, W1_m, W1_d)

proj_week_dates = [W1_date + timedelta(weeks=i) for i in range(26)]
ly_week_dates   = [d - timedelta(weeks=52) for d in proj_week_dates]
l26_week_dates  = [W1_date - timedelta(weeks=26-i) for i in range(26)]

print(f"\nProjection: {W1_date} (W1) - {proj_week_dates[25]} (W26)")
print(f"LY window:  {ly_week_dates[0]} - {ly_week_dates[25]}")
print(f"L26W:       {l26_week_dates[0]} - {l26_week_dates[25]}")
print(f"Loaded {len(records)} records\n")

# -----------------------------------------------------------------
# Pull Season from QB Styles
# -----------------------------------------------------------------
mstyles_all = list({r["mstyle"] for r in records if r.get("mstyle")})
print(f"[QB] Season tags for {len(mstyles_all)} mstyles ...", flush=True)
season_map = {}
BATCH = 200
for i in range(0, len(mstyles_all), BATCH):
    batch = mstyles_all[i:i + BATCH]
    in_c  = ", ".join(f"'{m}'" for m in batch)
    rows  = cdata_query(
        f"SELECT [Mstyle],[Season] FROM [Quickbase1].[ProductTrack].[Styles] WHERE [Mstyle] IN ({in_c})",
        f"Season {i//BATCH+1}/{(len(mstyles_all)-1)//BATCH+1}")
    for r in rows:
        ms = (r.get("Mstyle") or "").strip()
        sv = (r.get("Season") or "").strip()
        if ms and sv:
            season_map[ms] = sv

print(f"  {len(season_map)} mstyles with Season tag")
from collections import Counter
sc = Counter(season_map.values())
for s, c in sorted(sc.items(), key=lambda x: -x[1]):
    print(f"    {c:4d}  {s}")

# -----------------------------------------------------------------
# Filter: all Amazon records (include Inactive for Season analysis)
# but require at least some history
# -----------------------------------------------------------------
# Tariff-OOS period in LY: May-Sep 2025 = LY indices 0-16 (approx)
TARIFF_LY_IDX = set(range(0, 17))

amz_all = []
for r in records:
    cust = (r.get("cust") or "").lower()
    if "amazon" not in cust:
        continue
    ly  = [float(x or 0) for x in (r.get("history_ly_ord") or [])]
    l26 = [float(x or 0) for x in (r.get("history_l26_ord") or [])]
    if len(ly) < 26 or len(l26) < 26:
        continue
    # Need at least 3 non-zero weeks outside tariff period for a baseline
    clean_ly  = [ly[i] for i in range(17, 26)]  # Oct 2025 area
    clean_l26 = l26  # Nov 2025 - May 2026
    nz_clean = sum(1 for x in clean_ly + clean_l26 if x > 0)
    if nz_clean < 3:
        continue
    amz_all.append(r)

print(f"\n{len(amz_all)} Amazon records with sufficient history")

# Subset: Active Replen (non-Inactive model) -- for standard T5
amz_replen = [r for r in amz_all
              if "replen" in (r.get("item_status") or "").lower()
              and not (r.get("model") or "").lower().startswith("inactive")
              and not (r.get("model") or "").lower().startswith("otb")]
print(f"{len(amz_replen)} Active Replen (non-Inactive model)")

# -----------------------------------------------------------------
# Compute active-week baseline and per-week multipliers
# -----------------------------------------------------------------
# Combined 52-week sequence: indices 0-51
#   [0..25] = history_ly_ord (LY equiv W1..W26)
#   [26..51] = history_l26_ord (L26W, 25wks ago .. last week)

def active_week_baseline(ly, l26):
    """Mean of non-zero weeks outside tariff period."""
    vals = []
    for i in range(17, 26):  # clean LY weeks Oct-Nov
        v = ly[i]
        if v > 0:
            vals.append(v)
    for v in l26:  # all L26W (Nov 2025 - May 2026)
        if v > 0:
            vals.append(v)
    return statistics.mean(vals) if vals else 0.0

# Accumulate per Season per week: list of (item_mult) for items that
# ACTUALLY ORDERED that week (non-zero order).
boost_data  = defaultdict(lambda: defaultdict(list))  # season -> key -> [mults]
count_data  = defaultdict(lambda: defaultdict(int))   # season -> key -> n items with history

for r in amz_all:
    ms     = r.get("mstyle", "")
    season = season_map.get(ms, "")
    ly  = [float(x or 0) for x in r.get("history_ly_ord")]
    l26 = [float(x or 0) for x in r.get("history_l26_ord")]

    base = active_week_baseline(ly, l26)
    if base < 10:
        continue

    # LY weeks (W1-W26 equiv, skip tariff period)
    for i in range(26):
        if i in TARIFF_LY_IDX:
            continue
        qty = ly[i]
        count_data[season][("LY", i)] += 1
        if qty > 0:
            boost_data[season][("LY", i)].append(qty / base)

    # L26W weeks
    for i in range(26):
        qty = l26[i]
        count_data[season][("L26", i)] += 1
        if qty > 0:
            boost_data[season][("L26", i)].append(qty / base)

# -----------------------------------------------------------------
# Print results
# -----------------------------------------------------------------
def med(lst):
    return statistics.median(lst) if lst else 0.0
def avg(lst):
    return statistics.mean(lst) if lst else 0.0

all_seasons = sorted(set([""] + list(season_map.values())))

print("\n" + "="*72)
print("SEASON-SPECIFIC ORDER BOOST ANALYSIS")
print("(Only shows weeks where at least 2 items ordered)")
print("="*72)

RESULTS = {}

for season in all_seasons:
    rows_for_season = [r for r in amz_all
                       if season_map.get(r.get("mstyle",""), "") == season]
    if not rows_for_season:
        continue

    label = season if season else "(standard -- no Season tag)"
    print(f"\n--- Season: {label} ({len(rows_for_season)} records) ---")

    season_weekly = {}

    # LY W15-W26 (Aug-Nov) -- most relevant seasonal window
    print(f"  LY W15-W26 ({ly_week_dates[14].strftime('%b %d')} - {ly_week_dates[25].strftime('%b %d')} 2025):")
    for i in range(14, 26):
        key   = ("LY", i)
        mults = boost_data[season].get(key, [])
        total = count_data[season].get(key, 0)
        if len(mults) < 2:
            continue
        m   = med(mults)
        mn  = avg(mults)
        pct = len(mults) / total * 100 if total else 0
        dt  = ly_week_dates[i].strftime("%b %d")
        tag = ""
        if 21 <= i <= 25:
            tag = " << T5 RAMP"
        if season == "Halloween" and 17 <= i <= 20:
            tag = " << PRE-HALLOWEEN"
        print(f"    W{i+1:2d} ({dt}): median={m:.2f}x  mean={mn:.2f}x  "
              f"{len(mults)}/{total} ordered ({pct:.0f}%){tag}")
        season_weekly[i] = {"date": str(ly_week_dates[i]), "median": round(m, 3),
                             "mean": round(mn, 3), "ordered": len(mults), "total": total}

    # L26W 0-7 (Nov-Dec 2025 = post-T5 peak)
    print(f"  L26W 0-7 (Nov 16 - Jan 04 2026):")
    for i in range(8):
        key   = ("L26", i)
        mults = boost_data[season].get(key, [])
        total = count_data[season].get(key, 0)
        if len(mults) < 2:
            continue
        m   = med(mults)
        mn  = avg(mults)
        pct = len(mults) / total * 100 if total else 0
        dt  = l26_week_dates[i].strftime("%b %d")
        print(f"    L26W[{i}] ({dt}): median={m:.2f}x  mean={mn:.2f}x  "
              f"{len(mults)}/{total} ordered ({pct:.0f}%)")

    RESULTS[season] = season_weekly

# -----------------------------------------------------------------
# Summary: proposed boost constants
# -----------------------------------------------------------------
print("\n" + "="*72)
print("PROPOSED T5_SEASONAL_BOOSTS CONSTANTS")
print("(weeks W22-W26 = Oct 11-Nov 08 in current window)")
print("Median lift >= 1.10 and order rate >= 30% to qualify")
print("="*72)

for season in all_seasons:
    sw = RESULTS.get(season, {})
    boosts = []
    # Only T5 ramp weeks W22-W26 (indices 21-25) for standard/holiday
    # Halloween: W16-W25 (Aug-Oct) for pre-Halloween ordering spike
    if season.lower() == "halloween":
        check_range = range(15, 26)  # W16-W26
    else:
        check_range = range(21, 26)  # W22-W26 (T5 ramp only)
    for i in check_range:
        d = sw.get(i, {})
        if not d:
            continue
        m = d["median"]
        ordered = d["ordered"]
        total   = d["total"]
        order_rate = ordered / total if total else 0
        if m >= 1.10 and order_rate >= 0.20:
            boosts.append((i, round(m, 2), d["date"]))
    if boosts:
        print(f"\n  Season '{season}':")
        for idx, mult, dt in boosts:
            print(f"    week index {idx} ({dt}) = {mult:.2f}x")

# -----------------------------------------------------------------
# Save JSON
# -----------------------------------------------------------------
out = {"prj_w1": str(W1_date),
       "ly_week_dates": [str(d) for d in ly_week_dates],
       "l26_week_dates": [str(d) for d in l26_week_dates],
       "boosts": {}}
for season in all_seasons:
    sw = RESULTS.get(season, {})
    if sw:
        out["boosts"][season] = sw

with open(SKILL_DIR + r"\season_boost_analysis.json", "w") as f:
    json.dump(out, f, indent=2)
print(f"\nSaved: {SKILL_DIR}\\season_boost_analysis.json")
