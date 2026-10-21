"""Web UI pages that are not OPDS feeds (reading page, sign-in page) and the static assets of the web UI."""
import email.utils
import gzip
import os
import re

STATIC_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'static')

# Only plain file names (no folder, no leading dot) with a known web extension are served from static/
STATIC_NAME_RE = re.compile(r'^[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)*\.(html|js|css|svg)$')
# Asset references in the served pages, versioned so that a proxy caching static files serves the new ones
ASSET_REF_RE = re.compile(r'(/static/([A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)*\.(?:js|css|svg)))"')
# A versioned URL never changes content: cached for a year (the version changes with the file)
IMMUTABLE = 'public, max-age=31536000, immutable'
# Scripts and stylesheets sent gzip-compressed when the browser accepts it (polyfill: 1.5 MB -> 0.5 MB),
# compressed once per file version
GZIP_TYPES = ('js', 'css', 'svg')
_gzip_cache: dict[str, tuple[int, bytes]] = {}


def _gzipped(path: str, mtime: int, data: bytes) -> bytes:
    cached = _gzip_cache.get(path)
    if cached and cached[0] == mtime:
        return cached[1]
    compressed = gzip.compress(data, compresslevel=6, mtime=0)
    _gzip_cache[path] = (mtime, compressed)
    return compressed


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
}


class WebUIController:
    """Serve the static pages of the web UI (the data comes from the sync API, called by the page)."""

    def __init__(self, request_handler):
        self.request = request_handler
        self.params = getattr(request_handler, 'route_params', {}) or {}

    def reading_page(self) -> None:
        """GET /reading: reading statistics and sync progress page (signs in with the sync account)."""
        self._serve('reading.html')

    def static_file(self) -> None:
        """GET /static/{name}: a web UI asset (scripts, stylesheets) from the static/ folder."""
        self._serve(self.params.get('name') or '')

    def serve_page(self, name: str, replacements: dict[str, str] | None = None) -> None:
        """Serve a page of static/ (e.g. the sign-in page), with versioned asset URLs and text replacements."""
        self._serve(name, replacements)

    def _serve(self, name: str, replacements: dict[str, str] | None = None) -> None:
        match = STATIC_NAME_RE.match(name)
        path = os.path.join(STATIC_DIR, name)
        if not match or not os.path.isfile(path):
            self._send_error(404, 'Not found')
            return
        try:
            mtime = int(os.path.getmtime(path))
            last_modified = email.utils.formatdate(mtime, usegmt=True)
            # Unchanged since the browser's copy: no body (the XSLT polyfill weighs 1.5 MB)
            if match.group(1) != 'html' and self._not_modified_since(mtime):
                self.request.send_response(304)
                self.request.send_header('Last-Modified', last_modified)
                self.request.send_header('Cache-Control', self._cache_control())
                self.request.end_headers()
                return
            with open(path, 'rb') as f:
                data = f.read()
            if match.group(1) == 'html':
                text = ASSET_REF_RE.sub(self._versioned_ref, data.decode('utf-8'))
                for old, new in (replacements or {}).items():
                    text = text.replace(old, new)
                data = text.encode('utf-8')
        except (OSError, UnicodeDecodeError):
            self._send_error(500, 'Unable to read file')
            return
        encoding = None
        if match.group(1) in GZIP_TYPES and 'gzip' in (self.request.headers.get('Accept-Encoding') or ''):
            data = _gzipped(path, mtime, data)
            encoding = 'gzip'
        self.request.send_response(200)
        self.request.send_header('Content-Type', CONTENT_TYPES[match.group(1)])
        if encoding:
            self.request.send_header('Content-Encoding', encoding)
        if match.group(1) in GZIP_TYPES:
            self.request.send_header('Vary', 'Accept-Encoding')
        self.request.send_header('Content-Length', str(len(data)))
        if match.group(1) == 'html':
            self.request.send_header('Cache-Control', 'no-cache')
        else:
            self.request.send_header('Last-Modified', last_modified)
            self.request.send_header('Cache-Control', self._cache_control())
        self.request.end_headers()
        self.request.wfile.write(data)

    def _cache_control(self) -> str:
        """Versioned asset URLs are immutable; plain ones are revalidated (Last-Modified)."""
        return IMMUTABLE if 'v=' in self.request.path.partition('?')[2] else 'no-cache'

    def _not_modified_since(self, mtime: int) -> bool:
        header = self.request.headers.get('If-Modified-Since')
        if not header:
            return False
        try:
            since = email.utils.parsedate_to_datetime(header).timestamp()
        except (TypeError, ValueError, IndexError):
            return False
        return mtime <= since

    @staticmethod
    def _versioned_ref(match: re.Match) -> str:
        try:
            version = int(os.path.getmtime(os.path.join(STATIC_DIR, match.group(2))))
        except OSError:
            return match.group(0)
        return f'{match.group(1)}?v={version}"'

    def _send_error(self, code: int, message: str) -> None:
        body = message.encode('utf-8')
        self.request.send_response(code)
        self.request.send_header('Content-Type', 'text/plain; charset=utf-8')
        self.request.send_header('Content-Length', str(len(body)))
        self.request.end_headers()
        self.request.wfile.write(body)


__all__ = ['WebUIController', 'asset_url']
