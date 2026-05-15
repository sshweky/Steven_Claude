#!/usr/bin/env python3
"""
Aggregate raw Sales_Budgets data into a Combined deck (FF + BB together,
sorted by potential miss across all qualifying customers).

Inputs:
  /home/claude/sb_raw.json          — raw Sales_Budgets pull
  /home/claude/code_name_map.json   — Slsprsn_Code → name dict

Outputs:
  /home/claude/deck_data_combined.json   — list of customer slide payloads
  /home/claude/combined_config.json      — cover-slide totals and counts
"""
import json
from collections import defaultdict

import sys
sys.path.insert(0, '/home/claude/skills/customer-deep-dives/scripts')
from lib_aggregate import (
    load_data, get_entity_group, calc_est, build_customer_slide,
    CUSTOMER_THRESHOLD, ENTITY_GROUPS
)

records, code_name_map = load_data()

# Aggregate at (entity_group, brand) level, ignoring side and salesperson
key_data = defaultdict(lambda: {'fy25':0,'bdgt':0,'ytd':0,'oo':0,'brand_type':''})
for r in records:
    cust_group = get_entity_group(r['customer'])
    key = (cust_group, r['brand'])
    d = key_data[key]
    d['fy25'] += r['fy25']
    d['bdgt'] += r['bdgt']
    d['ytd']  += r['ytd']
    d['oo']   += r['oo']
    bt = r.get('brand_type') or ''
    if bt and not d['brand_type']:
        d['brand_type'] = bt

# Group by customer
customers = defaultdict(dict)
for (cust, brand), d in key_data.items():
    customers[cust][brand] = d

# Build customer slides for those above threshold
slides = []
for cust, brand_dict in customers.items():
    cust_total_fy25 = sum(d['fy25'] for d in brand_dict.values())
    cust_total_bdgt = sum(d['bdgt'] for d in brand_dict.values())
    portfolio = max(cust_total_fy25, cust_total_bdgt)
    if portfolio < CUSTOMER_THRESHOLD:
        continue
    slide = build_customer_slide(cust, brand_dict, side_name='', use_brand_programs=True)
    slide['portfolio'] = portfolio
    slides.append(slide)

# Sort: worst miss first
slides.sort(key=lambda c: (c['totals']['miss'], -c['portfolio']))

# Cover totals: span ALL records (not just qualifying customers)
total = {
    'fy25': sum(r['fy25'] for r in records),
    'bdgt': sum(r['bdgt'] for r in records),
    'ytd':  sum(r['ytd']  for r in records),
    'oo':   sum(r['oo']   for r in records),
}
total['est']  = sum(s['totals']['est']  for s in slides)  # Est always brand-summed
total['miss'] = total['est'] - total['bdgt']

config = {
    'cust_count': len(slides),
    'totals': total
}

with open('/home/claude/deck_data_combined.json', 'w') as f:
    json.dump(slides, f, indent=2, default=str)
with open('/home/claude/combined_config.json', 'w') as f:
    json.dump(config, f, indent=2, default=str)

print(f"Combined deck data:")
print(f"  Qualifying customers (≥${CUSTOMER_THRESHOLD/1000:.0f}K): {len(slides)}")
print(f"  FY25:  ${total['fy25']:>14,.0f}")
print(f"  Bdgt:  ${total['bdgt']:>14,.0f}")
print(f"  YTD:   ${total['ytd']:>14,.0f}")
print(f"  OO:    ${total['oo']:>14,.0f}")
print(f"  Est:   ${total['est']:>14,.0f}")
print(f"  Miss:  ${total['miss']:>14,.0f}")
