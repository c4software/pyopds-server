"""Web UI pages that are not OPDS feeds (reading page, sign-in page), the static assets of the web UI
and the reading status of a catalog book (book sheet)."""
import email.utils
import gzip
import hashlib
import json
import os
import re
from urllib.parse import parse_qs, urlparse

from controllers.core.http_io import not_modified_since, read_json, send_body

STATIC_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), 'static')

# Served from static/: a plain file name, or up to two levels of sub-folders (static/catalog/main.js,
# static/vendor/foliate/view.js), each part without leading dot nor "..", with a known web extension
_PART = r'[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)*'
STATIC_NAME_RE = re.compile(rf'^(?:[A-Za-z0-9_-]+/){{0,2}}{_PART}\.(html|js|css|svg|png|webmanifest)$')
# Asset references in the served pages and in the catalog XSLT, versioned so that a proxy caching
# static files serves the new ones
ASSET_REF_RE = re.compile(rf'(/static/((?:[A-Za-z0-9_-]+/){{0,2}}{_PART}\.(?:js|css|svg|png|webmanifest)))"')
# A versioned URL never changes content: cached for a year (the version changes with the file)
IMMUTABLE = 'public, max-age=31536000, immutable'
# Server-side includes of the pages (include_partials)
PARTIAL_RE = re.compile(r'<!--#include ([a-z-]+)((?: [a-z]+="[^"]*")*) -->')
PARTIAL_ARG_RE = re.compile(r' ([a-z]+)="([^"]*)"')
# Largest JSON body accepted by the web UI endpoints
JSON_BODY_MAX = 4096
# Scripts and stylesheets sent gzip-compressed when the browser accepts it (polyfill: 1.5 MB -> 0.5 MB),
# compressed once per file version
GZIP_TYPES = ('js', 'css', 'svg', 'webmanifest')
_gzip_cache: dict[str, tuple[int, bytes]] = {}


def _gzipped(path: str, mtime: int, data: bytes) -> bytes:
    cached = _gzip_cache.get(path)
    if cached and cached[0] == mtime:
        return cached[1]
    compressed = gzip.compress(data, compresslevel=6, mtime=0)
    _gzip_cache[path] = (mtime, compressed)
    return compressed


def version_asset_refs(text: str) -> str:
    """Add ?v=<mtime> to the "/static/..." references of a page or of the catalog XSLT.

    A new deployment changes the URL of every modified script or stylesheet, so that browsers and
    proxies (which keep the versioned URLs for a year) never mix old and new assets.
    """
    def versioned(match: re.Match) -> str:
        try:
            version = int(os.path.getmtime(os.path.join(STATIC_DIR, match.group(2))))
        except OSError:
            return match.group(0)
        return f'{match.group(1)}?v={version}"'
    return ASSET_REF_RE.sub(versioned, text)


def include_partials(text: str) -> str:
    """Replace each `<!--#include name key="value"… -->` of a page by static/partials/<name>.html, whose
    `{{key}}` placeholders take the given values (markup shared by the pages, e.g. their header), then the
    enabled parts of the server (`{{mode}}`, `{{accounts}}`: controllers.core.features.page_flags); the partials of the
    full server only are left out in minimal mode (controllers.core.features.FULL_PARTIALS)."""
    from controllers.core.features import included_partial, page_flags

    def include(match: re.Match) -> str:
        if not included_partial(match.group(1)):
            return ''
        with open(os.path.join(STATIC_DIR, 'partials', f'{match.group(1)}.html'), encoding='utf-8') as f:
            partial = f.read()
        for key, value in [*PARTIAL_ARG_RE.findall(match.group(2)), *page_flags().items()]:
            partial = partial.replace('{{' + key + '}}', value)
        # A partial may include others (the page header includes the menu)
        return include_partials(partial)

    return PARTIAL_RE.sub(include, text)


def catalog_xslt() -> tuple[str, str]:
    """The catalog XSLT as served (partials included, asset URLs versioned) and its version (hash of the
    content): the feeds reference it as /opds_to_html.xslt?v=<version>, cached for good by the browsers."""
    with open(os.path.join(STATIC_DIR, 'catalog', 'opds_to_html.xslt'), encoding='utf-8') as f:
        text = version_asset_refs(include_partials(f.read()))
    return text, hashlib.md5(text.encode('utf-8')).hexdigest()[:12]


def catalog_xslt_url() -> str:
    """URL of the catalog XSLT named by the feeds, versioned by its content (plain when unreadable)."""
    try:
        return f'/opds_to_html.xslt?v={catalog_xslt()[1]}'
    except OSError:
        return '/opds_to_html.xslt'


def asset_url(name: str) -> str:
    """URL of a static/ asset, versioned by its modification time (see ASSET_REF_RE)."""
    try:
        return f'/static/{name}?v={int(os.path.getmtime(os.path.join(STATIC_DIR, name)))}'
    except OSError:
        return f'/static/{name}'

CONTENT_TYPES = {
    'html': 'text/html; charset=utf-8',
    'js': 'text/javascript; charset=utf-8',
    'css': 'text/css; charset=utf-8',
    'svg': 'image/svg+xml',
    'png': 'image/png',
    'webmanifest': 'application/manifest+json',
}


class WebUIController:
    """Serve the static pages of the web UI (the data comes from the sync API, called by the page)."""

    def __init__(self, request_handler):
        self.request = request_handler
        self.params = getattr(request_handler, 'route_params', {}) or {}

    def reading_page(self) -> None:
        """GET /reading: reading statistics and sync progress page (signs in with the sync account)."""
        self._serve('reading/reading.html')

    def static_file(self) -> None:
        """GET /static/{name} or /static/{folder}/{name}: a web UI asset (scripts, stylesheets) from static/."""
        self._serve(self.params.get('name') or '')

    def library_sync(self) -> None:
        """POST /webui/library/sync: read the new and modified EPUBs of the library now (controllers.library.store);
        answers {added, updated, removed, total, seconds, at}. For signed-in readers, or anyone when the server
        has no accounts (minimal mode with a public catalog)."""
        from controllers.core.features import accounts_enabled
        from controllers.library.store import Library

        if accounts_enabled() and not self.request.opds_user:
            self._send_json({'message': 'Connexion requise'}, 401)
            return
        self._send_json(Library.get_instance().sync())

    def book_status(self) -> None:
        """GET /webui/book-status?path=<EPUB relative to LIBRARY_DIR>: reading status of a catalog book
        for the signed-in reader ({read, status, percentage, finished_date}; status None when never synced)."""
        book = self._library_book(self._query('path'))
        if not book:
            return
        storage, document = book[0], book[1]
        state = storage.reading_state(self.request.opds_user, document) if document else {'status': None, 'percentage': None}
        self._send_json(self._status_body(state))

    def book_status_update(self) -> None:
        """PUT /webui/book-status {path, read, finished_date?}: mark a catalog book as read (manual "finished"
        status) or not read for the signed-in reader; finished_date ('YYYY-MM-DD' or null, left unchanged
        when absent) is the day it was read, used by the reading statistics instead of the day it was marked."""
        from controllers.sync.storage import manual_date

        body = self._read_json()
        if body is None:
            return
        read = body.get('read')
        if not isinstance(read, bool) or not isinstance(body.get('path'), str):
            self._send_json({'message': 'Champs invalides'}, 400)
            return
        dates = {}
        if 'finished_date' in body:
            try:
                dates['finished_date'] = manual_date(body['finished_date'])
            except ValueError:
                self._send_json({'message': 'Date invalide'}, 400)
                return
        book = self._library_book(body['path'])
        if not book:
            return
        storage, _, epub, relative = book
        from controllers.library.index import LibraryIndex

        info = {'filename': os.path.basename(epub), 'library_path': relative}
        try:
            meta = LibraryIndex.get_instance().book_meta(epub)
            info.update(title=meta.get('title'), author=meta.get('author'))
        except OSError:
            pass
        # Not synced yet: KOReader's binary id of the file, so that a later reading on a device continues it
        document = storage.epub_document(self.request.opds_user, epub, relative)
        self._send_json(self._status_body(storage.mark_library_book(self.request.opds_user, document, read, info, dates)))

    @staticmethod
    def _status_body(state: dict) -> dict:
        return {
            'read': state.get('status') == 'finished',
            'status': state.get('status'),
            'percentage': state.get('percentage'),
            'finished_date': state.get('finished_date'),
        }

    def _library_book(self, relative: str) -> tuple | None:
        """(sync storage, user's document or None, EPUB path, relative path) of a library EPUB; answers 404 otherwise."""
        from controllers.sync.koreader import get_sync_storage
        from controllers.library.index import LibraryIndex

        relative = relative.strip().lstrip('/')
        epub = LibraryIndex.resolve_relative(relative) if relative else None
        if not epub:
            self._send_json({'message': 'Livre introuvable'}, 404)
            return None
        storage = get_sync_storage()
        return storage, storage.epub_document(self.request.opds_user, epub, relative, known_only=True), epub, relative

    def _read_json(self) -> dict | None:
        """JSON object body of the request; answers 400 (and returns None) when missing or invalid."""
        _, body = read_json(self.request, JSON_BODY_MAX)
        if not isinstance(body, dict):
            self._send_json({'message': 'Corps de requête invalide'}, 400)
            return None
        return body

    def _send_json(self, data: dict, status: int = 200) -> None:
        self._send_body(json.dumps(data).encode('utf-8'), 'application/json; charset=utf-8', status, {'Cache-Control': 'no-store'})

    def _send_body(self, body: bytes, content_type: str, status: int = 200, headers: dict[str, str] | None = None) -> None:
        """Send a complete response (controllers.core.http_io.send_body)."""
        send_body(self.request, body, content_type, status, headers or {})

    def _query(self, name: str) -> str:
        """First value of a query string parameter ('' when absent)."""
        return (parse_qs(urlparse(self.request.path).query).get(name) or [''])[0]

    def serve_page(self, name: str, replacements: dict[str, str] | None = None) -> None:
        """Serve a page of static/ (e.g. the sign-in page), with versioned asset URLs and text replacements."""
        self._serve(name, replacements)

    def _serve(self, name: str, replacements: dict[str, str] | None = None) -> None:
        match = STATIC_NAME_RE.match(name)
        path = os.path.realpath(os.path.join(STATIC_DIR, name))
        # The name pattern already refuses "..", hidden files and deeper folders: never outside static/
        if not match or not path.startswith(os.path.realpath(STATIC_DIR) + os.sep) or not os.path.isfile(path):
            self._send_error(404, 'Not found')
            return
        try:
            mtime = int(os.path.getmtime(path))
            last_modified = email.utils.formatdate(mtime, usegmt=True)
            # Unchanged since the browser's copy: no body (the XSLT polyfill weighs 1.5 MB)
            if match.group(1) != 'html' and not_modified_since(self.request, mtime):
                send_body(self.request, status=304,
                          headers={'Last-Modified': last_modified, 'Cache-Control': self._cache_control()})
                return
            with open(path, 'rb') as f:
                data = f.read()
            if match.group(1) == 'html':
                text = version_asset_refs(include_partials(data.decode('utf-8')))
                for old, new in (replacements or {}).items():
                    text = text.replace(old, new)
                data = text.encode('utf-8')
        except (OSError, UnicodeDecodeError):
            self._send_error(500, 'Unable to read file')
            return
        headers: dict[str, str] = {}
        if match.group(1) in GZIP_TYPES:
            headers['Vary'] = 'Accept-Encoding'
            if 'gzip' in (self.request.headers.get('Accept-Encoding') or ''):
                data = _gzipped(path, mtime, data)
                headers['Content-Encoding'] = 'gzip'
        if match.group(1) == 'html':
            headers['Cache-Control'] = 'no-cache'
        else:
            headers.update({'Last-Modified': last_modified, 'Cache-Control': self._cache_control()})
        self._send_body(data, CONTENT_TYPES[match.group(1)], 200, headers)

    def _cache_control(self) -> str:
        """Versioned asset URLs are immutable; plain ones are revalidated (Last-Modified)."""
        return IMMUTABLE if 'v=' in self.request.path.partition('?')[2] else 'no-cache'

    def _send_error(self, code: int, message: str) -> None:
        self._send_body(message.encode('utf-8'), 'text/plain; charset=utf-8', code)


__all__ = ['WebUIController', 'asset_url', 'version_asset_refs', 'STATIC_DIR']
