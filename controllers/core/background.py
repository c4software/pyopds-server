"""Shared services and background work: the server answers each request in its own thread.

`shared_instance` creates a service (library, indexes, reader caches) once even under concurrent requests;
`refresh_in_background` runs a task (a library sync) in a daemon thread, one at a time per key.
"""
import threading
from collections.abc import Callable

_running: set[str] = set()
_running_lock = threading.Lock()
# Creation of the shared services
_instances_lock = threading.RLock()


def refresh_in_background(key: str, task: Callable[[], object]) -> None:
    """Run `task` in a daemon thread, unless the task `key` is already running."""
    with _running_lock:
        if key in _running:
            return
        _running.add(key)

    def run() -> None:
        try:
            task()
        except Exception as exc:
            print(f'Background task {key} failed: {exc}')
        finally:
            with _running_lock:
                _running.discard(key)

    threading.Thread(target=run, name=key, daemon=True).start()


def shared_instance(cls):
    """The single instance of a service class (`cls._instance`), created once even by concurrent requests."""
    if cls._instance is None:
        with _instances_lock:
            if cls._instance is None:
                cls._instance = cls()
    return cls._instance


__all__ = ['refresh_in_background', 'shared_instance']
