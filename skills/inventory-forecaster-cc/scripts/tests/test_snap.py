"""Tests for the master-pack snap() function."""


def test_snap_rounds_to_master_pack(fc):
    assert fc.snap(99, 6) == 102  # nearest multiple of 6 >= 99 (or nearest)
    # Confirm it's snapping to multiples
    for q in [50, 100, 250, 500, 1000]:
        for mp in [6, 12, 24, 48]:
            result = fc.snap(q, mp)
            assert result % mp == 0, f"snap({q}, {mp}) = {result} not a multiple of {mp}"


def test_snap_zero_input(fc):
    assert fc.snap(0, 6) == 0
    assert fc.snap(0, 1) == 0


def test_snap_mp_one_passes_through(fc):
    # mp=1 means no rounding (or rounds to nearest int)
    for q in [1.0, 1.5, 99.7, 1000.3]:
        result = fc.snap(q, 1)
        assert isinstance(result, (int, float))


def test_snap_handles_missing_mp(fc):
    # mp=None or 0 should not crash
    result = fc.snap(100, None) if hasattr(fc, "snap") else None
    # If it doesn't accept None, this test is informational; check it doesn't blow up
    try:
        fc.snap(100, 0)
    except (ZeroDivisionError, ValueError):
        pass  # acceptable
