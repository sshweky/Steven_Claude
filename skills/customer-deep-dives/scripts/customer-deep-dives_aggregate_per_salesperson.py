#!/usr/bin/env python3
"""
Aggregate raw Sales_Budgets data into Per-Salesperson decks. One JSON output
per qualifying salesperson (raw portfolio ≥ $750K, excluding House/Unassigned).

Inputs:
  /home/claude/sb_raw.json
  /home/claude/code_name_map.json

Outputs:
  /home/claude/sp_decks/<Name>.json       — one per salesperson with raw_portfolio ≥ $750K
"""
import json
import os
from collections import defaultdict

import sys
sys.path.insert(0, '/home/claude/skills/customer-deep-dives/scripts')
from lib_aggregate import (
    load_data, get_entity_group, build_customer_slide,
    SIDE_THRESHOLD, CUSTOMER_THRESHOLD, SALESPERSON_THRESHOLD,
    SIDE_NAMES, SIDE_TAGLINES, SIDE_ACCENTS, SKIP_SALESPEOPLE
)

records, _ = load_data()

# Aggregate at (salesperson_name, div, entity_group, brand)
# Entity grouping is applied within each salesperson's territory only.
# Brand_Type is preserved on each (sp,div,cust,brand) tuple — it's a 1:1 relation with brand.
key_data = defaultdict(lambda: {'fy25':0,'bdgt':0,'ytd':0,'oo':0,'brand_type':''})
for r in records:
    sp = r['salesperson_name']
    if sp in SKIP_SALESPEOPLE:
        continue
    if r['div'] not in ('FF', 'BB'):
        continue
    cust_group = get_entity_group(r['customer'])
    key = (sp, r['div'], cust_group, r['brand'])
    d = key_data[key]
    d['fy25'] += r['fy25']
    d['bdgt'] += r['bdgt']
    d['ytd']  += r['ytd']
    d['oo']   += r['oo']
    bt = r.get('brand_type') or ''
    if bt and not d['brand_type']:
        d['brand_type'] = bt

# Reorganize by salesperson → div → customer → brand
sp_data = defaultdict(lambda: {'FF': defaultdict(lambda: defaultdict(dict)),
                                 'BB': defaultdict(lambda: defaultdict(dict))})
for (sp, div, cust, brand), d in key_data.items():
    sp_data[sp][div][cust][brand] = d

# First pass: compute RAW portfolio (before customer threshold filtering)
sp_raw_portfolio = {}
for sp, sides in sp_data.items():
    raw_total = 0
    for div, customers in sides.items():
        side_fy25 = sum(d['fy25'] for cust in customers.values() for d in cust.values())
        side_bdgt = sum(d['bdgt'] for cust in customers.values() for d in cust.values())
        raw_total += max(side_fy25, side_bdgt)
    sp_raw_portfolio[sp] = raw_total

# Second pass: build deck data for qualifying salespeople
decks = {}
for sp, sides in sp_data.items():
    if sp_raw_portfolio.get(sp, 0) < SALESPERSON_THRESHOLD:
        continue
    deck = {'salesperson': sp, 'sections': {}, 'raw_portfolio': sp_raw_portfolio[sp]}
    
    for div, customers in sides.items():
        side_customers = []
        for cust_name, brand_dict in customers.items():
            cust_fy25 = sum(d['fy25'] for d in brand_dict.values())
            cust_bdgt = sum(d['bdgt'] for d in brand_dict.values())
            portfolio = max(cust_fy25, cust_bdgt)
            if portfolio < CUSTOMER_THRESHOLD:
                continue
            slide = build_customer_slide(cust_name, brand_dict,
                                         side_name=SIDE_NAMES[div],
                                         use_brand_programs=True)
            slide['portfolio'] = portfolio
            side_customers.append(slide)
        
        side_customers.sort(key=lambda c: (c['totals']['miss'], -c['portfolio']))
        if not side_customers:
            continue
        
        side_total = {
            'fy25': sum(c['totals']['fy25'] for c in side_customers),
            'bdgt': sum(c['totals']['bdgt'] for c in side_customers),
            'ytd':  sum(c['totals']['ytd']  for c in side_customers),
            'oo':   sum(c['totals']['oo']   for c in side_customers),
            'est':  sum(c['totals']['est']  for c in side_customers),
        }
        side_total['miss'] = side_total['est'] - side_total['bdgt']
        side_total['cov'] = ((side_total['ytd'] + side_total['oo']) / side_total['bdgt'] * 100) if side_total['bdgt'] > 0 else 0
        
        portfolio_side = max(side_total['fy25'], side_total['bdgt'])
        if portfolio_side < SIDE_THRESHOLD:
            continue
        
        deck['sections'][div] = {
            'side_name':  SIDE_NAMES[div],
            'tagline':    SIDE_TAGLINES[div],
            'accent':     SIDE_ACCENTS[div],
            'totals':     side_total,
            'customers':  side_customers,
            'cust_count': len(side_customers)
        }
    
    if not deck['sections']:
        continue
    deck['portfolio'] = sp_raw_portfolio[sp]
    deck['structure'] = '+'.join(deck['sections'].keys())
    decks[sp] = deck

# Save
os.makedirs('/home/claude/sp_decks', exist_ok=True)
for sp, deck in decks.items():
    safe = sp.replace(' ', '_').replace("'", '').replace(',', '')
    with open(f'/home/claude/sp_decks/{safe}.json', 'w') as f:
        json.dump(deck, f, indent=2, default=str)

print(f"Per-salesperson deck data: {len(decks)} salespeople qualified.")
for sp, deck in sorted(decks.items(), key=lambda x: -x[1]['portfolio']):
    total_custs = sum(s['cust_count'] for s in deck['sections'].values())
    sections = '/'.join(SIDE_NAMES[d] for d in deck['sections'].keys())
    print(f"  {sp:<22} {sections:<22} {total_custs:>3} customers   portfolio ${deck['portfolio']/1e6:>5.1f}M")
