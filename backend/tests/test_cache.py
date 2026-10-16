"""Cache behaviour and the UTC timestamp contract."""

from datetime import datetime, timedelta, timezone

import pytest

from app import cache


@pytest.fixture(autouse=True)
def memory_store(monkeypatch):
    """Force the in-process store so these tests need no Redis server."""
    monkeypatch.setattr(cache.settings, "redis_url", None)
    cache.reset_store()
    yield
    cache.reset_store()


def test_memory_store_roundtrip():
    store = cache.MemoryStore()
    store.set("k", "v", ttl=60)
    assert store.get("k") == "v"


def test_memory_store_expires():
    store = cache.MemoryStore()
    store.set("k", "v", ttl=0)
    assert store.get("k") is None


def test_memory_counters_increment():
    store = cache.MemoryStore()
    assert store.get_int("c") is None
    assert store.incr("c") == 1
    assert store.incr("c") == 2
    assert store.get_int("c") == 2


def test_memory_store_is_bounded(monkeypatch):
    """A burst of distinct viewers must not grow the store without limit.

    Cache keys embed the viewer id, so an unbounded store grows with the number
    of people browsing. On a small instance that is a slow memory leak that ends
    in an OOM kill — the "crashes as new users join" failure.
    """
    store = cache.MemoryStore()
    monkeypatch.setattr(store, "MAX_ENTRIES", 50)
    # Defeat the sweep interval so the ceiling is what does the pruning.
    monkeypatch.setattr(store, "SWEEP_INTERVAL_SECONDS", 10_000)

    for index in range(500):
        store.set(f"viewer:{index}", "x", ttl=3600)

    assert len(store._values) <= 50
    # The most recent writes are the ones kept.
    assert store.get("viewer:499") == "x"


def test_memory_store_prunes_expired_entries(monkeypatch):
    store = cache.MemoryStore()
    monkeypatch.setattr(store, "SWEEP_INTERVAL_SECONDS", 0)
    store.set("old", "x", ttl=0)
    store.set("new", "y", ttl=60)

    # A write triggers a sweep, which drops the expired key.
    store.set("trigger", "z", ttl=60)
    assert "old" not in store._values
    assert store.get("new") == "y"


def test_get_or_set_computes_once():
    calls = []

    def produce():
        calls.append(1)
        return {"value": len(calls)}

    first = cache.get_or_set("ns", 60, ("a",), produce)
    second = cache.get_or_set("ns", 60, ("a",), produce)

    assert first == {"value": 1}
    assert second == {"value": 1}
    assert len(calls) == 1


def test_bump_invalidates_namespace():
    calls = []

    def produce():
        calls.append(1)
        return {"value": len(calls)}

    cache.get_or_set("ns", 60, ("a",), produce)
    cache.bump("ns")
    after = cache.get_or_set("ns", 60, ("a",), produce)

    assert after == {"value": 2}
    assert len(calls) == 2


def test_bump_leaves_other_namespaces_alone():
    cache.set_json("keep", {"v": 1}, 60, "a")
    cache.set_json("drop", {"v": 1}, 60, "a")

    cache.bump("drop")

    assert cache.get_json("keep", "a") == {"v": 1}
    assert cache.get_json("drop", "a") is None


def test_cache_can_be_disabled(monkeypatch):
    monkeypatch.setattr(cache.settings, "cache_enabled", False)
    cache.set_json("ns", {"v": 1}, 60, "a")
    assert cache.get_json("ns", "a") is None


def test_health_reports_cache_backend(client):
    body = client.get("/api/health").json()
    assert body["cache"] in {"memory", "redis"}


def test_timestamps_are_utc_with_offset(client, make_user):
    headers, _ = make_user("clock")
    created = client.post("/api/posts", json={"body": "hello"}, headers=headers).json()["created_at"]

    # A naive timestamp would be parsed as local time by the browser, which is
    # what made a fresh post read as "1h ago".
    assert created.endswith("Z") or "+00:00" in created
    parsed = datetime.fromisoformat(created.replace("Z", "+00:00"))
    assert parsed.tzinfo is not None
    assert abs(parsed - datetime.now(timezone.utc)) < timedelta(minutes=1)


def test_just_created_post_is_not_backdated(client, make_user):
    headers, _ = make_user("fresh")
    created = client.post("/api/posts", json={"body": "brand new"}, headers=headers).json()

    listed = client.get("/api/posts", headers=headers).json()["items"][0]
    assert listed["id"] == created["id"]
    assert listed["created_at"] == created["created_at"]
