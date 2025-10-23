"""KoReader sync storage and HTTP controller."""
import datetime
import json
import os
import sqlite3
from urllib.parse import parse_qs

KOREADER_SYNC_DB_PATH = os.environ.get('KOREADER_SYNC_DB_PATH', 'koreader_sync.db')
KOREADER_SYNC_TOKEN = os.environ.get('KOREADER_SYNC_TOKEN')


class KoReaderSyncStorage:
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
                    device TEXT NOT NULL,
                    document TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    updated_at REAL NOT NULL,
                    PRIMARY KEY (user, device, document)
                )
                """
            )

    def upsert_record(self, user, device, document, payload):
        timestamp = datetime.datetime.now(datetime.timezone.utc).timestamp()
        serialized_payload = json.dumps(payload, separators=(",", ":"))

        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO sync_records (user, device, document, payload, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(user, device, document) DO UPDATE SET
                    payload=excluded.payload,
                    updated_at=excluded.updated_at
                """,
                (user, device, document, serialized_payload, timestamp),
            )

        return timestamp

    def fetch_records(self, user=None, device=None, since=None, limit=None, offset=None):
        query = "SELECT user, device, document, payload, updated_at FROM sync_records"
        clauses = []
        params = []

        if user:
            clauses.append("user = ?")
            params.append(user)
        if device:
            clauses.append("device = ?")
            params.append(device)
        if since is not None:
            clauses.append("updated_at > ?")
            params.append(since)

        if clauses:
            query += " WHERE " + " AND ".join(clauses)

        query += " ORDER BY updated_at ASC"

        if limit is not None:
            query += " LIMIT ?"
            params.append(limit)
        if offset is not None:
            query += " OFFSET ?"
            params.append(offset)

        with self._get_connection() as conn:
            rows = conn.execute(query, params).fetchall()

        return [self._row_to_record(row) for row in rows]

    def _row_to_record(self, row):
        payload = json.loads(row["payload"])
        return {
            "user": row["user"],
            "device": row["device"],
            "document": row["document"],
            "data": payload,
            "updated_at": datetime.datetime.fromtimestamp(
                row["updated_at"], datetime.timezone.utc
            ).isoformat(),
            "updated_at_epoch": row["updated_at"],
        }


class KoReaderSyncController:
    """Controller for KoReader sync operations."""

    _sync_storage_instance = None

    def __init__(self, request_handler, auth_token=None):
        """
        Initialize KoReader sync controller.
        
        Args:
            request_handler: HTTP request handler instance
            auth_token: Optional authentication token (uses env var if not provided)
        """
        if KoReaderSyncController._sync_storage_instance is None:
            KoReaderSyncController._sync_storage_instance = KoReaderSyncStorage()

        self.request = request_handler
        self.sync_storage = KoReaderSyncController._sync_storage_instance
        self.auth_token = auth_token if auth_token is not None else KOREADER_SYNC_TOKEN

    def get_sync_records(self, parsed_url):
        """Retrieve sync records for a user."""
        if not self._authenticate_request(parsed_url):
            return

        params = parse_qs(parsed_url.query)
        user = params.get('user', [None])[0]
        device = params.get('device', [None])[0]
        since_param = params.get('since', [None])[0]
        limit_param = params.get('limit', [None])[0]
        offset_param = params.get('offset', [None])[0]

        if not user:
            self._send_json_error(400, 'Missing "user" query parameter')
            return

        since = None
        if since_param is not None:
            try:
                since = float(since_param)
            except (TypeError, ValueError):
                self._send_json_error(400, 'Invalid "since" parameter')
                return

        limit = None
        if limit_param is not None:
            try:
                limit = int(limit_param)
                if limit <= 0:
                    raise ValueError
            except (TypeError, ValueError):
                self._send_json_error(400, 'Invalid "limit" parameter')
                return

        offset = None
        if offset_param is not None:
            try:
                offset = int(offset_param)
                if offset < 0:
                    raise ValueError
            except (TypeError, ValueError):
                self._send_json_error(400, 'Invalid "offset" parameter')
                return

        records = self.sync_storage.fetch_records(
            user=user,
            device=device,
            since=since,
            limit=limit,
            offset=offset,
        )

        response_timestamp = datetime.datetime.now(datetime.timezone.utc).isoformat()

        self._send_json_response({
            'status': 'ok',
            'timestamp': response_timestamp,
            'user': user,
            'device': device,
            'count': len(records),
            'records': records,
        })

    def store_sync_records(self, parsed_url):
        """Store sync records from a user."""
        if not self._authenticate_request(parsed_url):
            return

        payload = self._parse_json_body()
        if payload is None:
            return

        user = payload.get('user')
        device = payload.get('device')
        records = (
            payload.get('records')
            or payload.get('documents')
            or payload.get('entries')
        )

        if not user or not device or not isinstance(records, list):
            self._send_json_error(400, 'Invalid payload: "user", "device", and record list required')
            return

        stored_documents = []

        for record in records:
            if not isinstance(record, dict):
                continue

            document = record.get('document')
            if not document:
                continue

            record_payload = dict(record)
            record_payload['document'] = document
            record_payload.setdefault('user', user)
            record_payload.setdefault('device', device)

            timestamp = self.sync_storage.upsert_record(user, device, document, record_payload)

            stored_documents.append({
                'user': user,
                'device': device,
                'document': document,
                'updated_at_epoch': timestamp,
                'updated_at': datetime.datetime.fromtimestamp(
                    timestamp, datetime.timezone.utc
                ).isoformat(),
            })

        if not stored_documents:
            self._send_json_error(400, 'No valid records provided')
            return

        response_timestamp = datetime.datetime.now(datetime.timezone.utc).isoformat()
        self._send_json_response({
            'status': 'ok',
            'stored': len(stored_documents),
            'documents': stored_documents,
            'timestamp': response_timestamp,
        })

    # Helper methods -------------------------------------------------------

    def _parse_json_body(self):
        """Parse JSON request body."""
        content_length = self.request.headers.get('Content-Length')
        if content_length is None:
            self._send_json_error(411, 'Missing Content-Length header')
            return None

        try:
            length = int(content_length)
        except ValueError:
            self._send_json_error(400, 'Invalid Content-Length header')
            return None

        if length <= 0:
            self._send_json_error(400, 'Empty request body')
            return None

        body = self.request.rfile.read(length)

        try:
            return json.loads(body.decode('utf-8'))
        except json.JSONDecodeError:
            self._send_json_error(400, 'Invalid JSON payload')
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
        """Send JSON error response."""
        response = {
            'status': 'error',
            'code': code,
            'error': message,
        }
        self._send_json_response(response, status=code)

    def _authenticate_request(self, parsed_url=None):
        """Verify authentication token if required."""
        if not self.auth_token:
            return True

        token = None
        auth_header = self.request.headers.get('Authorization')
        if auth_header:
            if auth_header.lower().startswith('bearer '):
                token = auth_header.split(' ', 1)[1].strip()
            else:
                token = auth_header.strip()

        if not token:
            token = self.request.headers.get('X-Auth-Token')

        if not token and parsed_url is not None:
            params = parse_qs(parsed_url.query)
            token = params.get('token', [None])[0]

        if token == self.auth_token:
            return True

        self._send_json_error(401, 'Unauthorized')
        return False


class KoReaderSyncHandlerMixin:
    """Mixin providing KoReader sync HTTP helpers (delegates to controller)."""

    def setup_koreader_sync(self, auth_token=None):
        """Initialize KoReader sync controller."""
        self.koreader_controller = KoReaderSyncController(self, auth_token)

    def handle_koreader_sync_get(self, parsed_url):
        """Handle GET request for sync records."""
        self.koreader_controller.get_sync_records(parsed_url)

    def handle_koreader_sync_post(self, parsed_url):
        """Handle POST request to store sync records."""
        self.koreader_controller.store_sync_records(parsed_url)

    def _send_json_error(self, code, message):
        """Send JSON error response (for compatibility)."""
        if hasattr(self, 'koreader_controller'):
            self.koreader_controller._send_json_error(code, message)
        else:
            response = {
                'status': 'error',
                'code': code,
                'error': message,
            }
            payload = json.dumps(response, ensure_ascii=False).encode('utf-8')
            self.send_response(code)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)


__all__ = [
    'KOREADER_SYNC_DB_PATH',
    'KOREADER_SYNC_TOKEN',
    'KoReaderSyncController',
    'KoReaderSyncHandlerMixin',
    'KoReaderSyncStorage',
]
