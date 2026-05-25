---
name: Quickbase API Connection
description: Credentials, headers, endpoints, and table indexes for Quickbase apps at pim.quickbase.com (Amazon AdTrack + InventoryTrack)
type: reference
originSessionId: 7fd250e7-4824-4617-8d21-edaa141aab96
---
## Connection Details

- **Realm**: pim.quickbase.com
- **User Token**: b39re4_mkf7_du2buby24kr7d4hkcu9cpxn69s

## Apps

| Name | App ID |
|------|--------|
| Amazon AdTrack | bqkdiemav |
| InventoryTrack | bpd24h9wy |
| ProductTrack   | bn458t5nz |
- **User Token**: b39re4_mkf7_du2buby24kr7d4hkcu9cpxn69s

## Standard Headers
```
QB-Realm-Hostname: pim.quickbase.com
Authorization: QB-USER-TOKEN b39re4_mkf7_du2buby24kr7d4hkcu9cpxn69s
Content-Type: application/json
```

## API Base URL
`https://api.quickbase.com/v1`

## Common Endpoints
- **Get all fields:** `GET /fields?tableId={tableId}`
- **Get field definition:** `GET /fields/{fieldId}?tableId={tableId}`
- **Update field formula:** `POST /fields/{fieldId}?tableId={tableId}` — body: `{"properties": {"formula": "..."}}`
- **Query records:** `POST /records/query`

## Formula Syntax Gotcha
`var bool` variables CANNOT be used as conditions in `If()` expressions that return currency or percent field values.
- WRONG: `var bool use14d = ...; If($use14d, [cpc14d], [cpc60d])`
- RIGHT: `If([clicks14d]>=8, [cpc14d], [cpc60d])`

## Amazon AdTrack Tables (bqkdiemav)

| ID | Name | ~Records |
|----|------|----------|
| bqkdjaqi7 | Amazon Catalog | 27,638 |
| bqst6ab3b | ASIN Keyword Master | 84.9M |
| bqyb8bwif | ASIN_TargetASIN Master | 17.8M |
| bqm2giz86 | Manual Campaign Bid Recommendations | 170.5M |
| bqw8g486c | Amazon Top Keywords | 266.5M |
| bqkdkj4pw4 | Keyword Performance | 40.8M |
| bqkdjgexf | Campaign Performance | 31.2M |
| bqm3n26sh | AdGroup Parameters | 12M |
| bqkdjpm5z | AdGroup Performance | 12.9M |
| bqkdkgnph | ProductAd Performance | 14M |
| bqwbdwurt | Keyword Search Terms Performance | 10.9M |
| bqkdkremr | ProductTarget Performance | 3.7M |
| bqt284wds | ProductTarget Search Terms Performance | 12.6M |
| bqm3nz2wv | Keyword Parameters | 112M |
| bqm3n69sp | ProductAd Parameters | 100.6M |
| bqm3n77j8 | Product Target Parameters | 18.2M |
| brgxdpadi | Daily Metrics | 10.7M |
| brhmtrisj | Campaign Suggested Budgets | 106K |
| bqnujkgxb | Amazon US Daily Sales Diagnostic | 6.2M |
| bqzpwr357 | Amazon US Daily Inventory Health | 5.6M |
| bqzpc4z2a | Bidding Profile Assignments | 6,726 |
| bp83954uq | Amazon Bidding Profiles | 25 |
| bqn6k7suj | Dates | 2,192 |
| bqtaj45f7 | Amazon Advertising IDs | 14 |
| bq5qwwrqh | Advertisers | 2 |
| breus3wdk | Master Brands | 148 |
| brjdizght | Divisions | 2 |
| brd2ydqmq | Amazon Product Data | 6,166 |
| btj4trcxv | Amazon Product Metadata | 7,662 |
| brea9jxbm | Amazon Bestsellers | 51.6M |
| bqnukwew8 | Amazon US Weekly Inventory Health | 727K |
| bqnujzfw6 | Amazon US Weekly Forecast and Inventory Planning | 136K |
| bqziubydf | Amazon Traffic Data | 17,797 |
| brhfkxsiy | Daily Listing Updates | 27,739 |
| brhmtrisj | Campaign Suggested Budgets | 106K |
| brivwyehm | Brand Category Subcategory Keyword Master | 3.5M |
| bq8323jir | Category Subcategory Keyword Master | 2.4M |
| btgjimgzi | ASIN Keyword Organic Ranks | 93M |
| bsiwazwkr | Amazon Sponsored Products ASINs | 3.6M |
| bsiv4u7dc | Amazon Also Viewed | 363K |
| bsiv6kpjg | Amazon Also Bought | 9.4M |
| bsiwcuybd | Amazon Frequently Bought Together | 6.6M |
| bqxk8ffkt | User Focus | 3 |
| brd77i9ki | User Focus Selected ASINs | 10,970 |
| bqxw4h8f7 | Negative ProductTarget Parameters | 989K |
| brdnbn48f | Excluded Keywords | 542 |
| brd3bpm6b | ASIN Description | 394K |
| brd3ftcna | MStyles | 31K |
| bu5359n56 | Root Mstyle | 18K |
| bu6xrn4bu | Root Keywords | 73.3M |
| brj55qnwg | Keyword Texts | 79.6M |
| bts4b5x7r | Keyword CampaignIds | 61.8M |
| bts4cwx89 | Keyword ASINs | 67.4M |
| bts63dfa4 | Keyword Divs | 61.8M |
| bt5tpxixm | Created ASIN_Keywords | 34.1M |
| bt52rtzkn | Keyword MatchTypes | 55.7M |
| buns5n8v9 | Keyword Dates | 25.2M |
| bq96p979n | Market Basket | 303K |
| bqw2n5tzg | Campaign Placements Performance | 1.4M |
| bqx6rszut | Create Manual ProductTargets | 6.3M |
| bqx6s554j | Create Manual Keyword Targets | 19.1M |
| bqzp5n9ft | Amazon US Weekly Amazon Search Terms | 125K |
| bqyxdz3pe | ASIN Item Comparisons | 9,845 |
| bqn7t6khh | Campaign Creation | 11,445 |
| brd3gfxgb | Amazon Keyword & Target Creation | 24 |
| bsqnc6grv | SB Store Assets | 18K |
| bubhhstrq | Custom Image Assets | 5,111 |
| bubhhtgpf | Brand Logo Assets | 68 |
| bubhht4pg | Video Assets | 203 |
| bub8zha8h | ASIN Ad Approval Status | 2.9M |
| brrh24v6k | SB Campaign Parameters | 95K |
| brrh24x65 | SB Keyword Parameters | 9.6M |
| brrh244ki | SB Video Campaign Parameters | 152 |
| brrh246uh | SB Video Keyword Parameters | 66K |
| brrh242jx | SB Target Parameters | 8,786 |
| brrh248q5 | SB Video Target Parameters | 618 |
| brrh24wym | SB Ad Group Parameters | 209K |
| brrh24y7s | SB Negative Keyword Parameters | 12K |
| brrh243hb | SB Negative Target Parameters | 2 |
| brrh245qf | SB Video Ad Group Parameters | 157 |
| brrh247ss | SB Video Negative Keyword Parameters | 152 |
| brrh249pm | SB Video Negative Target Parameters | 3 |
| bri6mvabq | SB Campaign Performance | 12,580 |
| bri6m2g4b | SB Ad Group Performance | 122K |
| bri6m2hzs | SB Keyword Performance | 289K |
| bri6m2i7j | SB Target Performance | 1,164 |
| bri6m2ked | SB Campaign Placement Performance | 16K |
| bri6m2mjn | SB Keyword Search Term Performance | 28K |
| bri6m2np4 | SB Video Campaign Performance | 201 |
| bri6m2ps7 | SB Video Ad Group Performance | 142 |
| bri6m2q26 | SB Video Keyword Performance | 25K |
| bri6m2sc4 | SB Video Target Performance | 203 |
| bri6m2tdw | SB Video Campaign Placement Performance | 120 |
| bri6m2uha | SB Video Keyword Search Term Performance | 0 |
| brrn8zbra | SB Keyword Bid Recommendations | 4.1M |
| brrn8zc26 | SB Video Keyword Bid Recommendations | 81K |
| brrn8zfet | SB Target Bid Recommendations | 4,218 |
| brrn8zgyr | SB Video Target Bid Recommendations | 618 |
| brhfkxsiy | Daily Listing Updates | 27K |
| brhhbufhq | Metric Trends | 2.3M |
| bri98npvq | Suggested Keywords | 10.3M |
| brpnx68mt | CampaignNegativeKeyword Parameters | 63K |
| brx58mp9b | Daily Budgets | 0 |
| bsrk9x4bb | Daily Budget Caps | 9 |
| btdvac26n | Daily Brand Budget Caps | 226 |
| bq3qhvwmy | My Menus | 10 |
| brtjbtqpn | SB Campaign Creation Brand_Cat_Sub | 883 |
| brtjb2a5m | SB Campaign Creation Brand_Cat | 463 |
| brg6n6mgp | Process Finish Times | 10,222 |
| bui36afwx | Advertising Stats | 1 |

## InventoryTrack Tables (bpd24h9wy)

| ID | Name |
|----|------|
| bpsaju5pm | Inventory Flow |
| bpd237tvm | Projections |
| bphzqfkev | Styles |
| bpe4maa4c | Order History |
| btjf84re5 | Inventory Requests |
| bptvjvdsa | Acct-MStyle Master |
| bphzp9qmg | Inventory |
| bphzp7wjg | Supplier POs |
| bpu3x8i9j | Invoice Detail |
| bqsb5jjzv | Allocation Admin |
| bqsbgzrxi | Customer Order Allocations |
| bpgbx2f5k | Inv Mgmt Admin |
| bqns9mpn2 | Warehouse Receipts |
| bpt35zccg | Projection Comments |
| bqp8vz625 | Amazon Catalog |
| bptc26w7j | Acct# - MStyle Snooze |
| bqp8ve5i3 | Amazon Bulk Buys |
| bphc3vs5h | Orders and Shipments |
| bptt4t9ef | Customers |
| brynsaud5 | Petsmart Sales |
| bv2izcn5b | Retailer Sales |
| bpx7i23si | Retailer Forecasts |
| bptcjvybr | Retailer Promotions |
| bpvkc7cje | Allowed Accounts for Exclusive Mstyles |
| bp4rqduqp | Sales_Amazon |
| bpx7iqmb4 | Forecast_Amazon |
| btjf9wtis | Inventory Request Detail |
| bpvanvbv3 | Item Status Codes |
| bp9yhxpcz | Customer Store List |
| bqbkk86tf | AS400 Templates |
| bqsb6bw9g | Allocation Batch IDs |
| bps2cz3bq | Admin Functions #2 |
| bps2drz5s | Admin Functions #3 |
| bps2qur9h | Admin Functions #4 |
| bps2qwdzw | Admin Functions #5 |
| bps2q3wsi | Admin Functions #6 |
| bpz86f2e3 | Admin Functions #7 |
| bpz86ha7f | Admin Functions #8 |
| bpz86iiiw | Admin Functions #9 |
| bptqrumh9 | Customer Master (sync) |
| bpt358sha | Weeks |
| bpuxbhcg5 | Admin Scripts |
| bpw5nvmss | Accounts (mirror) |
| bpxax2rd4 | User Focus |
| bpxfst4pi | 52 Weeks |
| bp9w97mmy | AS400 Order Entry |
| bqvpa56h9 | Whse Messages |
| bqv9jggb7 | Sup. PO Cust Ord Alloc. |
| bqv94qn9n | Allocated from In Transit |
| bqwwaqxhr | Supplier PO Customer Allocations |
| bqww23885 | AS/400 In Transit Allocations Import File |
| bqymdrgvy | Amazon Traffic Diagnostics |
| brccu98ec | Temp Unique Order History |
| bru353nxm | Brand Category Subcategory |
| br3afzd2h | Metric Trends (sync) |
| br5fajrrx | MStyle Mirror Pipelines Test |
| br6dcnv35 | Inventory History |
| bv2sxg2ji | Inventory History - Weekly |
| bsa9dpz87 | Current Stock Errors |
| bsa9dz864 | PO Data Errors |
| bsa9d3gp9 | Suggested PO ETAs |
| bsbithesf | PO ETA Changes |
| bsrbz9vys | Order OOS Alerts |
| bs387f5dv | MStyle Projection Changes |
| bs387gbph | Account MStyle Projection Changes |
| btg5fcjy8 | OOS Lost Sales |
| btud5bggf | Shipment Detail |
| bujymir8r | Proj Accuracy By Brand |
| bujymi7ne | Proj Accuracy By Customer |
| bujymjkj3 | Proj Accuracy By MStyle |
| bujymqhhi | Proj Accuracy By Acct-MStyle |
| bupdfpeuy | FOB Points |
| bux2qehhv | Proj Accuracy By Segment |
| bvb9mi9vi | Inventory Age |
