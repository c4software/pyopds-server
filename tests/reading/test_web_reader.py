"""Web reader (/read): EPUB entries and reading statistics."""
import base64
import contextlib
import datetime
import gzip
import hashlib
import importlib
import io
import json
import os
import tempfile
import threading
import time
import unittest
import urllib.parse
from support import ThreadedTCPServer, basic_header, http_request, create_chapter_epub  # noqa: F401  (first: test database, project on sys.path)


class TestWebReader(unittest.TestCase):
    """Web reader (/read): EPUB entries for foliate-js and reading statistics of the web reader device."""

    password = 'lecture'

    @classmethod
    def setUpClass(cls):
        cls._orig_env = {key: os.environ.get(key) for key in ('LIBRARY_DIR', 'OPDS_AUTH', 'OPDS_SESSION_SECRET')}
        cls.library_dir = tempfile.TemporaryDirectory()
        cls.db_dir = tempfile.TemporaryDirectory()
        os.environ['LIBRARY_DIR'] = cls.library_dir.name
        os.environ.pop('OPDS_AUTH', None)
        os.environ['OPDS_SESSION_SECRET'] = 'test-session-secret'
        cls.epub = os.path.join(cls.library_dir.name, 'Romans', 'roman.epub')
        os.makedirs(os.path.dirname(cls.epub))
        create_chapter_epub(cls.epub, 'Le Roman', 'Une Autrice')
        for module in ('controllers.catalog.opds', 'controllers.sync.storage', 'controllers.sync.koreader',
                       'controllers.library.index', 'controllers.sync.crosspoint', 'controllers.accounts.opds_auth',
                       'controllers.reading.web_reader'):
            importlib.reload(importlib.import_module(module))
        importlib.reload(importlib.import_module('routes'))
        cls.server = importlib.reload(importlib.import_module('server'))
        cls.koreader_sync = importlib.import_module('controllers.sync.koreader')
        cls.web_reader = importlib.import_module('controllers.reading.web_reader')
        cls.library_index = importlib.import_module('controllers.library.index')
        sync_storage = importlib.import_module('controllers.sync.storage')
        cls.koreader_sync.SyncControllerBase._sync_storage_instance = sync_storage.SyncStorage(
            os.path.join(cls.db_dir.name, 'sync.db'))
        cls.httpd = ThreadedTCPServer(('localhost', 0), cls.server.UnifiedHandler)
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()
        time.sleep(0.1)
        with contextlib.redirect_stdout(io.StringIO()):
            status, _, _ = http_request(cls.port, 'POST', '/users/create', {'username': 'liseur', 'password': cls.password})
            assert status == 201, status

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.thread.join(timeout=1)
        cls.koreader_sync.SyncControllerBase._sync_storage_instance = None
        cls.library_dir.cleanup()
        cls.db_dir.cleanup()
        for key, value in cls._orig_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        importlib.reload(importlib.import_module('controllers.catalog.opds'))
        importlib.reload(importlib.import_module('server'))

    def _request(self, method, path, body=None, headers=None):
        return http_request(self.port, method, path, body, headers)

    def _auth(self):
        return basic_header('liseur', self.password)

    def _sync_headers(self):
        return {'X-Auth-User': 'liseur', 'X-Auth-Key': hashlib.md5(self.password.encode('utf-8')).hexdigest()}

    def test_reader_page_and_book_entries(self):
        status, headers, body = self._request('GET', '/read?path=Romans/roman.epub')
        self.assertEqual(status, 200)
        page = body.decode('utf-8')
        self.assertIn('x-data="reader"', page)
        self.assertIn('/static/reader/reader.js?v=', page)
        self.assertIn('rel="manifest"', page)
        # Without an account: the book, no sync document
        status, _, body = self._request('GET', '/webui/reader/book?path=' + urllib.parse.quote('Romans/roman.epub'))
        self.assertEqual(status, 200, body)
        info = json.loads(body)
        self.assertEqual((info['title'], info['author'], info['filename']), ('Le Roman', 'Une Autrice', 'roman.epub'))
        self.assertIn('OEBPS/ch2.xhtml', info['entries'])
        self.assertIsNone(info['document'])
        # Signed in: the KOReader id of the file, the one the readers sync
        _, _, body = self._request('GET', '/webui/reader/book?path=Romans/roman.epub', headers=self._auth())
        self.assertEqual(json.loads(body)['document'], self.library_index.koreader_partial_md5(self.epub))
        # One entry: XHTML type, gzip when accepted, cached for good when versioned, 304 when unchanged
        url = '/webui/reader/file?path=Romans/roman.epub&entry=' + urllib.parse.quote('OEBPS/ch1.xhtml')
        status, headers, body = self._request('GET', url)
        self.assertEqual((status, headers['Content-Type']), (200, 'application/xhtml+xml'))
        self.assertIn(b'Chapitre 1, paragraphe 3.', body)
        self.assertIn('no-cache', headers['Cache-Control'])
        status, headers, body = self._request('GET', url + '&v=1', headers={'Accept-Encoding': 'gzip'})
        self.assertEqual(headers['Content-Encoding'], 'gzip')
        self.assertIn(b'paragraphe 3', gzip.decompress(body))
        self.assertIn('immutable', headers['Cache-Control'])
        self.assertTrue(headers['Cache-Control'].startswith('private'))
        status, _, _ = self._request('GET', url, headers={'If-Modified-Since': headers['Last-Modified']})
        self.assertEqual(status, 304)
        # Unknown entries and books, paths outside the library
        self.assertEqual(self._request('GET', '/webui/reader/file?path=Romans/roman.epub&entry=OEBPS/none.xhtml')[0], 404)
        self.assertEqual(self._request('GET', '/webui/reader/file?path=Romans/roman.epub&entry=../../etc/passwd')[0], 404)
        self.assertEqual(self._request('GET', '/webui/reader/book?path=../sync.db')[0], 404)
        self.assertEqual(self._request('GET', '/webui/reader/book?path=absent.epub')[0], 404)

    def test_reading_session_adds_to_the_web_reader_statistics(self):
        session = {'path': 'Romans/roman.epub', 'seconds': 120, 'pages': 4, 'new_session': True, 'completed': False, 'tz': -120,
                   'device_id': 'web-0123456789abcdef', 'device': 'Navigateur (Android)'}
        # Statistics of the first web reader version (one device for every browser): given to the first browser
        storage = self.koreader_sync.SyncControllerBase._sync_storage_instance
        storage.store_global_stats('liseur', self.web_reader.LEGACY_DEVICE_ID, 'Lecteur web', {'v': 5}, [{'date': '2026-01-01', 'seconds': 5}])
        # An account is always required
        self.assertEqual(self._request('POST', '/webui/reader/session', session)[0], 401)
        status, _, body = self._request('POST', '/webui/reader/session', session, self._auth())
        self.assertEqual(status, 200, body)
        document = json.loads(body)['document']
        self.assertEqual(document, self.library_index.koreader_partial_md5(self.epub))
        status, _, body = self._request('POST', '/webui/reader/session', {**session, 'seconds': 60, 'pages': 2, 'new_session': False, 'completed': True}, self._auth())
        self.assertEqual(json.loads(body), {'document': document, 'seconds': 180, 'pages': 6})
        # Combined statistics of the account: the web reader is one device
        _, _, body = self._request('GET', '/api/v1/stats/summary', headers=self._sync_headers())
        summary = json.loads(body)
        self.assertEqual((summary['sessions'], summary['seconds'], summary['pages'], summary['completed']), (1, 180, 6, 1))
        # The browser is one device: its statistics and its position (PUT /api/v1/progress) go together
        self.assertEqual([(d['device_id'], d['device']) for d in summary['devices']], [('web-0123456789abcdef', 'Navigateur (Android)')])
        self.assertEqual(summary['current_streak'], 1)
        self.assertEqual(sum(day['seconds'] for day in summary['daily']), 185)
        _, _, body = self._request('GET', f'/api/v1/stats/books/{document}', headers=self._sync_headers())
        combined = json.loads(body)['combined']
        self.assertEqual((combined['seconds'], combined['pages'], combined['completed'], combined['avg_fwd']), (180, 6, True, 30))
        self.assertGreater(combined['finished_date'], 0)
        # Statistics only: the position is sent by the page itself (PUT /api/v1/progress)
        _, _, body = self._request('GET', '/api/v1/progress', headers=self._sync_headers())
        self.assertEqual(json.loads(body)['items'], [])
        # Invalid uploads
        for bad in ({**session, 'seconds': 7200}, {**session, 'pages': -1}, {**session, 'tz': 'x'},
                    {**session, 'new_session': 1}, {**session, 'path': 'absent.epub'}, {**session, 'device_id': 'cp-1'},
                    {**session, 'device': ' '}):
            self.assertIn(self._request('POST', '/webui/reader/session', bad, self._auth())[0], (400, 404))

    def test_statistics_helpers(self):
        reader = self.web_reader
        self.assertEqual([reader.time_of_day(h) for h in (4, 5, 11, 12, 16, 17, 21, 22)], [3, 0, 0, 1, 1, 2, 2, 3])
        self.assertEqual(reader.history_day(datetime.date(2000, 1, 2)), 1)
        # First day, then two days later: the anchor moves, older days shift
        anchor, history = reader.mark_reading_day(0, '', 100)
        self.assertEqual(anchor, 100)
        anchor, history = reader.mark_reading_day(anchor, history, 102)
        bits = base64.b64decode(history)
        self.assertEqual((anchor, bits[0]), (102, 0b101))
        # An older day sets its bit without moving the anchor
        anchor, history = reader.mark_reading_day(anchor, history, 101)
        self.assertEqual((anchor, base64.b64decode(history)[0]), (102, 0b111))
        local = datetime.datetime(2026, 10, 24, 21, 30, tzinfo=datetime.UTC)
        stats = reader.add_to_global_stats(None, {'seconds': 60, 'pages': 2, 'new_session': True, 'newly_completed': False, 'local': local})
        self.assertEqual((stats['tod'], stats['dow'][5], stats['streak']), ([0, 0, 60, 0], 60, 1))
        book = reader.add_to_book_stats(None, {'seconds': 60, 'pages': 2, 'new_session': True, 'completed': False, 'local': local})
        self.assertEqual((book['start_date'], book['completed'], book['avg_fwd']), (1792800000, False, 30))
