#!/usr/bin/env python3
"""
Compute Season-specific T5/Holiday order boost curves from LY order data.

Uses forecast_results.json (history_ly_ord = ORD_COLS[:26] = 26-51 weeks ago,
mapping to LY equivalent of the current 26-week projection window) plus the
Season tag pulled fresh from QB Styles table.

Mapping key:
  projection week W(n) (1-indexed) -> LY equivalent = history_ly_ord[n-1]
  So history_ly_ord[0]  = LY W1  (week of ~May 17 last year)
     history_ly_ord[22] = LY W23 (week of ~Oct 18 last year = T5 ramp start)
     history_ly_ord[25] = LY W26 (week of ~Nov 8 last year)

Also uses history_l26_ord (ORD_COLS[-26:] = 0-25 weeks ago) for L26W baseline
and to capture the T5 peak period (Nov-Dec LY):
  history_l26_ord[0]  = 25 weeks ago = ~Nov 16, 2025 (pre-T5 peak)
  history_l26_ord[1]  = 24 weeks ago = ~Nov 23 (T5 Thanksgiving week)
  history_l26_ord[2]  = 23 weeks ago = ~Nov 30 (Cyber Monday week)
  history_l26_ord[3]  = 22 weeks ago = ~Dec 7
  history_l26_ord[4]  = 21 weeks ago = ~Dec 14
  history_l26_ord[5]  = 20 weeks ago = ~Dec 21 (last shipping week before Xmas)
"""

import sys, json, os, base64, urllib.request, statistics
from datetime import date, timedelta

SKILL_DIR  = r"C:\Users\steven\.claude\skills\inventory-forecaster-cc"
SCRIPTS    = SKILL_DIR + r"\scripts"
CDATA_URL  = "https://mcp.cloud.cdata.com/mcp"
CDATA_EMAIL = os.environ.get("CDATA_EMAIL", "steven@skaffles.com")
CDATA_PAT   = os.environ.get("CDATA_PAT",   "VaTIPqklo14D1yMkfqKRi1punowIvp/6XEHtBSgybad2Jbyl")

# -----------------------------------------------------------------
# CData helpers
# -----------------------------------------------------------------
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

# Prime getInstructions
print("[init] Priming CData session ...", flush=True)
_mcp("tools/call", {"name": "getInstructions",
                    "arguments": {"driverName": "Quickbase1"}})
print("  Session ready.", flush=True)

# -----------------------------------------------------------------
# Load forecast_results
# -----------------------------------------------------------------
with open(f"{SCRIPTS}/forecast_results.json") as f:
    fdata = json.load(f)
records  = fdata["records"]
prj_cols = fdata["meta"]["prj_cols"]   # e.g. ["05_17_W1", ..., "11_08_W26"]

W1_str = prj_cols[0]   # "05_17_W1"
W1_m, W1_d = int(W1_str[:2]), int(W1_str[3:5])
W1_date = date(2026, W1_m, W1_d)

print(f"\nProjection window: {W1_date} (W1) through W26")
print(f"Loaded {len(records)} records from forecast_results.json")

# Calendar dates for each projection week (W1-W26)
proj_week_dates = [W1_date + timedelta(weeks=i) for i in range(26)]

# Calendar dates for LY equivalent weeks (history_ly_ord[0..25] = W1 LY through W26 LY)
ly_week_dates = [d - timedelta(weeks=52) for d in proj_week_dates]

# Calendar dates for L26W history (history_l26_ord[0..25])
# L26W = 25 weeks ago through last week (0 weeks ago)
l26_week_dates = [W1_date - timedelta(weeks=26-i) for i in range(26)]

print(f"\nLY window: {ly_week_dates[0]} to {ly_week_dates[25]}")
print(f"L26W window: {l26_week_dates[0]} to {l26_week_dates[25]}")
print(f"\nKey T5 dates in LY (Oct 18-24 = W23 LY equiv, W23-W26 in scope):")
for n in range(21, 26):
    print(f"  W{n+1} ({proj_week_dates[n].strftime('%b %d')} 2026) -> LY {ly_week_dates[n].strftime('%b %d %Y')}")
print(f"\nKey post-T5 in L26W (beyond W26 but captured in history_l26_ord):")
for i, d in enumerate(l26_week_dates[:7]):
    print(f"  L26W[{i:2d}] = {d.strftime('%b %d, %Y')}")

# -----------------------------------------------------------------
# Pull Season data from QB Styles table
# -----------------------------------------------------------------
mstyles_all = list({r["mstyle"] for r in records if r.get("mstyle")})
print(f"\n[QB] Pulling Season tags for {len(mstyles_all)} mstyles ...", flush=True)
season_map = {}
BATCH = 200
for i in range(0, len(mstyles_all), BATCH):
    batch = mstyles_all[i:i + BATCH]
    in_c  = ", ".join(f"'{m}'" for m in batch)
    sql   = (f"SELECT [Mstyle], [Season] FROM [Quickbase1].[ProductTrack].[Styles] "
             f"WHERE [Mstyle] IN ({in_c})")
    rows = cdata_query(sql, f"Season batch {i//BATCH+1}/{(len(mstyles_all)-1)//BATCH+1}")
    for r in rows:
        ms = (r.get("Mstyle") or "").strip()
        sv = (r.get("Season") or "").strip()
        if ms and sv:
            season_map[ms] = sv

print(f"  {len(season_map)} mstyles with Season tag")
season_counts = {}
for s in season_map.values():
    season_counts[s] = season_counts.get(s, 0) + 1
print("  Season distribution:")
for s, c in sorted(season_counts.items(), key=lambda x: -x[1]):
    print(f"    {c:4d}  {s}")

# -----------------------------------------------------------------
# Filter to Amazon Active Replen records with sufficient history
# -----------------------------------------------------------------
amz_replen = []
for r in records:
    cust   = (r.get("cust") or "").upper()
    status = (r.get("item_status") or "").lower()
    model  = (r.get("model") or "").lower()
    if ("amazon" not in cust):
        continue
    if ("replen" not in status):
        continue
    if ("inactive" in model or "otb" in model or "pre-launch" in model):
        continue
    ly  = r.get("history_ly_ord") or []
    l26 = r.get("history_l26_ord") or []
    if len(ly) < 26 or len(l26) < 26:
        continue
    # Need at least 8 non-zero weeks across combined history for a useful baseline
    combined = ly + l26
    nz = sum(1 for x in combined if float(x or 0) > 0)
    if nz < 8:
        continue
    amz_replen.append(r)

print(f"\n{len(amz_replen)} Amazon Active Replen records with sufficient history")

# -----------------------------------------------------------------
# Compute multipliers per record per week
# -----------------------------------------------------------------
# Baseline = mean of the 13 most recent non-tariff weeks (last 13 of L26W history,
# avoiding the tariff-disrupted May-Sep 2025 window in LY).
# Using ORD L13W all-weeks avg (same as F_AMZ_RPL baseline).

def baseline_rate(l26):
    """L13W all-weeks avg (same formula as F_AMZ_RPL)."""
    vals = [float(x or 0) for x in l26[-13:]]
    return sum(vals) / 13.0 if vals else 0.0

def pos_l13_from_record(r):
    """Approximate POS L13W from AMZ viewer cache if available."""
    # Not needed for this analysis
    return 0.0

# For each record, compute relative order index for each of the 52 combined weeks.
# Weeks 0-25 = history_ly_ord (LY equivalent of W1-W26)
# Weeks 26-51 = history_l26_ord (L26W, 25wks ago to last week)

# We also want to look at L26W weeks beyond W26 to see T5 peak:
# l26[0] = 25wk ago = ~Nov 16, l26[1] = ~Nov 23 (T5 peak), l26[2] = ~Nov 30, etc.

# Summary containers: season -> {week_index -> [multipliers]}
# week_index for LY: 0-25 (projection weeks W1-W26 LY equivalent)
# week_index for L26W beyond projection: 26-32 (representing l26[0]-l26[6])

from collections import defaultdict

boost_data = defaultdict(lambda: defaultdict(list))  # season -> week_key -> [mults]

TARIFF_WEEKS_LY = set(range(0, 17))  # May-Sep 2025 in LY = indices 0-16 (tariff OOS)

for r in amz_replen:
    ms = r.get("mstyle", "")
    season = season_map.get(ms, "")  # "" = no season tag
    ly  = [float(x or 0) for x in r.get("history_ly_ord") or []]
    l26 = [float(x or 0) for x in r.get("history_l26_ord") or []]

    base = baseline_rate(l26)
    if base < 10:
        continue  # skip very low-volume items

    # LY weeks (projection W1-W26 LY equivalent)
    for i in range(26):
        if i in TARIFF_WEEKS_LY:
            continue  # skip tariff-disrupted weeks
        qty = ly[i]
        mult = qty / base
        boost_data[season][("LY", i)].append(mult)

    # L26W weeks (most recent history) - captures T5 peak and post-holiday
    for i in range(26):
        boost_data[season][("L26", i)].append(l26[i] / base)

# -----------------------------------------------------------------
# Print results: focus on T5/Holiday window
# -----------------------------------------------------------------
print("\n" + "="*72)
print("SEASON-SPECIFIC ORDER BOOST ANALYSIS")
print("="*72)

def pct(v):
    return f"{v:.2f}x"

all_seasons = sorted(set([""] + list(season_map.values())))

for season in all_seasons:
    label = season if season else "(no Season tag = standard)"
    rows_for_season = [r for r in amz_replen
                       if season_map.get(r.get("mstyle",""), "") == season]
    if not rows_for_season:
        continue

    print(f"\n--- Season: {label} ({len(rows_for_season)} records) ---")

    # LY equivalent weeks W15-W26 (Aug through Nov) = key seasonal window
    print(f"  LY equivalent order pattern (W15-W26 = {ly_week_dates[14].strftime('%b %d')} - {ly_week_dates[25].strftime('%b %d %Y')}):")
    for i in range(14, 26):
        key = ("LY", i)
        mults = boost_data[season].get(key, [])
        if not mults:
            continue
        med   = statistics.median(mults)
        mean  = statistics.mean(mults)
        nrec  = len(mults)
        dt    = ly_week_dates[i].strftime("%b %d")
        flag  = " <-- T5 RAMP" if 21 <= i <= 25 else ""
        flag  = " <-- PRE-HALLOWEEN" if (10 <= i <= 15) and season.lower() in ("halloween",) else flag
        print(f"    W{i+1:2d} ({dt}): median={pct(med)}, mean={pct(mean)}, n={nrec}{flag}")

    # L26W weeks 0-6 = Nov 16 to Dec 27 (T5 peak + holiday season)
    print(f"  L26W post-T5 window ({l26_week_dates[0].strftime('%b %d')} - {l26_week_dates[6].strftime('%b %d %Y')}):")
    for i in range(7):
        key = ("L26", i)
        mults = boost_data[season].get(key, [])
        if not mults:
            continue
        med  = statistics.median(mults)
        mean = statistics.mean(mults)
        nrec = len(mults)
        dt   = l26_week_dates[i].strftime("%b %d")
        print(f"    L26W[{i}] ({dt}): median={pct(med)}, mean={pct(mean)}, n={nrec}")

# -----------------------------------------------------------------
# Proposed config constants
# -----------------------------------------------------------------
print("\n" + "="*72)
print("PROPOSED T5_SEASONAL_BOOSTS CONSTANTS FOR config.py")
print("="*72)
print("""
# T5/Holiday event boost constants for Amazon Active Replen.
# Format: {\"season_tag\": [(week_index_0based, multiplier), ...]}
# \"\" = standard (no Season tag), \"Holiday\" = season-tagged Holiday items, etc.
# Week index is 0-based relative to W1 of the current projection window.
# Only weeks in the T5/Halloween ramp window are listed (no entry = no boost).
""")

for season in all_seasons:
    rows_for_season = [r for r in amz_replen
                       if season_map.get(r.get("mstyle",""), "") == season]
    if not rows_for_season:
        continue
    # Collect boost weeks where median mult >= 1.10
    boosts = []
    for i in range(26):
        key = ("LY", i)
        mults = boost_data[season].get(key, [])
        if len(mults) < 3:
            continue
        med = statistics.median(mults)
        if med >= 1.10:
            boosts.append((i, round(med, 2)))
    if boosts:
        label = f'"{season}"' if season else '""'
        print(f"  {label}: {boosts}")

# -----------------------------------------------------------------
# Save results JSON
# -----------------------------------------------------------------
out = {"prj_w1": str(W1_date),
       "ly_week_dates": [str(d) for d in ly_week_dates],
       "l26_week_dates": [str(d) for d in l26_week_dates],
       "season_item_counts": {s: len([r for r in amz_replen if season_map.get(r.get("mstyle",""),"") == s])
                               for s in all_seasons},
       "boosts": {}}
for season in all_seasons:
    rows_for_season = [r for r in amz_replen
                       if season_map.get(r.get("mstyle",""), "") == season]
    if not rows_for_season:
        continue
    season_boosts = {}
    for i in range(26):
        key = ("LY", i)
        mults = boost_data[season].get(key, [])
        if len(mults) >= 3:
            season_boosts[str(i)] = {
                "week_date": str(ly_week_dates[i]),
                "median": round(statistics.median(mults), 3),
                "mean":   round(statistics.mean(mults), 3),
                "n": len(mults)
            }
    l26_extra = {}
    for i in range(7):
        key = ("L26", i)
        mults = boost_data[season].get(key, [])
        if len(mults) >= 3:
            l26_extra[str(i)] = {
                "week_date": str(l26_week_dates[i]),
                "median": round(statistics.median(mults), 3),
                "mean":   round(statistics.mean(mults), 3),
                "n": len(mults)
            }
    out["boosts"][season] = {"ly": season_boosts, "l26w_extra": l26_extra}

out_path = SKILL_DIR + r"\season_boost_analysis.json"
with open(out_path, "w") as f:
    json.dump(out, f, indent=2)
print(f"\nResults saved to: {out_path}")
