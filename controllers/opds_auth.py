"""Optional authentication of the OPDS catalog and web UI (OPDS_AUTH=true).

Accounts are the sync accounts: the MD5 of the password is checked against the stored key with
the same function as the KOSync x-auth-user / x-auth-key headers.
- OPDS clients (KoReader, CrossPoint…) use HTTP Basic.
- Browsers are sent to the sign-in page /login, which opens a session (signed cookie).
The sync API, /cover/* and /static/* are never protected (routes declared without auth='opds').
"""
import base64
import binascii
import hashlib
import hmac
import json
import os
import secrets
import time
from urllib.parse import parse_qs, quote

# Module attribute access: tests reload controllers.koreader_sync
from controllers import koreader_sync
from controllers.sync_storage import md5_hex

REALM = 'PyOPDS'
CHALLENGE = f'Basic realm="{REALM}", charset="UTF-8"'
SESSION_COOKIE = 'pyopds_session'
# Readable by the pages (not the session itself): shows the logout button, signs the reading page out
USER_COOKIE = 'pyopds_user'
SESSION_SECONDS = 90 * 86400
MAX_LOGIN_BODY = 4096

_session_secret: bytes | None = None


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


def _secret() -> bytes:
    """Key signing the session cookies: OPDS_SESSION_SECRET, else a random key kept next to the sync DB."""
    global _session_secret
    if _session_secret is None:
        configured = os.environ.get('OPDS_SESSION_SECRET', '').strip()
        if configured:
            _session_secret = configured.encode('utf-8')
        else:
            path = os.path.join(os.path.dirname(os.path.abspath(koreader_sync.KOREADER_SYNC_DB_PATH)), 'session.secret')
            try:
                with open(path, 'rb') as f:
                    _session_secret = f.read().strip()
            except OSError:
                _session_secret = b''
            if len(_session_secret) < 32:
                _session_secret = secrets.token_hex(32).encode('ascii')
                try:
                    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
                    with os.fdopen(fd, 'wb') as f:
                        f.write(_session_secret)
                except OSError:
                    pass  # Not writable: sessions only last until the server restarts
    return _session_secret


def _sign(payload: str) -> str:
    return hmac.new(_secret(), payload.encode('utf-8'), hashlib.sha256).hexdigest()


def make_session(user: str, now: float | None = None) -> str:
    """Session cookie value: user, expiry and their signature."""
    expiry = int((now or time.time()) + SESSION_SECONDS)
    payload = f"{base64.urlsafe_b64encode(user.encode('utf-8')).decode('ascii')}.{expiry}"
    return f'{payload}.{_sign(payload)}'


def session_user(value: str | None, now: float | None = None) -> str | None:
    """User of a valid, unexpired session cookie value, None otherwise."""
    if not value:
        return None
    encoded, _, rest = value.partition('.')
    expiry, _, signature = rest.partition('.')
    if not hmac.compare_digest(_sign(f'{encoded}.{expiry}'), signature):
        return None
    try:
        if int(expiry) < (now or time.time()):
            return None
        return base64.urlsafe_b64decode(encoded.encode('ascii')).decode('utf-8') or None
    except (ValueError, binascii.Error, UnicodeDecodeError):
        return None


def _cookies(request_handler) -> dict[str, str]:
    cookies = {}
    for part in (request_handler.headers.get('Cookie') or '').split(';'):
        name, separator, value = part.strip().partition('=')
        if separator:
            cookies[name] = value
    return cookies


def _cookie_header(request_handler, name: str, value: str, max_age: int, http_only: bool) -> str:
    secure = (request_handler.headers.get('X-Forwarded-Proto') or '').lower() == 'https'
    return (f'{name}={value}; Path=/; Max-Age={max_age}; SameSite=Lax'
            + ('; HttpOnly' if http_only else '') + ('; Secure' if secure else ''))


def _wants_html(request_handler) -> bool:
    """Browser navigation (sent to the sign-in page) rather than an OPDS client (answered with Basic)."""
    return 'text/html' in (request_handler.headers.get('Accept') or '')


def _redirect(request_handler, location: str, cookies: list[str] = ()) -> None:
    request_handler.send_response(303)
    request_handler.send_header('Location', location)
    for cookie in cookies:
        request_handler.send_header('Set-Cookie', cookie)
    request_handler.send_header('Content-Length', '0')
    request_handler.send_header('Cache-Control', 'no-store')
    request_handler.end_headers()


def safe_next(target: str | None) -> str:
    """Local path to go back to after signing in (never another site)."""
    if not target or not target.startswith('/') or target.startswith('//') or target.startswith('/\\'):
        return '/opds'
    return target


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
        user = session_user(_cookies(request_handler).get(SESSION_COOKIE))
    if user is None:
        if _wants_html(request_handler):
            _redirect(request_handler, '/login?next=' + quote(request_handler.path, safe=''))
        else:
            send_challenge(request_handler)
        return False
    request_handler.opds_user = user
    return True


class AuthController:
    """Sign-in page, session opening and closing for browsers (OPDS_AUTH)."""

    def __init__(self, request_handler):
        self.request = request_handler

    def login_page(self) -> None:
        """GET /login: sign-in page (straight to the catalog when OPDS_AUTH is off)."""
        if not is_enabled():
            _redirect(self.request, '/opds')
            return
        from controllers.webui import WebUIController
        WebUIController(self.request).serve_page('login.html')

    def login(self) -> None:
        """POST /login: check the sync account and open the session.

        JSON body {username, password} (sign-in page) answers 204 or 401; a plain form post
        (username, password, next) is redirected to `next` or back to the sign-in page.
        """
        try:
            length = int(self.request.headers.get('Content-Length') or 0)
        except ValueError:
            length = -1
        if length < 0 or length > MAX_LOGIN_BODY:
            self._status(413 if length > 0 else 400)
            return
        raw = self.request.rfile.read(length) if length else b''
        is_json = 'application/json' in (self.request.headers.get('Content-Type') or '')
        try:
            if is_json:
                body = json.loads(raw.decode('utf-8') or '{}')
                if not isinstance(body, dict):
                    raise ValueError
            else:
                body = {key: values[0] for key, values in parse_qs(raw.decode('utf-8')).items()}
        except (ValueError, UnicodeDecodeError):
            self._status(400)
            return
        username = str(body.get('username') or '').strip()
        password = str(body.get('password') or '')
        target = safe_next(body.get('next'))
        user = None
        if is_enabled() and username:
            try:
                user = koreader_sync.get_sync_storage().authenticate(username, md5_hex(password))
            except Exception:
                user = None
        if user is None:
            if is_json:
                self._status(401)
            else:
                _redirect(self.request, '/login?error=1&next=' + quote(target, safe=''))
            return
        cookies = [
            _cookie_header(self.request, SESSION_COOKIE, make_session(user), SESSION_SECONDS, True),
            _cookie_header(self.request, USER_COOKIE, quote(user, safe=''), SESSION_SECONDS, False),
        ]
        if is_json:
            self.request.send_response(204)
            for cookie in cookies:
                self.request.send_header('Set-Cookie', cookie)
            self.request.send_header('Cache-Control', 'no-store')
            self.request.end_headers()
        else:
            _redirect(self.request, target, cookies)

    def logout(self) -> None:
        """GET /logout: close the session and go back to the sign-in page (then to `next`)."""
        target = safe_next(parse_qs(self.request.path.partition('?')[2]).get('next', ['/opds'])[0])
        _redirect(self.request, '/login?next=' + quote(target, safe=''), [
            _cookie_header(self.request, SESSION_COOKIE, '', 0, True),
            _cookie_header(self.request, USER_COOKIE, '', 0, False),
        ])

    def _status(self, code: int) -> None:
        self.request.send_response(code)
        self.request.send_header('Content-Length', '0')
        self.request.send_header('Cache-Control', 'no-store')
        self.request.end_headers()


__all__ = [
    'AuthController', 'authenticate_basic', 'guard', 'is_enabled', 'make_session', 'parse_basic_credentials',
    'safe_next', 'send_challenge', 'session_user',
]
