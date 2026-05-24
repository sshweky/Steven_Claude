# VP-Q2 OOS-smoothing back-test &mdash; cust_WAL_MART_STORES

Compares the production forecaster output (no smoothing) vs the new 
VP-Q2 OOS-aware demand reconstruction (clean demand from Order_History).


- **Records compared:** 166

- **Aggregate 26-week demand:**
    - Baseline:    2,977,704 units
    - VP-Q2:       2,945,733 units
    - Delta:       -31,971 units (-1.1%)

- **Records with any change:** 105  (lifted: 60, lowered: 45)


## Model breakdown

| Model | Count | Base 26w | VP-Q2 26w | Delta% |
|---|---:|---:|---:|---:|
| Croston's | 66 | 340,138 | 269,449 | -20.8% |
| Seasonal Baseline | 62 | 2,627,100 | 2,666,046 | +1.5% |
| Sparse Intermittent | 14 | 2,196 | 2,478 | +12.8% |
| Inactive | 9 | 0 | 0 | +0.0% |
| New/Relaunch | 7 | 1,508 | 1,508 | +0.0% |
| Heuristic | 4 | 6,414 | 6,096 | -5.0% |
| Reactivating | 2 | 348 | 156 | -55.2% |
| OTB (zero) | 2 | 0 | 0 | +0.0% |

## Top 25 records by absolute change

| Key | Customer | Mstyle | Model | Old/wk | New/wk | Delta% |
|---|---|---|---|---:|---:|---:|
| 23011-FF27687 | WAL MART STORES | FF27687 | Croston's | 2 | 0 | -100.0% |
| 23011-FF29833 | WAL MART STORES | FF29833 | Croston's | 12 | 23 | +92.3% |
| 23011-FF19341 | WAL MART STORES | FF19341 | Croston's | 44 | 74 | +67.5% |
| 23011-BB0098PCS2 | WAL MART STORES | BB0098PCS2 | Sparse Intermitten | 4 | 1 | -66.7% |
| 23011-FF7217 | WAL MART STORES | FF7217 | Seasonal Baseline | 18 | 30 | +66.7% |
| 23011-FF12660 | WAL MART STORES | FF12660 | Croston's | 3378 | 1374 | -59.3% |
| 23011-BB0083 | WAL MART STORES | BB0083 | Reactivating | 13 | 6 | -55.2% |
| 23011-FF7228 | WAL MART STORES | FF7228 | Croston's | 15 | 24 | +54.5% |
| 23011-FF7120 | WAL MART STORES | FF7120 | Croston's | 567 | 873 | +53.9% |
| 23011-FF30114 | WAL MART STORES | FF30114 | Croston's | 12 | 6 | -50.0% |
| 23011-FF4935 | WAL MART STORES | FF4935 | Croston's | 24 | 12 | -50.0% |
| 23011-FF8991 | WAL MART STORES | FF8991 | Sparse Intermitten | 0 | 1 | +50.0% |
| 23011-FF31287 | WAL MART STORES | FF31287 | Croston's | 8 | 12 | +46.2% |
| 23011-FF7216 | WAL MART STORES | FF7216 | Croston's | 12 | 18 | +46.2% |
| 23011-BB0100PCS2 | WAL MART STORES | BB0100PCS2 | Sparse Intermitten | 2 | 3 | +40.0% |
| 23011-FF31607 | WAL MART STORES | FF31607 | Sparse Intermitten | 8 | 12 | +38.9% |
| 23011-FF31606 | WAL MART STORES | FF31606 | Sparse Intermitten | 12 | 17 | +38.5% |
| 23011-FF32824 | WAL MART STORES | FF32824 | Croston's | 12 | 7 | -38.5% |
| 23011-FF8258 | WAL MART STORES | FF8258 | Sparse Intermitten | 6 | 8 | +37.5% |
| 23011-FF8423 | WAL MART STORES | FF8423 | Seasonal Baseline | 504 | 688 | +36.5% |
| 23011-FF31605 | WAL MART STORES | FF31605 | Sparse Intermitten | 6 | 9 | +35.7% |
| 23011-FF16705 | WAL MART STORES | FF16705 | Croston's | 36 | 24 | -33.3% |
| 23011-FF7210 | WAL MART STORES | FF7210 | Croston's | 18 | 24 | +33.3% |
| 23011-FF7263 | WAL MART STORES | FF7263 | Croston's | 9 | 6 | -31.6% |
| 23011-FF6554 | WAL MART STORES | FF6554 | Croston's | 3 | 2 | -30.8% |