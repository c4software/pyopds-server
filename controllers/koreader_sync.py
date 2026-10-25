"""KOSync-compatible HTTP controller (KoReader, CrossPoint readers) and shared sync helpers."""
import json
import math
import os
import re
import threading
from urllib.parse import parse_qs, unquote, urlparse

from controllers.http_io import read_json, send_body
from controllers.sync_storage import KOREADER_SYNC_DB_PATH, SyncStorage, md5_hex

# One SyncStorage for every request thread (get_sync_storage)
_storage_lock = threading.Lock()
REGISTRATION_DISABLED = os.environ.get('REGISTRATION_DISABLED', '').lower() in ('1', 'true', 'yes')
SERVER_VERSION = '1.0.0'

DOCUMENT_RE = re.compile(r'^[A-Za-z0-9._-]{1,64}$')
USERNAME_RE = re.compile(r'^[A-Za-z0-9._@+-]{1,64}$')
MD5_RE = re.compile(r'^[a-fA-F0-9]{32}$')

PROGRESS_MAX_LENGTH = 4096
DEVICE_MAX_LENGTH = 128
METADATA_MAX_LENGTH = 512
UINT16_MAX = 65535
# Readers send at most 32 KiB; anything far bigger is refused before being read
MAX_BODY_BYTES = 1024 * 1024


def is_valid_document(value: object) -> bool:
    """Document hashes are 1-64 characters of [A-Za-z0-9._-]."""
    return isinstance(value, str) and DOCUMENT_RE.match(value) is not None


def is_uint(value: object, maximum: int) -> bool:
    """JSON integer (5 or 5.0, never a bool) between 0 and maximum."""
    if isinstance(value, bool):
        return False
    if isinstance(value, float):
        if not value.is_integer():
            return False
        value = int(value)
    return isinstance(value, int) and 0 <= value <= maximum


def is_fraction(value: object) -> bool:
    """JSON number between 0 and 1 (huge integers and NaN included in the rejected values)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return 0 <= value <= 1 and math.isfinite(value)


def utf8_length(value: str) -> int:
    """Length of a string in UTF-8 bytes."""
    return len(value.encode('utf-8'))


def parse_position(value: object) -> dict | None:
    """Validate a CrossPoint rich position; anything invalid drops the whole position."""
    if not isinstance(value, dict):
        return None
    position = {}
    for key, maximum in (('pctQ', 1_000_000), ('spine', UINT16_MAX), ('page', UINT16_MAX), ('pages', UINT16_MAX)):
        if not is_uint(value.get(key), maximum):
            return None
        position[key] = int(value[key])
    for key in ('para', 'li'):
        if key in value:
            if not is_uint(value[key], UINT16_MAX):
                return None
            position[key] = int(value[key])
    for key, max_bytes in (('anchor', 48), ('xpath', 120)):
        if key in value:
            if not isinstance(value[key], str) or utf8_length(value[key]) > max_bytes:
                return None
            position[key] = value[key]
    return position


def parse_metadata(value: object) -> dict | None:
    """Keep the file name, title, authors, series, series index and ISBN a reader sends along with its progress.

    CrossPoint readers add `series`, `series_index` and `isbn` (servers of type "Other").
    """
    if not isinstance(value, dict):
        return None

    def clean(field: object) -> str | None:
        return field[:METADATA_MAX_LENGTH] if isinstance(field, str) and field else None

    metadata: dict = {key: clean(value.get(key)) for key in ('filename', 'title', 'authors', 'series', 'isbn')}
    index = value.get('series_index')
    is_number = isinstance(index, (int, float)) and not isinstance(index, bool) and math.isfinite(index)
    metadata['series_index'] = (int(index) if float(index).is_integer() else index) if is_number and 0 <= index < 1e6 else None
    if not metadata['series']:
        metadata['series_index'] = None
    return metadata if any(v is not None for v in metadata.values()) else None


def parse_progress_body(body: object) -> tuple[dict | None, tuple[int, int, str] | None]:
    """Validate a KOSync progress body; returns (record, None) or (None, (status, code, message))."""
    if not isinstance(body, dict):
        return None, (403, SyncControllerBase.ERROR_INVALID_FIELDS, 'Invalid request')
    if not is_valid_document(body.get('document')):
        return None, (403, SyncControllerBase.ERROR_DOCUMENT_FIELD_MISSING, "Field 'document' not provided.")
    progress = body.get('progress')
    if not isinstance(progress, str) or not 0 < len(progress) <= PROGRESS_MAX_LENGTH:
        return None, (403, SyncControllerBase.ERROR_INVALID_FIELDS, 'Invalid request')

    percentage = body.get('percentage')
    if isinstance(percentage, str):
        try:
            percentage = float(percentage) if percentage.strip() else 0.0
        except ValueError:
            percentage = None
    if not is_fraction(percentage):
        return None, (403, SyncControllerBase.ERROR_INVALID_FIELDS, 'Invalid request')

    device = body['device'][:DEVICE_MAX_LENGTH] if isinstance(body.get('device'), str) else ''
    device_id = body.get('device_id')
    device_id = device_id[:DEVICE_MAX_LENGTH] if isinstance(device_id, str) and device_id else device
    return {
        'document': body['document'],
        'progress': progress,
        'percentage': percentage,
        'device': device,
        'device_id': device_id,
        'position': parse_position(body.get('position')),
        'metadata': parse_metadata(body.get('metadata')),
    }, None


class SyncControllerBase:
    """Shared plumbing for the sync controllers: storage, auth headers and JSON I/O."""

    ERROR_NO_DATABASE = 1000
    ERROR_INTERNAL = 2000
    ERROR_UNAUTHORIZED_USER = 2001
    ERROR_USER_EXISTS = 2002
    ERROR_INVALID_FIELDS = 2003
    ERROR_DOCUMENT_FIELD_MISSING = 2004

    _sync_storage_instance: SyncStorage | None = None

    def __init__(self, request_handler):
        self.request = request_handler
        self.sync_storage = get_sync_storage()
        self.params = getattr(request_handler, 'route_params', {}) or {}
        self.query = parse_qs(urlparse(request_handler.path).query)

    def _param(self, name: str) -> str:
        """Decoded path parameter captured by the route pattern."""
        return unquote(self.params.get(name) or '')

    def _query(self, name: str) -> str | None:
        """First value of a query string parameter, None when absent."""
        values = self.query.get(name)
        return values[0] if values else None

    def _authorize(self) -> str | None:
        """Authorize with the x-auth-user / x-auth-key headers (readers, apps); without them, with the web UI
        session (same sign-in for the catalog and the pages). Returns the username."""
        user = self.request.headers.get('X-Auth-User')
        key = self.request.headers.get('X-Auth-Key')
        if not user and not key:
            # Imported here: opds_auth imports this module
            from controllers.opds_auth import request_user
            return request_user(self.request)
        if not user or not key:
            return None
        return self.sync_storage.authenticate(user, key)

    def _require_user(self) -> str | None:
        """Authorize the request or answer 401; returns the username."""
        user = self._authorize()
        if not user:
            self._send_json_error(self.ERROR_UNAUTHORIZED_USER, 'Unauthorized')
        return user

    def _read_json(self) -> tuple[bool, object]:
        """Read the JSON body; returns (False, None) when it is missing or invalid."""
        return read_json(self.request, MAX_BODY_BYTES)

    def _parse_json_body(self) -> dict | None:
        """Read a JSON object body or answer 403 Invalid request (returns None)."""
        ok, body = self._read_json()
        if not ok or not isinstance(body, dict):
            self._send_json_error(self.ERROR_INVALID_FIELDS, 'Invalid request')
            return None
        return body

    def _send_json_response(self, data, status: int = 200) -> None:
        """Send a JSON response."""
        payload = json.dumps(data, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
        send_body(self.request, payload, 'application/json', status, {'Cache-Control': 'no-store'})

    def _send_json_error(self, code: int, message: str, status: int | None = None) -> None:
        """Send a KOSync error body {code, message} with the reference server's HTTP status."""
        # Same HTTP statuses as the reference KOSync server: CrossPoint readers rely on 402
        # to detect an existing user, KoReader shows `message`
        http_status_map = {
            self.ERROR_NO_DATABASE: 500,
            self.ERROR_INTERNAL: 500,
            self.ERROR_UNAUTHORIZED_USER: 401,
            self.ERROR_USER_EXISTS: 402,
            self.ERROR_INVALID_FIELDS: 403,
            self.ERROR_DOCUMENT_FIELD_MISSING: 403,
        }
        self._send_json_response({'code': code, 'message': message}, status=status or http_status_map.get(code, 500))


def get_sync_storage() -> SyncStorage:
    """Shared SyncStorage (created on first use), also used by the OPDS authentication and recommendations."""
    if SyncControllerBase._sync_storage_instance is None:
        # Requests run in parallel threads: the database schema is set up once
        with _storage_lock:
            if SyncControllerBase._sync_storage_instance is None:
                SyncControllerBase._sync_storage_instance = SyncStorage(KOREADER_SYNC_DB_PATH)
    return SyncControllerBase._sync_storage_instance


class KoReaderSyncController(SyncControllerBase):
    """KOSync API: users, progress upload and download, health probe."""

    def health(self) -> None:
        """Health probe; the CrossPoint Sync app checks it before signing in."""
        self._send_json_response({'status': 'ok', 'version': SERVER_VERSION})

    def register(self) -> None:
        """POST /users/create: create an account (password = MD5 of the password)."""
        if REGISTRATION_DISABLED:
            self._send_json_error(self.ERROR_INVALID_FIELDS, 'Registration is disabled')
            return
        payload = self._parse_json_body()
        if payload is None:
            return

        username = payload.get('username')
        password = payload.get('password')
        if not isinstance(username, str) or not USERNAME_RE.match(username) \
                or not isinstance(password, str) or not 0 < len(password) <= 128:
            self._send_json_error(self.ERROR_INVALID_FIELDS, 'Invalid request')
            return

        username = username.lower()
        password_md5 = password.lower() if MD5_RE.match(password) else md5_hex(password)
        if self.sync_storage.create_user(username, password_md5):
            print("User created:", username)
            self._send_json_response({'username': username}, status=201)
        else:
            self._send_json_error(self.ERROR_USER_EXISTS, 'Username is already registered.')

    def login(self) -> None:
        """GET /users/auth: check the x-auth-user / x-auth-key headers."""
        if self._require_user():
            self._send_json_response({'authorized': 'OK'})

    def get_sync_records(self) -> None:
        """GET /syncs/progress/{document}: newest progress across the user's devices."""
        user = self._require_user()
        if not user:
            return
        # Clients like CrossPoint append a query string (?position_kinds=...), excluded by the route
        document = self._param('document')
        if not is_valid_document(document):
            self._send_json_error(self.ERROR_DOCUMENT_FIELD_MISSING, "Field 'document' not provided.")
            return

        row = self.sync_storage.latest_progress(user, document)
        if not row:
            self._send_json_response({})
            return

        response = {
            'document': document,
            'progress': row['progress'],
            'percentage': row['percentage'],
            'device': row['device'],
            'device_id': row['device_id'],
            'timestamp': row['updated_at'],
        }
        if row['position']:
            response['position'] = json.loads(row['position'])
        self._send_json_response(response)

    def store_sync_records(self) -> None:
        """PUT /syncs/progress: store a device's progress."""
        user = self._require_user()
        if not user:
            return
        payload = self._parse_json_body()
        if payload is None:
            return
        record, error = parse_progress_body(payload)
        if error:
            status, code, message = error
            self._send_json_error(code, message, status)
            return

        timestamp = self.sync_storage.store_progress(user, record)
        self._send_json_response({'document': record['document'], 'timestamp': timestamp})


__all__ = [
    'KOREADER_SYNC_DB_PATH',
    'KoReaderSyncController',
    'SyncControllerBase',
    'get_sync_storage',
    'is_fraction',
    'is_valid_document',
    'is_uint',
    'parse_progress_body',
    'utf8_length',
]
