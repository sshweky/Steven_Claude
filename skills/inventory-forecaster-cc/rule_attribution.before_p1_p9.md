# Rule Attribution Report

Source: `forecast_results.json`  ·  5571 records  ·  87 rules

**Interpretation:**
- `n` = records where this rule fired
- `median |delta|` = median |AI - manual| / manual on those records
- `unit gap` = sum of (AI - manual) on those records (signed)
- Tier: CRITICAL >= 100K units AND fires on >=5% of records

## Top rules by impact

| Rank | Rule | Tier | n | Fire % | Median Δ | Unit gap | AI total | Top model |
|------|------|------|---:|---:|---:|---:|---:|---|
| 1 | `VP-Q4` | CRITICAL | 1603 | 28.8% | 43.1% | -850,070 | 8,498,122 | Seasonal Baseline |
| 2 | `F64` | CRITICAL | 1677 | 30.1% | 38.2% | -669,975 | 8,591,695 | Sparse Intermittent |
| 3 | `F65` | CRITICAL | 1096 | 19.7% | 100.0% | -660,961 | 296,885 | Inactive |
| 4 | `F10` | CRITICAL | 964 | 17.3% | 55.1% | -541,996 | 4,140,601 | Sparse Intermittent |
| 5 | `F59j` | HIGH | 133 | 2.4% | 20.4% | +439,144 | 2,245,799 | Seasonal Baseline |
| 6 | `F59d` | CRITICAL | 311 | 5.6% | 40.2% | +434,196 | 2,578,039 | Croston's |
| 7 | `F34` | CRITICAL | 809 | 14.5% | 59.2% | -430,115 | 2,837,583 | Sparse Intermittent |
| 8 | `F37` | CRITICAL | 829 | 14.9% | 42.4% | -425,795 | 5,875,146 | Seasonal Baseline |
| 9 | `F59h` | CRITICAL | 564 | 10.1% | 73.3% | -415,651 | 1,172,801 | Croston's |
| 10 | `F52` | CRITICAL | 569 | 10.2% | 75.8% | +387,793 | 703,067 | Inactive (zeroed by guards) |
| 11 | `M1` | CRITICAL | 840 | 15.1% | 59.2% | -304,695 | 3,251,737 | Sparse Intermittent |
| 12 | `F35` | CRITICAL | 557 | 10.0% | 40.2% | -303,881 | 2,493,797 | Seasonal Baseline |
| 13 | `VP-OP` | HIGH | 153 | 2.7% | 52.4% | -303,135 | 390,847 | Sparse Intermittent |
| 14 | `F59a` | CRITICAL | 283 | 5.1% | 37.7% | +293,159 | 2,943,297 | Seasonal Baseline |
| 15 | `F59f` | HIGH | 147 | 2.6% | 66.7% | -289,016 | 487,425 | Croston's |
| 16 | `F71` | CRITICAL | 609 | 10.9% | 48.5% | -287,255 | 2,312,876 | Croston's |
| 17 | `F43` | CRITICAL | 389 | 7.0% | 37.6% | +279,959 | 3,317,001 | Seasonal Baseline |
| 18 | `F_PO_CUTOFF` | CRITICAL | 1433 | 25.7% | 87.0% | +277,974 | 3,326,697 | Inactive |
| 19 | `VP-Q2` | HIGH | 93 | 1.7% | 55.0% | +263,876 | 621,674 | Seasonal Baseline |
| 20 | `F49` | HIGH | 77 | 1.4% | 34.9% | +263,105 | 1,045,924 | Seasonal Baseline |
| 21 | `F36` | CRITICAL | 653 | 11.7% | 100.0% | -241,415 | 365,855 | Inactive (zeroed by guards) |
| 22 | `R2` | CRITICAL | 606 | 10.9% | 52.4% | -239,166 | 1,145,312 | Sparse Intermittent |
| 23 | `F45` | HIGH | 226 | 4.1% | 33.1% | -229,871 | 1,887,466 | Seasonal Baseline |
| 24 | `F59g` | HIGH | 198 | 3.6% | 28.4% | +220,588 | 4,492,541 | Croston's |
| 25 | `F61` | CRITICAL | 443 | 8.0% | 47.0% | -210,215 | 676,980 | Sparse Intermittent |
| 26 | `F59e` | CRITICAL | 282 | 5.1% | 75.0% | +204,281 | 1,084,834 | Croston's |
| 27 | `F59i` | HIGH | 119 | 2.1% | 20.0% | -204,151 | 1,907,455 | Seasonal Baseline |
| 28 | `F38b` | HIGH | 237 | 4.3% | 30.3% | +201,495 | 2,684,238 | Seasonal Baseline |
| 29 | `F26` | HIGH | 184 | 3.3% | 28.1% | -195,539 | 1,246,126 | Seasonal Baseline |
| 30 | `F15` | CRITICAL | 353 | 6.3% | 36.5% | +184,134 | 3,440,436 | Seasonal Baseline |
| 31 | `F51` | HIGH | 11 | 0.2% | 30.1% | +183,323 | 684,922 | Seasonal Baseline |
| 32 | `F6b` | HIGH | 220 | 3.9% | 37.9% | -179,422 | 1,656,246 | Seasonal Baseline |
| 33 | `VP-ATS` | HIGH | 54 | 1.0% | 69.6% | +176,642 | 252,925 | Seasonal Baseline |
| 34 | `F70` | CRITICAL | 503 | 9.0% | 100.0% | -173,201 | 42,858 | Inactive (zeroed by guards) |
| 35 | `VP-Q3` | CRITICAL | 843 | 15.1% | 43.6% | -170,983 | 2,502,193 | Croston's |
| 36 | `F39` | HIGH | 138 | 2.5% | 40.0% | -167,846 | 1,240,574 | Croston's |
| 37 | `F23b` | CRITICAL | 373 | 6.7% | 99.2% | -156,881 | 292,645 | Heuristic |
| 38 | `F38f` | CRITICAL | 570 | 10.2% | 100.0% | -152,799 | 170,066 | Inactive (zeroed by guards) |
| 39 | `F44` | HIGH | 48 | 0.9% | 27.1% | +150,392 | 394,113 | Seasonal Baseline |
| 40 | `F46` | HIGH | 42 | 0.8% | 27.1% | +147,116 | 346,262 | Seasonal Baseline |

## By rule family

| Family | Rules | Total fires | Total |unit gap| |
|---|---:|---:|---:|
| F | 73 | 18112 | 11,311,428 |
| VP | 8 | 3639 | 1,860,620 |
| R | 4 | 1203 | 366,292 |
| M | 1 | 840 | 304,695 |
| F_ | 1 | 1433 | 277,974 |