"""Quick gap analysis of fr_all_s1_s6.json vs manual projections."""
import json
from collections import defaultdict

with open("fr_all_s1_s6.json") as f:
    data = json.load(f)
recs = data.get("records", [])

def pct(num, den):
    return (num / den * 100) if den else 0.0

# Overall roll-up
total_ai  = sum(sum(r.get("forecast", [])) for r in recs)
total_man = sum(sum(r.get("manual", []))  for r in recs)
print(f"Records: {len(recs)}")
print(f"Total AI  26w: {total_ai:>12,.0f}")
print(f"Total Man 26w: {total_man:>12,.0f}")
print(f"Overall gap:  {pct(total_ai-total_man, total_man):+6.2f}%")
print()

# By model
by_model = defaultdict(lambda: {"n":0, "ai":0, "man":0})
for r in recs:
    m = r.get("model","?")
    by_model[m]["n"]  += 1
    by_model[m]["ai"] += sum(r.get("forecast",[]))
    by_model[m]["man"]+= sum(r.get("manual",[]))
print("By model:")
print(f"  {'model':30s} {'n':>5} {'ai':>12} {'manual':>12} {'gap':>8}")
for m, d in sorted(by_model.items(), key=lambda kv: -abs(kv[1]['ai']-kv[1]['man'])):
    print(f"  {m:30s} {d['n']:>5} {d['ai']:>12,} {d['man']:>12,} {pct(d['ai']-d['man'], d['man']):>+7.1f}%")
print()

# By customer (top 20 by |gap|)
by_cust = defaultdict(lambda: {"n":0, "ai":0, "man":0})
for r in recs:
    c = (r.get("cust","") or "?")[:30]
    by_cust[c]["n"]  += 1
    by_cust[c]["ai"] += sum(r.get("forecast",[]))
    by_cust[c]["man"]+= sum(r.get("manual",[]))
print("Top 20 customers by gap magnitude (min 5 recs):")
print(f"  {'cust':32s} {'n':>5} {'ai':>12} {'manual':>12} {'gap':>8}")
items = [(c,d) for c,d in by_cust.items() if d["n"] >= 5]
for c, d in sorted(items, key=lambda kv: -abs(kv[1]['ai']-kv[1]['man']))[:20]:
    print(f"  {c:32s} {d['n']:>5} {d['ai']:>12,} {d['man']:>12,} {pct(d['ai']-d['man'], d['man']):>+7.1f}%")
