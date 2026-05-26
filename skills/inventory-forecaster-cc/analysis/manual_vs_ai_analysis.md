# Manual vs AI Projection Analysis
**Generated:** 2026-05-26  
**Source table:** Projections (bpd237tvm), InventoryTrack app  
**Filter:** Status @ Cust starts with 'A' (active records)  
**Sample:** 4909 records

---

## 1. Sample Composition

| Segment | Count | % |
|---|---|---|
| Both MAN and AI > 0 | 3,051 | 62.2% |
| MAN only (AI = 0) | 793 | 16.2% |
| AI only (MAN = 0) | 173 | 3.5% |
| Both zero | 892 | 18.2% |

## 2. Overall Bias

| Direction | Count | % |
|---|---|---|
| UP (MAN > AI by >5%) | 1,843 | 60.4% |
| DOWN (MAN < AI by >5%) | 895 | 29.3% |
| FLAT (within 5%) | 313 | 10.3% |

**Aggregate volume bias:** +21.0%  (Total MAN 16,765,099 vs AI 13,855,162)
**Median delta:** +62.5%

## 3. Patterns by Customer

### Top 10 — Systematic UPWARD bias (AI under-projecting)

| Customer | N | UP | DN | Agg Bias% |
|---|---|---|---|---|
| GABRIEL BROTHERS INC | 24 | 24 | 0 | +1761.2% |
| VALLEY FOOD SUPER CENTER | 1 | 1 | 0 | +766.7% |
| BOMGAAR'S | 14 | 14 | 0 | +654.1% |
| MORALE W & R #0120 | 14 | 12 | 2 | +116.3% |
| PET CIRCLE | 19 | 17 | 2 | +110.3% |
| UNFI- EAST | 3 | 3 | 0 | +106.1% |
| EUROPRIS AS | 3 | 3 | 0 | +98.1% |
| T J MAXX | 6 | 5 | 0 | +97.3% |
| METRO INC. | 5 | 5 | 0 | +96.2% |
| CVS CORPORATION | 21 | 17 | 4 | +85.7% |

### Top 10 — Systematic DOWNWARD bias (AI over-projecting)

| Customer | N | UP | DN | Agg Bias% |
|---|---|---|---|---|
| GRUP CONOCIDO S.A. DE C.V | 10 | 1 | 9 | -65.2% |
| THE HOME DEPOT | 3 | 2 | 1 | -63.9% |
| PET PHARM LTD | 31 | 9 | 22 | -57.3% |
| RED APPLE STORES INC. | 3 | 0 | 3 | -51.7% |
| SIERRA TRADING POST, INC | 2 | 0 | 2 | -49.4% |
| ORGILL INC | 21 | 8 | 12 | -43.7% |
| CAVALLARO FOODS, LLC | 4 | 0 | 4 | -36.5% |
| VAN DEN BOSCH | 6 | 1 | 4 | -35.5% |
| PIGGLY WIGGLY OF AL | 5 | 0 | 5 | -32.2% |
| HONG CHI PETCARE CO., LTD | 25 | 7 | 17 | -31.0% |

## 4. Patterns by Brand

| Brand | N | UP | DN | Agg Bias% |
|---|---|---|---|---|
| Great Value [WMT] | 1 | 1 | 0 | +1207.6% |
| Clorox Fraganzia (Dollar) | 1 | 1 | 0 | +734.4% |
| Cleanze | 3 | 3 | 0 | +615.3% |
| Pine Sol | 15 | 14 | 0 | +197.6% |
| Arm & Hammer / Treadz | 5 | 5 | 0 | +135.5% |
| Clorox | 49 | 39 | 2 | +104.0% |
| Glad for Kids (Generic) | 68 | 34 | 23 | +91.9% |
| Play On [PSP] | 18 | 17 | 0 | +82.3% |
| Glad for Kids (Disney Classics) | 20 | 8 | 9 | +71.5% |
| Arm & Hammer Scented Brush | 27 | 16 | 9 | +55.3% |
| ... | | | | |
| Warner Bros - Friends | 1 | 0 | 1 | -66.8% |
| Kingsford Rolled Foil | 9 | 3 | 4 | -59.4% |
| Harry Potter | 12 | 2 | 6 | -39.3% |
| Kingsford Foil Pans | 7 | 2 | 5 | -34.0% |
| Disney Pixar | 8 | 2 | 5 | -29.9% |
| Disney Classics | 32 | 10 | 20 | -28.8% |
| Moxie [LOWES] | 2 | 1 | 1 | -25.1% |
| Kingsford | 48 | 21 | 24 | -20.4% |

## 5. Patterns by Item Status

| Item Status | N | UP% | DN% | Agg Bias% |
|---|---|---|---|---|
| Active: Replen | 2597 | 59.0% | 30.0% | +21.4% |
| Active: Multi-Pk Replen | 367 | 71.1% | 24.0% | +14.9% |
| Active: Replen Commt | 60 | 75.0% | 21.7% | +41.2% |
| Active: Promo | 11 | 18.2% | 81.8% | -38.2% |
| In Prodn: Replen | 7 | 0.0% | 14.3% | -2.1% |
| Future Delete | 6 | 33.3% | 33.3% | -5.6% |
| Active: Promo Commt | 2 | 0.0% | 100.0% | -58.7% |
| Ready to Sell | 1 | 100.0% | 0.0% | +69.9% |

## 6. Magnitude vs Baseline (Volume Tier by L13W avg/wk)

| Tier | Threshold | N | UP% | DN% | Avg Δ% | Agg Bias% |
|---|---|---|---|---|---|---|
| HIGH | >=500/wk | 281 | 52.3% | 23.8% | +46.5% | +18.1% |
| MED | 100-499/wk | 690 | 61.0% | 28.7% | +81.4% | +20.7% |
| LOW | 1-99/wk | 1844 | 63.0% | 31.8% | +153.5% | +31.7% |
| ZERO | 0/wk | 236 | 48.3% | 18.2% | +191.4% | +40.2% |

## 7. Week-by-Week Shape Analysis (MAN vs AI average)

| Wk | MAN avg | AI avg | Ratio |
|---|---|---|---|
| W1 | 30.8 | 95.8 | **0.322** |
| W2 | 177.9 | 157.5 | 1.129 |
| W3 | 227.7 | 172.0 | **1.324** |
| W4 | 221.4 | 147.9 | **1.496** |
| W5 | 197.3 | 153.2 | **1.288** |
| W6 | 200.1 | 166.0 | **1.206** |
| W7 | 270.4 | 177.9 | **1.520** |
| W8 | 223.4 | 161.7 | **1.381** |
| W9 | 211.5 | 164.4 | **1.286** |
| W10 | 195.6 | 164.0 | **1.193** |
| W11 | 266.4 | 169.0 | **1.576** |
| W12 | 210.7 | 170.7 | **1.235** |
| W13 | 206.2 | 179.3 | 1.150 |
| W14 | 185.6 | 157.2 | **1.181** |
| W15 | 198.8 | 177.6 | 1.119 |
| W16 | 323.1 | 214.7 | **1.505** |
| W17 | 209.0 | 174.8 | **1.195** |
| W18 | 190.1 | 197.7 | 0.962 |
| W19 | 184.7 | 165.6 | 1.115 |
| W20 | 314.4 | 183.1 | **1.718** |
| W21 | 201.3 | 194.6 | 1.034 |
| W22 | 189.8 | 213.6 | 0.889 |
| W23 | 238.2 | 197.3 | **1.208** |
| W24 | 242.1 | 190.5 | **1.271** |
| W25 | 192.5 | 195.0 | 0.987 |
| W26 | 186.0 | 200.0 | 0.930 |

**Front-load score by direction** (>1 = MAN heavier in W1-W6 relative to W7-W26):

- UP: 1.018
- DOWN: 1.048
- FLAT: 1.279

## 8. Kill Patterns (Planner Zeros ≥ 20 Weeks, AI > 0)

**777 records killed** (planner zeroed out AI forecast)

**Top customers (kill count):**

- AMAZON.COM.KYDC,INC: 115
- VARIETY WHOLESALERS INC: 80
- BURLINGTON COAT FACTORY: 69
- DD'S DISCOUNTS: 66
- ROSS STORES INC - MERCHANDISE: 38
- TROPICAL REPS AND DISTRIBUTORS: 32
- GABRIEL BROTHERS INC: 24
- KOHL'S DEPT STR: 18
- CERTCO INC.: 17
- HONG CHI PETCARE CO., LTD: 17

**Top brands (kill count):**

- Clorox Fraganzia: 109
- Gladware: 92
- Arm & Hammer Core Grooming: 78
- Glad: 50
- Burt's Bees: 48
- Fabuloso: 41
- Arm & Hammer Specialty: 38
- Arm & Hammer Complete Care: 28
- Arm & Hammer Core Dental: 23
- Clorox: 21

## 9. Spike Patterns

1429 of 3051 records (46.8%) have at least one spike week (MAN > 2× AI avg for that week).

**Most common spike weeks:**

| Week | Count |
|---|---|
| W16 | 901 |
| W20 | 896 |
| W11 | 854 |
| W7 | 839 |
| W3 | 781 |
| W24 | 723 |
| W8 | 674 |
| W12 | 656 |
| W4 | 653 |
| W17 | 647 |

## 10. L13W Anchoring

| Direction | N | avg MAN / L13W-basis | avg AI / L13W-basis |
|---|---|---|---|
| UP | 1729 | 3.865x | 1.986x |
| DOWN | 852 | 0.901x | 1.768x |
| FLAT | 234 | 1.123x | 1.121x |

## 11. By Inventory Manager

| Manager | N | UP% | DN% | Agg Bias% |
|---|---|---|---|---|
| John Grossi | 955 | 67.3% | 24.7% | +18.5% |
| Tae Kang | 788 | 46.3% | 44.2% | +32.3% |
| Shina Yang | 493 | 71.0% | 12.2% | +16.9% |
| Amy Rodriguez | 466 | 57.5% | 30.5% | +13.1% |
| Jonathan Pichardo | 314 | 63.4% | 30.3% | +24.3% |
| Mikey Scott | 35 | 51.4% | 40.0% | +11.2% |

## 12. Algorithm Improvement Hypotheses

Based on the patterns above. Review and confirm before implementing.

| Priority | Finding | Proposed Change | Effort |
|---|---|---|---|
| 1. Order-cadence modeling | Spike weeks W16, W20, W11, W7, W3 suggest 4-week order cycle encoding by planners | Model per-customer order cadence; project in cycle-aligned buckets not flat weekly | Medium |
| 2. Horizon confidence decay | DOWN records front-load score 1.15 — planners cut AI back-half more than near-term | Apply exponential damping beyond W8 for items with no strong seasonal signal | Low |
| 3. L4W/L13W trend signal | AI projects 1.77× L13W on DOWN records — not picking up recent decline | When L4W/L13W < 0.8, anchor base = L4W×0.6 + L13W×0.4 | Low |
| 4. Channel type suppression | Planners kill 100% of AI forecasts for off-price/closeout accounts (AMAZON.COM.KYDC,INC, VARIETY WHOLESALERS INC, BURLINGTON COAT FACTORY, …) | Add channel-type flag; suppress AI for closeout/opportunistic accounts | Low |
| 5. Zero-velocity suppression | 236 records with L13W=0 still receive AI projections | If both L4W and L13W = 0, AI projection = 0 unless launch/POG trigger flag set | Low |
| 6. Multi-pack unit conversion | Multi-Pk Replen items avg delta 227% — AI under-projects | Derive demand from parent single-pack L13W ÷ units-per-pack instead of sparse multi-pack history | Medium |

---
*Report generated by `scripts/analyze_manual_vs_ai.py` on 2026-05-26*