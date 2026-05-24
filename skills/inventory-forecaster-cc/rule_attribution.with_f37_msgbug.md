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
| 1 | `VP-Q4` | CRITICAL | 1661 | 29.8% | 40.6% | -1,053,552 | 9,457,068 | Seasonal Baseline |
| 2 | `F64` | CRITICAL | 1658 | 29.8% | 35.9% | -806,559 | 9,211,499 | Croston's |
| 3 | `F65` | CRITICAL | 963 | 17.3% | 100.0% | -634,543 | 348,793 | Inactive |
| 4 | `F37` | CRITICAL | 2288 | 41.1% | 40.0% | -560,043 | 12,965,193 | Croston's |
| 5 | `F59h` | CRITICAL | 558 | 10.0% | 76.0% | -493,748 | 1,166,474 | Croston's |
| 6 | `F10` | CRITICAL | 900 | 16.2% | 54.8% | -410,720 | 4,496,897 | Sparse Intermittent |
| 7 | `F59j` | HIGH | 130 | 2.3% | 23.0% | +361,713 | 2,225,098 | Seasonal Baseline |
| 8 | `F45` | HIGH | 198 | 3.6% | 36.4% | +328,247 | 1,889,943 | Seasonal Baseline |
| 9 | `F43` | CRITICAL | 374 | 6.7% | 35.6% | +325,810 | 3,199,641 | Seasonal Baseline |
| 10 | `VP-Q3` | CRITICAL | 812 | 14.6% | 41.1% | -312,603 | 2,293,175 | Croston's |
| 11 | `F52` | CRITICAL | 550 | 9.9% | 76.0% | +304,948 | 621,464 | Inactive (zeroed by guards) |
| 12 | `F59d` | HIGH | 247 | 4.4% | 43.4% | +288,752 | 1,611,440 | Croston's |
| 13 | `F59f` | HIGH | 161 | 2.9% | 66.4% | -287,713 | 900,513 | Croston's |
| 14 | `F26` | HIGH | 176 | 3.2% | 27.4% | -269,342 | 1,491,144 | Seasonal Baseline |
| 15 | `VP-Q2` | HIGH | 102 | 1.8% | 61.4% | +268,967 | 563,114 | Seasonal Baseline |
| 16 | `R2` | CRITICAL | 551 | 9.9% | 47.7% | -255,796 | 1,278,419 | Sparse Intermittent |
| 17 | `F36` | CRITICAL | 605 | 10.9% | 100.0% | -231,474 | 275,087 | Inactive (zeroed by guards) |
| 18 | `F49` | HIGH | 64 | 1.1% | 40.9% | +223,059 | 883,671 | Seasonal Baseline |
| 19 | `F59i` | HIGH | 110 | 2.0% | 27.1% | -213,961 | 1,481,674 | Seasonal Baseline |
| 20 | `F14a` | HIGH | 180 | 3.2% | 40.4% | -210,321 | 1,543,211 | Croston's |
| 21 | `VP-OP` | HIGH | 130 | 2.3% | 39.4% | -206,626 | 412,556 | Sparse Intermittent |
| 22 | `F59a` | HIGH | 242 | 4.3% | 38.0% | +205,818 | 2,498,820 | Seasonal Baseline |
| 23 | `VP-ATS` | HIGH | 55 | 1.0% | 53.8% | +201,037 | 277,222 | Seasonal Baseline |
| 24 | `F_PO_CUTOFF` | CRITICAL | 1303 | 23.4% | 94.7% | +199,346 | 3,162,032 | Inactive |
| 25 | `F34` | CRITICAL | 736 | 13.2% | 57.8% | -187,873 | 3,015,018 | Sparse Intermittent |
| 26 | `F35` | CRITICAL | 553 | 9.9% | 40.0% | -184,848 | 2,686,688 | Croston's |
| 27 | `F72` | HIGH | 8 | 0.1% | 69.9% | -182,991 | 78,846 | Heuristic (F72 new-launch ramp) |
| 28 | `F59g` | HIGH | 183 | 3.3% | 30.1% | +173,264 | 4,429,814 | Seasonal Baseline |
| 29 | `F44` | HIGH | 47 | 0.8% | 41.2% | +170,898 | 353,324 | Seasonal Baseline |
| 30 | `F46` | HIGH | 38 | 0.7% | 41.2% | +165,271 | 307,568 | Seasonal Baseline |
| 31 | `F23b` | CRITICAL | 382 | 6.9% | 97.6% | -163,272 | 287,662 | Heuristic |
| 32 | `F59e` | CRITICAL | 279 | 5.0% | 71.7% | +163,206 | 1,070,039 | Croston's |
| 33 | `F51` | HIGH | 10 | 0.2% | 14.5% | +158,604 | 696,184 | Seasonal Baseline |
| 34 | `F40` | HIGH | 36 | 0.6% | 40.0% | -153,119 | 432,199 | Croston's |
| 35 | `F6b` | HIGH | 197 | 3.5% | 42.4% | -149,546 | 1,309,070 | Seasonal Baseline |
| 36 | `F70` | CRITICAL | 475 | 8.5% | 100.0% | -141,963 | 48,426 | Inactive (zeroed by guards) |
| 37 | `F62` | CRITICAL | 288 | 5.2% | 27.5% | -134,793 | 2,404,101 | Seasonal Baseline |
| 38 | `F69` | HIGH | 36 | 0.6% | 21.6% | -130,225 | 531,681 | Croston's |
| 39 | `F69-wos` | HIGH | 36 | 0.6% | 21.6% | -130,225 | 531,681 | Croston's |
| 40 | `F59m` | HIGH | 78 | 1.4% | 20.2% | +127,471 | 1,325,216 | Seasonal Baseline |

## By rule family

| Family | Rules | Total fires | Total |unit gap| |
|---|---:|---:|---:|
| F | 76 | 18707 | 10,656,054 |
| VP | 8 | 3649 | 2,072,477 |
| R | 5 | 1176 | 438,395 |
| F_ | 1 | 1303 | 199,346 |
| M | 1 | 774 | 94,632 |