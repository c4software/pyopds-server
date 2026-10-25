"""HTTP plumbing shared by the controllers: request bodies (announced size checked against a limit,
then raw bytes or JSON) and complete responses."""
import email.utils
import json
from collections.abc import Iterable, Mapping


def content_length(request_handler) -> int:
    """Announced body size (0 when absent, -1 when the header is not a number)."""
    try:
        return int(request_handler.headers.get('Content-Length') or 0)
    except ValueError:
        return -1


def read_body(request_handler, max_bytes: int) -> bytes | None:
    """Body of the request (b'' when there is none); None when its size is invalid or above `max_bytes`."""
    length = content_length(request_handler)
    if length < 0 or length > max_bytes:
        return None
    return request_handler.rfile.read(length) if length else b''


def read_json(request_handler, max_bytes: int) -> tuple[bool, object]:
    """(True, decoded JSON body), or (False, None) when the body is missing, too large or not JSON."""
    raw = read_body(request_handler, max_bytes)
    if not raw:
        return False, None
    try:
        return True, json.loads(raw.decode('utf-8'))
    except (ValueError, RecursionError):
        # ValueError covers invalid UTF-8, invalid JSON and over-long integer literals
        return False, None


def send_body(request_handler, body: bytes = b'', content_type: str | None = None, status: int = 200,
              headers: Mapping[str, str] | Iterable[tuple[str, str]] = ()) -> None:
    """Send a complete response: status, Content-Type (when given), Content-Length, extra headers (a
    mapping, or (name, value) pairs for repeated ones like Set-Cookie), then the body."""
    request_handler.send_response(status)
    if content_type:
        request_handler.send_header('Content-Type', content_type)
    request_handler.send_header('Content-Length', str(len(body)))
    for name, value in (headers.items() if isinstance(headers, Mapping) else headers):
        request_handler.send_header(name, value)
    request_handler.end_headers()
    if body:
        request_handler.wfile.write(body)


def not_modified_since(request_handler, mtime: float) -> bool:
    """Whether the browser's copy (If-Modified-Since) is as recent as `mtime` (unix seconds)."""
    header = request_handler.headers.get('If-Modified-Since')
    if not header:
        return False
    try:
        since = email.utils.parsedate_to_datetime(header).timestamp()
    except (TypeError, ValueError, IndexError):
        return False
    return int(mtime) <= since


def redirect(request_handler, location: str, status: int = 302, headers: Iterable[tuple[str, str]] = ()) -> None:
    """Redirect to `location` (extra headers as (name, value) pairs, e.g. Set-Cookie)."""
    send_body(request_handler, status=status, headers=[('Location', location), *headers])


__all__ = ['content_length', 'not_modified_since', 'read_body', 'read_json', 'redirect', 'send_body']
