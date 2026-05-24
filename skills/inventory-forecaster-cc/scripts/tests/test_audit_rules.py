"""Smoke tests for audit_rules.py rule-drift checker."""

import subprocess
import sys
from pathlib import Path


def test_audit_rules_runs_to_completion():
    here = Path(__file__).resolve().parent.parent
    result = subprocess.run(
        [sys.executable, str(here / "audit_rules.py")],
        capture_output=True, text=True, timeout=60
    )
    # Either exit 0 (no drift) or exit 1 (drift found). 2 = crash.
    assert result.returncode in (0, 1), f"audit_rules.py crashed: {result.stderr}"
    assert "Rules in code" in result.stdout
    assert "Rules in docs" in result.stdout


def test_is_rule_code_function():
    here = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(here))
    from audit_rules import is_rule_code
    # Valid rule codes
    assert is_rule_code("F18")
    assert is_rule_code("F59a")
    assert is_rule_code("VP-Q4")
    assert is_rule_code("VP-ATS-Catch")
    assert is_rule_code("F_PO_CUTOFF")
    assert is_rule_code("R1")
    assert is_rule_code("M3")
    assert is_rule_code("G2")
    # NOT rule codes
    assert not is_rule_code("hello")
    assert not is_rule_code("baseline")
    assert not is_rule_code("fcst")
