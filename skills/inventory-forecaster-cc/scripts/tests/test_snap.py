"""Tests for the master-pack snap() function. snap rounds to NEAREST multiple."""

import pytest


def test_snap_rounds_to_nearest_multiple(fc):
    # 99 is closer to 96 (3 away) than 102 (3 away) -- ties round to even / floor
    result = fc.snap(99, 6)
    assert result % 6 == 0
    assert result in (96, 102), f"snap(99, 6) = {result}"


def test_snap_various_quantities(fc):
    for q in [50, 100, 250, 500, 1000]:
        for mp in [6, 12, 24, 48]:
            result = fc.snap(q, mp)
            assert result % mp == 0, f"snap({q}, {mp}) = {result} not a multiple of {mp}"


def test_snap_zero_input(fc):
    assert fc.snap(0, 6) == 0


def test_snap_mp_one_no_rounding(fc):
    # mp=1 returns max(0, round(qty))
    assert fc.snap(99.7, 1) == 100
    assert fc.snap(0.3, 1) == 0


def test_snap_negative_qty_returns_zero(fc):
    assert fc.snap(-10, 6) == 0


def test_snap_handles_none_mp_gracefully(fc):
    # snap is allowed to error on None -- caller's responsibility to pass valid mp
    with pytest.raises(TypeError):
        fc.snap(100, None)
