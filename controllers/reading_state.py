"""Reading state of a sync user over the library: which EPUBs were opened, read, are in progress.

The sync history (progress, manual statuses, books marked as read in the web UI) is matched to
the library EPUBs (LibraryIndex: hash, file name or hand-made association). Shared by the
recommendations, the personal OPDS feeds ("En cours", "Suite de vos séries") and the "hide read
books" filter of the catalog.
"""
import threading
import time
from dataclasses import dataclass, field

from controllers.background import CACHE_LIFETIME, refresh_in_background, shared_instance

FINISHED_RATIO = 0.9
HISTORY_LIMIT = 100_000


def reading_signal(status: str | None, percentage: float | None) -> str | None:
    """Classify a synced book: 'read', 'reading', 'paused', 'dnf' (abandoned) or None.

    A book is read when marked finished or read to 90 % or more; abandoned (dnf) books never count as read.
    """
    if status == 'dnf':
        return 'dnf'
    if status == 'finished' or (percentage or 0) >= FINISHED_RATIO:
        return 'read'
    if status == 'paused':
        return 'paused'
    return 'reading'


@dataclass
class ReadingState:
    """Library EPUBs of a user's sync history, most recently synced first.

    Each book: {'path', 'document', 'author', 'genres', 'series', 'series_index', 'status', 'signal',
    'percentage', 'updated_at'}.
    """
    books: list[dict] = field(default_factory=list)
    fingerprint: int = 0

    @property
    def opened_paths(self) -> set[str]:
        """EPUBs the user synced at least once (whatever their status)."""
        return {book['path'] for book in self.books}

    @property
    def read_paths(self) -> frozenset[str]:
        """EPUBs the user finished."""
        return frozenset(book['path'] for book in self.books if book['signal'] == 'read')

    def with_signal(self, signal: str) -> list[dict]:
        """Books with a given reading signal ('reading', 'read', 'paused', 'dnf'), most recent first."""
        return [book for book in self.books if book['signal'] == signal]


class ReadingStateService:
    """Compute and cache the reading state of a sync user.

    The cache entry is dropped as soon as the sync history changes (fingerprint of the progress rows, a
    single SQL query); older than CACHE_LIFETIME seconds, it keeps answering while recomputed in the background.
    """

    _instance: 'ReadingStateService | None' = None

    @classmethod
    def get_instance(cls) -> 'ReadingStateService':
        """Get or create the singleton service."""
        return shared_instance(cls)

    def __init__(self):
        self._cache: dict[str, tuple[float, ReadingState]] = {}
        self._lock = threading.Lock()

    def invalidate(self, user: str | None = None) -> None:
        """Forget the cached state of a user (of everyone when None, e.g. after a library rescan)."""
        with self._lock:
            if user is None:
                self._cache.clear()
            else:
                self._cache.pop(user, None)

    def for_user(self, user: str) -> ReadingState:
        """Reading state of a user, cached while fresh and while the sync history is unchanged."""
        # Imported here: tests reload controllers.koreader_sync
        from controllers.koreader_sync import get_sync_storage

        rows, aliases = get_sync_storage().list_progress(user, HISTORY_LIMIT)
        fingerprint = hash(tuple(
            (row['document'], row.get('status'), row.get('percentage'), row.get('updated_at'), row.get('library_path'))
            for row in rows
        ))
        def compute() -> ReadingState:
            state = ReadingState(self._match(rows, aliases), fingerprint)
            with self._lock:
                self._cache[user] = (time.time(), state)
            return state

        with self._lock:
            cached = self._cache.get(user)
        if cached and cached[1].fingerprint == fingerprint:
            # Same history, only old: kept while it is matched again in the background (library changes)
            if time.time() - cached[0] >= CACHE_LIFETIME:
                refresh_in_background(f'reading-state:{user}', compute)
            return cached[1]
        return compute()

    @staticmethod
    def _match(rows: list[dict], aliases: dict[str, list[str]]) -> list[dict]:
        """Library EPUB of each synced row (rows without one are skipped, the newest row wins per EPUB)."""
        # Imported here: library_index imports controllers.opds
        from controllers.library_index import LibraryIndex

        library = LibraryIndex.get_instance()
        books: list[dict] = []
        seen: set[str] = set()
        for row in rows:
            try:
                path = library.locate([row['document'], *aliases.get(row['document'], [])], row.get('filename'),
                                      row.get('library_path'))
                if not path or path in seen:
                    continue
                seen.add(path)
                details = library.book_meta(path)
            except OSError:
                continue
            books.append({
                'path': path,
                'document': row['document'],
                'author': details.get('author') or row.get('author'),
                'genres': details.get('genres') or [],
                'series': details.get('series'),
                'series_index': details.get('series_index'),
                'status': row.get('status'),
                'signal': reading_signal(row.get('status'), row.get('percentage')),
                'percentage': row.get('percentage'),
                'updated_at': row.get('updated_at'),
            })
        return books


__all__ = ['FINISHED_RATIO', 'ReadingState', 'ReadingStateService', 'reading_signal']
