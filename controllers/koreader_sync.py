"""KoReader sync storage and HTTP controller."""
import datetime
import json
import os
import sqlite3
from urllib.parse import parse_qs
import base64
import time

KOREADER_SYNC_DB_PATH = os.environ.get('KOREADER_SYNC_DB_PATH', 'koreader_sync.db')

class KoReaderSyncStorage:
    def _ensure_user_table(self):
        with self._get_connection() as conn:
            conn.execute(
                '''
                CREATE TABLE IF NOT EXISTS users (
                    username TEXT PRIMARY KEY,
                    password_md5 TEXT NOT NULL
                )
                '''
            )

    def create_user(self, username, password_md5):
        self._ensure_user_table()
        try:
            with self._get_connection() as conn:
                conn.execute(
                    'INSERT INTO users (username, password_md5) VALUES (?, ?)',
                    (username, password_md5)
                )
            return True
        except sqlite3.IntegrityError:
            return False

    def verify_user(self, username, password_md5):
        self._ensure_user_table()
        with self._get_connection() as conn:
            row = conn.execute(
                'SELECT 1 FROM users WHERE username = ? AND password_md5 = ?',
                (username, password_md5)
            ).fetchone()
        return row is not None

    """SQLite-backed storage for KoReader sync progress."""

    def __init__(self, db_path=KOREADER_SYNC_DB_PATH):
        self.db_path = db_path
        self._ensure_tables()

    def _get_connection(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_tables(self):
        with self._get_connection() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS sync_records (
                    user TEXT NOT NULL,
                    document TEXT NOT NULL,
                    percentage REAL,
                    progress TEXT,
                    device TEXT,
                    device_id TEXT,
                    timestamp REAL,
                    PRIMARY KEY (user, document)
                )
                """
            )

    def upsert_record(self, user, document, percentage, progress, device, device_id, timestamp):
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO sync_records (user, document, percentage, progress, device, device_id, timestamp)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (user, document, percentage, progress, device, device_id, timestamp),
            )

    def fetch_records(self, user, document=None):
        if document:
            query = "SELECT * FROM sync_records WHERE user = ? AND document = ?"
            params = (user, document)
        else:
            query = "SELECT * FROM sync_records WHERE user = ?"
            params = (user,)
        query += " ORDER BY timestamp ASC"
        with self._get_connection() as conn:
            rows = conn.execute(query, params).fetchall()
        return [dict(row) for row in rows]


class KoReaderSyncController:
    ERROR_NO_DATABASE = 1000
    ERROR_INTERNAL = 2000
    ERROR_UNAUTHORIZED_USER = 2001
    ERROR_USER_EXISTS = 2002
    ERROR_INVALID_FIELDS = 2003
    ERROR_DOCUMENT_FIELD_MISSING = 2004

    _sync_storage_instance = None

    def __init__(self, request_handler):
        if KoReaderSyncController._sync_storage_instance is None:
            KoReaderSyncController._sync_storage_instance = KoReaderSyncStorage()
        self.request = request_handler
        self.sync_storage = KoReaderSyncController._sync_storage_instance

    def _is_valid_field(self, field):
        """Check if field is a non-empty string."""
        return isinstance(field, str) and len(field) > 0

    def _is_valid_key_field(self, field):
        """Check if field is a non-empty string without colons."""
        return self._is_valid_field(field) and ":" not in field

    def register(self):
        payload = self._parse_json_body()
        if payload is None:
            return

        username = payload.get('username')
        password_md5 = payload.get('password')
        if not self._is_valid_key_field(username) or not self._is_valid_field(password_md5):
            self._send_json_error(self.ERROR_INVALID_FIELDS, 'Invalid username or password')
            return

        if self.sync_storage.create_user(username, password_md5):
            print("User created:", username)
            self._send_json_response({'username': username}, status=201)
        else:
            self._send_json_error(self.ERROR_USER_EXISTS, 'User already exists')

    def login(self):
        user = self.request.headers.get('X-Auth-User')
        password_md5 = self.request.headers.get('X-Auth-Key')
        if not self._is_valid_key_field(user) or not self._is_valid_field(password_md5):
            self._send_json_error(self.ERROR_INVALID_FIELDS, 'Invalid X-Auth-User or X-Auth-Key')
            return

        if self.sync_storage.verify_user(user, password_md5):
            self._send_json_response({'authorized': "OK"})
        else:
            self._send_json_error(self.ERROR_UNAUTHORIZED_USER, 'Unauthorized: invalid user or password')

    def get_sync_records(self):
        user = self._authorize()
        document = self.request.path.split('syncs/progress/')[1]

        if not user:
            self._send_json_error(self.ERROR_UNAUTHORIZED_USER, 'Unauthorized: invalid user or password')
            return

        if not document:
            self._send_json_error(self.ERROR_DOCUMENT_FIELD_MISSING, 'Missing document parameter')
            return

        if not self._is_valid_key_field(document):
            self._send_json_error(self.ERROR_DOCUMENT_FIELD_MISSING, 'Invalid document parameter')
            return

        records = self.sync_storage.fetch_records(
            user=user,
            document=document,
        )

        if not records:
            self._send_json_response({})
            return

        row = records[0]
        res = {}
        if row['percentage'] is not None:
            res['percentage'] = row['percentage']
        if row['progress'] is not None:
            res['progress'] = row['progress']
        if row['device'] is not None:
            res['device'] = row['device']
        if row['device_id'] is not None:
            res['device_id'] = row['device_id']
        if row['timestamp'] is not None:
            res['timestamp'] = row['timestamp']
        if res:
            res['document'] = document

        self._send_json_response(res)

    def store_sync_records(self):
        user = self._authorize()
        if not user:
            self._send_json_error(self.ERROR_UNAUTHORIZED_USER, 'Unauthorized: invalid user or password')
            return

        payload = self._parse_json_body()

        if payload is None:
            return

        document = payload.get('document')
        percentage_str = payload.get('percentage')
        progress = payload.get('progress')
        device = payload.get('device')
        device_id = payload.get('device_id')

        if not self._is_valid_key_field(document) or not self._is_valid_field(progress) or not self._is_valid_field(device):
            self._send_json_error(self.ERROR_INVALID_FIELDS, 'Invalid payload: document, progress, and device required')
            return

        try:
            percentage = float(percentage_str)
        except (TypeError, ValueError):
            self._send_json_error(self.ERROR_INVALID_FIELDS, 'Invalid percentage')
            return

        timestamp = time.time()

        self.sync_storage.upsert_record(user, document, percentage, progress, device, device_id, timestamp)

        self._send_json_response({
            'document': document,
            'timestamp': timestamp,
        })

    def _authorize(self):
        """Authorize user using X-Auth-User and X-Auth-Key headers."""
        user = self.request.headers.get('X-Auth-User')
        password_md5 = self.request.headers.get('X-Auth-Key')
        if self._is_valid_key_field(user) and self._is_valid_field(password_md5):
            if self.sync_storage.verify_user(user, password_md5):
                return user
        return None

    def _parse_json_body(self):
        """Parse JSON request body."""
        content_length = self.request.headers.get('Content-Length')
        if content_length is None:
            self._send_json_error(self.ERROR_INVALID_FIELDS, 'Missing Content-Length header')
            return None

        try:
            length = int(content_length)
        except ValueError:
            self._send_json_error(self.ERROR_INVALID_FIELDS, 'Invalid Content-Length header')
            return None

        if length <= 0:
            self._send_json_error(self.ERROR_INVALID_FIELDS, 'Empty request body')
            return None

        body = self.request.rfile.read(length)

        try:
            return json.loads(body.decode('utf-8'))
        except json.JSONDecodeError:
            self._send_json_error(self.ERROR_INVALID_FIELDS, 'Invalid JSON payload')
            return None

    def _send_json_response(self, data, status=200):
        """Send JSON response."""
        payload = json.dumps(data, ensure_ascii=False).encode('utf-8')
        self.request.send_response(status)
        self.request.send_header('Content-Type', 'application/json')
        self.request.send_header('Content-Length', str(len(payload)))
        self.request.end_headers()
        self.request.wfile.write(payload)

    def _send_json_error(self, code, message):
        """Send JSON error response with custom code."""
        # Mapping explicite des codes d'erreur vers les codes HTTP standards
        http_status_map = {
            self.ERROR_NO_DATABASE: 500,
            self.ERROR_INTERNAL: 500,
            self.ERROR_UNAUTHORIZED_USER: 401,
            self.ERROR_USER_EXISTS: 409,
            self.ERROR_INVALID_FIELDS: 400,
            self.ERROR_DOCUMENT_FIELD_MISSING: 400,
        }
        http_status = http_status_map.get(code, 500)
        
        response = {
            'status': 'error',
            'code': code,
            'error': message,
        }
        self._send_json_response(response, status=http_status)

    def _extract_basic_auth(self, parsed_url=None):
        """Extract username and password from Authorization: Basic header."""
        auth_header = self.request.headers.get('Authorization')
        if auth_header and auth_header.lower().startswith('basic '):
            try:
                b64 = auth_header.split(' ', 1)[1].strip()
                decoded = base64.b64decode(b64).decode('utf-8')
                username, password_md5 = decoded.split(':', 1)
                return username, password_md5
            except Exception:
                return None, None
        return None, None


__all__ = [
    'KOREADER_SYNC_DB_PATH',
    'KoReaderSyncController',
    'KoReaderSyncStorage',
]

