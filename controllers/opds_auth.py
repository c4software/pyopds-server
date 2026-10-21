"""Optional HTTP Basic authentication of the OPDS catalog and web UI (OPDS_AUTH=true).

Accounts are the sync accounts: the MD5 of the Basic password is checked against the stored
key with the same function as the KOSync x-auth-user / x-auth-key headers.
The sync API and /cover/* are never protected (routes declared without auth='opds').
"""
import base64
import binascii
import os

# Module attribute access: tests reload controllers.koreader_sync
from controllers import koreader_sync
from controllers.sync_storage import md5_hex

REALM = 'PyOPDS'
CHALLENGE = f'Basic realm="{REALM}", charset="UTF-8"'


def is_enabled() -> bool:
    """OPDS_AUTH is read on every request (cheap) so that it can be toggled in tests."""
    return os.environ.get('OPDS_AUTH', '').strip().lower() in ('1', 'true', 'yes')


def parse_basic_credentials(header: str | None) -> tuple[str, str] | None:
    """Decode an `Authorization: Basic …` header into (user, password), UTF-8 (latin-1 fallback)."""
    if not header:
        return None
    scheme, _, token = header.strip().partition(' ')
    if scheme.lower() != 'basic' or not token.strip():
        return None
    try:
        raw = base64.b64decode(token.strip(), validate=True)
    except (binascii.Error, ValueError):
        return None
    try:
        decoded = raw.decode('utf-8')
    except UnicodeDecodeError:
        decoded = raw.decode('latin-1')
    user, separator, password = decoded.partition(':')
    if not separator or not user:
        return None
    return user, password


def authenticate_basic(header: str | None) -> str | None:
    """Return the sync username matching the Basic credentials, None otherwise."""
    credentials = parse_basic_credentials(header)
    if credentials is None:
        return None
    user, password = credentials
    try:
        return koreader_sync.get_sync_storage().authenticate(user, md5_hex(password))
    except Exception:
        # A broken database must not crash the server: the request is simply refused
        return None


def send_challenge(request_handler) -> None:
    """Answer 401 with the Basic challenge."""
    body = b'Authentication required'
    request_handler.send_response(401)
    request_handler.send_header('WWW-Authenticate', CHALLENGE)
    request_handler.send_header('Content-Type', 'text/plain; charset=utf-8')
    request_handler.send_header('Content-Length', str(len(body)))
    request_handler.send_header('Cache-Control', 'no-store')
    request_handler.end_headers()
    request_handler.wfile.write(body)


def guard(request_handler, auth: str | None) -> bool:
    """Route middleware: set request_handler.opds_user and return False after answering 401.

    `auth` is the route's auth option; only 'opds' routes are guarded, and only when OPDS_AUTH is on.
    """
    request_handler.opds_user = None
    if auth != 'opds' or not is_enabled():
        return True
    user = authenticate_basic(request_handler.headers.get('Authorization'))
    if user is None:
        send_challenge(request_handler)
        return False
    request_handler.opds_user = user
    return True


__all__ = ['authenticate_basic', 'guard', 'is_enabled', 'parse_basic_credentials', 'send_challenge']
