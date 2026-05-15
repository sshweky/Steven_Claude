# Business Rules

The math, thresholds, and classification rules that govern what gets shown and how.

## Estimation formula (the heart of the deck)

```
Est. Sales = MAX(YTD ÷ 0.30, (YTD + OO) ÷ 0.45)
```

Implemented at the **brand level**, then summed to customer / side / portfolio.

```python
YTD_FRAC = 0.30   # YTD assumed to represent ~30% of full year
H1_FRAC  = 0.45   # YTD + OO (which ships in H1) assumed to be ~45% of full year

def calc_est(ytd, oo):
    if ytd <= 0 and oo <= 0:
        return 0
    e1 = ytd / YTD_FRAC if ytd > 0 else 0
    e2 = (ytd + oo) / H1_FRAC if (ytd + oo) > 0 else 0
    return max(e1, e2)
```

**Why MAX, not average:** the formula picks the more optimistic of two views. If a customer has heavy YTD shipping (low OO), `YTD ÷ 0.30` reflects the run-rate. If they have heavy OO (light YTD because programs haven't shipped yet), `(YTD + OO) ÷ 0.45` reflects the committed pipeline. Whichever is higher is the better predictor.

**Why not at customer level:** customer-level Est = sum of brand-level Ests. The MAX function is non-linear, so summing brand-level Ests gives a more accurate customer estimate than applying the formula to customer totals.

## Entertainment-brand override (added May 10, 2026)

P+P is discontinuing all entertainment-branded items. Any row where `Brand_Type = 'Entertainment'` is forced to **booked-only** — no forward projection:

```python
ENTERTAINMENT_BRAND_TYPE = 'Entertainment'

def calc_est(ytd, oo, brand_type=None):
    if brand_type == ENTERTAINMENT_BRAND_TYPE:
        return ytd + oo                  # booked only, no extrapolation
    # ...standard formula below...
```

The Brand_Type field comes directly from Sales_Budgets (added to the pull as the 9th column — see `data_pull_strategy.md`). Don't maintain a hardcoded brand allowlist — new licensed lines added to the catalog will be Brand_Type-tagged automatically and the rule picks them up. Current entertainment lines include Disney, Star Wars, Care Bears, DC Comics, Dr. Seuss, Friends, Harry Potter, Looney Tunes, Peanuts, Peeps, Rudolph, Scooby Doo, Spongebob, Universal-Horror, Warner Bros, plus several others — 23 brands totaling roughly 250 rows in the May 2026 pull.

In the brand table, entertainment rows render with a small italic `*DISC*` tag appended to the brand name (e.g., `Disney  *DISC*`). The Miss column is still computed (`Est − Bdgt`) and colored — the tag is informational, not a column. See `layout_specs.md` for the tag rendering detail.

**Reconciliation impact:** Entertainment runoff pulls the portfolio FY26 Est down by roughly $2–$4M vs. the prior version. The reconciliation sanity check below ("FY26 Est within ~1% of Bdgt") no longer applies when entertainment volume sat in budget but had no booked support — the gap is deliberate.

## Coverage % (`cov`)

```
cov = (YTD + OO) / Budget × 100
```

Used for the risk classifier and the coverage progress bar on the customer slide.

## Miss

```
miss = Est - Budget
```

- Negative → behind budget (loss)
- Positive → ahead of budget (gain)

## Risk classification

```python
def calc_risk(cov):
    if cov >= 90: return 'ON TRACK'
    if cov >= 60: return 'WATCH'
    if cov >= 35: return 'MEDIUM'
    if cov >= 20: return 'HIGH'
    return 'CRITICAL'
```

Customers with no budget show 'NO BUDGET' (muted grey).

## Thresholds (all in USD)

| Threshold | Value | Applied to |
|---|---|---|
| Salesperson qualification | $750,000 | Salesperson's raw portfolio (max of side fy25 vs side bdgt, summed) |
| Side qualification | $100,000 | A side qualifies for a section if `max(side_fy25, side_bdgt) ≥ this` |
| Customer qualification | $100,000 | Customer appears in deep-dive slides if `max(customer_fy25, customer_bdgt) ≥ this` |
| Brand floor | $5,000 | Brands below `|fy25|+|bdgt|+|ytd|+|oo| < $5K` are dropped from the brand table (immaterial) |

These are the values used in production. Adjust in `scripts/aggregate_*.py` if review scope changes.

## Brand selection for customer slide

For a given customer, after filtering brands below the $5K floor:

1. **Sort by `abs(miss)` DESCENDING** — surfaces both worst losses and biggest overshoots
2. **Take top 8**
3. **Roll remaining brands into "Other (N brands)" row** — sums their fy25/bdgt/ytd/oo/est/miss
4. **Display sorted by `miss` ASCENDING** — worst loss at top, gains at bottom

The selection sort (by abs miss) ≠ the display sort (by miss). This means a brand that's $1M ahead of budget appears in the "top 8" alongside a brand that's $1M behind, and they show in opposite ends of the displayed table.

## Programs Won / Lost (Combined and Side-Split decks)

Used when `Product_Category` is available (legacy combined/side-split decks pulled categories from Invoices/Customer_POs).

**Won** (a Brand+Category combo entered the customer):
```
fy25_brand_cat < $5K AND ytd_brand_cat + oo_brand_cat ≥ $25K
```

**Lost** (a Brand+Category combo exited the customer):
```
fy25_brand_cat ≥ $25K AND ytd_brand_cat + oo_brand_cat < $5K
```

Show top 5 won + top 5 lost mixed, sorted by `abs(impact)` DESC. Net Programs = `sum(won) − sum(lost)`.

## Brand Entries / Exits (Per-Salesperson decks)

Sales_Budgets has no Product_Category column. Per-salesperson decks operate at brand-level only. Same thresholds, different granularity:

**Entered** (brand new at this customer this year):
```
fy25_brand < $5K AND ytd_brand + oo_brand ≥ $25K
```

**Exited** (brand discontinued at this customer):
```
fy25_brand ≥ $25K AND ytd_brand + oo_brand < $5K
```

The 3-section callout's third panel shows "Brand Entries / Exits" instead of "Programs Won / Lost". The "category" field on each entry is set to "Brand Entry" or "Brand Exit" rather than a real category name.

## Skip rules (apply to all variants)

- **Skip salespeople**: `'House Account'`, `'UNASSIGNED'`, `'<Unassigned>'`, blank
- **Skip rows with Div not in `('FF', 'BB')`**: rare, but catches data hygiene issues
- **Skip customers below $100K** from deep-dive slides; their data still rolls up into portfolio totals

## Reconciliation reference

Expected aggregate totals from BV 0326, 2026 active rows, all customers (as of May 2026):

| Metric | Total | FF | BB |
|---|---|---|---|
| FY25 Actual | $142.3M | $95.5M | $46.8M |
| FY26 Budget | $154.1M | $99.2M | $54.9M |
| 2026 YTD Shipped | $50.8M | $33.3M | $17.5M |
| 2026 Open Orders | $12.6M | $5.8M | $6.8M |

Salespeople ranked by raw portfolio (max of side fy25 vs side bdgt, summed):

| Rank | Salesperson | Sides | Portfolio |
|---|---|---|---|
| House | House Account | FF+BB | $46.9M (excluded from per-sp decks) |
| 1 | Caroline McIntosh | FF (BB <$1K) | $20.9M |
| 2 | Richard Goodrum | FF | $20.4M |
| 3 | Jason Shorr | FF+BB | $20.1M |
| 4 | George Tuttle | FF | $15.3M |
| 5 | Isaac Sasson | BB (FF <$80K) | $14.7M |
| 6 | Joseph Chalom | FF+BB | $14.5M |
| 7 | Jeannine Duggins | FF+BB | $6.1M |
| 8 | Daniel Sawford | FF | $3.3M |
| 9 | Ralph Shweky | BB | $0.9M |

If your numbers differ materially from the above (>5%), something is off — check the BV filter, the year filter, and the column projection.
