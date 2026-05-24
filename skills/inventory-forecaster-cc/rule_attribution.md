# Rule Attribution Report

Source: `forecast_results.json`  ·  5571 records  ·  91 rules

**Interpretation:**
- `n` = records where this rule fired
- `median |delta|` = median |AI - manual| / manual on those records
- `unit gap` = sum of (AI - manual) on those records (signed)
- Tier: CRITICAL >= 100K units AND fires on >=5% of records

## Top rules by impact

| Rank | Rule | Tier | n | Fire % | Median Δ | Unit gap | AI total | Top model |
|------|------|------|---:|---:|---:|---:|---:|---|
| 1 | `VP-Q4` | CRITICAL | 1635 | 29.3% | 40.0% | -822,789 | 9,438,848 | Seasonal Baseline |
| 2 | `F64` | CRITICAL | 1644 | 29.5% | 35.2% | -689,298 | 9,202,619 | Croston's |
| 3 | `F65` | CRITICAL | 963 | 17.3% | 100.0% | -634,543 | 348,793 | Inactive |
| 4 | `F59h` | CRITICAL | 558 | 10.0% | 75.6% | -481,478 | 1,166,102 | Croston's |
| 5 | `F59j` | HIGH | 130 | 2.3% | 24.5% | +408,721 | 2,225,058 | Seasonal Baseline |
| 6 | `F43` | CRITICAL | 374 | 6.7% | 35.6% | +392,205 | 3,199,937 | Seasonal Baseline |
| 7 | `F10` | CRITICAL | 900 | 16.2% | 55.0% | -368,205 | 4,496,723 | Sparse Intermittent |
| 8 | `F45` | HIGH | 197 | 3.5% | 39.0% | +363,416 | 1,888,935 | Seasonal Baseline |
| 9 | `F37` | CRITICAL | 2280 | 40.9% | 40.0% | -348,545 | 12,928,528 | Croston's |
| 10 | `F52` | CRITICAL | 550 | 9.9% | 76.0% | +308,909 | 620,729 | Inactive (zeroed by guards) |
| 11 | `F59d` | HIGH | 247 | 4.4% | 42.7% | +304,364 | 1,611,134 | Croston's |
| 12 | `VP-Q3` | CRITICAL | 812 | 14.6% | 40.0% | -277,982 | 2,293,805 | Croston's |
| 13 | `F59f` | HIGH | 161 | 2.9% | 66.4% | -274,649 | 900,525 | Croston's |
| 14 | `VP-Q2` | HIGH | 102 | 1.8% | 61.8% | +269,729 | 562,922 | Seasonal Baseline |
| 15 | `F59a` | HIGH | 241 | 4.3% | 37.6% | +267,389 | 2,498,780 | Seasonal Baseline |
| 16 | `F59g` | HIGH | 183 | 3.3% | 28.6% | +258,655 | 4,429,544 | Seasonal Baseline |
| 17 | `R2` | CRITICAL | 551 | 9.9% | 47.7% | -255,255 | 1,278,775 | Sparse Intermittent |
| 18 | `F26` | HIGH | 176 | 3.2% | 25.9% | -251,549 | 1,492,110 | Seasonal Baseline |
| 19 | `F49` | HIGH | 64 | 1.1% | 42.9% | +243,289 | 883,671 | Seasonal Baseline |
| 20 | `F36` | CRITICAL | 605 | 10.9% | 100.0% | -229,620 | 274,805 | Inactive (zeroed by guards) |
| 21 | `VP-OP` | HIGH | 131 | 2.4% | 39.9% | -204,014 | 412,734 | Sparse Intermittent |
| 22 | `VP-ATS` | HIGH | 55 | 1.0% | 52.0% | +201,905 | 277,222 | Seasonal Baseline |
| 23 | `F_PO_CUTOFF` | CRITICAL | 1303 | 23.4% | 94.7% | +199,300 | 3,161,986 | Inactive |
| 24 | `F14a` | HIGH | 180 | 3.2% | 40.0% | -189,741 | 1,542,779 | Croston's |
| 25 | `F72` | HIGH | 8 | 0.1% | 69.9% | -182,931 | 78,846 | Heuristic (F72 new-launch ramp) |
| 26 | `F15` | CRITICAL | 352 | 6.3% | 33.2% | +175,648 | 3,444,322 | Seasonal Baseline |
| 27 | `F59e` | CRITICAL | 279 | 5.0% | 71.7% | +173,284 | 1,069,757 | Croston's |
| 28 | `F38b` | HIGH | 223 | 4.0% | 30.4% | +172,047 | 2,478,342 | Seasonal Baseline |
| 29 | `F59i` | HIGH | 110 | 2.0% | 26.3% | -171,432 | 1,481,728 | Seasonal Baseline |
| 30 | `F44` | HIGH | 47 | 0.8% | 42.0% | +171,306 | 353,324 | Seasonal Baseline |
| 31 | `F51` | HIGH | 10 | 0.2% | 16.5% | +169,742 | 696,196 | Seasonal Baseline |
| 32 | `F46` | HIGH | 38 | 0.7% | 42.0% | +165,679 | 307,568 | Seasonal Baseline |
| 33 | `F34` | CRITICAL | 736 | 13.2% | 58.3% | -165,013 | 3,015,276 | Sparse Intermittent |
| 34 | `F23b` | CRITICAL | 382 | 6.9% | 97.6% | -163,548 | 287,386 | Heuristic |
| 35 | `F59m` | HIGH | 78 | 1.4% | 24.5% | +156,712 | 1,325,176 | Seasonal Baseline |
| 36 | `F16` | CRITICAL | 603 | 10.8% | 22.0% | +153,661 | 8,221,776 | Seasonal Baseline |
| 37 | `F66` | CRITICAL | 482 | 8.7% | 30.6% | +153,627 | 3,655,376 | Seasonal Baseline |
| 38 | `F40` | HIGH | 36 | 0.6% | 40.0% | -149,298 | 432,235 | Croston's |
| 39 | `F70` | CRITICAL | 475 | 8.5% | 100.0% | -138,259 | 48,426 | Inactive (zeroed by guards) |
| 40 | `VP-Q1` | CRITICAL | 829 | 14.9% | 26.2% | +134,281 | 8,332,460 | Seasonal Baseline |

## By rule family

| Family | Rules | Total fires | Total |unit gap| |
|---|---:|---:|---:|
| F | 76 | 18655 | 10,611,982 |
| VP | 8 | 3624 | 1,938,221 |
| R | 5 | 1176 | 415,722 |
| F_ | 1 | 1303 | 199,300 |
| M | 1 | 774 | 126,209 |