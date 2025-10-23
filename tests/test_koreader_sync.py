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
os.environ['KOREADER_SYNC_TOKEN'] = 'test-token'

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

server = importlib.import_module('server')


class ThreadedTCPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True


class TestKoReaderSync(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.token = os.environ['KOREADER_SYNC_TOKEN']
        cls.httpd = ThreadedTCPServer(('localhost', 0), server.UnifiedHandler)
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()
        time.sleep(0.1)

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.thread.join(timeout=1)
        if os.path.exists(TEMP_DB.name):
            os.unlink(TEMP_DB.name)

    def _auth_headers(self, headers=None, include_auth=True):
        headers = dict(headers or {})
        if include_auth:
            headers.setdefault('Authorization', f'Bearer {self.token}')
        return headers

    def _post_json(self, path, body, include_auth=True):
        conn = http.client.HTTPConnection('localhost', self.port, timeout=5)
        headers = self._auth_headers({'Content-Type': 'application/json'}, include_auth=include_auth)
        conn.request('POST', path, body=json.dumps(body), headers=headers)
        response = conn.getresponse()
        payload = response.read()
        conn.close()
        data = json.loads(payload.decode('utf-8')) if payload else None
        return response.status, data

    def _get_json(self, path, include_auth=True):
        conn = http.client.HTTPConnection('localhost', self.port, timeout=5)
        headers = self._auth_headers(include_auth=include_auth)
        conn.request('GET', path, headers=headers)
        response = conn.getresponse()
        payload = response.read()
        conn.close()
        data = json.loads(payload.decode('utf-8')) if payload else None
        return response.status, data

    def test_post_and_get_sync_records(self):
        payload = {
            'user': 'alice',
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

        status, data = self._get_json('/koreader/sync?user=alice')
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
            'user': 'alice',
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

        status, data = self._get_json(f'/koreader/sync?user=alice&since={first_epoch}')
        self.assertEqual(status, 200)
        self.assertEqual(data['count'], 1)
        updated_record = data['records'][0]
        self.assertGreater(updated_record['updated_at_epoch'], first_epoch)
        self.assertEqual(updated_record['data']['progress']['percent'], 75)

    def test_requires_authentication(self):
        status, data = self._get_json('/koreader/sync?user=alice', include_auth=False)
        self.assertEqual(status, 401)
        self.assertEqual(data['code'], 401)
        self.assertEqual(data['status'], 'error')

        payload = {
            'user': 'alice',
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
