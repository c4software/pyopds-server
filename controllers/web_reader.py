"""Web reader of the library EPUBs (/read), built on foliate-js (static/foliate/, vendored).

The browser never downloads the whole EPUB: `/webui/reader/book` lists its zip entries and
`/webui/reader/file` serves them one by one (what foliate-js loads, as it needs them). The reading
position, the clippings and the statistics are those of the sync account (KOSync / CrossPoint Sync
formats), so that the web reader, KOReader and CrossPoint readers continue each other:
- position: PUT /api/v1/progress by the page (KOReader XPointer, CrossPoint rich position);
- clippings: /api/v1/clippings/{document} by the page (CrossPoint offsets: visible code points of a spine item);
- statistics: POST /webui/reader/session, added up here into the snapshots of the browser's sync device
  (CrossPoint Sync statistics: totals, time of day, day of week, reading days, daily seconds).
"""
import base64
import datetime
import email.utils
import gzip
import mimetypes
import os
import re
import zipfile

from controllers.http_io import not_modified_since
from controllers.reading_activity import HISTORY_BYTES, HISTORY_DAYS, lenient_b64decode, longest_streak
from controllers.webui import IMMUTABLE, WebUIController

# Statistics device of the first web reader version (every browser of the account added to it): its
# statistics go to the first browser that sends its own, shown then as one device with its position
LEGACY_DEVICE_ID = 'pyopds-web-reader'
# Sync device of a browser (static/reader/sync.js): its statistics are those of its position
BROWSER_DEVICE_ID_RE = re.compile(r'^web-[0-9a-f]{8,32}$')
DEVICE_NAME_MAX = 64
# Schema version of the statistics snapshots (crosspoint-sync stats_v5)
STATS_VERSION = 5
# Day 0 of the reading-day bitmaps (CrossPoint: days since 2000-01-01)
HISTORY_EPOCH = datetime.date(2000, 1, 1)
# Bounds of one statistics upload (the page sends one every minute)
SESSION_SECONDS_MAX = 3600
SESSION_PAGES_MAX = 2000
TZ_OFFSET_MAX = 840
# Largest EPUB entry served (protects against zip bombs)
ENTRY_SIZE_MAX = 64 * 1024 * 1024
# Entries sent gzip-compressed when the browser accepts it
TEXT_ENTRY_EXTENSIONS = ('.xhtml', '.html', '.htm', '.xml', '.opf', '.ncx', '.css', '.svg', '.smil', '.txt', '.js')
ENTRY_TYPES = {
    '.xhtml': 'application/xhtml+xml', '.opf': 'application/oebps-package+xml', '.ncx': 'application/x-dtbncx+xml',
    '.otf': 'font/otf', '.ttf': 'font/ttf', '.woff': 'font/woff', '.woff2': 'font/woff2', '.smil': 'application/smil+xml',
}


def time_of_day(hour: int) -> int:
    """Time-of-day bucket of the statistics: 0 morning (5-12 h), 1 afternoon (12-17 h), 2 evening (17-22 h), 3 night."""
    if 5 <= hour < 12:
        return 0
    if 12 <= hour < 17:
        return 1
    if 17 <= hour < 22:
        return 2
    return 3


def history_day(day: datetime.date) -> int:
    """Day number of the reading-day bitmaps (days since 2000-01-01)."""
    return (day - HISTORY_EPOCH).days


def mark_reading_day(anchor_day: int, history_b64: str, day: int) -> tuple[int, str]:
    """Set the bit of `day` in a reading-day bitmap (bit 0 = anchor day, bit N = anchor day - N, LSB first),
    moving the anchor forward to `day` when it is newer. Returns the new (anchor_day, history_b64)."""
    bits = lenient_b64decode(history_b64 or '')
    history = bytearray(HISTORY_BYTES)
    shift = day - anchor_day if anchor_day and day > anchor_day else 0
    if anchor_day:
        for n in range(HISTORY_DAYS - shift):
            if n >> 3 < len(bits) and bits[n >> 3] & (1 << (n & 7)):
                history[(n + shift) >> 3] |= 1 << ((n + shift) & 7)
    anchor = max(anchor_day, day)
    offset = anchor - day
    if offset < HISTORY_DAYS:
        history[offset >> 3] |= 1 << (offset & 7)
    return anchor, base64.b64encode(bytes(history)).decode('ascii')


def add_to_global_stats(snapshot: dict | None, session: dict) -> dict:
    """Global statistics snapshot of the web reader device once a reading session is added.

    `session`: {seconds, pages, new_session, newly_completed, local (datetime of the reader's local time)}.
    """
    stats = {
        'v': STATS_VERSION, 'sessions': 0, 'seconds': 0, 'pages': 0, 'completed': 0, 'anchor_day': 0, 'streak': 0,
        'tod': [0] * 4, 'dow': [0] * 7, 'history_b64': '',
        **(snapshot or {}),
    }
    local = session['local']
    stats['v'] = STATS_VERSION
    stats['sessions'] += 1 if session['new_session'] else 0
    stats['seconds'] += session['seconds']
    stats['pages'] += session['pages']
    stats['completed'] += 1 if session['newly_completed'] else 0
    stats['tod'] = list(stats['tod'])
    stats['dow'] = list(stats['dow'])
    stats['tod'][time_of_day(local.hour)] += session['seconds']
    stats['dow'][local.weekday()] += session['seconds']
    if session['seconds']:
        stats['anchor_day'], stats['history_b64'] = mark_reading_day(stats['anchor_day'], stats['history_b64'], history_day(local.date()))
        stats['streak'] = max(stats['streak'], longest_streak(lenient_b64decode(stats['history_b64'])))
    return stats


def add_to_book_stats(snapshot: dict | None, session: dict) -> dict:
    """Book statistics snapshot of the web reader device once a reading session of the book is added
    (`session` as for add_to_global_stats, plus `completed`: the end of the book is reached)."""
    stats = {
        'v': STATS_VERSION, 'sessions': 0, 'seconds': 0, 'pages': 0, 'completed': False, 'avg_fwd': 0, 'pace_n': 0,
        'eta': 0, 'start_manual': False, 'finish_manual': False, 'start_date': 0, 'finished_date': 0,
        'tod': [0] * 4, 'dow': [0] * 7,
        **(snapshot or {}),
    }
    local = session['local']
    midnight = int(datetime.datetime(local.year, local.month, local.day, tzinfo=datetime.UTC).timestamp())
    stats['v'] = STATS_VERSION
    stats['sessions'] += 1 if session['new_session'] else 0
    stats['seconds'] += session['seconds']
    stats['pages'] += session['pages']
    stats['tod'] = list(stats['tod'])
    stats['dow'] = list(stats['dow'])
    stats['tod'][time_of_day(local.hour)] += session['seconds']
    stats['dow'][local.weekday()] += session['seconds']
    # Pace: seconds per page turned forward, over the whole book
    stats['pace_n'] = stats['pages']
    stats['avg_fwd'] = round(stats['seconds'] / stats['pages']) if stats['pages'] else 0
    if not stats['start_date'] and session['seconds']:
        stats['start_date'] = midnight
    if session['completed'] and not stats['completed']:
        stats['completed'] = True
        stats['finished_date'] = midnight
    return stats


def is_count(value: object, maximum: int) -> bool:
    """JSON integer (never a bool) between 0 and maximum."""
    return isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= maximum


class WebReaderController(WebUIController):
    """/read page, EPUB entries for foliate-js and reading statistics of the web reader."""

    def reader_page(self) -> None:
        """GET /read?path=<EPUB relative to LIBRARY_DIR>: the web reader (the page loads the book itself)."""
        self._serve('reader.html')

    def book_info(self) -> None:
        """GET /webui/reader/book?path=…: {path, title, author, filename, version, entries: {name: size}, document}.

        `document` is the signed-in reader's document of the EPUB (the one its devices sync), None when
        nobody is signed in; `version` (the EPUB's mtime) versions the entry URLs.
        """
        book = self._epub(self._query('path'))
        if not book:
            return
        epub, relative = book
        try:
            with zipfile.ZipFile(epub) as archive:
                entries = {info.filename: info.file_size for info in archive.infolist() if not info.is_dir()}
            version = int(os.path.getmtime(epub))
        except (OSError, zipfile.BadZipFile):
            self._send_json({'message': 'EPUB illisible'}, 422)
            return
        meta = self._meta(epub)
        user = self.request.opds_user
        document = None
        if user:
            from controllers.koreader_sync import get_sync_storage

            document = get_sync_storage().epub_document(user, epub, relative)
        self._send_json({
            'path': relative,
            'title': meta.get('title') or os.path.splitext(os.path.basename(epub))[0],
            'author': meta.get('author') or '',
            'filename': os.path.basename(epub),
            'version': version,
            'entries': entries,
            'document': document,
        })

    def book_file(self) -> None:
        """GET /webui/reader/file?path=…&entry=<zip entry>[&v=<version>]: one file of the EPUB (chapter,
        stylesheet, image, font…), cached by the browser (for good when versioned)."""
        book = self._epub(self._query('path'))
        if not book:
            return
        epub, _ = book
        name = self._query('entry')
        try:
            mtime = int(os.path.getmtime(epub))
            if not_modified_since(self.request, mtime):
                self._send_body(b'', None, 304, self._file_headers(mtime))
                return
            with zipfile.ZipFile(epub) as archive:
                info = archive.getinfo(name)
                if info.is_dir() or info.file_size > ENTRY_SIZE_MAX:
                    raise KeyError(name)
                data = archive.read(info)
        except KeyError:
            self._send_json({'message': 'Fichier introuvable dans le livre'}, 404)
            return
        except (OSError, zipfile.BadZipFile, RuntimeError, ValueError):
            # RuntimeError: encrypted entry; ValueError: unsupported compression
            self._send_json({'message': 'EPUB illisible'}, 422)
            return
        extension = os.path.splitext(name)[1].lower()
        content_type = ENTRY_TYPES.get(extension) or mimetypes.guess_type(name)[0] or 'application/octet-stream'
        headers = self._file_headers(mtime)
        if extension in TEXT_ENTRY_EXTENSIONS:
            headers['Vary'] = 'Accept-Encoding'
            if 'gzip' in (self.request.headers.get('Accept-Encoding') or ''):
                data = gzip.compress(data, compresslevel=6, mtime=0)
                headers['Content-Encoding'] = 'gzip'
        self._send_body(data, content_type, 200, headers)

    def reading_session(self) -> None:
        """POST /webui/reader/session {path, seconds, pages, new_session, completed, tz, device_id, device}:
        add reading time to the statistics of the browser's sync device (book and global snapshots, daily seconds).

        seconds: reading time since the last upload (at most an hour); pages: pages turned forward;
        new_session: first upload of a reading session; completed: the end of the book is reached;
        tz: Date#getTimezoneOffset() of the browser (minutes behind UTC), for the day and the hour;
        device_id, device: the browser as a sync device ("web-…", "Navigateur (Android)").
        """
        from controllers.koreader_sync import get_sync_storage
        from controllers.sync_storage import now_seconds

        body = self._read_json()
        if body is None:
            return
        valid = (
            isinstance(body.get('path'), str)
            and is_count(body.get('seconds'), SESSION_SECONDS_MAX)
            and is_count(body.get('pages'), SESSION_PAGES_MAX)
            and isinstance(body.get('new_session'), bool)
            and isinstance(body.get('completed'), bool)
            and isinstance(body.get('tz'), int) and not isinstance(body.get('tz'), bool) and abs(body['tz']) <= TZ_OFFSET_MAX
            and isinstance(body.get('device_id'), str) and BROWSER_DEVICE_ID_RE.match(body['device_id']) is not None
            and isinstance(body.get('device'), str) and 0 < len(body['device'].strip()) <= DEVICE_NAME_MAX
        )
        if not valid:
            self._send_json({'message': 'Champs invalides'}, 400)
            return
        book = self._epub(body['path'])
        if not book:
            return
        epub, relative = book
        user = self.request.opds_user
        storage = get_sync_storage()
        meta = self._meta(epub)
        document = storage.remember_document(user, storage.epub_document(user, epub, relative), {
            'title': meta.get('title'), 'author': meta.get('author'),
            'filename': os.path.basename(epub), 'library_path': relative,
        })
        local = datetime.datetime.fromtimestamp(now_seconds() - body['tz'] * 60, datetime.UTC)
        date = local.date().isoformat()
        device_id, device = body['device_id'], body['device'].strip()
        storage.move_device_stats(user, LEGACY_DEVICE_ID, device_id)
        global_stats, book_stats, day_seconds = storage.device_stats(user, device_id, document, date)
        session = {
            'seconds': body['seconds'], 'pages': body['pages'], 'new_session': body['new_session'],
            'completed': body['completed'], 'local': local,
            'newly_completed': body['completed'] and not (book_stats or {}).get('completed'),
        }
        book_payload = add_to_book_stats(book_stats, session)
        global_payload = add_to_global_stats(global_stats, session)
        storage.store_book_stats(user, device_id, [{'document': document, **book_payload}])
        daily = [{'date': date, 'seconds': day_seconds + body['seconds']}] if body['seconds'] else []
        storage.store_global_stats(user, device_id, device, global_payload, daily)
        self._send_json({'document': document, 'seconds': book_payload['seconds'], 'pages': book_payload['pages']})

    def _epub(self, relative: str) -> tuple[str, str] | None:
        """(EPUB path, path relative to LIBRARY_DIR) of a library EPUB; answers 404 otherwise."""
        from controllers.library_index import LibraryIndex

        relative = (relative or '').strip().lstrip('/')
        epub = LibraryIndex.resolve_relative(relative) if relative else None
        if not epub:
            self._send_json({'message': 'Livre introuvable'}, 404)
            return None
        return epub, relative

    @staticmethod
    def _meta(epub: str) -> dict:
        """Title and author of a library EPUB (empty when its OPF cannot be read)."""
        from controllers.library_index import LibraryIndex

        try:
            return LibraryIndex.get_instance().book_meta(epub)
        except OSError:
            return {}

    def _file_headers(self, mtime: int) -> dict[str, str]:
        """Cache headers of an EPUB entry: private (the catalog may require an account), for good when versioned."""
        versioned = 'v=' in self.request.path.partition('?')[2]
        return {
            'Last-Modified': email.utils.formatdate(mtime, usegmt=True),
            'Cache-Control': IMMUTABLE.replace('public', 'private') if versioned else 'private, no-cache',
        }


__all__ = [
    'LEGACY_DEVICE_ID', 'WebReaderController', 'add_to_book_stats', 'add_to_global_stats',
    'history_day', 'mark_reading_day', 'time_of_day',
]
