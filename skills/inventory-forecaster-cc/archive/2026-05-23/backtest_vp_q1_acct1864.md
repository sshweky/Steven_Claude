# VP-Q1 baseline back-test &mdash; acct1864

Compares AI_PRJ values currently in Quickbase (old logic) vs the new 
evidence-based baseline-mode logic in `seasonal_baseline()`.


- **Records compared:** 1511

- **Aggregate 26-week demand:**
    - Old: 4,543,640 units
    - New: 5,265,059 units
    - Delta: +721,419 units (+15.9%)

## Baseline-mode breakdown

| Mode | Count | Old 26w | New 26w | Delta% |
|---|---:|---:|---:|---:|
|  | 1199 | 1,794,328 | 2,216,857 | +23.5% |
| L13 nz-avg | 300 | 2,739,976 | 3,035,101 | +10.8% |
| L13 all-weeks avg | 10 | 732 | 807 | +10.2% |
| L26 nz-avg | 2 | 8,604 | 12,294 | +42.9% |

## Top 25 records by absolute change

| Key | Customer | Mstyle | Desc | Old/wk | New/wk | Delta% | Mode |
|---|---|---|---|---:|---:|---:|---|
| 1864-FF7115 | AMAZON.COM.KYDC,INC | FF7115 | BioSilk for Dogs Puppy Tearless  | 2 | 292 | +12560.0% |  |
| 1864-FF27997 | AMAZON.COM.KYDC,INC | FF27997 | GLAD Small Activated Carbon Trai | 4 | 183 | +4471.2% |  |
| 1864-FF28036 | AMAZON.COM.KYDC,INC | FF28036 | Wags & Wiggles Deterrent Trainin | 1 | 28 | +4000.0% |  |
| 1864-FF20210EC | AMAZON.COM.KYDC,INC | FF20210EC | Arm & Hammer Itch Relief Shampoo | 7 | 113 | +1474.2% |  |
| 1864-BB35101PCS6 | AMAZON.COM.KYDC,INC | BB35101PCS6 | Gladware - Design Series 24oz -  | 0 | 1 | +1300.0% |  |
| 1864-BB29714PCS6 | AMAZON.COM.KYDC,INC | BB29714PCS6 | Clorox Fraganzia - Dryer Sheets  | 0 | 1 | +1050.0% |  |
| 1864-FF33871 | AMAZON.COM.KYDC,INC | FF33871 | Arm & Hammer Complete Care Brush | 1 | 10 | +1000.0% |  |
| 1864-BB22025PCS2 | AMAZON.COM.KYDC,INC | BB22025PCS2 | Fabuloso -Sponges Purple 4 CT -  | 1 | 9 | +900.0% |  |
| 1864-FF14365PCS12 | AMAZON.COM.KYDC,INC | FF14365PCS12 | Wet Ones for Pets Hypoallergenic | 0 | 5 | +869.2% |  |
| 1864-BB0191PCS6 | AMAZON.COM.KYDC,INC | BB0191PCS6 | Clorox Fraganzia - Dryer Sheets  | 0 | 1 | +800.0% |  |
| 1864-BB36990PCS10 | AMAZON.COM.KYDC,INC | BB36990PCS10 | Glad for Kids - 6oz Paper Snack  | 0 | 1 | +800.0% |  |
| 1864-FF10162PCS6 | AMAZON.COM.KYDC,INC | FF10162PCS6 | Arm & Hammer Tearless Puppy Sham | 0 | 4 | +630.8% |  |
| 1864-FF30880PX6 | AMAZON.COM.KYDC,INC | FF30880PX6 | Dole Molded Chew; Pumpkin; 8oz b | 0 | 0 | +600.0% |  |
| 1864-BB19110PCS6 | AMAZON.COM.KYDC,INC | BB19110PCS6 | Clorox Fraganzia - Air Freshener | 1 | 5 | +557.1% |  |
| 1864-BB12010 | AMAZON.COM.KYDC,INC | BB12010 | Kingsford - Stainless Steel Smok | 20 | 108 | +451.8% |  |
| 1864-BB16180 | AMAZON.COM.KYDC,INC | BB16180 | Kingsford - Assorted Cutlery, 75 | 6 | 30 | +450.0% |  |
| 1864-FF33867 | AMAZON.COM.KYDC,INC | FF33867 | Arm & Hammer Complete Care Denta | 38 | 199 | +426.8% |  |
| 1864-BB21641PCS2 | AMAZON.COM.KYDC,INC | BB21641PCS2 | Clorox Fraganzia - Air Freshener | 8 | 42 | +422.9% |  |
| 1864-FF30879PX6 | AMAZON.COM.KYDC,INC | FF30879PX6 | Dole Molded Chew; Banana; 8oz ba | 0 | 2 | +420.0% |  |
| 1864-FF31183 | AMAZON.COM.KYDC,INC | FF31183 | Burts Bees Deodorizing Wipes wit | 6 | 32 | +407.4% |  |
| 1864-FF9297PCS2 | AMAZON.COM.KYDC,INC | FF9297PCS2 | A&H Complete Care Dog Dental Kit | 2 | 8 | +350.0% |  |
| 1864-FF28036PCS6 | AMAZON.COM.KYDC,INC | FF28036PCS6 | Wags & Wiggles Deterrent Trainin | 1 | 4 | +329.6% |  |
| 1864-FF33877 | AMAZON.COM.KYDC,INC | FF33877 | Arm & Hammer Cologne - Vanilla S | 66 | 267 | +302.1% |  |
| 1864-BB25519PCS6 | AMAZON.COM.KYDC,INC | BB25519PCS6 | Glad - Everyday Plastic Cups - 1 | 0 | 1 | +300.0% |  |
| 1864-FF28400 | AMAZON.COM.KYDC,INC | FF28400 | Disney The Lion King: Pumba & Bu | 2 | 7 | +300.0% |  |