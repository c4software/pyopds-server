"""Web UI pages that are not OPDS feeds: the reading statistics / sync page and its assets."""
import os
import re

STATIC_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'static')

# Only plain file names with a known web extension are served from static/
STATIC_NAME_RE = re.compile(r'^[A-Za-z0-9_-]+\.(html|js|css|svg)$')
# Asset references in the served pages, versioned so that a proxy caching static files serves the new ones
ASSET_REF_RE = re.compile(r'(/static/([A-Za-z0-9_-]+\.(?:js|css|svg)))"')
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

    def _serve(self, name: str) -> None:
        match = STATIC_NAME_RE.match(name)
        path = os.path.join(STATIC_DIR, name)
        if not match or not os.path.isfile(path):
            self._send_error(404, 'Not found')
            return
        try:
            with open(path, 'rb') as f:
                data = f.read()
            if match.group(1) == 'html':
                data = ASSET_REF_RE.sub(self._versioned_ref, data.decode('utf-8')).encode('utf-8')
        except (OSError, UnicodeDecodeError):
            self._send_error(500, 'Unable to read file')
            return
        self.request.send_response(200)
        self.request.send_header('Content-Type', CONTENT_TYPES[match.group(1)])
        self.request.send_header('Content-Length', str(len(data)))
        self.request.send_header('Cache-Control', 'no-cache')
        self.request.end_headers()
        self.request.wfile.write(data)

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


__all__ = ['WebUIController']
