# Customer Deep Dives — Quickstart

End-to-end workflow for any of the three variants. Total time: ~3-5 minutes including QB pull.

## Prerequisites

- `pptxgenjs` installed (`node -e "require('pptxgenjs')"` should not error)
- CData Connect AI MCP available with Quickbase connection (`getCatalogs` returns `Quickbase1`)
- Realm not throttled (smoke test below if uncertain)

## Step 0 — Smoke test (only if realm health is uncertain)

```javascript
// Single tiny query — should return in < 2 seconds
queryData("SELECT TOP 1 [Master_Brand] FROM [Quickbase1].[ProductTrack].[Master_Brands]")
```

If this hangs or returns "MCP server connection lost", the realm is throttled. STOP. Wait 15 minutes. Inform the user.

## Step 1 — Pull data (2 CData calls)

**Required first call:**
```
getInstructions("QuickBase")
```

**Then fetch the salesperson lookup:**
```sql
-- See scripts/fetch_accounts_map.sql
SELECT [Customer_Name], [Div], [Salesperson], [Slsprsn_Code]
FROM [Quickbase1].[ProductTrack].[Accounts]
WHERE [Salesperson] IS NOT NULL
  AND [Salesperson] <> 'House Account'
  AND [Salesperson] <> 'UNASSIGNED'
```

Save result rows + build code→name map → `/home/claude/code_name_map.json`

```python
# Skeleton
import json
mapping = []  # list of {customer, div, salesperson, code}
for r in rows:
    mapping.append({"customer": r[0], "div": r[1], "salesperson": r[2], "code": r[3]})

# Build code → name dict (most common name per code)
from collections import defaultdict, Counter
code_to_name = defaultdict(Counter)
for m in mapping:
    if m['code']:
        code_to_name[m['code']][m['salesperson']] += 1
code_name_map = {code: cs.most_common(1)[0][0] for code, cs in code_to_name.items()}
code_name_map.update({'FC000': 'House Account', 'BB000': 'House Account', 'FC': 'UNASSIGNED'})
with open('/home/claude/code_name_map.json', 'w') as f:
    json.dump(code_name_map, f, indent=2)
```

**Then fetch Sales_Budgets:**
```sql
-- See scripts/fetch_sales_budgets.sql
SELECT [Customer_Name], [Master_Brand], [Div], [Salesperson_lookup_],
       [Total_LY_], [Tot_CY_Budget_], [Total_Actual_YTD_], [Total_Open_]
FROM [Quickbase1].[ProductTrack].[Sales_Budgets]
WHERE [YYYY_numeric_] = 2026 AND [Active_BV_2] = 1
```

Save raw records → `/home/claude/sb_raw.json`

```python
records = [{"customer": r[0], "brand": r[1], "div": r[2], "salesperson": r[3],
            "fy25": r[4] or 0, "bdgt": r[5] or 0, "ytd": r[6] or 0, "oo": r[7] or 0}
           for r in rows]
with open('/home/claude/sb_raw.json', 'w') as f:
    json.dump(records, f)
```

## Step 2 — Aggregate (no CData calls)

Pick ONE based on the variant requested:

```bash
# Combined deck
python3 /home/claude/skills/customer-deep-dives/scripts/aggregate_combined.py

# Side-Split decks
python3 /home/claude/skills/customer-deep-dives/scripts/aggregate_side_split.py

# Per-Salesperson decks
python3 /home/claude/skills/customer-deep-dives/scripts/aggregate_per_salesperson.py
```

Each prints reconciliation totals — verify they match the expected source totals (~$142M FY25, ~$154M Bdgt) before continuing.

## Step 3 — Build PPT (no CData calls)

```bash
# Combined deck (single output)
node /home/claude/skills/customer-deep-dives/scripts/build_combined_deck.js
# → /mnt/user-data/outputs/PP_Combined_Deck.pptx

# Side-Split decks (two outputs)
node /home/claude/skills/customer-deep-dives/scripts/build_side_deck.js fetch
node /home/claude/skills/customer-deep-dives/scripts/build_side_deck.js brandbuzz
# → /mnt/user-data/outputs/PP_Fetch_Deck.pptx and PP_BrandBuzz_Deck.pptx

# Per-Salesperson decks (one per qualifying salesperson)
for f in /home/claude/sp_decks/*.json; do
  node /home/claude/skills/customer-deep-dives/scripts/build_salesperson_deck.js "$(basename $f)"
done
# → /mnt/user-data/outputs/PP_<Name>_Deck.pptx for each
```

## Step 4 — Present

Use `present_files` with the full output paths. For per-salesperson, present them in portfolio-size order (largest first).

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Sales_Budgets pull returns 0 rows | Wrong BV filter | Verify `[Active_BV_2] = 1` is in WHERE |
| Per-SP deck has wrong customers | Stale code_name_map | Re-pull Accounts query first |
| "Invalid column name 'Product_Category'" | You're trying old query | Sales_Budgets has no category — drop the column |
| "MCP server connection lost" twice | Realm throttled | STOP for 15 min, do not retry |
| Reconciliation off by big amount | Stale sb_raw.json | Re-pull from QB |
| Salesperson missing from output | Below $750K threshold or House/Unassigned | Expected — check raw_portfolio in aggregate output |
