"""Response caching for hot read endpoints.

Reads are cached as opaque JSON keyed by a logical namespace plus the caller's
identity. Writes never delete keys one by one; they bump a per-namespace
*version* counter, which is folded into every key. A stale entry is therefore
unreachable the moment the version changes and simply expires later, so there is
no key-scanning or invalidation race to get wrong.

Redis is used when `settings.redis_url` is set and reachable. If it is absent or
goes away mid-flight the store degrades to an in-process dictionary, so the app
and the test suite keep working without a Redis server. The two stores are not
shared, so mixing them across processes is fine only because the in-process
fallback is used for local dev and tests.
"""

from __future__ import annotations

import json
import threading
import time
from typing import Any, Protocol

from .config import settings


class CacheStore(Protocol):
    def get(self, key: str) -> str | None: ...
    def set(self, key: str, value: str, ttl: int) -> None: ...
    def incr(self, key: str) -> int: ...
    def get_int(self, key: str) -> int | None: ...
    def delete(self, key: str) -> None: ...


class MemoryStore:
    """Thread-safe TTL dictionary used when Redis is not configured or is down."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._values: dict[str, tuple[str, float]] = {}
        self._counters: dict[str, int] = {}

    def _live(self, key: str) -> str | None:
        entry = self._values.get(key)
        if entry is None:
            return None
        value, expires_at = entry
        if expires_at < time.monotonic():
            self._values.pop(key, None)
            return None
        return value

    def get(self, key: str) -> str | None:
        with self._lock:
            return self._live(key)

    def set(self, key: str, value: str, ttl: int) -> None:
        with self._lock:
            self._values[key] = (value, time.monotonic() + ttl)

    def incr(self, key: str) -> int:
        with self._lock:
            self._counters[key] = self._counters.get(key, 0) + 1
            return self._counters[key]

    def get_int(self, key: str) -> int | None:
        with self._lock:
            return self._counters.get(key)

    def delete(self, key: str) -> None:
        with self._lock:
            self._values.pop(key, None)
            self._counters.pop(key, None)


class RedisStore:
    """Thin adapter over redis-py. Values are strings; versions are integers."""

    def __init__(self, client: Any) -> None:
        self._client = client

    def get(self, key: str) -> str | None:
        value = self._client.get(key)
        return value.decode() if isinstance(value, bytes) else value

    def set(self, key: str, value: str, ttl: int) -> None:
        self._client.set(key, value, ex=ttl)

    def incr(self, key: str) -> int:
        return int(self._client.incr(key))

    def get_int(self, key: str) -> int | None:
        value = self._client.get(key)
        return None if value is None else int(value)

    def delete(self, key: str) -> None:
        self._client.delete(key)


_store: CacheStore | None = None
_store_lock = threading.Lock()
_redis_failed = False


def _build_store() -> CacheStore:
    """Prefer Redis; fall back to memory if it is unconfigured or unreachable."""
    global _redis_failed
    url = settings.redis_url
    if url:
        try:
            import redis  # imported lazily so Redis stays an optional dependency

            client = redis.Redis.from_url(url, socket_connect_timeout=0.5, socket_timeout=0.5)
            client.ping()
            return RedisStore(client)
        except Exception:  # pragma: no cover - depends on the local environment
            _redis_failed = True
    return MemoryStore()


def get_store() -> CacheStore:
    global _store
    if _store is None:
        with _store_lock:
            if _store is None:
                _store = _build_store()
    return _store


def reset_store() -> None:
    """Drop the cached store so the next call rebuilds it. Used by tests."""
    global _store, _redis_failed
    with _store_lock:
        _store = None
        _redis_failed = False


def backend_name() -> str:
    return 'redis' if isinstance(get_store(), RedisStore) else 'memory'


def _version_key(namespace: str) -> str:
    return f'pulse:v:{namespace}'


def version(namespace: str) -> int:
    store = get_store()
    key = _version_key(namespace)
    current = store.get_int(key)
    if current is None:
        # First use in this process. Record it so writers bump from a known base.
        return store.incr(key)
    return current


def bump(*namespaces: str) -> None:
    """Invalidate every cache entry in the given namespaces."""
    store = get_store()
    for namespace in namespaces:
        store.incr(_version_key(namespace))


def make_key(namespace: str, *parts: Any) -> str:
    versioned = version(namespace)
    joined = ':'.join(str(part) for part in parts)
    return f'pulse:c:{namespace}:{versioned}:{joined}'


def get_json(namespace: str, *parts: Any) -> Any | None:
    if not settings.cache_enabled:
        return None
    raw = get_store().get(make_key(namespace, *parts))
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except ValueError:
        return None


def set_json(namespace: str, value: Any, ttl: int, *parts: Any) -> None:
    if not settings.cache_enabled:
        return
    get_store().set(make_key(namespace, *parts), json.dumps(value), ttl)


def get_or_set(namespace: str, ttl: int, key_parts: tuple, produce):
    """Return the cached value for `key_parts`, computing and storing it on a miss.

    `produce` must return JSON-serialisable data (a `model_dump(mode="json")`
    dict or a plain list), which the endpoint's `response_model` validates back
    into the response type.
    """
    hit = get_json(namespace, *key_parts)
    if hit is not None:
        return hit
    value = produce()
    set_json(namespace, value, ttl, *key_parts)
    return value
