"""Freshness of the in-memory caches (library indexes, recent books, reading states, recommendations).

A cache is built once in the request that first needs it; past its lifetime it keeps answering while a
daemon thread rebuilds it (stale-while-revalidate): a reader coming back after a pause never waits for a
rebuild of the library indexes. One build at a time per cache. `warm_up` builds the catalog indexes at
startup, in the background, so the first visitor does not pay for them either.
"""
import threading
import time
from collections.abc import Callable

# Lifetime of the library caches (indexes, recent books, reading states, recommendations): past it, they are
# rebuilt in the background. A synced reading refreshes its reader's caches at once, « Rafraîchir » all of them
CACHE_LIFETIME = 3600

_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()
_running: set[str] = set()
# Creation of the shared services (the server answers each request in its own thread)
_instances_lock = threading.RLock()


def _lock(key: str) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(key, threading.Lock())


def refresh_in_background(key: str, rebuild: Callable[[], None]) -> None:
    """Run `rebuild` in a daemon thread, unless the cache `key` is already being rebuilt."""
    with _locks_guard:
        if key in _running:
            return
        _running.add(key)

    def run() -> None:
        try:
            with _lock(key):
                rebuild()
        except Exception as exc:
            # The stale cache keeps answering; the next request past its lifetime tries again
            print(f'Background refresh of {key} failed: {exc}')
        finally:
            with _locks_guard:
                _running.discard(key)

    threading.Thread(target=run, name=f'refresh-{key}', daemon=True).start()


def keep_fresh(key: str, built_at: Callable[[], float], ttl: float, rebuild: Callable[[], None]) -> None:
    """Make the cache `key` usable: built now when it never was (one build at a time, the other requests
    wait for it), rebuilt in the background once older than `ttl` seconds. `built_at` gives the time of
    its last build (0 when never built)."""
    if not built_at():
        with _lock(key):
            if not built_at():
                rebuild()
    elif time.time() - built_at() >= ttl:
        refresh_in_background(key, rebuild)


def shared_instance(cls):
    """The single instance of a service class (`cls._instance`), created once even by concurrent requests."""
    if cls._instance is None:
        with _instances_lock:
            if cls._instance is None:
                cls._instance = cls()
    return cls._instance


def warm_up(tasks: list[Callable[[], None]]) -> None:
    """Build caches at startup in a daemon thread, one after the other (failures only logged)."""
    def run() -> None:
        for task in tasks:
            try:
                task()
            except Exception as exc:
                print(f'Cache warm-up failed: {exc}')

    threading.Thread(target=run, name='warm-up', daemon=True).start()


__all__ = ['CACHE_LIFETIME', 'keep_fresh', 'refresh_in_background', 'shared_instance', 'warm_up']
