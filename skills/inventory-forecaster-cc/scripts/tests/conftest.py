"""
conftest.py -- pytest fixtures shared across all unit tests.

Lets every test import inventory_forecaster directly with mocked network
dependencies so tests run offline.
"""

import sys
import os
import pytest
from pathlib import Path
from unittest import mock


# Make scripts/ importable as a flat package
_SCRIPTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_SCRIPTS))


@pytest.fixture(scope="session")
def fc():
    """Import inventory_forecaster with network calls mocked."""
    with mock.patch("urllib.request.urlopen"), \
         mock.patch("urllib.request.Request"):
        import inventory_forecaster as fc_mod
    return fc_mod


@pytest.fixture
def empty_meta():
    return {"drivers": []}


@pytest.fixture
def amazon_history_steady():
    """52w order history -- steady ~500/wk Amazon-like pattern, oldest -> newest."""
    return [500] * 52


@pytest.fixture
def amazon_history_post_stockup():
    """L52W history representing the FF7297 case:
    - Old weeks (1-39): baseline 500/wk
    - Recent stock-up (40-49): 2000/wk (stocked up for Prime Day pre-buy)
    - Last 2 weeks (50-51): 0 (Amazon paused ordering, drawing down inventory)
    - Last week (52): single 849 spike (caught up partially)
    """
    h = [500] * 39 + [2000] * 10 + [0, 0, 849]
    assert len(h) == 52
    return h


@pytest.fixture
def amazon_history_declining():
    """Declining item: L52W ramps down from 600 -> 200 -> 50/wk."""
    return [600] * 20 + [200] * 20 + [50] * 12


@pytest.fixture
def amazon_history_zero():
    """52w of zeros = inactive."""
    return [0] * 52


@pytest.fixture
def amazon_history_sparse():
    """Sparse / intermittent: 8 active weeks scattered across 52w."""
    h = [0] * 52
    for i in [3, 8, 14, 22, 27, 35, 40, 47]:
        h[i] = 600
    return h


@pytest.fixture
def amazon_history_seasonal():
    """Q4 holiday peak: low Q1-Q3, ramp in Q4."""
    h = [50] * 26 + [150] * 13 + [500] * 13
    assert len(h) == 52
    return h


@pytest.fixture
def amazon_pos_strong():
    """Strong Amazon POS signal -- healthy DC, accelerating velocity."""
    return {
        "Avg_Units_Wk_L4w":  600,
        "Avg_Units_Wk_L13w": 500,
        "Avg_Units_Wk_L26w": 450,
        "Avg_Units_Wk_L52w": 400,
        "Ordered_Units_LW":  610,
    }


@pytest.fixture
def amazon_pos_weak():
    """Declining POS velocity."""
    return {
        "Avg_Units_Wk_L4w":  100,
        "Avg_Units_Wk_L13w": 300,
        "Avg_Units_Wk_L26w": 400,
        "Avg_Units_Wk_L52w": 450,
        "Ordered_Units_LW":  80,
    }


@pytest.fixture
def amazon_pos_ff7297():
    """POS for the FF7297 case -- ~500/wk consumer demand, spike to 849 LW."""
    return {
        "Avg_Units_Wk_L4w":  510,
        "Avg_Units_Wk_L13w": 480,
        "Avg_Units_Wk_L26w": 470,
        "Avg_Units_Wk_L52w": 460,
        "Ordered_Units_LW":  849,
    }
