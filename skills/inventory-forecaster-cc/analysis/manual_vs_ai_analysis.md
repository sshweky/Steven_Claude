# Manual vs AI Projection Analysis
**Generated:** 2026-05-20  
**Source table:** Projections (bpd237tvm), InventoryTrack app  
**Filter:** Status @ Cust starts with 'A' (active records)  
**Sample:** 4960 records

---

## 1. Sample Composition

| Segment | Count | % |
|---|---|---|
| Both MAN and AI > 0 | 2,937 | 59.2% |
| MAN only (AI = 0) | 884 | 17.8% |
| AI only (MAN = 0) | 182 | 3.7% |
| Both zero | 957 | 19.3% |

## 2. Overall Bias

| Direction | Count | % |
|---|---|---|
| UP (MAN > AI by >5%) | 1,814 | 61.8% |
| DOWN (MAN < AI by >5%) | 865 | 29.5% |
| FLAT (within 5%) | 258 | 8.8% |

**Aggregate volume bias:** +13.9%  (Total MAN 16,429,009 vs AI 14,420,619)
**Median delta:** +73.3%

## 3. Patterns by Customer

### Top 10 — Systematic UPWARD bias (AI under-projecting)

| Customer | N | UP | DN | Agg Bias% |
|---|---|---|---|---|
| VALLEY FOOD SUPER CENTER | 1 | 1 | 0 | +333.3% |
| PET CIRCLE | 16 | 15 | 1 | +326.7% |
| MORALE W & R #0120 | 10 | 8 | 2 | +172.4% |
| GABRIEL BROTHERS INC | 24 | 24 | 0 | +151.5% |
| CERTCO INC. | 28 | 22 | 5 | +115.8% |
| HEINEN'S HEADQUARTERS | 5 | 4 | 1 | +114.7% |
| METRO INC. | 5 | 5 | 0 | +94.2% |
| EUROPRIS AS | 3 | 3 | 0 | +86.6% |
| ROSS STORES INC - MERCHANDISE | 39 | 33 | 6 | +81.4% |
| VARIETY DISTRIBUTORS INC | 4 | 4 | 0 | +74.7% |

### Top 10 — Systematic DOWNWARD bias (AI over-projecting)

| Customer | N | UP | DN | Agg Bias% |
|---|---|---|---|---|
| GRUP CONOCIDO S.A. DE C.V | 11 | 1 | 9 | -57.7% |
| HOBBY LOBBY INC. WAREHOUSE | 4 | 0 | 4 | -48.3% |
| SIERRA TRADING POST, INC | 2 | 0 | 2 | -46.6% |
| RED APPLE STORES INC. | 3 | 0 | 3 | -43.7% |
| BEALLS OUTLET | 1 | 0 | 1 | -37.8% |
| KRASDALE FOODS | 8 | 1 | 7 | -33.7% |
| COMERCIALIZADORA MEXICO AMERIC | 18 | 3 | 13 | -31.2% |
| PRESTIGE PET PRODUCTS | 5 | 1 | 3 | -31.0% |
| FIELIN PET CLOTHES | 5 | 1 | 4 | -30.9% |
| VAN DEN BOSCH | 6 | 1 | 5 | -30.8% |

## 4. Patterns by Brand

| Brand | N | UP | DN | Agg Bias% |
|---|---|---|---|---|
| Cleanze | 3 | 3 | 0 | +375.3% |
| Clorox Fraganzia (Dollar) | 2 | 2 | 0 | +329.9% |
| Arm & Hammer / Treadz | 5 | 5 | 0 | +208.7% |
| Biosilk [OTHER] | 6 | 5 | 1 | +173.5% |
| Pine Sol | 15 | 14 | 1 | +155.2% |
| Thrive [PSM] | 1 | 1 | 0 | +106.4% |
| Clorox | 42 | 37 | 5 | +99.9% |
| Full Cheeks [PSM] | 2 | 2 | 0 | +79.0% |
| Play On [PSP] | 18 | 16 | 2 | +71.7% |
| Glad for Kids (Generic) | 60 | 32 | 16 | +47.1% |
| … | | | | |
| Warner Bros - Friends | 1 | 0 | 1 | -70.0% |
| Kingsford Rolled Foil | 9 | 5 | 3 | -56.3% |
| Disney Pixar | 8 | 2 | 6 | -46.0% |
| Kingsford Foil Pans | 7 | 1 | 5 | -31.8% |
| Disney Classics | 30 | 10 | 16 | -28.6% |
| Kingsford | 44 | 21 | 23 | -19.2% |
| Arm & Hammer Complete Care | 117 | 51 | 58 | -16.4% |
| GNC Pets [Essentials] | 7 | 3 | 3 | -11.2% |

## 5. Patterns by Item Status

| Item Status | N | UP% | DN% | Agg Bias% |
|---|---|---|---|---|
| Active: Replen | 2535 | 60.9% | 29.8% | +13.8% |
| Active: Multi-Pk Replen | 321 | 67.6% | 28.3% | +15.1% |
| Active: Replen Commt | 60 | 81.7% | 11.7% | +99.6% |
| Active: Promo | 8 | 25.0% | 75.0% | -7.9% |
| In Prodn: Replen | 7 | 0.0% | 14.3% | -2.2% |
| Future Delete | 3 | 33.3% | 66.7% | -43.5% |
| Discontinued | 1 | 0.0% | 100.0% | -79.1% |
| Active: Promo Commt | 1 | 0.0% | 100.0% | -25.0% |
| In Prodn: Promo | 1 | 0.0% | 100.0% | -70.2% |

## 6. Magnitude vs Baseline (Volume Tier by L13W avg/wk)

| Tier | Threshold | N | UP% | DN% | Avg Δ% | Agg Bias% |
|---|---|---|---|---|---|---|
| HIGH | ≥500/wk | 286 | 57.7% | 29.4% | +71.9% | +9.0% |
| MED | 100-499/wk | 675 | 59.0% | 31.9% | +103.1% | +22.2% |
| LOW | 1-99/wk | 1831 | 64.9% | 28.7% | +138.4% | +35.5% |
| ZERO | 0/wk | 145 | 42.8% | 27.6% | +123.3% | -6.9% |

## 7. Week-by-Week Shape Analysis (MAN vs AI average)

| Wk | MAN avg | AI avg | Ratio |
|---|---|---|---|
| W1 | 66.7 | 154.6 | **0.431** |
| W2 | 129.1 | 112.8 | 1.144 |
| W3 | 195.6 | 178.0 | 1.099 |
| W4 | 242.0 | 177.6 | **1.362** |
| W5 | 227.0 | 186.3 | **1.218** |
| W6 | 202.7 | 179.0 | 1.132 |
| W7 | 204.6 | 207.3 | 0.987 |
| W8 | 275.4 | 196.8 | **1.399** |
| W9 | 226.7 | 208.0 | 1.090 |
| W10 | 217.0 | 194.7 | 1.115 |
| W11 | 199.5 | 206.2 | 0.967 |
| W12 | 270.8 | 175.6 | **1.542** |
| W13 | 217.2 | 210.7 | 1.031 |
| W14 | 210.7 | 183.0 | **1.151** |
| W15 | 190.5 | 223.9 | 0.851 |
| W16 | 204.4 | 202.5 | 1.009 |
| W17 | 328.1 | 251.3 | **1.306** |
| W18 | 214.9 | 196.3 | 1.095 |
| W19 | 193.4 | 210.1 | 0.921 |
| W20 | 191.6 | 159.0 | **1.205** |
| W21 | 318.1 | 206.9 | **1.538** |
| W22 | 203.4 | 183.1 | 1.111 |
| W23 | 192.6 | 195.3 | 0.986 |
| W24 | 244.8 | 187.6 | **1.304** |
| W25 | 238.2 | 172.2 | **1.384** |
| W26 | 188.8 | 151.2 | **1.249** |

**Front-load score by direction** (>1 = MAN heavier in W1-W6 relative to W7-W26):

- UP: 0.978
- DOWN: 1.044
- FLAT: 0.950

## 8. Kill Patterns (Planner Zeros ≥ 20 Weeks, AI > 0)

**766 records killed** (planner zeroed out AI forecast)

**Top customers (kill count):**

- AMAZON.COM.KYDC,INC: 87
- VARIETY WHOLESALERS INC: 85
- DD'S DISCOUNTS: 64
- BURLINGTON COAT FACTORY: 58
- ROSS STORES INC - MERCHANDISE: 44
- TROPICAL REPS AND DISTRIBUTORS: 32
- GABRIEL BROTHERS INC: 24
- CERTCO INC.: 17
- HONG CHI PETCARE CO., LTD: 17
- COMERCIALIZADORA MEXICO AMERIC: 17

**Top brands (kill count):**

- Clorox Fraganzia: 114
- Gladware: 112
- Arm & Hammer Core Grooming: 74
- Glad: 51
- Burt's Bees: 37
- Arm & Hammer Specialty: 36
- Fabuloso: 28
- Arm & Hammer Complete Care: 25
- Arm & Hammer Core Dental: 21
- Clorox: 21

## 9. Spike Patterns

1342 of 2937 records (45.7%) have at least one spike week (MAN > 2× AI avg for that week).

**Most common spike weeks:**

| Week | Count |
|---|---|
| W17 | 899 |
| W21 | 886 |
| W12 | 832 |
| W8 | 815 |
| W4 | 752 |
| W25 | 719 |
| W24 | 690 |
| W18 | 660 |
| W14 | 636 |
| W20 | 626 |

## 10. L13W Anchoring

| Direction | N | avg MAN / L13W-basis | avg AI / L13W-basis |
|---|---|---|---|
| UP | 1752 | 2.443x | 0.914x |
| DOWN | 825 | 2.692x | 3.414x |
| FLAT | 215 | 1.223x | 1.220x |

## 11. By Inventory Manager

| Manager | N | UP% | DN% | Agg Bias% |
|---|---|---|---|---|
| John Grossi | 899 | 61.1% | 30.8% | +0.9% |
| Tae Kang | 755 | 53.8% | 38.3% | +29.2% |
| Shina Yang | 482 | 75.9% | 13.9% | +29.3% |
| Amy Rodriguez | 456 | 61.8% | 30.0% | +8.2% |
| Jonathan Pichardo | 309 | 63.1% | 29.4% | +19.1% |
| Mikey Scott | 36 | 44.4% | 11.1% | +14.3% |

## 12. Algorithm Improvement Hypotheses

Based on the patterns above. Review and confirm before implementing.

| Priority | Finding | Proposed Change | Effort |
|---|---|---|---|
| 1. Order-cadence modeling | Spike weeks W17, W21, W12, W8, W4 suggest 4-week order cycle encoding by planners | Model per-customer order cadence; project in cycle-aligned buckets not flat weekly | Medium |
| 2. Horizon confidence decay | DOWN records front-load score 1.14 — planners cut AI back-half more than near-term | Apply exponential damping beyond W8 for items with no strong seasonal signal | Low |
| 3. L4W/L13W trend signal | AI projects 3.41× L13W on DOWN records — not picking up recent decline | When L4W/L13W < 0.8, anchor base = L4W×0.6 + L13W×0.4 | Low |
| 4. Channel type suppression | Planners kill 100% of AI forecasts for off-price/closeout accounts (AMAZON.COM.KYDC,INC, VARIETY WHOLESALERS INC, DD'S DISCOUNTS, …) | Add channel-type flag; suppress AI for closeout/opportunistic accounts | Low |
| 5. Zero-velocity suppression | 145 records with L13W=0 still receive AI projections | If both L4W and L13W = 0, AI projection = 0 unless launch/POG trigger flag set | Low |
| 6. Multi-pack unit conversion | Multi-Pk Replen items avg delta 196% — AI under-projects | Derive demand from parent single-pack L13W ÷ units-per-pack instead of sparse multi-pack history | Medium |

---
*Report generated by `scripts/analyze_manual_vs_ai.py` on 2026-05-20*