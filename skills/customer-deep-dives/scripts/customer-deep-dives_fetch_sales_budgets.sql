-- Customer Deep Dives — canonical data pull
-- Run via CData Connect AI MCP queryData tool
-- Returns ~1,200 rows. NO GROUP BY, NO SUM — raw rows only.
-- Aggregation happens locally in Python.
--
-- Cost: 1 CData call. Pair with the Accounts query (separate file) for full data needs.

SELECT [Customer_Name],
       [Master_Brand],
       [Brand_Type],
       [Div],
       [Salesperson_lookup_],
       [Total_LY_],
       [Tot_CY_Budget_],
       [Total_Actual_YTD_],
       [Total_Open_]
FROM [Quickbase1].[ProductTrack].[Sales_Budgets]
WHERE [YYYY_numeric_] = 2026
  AND [Active_BV_2] = 1
