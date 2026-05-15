"""Drill into the remaining outliers after S1-S6."""
import json
from collections import defaultdict

with open("fr_all_s1_s6.json") as f:
    data = json.load(f)
recs = data.get("records", [])

def tot(r, k): return sum(r.get(k, []) or [])

# --- 1. Burlington detail: why didn't S2 catch the +378% overshoot? ---
print("=" * 80)
print("BURLINGTON — top overshoots (S2 path B misses)")
print("=" * 80)
burl = [r for r in recs if "BURLINGTON" in (r.get("cust","") or "").upper()]
print(f"Total: {len(burl)} recs  AI={sum(tot(r,'forecast') for r in burl):,}  Man={sum(tot(r,'manual') for r in burl):,}")
print(f"  {'mstyle':18s} {'model':20s} {'ai':>8} {'man':>8} {'diff':>8}")
for r in sorted(burl, key=lambda r: -(tot(r,'forecast') - tot(r,'manual')))[:15]:
    a, m = tot(r,'forecast'), tot(r,'manual')
    print(f"  {r['mstyle'][:18]:18s} {r['model'][:20]:20s} {a:>8} {m:>8} {a-m:>+8}")

# --- 2. Heuristic overshoot — which items? R9 too loose?
print()
print("=" * 80)
print("HEURISTIC — top overshoots (+24% overall, small sample)")
print("=" * 80)
heu = [r for r in recs if r.get("model") == "Heuristic"]
print(f"Total: {len(heu)} recs  AI={sum(tot(r,'forecast') for r in heu):,}  Man={sum(tot(r,'manual') for r in heu):,}")
print(f"  {'mstyle':18s} {'cust':22s} {'ai':>8} {'man':>8} {'diff':>8}")
for r in sorted(heu, key=lambda r: -(tot(r,'forecast') - tot(r,'manual')))[:12]:
    a, m = tot(r,'forecast'), tot(r,'manual')
    if a - m > 500:
        print(f"  {r['mstyle'][:18]:18s} {r['cust'][:22]:22s} {a:>8} {m:>8} {a-m:>+8}")

# --- 3. International (Wakefern, Loblaws, Petbarn) — S5 should've fired
print()
print("=" * 80)
print("INTERNATIONAL — why S5 isn't firing")
print("=" * 80)
for cust_sub in ["WAKEFERN", "LOBLAWS", "PETBARN"]:
    intl = [r for r in recs if cust_sub in (r.get("cust","") or "").upper()]
    print(f"\n{cust_sub}: {len(intl)} recs  AI={sum(tot(r,'forecast') for r in intl):,}  Man={sum(tot(r,'manual') for r in intl):,}")
    by_model = defaultdict(lambda: {"n":0,"ai":0,"man":0})
    for r in intl:
        by_model[r["model"]]["n"] += 1
        by_model[r["model"]]["ai"] += tot(r,'forecast')
        by_model[r["model"]]["man"] += tot(r,'manual')
    for m, d in sorted(by_model.items(), key=lambda kv: -abs(kv[1]['ai']-kv[1]['man'])):
        print(f"    {m:30s} n={d['n']:>3}  ai={d['ai']:>7}  man={d['man']:>7}  diff={d['ai']-d['man']:>+7}")

# --- 4. Chewy — what model is undershooting?
print()
print("=" * 80)
print("CHEWY — undershoot breakdown")
print("=" * 80)
chewy = [r for r in recs if "CHEWY" in (r.get("cust","") or "").upper()]
print(f"Total: {len(chewy)} recs  AI={sum(tot(r,'forecast') for r in chewy):,}  Man={sum(tot(r,'manual') for r in chewy):,}")
by_model = defaultdict(lambda: {"n":0,"ai":0,"man":0})
for r in chewy:
    by_model[r["model"]]["n"] += 1
    by_model[r["model"]]["ai"] += tot(r,'forecast')
    by_model[r["model"]]["man"] += tot(r,'manual')
for m, d in sorted(by_model.items(), key=lambda kv: -abs(kv[1]['ai']-kv[1]['man'])):
    print(f"  {m:25s} n={d['n']:>3}  ai={d['ai']:>8}  man={d['man']:>8}  diff={d['ai']-d['man']:>+8}")
print("\nTop Chewy undershoots:")
for r in sorted(chewy, key=lambda r: (tot(r,'forecast') - tot(r,'manual')))[:8]:
    a, m = tot(r,'forecast'), tot(r,'manual')
    if m - a > 500:
        print(f"  {r['mstyle'][:18]:18s} {r['model'][:20]:20s} ai={a:>7} man={m:>7} diff={a-m:>+7}")
