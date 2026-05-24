#!/usr/bin/env python3
"""
audit_rules.py  --  Drift checker for rule codes between code and docs.

Catches the case where someone adds a rule via _fire("Fxx") in
inventory_forecaster.py but forgets to add it to RULES.md (or vice versa).

What it does:
  1. Greps inventory_forecaster.py for:
       _fire("CODE")                     -- explicit fires
       meta["drivers"].append("CODE ...") -- driver-string fires (regex)
     and extracts every distinct rule code with line numbers.
  2. Parses the rule tables in RULES.md (any markdown table row whose first
     column contains a rule code).
  3. Reports:
       - Rules in code but not in RULES.md          [add to docs]
       - Rules in RULES.md but not in code          [remove or implement]
       - Rules in code but with no narrative driver  [hard to debug]

Exit code:
  0 = no drift
  1 = drift found (suitable for pre-commit hook)

Known limitations (false positives in "docs-only" section):
  - Rules that fire via _scan_rule_fires() baseline_mode signatures
    (e.g. F25/F26/F27 detected by string match on baseline_mode) are not
    picked up by this scanner. After B2 (structured fire() refactor in
    Phase 3) those will route through _fire() and this tool will be
    accurate without exceptions.

Usage:
    python scripts/audit_rules.py                  # default paths
    python scripts/audit_rules.py --strict         # exit 1 on any warning
    python scripts/audit_rules.py --verbose        # show line numbers
"""

import argparse
import re
import sys
from pathlib import Path
from collections import defaultdict


# ─────────────────────────────────────────────────────────────────────────────
# Patterns
# ─────────────────────────────────────────────────────────────────────────────

# _fire("F18"), _fire('F59a'), _fire("VP-Q1")
_FIRE_PATTERN = re.compile(r'''_fire\(\s*["']([A-Za-z][A-Za-z0-9_\-]*)["']''')

# Driver / alert / message strings often start with the rule code, e.g.:
#     f"F70 Switchover variant ..."
#     "VP-Q4 zeroed W{w}: confirmed PO ..."
#     f"R5 International liveness extended ..."
# These can be on a different line than the .append() call, so we scan ANY
# quoted string starting with a token followed by space. is_rule_code()
# filters out non-rule words.
_DRIVER_PATTERN = re.compile(
    r'''[fF]?["']([A-Za-z][A-Za-z0-9_\-]*?)\s'''
)

# RULES.md table row: starts with "|", second cell may contain a rule code
# wrapped in backticks or bold. We grab anything that looks like a code in
# the first or second non-empty cell.
_RULE_CODE_TOKEN = re.compile(r'\b([A-Z][A-Za-z0-9]*(?:-[A-Za-z0-9]+)*[a-z]?)\b')

# Codes we consider "rule codes" rather than generic words. Must match one of
# the conventions in CHANGELOG.md.
_RULE_CODE_FAMILIES = re.compile(
    r'''^(?:
        F\d+[a-z]?                |   # F12, F38a, F59o
        F\d+-[A-Za-z]+            |   # F69-WOS, F69-shift
        F_[A-Z_]+                 |   # F_PO_CUTOFF
        R\d+                      |   # R1, R3, R9
        M\d+                      |   # M1, M2, M3
        S\d+                      |   # S1..S6
        T\d+                      |   # T1..T4
        VP-Q\d+                   |   # VP-Q1..VP-Q4
        VP-[A-Z]+(?:-[A-Za-z]+)?  |   # VP-FL, VP-OP, VP-ATS, VP-ATS-Catch
        G\d+                      |   # G2
        Fix\s*\d+                 |   # Fix 1..Fix 5 (legacy)
        Priority\s*\d+                # Priority 1..Priority 8 (legacy)
    )$''',
    re.VERBOSE,
)


def is_rule_code(token: str) -> bool:
    return bool(_RULE_CODE_FAMILIES.match(token))


def extract_codes_from_source(path: Path) -> dict[str, list[tuple[int, str]]]:
    """Return {rule_code: [(line_num, kind), ...]} from a .py source file."""
    found = defaultdict(list)
    with open(path, encoding="utf-8") as f:
        for i, line in enumerate(f, 1):
            for m in _FIRE_PATTERN.finditer(line):
                code = m.group(1)
                if is_rule_code(code):
                    found[code].append((i, "_fire"))
            for m in _DRIVER_PATTERN.finditer(line):
                code = m.group(1)
                if is_rule_code(code):
                    found[code].append((i, "driver"))
    return dict(found)


def extract_codes_from_rules_md(path: Path) -> dict[str, int]:
    """Return {rule_code: line_num} from a RULES.md markdown file.

    Picks up any backtick-wrapped or bold rule code in the table rows.
    Also picks up codes in plain-text cells (the registry uses mixed formatting).
    """
    found = {}
    with open(path, encoding="utf-8") as f:
        for i, line in enumerate(f, 1):
            if not line.lstrip().startswith("|"):
                continue
            # Skip the table separator row
            if set(line.strip().replace("|", "").strip()) <= {"-", ":", " "}:
                continue
            # Strip markdown formatting for code scan
            stripped = line.replace("**", "").replace("`", "").replace("*", "")
            for token in _RULE_CODE_TOKEN.findall(stripped):
                if is_rule_code(token) and token not in found:
                    found[token] = i
    return found


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    here = Path(__file__).resolve().parent
    p.add_argument("--source", default=str(here / "inventory_forecaster.py"),
                   help="Path to the forecaster Python source.")
    p.add_argument("--rules-md", default=str(here.parent / "RULES.md"),
                   help="Path to RULES.md registry.")
    p.add_argument("--strict", action="store_true",
                   help="Exit 1 if any warning is reported (default: only on drift).")
    p.add_argument("--verbose", action="store_true",
                   help="Print every line number that fires each rule.")
    args = p.parse_args()

    src_path = Path(args.source)
    rules_path = Path(args.rules_md)
    if not src_path.exists():
        sys.exit(f"ERROR: source file not found: {src_path}")
    if not rules_path.exists():
        sys.exit(f"ERROR: RULES.md not found: {rules_path}")

    print(f"Source:    {src_path}")
    print(f"Registry:  {rules_path}")
    print()

    code_rules  = extract_codes_from_source(src_path)
    doc_rules   = extract_codes_from_rules_md(rules_path)
    code_set    = set(code_rules.keys())
    doc_set     = set(doc_rules.keys())

    in_code_not_docs = sorted(code_set - doc_set)
    in_docs_not_code = sorted(doc_set - code_set)
    in_both          = sorted(code_set & doc_set)

    print(f"Rules in code only: {len(in_code_not_docs)}")
    print(f"Rules in docs only: {len(in_docs_not_code)}")
    print(f"Rules in both:      {len(in_both)}")
    print()

    drift = False

    if in_code_not_docs:
        drift = True
        print("=" * 60)
        print("  DRIFT: rules in code but NOT in RULES.md")
        print("=" * 60)
        for code in in_code_not_docs:
            sites = code_rules[code]
            if args.verbose:
                site_str = "  ".join(f"L{ln}:{kind}" for ln, kind in sites[:5])
                if len(sites) > 5:
                    site_str += f"  +{len(sites)-5} more"
            else:
                site_str = f"{len(sites)} fire site{'s' if len(sites)!=1 else ''}"
            print(f"  {code:14s}  {site_str}")
        print()

    if in_docs_not_code:
        drift = True
        print("=" * 60)
        print("  DRIFT: rules in RULES.md but NOT in code")
        print("=" * 60)
        for code in in_docs_not_code:
            print(f"  {code:14s}  RULES.md L{doc_rules[code]}")
        print()

    # Warning: rules that fire but never emit a driver string (hard to debug).
    code_with_drivers   = {c for c, sites in code_rules.items()
                           if any(kind == "driver" for _, kind in sites)}
    code_only_explicit  = code_set - code_with_drivers
    if code_only_explicit and args.verbose:
        print("=" * 60)
        print("  INFO: rules with _fire() but no driver-string emission")
        print("        (harder for planners to debug; consider adding a driver)")
        print("=" * 60)
        for code in sorted(code_only_explicit):
            print(f"  {code}")
        print()

    if drift:
        print(f"\n[DRIFT] {len(in_code_not_docs)} code-only, "
              f"{len(in_docs_not_code)} docs-only rules. Update RULES.md.")
        sys.exit(1)
    else:
        print("\n[OK] No drift between code and RULES.md.")
        sys.exit(0)


if __name__ == "__main__":
    main()
