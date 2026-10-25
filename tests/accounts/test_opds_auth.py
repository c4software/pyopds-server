"""Accounts of the catalog (OPDS_AUTH, sessions) and the personal feeds."""
import contextlib
import hashlib
import http.client
import importlib
import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.parse
import xml.etree.ElementTree as ET
from unittest import mock
from support import ThreadedTCPServer, create_epub, basic_header, http_request, PROJECT_ROOT  # noqa: F401  (first: test database, project on sys.path)


class TestOPDSAuthAndRecommendations(unittest.TestCase):
    """OPDS_AUTH (HTTP Basic with the sync accounts) and the "Read Next" recommendations feed."""

    ns = {'atom': 'http://www.w3.org/2005/Atom'}
    password = 'pässwörd ünicode'

    @classmethod
    def setUpClass(cls):
        cls._orig_env = {key: os.environ.get(key) for key in ('LIBRARY_DIR', 'PAGE_SIZE', 'OPDS_AUTH', 'OPDS_SESSION_SECRET')}
        cls.library_dir = tempfile.TemporaryDirectory()
        cls.db_dir = tempfile.TemporaryDirectory()
        os.environ['LIBRARY_DIR'] = cls.library_dir.name
        os.environ['PAGE_SIZE'] = '25'
        os.environ.pop('OPDS_AUTH', None)
        # Session signing key from the environment: no session.secret file written by the tests
        os.environ['OPDS_SESSION_SECRET'] = 'test-session-secret'
        books = {
            'dune.epub': ('Dune', 'Frank Herbert', ['Science Fiction']),
            'messiah.epub': ('Dune Messiah', 'Frank Herbert', ['Science Fiction']),
            'hyperion.epub': ('Hyperion', 'Dan Simmons', ['Science Fiction']),
            'cuisine.epub': ('Cuisine facile', 'Chef Anonyme', ['Cuisine']),
            'abandoned.epub': ('Abandoned', 'Frank Herbert', ['Science Fiction']),
        }
        for name, (title, author, subjects) in books.items():
            create_epub(os.path.join(cls.library_dir.name, name), title, author, subjects=subjects)
        # A series of its own (author and genre read by nobody else): « Suite de vos séries »
        for index in (1, 2, 3):
            create_epub(os.path.join(cls.library_dir.name, f'saga{index}.epub'), f'Saga {index}', 'Auteur Saga',
                        subjects=['Poésie'], series='La Saga', series_index=index)
        for module in ('controllers.catalog.opds', 'controllers.sync.storage', 'controllers.sync.koreader',
                       'controllers.library.index', 'controllers.reading.state', 'controllers.reading.recommendations',
                       'controllers.sync.crosspoint', 'controllers.accounts.opds_auth', 'controllers.reading.reader_feeds'):
            importlib.reload(importlib.import_module(module))
        importlib.reload(importlib.import_module('routes'))
        cls.server = importlib.reload(importlib.import_module('server'))
        cls.koreader_sync = importlib.import_module('controllers.sync.koreader')
        sync_storage = importlib.import_module('controllers.sync.storage')
        cls.koreader_sync.SyncControllerBase._sync_storage_instance = sync_storage.SyncStorage(
            os.path.join(cls.db_dir.name, 'sync.db'))
        cls.httpd = ThreadedTCPServer(('localhost', 0), cls.server.UnifiedHandler)
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()
        time.sleep(0.1)
        with contextlib.redirect_stdout(io.StringIO()):
            for user in ('reader', 'newbie', 'other', 'shelves'):
                status, _, _ = http_request(cls.port, 'POST', '/users/create', {'username': user, 'password': cls.password})
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

    def _sync_headers(self, user):
        return {'X-Auth-User': user, 'X-Auth-Key': hashlib.md5(self.password.encode('utf-8')).hexdigest()}

    def _feed(self, body):
        text = '\n'.join(line for line in body.decode('utf-8').split('\n') if not line.strip().startswith('<?'))
        return ET.fromstring(text)

    def _push_progress(self, user, filename, percentage, status=None):
        document = hashlib.md5(filename.encode('utf-8')).hexdigest()
        status_code, _, _ = self._request('PUT', '/syncs/progress', {
            'document': document, 'progress': '/body/DocFragment[1]', 'percentage': percentage,
            'device': 'test', 'device_id': 'test-device',
        }, self._sync_headers(user))
        self.assertEqual(status_code, 200)
        if status:
            status_code, _, _ = self._request('PUT', f'/api/v1/documents/{document}/status', {'status': status},
                                              self._sync_headers(user))
            self.assertEqual(status_code, 200)

    def test_public_catalog_with_optional_account(self):
        browser = {'Accept': 'text/html,application/xhtml+xml,*/*;q=0.8'}
        # Public catalog: no account needed
        status, _, body = self._request('GET', '/opds', headers=browser)
        self.assertEqual(status, 200)
        # Browsers without an account get the header's sign-in button instead of the tile
        self.assertNotIn(b'urn:recommendations', body)
        self.assertEqual(self._request('GET', '/opds/recommendations', headers=browser)[1].get('Location'),
                         '/login?next=%2Fopds%2Frecommendations')
        # OPDS clients see the tile, opening it asks for their credentials
        self.assertIn(b'urn:recommendations', self._request('GET', '/opds')[2])
        status, headers, _ = self._request('GET', '/opds/recommendations')
        self.assertEqual((status, headers.get('WWW-Authenticate')), (401, 'Basic realm="PyOPDS", charset="UTF-8"'))
        good = basic_header('reader', self.password)
        self.assertEqual(self._request('GET', '/opds/recommendations', headers=good)[0], 200)
        # Wrong credentials on the public catalog: simply anonymous
        self.assertEqual(self._request('GET', '/opds', headers=basic_header('reader', 'wrong'))[0], 200)

    def test_catalog_requires_basic_auth_when_enabled(self):
        with mock.patch.dict(os.environ, {'OPDS_AUTH': 'true'}):
            for path in ('/', '/opds', '/opds/books', '/opds/search?q=dune', '/opds/opensearch.xml', '/opds_to_html.xslt',
                         '/download/dune.epub', '/reading', '/opds/recommendations'):
                status, headers, _ = self._request('GET', path)
                self.assertEqual(status, 401, path)
                self.assertEqual(headers.get('WWW-Authenticate'), 'Basic realm="PyOPDS", charset="UTF-8"')
            self.assertEqual(self._request('GET', '/opds', headers=basic_header('reader', 'wrong'))[0], 401)
            self.assertEqual(self._request('GET', '/opds', headers=basic_header('nobody', self.password))[0], 401)
            self.assertEqual(self._request('GET', '/opds', headers={'Authorization': 'Basic !!!'})[0], 401)
            good = basic_header('reader', self.password)
            status, _, body = self._request('GET', '/opds', headers=good)
            self.assertEqual(status, 200)
            self.assertIn(b'urn:recommendations', body)
            self.assertEqual(self._request('GET', '/download/dune.epub', headers=good)[0], 200)
            # Page assets hold no library data: public, the sign-in page needs them
            self.assertEqual(self._request('GET', '/static/reading/reading.js')[0], 200)

    def test_minimal_mode_keeps_the_catalog_only(self):
        good = basic_header('reader', self.password)
        browser = {'Accept': 'text/html'}
        with mock.patch.dict(os.environ, {'MINIMAL_MODE': 'true'}):
            # Catalog, its web view and the books
            for path in ('/opds', '/opds/books', '/opds/search?q=dune', '/download/dune.epub', '/static/catalog/main.js'):
                self.assertEqual(self._request('GET', path)[0], 200, path)
            # No reader's feeds, tiles nor filter, even signed in
            status, _, body = self._request('GET', '/opds', headers=good)
            self.assertEqual(status, 200)
            self.assertNotIn(b'urn:recommendations', body)
            self.assertNotIn(b'urn:reading', body)
            self.assertNotIn(b'facetGroup', self._request('GET', '/opds/books', headers=good)[2])
            # Pages, web reader and sync APIs are gone
            for method, path in (('GET', '/reading'), ('GET', '/clippings'), ('GET', '/read?path=dune.epub'),
                                 ('GET', '/opds/recommendations'), ('GET', '/opds/reading'), ('GET', '/healthz'),
                                 ('GET', '/users/auth'), ('GET', '/api/v1/progress'), ('GET', '/koreader/sync/users/auth'),
                                 ('POST', '/users/create'), ('GET', '/webui/book-status?path=dune.epub'),
                                 ('GET', '/index.php/apps/notes/api/v1/notes')):
                self.assertEqual(self._request(method, path, headers=good)[0], 404, path)
            # The web view hides the reader's sections and the sign-in of a public catalog
            xslt = self._request('GET', '/opds_to_html.xslt')[2].decode('utf-8')
            self.assertIn('data-mode="minimal"', xslt)
            self.assertIn('data-accounts="off"', xslt)
            self.assertRegex(xslt, r'href="/reading" data-nav="reading" class="nav-link hidden ')
            self.assertNotIn('/static/catalog/read-status.js', xslt)
            self.assertIn(b'data-registration="closed"', self._request('GET', '/login', headers=browser)[2])
            # A private catalog keeps its sign-in, with the existing accounts
            with mock.patch.dict(os.environ, {'OPDS_AUTH': 'true'}):
                self.assertEqual(self._request('GET', '/opds')[0], 401)
                self.assertEqual(self._request('GET', '/opds', headers=good)[0], 200)
                self.assertIn('data-accounts="on"', self._request('GET', '/opds_to_html.xslt', headers=good)[2].decode('utf-8'))
        xslt = self._request('GET', '/opds_to_html.xslt')[2].decode('utf-8')
        self.assertIn('data-mode="full"', xslt)
        self.assertRegex(xslt, r'href="/reading" data-nav="reading" class="nav-link flex ')
        self.assertIn('/static/catalog/read-status.js?v=', xslt)

    def test_library_sync_button(self):
        good = basic_header('reader', self.password)
        # Signed-in readers only, everyone when the server has no accounts (minimal mode, public catalog)
        self.assertEqual(self._request('POST', '/webui/library/sync', {})[0], 401)
        status, _, body = self._request('POST', '/webui/library/sync', {}, good)
        self.assertEqual(status, 200)
        result = json.loads(body)
        self.assertEqual((result['added'], result['removed']), (0, 0))
        self.assertGreater(result['total'], 0)
        with mock.patch.dict(os.environ, {'MINIMAL_MODE': 'true'}):
            self.assertEqual(self._request('POST', '/webui/library/sync', {})[0], 200)
        self.assertEqual(self._request('GET', '/opds/refresh')[0], 404)

    def test_minimal_mode_does_not_load_the_full_server_modules(self):
        # Controllers of the sync, the web reader and the reader's data are imported on their first request only
        script = ('import sys, server; '
                  'print(" ".join(sorted(m for m in sys.modules if m.startswith("controllers."))))')
        env = {**os.environ, 'MINIMAL_MODE': 'true'}
        loaded = subprocess.run([sys.executable, '-c', script], cwd=PROJECT_ROOT, env=env, capture_output=True,
                                text=True, check=True).stdout.split()
        self.assertIn('controllers.catalog.opds', loaded)
        for module in ('controllers.sync.koreader', 'controllers.sync.storage', 'controllers.sync.crosspoint',
                       'controllers.reading.web_reader', 'controllers.reading.reader_feeds', 'controllers.reading.clippings', 'controllers.library.online_metadata',
                       'controllers.audiobook.controller', 'controllers.audiobook.config', 'controllers.audiobook.speech_client'):
            self.assertNotIn(module, loaded)

    def test_browsers_sign_in_with_a_session(self):
        browser = {'Accept': 'text/html,application/xhtml+xml,*/*;q=0.8'}
        with mock.patch.dict(os.environ, {'OPDS_AUTH': 'true'}):
            # Browser navigation: sent to the sign-in page (no Basic pop-up), OPDS clients still get Basic
            status, headers, _ = self._request('GET', '/opds/books?page=2', headers=browser)
            self.assertEqual((status, headers.get('Location')), (303, '/login?next=%2Fopds%2Fbooks%3Fpage%3D2'))
            self.assertNotIn('WWW-Authenticate', headers)
            status, headers, body = self._request('GET', '/login')
            self.assertEqual(status, 200)
            self.assertIn(b'/static/login/login.js', body)

            self.assertEqual(self._request('POST', '/login', {'username': 'reader', 'password': 'wrong'})[0], 401)
            # Both cookies are needed: read them with http.client (the helper keeps one header per name)
            conn = http.client.HTTPConnection('localhost', self.port, timeout=10)
            # Behind an HTTPS proxy too: never Secure, the same session works over HTTP and HTTPS
            conn.request('POST', '/login', body=json.dumps({'username': 'reader', 'password': self.password}),
                         headers={'Content-Type': 'application/json', 'X-Forwarded-Proto': 'https'})
            response = conn.getresponse()
            response.read()
            self.assertEqual(response.status, 204)
            set_cookies = response.headers.get_all('Set-Cookie')
            conn.close()
            session = next(c for c in set_cookies if c.startswith('pyopds_sid='))
            self.assertIn('HttpOnly', session)
            self.assertIn('pyopds_name=reader', ' '.join(set_cookies))
            self.assertNotIn('Secure', ' '.join(set_cookies))
            cookie = {'Cookie': session.split(';')[0]}
            status, _, body = self._request('GET', '/opds', headers={**browser, **cookie})
            self.assertEqual(status, 200)
            self.assertIn(b'urn:recommendations', body)
            # One sign-in for everything: the reading page and the sync API calls of the pages use the session
            self.assertEqual(self._request('GET', '/reading', headers={**browser, **cookie})[0], 200)
            self.assertEqual(self._request('GET', '/reading', headers=browser)[0], 303)
            status, _, body = self._request('GET', '/api/v1/progress', headers=cookie)
            self.assertEqual(status, 200, body)
            self.assertEqual(self._request('GET', '/api/v1/progress')[0], 401)
            # Explicit sync credentials still win (readers, apps): a wrong key is refused despite the session
            bad_key = {**cookie, 'X-Auth-User': 'reader', 'X-Auth-Key': '0' * 32}
            self.assertEqual(self._request('GET', '/api/v1/progress', headers=bad_key)[0], 401)
            # Tampered session: back to the sign-in page
            tampered = {'Cookie': session.split(';')[0].replace('pyopds_sid=', 'pyopds_sid=x')}
            self.assertEqual(self._request('GET', '/opds', headers={**browser, **tampered})[0], 303)

            # Plain form post (no JavaScript): redirected to `next`, never to another site
            for target, expected in (('/reading', '/reading'), ('//evil.example', '/opds')):
                conn = http.client.HTTPConnection('localhost', self.port, timeout=10)
                form = urllib.parse.urlencode({'username': 'reader', 'password': self.password, 'next': target})
                conn.request('POST', '/login', body=form, headers={'Content-Type': 'application/x-www-form-urlencoded'})
                response = conn.getresponse()
                response.read()
                self.assertEqual((response.status, response.headers['Location']), (303, expected))
                conn.close()

            status, headers, _ = self._request('GET', '/logout?next=/reading')
            self.assertEqual((status, headers.get('Location')), (303, '/login?next=%2Freading'))
        # Public catalog: the sign-in page and sessions work the same
        status, _, body = self._request('GET', '/login')
        self.assertEqual(status, 200)
        self.assertIn(b'data-registration="open"', body)
        status, _, body = self._request('GET', '/opds', headers={**browser, **cookie})
        self.assertIn(b'urn:recommendations', body)
        with mock.patch.object(self.koreader_sync, 'REGISTRATION_DISABLED', True):
            self.assertIn(b'data-registration="closed"', self._request('GET', '/login')[2])

    def test_session_cookie_signature_and_expiry(self):
        opds_auth = importlib.import_module('controllers.accounts.opds_auth')
        value = opds_auth.make_session('réader', now=1000)
        self.assertEqual(opds_auth.session_user(value, now=2000), 'réader')
        self.assertIsNone(opds_auth.session_user(value, now=1000 + opds_auth.SESSION_SECONDS + 1))
        self.assertIsNone(opds_auth.session_user(value[:-1] + ('0' if value[-1] != '0' else '1'), now=2000))
        self.assertIsNone(opds_auth.session_user('garbage', now=2000))
        self.assertEqual(opds_auth.safe_next('/opds?x=1'), '/opds?x=1')
        for unsafe in (None, '', 'https://evil.example', '//evil.example', '/\\evil.example'):
            self.assertEqual(opds_auth.safe_next(unsafe), '/opds')

    def test_covers_and_sync_routes_stay_public(self):
        with mock.patch.dict(os.environ, {'OPDS_AUTH': 'true'}):
            # The test EPUBs have no cover: 404, but never 401
            self.assertEqual(self._request('GET', '/cover/dune.epub')[0], 404)
            self.assertEqual(self._request('GET', '/health')[0], 200)
            for prefix in ('', '/koreader/sync'):
                self.assertEqual(self._request('GET', f'{prefix}/healthz')[0], 200)
                self.assertEqual(self._request('GET', f'{prefix}/users/auth', headers=self._sync_headers('reader'))[0], 200)
                self.assertEqual(self._request('GET', f'{prefix}/api/v1/progress', headers=self._sync_headers('reader'))[0], 200)
                self.assertEqual(self._request('GET', f'{prefix}/users/auth')[0], 401)

    def test_recommendations_feed_without_history(self):
        with mock.patch.dict(os.environ, {'OPDS_AUTH': 'true'}):
            status, _, body = self._request('GET', '/opds/recommendations', headers=basic_header('newbie', self.password))
        self.assertEqual(status, 200)
        feed = self._feed(body)
        self.assertEqual(feed.findall('atom:entry', self.ns), [])
        self.assertIn('historique', feed.find('atom:subtitle', self.ns).text)

    def test_recommendations_feed_from_sync_history(self):
        self._push_progress('other', 'hyperion.epub', 1.0)
        self._push_progress('reader', 'dune.epub', 1.0)
        self._push_progress('reader', 'abandoned.epub', 0.1, status='dnf')
        with mock.patch.dict(os.environ, {'OPDS_AUTH': 'true'}):
            good = basic_header('reader', self.password)
            status, _, body = self._request('GET', '/opds/recommendations', headers=good)
            self.assertEqual(status, 200)
            entries = self._feed(body).findall('atom:entry', self.ns)
            titles = [entry.find('atom:title', self.ns).text for entry in entries]
            # Same author first, then same genre; read, abandoned and unrelated books are left out
            self.assertEqual(titles, ['Dune Messiah', 'Hyperion'])
            self.assertIn('Frank Herbert', entries[0].find('atom:summary', self.ns).text)
            links = {link.get('rel'): link.get('href') for link in entries[0].findall('atom:link', self.ns)}
            self.assertEqual(links['http://opds-spec.org/acquisition/open-access'], '/download/messiah.epub')
            self.assertEqual(links['http://opds-spec.org/image'], '/cover/messiah.epub')
            # Root tile: mosaic of the recommended covers (web view only)
            _, _, body = self._request('GET', '/opds', headers={**good, 'Accept': 'text/html'})
            tile = next(entry for entry in self._feed(body).findall('atom:entry', self.ns)
                        if entry.find('atom:id', self.ns).text == 'urn:recommendations')
            mosaic = {link.get('href') for link in tile.findall('atom:link', self.ns)
                      if link.get('rel') == 'urn:pyopds:mosaic-cover'}
            self.assertEqual(mosaic, {'/cover/messiah.epub', '/cover/hyperion.epub'})

    def test_book_sheet_marks_a_book_as_read(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(self._request('POST', '/users/create', {'username': 'marker', 'password': self.password})[0], 201)
        auth = basic_header('marker', self.password)

        def book_status(path):
            status, _, body = self._request('GET', '/webui/book-status?path=' + urllib.parse.quote(path), headers=auth)
            self.assertEqual(status, 200, body)
            return json.loads(body)

        def mark(path, read):
            status, _, body = self._request('PUT', '/webui/book-status', {'path': path, 'read': read}, auth)
            self.assertEqual(status, 200, body)
            return json.loads(body)

        def progress_items():
            _, _, body = self._request('GET', '/api/v1/progress', headers=self._sync_headers('marker'))
            return {item['library_path']: item for item in json.loads(body)['items']}

        # An account is always required
        self.assertEqual(self._request('GET', '/webui/book-status?path=cuisine.epub')[0], 401)
        # Never synced book: marked as read, listed as finished by the reading page, then unmarked
        self.assertEqual(book_status('cuisine.epub'), {'read': False, 'status': None, 'percentage': None, 'finished_date': None})
        self.assertEqual(mark('cuisine.epub', True)['status'], 'finished')
        self.assertTrue(book_status('cuisine.epub')['read'])
        item = progress_items()['cuisine.epub']
        self.assertEqual((item['status'], item['title'], item['percentage']), ('finished', 'Cuisine facile', 1.0))
        # Day it was read: replaces the day it was marked in the statistics, cleared when unmarked
        status, _, body = self._request('PUT', '/webui/book-status', {'path': 'cuisine.epub', 'read': True, 'finished_date': '2019-05-04'}, auth)
        self.assertEqual((status, json.loads(body)['finished_date']), (200, '2019-05-04'))
        self.assertEqual(book_status('cuisine.epub')['finished_date'], '2019-05-04')
        _, _, body = self._request('GET', '/api/v1/stats/activity?tz=0', headers=self._sync_headers('marker'))
        finished_at = [book['finished_at'] for book in json.loads(body)['books']]
        self.assertEqual([time.strftime('%Y-%m-%d', time.gmtime(at)) for at in finished_at], ['2019-05-04'])
        self.assertEqual(mark('cuisine.epub', True)['finished_date'], '2019-05-04')
        for date in ('2019-13-01', '04/05/2019', 12, '2999-01-01'):
            self.assertEqual(self._request('PUT', '/webui/book-status', {'path': 'cuisine.epub', 'read': True, 'finished_date': date}, auth)[0], 400)
        self.assertFalse(mark('cuisine.epub', False)['read'])
        self.assertIsNone(book_status('cuisine.epub')['finished_date'])
        self.assertNotIn('cuisine.epub', progress_items())
        # Book synced by KOReader (file name hash), finished by its progress: unmarking keeps it in progress
        self._push_progress('marker', 'hyperion.epub', 1.0)
        self.assertEqual(book_status('hyperion.epub'), {'read': True, 'status': 'finished', 'percentage': 1.0, 'finished_date': None})
        self.assertEqual(mark('hyperion.epub', False)['status'], 'reading')
        self.assertEqual(progress_items()['hyperion.epub']['status'], 'reading')
        self.assertTrue(mark('hyperion.epub', True)['read'])
        self.assertEqual(len([path for path in progress_items() if path == 'hyperion.epub']), 1)
        # Other readers are not affected
        status, _, body = self._request('GET', '/webui/book-status?path=hyperion.epub', headers=basic_header('newbie', self.password))
        self.assertFalse(json.loads(body)['read'])
        # Unknown books, paths outside the library and invalid bodies
        self.assertEqual(self._request('GET', '/webui/book-status?path=missing.epub', headers=auth)[0], 404)
        self.assertEqual(self._request('GET', '/webui/book-status?path=../sync.db', headers=auth)[0], 404)
        self.assertEqual(self._request('PUT', '/webui/book-status', {'path': 'dune.epub', 'read': 'yes'}, auth)[0], 400)
        self.assertEqual(self._request('PUT', '/webui/book-status', None, auth)[0], 400)


    def test_personal_feeds_and_hide_read_filter(self):
        auth = basic_header('shelves', self.password)

        def feed(path):
            status, _, body = self._request('GET', path, headers=auth)
            self.assertEqual(status, 200, body)
            return self._feed(body)

        def titles(root):
            return [entry.find('atom:title', self.ns).text for entry in root.findall('atom:entry', self.ns)]

        # An account is always required
        self.assertEqual(self._request('GET', '/opds/reading')[0], 401)
        self.assertEqual(self._request('GET', '/opds/series-next')[0], 401)
        self._push_progress('shelves', 'saga1.epub', 1.0)
        self._push_progress('shelves', 'hyperion.epub', 0.42)

        # Root catalog: the reader's tiles come first
        self.assertEqual(titles(feed('/opds'))[:3], ['En cours', 'Suite de vos séries', 'À lire ensuite'])
        reading = feed('/opds/reading')
        self.assertEqual(titles(reading), ['Hyperion'])
        self.assertIn('Lu à 42 %', reading.find('atom:entry/atom:summary', self.ns).text)
        series = feed('/opds/series-next')
        self.assertEqual(titles(series), ['Saga 2'])
        self.assertIn('Tome 2 de La Saga', series.find('atom:entry/atom:summary', self.ns).text)
        # Volume 2 already opened: volume 3 comes next
        self._push_progress('shelves', 'saga2.epub', 0.1)
        self.assertEqual(titles(feed('/opds/series-next')), ['Saga 3'])

        # « Masquer les livres lus »: facets of the lists, remembered for the next lists
        books = feed('/opds/books?page=1')
        facets = {link.get('title'): link for link in books.findall('atom:link', self.ns)
                  if link.get('rel') == 'http://opds-spec.org/facet'}
        self.assertEqual(set(facets), {'Tous les livres', 'Masquer les livres lus'})
        self.assertEqual(facets['Tous les livres'].get('{http://opds-spec.org/2010/catalog}activeFacet'), 'true')
        self.assertEqual(facets['Masquer les livres lus'].get('{http://opds-spec.org/2010/catalog}facetGroup'), 'Livres lus')
        self.assertIn('Saga 1', titles(books))
        hidden = feed(facets['Masquer les livres lus'].get('href'))
        self.assertNotIn('Saga 1', titles(hidden))
        self.assertIn('Saga 2', titles(hidden))
        self.assertNotIn('Saga 1', titles(feed('/opds/series?name=La%20Saga')))
        self.assertNotIn('Saga 1', titles(feed('/opds/search?q=saga')))
        self.assertNotIn('Saga 1', titles(feed('/opds/recent')))
        # Other readers and anonymous clients keep the full lists, without facets
        _, _, body = self._request('GET', '/opds/books?page=1')
        self.assertIn('Saga 1', titles(self._feed(body)))
        self.assertNotIn(b'opds-spec.org/facet"', body)
        self.assertIn('Saga 1', titles(feed('/opds/books?read=show&page=1')))
        self.assertIn('Saga 1', titles(feed('/opds/books?page=1')))
