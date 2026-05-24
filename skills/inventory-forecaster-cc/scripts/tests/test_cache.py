"""Tests for unified cache.py module."""

import time
import os
import json
import pytest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture
def tmp_cache(tmp_path):
    """Cache instance bound to a temp path so tests don't pollute the real cache dir."""
    from cache import Cache
    return Cache("test_unit", ttl_hours=1.0, path=tmp_path / "test_unit.json")


def test_set_get_roundtrip(tmp_cache):
    tmp_cache.set("alpha", 42)
    tmp_cache.set("beta", {"nested": [1, 2, 3]})
    assert tmp_cache.get("alpha") == 42
    assert tmp_cache.get("beta") == {"nested": [1, 2, 3]}


def test_has_and_contains(tmp_cache):
    tmp_cache.set("k", "v")
    assert tmp_cache.has("k")
    assert "k" in tmp_cache
    assert not tmp_cache.has("missing")
    assert "missing" not in tmp_cache


def test_get_default(tmp_cache):
    assert tmp_cache.get("missing", "fallback") == "fallback"
    assert tmp_cache.get("missing") is None


def test_get_or_compute(tmp_cache):
    calls = []
    def producer():
        calls.append(1)
        return "computed"

    val1 = tmp_cache.get_or_compute("key1", producer)
    val2 = tmp_cache.get_or_compute("key1", producer)
    assert val1 == "computed"
    assert val2 == "computed"
    assert len(calls) == 1, "producer should be called only once"


def test_flush_persists_and_reloads(tmp_path):
    from cache import Cache
    p = tmp_path / "persist.json"
    c1 = Cache("persist", ttl_hours=1, path=p)
    c1.set("a", 1)
    c1.set("b", 2)
    c1.flush()
    assert p.exists()

    c2 = Cache("persist", ttl_hours=1, path=p)
    assert c2.get("a") == 1
    assert c2.get("b") == 2


def test_ttl_expiry(tmp_path):
    from cache import Cache
    p = tmp_path / "expired.json"
    # Write with very short TTL
    c1 = Cache("expired", ttl_hours=0.0001, path=p)  # 0.36 sec
    c1.set("k", "v")
    c1.flush()
    time.sleep(0.5)
    c2 = Cache("expired", ttl_hours=0.0001, path=p)
    assert c2.get("k") is None, "TTL expiry should invalidate the entry"


def test_context_manager_flushes_on_clean_exit(tmp_path):
    from cache import Cache
    p = tmp_path / "ctx.json"
    with Cache("ctx", ttl_hours=1, path=p) as c:
        c.set("k", "v")
    # After with-block exit, file should exist
    assert p.exists()
    c2 = Cache("ctx", ttl_hours=1, path=p)
    assert c2.get("k") == "v"


def test_context_manager_skips_flush_on_exception(tmp_path):
    from cache import Cache
    p = tmp_path / "ctx_err.json"
    try:
        with Cache("ctx_err", ttl_hours=1, path=p) as c:
            c.set("k", "v")
            raise ValueError("test error")
    except ValueError:
        pass
    # On exception, file should NOT be written
    assert not p.exists()


def test_clear(tmp_cache):
    tmp_cache.set("a", 1)
    tmp_cache.set("b", 2)
    assert len(tmp_cache) == 2
    tmp_cache.clear()
    assert len(tmp_cache) == 0


def test_producer_hash_invalidation(tmp_path):
    """When the producer function's source changes, cache is invalidated."""
    from cache import Cache

    def producer_v1(): return "v1"
    def producer_v2_with_different_source(): return "v2_changed_source"

    p = tmp_path / "ph.json"
    c1 = Cache("ph", ttl_hours=1, producer=producer_v1, path=p)
    c1.set("k", "old_value")
    c1.flush()

    # Reload with a DIFFERENT producer (different source -> different hash)
    c2 = Cache("ph", ttl_hours=1, producer=producer_v2_with_different_source, path=p)
    assert c2.get("k") is None, "Producer hash change should invalidate cache"
