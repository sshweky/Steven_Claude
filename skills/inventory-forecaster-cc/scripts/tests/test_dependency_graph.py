"""Smoke tests for rule_dependency_graph.py."""

import subprocess
import sys
from pathlib import Path


def test_graph_runs_to_completion(tmp_path):
    here = Path(__file__).resolve().parent.parent
    md = tmp_path / "rule_deps.md"
    dot = tmp_path / "rule_deps.dot"
    result = subprocess.run(
        [sys.executable, str(here / "rule_dependency_graph.py"),
         "--md-out", str(md), "--dot-out", str(dot)],
        capture_output=True, text=True, timeout=120
    )
    assert result.returncode == 0, f"graph crashed: {result.stderr}"
    assert md.exists()
    assert dot.exists()
    assert "Rule Dependency Graph" in md.read_text(encoding="utf-8")
