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
| 1 | `VP-Q4` | CRITICAL | 1603 | 28.8% | 100.0% | -9,348,192 | 0 | Seasonal Baseline |
| 2 | `F64` | CRITICAL | 1677 | 30.1% | 100.0% | -9,261,670 | 0 | Sparse Intermittent |
| 3 | `VP-Q1` | CRITICAL | 844 | 15.1% | 100.0% | -8,193,234 | 0 | Seasonal Baseline |
| 4 | `F16` | CRITICAL | 612 | 11.0% | 100.0% | -8,061,617 | 0 | Seasonal Baseline |
| 5 | `F37` | CRITICAL | 829 | 14.9% | 100.0% | -6,300,941 | 0 | Seasonal Baseline |
| 6 | `F10` | CRITICAL | 964 | 17.3% | 100.0% | -4,682,597 | 0 | Sparse Intermittent |
| 7 | `F59g` | HIGH | 198 | 3.6% | 100.0% | -4,271,953 | 0 | Croston's |
| 8 | `M1` | CRITICAL | 840 | 15.1% | 100.0% | -3,556,432 | 0 | Sparse Intermittent |
| 9 | `F34` | CRITICAL | 809 | 14.5% | 100.0% | -3,267,698 | 0 | Sparse Intermittent |
| 10 | `F15` | CRITICAL | 353 | 6.3% | 100.0% | -3,256,302 | 0 | Seasonal Baseline |
| 11 | `F_PO_CUTOFF` | CRITICAL | 1433 | 25.7% | 100.0% | -3,048,723 | 0 | Inactive |
| 12 | `F43` | CRITICAL | 389 | 7.0% | 100.0% | -3,037,042 | 0 | Seasonal Baseline |
| 13 | `F41` | CRITICAL | 307 | 5.5% | 100.0% | -2,971,779 | 0 | Croston's |
| 14 | `F66` | CRITICAL | 463 | 8.3% | 100.0% | -2,900,541 | 0 | Seasonal Baseline |
| 15 | `F35` | CRITICAL | 557 | 10.0% | 100.0% | -2,797,678 | 0 | Seasonal Baseline |
| 16 | `VP-Q3` | CRITICAL | 843 | 15.1% | 100.0% | -2,673,176 | 0 | Croston's |
| 17 | `F59a` | CRITICAL | 283 | 5.1% | 100.0% | -2,650,138 | 0 | Seasonal Baseline |
| 18 | `F71` | CRITICAL | 609 | 10.9% | 100.0% | -2,600,131 | 0 | Croston's |
| 19 | `F47` | CRITICAL | 389 | 7.0% | 100.0% | -2,502,678 | 0 | Seasonal Baseline |
| 20 | `F38b` | HIGH | 237 | 4.3% | 100.0% | -2,482,743 | 0 | Seasonal Baseline |
| 21 | `F59d` | CRITICAL | 311 | 5.6% | 100.0% | -2,143,843 | 0 | Croston's |
| 22 | `F62` | CRITICAL | 299 | 5.4% | 100.0% | -2,124,859 | 0 | Seasonal Baseline |
| 23 | `F45` | HIGH | 226 | 4.1% | 100.0% | -2,117,337 | 0 | Seasonal Baseline |
| 24 | `F59i` | HIGH | 119 | 2.1% | 100.0% | -2,111,606 | 0 | Seasonal Baseline |
| 25 | `F6b` | HIGH | 220 | 3.9% | 100.0% | -1,835,668 | 0 | Seasonal Baseline |
| 26 | `F59j` | HIGH | 133 | 2.4% | 100.0% | -1,806,655 | 0 | Seasonal Baseline |
| 27 | `F38` | HIGH | 137 | 2.5% | 100.0% | -1,777,706 | 0 | Seasonal Baseline |
| 28 | `F59h` | CRITICAL | 564 | 10.1% | 100.0% | -1,588,452 | 0 | Croston's |
| 29 | `F18` | HIGH | 141 | 2.5% | 100.0% | -1,525,848 | 0 | Croston's |
| 30 | `F14a` | HIGH | 174 | 3.1% | 100.0% | -1,482,766 | 0 | Croston's |
| 31 | `F26` | HIGH | 184 | 3.3% | 100.0% | -1,441,665 | 0 | Seasonal Baseline |
| 32 | `F39` | HIGH | 138 | 2.5% | 100.0% | -1,408,420 | 0 | Croston's |
| 33 | `R2` | CRITICAL | 606 | 10.9% | 100.0% | -1,384,478 | 0 | Sparse Intermittent |
| 34 | `F59m` | HIGH | 72 | 1.3% | 100.0% | -1,083,203 | 0 | Seasonal Baseline |
| 35 | `F57` | HIGH | 241 | 4.3% | 100.0% | -1,039,766 | 0 | Croston's |
| 36 | `F59o` | HIGH | 239 | 4.3% | 100.0% | -999,754 | 0 | Croston's |
| 37 | `F65` | CRITICAL | 1096 | 19.7% | 100.0% | -957,846 | 0 | Inactive |
| 38 | `F61` | CRITICAL | 443 | 8.0% | 100.0% | -887,195 | 0 | Sparse Intermittent |
| 39 | `VP-FL` | HIGH | 41 | 0.7% | 100.0% | -886,297 | 0 | Seasonal Baseline |
| 40 | `F59e` | CRITICAL | 282 | 5.1% | 100.0% | -880,553 | 0 | Croston's |

## By rule family

| Family | Rules | Total fires | Total |unit gap| |
|---|---:|---:|---:|
| F | 73 | 18112 | 98,896,761 |
| VP | 8 | 3639 | 22,278,115 |
| M | 1 | 840 | 3,556,432 |
| F_ | 1 | 1433 | 3,048,723 |
| R | 4 | 1203 | 2,638,472 |