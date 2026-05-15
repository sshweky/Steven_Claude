# Data Pull Strategy

## Why one query is enough

`Sales_Budgets` is the canonical source for everything we need. It has:

- **Customer + Brand + Div** as the natural granularity (one row per Customer × Brand × Year × Budget Version)
- **Pre-rolled annual columns** for budget, current-year actuals, YTD, and open orders
- **Salesperson_lookup_** field with the pre-joined salesperson code from Accounts

This means a single 8-column raw-row pull on the 2026 active rows gets every metric for every variant of every deck.

**Critical: do NOT query `Invoices` or `Customer_POs` at scale.** Both tables are large (Invoices has 216K rows for FF 2025 alone) and aggregation queries against them hit the 60-90s MCP proxy timeout. Sales_Budgets has these actuals pre-rolled.

## The exact query

```sql
SELECT [Customer_Name], [Master_Brand], [Brand_Type], [Div], [Salesperson_lookup_],
       [Total_LY_], [Tot_CY_Budget_], [Total_Actual_YTD_], [Total_Open_]
FROM [Quickbase1].[ProductTrack].[Sales_Budgets]
WHERE [YYYY_numeric_] = 2026 AND [Active_BV_2] = 1
```

- **No GROUP BY, no SUM** — raw rows stream back faster than aggregations
- **Filter `[Active_BV_2] = 1`** prevents double-counting across budget versions
- **Filter `[YYYY_numeric_] = 2026`** isolates current planning year
- **Returns ~1,200 rows** — small enough to fit in one response, no pagination needed
- **`[Brand_Type]` (added May 10, 2026)** drives the Entertainment-brand projection override — see `business_rules.md`. Values: `CPG`, `Entertainment`, `Fetch Owned`, `Private Label`, `Other`, `Lifestyle`

## Column meanings

| Column | Type | Meaning |
|---|---|---|
| `Customer_Name` | VARCHAR | Customer entity name (e.g., "WAL MART STORES") |
| `Master_Brand` | VARCHAR | Brand (e.g., "Arm & Hammer", "Glad", "Glad for Pets") |
| `Brand_Type` | VARCHAR | Classification: `CPG`, `Entertainment`, `Fetch Owned`, `Private Label`, `Other`, `Lifestyle`. Drives the Entertainment override (see `business_rules.md`). |
| `Div` | VARCHAR | `'FF'` (Fetch/Pet) or `'BB'` (Brand Buzz/People) — authoritative split |
| `Salesperson_lookup_` | VARCHAR | Salesperson code (e.g., `FC019`) — translate via Accounts table |
| `Total_LY_` | DOUBLE | FY2025 actual sales (full year invoiced) |
| `Tot_CY_Budget_` | DOUBLE | FY2026 budget total (sum of monthly Bdgt columns) |
| `Total_Actual_YTD_` | DOUBLE | YTD 2026 shipped sales |
| `Total_Open_` | DOUBLE | Open orders (any future ship date) |

## Why these specific column names matter

Sales_Budgets has **multiple** budget version columns and **multiple** actuals columns. The ones above are the canonical rolled-up versions tied to BV 0326. Other names exist:

- `Total_Bdgt_Comp` — budget for a *comparison* version, not the active one
- `Total_Budget` — generic total, may include inactive rows
- `Tot_2023_Budget_` — historical, not 2026
- `Tot_CY_Budget_LW_` / `_Yest_` — last-week / yesterday snapshot, not current

Always use the four specific columns listed in the query above.

## Salesperson code → name lookup

The `Salesperson_lookup_` field returns codes (e.g., `FC010`, `BB050`) and never the human-readable name. To translate, query Accounts:

```sql
SELECT [Customer_Name], [Div], [Salesperson], [Slsprsn_Code]
FROM [Quickbase1].[ProductTrack].[Accounts]
WHERE [Salesperson] IS NOT NULL
  AND [Salesperson] <> 'House Account'
  AND [Salesperson] <> 'UNASSIGNED'
```

Then build a dict `{code: name}` from `Slsprsn_Code → Salesperson`. Pattern: each salesperson has both an `FCxxx` code (for their FF book) and a `BBxxx` code (for their BB book), with the last 3 digits matching. E.g., Daniel Sawford = `FC059` + `BB059`.

## Special codes to remember

| Code | Name | Treatment |
|---|---|---|
| `FC000` | House Account | Skip (Amazon entities) |
| `BB000` | House Account | Skip |
| `FC` | UNASSIGNED | Skip |
| (blank) | UNASSIGNED | Skip |

## Schema discovery (only if needed)

If a future Sales_Budgets schema change breaks this query, use targeted metadata calls — but **only if the smoke-test query fails**. Don't preemptively pull schema.

```
getColumns(catalogName='Quickbase1', schemaName='ProductTrack',
           tableName='Sales_Budgets', columnName='%')
```

Notable column quirks (carried from prior sessions):
- June uses `June_Bdgt`/`June_Actual` (full word), not `Jun_*`
- July uses `July_Bdgt`/`July_Actual` (full word), not `Jul_*`
- Open-order monthly columns: `Jan_Act_OO`...`Dec_Act_OO`. `Open Orders = Act_OO − Actual` per month.
- Last-year actuals available as `LY_Jan_Actual`...`LY_Dec_Actual` and rolled up as `Total_LY_`

## What we explicitly do NOT need to query

- **Invoices** — too big, aggregations time out, and Sales_Budgets has actuals pre-joined
- **Customer_POs** — same. `Total_Open_` on Sales_Budgets gives the OO total.
- **Orders_and_Shipments** — always times out, do not query under any circumstances
- **Styles** — has 700+ columns, pull only when product-level analysis is needed (not for these decks)

## Throttle protection (re-iterating)

If you're rebuilding decks frequently, the realm gets throttled. Apply the rules from SKILL.md:
- `getInstructions("QuickBase")` first
- Two failures → 15-minute stop, no exceptions
- Real wall-clock backoff (2s, 4s, 8s)
- Project budget upfront, stop if >10 calls projected
