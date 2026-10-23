"""CrossPoint Sync API (/api/v1): the extensions used by CrossPoint readers and the CrossPoint Sync app.

Wire format follows crosspoint-sync (https://github.com/crosspoint-reader/crosspoint-sync).
Connectors (Hardcover, Spotify, Readwise...) are not provided: those endpoints answer with empty results, which the app handles by hiding the feature.
Book details missing from the sync data are taken from the OPDS library when the hash matches:
title, author, cover, print pages, genres, series, year, rating, description (/about) and the
next book of the series found in the library (/next).
Then, optionally (ONLINE_METADATA / HARDCOVER_API_KEY, see controllers/online_metadata.py), from the
online lookups cache.
"""
import json
import math
import os
import re
from urllib.parse import quote

from controllers.koreader_sync import (
    MD5_RE,
    SyncControllerBase,
    is_fraction,
    is_uint,
    is_valid_document,
    parse_progress_body,
    utf8_length,
)
from controllers.library_index import LibraryIndex
from controllers.opds import LIBRARY_DIR
from controllers.online_metadata import OnlineMetadata
from controllers.reading_activity import (
    HISTORY_BYTES,
    combine_book_stats,
    compute_activity,
    lenient_b64decode,
    summarize_global_stats,
)
from controllers.sync_storage import date_to_seconds, now_seconds

UINT32_MAX = 0xFFFFFFFF
SAFE_INTEGER_MAX = 2 ** 53 - 1
PROGRESS_LIST_DEFAULT_LIMIT = 100
PROGRESS_LIST_MAX_LIMIT = 500
PROGRESS_BATCH_MAX = 20
CHANGES_DEFAULT_LIMIT = 20
CHANGES_MAX_LIMIT = 50
LIST_DEFAULT_LIMIT = 50
LIST_MAX_LIMIT = 100
ITEMS_BATCH_MAX = 50
STATS_BOOKS_BATCH_MAX = 20
DAILY_MAX = 20
CLIPPING_TEXT_MAX_BYTES = 4096
CLIPPING_CHAPTER_MAX = 64
CLIPPING_COLOR_MAX = 32
DOCUMENT_STATUSES = ('reading', 'paused', 'finished', 'dnf')

ITEM_ID_RE = re.compile(r'^[\x21-\x7e]{1,64}$')
DAILY_DATE_RE = re.compile(r'^20\d{2}-\d{2}-\d{2}$')
DATE_RE = re.compile(r'^\d{4}-\d{2}-\d{2}$')
COVER_URL_RE = re.compile(r'^https?://\S{1,2000}$')

GLOBAL_COUNT_FIELDS = ('v', 'sessions', 'seconds', 'pages', 'completed', 'anchor_day', 'streak')
BOOK_COUNT_FIELDS = ('v', 'sessions', 'seconds', 'pages', 'avg_fwd', 'pace_n', 'eta', 'start_date', 'finished_date')
BOOK_FLAG_FIELDS = ('completed', 'start_manual', 'finish_manual')
CLIPPING_DEFAULTS = {
    'start_page': 0, 'end_page': 0, 'pages': 1, 'start_word': 0, 'end_word': 0,
    'words': 0, 'created_at': 0, 'layout_signature': 0,
}


def js_number(value: str | None, default: float) -> float:
    """Parse a query value like JavaScript's Number() (NaN when invalid)."""
    if value is None:
        return default
    value = value.strip()
    if not value:
        return 0.0
    try:
        return float(value)
    except ValueError:
        return math.nan


def json_number(value: float | int | None) -> int | float | None:
    """A stored number as JSON: an int when whole (1.0 -> 1), None stays None."""
    if value is None:
        return None
    return int(value) if float(value).is_integer() else value


def is_count(value: object) -> bool:
    """Non-negative JSON integer."""
    return is_uint(value, SAFE_INTEGER_MAX)


def is_count_list(value: object, length: int) -> bool:
    """List of exactly `length` non-negative integers."""
    return isinstance(value, list) and len(value) == length and all(is_count(v) for v in value)


def is_item_id(value: object) -> bool:
    """Clipping / bookmark ids: 1-64 printable ASCII characters."""
    return isinstance(value, str) and ITEM_ID_RE.match(value) is not None


def is_device_id(value: object) -> bool:
    """Device ids: non-empty string of at most 128 characters."""
    return isinstance(value, str) and 0 < len(value) <= 128


def parse_daily(value: object) -> list[dict] | None:
    """Validate the daily reading counters of a global stats upload (None when invalid)."""
    if not isinstance(value, list) or len(value) > DAILY_MAX:
        return None
    seen = set()
    daily = []
    for item in value:
        if not isinstance(item, dict):
            return None
        date = item.get('date')
        if not isinstance(date, str) or not DAILY_DATE_RE.match(date) or date_to_seconds(date) is None or date in seen:
            return None
        if not is_uint(item.get('seconds'), UINT32_MAX):
            return None
        seen.add(date)
        daily.append({'date': date, 'seconds': int(item['seconds'])})
    return daily


def parse_clipping(item: object) -> dict | None:
    """Validate one clipping operation of a PUT batch (None when invalid)."""
    if not isinstance(item, dict) or not is_item_id(item.get('id')):
        return None
    if item.get('deleted') in (1, True) and not isinstance(item.get('deleted'), float):
        return {'id': item['id'], 'deleted': True}

    op = {'id': item['id'], 'deleted': False}
    if not is_uint(item.get('spine'), UINT32_MAX):
        return None
    op['spine'] = int(item['spine'])
    for key, default in CLIPPING_DEFAULTS.items():
        if key not in item:
            op[key] = default
        elif is_uint(item[key], UINT32_MAX):
            op[key] = int(item[key])
        else:
            return None
    para = item.get('para')
    if para is not None and not is_uint(para, UINT32_MAX):
        return None
    op['para'] = int(para) if para is not None else None

    start, end = item.get('start_offset'), item.get('end_offset')
    if (start is None) != (end is None):
        return None
    if start is not None:
        if not is_uint(start, UINT32_MAX) or not is_uint(end, UINT32_MAX) or int(end) <= int(start):
            return None
        start, end = int(start), int(end)
    op['start_offset'], op['end_offset'] = start, end

    text = item.get('text')
    if not isinstance(text, str) or not text or utf8_length(text) > CLIPPING_TEXT_MAX_BYTES:
        return None
    op['text'] = text
    op['chapter'] = item['chapter'][:CLIPPING_CHAPTER_MAX] if isinstance(item.get('chapter'), str) else ''
    # note / color are only replaced when the key is sent, so reader uploads keep app notes
    if 'note' in item:
        note = item['note']
        op['note'] = note if isinstance(note, str) and utf8_length(note) <= CLIPPING_TEXT_MAX_BYTES else None
    if 'color' in item:
        op['color'] = item['color'][:CLIPPING_COLOR_MAX] if isinstance(item['color'], str) else None
    return op


def parse_bookmark(item: object) -> dict | None:
    """Validate one bookmark operation of a PUT batch (None when invalid)."""
    if not isinstance(item, dict) or not is_item_id(item.get('id')):
        return None
    if item.get('deleted') in (1, True) and not isinstance(item.get('deleted'), float):
        return {'id': item['id'], 'deleted': True}
    xpath = item.get('xpath')
    percentage = item.get('percentage')
    if not isinstance(xpath, str) or len(xpath) > 512:
        return None
    if not is_fraction(percentage):
        return None
    op = {
        'id': item['id'], 'deleted': False, 'xpath': xpath, 'percentage': percentage,
        'summary': item['summary'][:256] if isinstance(item.get('summary'), str) else '',
    }
    for key in ('si', 'pc', 'pp'):
        value = item.get(key)
        if value is not None and not is_uint(value, 65535):
            return None
        op[key] = int(value) if value is not None else None
    return op


def clipping_json(row: dict, reader_format: bool) -> dict:
    """Wire representation of a clipping row (integers only: the firmware rejects floats)."""
    item = {
        'id': row['id'],
        'revision': row['revision'],
        'spine': row['spine_index'],
        'start_page': row['start_page'],
        'end_page': row['end_page'],
        'pages': row['page_count'],
        'start_word': row['start_word'],
        'end_word': row['end_word'],
        'words': row['word_count'],
        'para': row['paragraph_index'],
        'chapter': row['chapter_title'],
        'text': row['text'],
    }
    if not reader_format:
        item['note'] = row['note']
        item['color'] = row['color']
    item.update({
        'created_at': row['created_at'],
        'deleted': 1 if row['deleted'] else 0,
        'updated_at': row['updated_at'],
        'layout_signature': row['layout_signature'],
        'start_offset': row['start_offset'],
        'end_offset': row['end_offset'],
    })
    return item


class CrossPointSyncController(SyncControllerBase):
    """CrossPoint Sync /api/v1 endpoints."""

    def __init__(self, request_handler):
        super().__init__(request_handler)
        self.library = LibraryIndex.get_instance()
        # Optional online details (ONLINE_METADATA / HARDCOVER_API_KEY): cache reads only, lookups in the background
        self.online = OnlineMetadata.for_db(self.sync_storage.db_path)

    # ----------------------------------------------------------------- helpers

    def _document_param(self, name: str = 'document', code: int | None = None) -> str | None:
        """Validated document path parameter, or answer 403 (returns None)."""
        document = self._param(name)
        if is_valid_document(document):
            return document
        if code == self.ERROR_INVALID_FIELDS:
            self._send_json_error(self.ERROR_INVALID_FIELDS, 'Invalid request')
        else:
            self._send_json_error(self.ERROR_DOCUMENT_FIELD_MISSING, "Field 'document' not provided.")
        return None

    def _invalid(self) -> None:
        self._send_json_error(self.ERROR_INVALID_FIELDS, 'Invalid request')

    def _json_object(self) -> dict | None:
        """JSON object body, or answer 403 Invalid request (returns None)."""
        ok, body = self._read_json()
        if not ok or not isinstance(body, dict):
            self._invalid()
            return None
        return body

    def _list_params(self) -> tuple[int, int]:
        """`since` and `limit` query parameters of the delta-sync list endpoints."""
        since = js_number(self._query('since'), 0)
        since = math.floor(since) if math.isfinite(since) and since > 0 else 0
        limit = js_number(self._query('limit'), LIST_DEFAULT_LIMIT)
        limit = min(math.floor(limit), LIST_MAX_LIMIT) if math.isfinite(limit) and limit > 0 else LIST_DEFAULT_LIMIT
        return since, limit

    def _base_url(self) -> str:
        """Public base URL of this server (honours a reverse proxy's X-Forwarded-* headers)."""
        headers = self.request.headers
        scheme = (headers.get('X-Forwarded-Proto') or 'http').split(',')[0].strip()
        host = (headers.get('X-Forwarded-Host') or headers.get('Host') or 'localhost').split(',')[0].strip()
        return f'{scheme}://{host}'

    def _library_book(self, documents: list[str], filename: str | None, library_path: str | None = None) -> dict | None:
        """Details of the library EPUB matching a synced document (or associated by hand), with an absolute cover URL."""
        try:
            path = (library_path and self.library.resolve_relative(library_path)) or self.library.find(documents, filename)
            if not path:
                return None
            details = dict(self.library.details(path))
        except OSError:
            return None
        details['filename'] = os.path.basename(path)
        details['cover_url'] = f"{self._base_url()}/cover/{quote(details['relative_path'])}" if details['has_cover'] else None
        return details

    # ---------------------------------------------------------------- progress

    def progress_put(self) -> None:
        """PUT /api/v1/progress: same as the KOSync progress upload."""
        user = self._require_user()
        if not user:
            return
        ok, body = self._read_json()
        record, error = parse_progress_body(body if ok else None)
        if error:
            status, code, message = error
            self._send_json_error(code, message, status)
            return
        timestamp = self.sync_storage.store_progress(user, record, adopt_twin=False)
        self._send_json_response({'document': record['document'], 'timestamp': timestamp})

    def progress_batch(self) -> None:
        """PUT /api/v1/progress/batch: several progress uploads, all or nothing."""
        user = self._require_user()
        if not user:
            return
        body = self._json_object()
        if body is None:
            return
        items = body.get('items')
        if not isinstance(items, list) or not 0 < len(items) <= PROGRESS_BATCH_MAX:
            self._invalid()
            return
        records = []
        for item in items:
            record, error = parse_progress_body(item)
            if error:
                status, code, message = error
                self._send_json_error(code, message, status)
                return
            records.append(record)
        timestamp = self.sync_storage.store_progress_batch(user, records)
        self._send_json_response({
            'accepted': len(records),
            'items': [{'document': r['document'], 'timestamp': timestamp} for r in records],
        })

    def progress_list(self) -> None:
        """GET /api/v1/progress: every synced book with its newest progress and details."""
        user = self._require_user()
        if not user:
            return
        limit = js_number(self._query('limit'), PROGRESS_LIST_DEFAULT_LIMIT)
        limit = min(math.floor(limit), PROGRESS_LIST_MAX_LIMIT) if math.isfinite(limit) and limit > 0 else PROGRESS_LIST_DEFAULT_LIMIT

        rows, aliases = self.sync_storage.list_progress(user, limit)
        items = []
        for row in rows:
            document_aliases = aliases.get(row['document'], [])
            title, author, filename, cover_url = row['title'], row['author'], row['filename'], row['cover_url']
            page_count = row['page_count']
            genres: list[str] = []
            library_path = None
            # Series sent by the reader (CrossPoint), replaced by the library EPUB's when it has one
            series, series_position = row['series'], json_number(row['series_index'])
            release_year = rating = None
            library_book = self._library_book([row['document'], *document_aliases], filename, row['library_path'])
            if library_book:
                genres = library_book['genres']
                library_path = library_book['relative_path']
                title = title or library_book['title']
                author = author or library_book['author']
                filename = filename or library_book['filename']
                cover_url = cover_url or library_book['cover_url']
                page_count = page_count or library_book['page_count']
                if library_book['series']:
                    series, series_position = library_book['series'], library_book['series_index']
                release_year, rating = library_book['year'], library_book['rating']
            position = None
            if row['position']:
                try:
                    position = json.loads(row['position'])
                except ValueError:
                    position = None
            items.append({
                'document': row['document'],
                'title': title,
                'author': author,
                'filename': filename,
                'status': row['status'],
                'status_at': row['status_at'],
                'pause_reason': row['pause_reason'],
                'cover_url': cover_url,
                'page_count': page_count,
                'hardcover_slug': None,
                'moods': [],
                'genres': genres,
                'content_warnings': [],
                'rating': rating,
                'series': series,
                'series_position': series_position,
                'release_year': release_year,
                'percentage': row['percentage'],
                'progress': row['progress'],
                'page': (position or {}).get('page'),
                'pages': (position or {}).get('pages'),
                'device_id': row['device_id'],
                'device': row['device'],
                'timestamp': row['updated_at'],
                'aliases': document_aliases,
                # Path of the matching library EPUB (relative to LIBRARY_DIR), for the web UI's catalog links
                'library_path': library_path,
                # True when the library EPUB was associated by hand (PUT /documents/{document}/info)
                'library_linked': bool(row['library_path']) and library_path is not None,
            })
        # Online fallback: fills only the fields still empty, queues lookups for unmatched books
        self.online.fill_progress_items(items, [row.get('isbn') for row in rows])
        self._send_json_response({'items': items})

    def progress_changes(self) -> None:
        """GET /api/v1/progress/changes: change feed for incremental device sync."""
        user = self._require_user()
        if not user:
            return
        since = js_number(self._query('since'), 0)
        since = math.floor(since) if math.isfinite(since) and since > 0 else 0
        limit = js_number(self._query('limit') or None, CHANGES_DEFAULT_LIMIT)
        limit = min(math.floor(limit), CHANGES_MAX_LIMIT) if math.isfinite(limit) and limit >= 0 else CHANGES_DEFAULT_LIMIT
        device = self._query('device')
        self._send_json_response(self.sync_storage.progress_changes(user, since, limit, device[:128] if device else None))

    def progress_show(self) -> None:
        """GET /api/v1/progress/{document}: every device's position for a book."""
        user = self._require_user()
        if not user:
            return
        document = self._document_param()
        if not document:
            return
        devices = [
            {
                'device_id': row['device_id'],
                'device': row['device'],
                'percentage': row['percentage'],
                'progress': row['progress'],
                'position': json.loads(row['position']) if row['position'] else None,
                'timestamp': row['updated_at'],
            }
            for row in self.sync_storage.device_progress(user, document)
        ]
        self._send_json_response({'document': document, 'devices': devices})

    def progress_delete(self) -> None:
        """DELETE /api/v1/progress/{document}: remove a book and all its synced data."""
        user = self._require_user()
        if not user:
            return
        document = self._document_param()
        if not document:
            return
        rows = self.sync_storage.delete_document(user, document)
        if rows is None:
            self._send_json_error(self.ERROR_INVALID_FIELDS, 'Unknown document', 404)
            return
        self._send_json_response({'document': document, 'deleted': True, 'rows': rows})

    # --------------------------------------------------------------- documents

    def documents_put(self) -> None:
        """PUT /api/v1/documents: store book details in bulk."""
        user = self._require_user()
        if not user:
            return
        body = self._json_object()
        if body is None:
            return
        raw_items = body.get('items')
        if not isinstance(raw_items, list) or not 0 < len(raw_items) <= ITEMS_BATCH_MAX:
            self._invalid()
            return
        items = []
        for item in raw_items:
            if not isinstance(item, dict) or not is_valid_document(item.get('document')):
                self._invalid()
                return

            def text(key: str) -> str | None:
                return item[key][:512] if isinstance(item.get(key), str) and item[key] else None

            items.append({
                'document': item['document'],
                'title': text('title'),
                'author': text('author'),
                'filename': text('filename'),
                'filesize': int(item['filesize']) if is_count(item.get('filesize')) else None,
            })
        until = self.sync_storage.upsert_documents(user, items)
        self._send_json_response({'until': until, 'accepted': len(items)})

    def documents_list(self) -> None:
        """GET /api/v1/documents: stored book details."""
        user = self._require_user()
        if user:
            self._send_json_response({'items': self.sync_storage.list_documents(user)})

    def document_status(self) -> None:
        """PUT /api/v1/documents/{document}/status: reading, paused, finished, dnf or null."""
        user = self._require_user()
        if not user:
            return
        document = self._document_param(code=self.ERROR_INVALID_FIELDS)
        if not document:
            return
        body = self._json_object()
        if body is None:
            return
        status = body.get('status')
        if status is not None and status not in DOCUMENT_STATUSES:
            self._invalid()
            return
        self._send_json_response(self.sync_storage.set_status(user, document, status))

    def document_dates(self) -> None:
        """PUT /api/v1/documents/{document}/dates: manual start / finish dates (YYYY-MM-DD or null)."""
        user = self._require_user()
        if not user:
            return
        document = self._document_param(code=self.ERROR_INVALID_FIELDS)
        if not document:
            return
        body = self._json_object()
        if body is None:
            return
        dates = {}
        for key in ('start_date', 'finished_date'):
            if key not in body:
                continue
            value = body[key]
            if value is None:
                dates[key] = None
                continue
            seconds = date_to_seconds(value) if isinstance(value, str) and DATE_RE.match(value) else None
            if seconds is None or seconds > now_seconds() + 86400:
                self._invalid()
                return
            dates[key] = seconds
        result = self.sync_storage.set_dates(user, document, dates)
        if result is None:
            self._send_json_error(self.ERROR_INVALID_FIELDS, 'The start date is after the finish date', 400)
            return
        self._send_json_response(result)

    def document_info(self) -> None:
        """PUT /api/v1/documents/{document}/info: cover URL, print page count, title, author, library EPUB.

        `library_path` (relative to LIBRARY_DIR, null to remove) associates the document with a library
        EPUB when its hashes match none, e.g. after the file was changed or sent by another way.
        """
        user = self._require_user()
        if not user:
            return
        document = self._document_param(code=self.ERROR_INVALID_FIELDS)
        if not document:
            return
        body = self._json_object()
        if body is None:
            return
        info = {}
        if 'cover_url' in body:
            cover = body['cover_url']
            if cover is not None and not (isinstance(cover, str) and COVER_URL_RE.match(cover)):
                self._invalid()
                return
            info['cover_url'] = cover
        if 'page_count' in body:
            pages = body['page_count']
            if pages is not None and not (is_uint(pages, 100000) and pages >= 1):
                self._invalid()
                return
            info['page_count'] = int(pages) if pages is not None else None
        if 'title' in body:
            title = body['title'].strip()[:500] if isinstance(body['title'], str) else ''
            if not title:
                self._invalid()
                return
            info['title'] = title
        if 'author' in body:
            if not isinstance(body['author'], str):
                self._invalid()
                return
            info['author'] = body['author'].strip()[:500]
        if 'library_path' in body:
            library_path = body['library_path']
            if library_path is not None and not (isinstance(library_path, str) and self.library.resolve_relative(library_path)):
                self._invalid()
                return
            info['library_path'] = library_path
        self._send_json_response(self.sync_storage.set_info(user, document, info))

    def document_cover(self) -> None:
        """GET /api/v1/documents/{document}/cover: stored cover, else the library EPUB's cover."""
        user = self._require_user()
        if not user:
            return
        document = self._document_param()
        if not document:
            return
        row = self.sync_storage.get_document(user, document) or {}
        url, pages = row.get('cover_url'), row.get('page_count')
        if not url or not pages:
            library_book = self._library_book([self.sync_storage.resolve_document(user, document), document], row.get('filename'), row.get('library_path'))
            if library_book:
                url = url or library_book['cover_url']
                pages = pages or library_book['page_count']
        if not url or not pages:
            # Online fallback (cache only)
            online = self.online.cached_info(row.get('title'), row.get('author'), row.get('isbn'), row.get('filename'))
            url, pages = url or online.get('cover_url'), pages or online.get('page_count')
        self._send_json_response({'url': url, 'pages': pages})

    def document_cover_candidates(self) -> None:
        """GET /api/v1/documents/{document}/cover/candidates: the library EPUB's cover, if any."""
        user = self._require_user()
        if not user:
            return
        document = self._document_param()
        if not document:
            return
        row = self.sync_storage.get_document(user, document) or {}
        library_book = self._library_book([self.sync_storage.resolve_document(user, document), document], row.get('filename'), row.get('library_path'))
        items = []
        if library_book and library_book['cover_url']:
            items.append({
                'url': library_book['cover_url'],
                'title': library_book['title'],
                'author': library_book['author'],
                'source': 'OPDS',
                'pages': None,
            })
        # Online candidates after the library one (live iTunes / Open Library search, ?q= searches another title)
        q = (self._query('q') or '').strip()[:200]
        title = q or row.get('title') or (library_book or {}).get('title')
        author = '' if q else (row.get('author') or (library_book or {}).get('author') or '')
        if title:
            items.extend(self.online.cover_candidates(title, author))
        self._send_json_response({'items': items})

    def _document_library_book(self, user: str, document: str) -> tuple[dict, dict | None]:
        """Stored row of a document (after alias resolution) and its library EPUB details, if any."""
        row = self.sync_storage.get_document(user, document) or {}
        canonical = self.sync_storage.resolve_document(user, document)
        return row, self._library_book([canonical, document], row.get('filename'), row.get('library_path'))

    def document_about(self) -> None:
        """GET /api/v1/documents/{document}/about: the library EPUB's description (plain text), else the online lookups cache, else null."""
        user = self._require_user()
        if not user:
            return
        document = self._document_param()
        if not document:
            return
        row, library_book = self._document_library_book(user, document)
        description = library_book['description'] if library_book else None
        if not description:
            # Online fallback (cache only)
            description = self._online_info(row, library_book).get('description')
        self._send_json_response({'description': description})

    def _online_query(self, row: dict, library_book: dict | None) -> tuple:
        """(title, author, isbn, filename) for the online lookups: the sync data, else the library EPUB."""
        library_book = library_book or {}
        return (row.get('title') or library_book.get('title'), row.get('author') or library_book.get('author'),
                row.get('isbn'), row.get('filename'))

    def _online_info(self, row: dict, library_book: dict | None) -> dict:
        """Cached online details of a document ({} when disabled or unknown)."""
        return self.online.cached_info(*self._online_query(row, library_book))

    def document_next(self) -> None:
        """GET /api/v1/documents/{document}/next: the next book of the series found in the library, else on Hardcover.

        The series comes from the library EPUB, else from the reader's metadata. Answers
        `{series, next: {position, title, author, year, slug, cover}}`, `next` null when the
        library holds no later book, and `{next: null}` for a book outside any known series.
        Without a later book in the library, Hardcover's series is used when HARDCOVER_API_KEY is set
        (`{next: null, pending: true}` while it is fetched in the background).
        """
        user = self._require_user()
        if not user:
            return
        document = self._document_param()
        if not document:
            return
        row, library_book = self._document_library_book(user, document)
        series, position, current = row.get('series'), json_number(row.get('series_index')), None
        if library_book and library_book['series']:
            series, position = library_book['series'], library_book['series_index']
            current = os.path.join(LIBRARY_DIR, library_book['relative_path'])
        answer = self._local_next(series, position, current)
        if answer.get('next') is None:
            # Online fallback: Hardcover series (cache only, fetched in the background; pending meanwhile)
            online = self.online.next_in_series(*self._online_query(row, library_book))
            if online and (online.get('next') or online.get('pending')):
                series_name = answer.get('series') or online.get('series')  # the local series name wins
                answer = {**({'series': series_name} if series_name else {}),
                          **{k: v for k, v in online.items() if k != 'series'}}
        self._send_json_response(answer)

    def _local_next(self, series: str | None, position: float | None, current: str | None) -> dict:
        """Next book of a series found in the library: {series, next}, or {next: None} without a series."""
        if not series or position is None:
            return {'next': None}
        try:
            found = self.library.next_in_series(series, position, current)
            details = self.library.details(found[1]) if found else None
        except OSError:
            found = details = None
        if not found or details is None:
            return {'series': series, 'next': None}
        return {
            'series': series,
            'next': {
                'position': found[0],
                'title': details['title'] or os.path.splitext(os.path.basename(found[1]))[0],
                'author': details['author'],
                'year': details['year'],
                'slug': None,
                'cover': f"{self._base_url()}/cover/{quote(details['relative_path'])}" if details['has_cover'] else None,
            },
        }

    def document_matches(self) -> None:
        """GET /api/v1/documents/{document}/matches: no connected services."""
        if self._require_user():
            document = self._document_param()
            if document:
                self._send_json_response({'document': document, 'services': []})

    def documents_merge(self) -> None:
        """POST /api/v1/documents/merge: make `document` an alias of `into`."""
        user = self._require_user()
        if not user:
            return
        body = self._json_object()
        if body is None:
            return
        if not is_valid_document(body.get('document')) or not is_valid_document(body.get('into')):
            self._invalid()
            return
        result = self.sync_storage.merge_documents(user, body['document'], body['into'], body.get('stats') is not False)
        if result is None:
            self._send_json_error(self.ERROR_INVALID_FIELDS, 'Documents are already merged')
            return
        self._send_json_response(result)

    def documents_unmerge(self) -> None:
        """DELETE /api/v1/documents/merge/{alias}: separate a merged copy again."""
        user = self._require_user()
        if not user:
            return
        alias = self._document_param('alias', code=self.ERROR_INVALID_FIELDS)
        if not alias:
            return
        if not self.sync_storage.unmerge(user, alias):
            self._send_json_error(self.ERROR_INVALID_FIELDS, 'Unknown alias', 404)
            return
        self._send_json_response({'alias': alias, 'unmerged': True})

    # ------------------------------------------------------------------- stats

    def stats_global_put(self) -> None:
        """PUT /api/v1/stats/global: a device's reading statistics snapshot."""
        user = self._require_user()
        if not user:
            return
        body = self._json_object()
        if body is None:
            return
        history = body.get('history_b64')
        valid = (
            is_device_id(body.get('device_id'))
            and all(is_count(body.get(key)) for key in GLOBAL_COUNT_FIELDS)
            and is_count_list(body.get('tod'), 4)
            and is_count_list(body.get('dow'), 7)
            and isinstance(history, str)
            and len(lenient_b64decode(history)) <= HISTORY_BYTES
        )
        daily = parse_daily(body['daily']) if 'daily' in body else []
        if not valid or daily is None:
            self._invalid()
            return
        payload = {key: int(body[key]) for key in GLOBAL_COUNT_FIELDS}
        payload.update({
            'tod': [int(v) for v in body['tod']],
            'dow': [int(v) for v in body['dow']],
            'history_b64': history,
        })
        device = body['device'][:128] if isinstance(body.get('device'), str) else ''
        until = self.sync_storage.store_global_stats(user, body['device_id'], device, payload, daily)
        self._send_json_response({'until': until, 'accepted_daily': len(daily)})

    def stats_global_get(self) -> None:
        """GET /api/v1/stats/global: every device's snapshot."""
        user = self._require_user()
        if user:
            devices, _ = self.sync_storage.global_stats(user)
            self._send_json_response({'devices': devices})

    def stats_books_put(self) -> None:
        """PUT /api/v1/stats/books: a device's statistics for up to 20 books."""
        user = self._require_user()
        if not user:
            return
        body = self._json_object()
        if body is None:
            return
        raw_items = body.get('items')
        if not is_device_id(body.get('device_id')) or not isinstance(raw_items, list) \
                or not 0 < len(raw_items) <= STATS_BOOKS_BATCH_MAX:
            self._invalid()
            return
        items = []
        for item in raw_items:
            valid = (
                isinstance(item, dict)
                and is_valid_document(item.get('document'))
                and all(isinstance(item.get(key), bool) for key in BOOK_FLAG_FIELDS)
                and all(is_count(item.get(key)) for key in BOOK_COUNT_FIELDS)
                and is_count_list(item.get('tod'), 4)
                and is_count_list(item.get('dow'), 7)
            )
            if not valid:
                self._invalid()
                return
            snapshot = {'document': item['document']}
            snapshot.update({key: int(item[key]) for key in BOOK_COUNT_FIELDS})
            snapshot.update({key: item[key] for key in BOOK_FLAG_FIELDS})
            snapshot['tod'] = [int(v) for v in item['tod']]
            snapshot['dow'] = [int(v) for v in item['dow']]
            items.append(snapshot)
        until = self.sync_storage.store_book_stats(user, body['device_id'], items)
        self._send_json_response({'until': until, 'accepted': len(items)})

    def stats_book_get(self) -> None:
        """GET /api/v1/stats/books/{document}: per-device and combined statistics of a book."""
        user = self._require_user()
        if not user:
            return
        document = self._document_param()
        if not document:
            return
        devices = self.sync_storage.book_stats(user, document)
        self._send_json_response({
            'document': document,
            'combined': combine_book_stats([d['stats'] for d in devices]),
            'devices': devices,
        })

    def stats_summary(self) -> None:
        """GET /api/v1/stats/summary: statistics combined across devices."""
        user = self._require_user()
        if user:
            devices, daily = self.sync_storage.global_stats(user)
            self._send_json_response(summarize_global_stats(devices, daily))

    def stats_activity(self) -> None:
        """GET /api/v1/stats/activity?tz=: reading days, pages and finished books from the progress history."""
        user = self._require_user()
        if not user:
            return
        tz = js_number(self._query('tz'), 0)
        if not math.isfinite(tz) or abs(tz) > 840:
            tz = 0
        tz = int(tz) if float(tz).is_integer() else tz
        log_rows, documents, book_stats, aliases = self.sync_storage.activity_inputs(user)
        # Print pages estimated from the library EPUB when the reader did not set them
        marked_finished = {d for d, info in documents.items() if info.get('status') == 'finished'}
        for document in {row['document'] for row in log_rows} | marked_finished:
            info = documents.setdefault(document, {})
            if not info.get('page_count'):
                library_book = self._library_book([document, *aliases.get(document, [])], info.get('filename'), info.get('library_path'))
                if library_book:
                    info['page_count'] = library_book['page_count']
        finished_dates, start_dates = {}, {}
        for document, snapshots in book_stats.items():
            combined = combine_book_stats(snapshots)
            if combined['completed'] and combined['finished_date'] > 0:
                finished_dates[document] = combined['finished_date']
            if combined['start_date'] > 0:
                start_dates[document] = combined['start_date']
        self._send_json_response(compute_activity(log_rows, documents, finished_dates, start_dates, tz))

    # --------------------------------------------------------------- clippings

    def clippings_get(self) -> None:
        """GET /api/v1/clippings/{document}: clipping changes after a revision cursor."""
        user = self._require_user()
        if not user:
            return
        document = self._document_param()
        if not document:
            return
        cursor_raw = self._query('cursor')
        cursor = None
        if cursor_raw is not None:
            value = js_number(cursor_raw, 0)
            if not math.isfinite(value) or not value.is_integer() or not 0 <= value <= SAFE_INTEGER_MAX:
                self._send_json_error(self.ERROR_INVALID_FIELDS, 'Invalid cursor')
                return
            cursor = int(value)
        since, limit = self._list_params()
        rows = self.sync_storage.get_clippings(user, document, cursor, since, limit)
        more = len(rows) > limit
        items = rows[:limit]
        reader_format = self._query('format') == 'reader'
        self._send_json_response({
            'document': document,
            'sync_version': 2,
            'cursor': items[-1]['revision'] if items else (cursor or 0),
            'until': items[-1]['updated_at'] if more else now_seconds(),
            'more': more,
            'items': [clipping_json(row, reader_format) for row in items],
        })

    def clippings_put(self) -> None:
        """PUT /api/v1/clippings/{document}: upload or delete up to 50 clippings, all or nothing."""
        user = self._require_user()
        if not user:
            return
        document = self._document_param()
        if not document:
            return
        body = self._json_object()
        if body is None:
            return
        raw_items = body.get('items')
        if not isinstance(raw_items, list) or not 0 < len(raw_items) <= ITEMS_BATCH_MAX:
            self._invalid()
            return
        operations = [parse_clipping(item) for item in raw_items]
        if any(op is None for op in operations):
            self._invalid()
            return
        until = self.sync_storage.store_clippings(user, document, operations)
        self._send_json_response({'until': until, 'accepted': len(operations)})

    def clippings_all(self) -> None:
        """GET /api/v1/clippings: every live clipping across books."""
        user = self._require_user()
        if not user:
            return
        items = [
            {
                'document': row['document'],
                'id': row['id'],
                'spine': row['spine_index'],
                'para': row['paragraph_index'],
                'start_offset': row['start_offset'],
                'chapter': row['chapter_title'],
                'text': row['text'],
                'note': row['note'],
                'created_at': row['created_at'],
            }
            for row in self.sync_storage.all_clippings(user)
        ]
        self._send_json_response({'items': items})

    # --------------------------------------------------------------- bookmarks

    def bookmarks_get(self) -> None:
        """GET /api/v1/bookmarks/{document}: bookmark changes after `since`."""
        user = self._require_user()
        if not user:
            return
        document = self._document_param()
        if not document:
            return
        since, limit = self._list_params()
        rows = self.sync_storage.get_bookmarks(user, document, since, limit)
        more = len(rows) > limit
        items = rows[:limit]
        self._send_json_response({
            'document': document,
            'until': items[-1]['updated_at'] if more else now_seconds(),
            'more': more,
            'items': [
                {
                    'id': row['id'], 'xpath': row['xpath'], 'percentage': row['percentage'], 'summary': row['summary'],
                    'si': row['spine_index'], 'pc': row['paragraph_count'], 'pp': row['paragraph_pos'],
                    'deleted': 1 if row['deleted'] else 0, 'updated_at': row['updated_at'],
                }
                for row in items
            ],
        })

    def bookmarks_put(self) -> None:
        """PUT /api/v1/bookmarks/{document}: upload or delete up to 50 bookmarks, all or nothing."""
        user = self._require_user()
        if not user:
            return
        document = self._document_param()
        if not document:
            return
        body = self._json_object()
        if body is None:
            return
        raw_items = body.get('items')
        if not isinstance(raw_items, list) or not 0 < len(raw_items) <= ITEMS_BATCH_MAX:
            self._invalid()
            return
        operations = [parse_bookmark(item) for item in raw_items]
        if any(op is None for op in operations):
            self._invalid()
            return
        until = self.sync_storage.store_bookmarks(user, document, operations)
        self._send_json_response({'until': until, 'accepted': len(operations)})

    # ----------------------------------------------------------------- account

    def account_password(self) -> None:
        """POST /api/v1/account/password: change the password (sent as its MD5 `key`)."""
        user = self._require_user()
        if not user:
            return
        ok, body = self._read_json()
        key = body.get('key') if ok and isinstance(body, dict) else None
        if not isinstance(key, str) or not MD5_RE.match(key):
            self._send_json_error(self.ERROR_INVALID_FIELDS, 'Send the new password as its MD5 (key)', 400)
            return
        self.sync_storage.set_password(user, key)
        self._send_json_response({'username': user})

    def account_data_delete(self) -> None:
        """DELETE /api/v1/account/data: delete every synced record, keep the account."""
        user = self._require_user()
        if user:
            self.sync_storage.delete_user_data(user, delete_account=False)
            self._send_json_response({'deleted': True})

    def account_delete(self) -> None:
        """DELETE /api/v1/account: delete the account and all its data."""
        user = self._require_user()
        if user:
            self.sync_storage.delete_user_data(user, delete_account=True)
            self._send_json_response({'deleted': True})

    # -------------------------------------------------------------- connectors

    def connectors_list(self) -> None:
        """GET /api/v1/connectors: no external services are available on this server."""
        if self._require_user():
            self._send_json_response({'encryption': 'enabled', 'connectors': []})

    def connector_unknown(self) -> None:
        """Any other /api/v1/connectors route."""
        if self._require_user():
            self._send_json_error(self.ERROR_INVALID_FIELDS, 'Unknown connector', 404)

    def not_found(self) -> None:
        """Unknown /api/v1 route: JSON 404 so clients can fall back."""
        self._send_json_error(self.ERROR_INVALID_FIELDS, 'Not found', 404)


__all__ = [
    'CrossPointSyncController',
]
