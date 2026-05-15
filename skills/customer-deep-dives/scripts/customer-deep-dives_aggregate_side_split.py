#!/usr/bin/env python3
"""
Aggregate raw Sales_Budgets data into Side-Split decks (Fetch and Brand Buzz separate).

Inputs:
  /home/claude/sb_raw.json
  /home/claude/code_name_map.json

Outputs:
  /home/claude/deck_custs_pet.json      — list of FF customer slide payloads
  /home/claude/deck_custs_people.json   — list of BB customer slide payloads
  /home/claude/pet_config.json          — FF cover totals
  /home/claude/people_config.json       — BB cover totals
"""
import json
from collections import defaultdict

import sys
sys.path.insert(0, '/home/claude/skills/customer-deep-dives/scripts')
from lib_aggregate import (
    load_data, get_entity_group, build_customer_slide,
    CUSTOMER_THRESHOLD
)

records, _ = load_data()

# Aggregate at (div, entity_group, brand)
key_data = defaultdict(lambda: {'fy25':0,'bdgt':0,'ytd':0,'oo':0,'brand_type':''})
for r in records:
    if r['div'] not in ('FF', 'BB'):
        continue
    cust_group = get_entity_group(r['customer'])
    key = (r['div'], cust_group, r['brand'])
    d = key_data[key]
    d['fy25'] += r['fy25']
    d['bdgt'] += r['bdgt']
    d['ytd']  += r['ytd']
    d['oo']   += r['oo']
    bt = r.get('brand_type') or ''
    if bt and not d['brand_type']:
        d['brand_type'] = bt

# Per-side rollups
def build_side(div_filter, side_name, out_data, out_config):
    customers = defaultdict(dict)
    for (div, cust, brand), d in key_data.items():
        if div != div_filter:
            continue
        customers[cust][brand] = d
    
    slides = []
    for cust, brand_dict in customers.items():
        cust_fy25 = sum(d['fy25'] for d in brand_dict.values())
        cust_bdgt = sum(d['bdgt'] for d in brand_dict.values())
        portfolio = max(cust_fy25, cust_bdgt)
        if portfolio < CUSTOMER_THRESHOLD:
            continue
        slide = build_customer_slide(cust, brand_dict, side_name=side_name, use_brand_programs=True)
        slide['portfolio'] = portfolio
        slides.append(slide)
    
    slides.sort(key=lambda c: (c['totals']['miss'], -c['portfolio']))
    
    side_records = [r for r in records if r['div'] == div_filter]
    total = {
        'fy25': sum(r['fy25'] for r in side_records),
        'bdgt': sum(r['bdgt'] for r in side_records),
        'ytd':  sum(r['ytd']  for r in side_records),
        'oo':   sum(r['oo']   for r in side_records),
    }
    total['est']  = sum(s['totals']['est']  for s in slides)
    total['miss'] = total['est'] - total['bdgt']
    
    config = {'cust_count': len(slides), 'totals': total, 'side': side_name}
    
    with open(out_data, 'w') as f:
        json.dump(slides, f, indent=2, default=str)
    with open(out_config, 'w') as f:
        json.dump(config, f, indent=2, default=str)
    return total, len(slides)

ff_total, ff_n = build_side('FF', 'FETCH',
    '/home/claude/deck_custs_pet.json',
    '/home/claude/pet_config.json')
bb_total, bb_n = build_side('BB', 'BRAND BUZZ',
    '/home/claude/deck_custs_people.json',
    '/home/claude/people_config.json')

print(f"Side-split deck data:")
print(f"  FETCH (FF):     {ff_n:>3} customers ≥$100K   FY25 ${ff_total['fy25']/1e6:>5.1f}M  Bdgt ${ff_total['bdgt']/1e6:>5.1f}M  YTD ${ff_total['ytd']/1e6:>4.1f}M  OO ${ff_total['oo']/1e6:>4.1f}M")
print(f"  BRAND BUZZ (BB):{bb_n:>3} customers ≥$100K   FY25 ${bb_total['fy25']/1e6:>5.1f}M  Bdgt ${bb_total['bdgt']/1e6:>5.1f}M  YTD ${bb_total['ytd']/1e6:>4.1f}M  OO ${bb_total['oo']/1e6:>4.1f}M")
