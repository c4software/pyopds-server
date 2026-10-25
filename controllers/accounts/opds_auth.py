"""Accounts of the OPDS catalog and web UI.

The catalog is public by default; signing in is optional and enables what depends on the reader
(the "read next" recommendations, routes declared with auth='user'). OPDS_AUTH=true makes the
whole catalog private (routes declared with auth='opds').

Accounts are the sync accounts: the MD5 of the password is checked against the stored key with
the same function as the KOSync x-auth-user / x-auth-key headers.
- OPDS clients (KoReader, CrossPoint…) use HTTP Basic.
- Browsers use the sign-in page /login, which opens a session (signed cookie).
The sync API, /cover/* and /static/* are never protected (routes declared without auth).
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

from controllers.core.http_io import content_length, read_body, redirect, send_body

REALM = 'PyOPDS'
CHALLENGE = f'Basic realm="{REALM}", charset="UTF-8"'
# One session for every page (catalog, reading statistics, clippings), whether the site is opened over
# HTTP or HTTPS: the cookies are never Secure (a Secure cookie set over HTTPS cannot be replaced over
# HTTP, the sign-in then silently fails). Named *_sid / *_name since the former pyopds_session /
# pyopds_user ones could be Secure and would shadow the new ones.
SESSION_COOKIE = 'pyopds_sid'
# Readable by the pages (not the session itself): shows the logout button and the account name
USER_COOKIE = 'pyopds_name'
SESSION_SECONDS = 90 * 86400
MAX_LOGIN_BODY = 4096

_session_secret: bytes | None = None


def is_private() -> bool:
    """OPDS_AUTH=true: the whole catalog requires an account. Read on every request (toggled in tests)."""
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


def _sync():
    """The sync module (accounts, database), imported on first use: a public catalog never loads it
    (minimal mode). Looked up on each call, tests reload controllers.sync.koreader."""
    from controllers.sync import koreader
    return koreader


def authenticate(user: str, password: str) -> str | None:
    """Sync username matching a user name and password, None otherwise (or when the database fails)."""
    from controllers.sync.storage import md5_hex
    try:
        return _sync().get_sync_storage().authenticate(user, md5_hex(password))
    except Exception:
        # A broken database must not crash the server: the request is simply refused
        return None


def authenticate_basic(header: str | None) -> str | None:
    """Return the sync username matching the Basic credentials, None otherwise."""
    credentials = parse_basic_credentials(header)
    if credentials is None:
        return None
    return authenticate(*credentials)


def _secret() -> bytes:
    """Key signing the session cookies: OPDS_SESSION_SECRET, else a random key kept next to the sync DB."""
    global _session_secret
    if _session_secret is None:
        configured = os.environ.get('OPDS_SESSION_SECRET', '').strip()
        if configured:
            _session_secret = configured.encode('utf-8')
        else:
            path = os.path.join(os.path.dirname(os.path.abspath(_sync().KOREADER_SYNC_DB_PATH)), 'session.secret')
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


def _cookie_header(name: str, value: str, max_age: int, http_only: bool) -> str:
    return f'{name}={value}; Path=/; Max-Age={max_age}; SameSite=Lax' + ('; HttpOnly' if http_only else '')


def request_user(request_handler) -> str | None:
    """Sync account of a request: HTTP Basic credentials (OPDS clients), else the browser session cookie."""
    user = authenticate_basic(request_handler.headers.get('Authorization'))
    return user if user is not None else session_user(_cookies(request_handler).get(SESSION_COOKIE))


def wants_html(request_handler) -> bool:
    """Browser request (web UI) rather than an OPDS client (answered with Basic, no web UI extras).

    Browsers send `Accept: text/html` when navigating, and Fetch metadata (`Sec-Fetch-*`) on every
    request, including the feed the XSLT polyfill fetches again with `fetch()`; OPDS clients send neither.
    """
    headers = request_handler.headers
    return 'text/html' in (headers.get('Accept') or '') or bool(headers.get('Sec-Fetch-Mode'))


def _redirect(request_handler, location: str, cookies: list[str] = ()) -> None:
    redirect(request_handler, location, 303, [*(('Set-Cookie', cookie) for cookie in cookies), ('Cache-Control', 'no-store')])


def safe_next(target: str | None) -> str:
    """Local path to go back to after signing in (never another site)."""
    if not target or not target.startswith('/') or target.startswith('//') or target.startswith('/\\'):
        return '/opds'
    return target


def send_challenge(request_handler) -> None:
    """Answer 401 with the Basic challenge."""
    send_body(request_handler, b'Authentication required', 'text/plain; charset=utf-8', 401,
              {'WWW-Authenticate': CHALLENGE, 'Cache-Control': 'no-store'})


def guard(request_handler, auth: str | None) -> bool:
    """Route middleware: set request_handler.opds_user, return False after asking for an account.

    `auth` is the route's auth option: on 'opds' (catalog) and 'user' routes the reader is identified
    when credentials are sent (HTTP Basic or session cookie). An account is required on 'user' routes,
    and on 'opds' routes when the catalog is private (OPDS_AUTH=true).
    """
    request_handler.opds_user = None
    if auth not in ('opds', 'user'):
        return True
    user = request_user(request_handler)
    if user is None and (auth == 'user' or is_private()):
        if wants_html(request_handler):
            _redirect(request_handler, '/login?next=' + quote(request_handler.path, safe=''))
        else:
            send_challenge(request_handler)
        return False
    request_handler.opds_user = user
    return True


class AuthController:
    """Sign-in (and sign-up) page, session opening and closing for browsers."""

    def __init__(self, request_handler):
        self.request = request_handler

    def login_page(self) -> None:
        """GET /login: sign-in page, with account creation unless REGISTRATION_DISABLED or MINIMAL_MODE."""
        from controllers.catalog.webui import WebUIController
        from controllers.core.features import minimal_mode
        registration = 'closed' if _sync().REGISTRATION_DISABLED or minimal_mode() else 'open'
        WebUIController(self.request).serve_page('login/login.html', {'data-registration="open"': f'data-registration="{registration}"'})

    def login(self) -> None:
        """POST /login: check the sync account and open the session.

        JSON body {username, password} (sign-in page) answers 204 or 401; a plain form post
        (username, password, next) is redirected to `next` or back to the sign-in page.
        """
        raw = read_body(self.request, MAX_LOGIN_BODY)
        if raw is None:
            self._status(413 if content_length(self.request) > 0 else 400)
            return
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
        user = authenticate(username, password) if username else None
        if user is None:
            if is_json:
                self._status(401)
            else:
                _redirect(self.request, '/login?error=1&next=' + quote(target, safe=''))
            return
        cookies = [
            _cookie_header(SESSION_COOKIE, make_session(user), SESSION_SECONDS, True),
            _cookie_header(USER_COOKIE, quote(user, safe=''), SESSION_SECONDS, False),
        ]
        if is_json:
            send_body(self.request, status=204, headers=[*(('Set-Cookie', cookie) for cookie in cookies),
                                                         ('Cache-Control', 'no-store')])
        else:
            _redirect(self.request, target, cookies)

    def logout(self) -> None:
        """GET /logout: close the session and go back to the sign-in page (then to `next`)."""
        target = safe_next(parse_qs(self.request.path.partition('?')[2]).get('next', ['/opds'])[0])
        _redirect(self.request, '/login?next=' + quote(target, safe=''), [
            _cookie_header(SESSION_COOKIE, '', 0, True),
            _cookie_header(USER_COOKIE, '', 0, False),
        ])

    def _status(self, code: int) -> None:
        send_body(self.request, status=code, headers={'Cache-Control': 'no-store'})


__all__ = [
    'AuthController', 'authenticate_basic', 'guard', 'is_private', 'make_session', 'parse_basic_credentials',
    'safe_next', 'send_challenge', 'session_user', 'wants_html',
]
