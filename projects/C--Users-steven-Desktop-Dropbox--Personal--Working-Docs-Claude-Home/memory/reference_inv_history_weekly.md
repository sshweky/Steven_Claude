---
name: Inventory History - Weekly Schema
description: Field IDs, relationship structure, and summary field pattern for the Inventory History - Weekly table (bv2sxg2ji)
type: reference
originSessionId: 7fd250e7-4824-4617-8d21-edaa141aab96
---
## Tables

| ID | Name |
|----|------|
| bv2sxg2ji | Inventory History - Weekly (parent) |
| br6dcnv35 | Inventory History (child) |

## Relationship

- **ID**: 35 (same as FK field ID)
- **Parent**: bv2sxg2ji (Weekly) — one row per Mstyle
- **Child**: br6dcnv35 (Inventory History) — many rows per Mstyle (one per date)
- **FK field in child**: fid 35 `Mstyle (mirror)` — plain text, auto-populated to match `[Mstyle]`
- **Parent key**: fid 6 `Mstyle` (primaryKey=true)

## Key Fields in Inventory History (br6dcnv35)

| FID | Label | Type | Notes |
|-----|-------|------|-------|
| 6 | Mstyle | text | key |
| 10 | ATS Qty OH# | numeric | the value being summarized |
| 11 | Date | date | snapshot date (weekly) |
| 35 | Mstyle (mirror) | text | FK — links to Weekly.Mstyle |
| 68 | Sunday | date formula | `FirstDayOfWeek(Today())` — this week's Sunday |
| 69 | LW-1 | date formula | `FirstDayOfWeek(Today()) - Days(7)` |
| 70 | LW-2 | date formula | `FirstDayOfWeek(Today()) - Days(14)` |
| ... | ... | ... | pattern: fid = 68 + n, formula = `FirstDayOfWeek(Today()) - Days(n*7)` |
| 119 | LW-51 | date formula | `FirstDayOfWeek(Today()) - Days(357)` |

## Summary Fields in Weekly (bv2sxg2ji)

All are `numeric`, `mode=summary`, `bold=true`, created via `POST /v1/tables/br6dcnv35/relationship/35`.

| FID | Label | summaryQuery |
|-----|-------|-------------|
| 64 | ATS LW | `{'11'.EX.'_fid_68'}` |
| 66 | ATS LW-1 | `{'11'.EX.'_fid_69'}` |
| 67 | ATS LW-2 | `{'11'.EX.'_fid_70'}` |
| ... | ... | pattern: fid = 64+n (skip 65), dateFid = 68+n |
| 116 | ATS LW-51 | `{'11'.EX.'_fid_119'}` |

**Pattern**: ATS LW-n uses dateFid = 68+n, summaryFid = 64+n (offset by 2 after fid 64 because fid 65 was a deleted test field).

## How to Add a Summary Field (via API)

```
POST /v1/tables/br6dcnv35/relationship/35
{
  "summaryFields": [
    { "label": "...", "accumulationType": "SUM", "from": 10, "where": "{'11'.EX.'_fid_<dateFid>'}" }
  ]
}
```
Max 10 summary fields per call. Returns the full updated relationship.

## Refresh Script

`_refresh_weekly.py` still works for back-filling data if needed, but the summary fields now auto-compute directly from Inventory History — no script writeback required.
