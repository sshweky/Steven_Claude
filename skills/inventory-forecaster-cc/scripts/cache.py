"""
cache.py -- Unified disk cache layer for the inventory forecaster.

Replaces ad-hoc cache files like viewer_pos_cache.json, viewer_brand_cache.json,
open_pos_report.json, derived_category_profiles.json with one consistent API.

Each cache:
  - Lives at <skill_root>/cache/<name>.json
  - Carries metadata: ttl_seconds, written_at, producer_hash
  - Auto-invalidates on TTL expiry OR when the producer code's source hash changes
  - Supports get/set/has/clear and a context-manager pattern

Usage:
    from cache import Cache

    pos_cache = Cache("amazon_pos", ttl_hours=12)

    # Direct API
    if pos_cache.has("FF8654"):
        pos = pos_cache.get("FF8654")
    else:
        pos = fetch_pos_from_qb("FF8654")
        pos_cache.set("FF8654", pos)
    pos_cache.flush()        # persist to disk

    # Or convenience:
    pos = pos_cache.get_or_compute("FF8654", lambda: fetch_pos_from_qb("FF8654"))
    pos_cache.flush()

Why this matters:
    The skill currently has 7+ cache files with inconsistent invalidation
    policies. When build_category_profiles_from_report.py logic changed, the
    user manually appended `_v3` to the cache filename. With this module, the
    producer-hash check auto-invalidates the cache on any producer-code change.
"""

import hashlib
import inspect
import json
import os
import sys
import time
from pathlib import Path


# ─────────────────────────────────────────────────────────────────────────────
# Module-level state
# ─────────────────────────────────────────────────────────────────────────────

_SKILL_ROOT = Path(__file__).resolve().parent.parent
_CACHE_DIR  = _SKILL_ROOT / "cache"
_CACHE_DIR.mkdir(parents=True, exist_ok=True)


def _hash_producer_source(fn) -> str:
    """SHA1 of a callable's source code, for auto-invalidation."""
    try:
        src = inspect.getsource(fn)
    except (OSError, TypeError):
        return "unknown"
    return hashlib.sha1(src.encode("utf-8")).hexdigest()[:12]


# ─────────────────────────────────────────────────────────────────────────────
# Cache class
# ─────────────────────────────────────────────────────────────────────────────

class Cache:
    """Disk-backed JSON cache with TTL + producer-hash invalidation.

    Each cache stores a payload dict {key: value} plus metadata:
        _ttl_seconds:   how long entries stay valid
        _written_at:    unix timestamp of last flush
        _producer_hash: optional sha1 of the producing function's source

    On load:
      - if file is older than ttl_seconds -> treat as empty (TTL expired)
      - if producer_hash differs from current producer -> treat as empty
    """

    def __init__(self, name: str, ttl_hours: float = 24.0,
                 producer=None, path: Path | None = None):
        self.name = name
        self.ttl_seconds = ttl_hours * 3600
        self._producer = producer
        self._producer_hash = _hash_producer_source(producer) if producer else None
        self.path = Path(path) if path else (_CACHE_DIR / f"{name}.json")
        self._data: dict = {}
        self._loaded = False
        self._dirty = False
        self._load()

    # ── Internal: load + validate from disk ──────────────────────────────────
    def _load(self):
        if not self.path.exists():
            self._loaded = True
            return
        try:
            with open(self.path, encoding="utf-8") as f:
                raw = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            print(f"  [cache:{self.name}] corrupt -- starting fresh: {e}",
                  file=sys.stderr, flush=True)
            self._loaded = True
            return

        meta = raw.get("_meta", {})
        written = meta.get("written_at", 0)
        ttl     = meta.get("ttl_seconds", self.ttl_seconds)
        prod_h  = meta.get("producer_hash")

        # TTL check
        age = time.time() - written
        if age > ttl:
            print(f"  [cache:{self.name}] expired ({age:.0f}s > {ttl:.0f}s) -- starting fresh",
                  file=sys.stderr, flush=True)
            self._loaded = True
            return

        # Producer-hash check
        if (self._producer_hash is not None
                and prod_h is not None
                and prod_h != self._producer_hash):
            print(f"  [cache:{self.name}] producer code changed "
                  f"(old hash {prod_h}, new {self._producer_hash}) -- starting fresh",
                  file=sys.stderr, flush=True)
            self._loaded = True
            return

        self._data = raw.get("data", {})
        self._loaded = True

    # ── Public API ───────────────────────────────────────────────────────────
    def has(self, key: str) -> bool:
        return str(key) in self._data

    def get(self, key: str, default=None):
        return self._data.get(str(key), default)

    def set(self, key: str, value):
        self._data[str(key)] = value
        self._dirty = True

    def update(self, mapping: dict):
        for k, v in mapping.items():
            self._data[str(k)] = v
        self._dirty = True

    def get_or_compute(self, key: str, producer_fn):
        """If key is cached, return it; else call producer_fn() and cache the result."""
        k = str(key)
        if k in self._data:
            return self._data[k]
        v = producer_fn()
        self._data[k] = v
        self._dirty = True
        return v

    def clear(self):
        self._data = {}
        self._dirty = True

    def keys(self):
        return list(self._data.keys())

    def __len__(self):
        return len(self._data)

    def __contains__(self, key):
        return self.has(key)

    def flush(self):
        """Persist current state to disk. Idempotent if nothing changed."""
        if not self._dirty:
            return
        payload = {
            "_meta": {
                "ttl_seconds":   self.ttl_seconds,
                "written_at":    time.time(),
                "producer_hash": self._producer_hash,
                "cache_name":    self.name,
            },
            "data": self._data,
        }
        # Atomic write -- write to tmp, then rename
        tmp = self.path.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f)
        os.replace(tmp, self.path)
        self._dirty = False

    # ── Context manager: ensures flush on exit ───────────────────────────────
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        # Only flush if no exception occurred -- on error, leave disk untouched
        if exc_type is None:
            self.flush()
        return False  # don't suppress exceptions


# ─────────────────────────────────────────────────────────────────────────────
# Convenience: pre-built cache instances for the well-known caches in the skill.
# Existing scripts can adopt these incrementally without breaking anything.
# ─────────────────────────────────────────────────────────────────────────────

def amazon_pos_cache() -> Cache:
    """POS data for Amazon mstyles. Short TTL -- POS changes weekly."""
    return Cache("amazon_pos", ttl_hours=12)


def open_pos_cache() -> Cache:
    """Open PO data from QB report 27. ~24h TTL per VP-Q4 cadence."""
    return Cache("open_pos", ttl_hours=24)


def brand_map_cache() -> Cache:
    """Mstyle -> master brand mapping. Brand changes are rare -> 30d TTL."""
    return Cache("brand_map", ttl_hours=24 * 30)


def field_map_cache() -> Cache:
    """QB field ID -> name mapping. Schema rarely changes -> 7d TTL."""
    return Cache("qb_field_map", ttl_hours=24 * 7)


if __name__ == "__main__":
    # Smoke test
    c = Cache("test_smoke", ttl_hours=1)
    c.set("k1", {"value": 42})
    c.set("k2", [1, 2, 3])
    assert c.has("k1")
    assert c.get("k1") == {"value": 42}
    c.flush()
    print(f"OK -- wrote {len(c)} entries to {c.path}")

    # Reload, verify
    c2 = Cache("test_smoke", ttl_hours=1)
    assert c2.get("k1") == {"value": 42}
    print(f"OK -- reload returned {c2.get('k1')}")

    # Clean up
    c2.clear()
    c2.flush()
    c2.path.unlink(missing_ok=True)
    print("OK -- cleanup done")
