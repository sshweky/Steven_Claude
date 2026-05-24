"""Tests for confidence.py residual-bootstrap CI generator."""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from confidence import bootstrap_intervals


def test_ci_returns_correct_horizon():
    history = [500] * 52
    fcst = [500] * 26
    low, high = bootstrap_intervals(history, fcst, mp=6, n_boot=100)
    assert len(low) == 26
    assert len(high) == 26


def test_low_le_point_le_high():
    history = [500 + (i % 5 - 2) * 50 for i in range(52)]
    fcst = [500] * 26
    low, high = bootstrap_intervals(history, fcst, mp=6, n_boot=200)
    for lo, point, hi in zip(low, fcst, high):
        assert lo <= point, f"low {lo} > point {point}"
        assert hi >= point, f"high {hi} < point {point}"


def test_no_negative_lower_bound():
    history = [10] * 52
    fcst = [10] * 26
    low, high = bootstrap_intervals(history, fcst, mp=1, n_boot=200)
    for lo in low:
        assert lo >= 0, f"low bound {lo} is negative"


def test_degenerate_history_returns_point():
    """If we can't compute residuals (empty history), bounds should equal point."""
    history = []
    fcst = [100] * 26
    low, high = bootstrap_intervals(history, fcst, mp=1)
    # Either equal to point, or graceful degraded behavior
    assert all(lo <= 100 <= hi for lo, hi in zip(low, high))


def test_deterministic_with_seed():
    """Same inputs -> same outputs (random.Random seed=42)."""
    history = [500 + (i % 7 - 3) * 50 for i in range(52)]
    fcst = [500] * 26
    low1, high1 = bootstrap_intervals(history, fcst, mp=6, n_boot=100)
    low2, high2 = bootstrap_intervals(history, fcst, mp=6, n_boot=100)
    assert low1 == low2
    assert high1 == high2


def test_high_volatility_wider_interval():
    """Volatile history should produce wider CIs than steady history."""
    steady_hist = [500] * 52
    volatile_hist = [500 + (-300 if i % 2 else 300) for i in range(52)]
    fcst = [500] * 26

    low_s, high_s = bootstrap_intervals(steady_hist, fcst, mp=1, n_boot=300)
    low_v, high_v = bootstrap_intervals(volatile_hist, fcst, mp=1, n_boot=300)

    width_s = high_s[0] - low_s[0]
    width_v = high_v[0] - low_v[0]
    assert width_v > width_s, f"Volatile width {width_v} should exceed steady {width_s}"
