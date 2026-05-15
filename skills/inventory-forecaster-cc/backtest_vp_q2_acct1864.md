# VP-Q2 OOS-smoothing back-test &mdash; acct1864

Compares the production forecaster output (no smoothing) vs the new 
VP-Q2 OOS-aware demand reconstruction (clean demand from Order_History).


- **Records compared:** 1511

- **Aggregate 26-week demand:**
    - Baseline:    5,303,193 units
    - VP-Q2:       5,383,392 units
    - Delta:       +80,199 units (+1.5%)

- **Records with any change:** 293  (lifted: 293, lowered: 0)


## Model breakdown

| Model | Count | Base 26w | VP-Q2 26w | Delta% |
|---|---:|---:|---:|---:|
| Croston's | 442 | 1,726,609 | 1,751,959 | +1.5% |
| Seasonal Baseline | 312 | 3,080,118 | 3,128,496 | +1.6% |
| Sparse Intermittent | 278 | 262,395 | 268,776 | +2.4% |
| Inactive | 178 | 0 | 0 | +0.0% |
| New/Relaunch | 155 | 23,028 | 23,028 | +0.0% |
| Heuristic | 89 | 206,125 | 206,215 | +0.0% |
| OTB (zero) | 46 | 0 | 0 | +0.0% |
| Reactivating | 9 | 4,216 | 4,216 | +0.0% |
| Inactive+Floor (R3) | 2 | 702 | 702 | +0.0% |

## Top 25 records by absolute change

| Key | Customer | Mstyle | Model | Old/wk | New/wk | Delta% |
|---|---|---|---|---:|---:|---:|
| 1864-BB0235 | AMAZON.COM.KYDC,INC | BB0235 | Sparse Intermitten | 30 | 61 | +100.0% |
| 1864-BB35514 | AMAZON.COM.KYDC,INC | BB35514 | Sparse Intermitten | 1 | 1 | +100.0% |
| 1864-BB14500CLR/9 | AMAZON.COM.KYDC,INC | BB14500CLR/9 | Sparse Intermitten | 162 | 243 | +50.0% |
| 1864-BB15258 | AMAZON.COM.KYDC,INC | BB15258 | Croston's | 105 | 157 | +50.0% |
| 1864-BB31552CLR/9 | AMAZON.COM.KYDC,INC | BB31552CLR/9 | Sparse Intermitten | 166 | 248 | +50.0% |
| 1864-FF12653 | AMAZON.COM.KYDC,INC | FF12653 | Sparse Intermitten | 53 | 80 | +50.0% |
| 1864-FF26536 | AMAZON.COM.KYDC,INC | FF26536 | Sparse Intermitten | 3 | 4 | +33.3% |
| 1864-FF20206PCS6 | AMAZON.COM.KYDC,INC | FF20206PCS6 | Croston's | 1 | 1 | +27.6% |
| 1864-BB26737PCS10 | AMAZON.COM.KYDC,INC | BB26737PCS10 | Croston's | 0 | 1 | +23.1% |
| 1864-BB35041 | AMAZON.COM.KYDC,INC | BB35041 | Seasonal Baseline | 47 | 56 | +19.8% |
| 1864-BB11417FL/12 | AMAZON.COM.KYDC,INC | BB11417FL/12 | Croston's | 6 | 6 | +16.7% |
| 1864-FF31186 | AMAZON.COM.KYDC,INC | FF31186 | Croston's | 19 | 22 | +16.7% |
| 1864-BB15069 | AMAZON.COM.KYDC,INC | BB15069 | Croston's | 12 | 14 | +15.4% |
| 1864-BB26613PCS2 | AMAZON.COM.KYDC,INC | BB26613PCS2 | Seasonal Baseline | 12 | 14 | +15.4% |
| 1864-BB0018AMZ6 | AMAZON.COM.KYDC,INC | BB0018AMZ6 | Croston's | 1 | 1 | +14.8% |
| 1864-BB12019 | AMAZON.COM.KYDC,INC | BB12019 | Croston's | 12 | 14 | +14.8% |
| 1864-FF21789 | AMAZON.COM.KYDC,INC | FF21789 | Croston's | 13 | 15 | +14.3% |
| 1864-FF28400 | AMAZON.COM.KYDC,INC | FF28400 | Croston's | 6 | 7 | +14.3% |
| 1864-BB35096 | AMAZON.COM.KYDC,INC | BB35096 | Croston's | 718 | 814 | +13.3% |
| 1864-FF20210PCS6 | AMAZON.COM.KYDC,INC | FF20210PCS6 | Croston's | 1 | 1 | +13.3% |
| 1864-BB26584 | AMAZON.COM.KYDC,INC | BB26584 | Seasonal Baseline | 51 | 58 | +13.2% |
| 1864-BB0092PCS2 | AMAZON.COM.KYDC,INC | BB0092PCS2 | Seasonal Baseline | 56 | 64 | +13.1% |
| 1864-FF33637 | AMAZON.COM.KYDC,INC | FF33637 | Croston's | 5 | 6 | +13.0% |
| 1864-BB16854PCS2 | AMAZON.COM.KYDC,INC | BB16854PCS2 | Seasonal Baseline | 32 | 36 | +12.9% |
| 1864-BB33526 | AMAZON.COM.KYDC,INC | BB33526 | Seasonal Baseline | 215 | 242 | +12.8% |