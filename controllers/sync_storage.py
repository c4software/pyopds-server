"""SQLite storage for reading sync: KOSync progress and the CrossPoint Sync extensions.

Semantics follow crosspoint-sync (https://github.com/crosspoint-reader/crosspoint-sync):
one progress row per device, newest row wins, progress history for statistics,
document aliases for merged copies of a book, device statistics and clippings.
"""
import contextlib
import datetime
import hashlib
import hmac
import json
import os
import sqlite3
import time
from collections.abc import Iterator

KOREADER_SYNC_DB_PATH = os.environ.get('KOREADER_SYNC_DB_PATH', 'koreader_sync.db')

FINISHED_PERCENTAGE = 0.98
AUTO_PAUSE_SECONDS = 30 * 86400
CHANGES_MAX_BYTES = 8192
CLIPPINGS_ALL_LIMIT = 5000

# Columns are added with ALTER TABLE when missing, so every definition needs a default or NULL
DOCUMENT_COLUMNS = {
    'title': 'TEXT',
    'author': 'TEXT',
    'filename': 'TEXT',
    'filesize': 'INTEGER',
    'status': 'TEXT',
    'status_at': 'INTEGER',
    'pause_reason': 'TEXT',
    'start_date': 'INTEGER',
    'finished_date': 'INTEGER',
    'cover_url': 'TEXT',
    'page_count': 'INTEGER',
    # Library EPUB (relative to LIBRARY_DIR) chosen by hand for a document the hashes do not match
    'library_path': 'TEXT',
    # Sent by CrossPoint readers with their progress (metadata of the EPUB on the device)
    'series': 'TEXT',
    'series_index': 'REAL',
    'isbn': 'TEXT',
    'meta_manual': 'INTEGER NOT NULL DEFAULT 0',
    'updated_at': 'INTEGER NOT NULL DEFAULT 0',
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    username TEXT PRIMARY KEY,
    password_md5 TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS progress (
    user TEXT NOT NULL,
    document TEXT NOT NULL,
    device_id TEXT NOT NULL,
    device TEXT NOT NULL DEFAULT '',
    percentage REAL NOT NULL,
    progress TEXT NOT NULL,
    position TEXT,
    updated_at INTEGER NOT NULL,
    change_seq INTEGER NOT NULL DEFAULT 0,
    server_change_seq INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (user, document, device_id)
);
CREATE INDEX IF NOT EXISTS idx_progress_latest ON progress (user, document, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_progress_change_seq ON progress (user, change_seq);
CREATE TABLE IF NOT EXISTS progress_log (
    user TEXT NOT NULL,
    document TEXT NOT NULL,
    device_id TEXT NOT NULL,
    percentage REAL NOT NULL,
    at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_progress_log ON progress_log (user, document, at);
CREATE TABLE IF NOT EXISTS counters (
    name TEXT PRIMARY KEY,
    value INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS documents (
    user TEXT NOT NULL,
    document TEXT NOT NULL,
    PRIMARY KEY (user, document)
);
CREATE TABLE IF NOT EXISTS document_aliases (
    user TEXT NOT NULL,
    alias TEXT NOT NULL,
    document TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    merge_stats INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (user, alias)
);
CREATE INDEX IF NOT EXISTS idx_document_aliases_doc ON document_aliases (user, document);
CREATE TABLE IF NOT EXISTS stats_device_global (
    user TEXT NOT NULL,
    device_id TEXT NOT NULL,
    device TEXT NOT NULL DEFAULT '',
    payload TEXT NOT NULL,
    updated_at INTEGER NOT NULL,
    PRIMARY KEY (user, device_id)
);
CREATE TABLE IF NOT EXISTS stats_device_book (
    user TEXT NOT NULL,
    device_id TEXT NOT NULL,
    document TEXT NOT NULL,
    payload TEXT NOT NULL,
    updated_at INTEGER NOT NULL,
    PRIMARY KEY (user, device_id, document)
);
CREATE INDEX IF NOT EXISTS idx_stats_book_doc ON stats_device_book (user, document);
CREATE TABLE IF NOT EXISTS stats_device_day (
    user TEXT NOT NULL,
    device_id TEXT NOT NULL,
    date TEXT NOT NULL,
    seconds INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    PRIMARY KEY (user, device_id, date)
);
CREATE TABLE IF NOT EXISTS clippings (
    user TEXT NOT NULL,
    document TEXT NOT NULL,
    id TEXT NOT NULL,
    spine_index INTEGER NOT NULL DEFAULT 0,
    start_page INTEGER NOT NULL DEFAULT 0,
    end_page INTEGER NOT NULL DEFAULT 0,
    page_count INTEGER NOT NULL DEFAULT 1,
    start_word INTEGER NOT NULL DEFAULT 0,
    end_word INTEGER NOT NULL DEFAULT 0,
    word_count INTEGER NOT NULL DEFAULT 0,
    paragraph_index INTEGER,
    chapter_title TEXT NOT NULL DEFAULT '',
    text TEXT NOT NULL DEFAULT '',
    note TEXT,
    color TEXT,
    created_at INTEGER NOT NULL DEFAULT 0,
    deleted INTEGER NOT NULL DEFAULT 0,
    updated_at INTEGER NOT NULL,
    layout_signature INTEGER NOT NULL DEFAULT 0,
    start_offset INTEGER,
    end_offset INTEGER,
    revision INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (user, document, id)
);
CREATE INDEX IF NOT EXISTS idx_clippings_since ON clippings (user, document, updated_at, id);
CREATE INDEX IF NOT EXISTS idx_clippings_revision ON clippings (user, document, revision);
CREATE TABLE IF NOT EXISTS bookmarks (
    user TEXT NOT NULL,
    document TEXT NOT NULL,
    id TEXT NOT NULL,
    xpath TEXT NOT NULL DEFAULT '',
    percentage REAL NOT NULL DEFAULT 0,
    summary TEXT NOT NULL DEFAULT '',
    spine_index INTEGER,
    paragraph_count INTEGER,
    paragraph_pos INTEGER,
    deleted INTEGER NOT NULL DEFAULT 0,
    updated_at INTEGER NOT NULL,
    PRIMARY KEY (user, document, id)
);
CREATE INDEX IF NOT EXISTS idx_bookmarks_since ON bookmarks (user, document, updated_at, id);
"""

# Tables holding per-document data, cleared when a book is removed
DOCUMENT_TABLES = ('stats_device_book', 'clippings', 'bookmarks', 'progress_log', 'progress', 'documents')
# Tables holding per-user data, cleared when the account data is deleted
USER_TABLES = (
    'stats_device_global', 'stats_device_book', 'stats_device_day', 'clippings', 'bookmarks',
    'document_aliases', 'documents', 'progress', 'progress_log',
)


def md5_hex(value: str) -> str:
    """Lowercase hex MD5 of a UTF-8 string."""
    return hashlib.md5(value.encode('utf-8')).hexdigest()


def now_seconds() -> int:
    """Current unix time in whole seconds (the unit used by every sync timestamp)."""
    return int(time.time())


def completed_sql(user_column: str, document_column: str) -> str:
    """SQL condition: a device reported the book (or a merged copy) as completed."""
    return f"""EXISTS (
        SELECT 1 FROM stats_device_book s
        WHERE s.user = {user_column} AND json_extract(s.payload, '$.completed') = 1
          AND (s.document = {document_column} OR s.document IN (
              SELECT a.alias FROM document_aliases a
              WHERE a.user = {user_column} AND a.document = {document_column} AND a.merge_stats = 1)))"""


NEWEST_DEVICE_SQL = """(
    SELECT p2.device_id FROM progress p2
    WHERE p2.user = p.user AND p2.document = p.document
    ORDER BY p2.updated_at DESC, p2.device_id ASC LIMIT 1)"""


class SyncStorage:
    """SQLite-backed storage shared by the KOSync and CrossPoint Sync controllers."""

    def __init__(self, db_path: str = KOREADER_SYNC_DB_PATH):
        self.db_path = db_path
        self._ensure_schema()

    @contextlib.contextmanager
    def _get_connection(self) -> Iterator[sqlite3.Connection]:
        """Open a connection, commit on success, roll back on error, always close."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    # ------------------------------------------------------------------ schema

    def _ensure_schema(self) -> None:
        """Create the tables and migrate databases written by older versions."""
        with self._get_connection() as conn:
            conn.executescript(SCHEMA)
            existing = {row['name'] for row in conn.execute('PRAGMA table_info(documents)')}
            for column, definition in DOCUMENT_COLUMNS.items():
                if column not in existing:
                    conn.execute(f'ALTER TABLE documents ADD COLUMN {column} {definition}')
            self._migrate_sync_records(conn)

    @staticmethod
    def _migrate_sync_records(conn: sqlite3.Connection) -> None:
        """Move the former single-row-per-book table to per-device progress rows."""
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'sync_records'").fetchone():
            return
        conn.execute(
            """
            INSERT OR IGNORE INTO progress (user, document, device_id, device, percentage, progress, updated_at)
            SELECT user, document,
                   COALESCE(NULLIF(device_id, ''), COALESCE(device, '')),
                   COALESCE(device, ''), MIN(MAX(COALESCE(percentage, 0), 0), 1), COALESCE(progress, ''),
                   CAST(COALESCE(timestamp, 0) AS INTEGER)
            FROM sync_records
            """
        )
        if not conn.execute('SELECT 1 FROM progress_log LIMIT 1').fetchone():
            conn.execute(
                'INSERT INTO progress_log (user, document, device_id, percentage, at) '
                'SELECT user, document, device_id, percentage, updated_at FROM progress'
            )
        conn.execute('DROP TABLE sync_records')

    @staticmethod
    def _next_counter(conn: sqlite3.Connection, name: str) -> int:
        """Increment and return a server-wide monotonic counter."""
        conn.execute(
            'INSERT INTO counters (name, value) VALUES (?, 1) '
            'ON CONFLICT (name) DO UPDATE SET value = value + 1',
            (name,),
        )
        return conn.execute('SELECT value FROM counters WHERE name = ?', (name,)).fetchone()['value']

    # ------------------------------------------------------------------- users

    @staticmethod
    def _find_user(conn: sqlite3.Connection, username: str) -> sqlite3.Row | None:
        row = conn.execute('SELECT username, password_md5 FROM users WHERE username = ?', (username,)).fetchone()
        if row is None:
            row = conn.execute('SELECT username, password_md5 FROM users WHERE username = ?', (username.lower(),)).fetchone()
        return row

    def create_user(self, username: str, password_md5: str) -> bool:
        """Register a user; returns False when the name is already taken."""
        with self._get_connection() as conn:
            if conn.execute('SELECT 1 FROM users WHERE lower(username) = lower(?)', (username,)).fetchone():
                return False
            conn.execute('INSERT INTO users (username, password_md5) VALUES (?, ?)', (username, password_md5.lower()))
        return True

    def authenticate(self, username: str, key: str) -> str | None:
        """Return the stored username when the key (MD5 of the password, or the raw password) matches."""
        with self._get_connection() as conn:
            row = self._find_user(conn, username)
        if row is None:
            return None
        stored = row['password_md5'].lower()
        for candidate in (key.lower(), md5_hex(key)):
            if hmac.compare_digest(stored.encode(), candidate.encode()):
                return row['username']
        return None

    def set_password(self, user: str, password_md5: str) -> None:
        """Replace the user's key (MD5 of the new password)."""
        with self._get_connection() as conn:
            conn.execute('UPDATE users SET password_md5 = ? WHERE username = ?', (password_md5.lower(), user))

    def delete_user_data(self, user: str, delete_account: bool) -> None:
        """Delete every synced row of a user, and the account itself when asked."""
        with self._get_connection() as conn:
            for table in USER_TABLES:
                conn.execute(f'DELETE FROM {table} WHERE user = ?', (user,))
            if delete_account:
                conn.execute('DELETE FROM users WHERE username = ?', (user,))

    # ----------------------------------------------------------------- aliases

    @staticmethod
    def _resolve(conn: sqlite3.Connection, user: str, document: str) -> str:
        row = conn.execute('SELECT document FROM document_aliases WHERE user = ? AND alias = ?', (user, document)).fetchone()
        return row['document'] if row else document

    def resolve_document(self, user: str, document: str) -> str:
        """Return the canonical document a (possibly merged) hash points to."""
        with self._get_connection() as conn:
            return self._resolve(conn, user, document)

    @staticmethod
    def _aliases_by_document(conn: sqlite3.Connection, user: str) -> dict[str, list[str]]:
        aliases: dict[str, list[str]] = {}
        for row in conn.execute('SELECT alias, document FROM document_aliases WHERE user = ? ORDER BY alias', (user,)):
            aliases.setdefault(row['document'], []).append(row['alias'])
        return aliases

    @staticmethod
    def _has_progress(conn: sqlite3.Connection, user: str, document: str) -> bool:
        return conn.execute('SELECT 1 FROM progress WHERE user = ? AND document = ? LIMIT 1', (user, document)).fetchone() is not None

    def _adopt_filename_twin(self, conn: sqlite3.Connection, user: str, document: str, filename: str | None) -> str:
        """Alias a new hash to the same book already synced under its other hash.

        KOReader identifies a book either by a partial MD5 of its content or by the MD5
        of its file name; both hashes of one book end up on the same progress.
        """
        resolved = self._resolve(conn, user, document)
        if resolved != document or self._has_progress(conn, user, document):
            return resolved
        twin = None
        if filename:
            named = self._resolve(conn, user, md5_hex(filename))
            if named != document and self._has_progress(conn, user, named):
                twin = named
        if twin is None:
            for row in conn.execute('SELECT document, filename FROM documents WHERE user = ? AND filename IS NOT NULL', (user,)):
                if md5_hex(row['filename']) == document and row['document'] != document and self._has_progress(conn, user, row['document']):
                    twin = row['document']
                    break
        if twin is None:
            return document
        conn.execute(
            'INSERT OR REPLACE INTO document_aliases (user, alias, document, created_at, merge_stats) VALUES (?, ?, ?, ?, 1)',
            (user, document, twin, now_seconds()),
        )
        return twin

    # ---------------------------------------------------------------- progress

    @staticmethod
    def _newest_row(conn: sqlite3.Connection, user: str, document: str) -> sqlite3.Row | None:
        return conn.execute(
            'SELECT * FROM progress WHERE user = ? AND document = ? ORDER BY updated_at DESC, device_id ASC LIMIT 1',
            (user, document),
        ).fetchone()

    @staticmethod
    def _auto_unpause(conn: sqlite3.Connection, user: str, document: str, at: int) -> None:
        conn.execute(
            "UPDATE documents SET status = NULL, status_at = ?, pause_reason = NULL "
            "WHERE user = ? AND document = ? AND status = 'paused' AND COALESCE(status_at, 0) <= ?",
            (at, user, document, at),
        )

    @staticmethod
    def _auto_finish(conn: sqlite3.Connection, user: str, document: str, at: int) -> None:
        conn.execute(
            "UPDATE documents SET status = NULL, status_at = ? "
            "WHERE user = ? AND document = ? AND status = 'reading' AND COALESCE(status_at, 0) <= ?",
            (at, user, document, at),
        )

    @staticmethod
    def _auto_pause(conn: sqlite3.Connection, user: str, document: str | None = None) -> None:
        """Pause books without any progress change for 30 days (unless finished)."""
        now = now_seconds()
        conn.execute(
            f"""
            INSERT INTO documents (user, document, status, status_at, pause_reason, updated_at)
            SELECT l.user, l.document, 'paused', :now, 'auto', :now FROM progress_log l
            WHERE l.user = :user AND (:document IS NULL OR l.document = :document)
            GROUP BY l.user, l.document
            HAVING MAX(l.at) < :cutoff
               AND NOT {completed_sql('l.user', 'l.document')}
               AND COALESCE((SELECT p.percentage FROM progress p WHERE p.user = l.user AND p.document = l.document
                             ORDER BY p.updated_at DESC, p.device_id ASC LIMIT 1), 0) < {FINISHED_PERCENTAGE}
            ON CONFLICT (user, document) DO UPDATE SET
                status = 'paused', status_at = excluded.status_at, pause_reason = 'auto'
            WHERE documents.status IS NULL OR documents.status = 'reading'
            """,
            {'now': now, 'user': user, 'document': document, 'cutoff': now - AUTO_PAUSE_SECONDS},
        )

    @staticmethod
    def _upsert_metadata(conn: sqlite3.Connection, user: str, document: str, metadata: dict, now: int) -> None:
        conn.execute(
            """
            INSERT INTO documents (user, document, title, author, filename, series, series_index, isbn, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (user, document) DO UPDATE SET
                title = CASE WHEN documents.meta_manual THEN documents.title ELSE COALESCE(excluded.title, documents.title) END,
                author = CASE WHEN documents.meta_manual THEN documents.author ELSE COALESCE(excluded.author, documents.author) END,
                filename = COALESCE(excluded.filename, documents.filename),
                series = COALESCE(excluded.series, documents.series),
                series_index = COALESCE(excluded.series_index, CASE WHEN excluded.series IS NULL OR excluded.series = documents.series
                                                                    THEN documents.series_index END),
                isbn = COALESCE(excluded.isbn, documents.isbn),
                updated_at = excluded.updated_at
            """,
            (user, document, metadata.get('title'), metadata.get('authors'), metadata.get('filename'),
             metadata.get('series'), metadata.get('series_index'), metadata.get('isbn'), now),
        )

    def _write_progress(self, conn: sqlite3.Connection, user: str, record: dict, adopt_twin: bool, now: int) -> str:
        """Store one device position; returns the canonical document it was stored under."""
        metadata = record.get('metadata')
        if adopt_twin:
            document = self._adopt_filename_twin(conn, user, record['document'], (metadata or {}).get('filename'))
        else:
            document = self._resolve(conn, user, record['document'])
        device_id = record['device_id']
        percentage = record['percentage']
        seq = self._next_counter(conn, 'progress_change_seq')

        previous_newest = self._newest_row(conn, user, document)
        own = conn.execute(
            'SELECT percentage FROM progress WHERE user = ? AND document = ? AND device_id = ?', (user, document, device_id)
        ).fetchone()
        previous = own['percentage'] if own else (previous_newest['percentage'] if previous_newest else None)
        if previous is None or previous != percentage:
            conn.execute(
                'INSERT INTO progress_log (user, document, device_id, percentage, at) VALUES (?, ?, ?, ?, ?)',
                (user, document, device_id, percentage, now),
            )
            self._auto_unpause(conn, user, document, now)
            if percentage >= FINISHED_PERCENTAGE:
                self._auto_finish(conn, user, document, now)

        conn.execute(
            """
            INSERT INTO progress (user, document, device_id, device, percentage, progress, position, updated_at, change_seq)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (user, document, device_id) DO UPDATE SET
                device = excluded.device, percentage = excluded.percentage, progress = excluded.progress,
                position = COALESCE(excluded.position, progress.position),
                updated_at = excluded.updated_at, change_seq = excluded.change_seq
            """,
            (user, document, device_id, record['device'], percentage, record['progress'],
             json.dumps(record['position']) if record.get('position') else None, now, seq),
        )
        if metadata:
            self._upsert_metadata(conn, user, document, metadata, now)

        # A merge can leave another device's row in the future: let the change feed announce it
        newest = self._newest_row(conn, user, document)
        if previous_newest is not None and previous_newest['device_id'] == device_id and newest['device_id'] != device_id:
            conn.execute(
                'UPDATE progress SET change_seq = ?, server_change_seq = ? WHERE user = ? AND document = ? AND device_id = ?',
                (seq, seq, user, document, newest['device_id']),
            )
        return document

    def store_progress(self, user: str, record: dict, adopt_twin: bool = True) -> int:
        """Store a progress record (KOSync PUT); returns the server timestamp."""
        now = now_seconds()
        with self._get_connection() as conn:
            self._write_progress(conn, user, record, adopt_twin, now)
        return now

    def store_progress_batch(self, user: str, records: list[dict]) -> int:
        """Store several validated progress records in one transaction."""
        now = now_seconds()
        with self._get_connection() as conn:
            for record in records:
                self._write_progress(conn, user, record, True, now)
        return now

    def latest_progress(self, user: str, document: str) -> dict | None:
        """Newest progress of a document across the user's devices."""
        with self._get_connection() as conn:
            canonical = self._resolve(conn, user, document)
            self._auto_pause(conn, user, canonical)
            row = self._newest_row(conn, user, canonical)
        return dict(row) if row else None

    def device_progress(self, user: str, document: str) -> list[dict]:
        """Every device's progress of a document, newest first."""
        with self._get_connection() as conn:
            canonical = self._resolve(conn, user, document)
            self._auto_pause(conn, user, canonical)
            rows = conn.execute(
                'SELECT * FROM progress WHERE user = ? AND document = ? ORDER BY updated_at DESC, device_id ASC',
                (user, canonical),
            ).fetchall()
        return [dict(row) for row in rows]

    def list_progress(self, user: str, limit: int) -> tuple[list[dict], dict[str, list[str]]]:
        """Newest progress of every document joined with its metadata, most recent first."""
        with self._get_connection() as conn:
            self._auto_pause(conn, user)
            rows = conn.execute(
                f"""
                SELECT p.document, p.device_id, p.device, p.percentage, p.progress, p.position, p.updated_at,
                       d.title, d.author, d.filename, d.cover_url, d.page_count, d.library_path, d.status_at, d.pause_reason,
                       d.series, d.series_index, d.isbn,
                       COALESCE(d.status, CASE WHEN p.percentage >= {FINISHED_PERCENTAGE} OR {completed_sql('p.user', 'p.document')}
                                               THEN 'finished' ELSE 'reading' END) AS status
                FROM progress p
                LEFT JOIN documents d ON d.user = p.user AND d.document = p.document
                WHERE p.user = ? AND p.device_id = {NEWEST_DEVICE_SQL}
                UNION ALL
                -- Books marked as read without any progress (web UI): finished, at 100 %
                SELECT d.document, '' AS device_id, '' AS device, 1.0 AS percentage, '' AS progress, NULL AS position,
                       d.status_at AS updated_at, d.title, d.author, d.filename, d.cover_url, d.page_count, d.library_path,
                       d.status_at, d.pause_reason, d.series, d.series_index, d.isbn, d.status
                FROM documents d
                WHERE d.user = ? AND d.status = 'finished'
                  AND NOT EXISTS (SELECT 1 FROM progress p3 WHERE p3.user = d.user AND p3.document = d.document)
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (user, user, limit),
            ).fetchall()
            aliases = self._aliases_by_document(conn, user)
        return [dict(row) for row in rows], aliases

    def progress_changes(self, user: str, since: int, limit: int, device: str | None) -> dict:
        """Change feed of the newest position per document, for incremental device sync."""
        with self._get_connection() as conn:
            if limit == 0:
                row = conn.execute('SELECT MAX(change_seq) AS seq FROM progress WHERE user = ?', (user,)).fetchone()
                return {'cursor': max(since, row['seq'] or 0), 'more': False, 'changes': []}
            rows = conn.execute(
                f"""
                SELECT p.*, (SELECT MAX(p3.server_change_seq) FROM progress p3
                             WHERE p3.user = p.user AND p3.document = p.document) AS document_server_seq
                FROM progress p
                WHERE p.user = ? AND p.device_id = {NEWEST_DEVICE_SQL} AND p.change_seq > ?
                ORDER BY p.change_seq, p.document
                """,
                (user, since),
            ).fetchall()
            aliases = self._aliases_by_document(conn, user)

        envelope = len(json.dumps({'cursor': 9007199254740991, 'more': False, 'changes': []}, separators=(',', ':')))
        size = envelope
        cursor = since
        changes: list[dict] = []
        more = False
        for row in rows:
            if device and row['device_id'] == device and row['document_server_seq'] <= since:
                cursor = row['change_seq']
                continue
            change = {
                'document': row['document'],
                'aliases': aliases.get(row['document'], []),
                'percentage': row['percentage'],
                'progress': row['progress'],
                'position': json.loads(row['position']) if row['position'] else None,
                'device': row['device'],
                'device_id': row['device_id'],
                'timestamp': row['updated_at'],
            }
            change_size = len(json.dumps(change, separators=(',', ':'), ensure_ascii=False).encode('utf-8')) + (1 if changes else 0)
            if len(changes) >= limit or (changes and size + change_size > CHANGES_MAX_BYTES):
                more = True
                break
            changes.append(change)
            size += change_size
            cursor = row['change_seq']
        return {'cursor': cursor, 'more': more, 'changes': changes}

    def delete_document(self, user: str, document: str) -> int | None:
        """Hard-delete a book and everything attached to it; None when unknown."""
        with self._get_connection() as conn:
            found = any(
                conn.execute(f'SELECT 1 FROM {table} WHERE user = ? AND document = ? LIMIT 1', (user, document)).fetchone()
                for table in DOCUMENT_TABLES
            )
            if not found:
                return None
            rows = 0
            aliases = [r['alias'] for r in conn.execute('SELECT alias FROM document_aliases WHERE user = ? AND document = ?', (user, document))]
            for alias in aliases:
                rows += conn.execute('DELETE FROM stats_device_book WHERE user = ? AND document = ?', (user, alias)).rowcount
            for table in DOCUMENT_TABLES:
                rows += conn.execute(f'DELETE FROM {table} WHERE user = ? AND document = ?', (user, document)).rowcount
            rows += conn.execute(
                'DELETE FROM document_aliases WHERE user = ? AND (alias = ? OR document = ?)', (user, document, document)
            ).rowcount
        return rows

    def synced_references(self) -> tuple[set[str], set[str], set[str]]:
        """Library references of every synced document, all users: document ids (and their
        aliases), device file names and EPUBs associated by hand (relative to LIBRARY_DIR)."""
        documents: set[str] = set()
        filenames: set[str] = set()
        library_paths: set[str] = set()
        with self._get_connection() as conn:
            for row in conn.execute('SELECT document FROM progress UNION SELECT alias FROM document_aliases'):
                documents.add(row['document'])
            for row in conn.execute('SELECT document, filename, library_path FROM documents'):
                documents.add(row['document'])
                if row['filename']:
                    filenames.add(row['filename'])
                if row['library_path']:
                    library_paths.add(row['library_path'])
        return documents, filenames, library_paths

    # --------------------------------------------------------------- documents

    def upsert_documents(self, user: str, items: list[dict]) -> int:
        """Store book metadata sent in bulk; null fields keep their stored value."""
        now = now_seconds()
        with self._get_connection() as conn:
            for item in items:
                conn.execute(
                    """
                    INSERT INTO documents (user, document, title, author, filename, filesize, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT (user, document) DO UPDATE SET
                        title = CASE WHEN documents.meta_manual THEN documents.title ELSE COALESCE(excluded.title, documents.title) END,
                        author = CASE WHEN documents.meta_manual THEN documents.author ELSE COALESCE(excluded.author, documents.author) END,
                        filename = COALESCE(excluded.filename, documents.filename),
                        filesize = COALESCE(excluded.filesize, documents.filesize),
                        updated_at = excluded.updated_at
                    """,
                    (user, item['document'], item['title'], item['author'], item['filename'], item['filesize'], now),
                )
        return now

    def list_documents(self, user: str) -> list[dict]:
        """Stored book metadata, most recently updated first."""
        with self._get_connection() as conn:
            rows = conn.execute(
                'SELECT document, title, author, filename, filesize, status, status_at, pause_reason, cover_url, '
                'page_count, updated_at FROM documents WHERE user = ? ORDER BY updated_at DESC LIMIT 500',
                (user,),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_document(self, user: str, document: str) -> dict | None:
        """Stored metadata of a document (after alias resolution)."""
        with self._get_connection() as conn:
            canonical = self._resolve(conn, user, document)
            row = conn.execute('SELECT * FROM documents WHERE user = ? AND document = ?', (user, canonical)).fetchone()
        return dict(row) if row else None

    def set_status(self, user: str, document: str, status: str | None) -> dict:
        """Set (or clear with None) the manual reading status of a book."""
        now = now_seconds()
        with self._get_connection() as conn:
            canonical = self._resolve(conn, user, document)
            conn.execute(
                """
                INSERT INTO documents (user, document, status, status_at, pause_reason, updated_at) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT (user, document) DO UPDATE SET
                    status = excluded.status, status_at = excluded.status_at, pause_reason = excluded.pause_reason
                """,
                (user, canonical, status, now, 'manual' if status == 'paused' else None, now),
            )
        return {'document': canonical, 'status': status, 'status_at': now}

    def find_library_document(self, user: str, relative_path: str, documents: list[str], filename: str) -> str | None:
        """Canonical document of a library EPUB already known to the user: associated by hand
        (library_path), synced under one of its hashes, or under its device file name; None otherwise."""
        with self._get_connection() as conn:
            row = conn.execute(
                'SELECT document FROM documents WHERE user = ? AND library_path = ? ORDER BY updated_at DESC LIMIT 1',
                (user, relative_path),
            ).fetchone()
            if row:
                return self._resolve(conn, user, row['document'])
            for document in documents:
                canonical = self._resolve(conn, user, document)
                known = conn.execute('SELECT 1 FROM documents WHERE user = ? AND document = ?', (user, canonical)).fetchone()
                if known or self._has_progress(conn, user, canonical):
                    return canonical
            row = conn.execute(
                'SELECT document FROM documents WHERE user = ? AND filename = ? ORDER BY updated_at DESC LIMIT 1',
                (user, filename),
            ).fetchone()
            return self._resolve(conn, user, row['document']) if row else None

    def reading_state(self, user: str, document: str) -> dict:
        """Effective reading status (manual status, else finished from the progress or the device
        statistics, else reading; None when never synced) and newest percentage of a document."""
        with self._get_connection() as conn:
            canonical = self._resolve(conn, user, document)
            stored = conn.execute('SELECT status FROM documents WHERE user = ? AND document = ?', (user, canonical)).fetchone()
            newest = self._newest_row(conn, user, canonical)
            completed = conn.execute(f'SELECT {completed_sql("?", "?")}', (user, canonical, user, canonical)).fetchone()[0]
        percentage = newest['percentage'] if newest else None
        status = stored['status'] if stored else None
        if status is None and (completed or (percentage or 0) >= FINISHED_PERCENTAGE):
            status = 'finished'
        elif status is None and newest:
            status = 'reading'
        return {'document': canonical, 'status': status, 'percentage': percentage}

    def mark_library_book(self, user: str, document: str, finished: bool, info: dict) -> dict:
        """Mark a library EPUB as read (manual 'finished' status) or not read, from the web UI.

        A new document stores the EPUB's details (title, author, file name, library_path) so that
        the reading page shows it. Unmarking clears the manual status, or sets 'reading' when the
        progress itself says finished.
        """
        now = now_seconds()
        with self._get_connection() as conn:
            canonical = self._resolve(conn, user, document)
            conn.execute(
                """
                INSERT INTO documents (user, document, title, author, filename, library_path, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (user, document) DO UPDATE SET
                    title = COALESCE(documents.title, excluded.title),
                    author = COALESCE(documents.author, excluded.author),
                    filename = COALESCE(documents.filename, excluded.filename)
                """,
                (user, canonical, info.get('title'), info.get('author'), info.get('filename'), info.get('library_path'), now),
            )
        if finished:
            self.set_status(user, canonical, 'finished')
        else:
            self.set_status(user, canonical, None)
            if self.reading_state(user, canonical)['status'] == 'finished':
                self.set_status(user, canonical, 'reading')
        return self.reading_state(user, canonical)

    def set_dates(self, user: str, document: str, dates: dict) -> dict | None:
        """Set the manual start / finish dates; None when the start would be after the finish."""
        now = now_seconds()
        with self._get_connection() as conn:
            canonical = self._resolve(conn, user, document)
            stored = conn.execute(
                'SELECT start_date, finished_date, status FROM documents WHERE user = ? AND document = ?', (user, canonical)
            ).fetchone()
            start = dates['start_date'] if 'start_date' in dates else (stored['start_date'] if stored else None)
            finish = dates['finished_date'] if 'finished_date' in dates else (stored['finished_date'] if stored else None)
            if start is not None and finish is not None and start > finish:
                return None
            stored_status = stored['status'] if stored else None
            mark_finished = dates.get('finished_date') is not None and stored_status != 'finished'
            conn.execute(
                """
                INSERT INTO documents (user, document, start_date, finished_date, updated_at) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT (user, document) DO UPDATE SET
                    start_date = excluded.start_date, finished_date = excluded.finished_date
                """,
                (user, canonical, start, finish, now),
            )
            if mark_finished:
                conn.execute(
                    "UPDATE documents SET status = 'finished', status_at = ?, pause_reason = NULL WHERE user = ? AND document = ?",
                    (now, user, canonical),
                )
        return {
            'document': canonical,
            'start_date': start,
            'finished_date': finish,
            'status': 'finished' if mark_finished else stored_status,
        }

    def set_info(self, user: str, document: str, info: dict) -> dict:
        """Edit cover URL, print page count, title, author or associated library EPUB of a book."""
        now = now_seconds()
        with self._get_connection() as conn:
            canonical = self._resolve(conn, user, document)
            conn.execute(
                'INSERT INTO documents (user, document, updated_at) VALUES (?, ?, ?) ON CONFLICT (user, document) DO NOTHING',
                (user, canonical, now),
            )
            if 'cover_url' in info:
                conn.execute('UPDATE documents SET cover_url = ? WHERE user = ? AND document = ?', (info['cover_url'], user, canonical))
            if 'page_count' in info:
                conn.execute('UPDATE documents SET page_count = ? WHERE user = ? AND document = ?', (info['page_count'], user, canonical))
            if 'library_path' in info:
                conn.execute('UPDATE documents SET library_path = ? WHERE user = ? AND document = ?', (info['library_path'], user, canonical))
            if 'title' in info or 'author' in info:
                conn.execute(
                    'UPDATE documents SET title = COALESCE(?, title), author = CASE WHEN ? THEN ? ELSE author END, '
                    'meta_manual = 1 WHERE user = ? AND document = ?',
                    (info.get('title'), 'author' in info, info.get('author') or None, user, canonical),
                )
            row = conn.execute(
                'SELECT cover_url, page_count, title, author, library_path FROM documents WHERE user = ? AND document = ?', (user, canonical)
            ).fetchone()
        return {'document': canonical, **dict(row)}

    def merge_documents(self, user: str, document: str, into: str, merge_stats: bool) -> dict | None:
        """Merge two copies of a book: `document` becomes an alias of `into`. None if already merged."""
        now = now_seconds()
        with self._get_connection() as conn:
            source = self._resolve(conn, user, document)
            target = self._resolve(conn, user, into)
            if source == target:
                return None

            # Furthest position per device wins
            conn.execute(
                """DELETE FROM progress WHERE user = ? AND document = ? AND EXISTS (
                    SELECT 1 FROM progress t WHERE t.user = progress.user AND t.document = ?
                      AND t.device_id = progress.device_id AND t.percentage >= progress.percentage)""",
                (user, source, target),
            )
            conn.execute(
                """DELETE FROM progress WHERE user = ? AND document = ? AND EXISTS (
                    SELECT 1 FROM progress f WHERE f.user = progress.user AND f.document = ?
                      AND f.device_id = progress.device_id AND f.percentage > progress.percentage)""",
                (user, target, source),
            )
            conn.execute('UPDATE progress SET document = ? WHERE user = ? AND document = ?', (target, user, source))

            # The furthest row becomes the newest so every device is served the highest progress
            best = conn.execute(
                'SELECT device_id FROM progress WHERE user = ? AND document = ? ORDER BY percentage DESC, updated_at DESC LIMIT 1',
                (user, target),
            ).fetchone()
            if best:
                newest = conn.execute(
                    'SELECT MAX(updated_at) AS at FROM progress WHERE user = ? AND document = ?', (user, target)
                ).fetchone()['at']
                conn.execute(
                    'UPDATE progress SET updated_at = ? WHERE user = ? AND document = ? AND device_id = ?',
                    (max(now, newest + 1), user, target, best['device_id']),
                )
            seq = self._next_counter(conn, 'progress_change_seq')
            conn.execute(
                'UPDATE progress SET change_seq = ?, server_change_seq = ? WHERE user = ? AND document = ?',
                (seq, seq, user, target),
            )
            conn.execute('UPDATE progress_log SET document = ? WHERE user = ? AND document = ?', (target, user, source))

            # Book details: the canonical copy wins, the alias fills the gaps
            source_row = conn.execute('SELECT * FROM documents WHERE user = ? AND document = ?', (user, source)).fetchone()
            target_row = conn.execute('SELECT * FROM documents WHERE user = ? AND document = ?', (user, target)).fetchone()
            if source_row and not target_row:
                conn.execute('UPDATE documents SET document = ? WHERE user = ? AND document = ?', (target, user, source))
            elif source_row:
                fields = ('title', 'author', 'filename', 'filesize', 'status', 'status_at', 'start_date',
                          'finished_date', 'cover_url', 'page_count')
                values = {f: target_row[f] if target_row[f] is not None else source_row[f] for f in fields}
                values['pause_reason'] = source_row['pause_reason'] if target_row['status'] is None else target_row['pause_reason']
                assignments = ', '.join(f'{f} = :{f}' for f in values)
                conn.execute(
                    f'UPDATE documents SET {assignments} WHERE user = :user AND document = :document',
                    {**values, 'user': user, 'document': target},
                )
                conn.execute('DELETE FROM documents WHERE user = ? AND document = ?', (user, source))
            last_log = conn.execute(
                'SELECT MAX(at) AS at FROM progress_log WHERE user = ? AND document = ?', (user, target)
            ).fetchone()['at']
            if last_log is not None:
                self._auto_unpause(conn, user, target, last_log)

            for table in ('bookmarks', 'clippings'):
                conn.execute(f'UPDATE OR IGNORE {table} SET document = ? WHERE user = ? AND document = ?', (target, user, source))
                conn.execute(f'DELETE FROM {table} WHERE user = ? AND document = ?', (user, source))

            conn.execute(
                'UPDATE document_aliases SET document = ?, merge_stats = merge_stats AND ? WHERE user = ? AND document = ?',
                (target, int(merge_stats), user, source),
            )
            conn.execute(
                'INSERT OR REPLACE INTO document_aliases (user, alias, document, created_at, merge_stats) VALUES (?, ?, ?, ?, ?)',
                (user, source, target, now, int(merge_stats)),
            )
        return {'document': target, 'merged': source, 'stats': merge_stats}

    def unmerge(self, user: str, alias: str) -> bool:
        """Forget an alias; the progress already merged stays on the canonical document."""
        with self._get_connection() as conn:
            return conn.execute('DELETE FROM document_aliases WHERE user = ? AND alias = ?', (user, alias)).rowcount > 0

    # ------------------------------------------------------------------- stats

    def store_global_stats(self, user: str, device_id: str, device: str, payload: dict, daily: list[dict]) -> int:
        """Replace a device's global statistics snapshot; daily counters keep their maximum."""
        now = now_seconds()
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO stats_device_global (user, device_id, device, payload, updated_at) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT (user, device_id) DO UPDATE SET
                    device = excluded.device, payload = excluded.payload, updated_at = excluded.updated_at
                """,
                (user, device_id, device, json.dumps(payload), now),
            )
            for item in daily:
                conn.execute(
                    """
                    INSERT INTO stats_device_day (user, device_id, date, seconds, updated_at) VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT (user, device_id, date) DO UPDATE SET
                        seconds = MAX(stats_device_day.seconds, excluded.seconds), updated_at = excluded.updated_at
                    """,
                    (user, device_id, item['date'], item['seconds'], now),
                )
        return now

    def store_book_stats(self, user: str, device_id: str, items: list[dict]) -> int:
        """Replace a device's statistics snapshot for each book."""
        now = now_seconds()
        with self._get_connection() as conn:
            for item in items:
                payload = {k: v for k, v in item.items() if k != 'document'}
                conn.execute(
                    """
                    INSERT INTO stats_device_book (user, device_id, document, payload, updated_at) VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT (user, device_id, document) DO UPDATE SET
                        payload = excluded.payload, updated_at = excluded.updated_at
                    """,
                    (user, device_id, item['document'], json.dumps(payload), now),
                )
        return now

    def global_stats(self, user: str) -> tuple[list[dict], list[dict]]:
        """Per-device global snapshots (with their daily counters) and the daily totals."""
        with self._get_connection() as conn:
            devices = [
                {'device_id': r['device_id'], 'device': r['device'], 'updated_at': r['updated_at'], 'stats': json.loads(r['payload'])}
                for r in conn.execute('SELECT * FROM stats_device_global WHERE user = ? ORDER BY device_id', (user,))
            ]
            for device in devices:
                device['stats']['daily'] = [
                    {'date': r['date'], 'seconds': r['seconds']}
                    for r in conn.execute(
                        'SELECT date, seconds FROM stats_device_day WHERE user = ? AND device_id = ? ORDER BY date',
                        (user, device['device_id']),
                    )
                ]
            daily = [
                {'date': r['date'], 'seconds': r['seconds']}
                for r in conn.execute(
                    'SELECT date, SUM(seconds) AS seconds FROM stats_device_day WHERE user = ? GROUP BY date ORDER BY date', (user,)
                )
            ]
        return devices, daily

    def book_stats(self, user: str, document: str) -> list[dict]:
        """Per-device statistics rows of a book and of its merged copies."""
        with self._get_connection() as conn:
            canonical = self._resolve(conn, user, document)
            rows = conn.execute(
                """
                SELECT device_id, document, updated_at, payload FROM stats_device_book
                WHERE user = ? AND (document = ? OR document IN (
                    SELECT alias FROM document_aliases WHERE user = ? AND document = ? AND merge_stats = 1))
                ORDER BY updated_at DESC, device_id
                """,
                (user, canonical, user, canonical),
            ).fetchall()
        return [
            {'device_id': r['device_id'], 'document': r['document'], 'updated_at': r['updated_at'], 'stats': json.loads(r['payload'])}
            for r in rows
        ]

    def activity_inputs(self, user: str) -> tuple[list[dict], dict[str, dict], dict[str, list[dict]], dict[str, list[str]]]:
        """Progress history, document info, book statistics grouped by canonical document, and aliases."""
        with self._get_connection() as conn:
            log_rows = [dict(r) for r in conn.execute('SELECT document, percentage, at FROM progress_log WHERE user = ?', (user,))]
            documents = {
                r['document']: dict(r)
                for r in conn.execute(
                    'SELECT document, filename, page_count, library_path, status, status_at, start_date, finished_date '
                    'FROM documents WHERE user = ?', (user,)
                )
            }
            alias_map = {
                r['alias']: r['document']
                for r in conn.execute('SELECT alias, document FROM document_aliases WHERE user = ? AND merge_stats = 1', (user,))
            }
            book_stats: dict[str, list[dict]] = {}
            for r in conn.execute('SELECT document, payload FROM stats_device_book WHERE user = ?', (user,)):
                book_stats.setdefault(alias_map.get(r['document'], r['document']), []).append(json.loads(r['payload']))
            aliases = self._aliases_by_document(conn, user)
        return log_rows, documents, book_stats, aliases

    # --------------------------------------------------------------- clippings

    def get_clippings(self, user: str, document: str, cursor: int | None, since: int, limit: int) -> list[dict]:
        """Clippings changed after a revision cursor (or after `since` without cursor), tombstones included."""
        with self._get_connection() as conn:
            canonical = self._resolve(conn, user, document)
            if cursor is not None:
                rows = conn.execute(
                    'SELECT * FROM clippings WHERE user = ? AND document = ? AND revision > ? ORDER BY revision LIMIT ?',
                    (user, canonical, cursor, limit + 1),
                ).fetchall()
            else:
                rows = conn.execute(
                    'SELECT * FROM clippings WHERE user = ? AND document = ? AND updated_at > ? ORDER BY updated_at, id LIMIT ?',
                    (user, canonical, since, limit + 1),
                ).fetchall()
        return [dict(row) for row in rows]

    def store_clippings(self, user: str, document: str, operations: list[dict]) -> int:
        """Apply clipping uploads and deletions; a deleted clipping never comes back."""
        now = now_seconds()
        with self._get_connection() as conn:
            canonical = self._resolve(conn, user, document)
            for op in operations:
                revision = self._next_counter(conn, 'clipping_revision')
                if op['deleted']:
                    conn.execute(
                        """
                        INSERT INTO clippings (user, document, id, deleted, updated_at, revision) VALUES (?, ?, ?, 1, ?, ?)
                        ON CONFLICT (user, document, id) DO UPDATE SET
                            deleted = 1, updated_at = excluded.updated_at, revision = excluded.revision
                        """,
                        (user, canonical, op['id'], now, revision),
                    )
                    continue
                existing = conn.execute(
                    'SELECT deleted, note, color FROM clippings WHERE user = ? AND document = ? AND id = ?',
                    (user, canonical, op['id']),
                ).fetchone()
                if existing and existing['deleted']:
                    continue
                note = op['note'] if 'note' in op else (existing['note'] if existing else None)
                color = op['color'] if 'color' in op else (existing['color'] if existing else None)
                conn.execute(
                    """
                    INSERT INTO clippings (user, document, id, spine_index, start_page, end_page, page_count, start_word,
                        end_word, word_count, paragraph_index, chapter_title, text, note, color, created_at, deleted,
                        updated_at, layout_signature, start_offset, end_offset, revision)
                    VALUES (:user, :document, :id, :spine, :start_page, :end_page, :pages, :start_word, :end_word, :words,
                        :para, :chapter, :text, :note, :color, :created_at, 0, :now, :layout_signature, :start_offset,
                        :end_offset, :revision)
                    ON CONFLICT (user, document, id) DO UPDATE SET
                        spine_index = excluded.spine_index, start_page = excluded.start_page, end_page = excluded.end_page,
                        page_count = excluded.page_count, start_word = excluded.start_word, end_word = excluded.end_word,
                        word_count = excluded.word_count, paragraph_index = excluded.paragraph_index,
                        chapter_title = excluded.chapter_title, text = excluded.text, note = excluded.note,
                        color = excluded.color, created_at = excluded.created_at, deleted = 0,
                        updated_at = excluded.updated_at, layout_signature = excluded.layout_signature,
                        start_offset = excluded.start_offset, end_offset = excluded.end_offset, revision = excluded.revision
                    WHERE clippings.deleted = 0
                    """,
                    {**op, 'user': user, 'document': canonical, 'note': note, 'color': color, 'now': now, 'revision': revision},
                )
        return now

    def all_clippings(self, user: str) -> list[dict]:
        """Every live clipping of the user across books, newest first."""
        with self._get_connection() as conn:
            rows = conn.execute(
                'SELECT document, id, spine_index, paragraph_index, start_offset, chapter_title, text, note, created_at '
                'FROM clippings WHERE user = ? AND deleted = 0 ORDER BY created_at DESC, id LIMIT ?',
                (user, CLIPPINGS_ALL_LIMIT),
            ).fetchall()
        return [dict(row) for row in rows]

    # --------------------------------------------------------------- bookmarks

    def get_bookmarks(self, user: str, document: str, since: int, limit: int) -> list[dict]:
        """Bookmarks changed after `since`, tombstones included."""
        with self._get_connection() as conn:
            rows = conn.execute(
                'SELECT * FROM bookmarks WHERE user = ? AND document = ? AND updated_at > ? ORDER BY updated_at, id LIMIT ?',
                (user, document, since, limit + 1),
            ).fetchall()
        return [dict(row) for row in rows]

    def store_bookmarks(self, user: str, document: str, operations: list[dict]) -> int:
        """Apply bookmark uploads and deletions (an upload brings a deleted bookmark back)."""
        now = now_seconds()
        with self._get_connection() as conn:
            for op in operations:
                if op['deleted']:
                    conn.execute(
                        """
                        INSERT INTO bookmarks (user, document, id, deleted, updated_at) VALUES (?, ?, ?, 1, ?)
                        ON CONFLICT (user, document, id) DO UPDATE SET deleted = 1, updated_at = excluded.updated_at
                        """,
                        (user, document, op['id'], now),
                    )
                    continue
                conn.execute(
                    """
                    INSERT INTO bookmarks (user, document, id, xpath, percentage, summary, spine_index, paragraph_count,
                        paragraph_pos, deleted, updated_at)
                    VALUES (:user, :document, :id, :xpath, :percentage, :summary, :si, :pc, :pp, 0, :now)
                    ON CONFLICT (user, document, id) DO UPDATE SET
                        xpath = excluded.xpath, percentage = excluded.percentage, summary = excluded.summary,
                        spine_index = excluded.spine_index, paragraph_count = excluded.paragraph_count,
                        paragraph_pos = excluded.paragraph_pos, deleted = 0, updated_at = excluded.updated_at
                    """,
                    {**op, 'user': user, 'document': document, 'now': now},
                )
        return now


def date_to_seconds(value: str) -> int | None:
    """'YYYY-MM-DD' -> unix seconds at UTC midnight, None if not a real date."""
    try:
        date = datetime.date.fromisoformat(value)
    except ValueError:
        return None
    if date.isoformat() != value:
        return None
    return int(datetime.datetime(date.year, date.month, date.day, tzinfo=datetime.UTC).timestamp())


__all__ = [
    'KOREADER_SYNC_DB_PATH',
    'SyncStorage',
    'date_to_seconds',
    'md5_hex',
    'now_seconds',
]
