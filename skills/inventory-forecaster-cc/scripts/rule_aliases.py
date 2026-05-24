"""
rule_aliases.py  --  Bidirectional alias mapping between legacy rule codes
                     (F18, R5, VP-Q4) and the new phase-prefixed scheme
                     (BAS-018, CLS-005, HRD-004).

NO existing rule is renamed by this commit. The aliases are additive --
either code resolves to the same rule.  This lets the new pipeline phases
adopt the phase-prefixed naming while existing rules keep working.

Migration strategy (per B5):
  1. New rules added in 2026-Q3 onwards adopt the phase-prefixed scheme.
  2. Aliases let docs, tooling, and tests reference either form.
  3. After 6-12 months, the legacy short codes can be deprecated in docs
     but the aliases continue to work in code.

Naming convention for new codes:
  PHASE-NNN[a-z]?
where PHASE is one of:
  HIS  -- History normalization (Phase A in pipeline.py)
  CLS  -- Classification (Phase B)
  BAS  -- Baseline / model (Phase C)
  GAT  -- Demand-signal gates (Phase D/E)
  HRD  -- Hard constraints (Phase F)
  FIN  -- Finalize / guards (Phase G)
  VAL  -- Validation-only rules

NNN is a zero-padded ordinal within the phase.

Usage:
    from rule_aliases import canonical, alias_for, all_aliases
    canonical("F18")          -> "BAS-018"    (preferred new form)
    canonical("BAS-018")      -> "BAS-018"
    alias_for("BAS-018")      -> "F18"        (legacy form for back-compat output)
    "F18" in all_aliases()    -> True
"""


# Each entry: (new_code, legacy_code, short_description)
# Adding a new rule? Add the legacy code as None if it's brand new.
_MAPPINGS = [
    # ── History normalization ────────────────────────────────────────────────
    ("HIS-001", "F35",  "Stockout-backlog removal"),
    ("HIS-002", "F39",  "Duplicate-order run dedup"),
    ("HIS-003", "F41",  "Phantom-order detection"),
    ("HIS-004", "F43",  "Recent-spike attenuation"),
    ("HIS-005", "F47",  "OOS rebuild-ramp cap"),
    ("HIS-006", "F49",  "F43-skip on sustained acceleration"),
    ("HIS-007", "F55",  "LY OOS-gap imputation"),
    ("HIS-008", "VP-ATS", "ATS L26W OOS-week imputation"),
    ("HIS-009", "VP-ATS-Catch", "ATS catch-up spike normalization"),
    ("HIS-010", "VP-Q2", "OOS-aware demand reconstruction"),

    # ── Classification / routing ─────────────────────────────────────────────
    ("CLS-001", "R1",   "One-time-buy detection"),
    ("CLS-002", "R3",   "Inactive conservative L26 floor"),
    ("CLS-003", "R5",   "International bulk-buyer relaxation"),
    ("CLS-004", "F6a",  "Inactive-with-activity reclassification"),
    ("CLS-005", "F6c",  "Sparse_intermittent -> Heuristic routing"),
    ("CLS-006", "F31",  "Pre-launch NEW-item manual passthrough"),
    ("CLS-007", "F60",  "EC/COS/AMZ-transition history inheritance"),
    ("CLS-008", "F68",  "Amazon inactive-channel hard zero"),
    ("CLS-009", "F-B",  "L13 burst-cadence Croston override"),

    # ── Baseline / model rules ───────────────────────────────────────────────
    ("BAS-001", "VP-Q1", "Baseline-mode gating"),
    ("BAS-002", "F3",   "Outlier cap"),
    ("BAS-003", "F4",   "Thin-history window widening"),
    ("BAS-004", "F25",  "Extreme-outlier drop"),
    ("BAS-005", "F18",  "Croston z POS anchor"),
    ("BAS-006", "F22a", "Trailing-zero drawdown discount"),
    ("BAS-007", "F22c", "Sparse-L13 final-baseline ceiling"),
    ("BAS-008", "F24",  "L13-all ceiling"),
    ("BAS-009", "F16",  "Category-gated damping relief"),
    ("BAS-010", "F10",  "Declining-item EOL scale-down"),
    ("BAS-011", "F14a", "POS-healthy override on F10"),
    ("BAS-012", "F26",  "Mild-zone decay"),
    ("BAS-013", "F27",  "Mild-zone ramp"),
    ("BAS-014", "F50",  "Stockout-pattern guard"),
    ("BAS-015", "T4",   "E-commerce accelerator lift"),
    ("BAS-016", "L8W-overlay", "Recency-weighted L8 blend"),

    # ── Demand-signal gates (D/E phases) ─────────────────────────────────────
    ("GAT-001", "F11",  "Prime Day calendar lifts"),
    ("GAT-002", "F58",  "Tell-AI comment replay"),
    ("GAT-003", "F38f", "Amazon Not-Buyable hard zero W1-W4"),
    ("GAT-004", "F67",  "Amazon buy-box $0 dampener"),
    ("GAT-005", "F59i", "F59 POS anchor when DC healthy"),
    ("GAT-006", "F59j", "F59 restock lift when DC low"),
    ("GAT-007", "F59k", "F59 EOL anchor"),
    ("GAT-008", "F59a", "F59 Amazon L4W floor"),
    ("GAT-009", "F66",  "Per-customer bias correction"),
    ("GAT-010", "F62",  "Soft L4W/L13W trend blend"),
    ("GAT-011", "F63",  "Multi-pack baseline floor"),
    ("GAT-012", "F64",  "Trade fall calendar events"),
    ("GAT-013", "F69",  "DI direct-import sibling blend"),

    # ── Hard constraints (Phase F) ───────────────────────────────────────────
    ("HRD-001", "VP-Q4", "Don't double-count confirmed POs"),
    ("HRD-002", "F70",   "Switchover variant conflict"),
    ("HRD-003", "F70b",  "Reverse-switchover"),
    ("HRD-004", "F_PO_CUTOFF", "Amazon division PO cutoff"),
    ("HRD-005", "VP-OP", "Off-price PO buffer zone"),
    ("HRD-006", "F19",   "Conservative inactive floor"),
    ("HRD-007", "M1",    "L52/L26 ceiling"),
    ("HRD-008", "M2",    "Phase-out / EOL dampening"),
    ("HRD-009", "F61",   "Horizon confidence decay"),
    ("HRD-010", "F65",   "Zero-velocity suppression"),

    # ── Finalize / guards (Phase G) ──────────────────────────────────────────
    ("FIN-001", "G2",   "All-zero-by-guards demotion"),
    ("FIN-002", "F71",  "Front-week W1 tail cap"),
    ("FIN-003", "F20",  "Heuristic -> Inactive when manual=0"),
    ("FIN-004", "F30",  "Zero-order-history hard guard"),
]


# Build forward + reverse lookup
_NEW_TO_LEGACY = {new: legacy for new, legacy, _ in _MAPPINGS if legacy}
_LEGACY_TO_NEW = {legacy: new for new, legacy, _ in _MAPPINGS if legacy}
_DESCRIPTIONS  = {new: desc for new, _, desc in _MAPPINGS}


def canonical(code: str) -> str:
    """Return the preferred new-form code for a given rule.

    Examples:
        canonical("F18")     -> "BAS-005"
        canonical("BAS-005") -> "BAS-005"
        canonical("UnknownX") -> "UnknownX"  (passthrough)
    """
    if code in _DESCRIPTIONS:
        return code  # already canonical
    return _LEGACY_TO_NEW.get(code, code)


def alias_for(code: str) -> str | None:
    """Return the legacy short code for a new-form code, or None.

    Examples:
        alias_for("BAS-005") -> "F18"
        alias_for("F18")     -> None  (already legacy)
    """
    return _NEW_TO_LEGACY.get(code)


def describe(code: str) -> str:
    """One-line description for either form."""
    new = canonical(code)
    return _DESCRIPTIONS.get(new, "(no description registered)")


def all_aliases() -> set[str]:
    """All known codes (both new and legacy)."""
    s = set(_NEW_TO_LEGACY.keys())
    s.update(_NEW_TO_LEGACY.values())
    return s


def migration_table_md() -> str:
    """Generate a markdown migration table for RULES.md."""
    lines = ["| New code | Legacy code | Description |",
             "|---|---|---|"]
    for new, legacy, desc in _MAPPINGS:
        lines.append(f"| `{new}` | `{legacy}` | {desc} |")
    return "\n".join(lines)


if __name__ == "__main__":
    # Smoke test + print migration table
    assert canonical("F18") == "BAS-005"
    assert canonical("BAS-005") == "BAS-005"
    assert alias_for("BAS-005") == "F18"
    assert alias_for("F18") is None
    assert describe("F18") == "Croston z POS anchor"
    print(f"Total aliased rules: {len(_NEW_TO_LEGACY)}")
    print("Migration table (paste into RULES.md):\n")
    print(migration_table_md())
