# Inventory Forecaster - Changelog

Reverted, removed, or superseded rules are documented here so the active
code base stays clean.  Anything still mentioned in code as "reverted" can
be moved here.

---

## 2026-05-23 - Deep audit Phase 1 (doc consolidation + dead code)

- **Deleted `METHODOLOGY.md`**: contradicted live code in 9+ places (still
  referenced removed Holt-Winters routing, stale 5% alert threshold instead of
  7.5%, DAMP=0.1 instead of 0.3/0.85, week-number Prime Day schedule, wrong
  VP-Q2/Q3 descriptions). Three docs remain as authoritative: `SKILL.md`
  (operational), `RULES.md` (rule registry), `CHANGELOG.md` (history).
- **`build_deck.py` footers updated** to reference SKILL.md + RULES.md.

## 2026-05-21 - Tier 1+2 audit cleanup

- **Removed `holt_winters()` function**: defined but never called anywhere.
  Dense buyers route through `seasonal_baseline()` which applies all the same
  concepts (level, seasonal profile, caps) plus the post-2025 calibration
  rules.  Restore from git history before this date if a true HW trend
  extrapolator is needed.
- **Renamed F31 (Front-week W1 tail cap) -> F71**: broke naming collision
  with F31 (Pre-launch NEW-item passthrough).  Same logic, new tag.
- **Renamed F6 (3 different rules) -> F6a/F6b/F6c**:
  - F6a = Inactive-with-Activity reclassification (in `classify()`)
  - F6b = L4/L13 decay dampener (in `seasonal_baseline()`)
  - F6c = Route sparse_intermittent to Heuristic (in `forecast_record()`)
- **Removed F29 dead pattern checks**: legacy `"new_item"` and `"sparse"`
  pattern values; `classify()` only emits `"inactive"`, `"sparse_intermittent"`,
  `"active"`.  Now also fires on ISO-detected items.
- **F70 Pre-launch protection**: F70 (switchover) no longer overrides
  Pre-launch passthrough records.  Pre-launch items have no historical signal
  and rely entirely on planner manual.
- **F70 Tell-AI protection**: F70 now respects weeks touched by F58 Tell-AI
  comments so explicit planner instructions beat the switchover heuristic.

## 2026-05-04 - F2 removed (Customer-median x 0.25 floor)

Was generating non-zero forecasts on items with zero history just because the
customer had other active SKUs - a known false-positive source for
"Reactivating" classifications.  Replaced by F1 (sibling-Mstyle median) and
F8 (shipment corroboration) only.  If neither fires, item legitimately has
no demand signal and stays Inactive with zero forecast.

## 2026-05-05 - R4 removed (Amazon Private Label skip)

APL items ARE shipped, so they should go through normal classification like
any other Amazon record.  Skipping them produced false zero forecasts.

## 2026-04-26 - F33 reverted (Model-class global calibration)

Attempted to apply global +/-X% multipliers per model class to correct the
observed Croston -15% median bias.  In practice the cap-base interaction
clipped Croston DOWN to median -37% (worse than the original).  The
global-multiplier approach is too blunt for cap-respecting models -- needs
realized-vs-forecast feedback data we do not yet have.

## 2026-04-22 - F22b reverted (cap ord_baseline before POS blend)

Capping ord_baseline BEFORE the F13/F15/F22a chain let F15 stop firing when
ord_baseline dropped, which then swung the POS blend UP rather than down.
Replaced with F22c which caps the FINAL baseline after the full chain.

## 2026-04-21 - F12 reverted (Tightened spike cap to 2.5x + secondary check)

The original F12 tightened the outlier-cap multiplier from 3.0x median to
2.5x and added a secondary spike check.  Both over-corrected on legitimate
multi-week buying patterns.  Reverted to the 3.0x cap (plus F25 extreme-
outlier DROP for outliers > 5x median).

---

## Naming conventions

| Prefix | Family | Examples |
|---|---|---|
| F# | Feature rule (numbered sequentially) | F1-F70 (with sub-letters: F38b, F59i, F69-WOS) |
| M# | Model-class rule | M1 (L52 ceiling), M2 (EOL dampen), M3 (Croston accel blend) |
| R# | Routing/Reclassification rule | R1 (OTB), R3 (Inactive floor), R5 (International) |
| S# | Sub-rule modifier | S1, S3, S4, S5, S6 |
| T# | Type-specific rule | T1-T4 (off-price, ecom, new-launch, Amazon-replen) |
| VP-Q# | VP-mandated query response | VP-Q1 through VP-Q6 |
| VP-XX | VP-mandated functional rule | VP-FL, VP-OP, VP-ATS, VP-ATS-Catch |
| F_XX | Functional/operational rule | F_PO_CUTOFF |

See `RULES.md` for the active rule registry.
