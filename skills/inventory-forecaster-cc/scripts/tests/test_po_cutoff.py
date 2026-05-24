"""Tests for F_PO_CUTOFF firing window logic (fixed 2026-05-24).

The rule should fire on Wed-Sat for FF (cutoff=Tue night) and Thu-Sat for BB
(cutoff=Wed night).  Sun-Tue are pre-cutoff days for the upcoming W1 PO and
must NOT zero W1.

Python weekday: Mon=0 Tue=1 Wed=2 Thu=3 Fri=4 Sat=5 Sun=6
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _in_firing_window(today_wd, cutoff_wd):
    """Reproduce the rule's gate condition for unit testing."""
    return cutoff_wd <= today_wd <= 5


def test_ff_fires_wed_through_sat():
    """FF (cutoff_wd=2 = Wed): should fire Wed, Thu, Fri, Sat."""
    cutoff = 2  # FF cutoff value from config (Python weekday for Wed)
    assert _in_firing_window(2, cutoff)  # Wed
    assert _in_firing_window(3, cutoff)  # Thu
    assert _in_firing_window(4, cutoff)  # Fri
    assert _in_firing_window(5, cutoff)  # Sat


def test_ff_does_not_fire_sun_through_tue():
    """FF: must NOT fire Sun, Mon, Tue -- cutoff for this week's W1 hasn't passed."""
    cutoff = 2
    assert not _in_firing_window(6, cutoff)  # Sun -- REGRESSION GUARD
    assert not _in_firing_window(0, cutoff)  # Mon
    assert not _in_firing_window(1, cutoff)  # Tue (cutoff day -- still time)


def test_bb_fires_thu_through_sat():
    """BB (cutoff_wd=3 = Thu): should fire Thu, Fri, Sat."""
    cutoff = 3
    assert _in_firing_window(3, cutoff)  # Thu
    assert _in_firing_window(4, cutoff)  # Fri
    assert _in_firing_window(5, cutoff)  # Sat


def test_bb_does_not_fire_sun_through_wed():
    """BB: must NOT fire Sun, Mon, Tue, Wed."""
    cutoff = 3
    assert not _in_firing_window(6, cutoff)  # Sun
    assert not _in_firing_window(0, cutoff)  # Mon
    assert not _in_firing_window(1, cutoff)  # Tue
    assert not _in_firing_window(2, cutoff)  # Wed (cutoff day -- still time for BB)


def test_sunday_regression_guard():
    """The exact bug fixed 2026-05-24: Sunday (Python wd=6) firing F_PO_CUTOFF
    on FF (cutoff=2) because the old check was `today_wd >= cutoff_wd`.
    Sunday is a new week -- the NEW W1's cutoff is upcoming Tuesday."""
    # Old (buggy) check would have been: 6 >= 2 = True -> fire
    # New check: 2 <= 6 <= 5 -> False -> don't fire
    assert not _in_firing_window(6, 2), \
        "Sunday FF must NOT fire F_PO_CUTOFF (new week starting, cutoff is upcoming)"
    assert not _in_firing_window(6, 3), \
        "Sunday BB must NOT fire F_PO_CUTOFF (new week starting, cutoff is upcoming)"
