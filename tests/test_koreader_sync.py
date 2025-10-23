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

server = importlib.import_module('server')


class ThreadedTCPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True


class TestKoReaderSync(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Default test user
        cls.username = 'alice'
        cls.password_plain = 'secret'
        cls.password_md5 = hashlib.md5(cls.password_plain.encode('utf-8')).hexdigest()
        cls.httpd = ThreadedTCPServer(('localhost', 0), server.UnifiedHandler)
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()
        time.sleep(0.1)

        # Register default test user
        status, data = cls._post_json_static(
            cls.port,
            '/koreader/register',
            {'username': cls.username, 'password': cls.password_md5},
            include_auth=False,
        )
        # 200 if created, 409 if already exists (when re-run); both are acceptable
        assert status in (200, 409), f"Unexpected register status: {status} data={data}"

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
        # Register a new user
        username = 'bob'
        password_md5 = hashlib.md5(b'supersecret').hexdigest()
        status, data = self._post_json('/koreader/register', {'username': username, 'password': password_md5}, include_auth=False)
        self.assertIn(status, (200, 409))

        # Login success
        status, data = self._post_json('/koreader/login', {'username': username, 'password': password_md5}, include_auth=False)
        self.assertEqual(status, 200)
        self.assertEqual(data['status'], 'ok')

        # Login failure
        wrong = hashlib.md5(b'wrong').hexdigest()
        status, data = self._post_json('/koreader/login', {'username': username, 'password': wrong}, include_auth=False)
        self.assertEqual(status, 401)
        self.assertEqual(data['status'], 'error')

    def test_post_and_get_sync_records(self):
        payload = {
            'user': self.username,
            'device': 'ereader',
            'records': [
                {
                    'document': 'book1.epub',
                    'progress': {'percent': 50},
                }
            ],
        }

        status, data = self._post_json('/koreader/sync', payload)
        self.assertEqual(status, 200)
        self.assertEqual(data['status'], 'ok')
        self.assertEqual(data['stored'], 1)
        self.assertEqual(len(data['documents']), 1)

        # GET without user query param (auth provides the user)
        status, data = self._get_json('/koreader/sync')
        self.assertEqual(status, 200)
        self.assertEqual(data['status'], 'ok')
        self.assertEqual(data['count'], 1)

        record = data['records'][0]
        self.assertEqual(record['document'], 'book1.epub')
        self.assertEqual(record['device'], 'ereader')
        self.assertIn('progress', record['data'])
        self.assertEqual(record['data']['progress']['percent'], 50)

        first_epoch = record['updated_at_epoch']
        time.sleep(0.05)

        updated_payload = {
            'user': self.username,
            'device': 'ereader',
            'records': [
                {
                    'document': 'book1.epub',
                    'progress': {'percent': 75},
                }
            ],
        }

        status, data = self._post_json('/koreader/sync', updated_payload)
        self.assertEqual(status, 200)
        self.assertEqual(data['stored'], 1)

        status, data = self._get_json(f'/koreader/sync?since={first_epoch}')
        self.assertEqual(status, 200)
        self.assertEqual(data['count'], 1)
        updated_record = data['records'][0]
        self.assertGreater(updated_record['updated_at_epoch'], first_epoch)
        self.assertEqual(updated_record['data']['progress']['percent'], 75)

    def test_requires_authentication(self):
        status, data = self._get_json('/koreader/sync', include_auth=False)
        self.assertEqual(status, 401)
        self.assertEqual(data['code'], 401)
        self.assertEqual(data['status'], 'error')

        payload = {
            'user': self.username,
            'device': 'ereader',
            'records': [
                {'document': 'book2.epub', 'progress': {'percent': 10}},
            ],
        }

        status, data = self._post_json('/koreader/sync', payload, include_auth=False)
        self.assertEqual(status, 401)
        self.assertEqual(data['code'], 401)


if __name__ == '__main__':
    unittest.main()
