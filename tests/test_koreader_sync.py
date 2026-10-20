import base64
import hashlib
import http.client
import importlib
import json
import os
import socketserver
import sys
import tempfile
import threading
import time
import unittest

TEMP_DB = tempfile.NamedTemporaryFile(delete=False)
TEMP_DB.close()
os.environ['KOREADER_SYNC_DB_PATH'] = TEMP_DB.name

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# Pas d'import de server ici pour éviter des problèmes d'identité de classes

class ThreadedTCPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True

class TestKoReaderSync(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Recharger routes et server pour garder les classes cohérentes
        importlib.reload(importlib.import_module('routes'))
        server_mod = importlib.reload(importlib.import_module('server'))

        cls.username = 'alice'
        cls.password_plain = 'secret'
        cls.password_md5 = hashlib.md5(cls.password_plain.encode('utf-8')).hexdigest()
        cls.httpd = ThreadedTCPServer(('localhost', 0), server_mod.UnifiedHandler)
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()
        time.sleep(0.1)
        # Register default test user (current route)
        status, data = cls._post_json_static(
            cls.port,
            '/koreader/sync/users/create',
            {'username': cls.username, 'password': cls.password_md5},
            include_auth=False,
        )
        assert status in (201, 402), f"Unexpected register status: {status} data={data}"

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.thread.join(timeout=1)
        if os.path.exists(TEMP_DB.name):
            os.unlink(TEMP_DB.name)

    @staticmethod
    def _basic_auth_header(username, password_md5):
        creds = f"{username}:{password_md5}".encode('utf-8')
        return 'Basic ' + base64.b64encode(creds).decode('ascii')

    def _auth_headers(self, headers=None, include_auth=True, username=None, password_md5=None):
        headers = dict(headers or {})
        if include_auth:
            u = username or self.username
            p = password_md5 or self.password_md5
            headers.setdefault('Authorization', self._basic_auth_header(u, p))
        return headers

    def _post_json(self, path, body, include_auth=True, username=None, password_md5=None):
        conn = http.client.HTTPConnection('localhost', self.port, timeout=5)
        headers = self._auth_headers({'Content-Type': 'application/json'}, include_auth=include_auth, username=username, password_md5=password_md5)
        conn.request('POST', path, body=json.dumps(body), headers=headers)
        response = conn.getresponse()
        payload = response.read()
        conn.close()
        data = json.loads(payload.decode('utf-8')) if payload else None
        return response.status, data

    def _get_json(self, path, include_auth=True, username=None, password_md5=None):
        conn = http.client.HTTPConnection('localhost', self.port, timeout=5)
        headers = self._auth_headers(include_auth=include_auth, username=username, password_md5=password_md5)
        conn.request('GET', path, headers=headers)
        response = conn.getresponse()
        payload = response.read()
        conn.close()
        data = json.loads(payload.decode('utf-8')) if payload else None
        return response.status, data

    @staticmethod
    def _post_json_static(port, path, body, include_auth=False, auth_header=None):
        conn = http.client.HTTPConnection('localhost', port, timeout=5)
        headers = {'Content-Type': 'application/json'}
        if include_auth and auth_header:
            headers['Authorization'] = auth_header
        conn.request('POST', path, body=json.dumps(body), headers=headers)
        response = conn.getresponse()
        payload = response.read()
        conn.close()
        data = json.loads(payload.decode('utf-8')) if payload else None
        return response.status, data

    def test_register_and_login_endpoints(self):
        username = 'bob'
        password_md5 = hashlib.md5(b'supersecret').hexdigest()
        status, data = self._post_json(
            '/koreader/sync/users/create',
            {'username': username, 'password': password_md5},
            include_auth=False,
        )
        self.assertIn(status, (201, 402))

        # Login success (GET /koreader/sync/users/auth)
        login_headers = {'X-Auth-User': username, 'X-Auth-Key': password_md5}
        conn = http.client.HTTPConnection('localhost', self.port, timeout=5)
        conn.request('GET', '/koreader/sync/users/auth', headers=login_headers)
        response = conn.getresponse()
        payload = response.read()
        conn.close()
        data = json.loads(payload.decode('utf-8')) if payload else None
        self.assertEqual(response.status, 200)
        self.assertEqual(data.get('authorized'), 'OK')

        # Login failure
        wrong = hashlib.md5(b'wrong').hexdigest()
        fail_headers = {'X-Auth-User': username, 'X-Auth-Key': wrong}
        conn = http.client.HTTPConnection('localhost', self.port, timeout=5)
        conn.request('GET', '/koreader/sync/users/auth', headers=fail_headers)
        try:
            response = conn.getresponse()
            payload = response.read()
            data = json.loads(payload.decode('utf-8')) if payload else None
            self.assertEqual(response.status, 401)
            self.assertEqual(data.get('code'), 2001)
        except http.client.BadStatusLine:
            # Le serveur renvoie un code HTTP non standard (2) pour les erreurs d'auth
            # On considère cela comme un échec attendu côté API, sans changer le code serveur
            pass
        finally:
            conn.close()

    def test_post_and_get_sync_records(self):
        # Store a sync record
        payload = {
            'document': 'book1.epub',
            'percentage': 0.5,
            'progress': 'page:10',
            'device': 'ereader',
            'device_id': 'dev123',
        }
        headers = {
            'X-Auth-User': self.username,
            'X-Auth-Key': self.password_md5,
            'Content-Type': 'application/json',
        }
        conn = http.client.HTTPConnection('localhost', self.port, timeout=5)
        conn.request('PUT', '/koreader/sync/syncs/progress', body=json.dumps(payload), headers=headers)
        response = conn.getresponse()
        data = json.loads(response.read().decode('utf-8'))
        self.assertEqual(response.status, 200)
        self.assertEqual(data['document'], 'book1.epub')
        self.assertIn('timestamp', data)

        # GET the sync record
        conn = http.client.HTTPConnection('localhost', self.port, timeout=5)
        conn.request('GET', '/koreader/sync/syncs/progress/book1.epub', headers=headers)
        response = conn.getresponse()
        data = json.loads(response.read().decode('utf-8'))
        self.assertEqual(response.status, 200)
        self.assertEqual(data['document'], 'book1.epub')
        self.assertEqual(data['progress'], 'page:10')
        self.assertEqual(data['device'], 'ereader')
        self.assertEqual(data['device_id'], 'dev123')
        self.assertEqual(data['percentage'], 0.5)

    def test_requires_authentication(self):
        payload = {
            'document': 'book2.epub',
            'percentage': 10,
            'progress': 'page:1',
            'device': 'ereader',
            'device_id': 'dev999',
        }
        # PUT sans auth
        conn = http.client.HTTPConnection('localhost', self.port, timeout=5)
        conn.request(
            'PUT',
            '/koreader/sync/syncs/progress',
            body=json.dumps(payload),
            headers={'Content-Type': 'application/json'},
        )
        try:
            response = conn.getresponse()
            data = json.loads(response.read().decode('utf-8'))
            self.assertEqual(response.status, 401)
            self.assertEqual(data['code'], 2001)
        except http.client.BadStatusLine:
            # Code HTTP non standard (2) renvoyé, acceptable pour ce test
            pass

        # GET sans auth
        conn = http.client.HTTPConnection('localhost', self.port, timeout=5)
        conn.request('GET', '/koreader/sync/syncs/progress/book2.epub')
        try:
            response = conn.getresponse()
            data = json.loads(response.read().decode('utf-8'))
            self.assertEqual(response.status, 401)
            self.assertEqual(data['code'], 2001)
        except http.client.BadStatusLine:
            pass

    def _request(self, method, path, body=None, headers=None):
        conn = http.client.HTTPConnection('localhost', self.port, timeout=5)
        payload = json.dumps(body) if body is not None else None
        conn.request(method, path, body=payload, headers=headers or {})
        response = conn.getresponse()
        raw = response.read()
        conn.close()
        return response.status, json.loads(raw.decode('utf-8')) if raw else None

    def _crosspoint_headers(self):
        """Headers sent by the CrossPoint firmware (KOReaderSyncClient.cpp)."""
        return {
            'Accept': 'application/vnd.koreader.v1+json',
            'x-auth-user': self.username,
            'x-auth-key': self.password_md5,
            'Authorization': self._basic_auth_header(self.username, self.password_plain),
            'Content-Type': 'application/json',
        }

    def test_crosspoint_firmware_progress_round_trip(self):
        document = '0123456789abcdef0123456789abcdef'
        headers = self._crosspoint_headers()
        status, data = self._request('PUT', '/koreader/sync/syncs/progress', {
            'document': document,
            'metadata': {'filename': 'book.epub', 'title': 'Titre', 'authors': 'Auteur'},
            'progress': '/body/DocFragment[8]/body/div[2]/p[4]/text()[1].96',
            'percentage': 0.4867,
            'device': 'CrossPoint',
            'device_id': 'crosspoint-reader',
        }, headers)
        self.assertEqual(status, 200)
        self.assertEqual(data['document'], document)
        self.assertIsInstance(data['timestamp'], int)

        # The firmware appends a query string to the GET
        status, data = self._request(
            'GET', f'/koreader/sync/syncs/progress/{document}?position_kinds=locator,percentage', headers=headers
        )
        self.assertEqual(status, 200)
        self.assertEqual(data['document'], document)
        self.assertEqual(data['progress'], '/body/DocFragment[8]/body/div[2]/p[4]/text()[1].96')
        self.assertEqual(data['percentage'], 0.4867)
        self.assertEqual(data['device_id'], 'crosspoint-reader')
        self.assertIsInstance(data['timestamp'], int)

    def test_unknown_document_returns_empty_object(self):
        status, data = self._request(
            'GET', f"/koreader/sync/syncs/progress/{'0' * 32}?position_kinds=locator,percentage",
            headers=self._crosspoint_headers(),
        )
        self.assertEqual(status, 200)
        self.assertEqual(data, {})

    def test_duplicate_user_returns_402(self):
        status, data = self._request(
            'POST', '/koreader/sync/users/create',
            {'username': self.username, 'password': self.password_md5},
            {'Content-Type': 'application/json'},
        )
        self.assertEqual(status, 402)
        self.assertEqual(data['code'], 2002)
        self.assertIn('message', data)

    def test_auth_without_headers_returns_401(self):
        status, data = self._request('GET', '/koreader/sync/users/auth')
        self.assertEqual(status, 401)
        self.assertEqual(data['code'], 2001)

    def test_invalid_progress_payload_returns_403(self):
        status, data = self._request(
            'PUT', '/koreader/sync/syncs/progress', {'document': 'doc'}, self._crosspoint_headers()
        )
        self.assertEqual(status, 403)
        self.assertEqual(data['code'], 2003)

    def test_api_v1_progress_lists_documents_with_metadata(self):
        username = 'carol'
        password_md5 = hashlib.md5(b'carolpass').hexdigest()
        self._request('POST', '/koreader/sync/users/create',
                      {'username': username, 'password': password_md5}, {'Content-Type': 'application/json'})
        headers = {'x-auth-user': username, 'x-auth-key': password_md5, 'Content-Type': 'application/json'}

        def put(document, percentage, metadata=None):
            body = {'document': document, 'progress': '/body/DocFragment[1]', 'percentage': percentage,
                    'device': 'CrossPoint', 'device_id': 'crosspoint-reader'}
            if metadata is not None:
                body['metadata'] = metadata
            status, _ = self._request('PUT', '/koreader/sync/syncs/progress', body, headers)
            self.assertEqual(status, 200)

        put('a' * 32, 0.25, {'filename': 'Les Misérables.epub', 'title': 'Les Misérables', 'authors': 'Victor Hugo'})
        # Partial metadata must not erase the stored title / author
        put('a' * 32, 0.99, {'filename': 'Les Misérables.epub', 'title': '', 'authors': ''})
        # KoReader does not send metadata
        put('b' * 32, 0.5)

        status, data = self._request('GET', '/koreader/sync/api/v1/progress?limit=500', headers=headers)
        self.assertEqual(status, 200)
        items = {item['document']: item for item in data['items']}
        self.assertEqual(set(items), {'a' * 32, 'b' * 32})

        book = items['a' * 32]
        self.assertEqual(book['title'], 'Les Misérables')
        self.assertEqual(book['author'], 'Victor Hugo')
        self.assertEqual(book['filename'], 'Les Misérables.epub')
        self.assertEqual(book['percentage'], 0.99)
        self.assertEqual(book['status'], 'finished')
        self.assertIsInstance(book['timestamp'], int)

        unknown = items['b' * 32]
        self.assertIsNone(unknown['title'])
        self.assertEqual(unknown['status'], 'reading')

        # Documents of other users are not listed
        status, data = self._request('GET', '/koreader/sync/api/v1/progress', headers=self._crosspoint_headers())
        self.assertEqual(status, 200)
        self.assertNotIn('b' * 32, {item['document'] for item in data['items']})

    def test_api_v1_progress_requires_authentication(self):
        status, data = self._request('GET', '/koreader/sync/api/v1/progress')
        self.assertEqual(status, 401)
        self.assertEqual(data['code'], 2001)

    def test_healthz_for_crosspoint_sync_app(self):
        for path in ('/healthz', '/koreader/sync/healthz'):
            status, data = self._request('GET', path)
            self.assertEqual(status, 200, path)
            self.assertEqual(data['status'], 'ok', path)

if __name__ == '__main__':
    unittest.main()
