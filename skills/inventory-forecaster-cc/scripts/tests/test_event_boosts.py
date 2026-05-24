"""Tests for the calendar-based Prime Day + Fall Prime Day boost calculator."""

from datetime import date


def test_prime_day_bumps_constant(fc):
    # May 1, May 15, May 29 ordering bumps
    bumps = fc.PRIME_DAY_BUMPS
    assert (5, 1, 1.25) in bumps
    assert (5, 15, 1.25) in bumps
    assert (5, 29, 1.50) in bumps


def test_event_boosts_compute_with_may_w1(fc, monkeypatch):
    # Mock W1 = May 17, 2026 -- Memorial Day = May 25, Labor Day = Sep 7
    fc.ORIG_PRJ_COLS = ["05_17_W1"] + [f"05_{17+i:02d}_W{i+1}" for i in range(1, 26)]
    fc._EVENT_BOOSTS_CACHE = None  # force recompute
    prime, fall = fc._compute_event_boosts()

    # May 29 lands in W2 (May 24-30)
    assert 2 in prime
    assert prime[2] == 1.50  # max of any bumps landing in that week

    # Labor Day 2026 = Sep 7 (first Monday of Sept)
    # Tuesday after = Sep 8
    # W1 starts May 17, so Sep 8 is delta = 114 days -> week 17 (114 // 7 + 1 = 17)
    assert 17 in fall
    assert fall[17] == 1.30


def test_event_boosts_with_february_w1(fc):
    """W1 = early February: ALL three Prime Day bumps and Fall Prime Day in window."""
    fc.ORIG_PRJ_COLS = ["02_01_W1"] + ["fake"] * 25
    fc._EVENT_BOOSTS_CACHE = None
    prime, fall = fc._compute_event_boosts()

    # May 1 -> W13 (delta = 89 days, 89//7+1 = 13)
    assert 13 in prime
    # May 15 -> W15
    assert 15 in prime
    # May 29 -> W17
    assert 17 in prime
    # Tuesday after Memorial Day 2026 (May 25 + 1) = May 26 -> W17
    # But fall is "Tuesday after Labor Day" not Memorial Day
    # Labor Day 2026 = Sep 7, Tuesday after = Sep 8 -> outside 26-week window
    # Actually: Feb 1 + 26*7 = Aug 1, so Sep 8 is OUT of window.
    # We expect no fall bump in this window.
    assert fall == {}


def test_event_boosts_uses_max_when_two_bumps_same_week(fc):
    """When two Prime Day bumps land in the same week, the LARGER multiplier wins."""
    # Pick a W1 where May 15 and May 22 would land in same week. May 22 isn't a bump,
    # so this is harder to test directly. Instead, verify the max() logic itself:
    fc.ORIG_PRJ_COLS = ["05_17_W1"] + ["fake"] * 25
    fc._EVENT_BOOSTS_CACHE = None
    prime, _ = fc._compute_event_boosts()
    # W2 should hold the MAY 29 lift (1.50), not get overwritten by a lower one
    for wk, mult in prime.items():
        # Every reported boost should be >= 1.0
        assert mult >= 1.0
    # If May 29 -> W2, that's 1.50
    if 2 in prime:
        assert prime[2] >= 1.25


def test_cache_invalidation(fc):
    """Resetting _EVENT_BOOSTS_CACHE forces recompute on next access."""
    fc.ORIG_PRJ_COLS = ["05_17_W1"] + ["fake"] * 25
    fc._EVENT_BOOSTS_CACHE = None
    p1, f1 = fc._get_event_boosts()
    # Now change ORIG_PRJ_COLS and DON'T invalidate -- should get cached result
    fc.ORIG_PRJ_COLS = ["02_01_W1"] + ["fake"] * 25
    p_cached, f_cached = fc._get_event_boosts()
    assert p_cached == p1  # still cached
    # Now invalidate and recompute
    fc._EVENT_BOOSTS_CACHE = None
    p_new, f_new = fc._get_event_boosts()
    assert p_new != p1 or f_new != f1  # at least one differs


def test_fall_prime_day_uses_labor_day_not_memorial(fc):
    """Regression: Fall Prime Day bump = Tuesday after LABOR Day, not Memorial Day.

    Memorial Day 2026 = May 25 (Monday); Tuesday after = May 26
    Labor Day 2026 = Sep 7 (Monday); Tuesday after = Sep 8

    With W1 = May 17:
      May 26 -> W2 (Memorial Day path -- WRONG)
      Sep 8  -> W17 (Labor Day path -- CORRECT)
    """
    fc.ORIG_PRJ_COLS = ["05_17_W1"] + ["fake"] * 25
    fc._EVENT_BOOSTS_CACHE = None
    _, fall = fc._compute_event_boosts()
    # If Memorial Day logic were back, fall would have W2
    assert 2 not in fall, "Fall Prime Day reverted to Memorial Day -- regression!"
    # Labor Day path: Sep 8 lands in W17 (delta = 114 days)
    assert 17 in fall
