"""Library books and their metadata, kept in SQLite (library.db): the single cache of the catalog.

One rule: `Library.sync()` lists the EPUBs of LIBRARY_DIR (walk + stat), reads only the new or modified
ones (OPF metadata, KOReader document hash), drops the missing ones, stores them in the database and
publishes a new `Snapshot`. Everything the catalog shows comes from the current snapshot: book lists,
year / author / genre / series indexes, recent books, search, document hash lookup; each index is computed
once per snapshot. The caches of the readers (reading state, recommendations) follow its `version`.

A sync runs at startup, every LIBRARY_SCAN_INTERVAL seconds (default: 4 hours, 0 to turn it off), from
the web UI button (POST /webui/library/sync) and when a book of the snapshot is missing on disk. A restart
reads the database (no EPUB opened). The database is a disposable cache: deleting it only costs one full
read of the library; it is emptied by itself when LIBRARY_DIR or the metadata rules change.
"""
import functools
import hashlib
import json
import os
import sqlite3
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field

from controllers import opds
from controllers.background import refresh_in_background
from controllers.genres import DEFAULT_GENRES_CONFIG_PATH, genre_key
from controllers.library_index import (
    description_text, estimate_print_pages, filename_md5, koreader_partial_md5, release_year, series_key,
)
from controllers.metadata_rules import DEFAULT_METADATA_CONFIG_PATH, author_key

# Bumped when the stored metadata change shape: the database is then read again from the EPUBs
SCHEMA_VERSION = 1
SCHEMA = """
CREATE TABLE IF NOT EXISTS books (
    path TEXT PRIMARY KEY,
    mtime REAL NOT NULL,
    size INTEGER NOT NULL,
    document TEXT,
    meta TEXT NOT NULL,
    extra TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


# Seconds between two automatic syncs when LIBRARY_SCAN_INTERVAL is not set (4 hours)
DEFAULT_SCAN_INTERVAL = 4 * 3600


def scan_interval() -> int:
    """Seconds between two automatic syncs (LIBRARY_SCAN_INTERVAL, default 4 hours; 0: never)."""
    try:
        return max(0, int(os.environ.get('LIBRARY_SCAN_INTERVAL', DEFAULT_SCAN_INTERVAL)))
    except ValueError:
        return DEFAULT_SCAN_INTERVAL


def database_path() -> str:
    """LIBRARY_DB_PATH, else library.db next to the sync database (KOREADER_SYNC_DB_PATH)."""
    configured = os.environ.get('LIBRARY_DB_PATH', '').strip()
    if configured:
        return configured
    sync_db = os.environ.get('KOREADER_SYNC_DB_PATH', 'koreader_sync.db')
    return os.path.join(os.path.dirname(os.path.abspath(sync_db)), 'library.db')


def epub_paths(library_dir: str) -> list[str]:
    """Every EPUB of the library (hidden files and folders left out), sorted by file name."""
    paths = []
    for root, dirs, files in os.walk(library_dir):
        dirs[:] = [name for name in dirs if not name.startswith('.')]
        for name in files:
            path = os.path.join(root, name)
            if name.endswith('.epub') and not opds.SecurityUtils.has_path_traversal(os.path.relpath(path, library_dir)):
                paths.append(path)
    return sorted(paths, key=lambda p: os.path.basename(p).lower())


def _rules_fingerprint() -> str:
    """Version of the stored metadata: schema and normalization rules (genres.toml, metadata.toml and their
    GENRES_CONFIG_PATH / METADATA_CONFIG_PATH complements)."""
    digest = hashlib.md5(str(SCHEMA_VERSION).encode('ascii'))
    for path in (DEFAULT_GENRES_CONFIG_PATH, os.environ.get('GENRES_CONFIG_PATH'),
                 DEFAULT_METADATA_CONFIG_PATH, os.environ.get('METADATA_CONFIG_PATH')):
        try:
            with open(path, 'rb') as f:
                digest.update(f.read())
        except (OSError, TypeError):
            digest.update(b'-')
    return digest.hexdigest()


def read_metadata(path: str) -> dict:
    """Stored metadata of an EPUB, from its OPF (one read): title, author, date, genres, series, series_index,
    description (plain text) and rating (0-5, None when not rated)."""
    info = opds.BookMetadata.extract_epub_info(path) or {}
    rating = info.get('rating')
    return {
        'title': info.get('title'),
        'author': info.get('author'),
        'date': info.get('date'),
        'genres': info.get('genres', []),
        'series': info.get('series'),
        'series_index': info.get('series_index'),
        'description': description_text(info.get('description')),
        # calibre:rating counts half stars (0-10); 0 means "not rated"
        'rating': min(5.0, rating / 2) if rating and rating > 0 else None,
    }


@dataclass
class Book:
    """A library EPUB: absolute path, file state, KOReader document hash and stored metadata. `extra` holds
    what is read later on demand (cover presence, print page estimate), stored too."""
    path: str
    relative: str
    mtime: float
    size: int
    document: str | None
    meta: dict
    extra: dict = field(default_factory=dict)

    @property
    def title(self) -> str:
        return self.meta.get('title') or os.path.basename(self.path)

    @property
    def author(self) -> str:
        return self.meta.get('author') or 'Unknown'

    def info(self) -> dict:
        """Book dict of the catalog lists (path, relative_path, title, author, publication_date, year, mtime)."""
        return {
            'path': self.path,
            'relative_path': self.relative,
            'title': self.title,
            'author': self.author,
            'publication_date': self.meta.get('date'),
            'year': opds.BookScanner._extract_year(self.meta.get('date')),
            'mtime': self.mtime,
        }

    def book_meta(self) -> dict:
        """Light metadata of the feeds and sync API: title, author, genres, series, series_index,
        description, year (int or None) and rating."""
        meta = {key: self.meta.get(key) for key in ('title', 'author', 'series', 'series_index', 'description', 'rating')}
        return {**meta, 'genres': self.meta.get('genres') or [], 'year': release_year(self.meta.get('date'))}


@dataclass(frozen=True)
class CatalogIndexes:
    """Year, author and genre indexes of a snapshot (paths sorted by file name)."""
    years: dict[str, list[str]]
    authors: dict[str, list[str]]
    # {author key: displayed author (its most common spelling)}
    author_names: dict[str, str]
    # {genre key: {'name': displayed genre, 'paths': [...]}}
    genres: dict[str, dict]
    # {path: (author, [genres])}
    paths: dict[str, tuple[str, list[str]]]


class Snapshot:
    """The library at one sync: its books and the indexes derived from them, each computed once."""

    def __init__(self, books: dict[str, Book], version: int):
        self.books = books
        self.version = version

    @functools.cached_property
    def paths(self) -> list[str]:
        """Every book path, sorted by file name."""
        return sorted(self.books, key=lambda p: os.path.basename(p).lower())

    @functools.cached_property
    def recent(self) -> list[str]:
        """Book paths, most recently modified first."""
        return sorted(self.books, key=lambda p: self.books[p].mtime, reverse=True)

    @functools.cached_property
    def by_key(self) -> dict[str, str]:
        """{KOReader document hash, file name MD5 or file name: path} (the first book wins)."""
        keys: dict[str, str] = {}
        for path in self.paths:
            book = self.books[path]
            for key in (book.document, filename_md5(path), os.path.basename(path)):
                if key:
                    keys.setdefault(key, path)
        return keys

    @functools.cached_property
    def catalog(self) -> CatalogIndexes:
        years: dict[str, list[str]] = {}
        groups: dict[str, dict] = {}
        genres: dict[str, dict] = {}
        paths: dict[str, tuple[str, list[str]]] = {}
        for path in self.paths:
            book = self.books[path]
            author = book.author
            book_genres = list(book.meta.get('genres') or [])
            paths[path] = (author, book_genres)
            years.setdefault(opds.BookScanner._extract_year(book.meta.get('date')), []).append(path)
            for name in book_genres:
                genres.setdefault(genre_key(name), {'name': name, 'paths': []})['paths'].append(path)
            # Variants differing only by case, accents, punctuation or word order are one author
            group = groups.setdefault(author_key(author) or author, {'paths': [], 'names': {}})
            group['paths'].append(path)
            group['names'][author] = group['names'].get(author, 0) + 1
        names = {key: max(group['names'], key=lambda name: (group['names'][name], name)) for key, group in groups.items()}
        authors = {names[key]: group['paths'] for key, group in groups.items()}
        return CatalogIndexes(years, authors, names, genres, paths)

    @functools.cached_property
    def series(self) -> dict[str, list[tuple[int | float | None, str]]]:
        """{series key: [(position or None, path)]}."""
        index: dict[str, list[tuple[int | float | None, str]]] = {}
        for path in self.paths:
            meta = self.books[path].meta
            if series_key(meta.get('series')):
                index.setdefault(series_key(meta['series']), []).append((meta.get('series_index'), path))
        return index

    @functools.cached_property
    def titles(self) -> dict[str, list[str]]:
        """{title key: [path]}."""
        index: dict[str, list[str]] = {}
        for path in self.paths:
            key = series_key(self.books[path].meta.get('title'))
            if key:
                index.setdefault(key, []).append(path)
        return index


class Library:
    """The library of LIBRARY_DIR and its database: current snapshot, syncs (one at a time)."""

    _instance: 'Library | None' = None
    _instance_lock = threading.Lock()

    @classmethod
    def get_instance(cls) -> 'Library':
        """The library of the current LIBRARY_DIR and database (tests change them between classes)."""
        with cls._instance_lock:
            instance = cls._instance
            if instance is None or instance.library_dir != opds.LIBRARY_DIR or instance.db_path != database_path():
                instance = cls._instance = cls(opds.LIBRARY_DIR, database_path())
            return instance

    def __init__(self, library_dir: str, db_path: str):
        self.library_dir = library_dir
        self.db_path = db_path
        self.last_sync: dict | None = None
        self._snapshot: Snapshot | None = None
        self._load_lock = threading.Lock()
        self._sync_lock = threading.Lock()

    @contextmanager
    def _connection(self):
        conn = sqlite3.connect(self.db_path, timeout=30)
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    @property
    def snapshot(self) -> Snapshot:
        """Current snapshot: read from the database on first use, synced first when the database is empty."""
        if not self._loaded().books and self.last_sync is None:
            self.sync()
        return self._snapshot

    def _loaded(self) -> Snapshot:
        if self._snapshot is None:
            with self._load_lock:
                if self._snapshot is None:
                    self._snapshot = self._load()
        return self._snapshot

    def _load(self) -> Snapshot:
        root = os.path.realpath(self.library_dir)
        with self._connection() as conn:
            conn.executescript(SCHEMA)
            settings = dict(conn.execute('SELECT key, value FROM settings'))
            wanted = {'library_dir': root, 'rules': _rules_fingerprint()}
            if any(settings.get(key) != value for key, value in wanted.items()):
                # Another library or other rules: the stored metadata are read again
                conn.execute('DELETE FROM books')
                conn.executemany('INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)', wanted.items())
            rows = conn.execute('SELECT path, mtime, size, document, meta, extra FROM books').fetchall()
        books = {}
        for relative, mtime, size, document, meta, extra in rows:
            path = os.path.join(self.library_dir, relative)
            books[path] = Book(path, relative, mtime, size, document, json.loads(meta), json.loads(extra))
        return Snapshot(books, 1)

    def sync(self) -> dict:
        """Bring the database and the snapshot up to date with the files: {added, updated, removed, total,
        seconds}. Only the new or modified EPUBs are opened."""
        self._loaded()
        with self._sync_lock:
            started = time.monotonic()
            current = self._snapshot
            known = current.books
            found: dict[str, tuple[float, int]] = {}
            for path in epub_paths(self.library_dir):
                try:
                    stat = os.stat(path)
                except OSError:
                    continue
                found[path] = (stat.st_mtime, stat.st_size)
            changed = [path for path, state in found.items()
                       if path not in known or (known[path].mtime, known[path].size) != state]
            removed = [path for path in known if path not in found]
            books = {path: book for path, book in known.items() if path in found}
            for path in changed:
                mtime, size = found[path]
                relative = os.path.relpath(path, self.library_dir)
                books[path] = Book(path, relative, mtime, size, koreader_partial_md5(path), read_metadata(path))
            if changed or removed:
                with self._connection() as conn:
                    conn.executemany('DELETE FROM books WHERE path = ?', [(known[path].relative,) for path in removed])
                    conn.executemany(
                        'INSERT OR REPLACE INTO books (path, mtime, size, document, meta, extra) VALUES (?, ?, ?, ?, ?, ?)',
                        [(books[p].relative, books[p].mtime, books[p].size, books[p].document,
                          json.dumps(books[p].meta), '{}') for p in changed],
                    )
                self._snapshot = Snapshot(books, current.version + 1)
            added = sum(1 for path in changed if path not in known)
            self.last_sync = {
                'added': added, 'updated': len(changed) - added, 'removed': len(removed), 'total': len(books),
                'seconds': round(time.monotonic() - started, 3), 'at': int(time.time()),
            }
            return self.last_sync

    def sync_later(self) -> None:
        """Sync in the background (a book of the snapshot is missing on disk)."""
        refresh_in_background('library-sync', self.sync)

    def book(self, path: str) -> Book | None:
        """Book of the snapshot at this path (absolute), None when it is not in the library."""
        return self.snapshot.books.get(os.path.join(self.library_dir, os.path.relpath(path, self.library_dir)))

    def details(self, path: str) -> dict:
        """Cover presence and estimated print pages of a library EPUB, read once then stored."""
        book = self.book(path)
        if book is not None and 'page_count' in book.extra:
            return book.extra
        cover, _ = opds.BookMetadata.extract_epub_cover(path)
        extra = {'has_cover': cover is not None, 'page_count': estimate_print_pages(path)}
        if book is not None:
            book.extra = extra
            with self._connection() as conn:
                conn.execute('UPDATE books SET extra = ? WHERE path = ? AND mtime = ?',
                             (json.dumps(extra), book.relative, book.mtime))
        return extra

    def start_auto_sync(self) -> None:
        """Sync now, then every LIBRARY_SCAN_INTERVAL seconds, in a daemon thread (started by server.py)."""
        def run() -> None:
            while True:
                try:
                    self.sync()
                except Exception as exc:
                    print(f'Library sync failed: {exc}')
                interval = scan_interval()
                if not interval:
                    return
                time.sleep(interval)

        threading.Thread(target=run, name='library-sync', daemon=True).start()


def forget(relative_paths: list[str]) -> None:
    """Drop books from the database (paths relative to LIBRARY_DIR), read again at the next start: EPUBs
    rewritten with their modification time kept (python -m controllers.metadata_fix apply --write)."""
    path = database_path()
    if not relative_paths or not os.path.exists(path):
        return
    with sqlite3.connect(path, timeout=30) as conn:
        conn.executemany('DELETE FROM books WHERE path = ?', [(relative,) for relative in relative_paths])


def snapshot() -> Snapshot:
    """Current snapshot of the library."""
    return Library.get_instance().snapshot


__all__ = ['Book', 'CatalogIndexes', 'Library', 'Snapshot', 'database_path', 'epub_paths', 'forget', 'read_metadata',
           'scan_interval', 'snapshot']
