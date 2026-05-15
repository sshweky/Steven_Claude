"""
Shared aggregator library for Customer Deep Dives.
Used by aggregate_combined.py, aggregate_side_split.py, aggregate_per_salesperson.py.

Loads raw Sales_Budgets data + Accounts code-name map and exposes:
  - load_data()           — read sb_raw.json + code_name_map.json, translate codes to names
  - get_entity_group()    — apply customer entity grouping rules
  - calc_est()            — Est = MAX(YTD/0.30, (YTD+OO)/0.45)
  - calc_risk()           — risk classifier from coverage %
  - build_customer_slide() — produce the per-customer slide payload (brand table, totals, programs)
"""
import json
from collections import defaultdict

# -----------------------------------------------------------------------------
# Constants — match references/business_rules.md
# -----------------------------------------------------------------------------
SIDE_THRESHOLD = 100_000
CUSTOMER_THRESHOLD = 100_000
SALESPERSON_THRESHOLD = 750_000
BRAND_FLOOR = 5_000

PROG_LOST_FY25_MIN = 25_000
PROG_LOST_2026_MAX = 5_000
PROG_WON_FY25_MAX = 5_000
PROG_WON_2026_MIN = 25_000

YTD_FRAC = 0.30
H1_FRAC = 0.45

SKIP_SALESPEOPLE = {'House Account', 'UNASSIGNED', '<Unassigned>', '(BLANK)', '',
                    'FC000', 'BB000'}   # house-account codes — Amazon, Walmart import buckets etc.

ENTERTAINMENT_BRAND_TYPE = 'Entertainment'   # rows with this Brand_Type are booked-only — no projection

SIDE_NAMES = {'FF': 'FETCH', 'BB': 'BRAND BUZZ'}
SIDE_TAGLINES = {'FF': 'Pet Products', 'BB': 'People Products'}
SIDE_ACCENTS = {'FF': '7C3AED', 'BB': 'EA580C'}

ENTITY_GROUPS = [
    ('Walmart Group', ['WAL MART STORES', 'WALMART.COM', 'WAL-MART CANADA',
                       'WAL MART STORES FULFILLMENT', 'WALMART CANADA DIRECT']),
    ('PetSmart Inc', ['PETSMART INC']),
    ('PetSmart Import', ['PETSMART IMPORT']),
    ('Petco', ['PETCO ANIMAL SUPPLIES, INC', 'PETCO ANIMAL SUPPLIES IMPORT']),
    ('Ross Stores', ['ROSS STORES INC - MERCHANDISE', 'ROSS STORES INC IMPORT']),
    ('Dollar Tree', ['DOLLAR TREE IMPORT', 'DOLLAR TREE MERCHANDISING']),
    ('Loblaws', ['LOBLAWS INC']),
    ('Burlington', ['BURLINGTON COAT FACTORY']),
]


# -----------------------------------------------------------------------------
# Loaders
# -----------------------------------------------------------------------------
def load_data(sb_path='/home/claude/sb_raw.json', map_path='/home/claude/code_name_map.json'):
    """Load raw Sales_Budgets rows and Accounts code-name map. Translate codes to names.
    
    Returns: list of records, each with keys customer/brand/div/salesperson/fy25/bdgt/ytd/oo.
    """
    with open(sb_path) as f:
        records = json.load(f)
    with open(map_path) as f:
        code_name_map = json.load(f)
    for r in records:
        r['salesperson_name'] = code_name_map.get(r.get('salesperson') or '', r.get('salesperson') or '')
    return records, code_name_map


def get_entity_group(customer_name):
    """Return the entity group display name for a customer, or the original name if no match."""
    for group_name, entities in ENTITY_GROUPS:
        if customer_name in entities:
            return group_name
    return customer_name


# -----------------------------------------------------------------------------
# Math
# -----------------------------------------------------------------------------
def calc_est(ytd, oo, brand_type=None):
    """Est = MAX(YTD/0.30, (YTD+OO)/0.45).

    For entertainment brands (Brand_Type == 'Entertainment') return YTD+OO only —
    P+P is discontinuing all entertainment-branded items, so no forward projection.
    """
    if brand_type == ENTERTAINMENT_BRAND_TYPE:
        return ytd + oo
    if ytd <= 0 and oo <= 0:
        return 0
    e1 = ytd / YTD_FRAC if ytd > 0 else 0
    e2 = (ytd + oo) / H1_FRAC if (ytd + oo) > 0 else 0
    return max(e1, e2)


def calc_risk(cov):
    """Risk classification based on coverage %"""
    if cov >= 90: return 'ON TRACK'
    if cov >= 60: return 'WATCH'
    if cov >= 35: return 'MEDIUM'
    if cov >= 20: return 'HIGH'
    return 'CRITICAL'


# -----------------------------------------------------------------------------
# Customer slide builder (shared across all 3 variants)
# -----------------------------------------------------------------------------
def build_customer_slide(cust_name, brand_dict, side_name='', use_brand_programs=True):
    """Build the customer-level slide payload from a {brand: {fy25,bdgt,ytd,oo}} dict.
    
    Args:
        cust_name: Customer/entity-group display name
        brand_dict: dict of brand → {fy25, bdgt, ytd, oo} (use lib totals to fill)
        side_name: 'FETCH' or 'BRAND BUZZ' (used for the side label in header)
        use_brand_programs: True for per-salesperson decks (Brand Entries/Exits),
                            False if Programs Won/Lost is computed externally with category
    
    Returns: dict matching the schema expected by the JS deck builders.
    """
    brand_rows = []
    
    for brand, d in brand_dict.items():
        if abs(d['fy25']) + abs(d['bdgt']) + abs(d['ytd']) + abs(d['oo']) < BRAND_FLOOR:
            continue
        brand_type = d.get('brand_type', '')
        is_disc = (brand_type == ENTERTAINMENT_BRAND_TYPE)
        est = calc_est(d['ytd'], d['oo'], brand_type)
        miss = est - d['bdgt'] if d['bdgt'] > 0 else 0
        cov = ((d['ytd'] + d['oo']) / d['bdgt'] * 100) if d['bdgt'] > 0 else 0
        brand_rows.append({
            'brand': brand,
            'brand_type': brand_type,
            'disc': is_disc,
            'fy25': d['fy25'], 'bdgt': d['bdgt'], 'ytd': d['ytd'], 'oo': d['oo'],
            'est': est, 'miss': miss, 'cov': cov
        })
    
    # Selection sort: by abs(miss) DESC, take top 8, roll rest into "Other"
    brand_rows.sort(key=lambda x: -abs(x['miss']))
    if len(brand_rows) > 8:
        top = brand_rows[:8]
        other = brand_rows[8:]
        other_row = {
            'brand': f'Other ({len(other)} brands)',
            'brand_type': '',
            'disc': False,
            'fy25': sum(r['fy25'] for r in other),
            'bdgt': sum(r['bdgt'] for r in other),
            'ytd':  sum(r['ytd']  for r in other),
            'oo':   sum(r['oo']   for r in other),
            'est':  sum(r['est']  for r in other),
            'miss': sum(r['miss'] for r in other),
            'cov':  0
        }
        if other_row['bdgt'] > 0:
            other_row['cov'] = (other_row['ytd'] + other_row['oo']) / other_row['bdgt'] * 100
        brand_rows = top + [other_row]
    
    # Display sort: by miss ASC (worst first)
    brand_rows.sort(key=lambda x: x['miss'])
    
    # Customer total = sum of brand-level (not derived from raw, to match the table footer)
    total = {
        'fy25': sum(r['fy25'] for r in brand_rows),
        'bdgt': sum(r['bdgt'] for r in brand_rows),
        'ytd':  sum(r['ytd']  for r in brand_rows),
        'oo':   sum(r['oo']   for r in brand_rows),
        'est':  sum(r['est']  for r in brand_rows),
    }
    total['miss'] = total['est'] - total['bdgt']
    total['cov'] = ((total['ytd'] + total['oo']) / total['bdgt'] * 100) if total['bdgt'] > 0 else 0
    risk = calc_risk(total['cov']) if total['bdgt'] > 0 else 'NO BUDGET'
    
    # Brand Entries / Exits (or Programs Won / Lost if external)
    progs_won = []
    progs_lost = []
    if use_brand_programs:
        for brand, d in brand_dict.items():
            ytd_oo = d['ytd'] + d['oo']
            if d['fy25'] >= PROG_LOST_FY25_MIN and ytd_oo < PROG_LOST_2026_MAX:
                progs_lost.append({'brand': brand, 'cat': 'Brand Exit', 'amt': d['fy25']})
            elif d['fy25'] < PROG_WON_FY25_MAX and ytd_oo >= PROG_WON_2026_MIN:
                progs_won.append({'brand': brand, 'cat': 'Brand Entry', 'amt': ytd_oo})
        progs_won.sort(key=lambda x: -x['amt'])
        progs_lost.sort(key=lambda x: -x['amt'])
        progs_won = progs_won[:5]
        progs_lost = progs_lost[:5]
    
    return {
        'name': cust_name,
        'side': side_name,
        'totals': total,
        'brand_rows': brand_rows,
        'risk': risk,
        'programs': {'won': progs_won, 'lost': progs_lost}
    }
