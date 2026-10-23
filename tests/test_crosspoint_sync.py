"""Tests of the CrossPoint Sync API (/api/v1) as used by CrossPoint readers and the CrossPoint Sync app."""
import base64
import gzip
import hashlib
import http.client
import importlib
import json
import os
import socketserver
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
import urllib.parse
import zipfile

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from test_opds import create_epub  # noqa: E402


def create_novel_epub(path, chapter_chars, spine=True):
    """EPUB whose chapters each hold `chapter_chars` characters of visible text."""
    manifest = ''.join(f'<item id="c{i}" href="text/ch{i}.xhtml" media-type="application/xhtml+xml"/>' for i in range(len(chapter_chars)))
    itemrefs = ''.join(f'<itemref idref="c{i}"/>' for i in range(len(chapter_chars))) if spine else ''
    with zipfile.ZipFile(path, 'w') as zf:
        zf.writestr('mimetype', 'application/epub+zip')
        zf.writestr('META-INF/container.xml', '<?xml version="1.0"?><container version="1.0" '
                    'xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles>'
                    '<rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>'
                    '</rootfiles></container>')
        zf.writestr('OEBPS/content.opf', '<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" version="3.0">'
                    '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>Roman</dc:title>'
                    f'<dc:creator>Auteur</dc:creator></metadata><manifest>{manifest}</manifest><spine>{itemrefs}</spine></package>')
        for i, chars in enumerate(chapter_chars):
            paragraphs = ''.join(f'<p>{"x" * 99} </p>\n' for _ in range(chars // 100))
            zf.writestr(f'OEBPS/text/ch{i}.xhtml', '<html xmlns="http://www.w3.org/1999/xhtml"><head><title>Titre</title>'
                        f'<style>p {{ margin: 0 }}</style></head><body>{paragraphs}</body></html>')


def create_series_epub(path, title, series, index, epub3=False, description=None, rating=None, date=None, cover=False):
    """EPUB of a series volume: calibre:series metadata (EPUB 2 style) or an EPUB 3 collection."""
    if epub3:
        series_meta = (f'<meta property="belongs-to-collection" id="c01">{series}</meta>'
                       '<meta refines="#c01" property="collection-type">series</meta>'
                       f'<meta refines="#c01" property="group-position">{index}</meta>')
    else:
        series_meta = f'<meta name="calibre:series" content="{series}"/><meta name="calibre:series_index" content="{index}"/>'
    if rating is not None:
        series_meta += f'<meta name="calibre:rating" content="{rating}"/>'
    if cover:
        series_meta += '<meta name="cover" content="cover-img"/>'
    extra = f'<dc:description>{description}</dc:description>' if description else ''
    extra += f'<dc:date>{date}</dc:date>' if date else ''
    manifest = '<item id="cover-img" href="cover.jpg" media-type="image/jpeg"/>' if cover else ''
    with zipfile.ZipFile(path, 'w') as zf:
        zf.writestr('mimetype', 'application/epub+zip')
        zf.writestr('META-INF/container.xml', '<?xml version="1.0"?><container version="1.0" '
                    'xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles>'
                    '<rootfile full-path="content.opf" media-type="application/oebps-package+xml"/>'
                    '</rootfiles></container>')
        zf.writestr('content.opf', '<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" version="3.0">'
                    f'<metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>{title}</dc:title>'
                    f'<dc:creator>Glen Cook</dc:creator>{extra}{series_meta}</metadata>'
                    f'<manifest>{manifest}</manifest><spine/></package>')
        if cover:
            zf.writestr('cover.jpg', b'\xff\xd8\xff\xe0fake-jpeg')


class ThreadedTCPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True


def md5(value: str) -> str:
    return hashlib.md5(value.encode('utf-8')).hexdigest()


class TestCrossPointSync(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.library_dir = tempfile.TemporaryDirectory()
        cls.db_dir = tempfile.TemporaryDirectory()
        cls.previous_library_dir = os.environ.get('LIBRARY_DIR')
        os.environ['LIBRARY_DIR'] = cls.library_dir.name
        cls.book_path = os.path.join(cls.library_dir.name, 'Le Rouge et le Noir.epub')
        create_epub(cls.book_path, 'Le Rouge et le Noir', 'Stendhal', date='1830-11-01', subjects=['romans historiques', 'lang:fr'])
        cls.novel_path = os.path.join(cls.library_dir.name, 'Roman.epub')
        create_novel_epub(cls.novel_path, [9000, 9000])
        series_dir = os.path.join(cls.library_dir.name, 'Compagnie noire')
        os.makedirs(series_dir)
        cls.series_paths = {
            1: os.path.join(series_dir, 'Tome 1.epub'),
            1.5: os.path.join(series_dir, 'Nouvelle.epub'),
            2: os.path.join(series_dir, 'Tome 2.epub'),
            3: os.path.join(series_dir, 'Tome 3.epub'),
        }
        create_series_epub(cls.series_paths[1], 'La Compagnie noire', 'La Compagnie noire', '1.0', rating='8.0', date='1984-05-01',
                           description='&lt;div&gt;&lt;p&gt;Une compagnie de &lt;b&gt;mercenaires&lt;/b&gt;&lt;br/&gt;au service'
                                       ' de la Dame.&lt;/p&gt;&lt;p&gt;Second   paragraphe &amp;amp; fin.&lt;/p&gt;&lt;/div&gt;')
        create_series_epub(cls.series_paths[1.5], 'Interlude', 'La Compagnie noire', '1.5')
        create_series_epub(cls.series_paths[2], 'Le Château noir', 'la compagnie  NOIRE', 2, epub3=True, date='1984', cover=True)
        create_series_epub(cls.series_paths[3], 'La Rose blanche', 'La Compagnie Noire', 3, rating=0)

        for module in ('controllers.opds', 'controllers.sync_storage', 'controllers.koreader_sync',
                       'controllers.library_index', 'controllers.reading_activity', 'controllers.crosspoint_sync',
                       'controllers.nextcloud_notes'):
            importlib.reload(importlib.import_module(module))
        importlib.reload(importlib.import_module('routes'))
        server_mod = importlib.reload(importlib.import_module('server'))

        cls.sync_storage = importlib.import_module('controllers.sync_storage')
        cls.koreader_sync = importlib.import_module('controllers.koreader_sync')
        cls.library_index = importlib.import_module('controllers.library_index')
        cls.db_path = os.path.join(cls.db_dir.name, 'sync.db')
        cls.koreader_sync.SyncControllerBase._sync_storage_instance = cls.sync_storage.SyncStorage(cls.db_path)

        cls.httpd = ThreadedTCPServer(('localhost', 0), server_mod.UnifiedHandler)
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()
        time.sleep(0.1)

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.thread.join(timeout=1)
        # Other test modules create their own storage from KOREADER_SYNC_DB_PATH
        cls.koreader_sync.SyncControllerBase._sync_storage_instance = None
        if cls.previous_library_dir is None:
            os.environ.pop('LIBRARY_DIR', None)
        else:
            os.environ['LIBRARY_DIR'] = cls.previous_library_dir
        cls.library_dir.cleanup()
        cls.db_dir.cleanup()

    # ----------------------------------------------------------------- helpers

    def request(self, method, path, body=None, user=None, password='secret', headers=None):
        conn = http.client.HTTPConnection('localhost', self.port, timeout=5)
        all_headers = {'Content-Type': 'application/json', 'Host': 'books.example:8080'}
        if user:
            all_headers.update({'x-auth-user': user, 'x-auth-key': md5(password)})
        all_headers.update(headers or {})
        conn.request(method, path, body=json.dumps(body) if body is not None else None, headers=all_headers)
        response = conn.getresponse()
        raw = response.read()
        conn.close()
        return response.status, json.loads(raw.decode('utf-8')) if raw else None

    def create_user(self, name, password='secret'):
        status, _ = self.request('POST', '/users/create', {'username': name, 'password': md5(password)})
        self.assertEqual(status, 201)
        return name

    def put_progress(self, user, document, percentage, device_id='crosspoint-reader', prefix='', **extra):
        body = {'document': document, 'progress': f'/body/DocFragment[{int(percentage * 10)}]',
                'percentage': percentage, 'device': 'CrossPoint', 'device_id': device_id, **extra}
        status, data = self.request('PUT', f'{prefix}/syncs/progress', body, user=user)
        self.assertEqual(status, 200, data)
        return data

    def books(self, user):
        status, data = self.request('GET', '/api/v1/progress?limit=500', user=user)
        self.assertEqual(status, 200, data)
        return {item['document']: item for item in data['items']}

    # ------------------------------------------------------------- app sign-in

    def test_app_sign_in_at_server_root(self):
        status, data = self.request('GET', '/healthz')
        self.assertEqual(status, 200)
        self.assertEqual(data['status'], 'ok')

        self.create_user('Signin')
        status, data = self.request('GET', '/users/auth', user='signin')
        self.assertEqual((status, data), (200, {'authorized': 'OK'}))
        # Some clients send the raw password instead of its MD5
        status, _ = self.request('GET', '/users/auth', headers={'x-auth-user': 'signin', 'x-auth-key': 'secret'})
        self.assertEqual(status, 200)
        status, data = self.request('GET', '/users/auth', user='signin', password='wrong')
        self.assertEqual((status, data['code']), (401, 2001))

    def test_same_api_under_koreader_prefix(self):
        user = self.create_user('prefixed')
        self.put_progress(user, 'a' * 32, 0.3, prefix='/koreader/sync')
        status, data = self.request('GET', '/koreader/sync/api/v1/progress', user=user)
        self.assertEqual(status, 200)
        self.assertEqual([item['document'] for item in data['items']], ['a' * 32])

    # ---------------------------------------------------------------- progress

    def test_rich_position_and_newest_device_wins(self):
        user = self.create_user('devices')
        document = 'b' * 32
        position = {'pctQ': 250000, 'spine': 3, 'page': 4, 'pages': 20, 'para': 7, 'xpath': '/body/p[7]'}
        self.put_progress(user, document, 0.25, device_id='crosspoint-reader', position=position)
        status, data = self.request('GET', f'/syncs/progress/{document}?position_kinds=locator,percentage', user=user)
        self.assertEqual(status, 200)
        self.assertEqual(data['position'], position)

        time.sleep(1.1)
        self.put_progress(user, document, 0.4, device_id='koreader-phone')
        status, data = self.request('GET', f'/syncs/progress/{document}', user=user)
        self.assertEqual((data['device_id'], data['percentage']), ('koreader-phone', 0.4))

        status, data = self.request('GET', f'/api/v1/progress/{document}', user=user)
        self.assertEqual([d['device_id'] for d in data['devices']], ['koreader-phone', 'crosspoint-reader'])
        self.assertEqual(data['devices'][1]['position'], position)

    def test_invalid_position_is_ignored(self):
        user = self.create_user('badposition')
        self.put_progress(user, 'c' * 32, 0.5, position={'pctQ': -1, 'spine': 1, 'page': 1, 'pages': 1})
        status, data = self.request('GET', f"/syncs/progress/{'c' * 32}", user=user)
        self.assertNotIn('position', data)

    def test_progress_batch_is_all_or_nothing(self):
        user = self.create_user('batch')
        good = {'document': 'd' * 32, 'progress': 'p', 'percentage': 0.1, 'device': 'X'}
        status, data = self.request('PUT', '/api/v1/progress/batch', {'items': [good, {'document': 'e' * 32}]}, user=user)
        self.assertEqual((status, data['code']), (403, 2003))
        self.assertEqual(self.books(user), {})
        status, data = self.request('PUT', '/api/v1/progress/batch', {'items': [good]}, user=user)
        self.assertEqual((status, data['accepted']), (200, 1))

    def test_progress_change_feed(self):
        user = self.create_user('changes')
        self.put_progress(user, 'f' * 32, 0.2)
        status, data = self.request('GET', '/api/v1/progress/changes?since=0', user=user)
        self.assertEqual(status, 200)
        self.assertEqual([c['document'] for c in data['changes']], ['f' * 32])
        status, again = self.request('GET', f"/api/v1/progress/changes?since={data['cursor']}", user=user)
        self.assertEqual((again['changes'], again['more']), ([], False))

    def test_malformed_bodies_get_an_answer(self):
        user = self.create_user('malformed')
        for raw in ('null', '[1]', '"text"', '1' * 5000, '[' * 100000, '{"document": "d", "progress": "p", "percentage": 1' + '0' * 400 + '}'):
            conn = http.client.HTTPConnection('localhost', self.port, timeout=5)
            conn.request('PUT', '/syncs/progress', body=raw,
                         headers={'Content-Type': 'application/json', 'x-auth-user': user, 'x-auth-key': md5('secret')})
            response = conn.getresponse()
            body = json.loads(response.read())
            conn.close()
            self.assertEqual((response.status, body['code']), (403, 2003), raw[:20])

        conn = http.client.HTTPConnection('localhost', self.port, timeout=5)
        conn.putrequest('PUT', '/syncs/progress')
        conn.putheader('Content-Length', str(10 ** 13))
        conn.putheader('x-auth-user', user)
        conn.putheader('x-auth-key', md5('secret'))
        conn.endheaders()
        response = conn.getresponse()
        self.assertEqual(response.status, 403)
        conn.close()

    # ------------------------------------------------------------- library

    def test_list_fills_details_from_library_epub(self):
        user = self.create_user('library')
        partial = self.library_index.koreader_partial_md5(self.book_path)
        self.put_progress(user, partial, 0.5, device_id='koreader')
        book = self.books(user)[partial]
        self.assertEqual(book['title'], 'Le Rouge et le Noir')
        self.assertEqual(book['author'], 'Stendhal')
        self.assertEqual(book['filename'], 'Le Rouge et le Noir.epub')
        self.assertEqual(book['genres'], ['Roman historique'])
        self.assertEqual(book['status'], 'reading')
        self.assertEqual(book['library_path'], 'Le Rouge et le Noir.epub')
        self.put_progress(user, 'e' * 32, 0.2, device_id='koreader')
        self.assertIsNone(self.books(user)['e' * 32]['library_path'])

    def test_document_associated_by_hand_with_library_epub(self):
        user = self.create_user('associate')
        document = 'f' * 32
        self.put_progress(user, document, 0.3, device_id='koreader')
        path = f'/api/v1/documents/{document}/info'
        for invalid in ('../secret.epub', 'missing.epub', 'Le Rouge et le Noir.txt', 42):
            status, _ = self.request('PUT', path, {'library_path': invalid}, user=user)
            self.assertEqual(status, 403, invalid)

        status, data = self.request('PUT', path, {'library_path': 'Le Rouge et le Noir.epub'}, user=user)
        self.assertEqual((status, data['library_path']), (200, 'Le Rouge et le Noir.epub'))
        book = self.books(user)[document]
        self.assertEqual((book['library_path'], book['library_linked']), ('Le Rouge et le Noir.epub', True))
        self.assertEqual(book['title'], 'Le Rouge et le Noir')

        status, _ = self.request('PUT', path, {'library_path': None}, user=user)
        self.assertEqual(status, 200)
        book = self.books(user)[document]
        self.assertEqual((book['library_path'], book['library_linked']), (None, False))

    def test_series_rating_year_and_description_from_library_epub(self):
        user = self.create_user('series')
        first = self.library_index.koreader_partial_md5(self.series_paths[1])
        third = self.library_index.koreader_partial_md5(self.series_paths[3])
        self.put_progress(user, first, 0.95, device_id='koreader')
        self.put_progress(user, third, 0.1, device_id='koreader')
        books = self.books(user)
        book = books[first]
        self.assertEqual((book['series'], book['series_position']), ('La Compagnie noire', 1))
        self.assertEqual((book['release_year'], book['rating']), (1984, 4.0))
        self.assertIsInstance(book['rating'], float)
        self.assertEqual((book['moods'], book['content_warnings'], book['hardcover_slug']), ([], [], None))
        # Calibre rating 0 means "not rated"
        self.assertEqual((books[third]['series_position'], books[third]['rating'], books[third]['release_year']), (3, None, None))

        status, data = self.request('GET', f'/api/v1/documents/{first}/about', user=user)
        self.assertEqual(status, 200)
        self.assertEqual(data, {'description': 'Une compagnie de mercenaires\nau service de la Dame.\n\nSecond paragraphe & fin.'})
        status, data = self.request('GET', f"/api/v1/documents/{'7' * 32}/about", user=user)
        self.assertEqual((status, data), (200, {'description': None}))

        details = self.library_index.LibraryIndex.get_instance().details(self.series_paths[2])
        self.assertEqual((details['series'], details['series_index'], details['year']), ('la compagnie NOIRE', 2, 1984))
        self.assertIsNone(details['description'])

    def test_next_in_series_from_library(self):
        user = self.create_user('nextbook')
        first = self.library_index.koreader_partial_md5(self.series_paths[1])
        status, data = self.request('GET', f'/api/v1/documents/{first}/next', user=user)
        self.assertEqual(status, 200)
        # The novella 1.5 is skipped for the next whole-numbered volume; series names compared normalized
        self.assertEqual(data, {
            'series': 'La Compagnie noire',
            'next': {
                'position': 2, 'title': 'Le Château noir', 'author': 'Glen Cook', 'year': 1984, 'slug': None,
                'cover': 'http://books.example:8080/cover/Compagnie%20noire/Tome%202.epub',
            },
        })
        # The cover URL serves the image
        conn = http.client.HTTPConnection('localhost', self.port, timeout=5)
        conn.request('GET', '/cover/Compagnie%20noire/Tome%202.epub')
        response = conn.getresponse()
        response.read()
        conn.close()
        self.assertEqual(response.status, 200)

        second = self.library_index.koreader_partial_md5(self.series_paths[2])
        data = self.request('GET', f'/api/v1/documents/{second}/next', user=user)[1]
        self.assertEqual((data['series'], data['next']['position'], data['next']['title'], data['next']['cover']),
                         ('la compagnie NOIRE', 3, 'La Rose blanche', None))
        last = self.library_index.koreader_partial_md5(self.series_paths[3])
        self.assertEqual(self.request('GET', f'/api/v1/documents/{last}/next', user=user)[1],
                         {'series': 'La Compagnie Noire', 'next': None})
        # Not in a series
        rouge = self.library_index.koreader_partial_md5(self.book_path)
        self.assertEqual(self.request('GET', f'/api/v1/documents/{rouge}/next', user=user)[1], {'next': None})

        # "Find it in your catalogs" searches the title in the OPDS catalog
        conn = http.client.HTTPConnection('localhost', self.port, timeout=5)
        conn.request('GET', '/opds/search?q=' + urllib.parse.quote('Le Château noir'))
        response = conn.getresponse()
        feed = response.read().decode('utf-8')
        conn.close()
        self.assertEqual(response.status, 200)
        self.assertIn('Le Château noir', feed)
        self.assertIn('/download/Compagnie%20noire/Tome%202.epub', feed)

    def test_series_sent_by_firmware(self):
        user = self.create_user('fwseries')
        document = '6' * 32
        self.put_progress(user, document, 0.5, metadata={
            'filename': 'Autre.epub', 'title': 'Le Château noir', 'authors': 'Glen Cook',
            'series': 'La Compagnie Noire', 'series_index': 2.0, 'isbn': '9782841720000'})
        book = self.books(user)[document]
        self.assertEqual((book['series'], book['series_position'], book['library_path']), ('La Compagnie Noire', 2, None))
        self.assertIsInstance(book['series_position'], int)
        stored = self.koreader_sync.SyncControllerBase._sync_storage_instance.get_document(user, document)
        self.assertEqual(stored['isbn'], '9782841720000')

        # A later upload without these fields keeps them
        self.put_progress(user, document, 0.6, metadata={'filename': 'Autre.epub'})
        self.put_progress(user, document, 0.7, metadata={'series': 'La Compagnie Noire', 'series_index': 'x'})
        book = self.books(user)[document]
        self.assertEqual((book['series'], book['series_position']), ('La Compagnie Noire', 2))

        # The next volume is looked up in the library from the reader's series
        data = self.request('GET', f'/api/v1/documents/{document}/next', user=user)[1]
        self.assertEqual((data['series'], data['next']['position'], data['next']['title']), ('La Compagnie Noire', 3, 'La Rose blanche'))

        self.put_progress(user, document, 0.8, metadata={'series': 'Autre série', 'series_index': 1.5})
        book = self.books(user)[document]
        self.assertEqual((book['series'], book['series_position']), ('Autre série', 1.5))
        self.assertEqual(self.request('GET', f'/api/v1/documents/{document}/next', user=user)[1], {'series': 'Autre série', 'next': None})

    # ------------------------------------------------------------------ web UI

    def raw_get(self, path, headers=None):
        conn = http.client.HTTPConnection('localhost', self.port, timeout=5)
        conn.request('GET', path, headers=headers or {})
        response = conn.getresponse()
        body = response.read()
        conn.close()
        return response.status, response.getheader('Content-Type'), body

    @staticmethod
    def basic(user, password='secret'):
        return {'Authorization': 'Basic ' + base64.b64encode(f'{user}:{password}'.encode()).decode()}

    def test_reading_page_and_assets(self):
        status, content_type, body = self.raw_get('/reading')
        self.assertEqual((status, content_type), (200, 'text/html; charset=utf-8'))
        # Versioned asset URLs: a proxy caching static files still serves the new ones after an update
        self.assertRegex(body, rb'/static/reading\.js\?v=\d+"')
        self.assertRegex(body, rb'/static/reading\.css\?v=\d+"')
        # Alpine.js templates, pinned version (components registered before Alpine starts)
        self.assertIn(b'https://cdn.jsdelivr.net/npm/alpinejs@3.17.4/dist/cdn.min.js', body)
        self.assertIn(b'x-data="reading"', body)
        self.assertLess(body.index(b'/static/reading.js'), body.index(b'alpinejs@'))
        status, content_type, _ = self.raw_get('/static/reading.js?v=1')
        self.assertEqual((status, content_type), (200, 'text/javascript; charset=utf-8'))
        status, content_type, _ = self.raw_get('/static/reading.css')
        self.assertEqual((status, content_type), (200, 'text/css; charset=utf-8'))

    def test_static_assets_caching(self):
        # Dotted names (xslt-polyfill.min.js) are served; versioned URLs are immutable, plain ones revalidated
        conn = http.client.HTTPConnection('localhost', self.port, timeout=5)
        conn.request('GET', '/static/xslt-polyfill.min.js?v=1')
        response = conn.getresponse()
        response.read()
        conn.close()
        self.assertEqual(response.status, 200)
        self.assertEqual(response.getheader('Cache-Control'), 'public, max-age=31536000, immutable')
        conn = http.client.HTTPConnection('localhost', self.port, timeout=5)
        conn.request('GET', '/static/xslt-polyfill.min.js', headers={'If-Modified-Since': response.getheader('Last-Modified')})
        response = conn.getresponse()
        conn.close()
        self.assertEqual(response.status, 304)
        self.assertEqual(response.getheader('Cache-Control'), 'no-cache')
        # Compressed when the browser accepts gzip
        conn = http.client.HTTPConnection('localhost', self.port, timeout=5)
        conn.request('GET', '/static/xslt-polyfill.min.js', headers={'Accept-Encoding': 'gzip, br'})
        response = conn.getresponse()
        body = response.read()
        conn.close()
        self.assertEqual(response.getheader('Content-Encoding'), 'gzip')
        with open(os.path.join(PROJECT_ROOT, 'static', 'xslt-polyfill.min.js'), 'rb') as f:
            self.assertEqual(gzip.decompress(body), f.read())

    def test_static_route_refuses_other_files(self):
        for path in ('/static/missing.js', '/static/opds_to_html.xslt', '/static/..%2Fserver.py', '/static/.hidden.js',
                     '/static/x..js', '/static/xslt-polyfill.LICENSE'):
            status, _, _ = self.raw_get(path)
            self.assertEqual(status, 404, path)

    def test_print_pages_estimated_from_library_epub(self):
        user = self.create_user('pages')
        document = self.library_index.koreader_partial_md5(self.novel_path)
        self.put_progress(user, document, 0.1, device_id='koreader')
        self.put_progress(user, document, 0.5, device_id='koreader')
        self.assertEqual(self.books(user)[document]['page_count'], 10)
        self.assertEqual(self.request('GET', f'/api/v1/documents/{document}/cover', user=user)[1]['pages'], 10)

        status, activity = self.request('GET', '/api/v1/stats/activity?tz=0', user=user)
        self.assertEqual((activity['books'][0]['page_count'], activity['books'][0]['pages_read']), (10, 5))
        self.assertEqual(sum(day['pages'] for day in activity['days']), 4)

        # A page count set in the app wins over the estimate
        self.request('PUT', f'/api/v1/documents/{document}/info', {'page_count': 300}, user=user)
        self.assertEqual(self.books(user)[document]['page_count'], 300)

    def test_activity_includes_books_marked_read_without_progress(self):
        user = self.create_user('markedread')
        document = self.library_index.koreader_partial_md5(self.novel_path)
        status, body = self.request('PUT', f'/api/v1/documents/{document}/status', {'status': 'finished'}, user=user)
        self.assertEqual(status, 200)

        status, activity = self.request('GET', '/api/v1/stats/activity?tz=0', user=user)
        book = activity['books'][0]
        self.assertEqual((book['document'], book['finished_at'], book['percentage']), (document, body['status_at'], 1.0))
        self.assertEqual((book['page_count'], book['pages_read'], activity['pages_total']), (10, 10, 10))
        self.assertEqual(activity['days'], [])

        # A finish date set by hand replaces the day the status was set, and marks that day
        self.request('PUT', f'/api/v1/documents/{document}/dates', {'start_date': '2020-01-01', 'finished_date': '2020-01-10'}, user=user)
        status, activity = self.request('GET', '/api/v1/stats/activity?tz=0', user=user)
        book = activity['books'][0]
        self.assertEqual(time.strftime('%Y-%m-%d', time.gmtime(book['finished_at'])), '2020-01-10')
        self.assertEqual((book['days_to_finish'], book['finish_manual'], book['start_manual']), (10, True, True))
        self.assertEqual(activity['reading_days'], ['2020-01-01', '2020-01-10'])

        # Back to reading: no longer finished
        self.request('PUT', f'/api/v1/documents/{document}/status', {'status': 'reading'}, user=user)
        self.assertEqual(self.request('GET', '/api/v1/stats/activity?tz=0', user=user)[1]['books'], [])

    def test_koreader_highlights_through_nextcloud_notes(self):
        user = self.create_user('notes')
        auth = self.basic('notes')
        api = '/index.php/apps/notes/api/v1/notes'
        document = self.library_index.koreader_partial_md5(self.novel_path)
        self.put_progress(user, document, 0.4, device_id='koreader')

        def note(*highlights):
            # KOReader's template/md.lua layout
            lines = ['# Roman', '##### Auteur\n']
            chapter = None
            for page, text, comment, chapter_title in highlights:
                if chapter_title != chapter:
                    chapter = chapter_title
                    lines.append('## ' + chapter)
                lines.append(f'### Page {page} @ 21 October 2026 09:15:42 PM')
                lines.append(text)
                if comment:
                    lines.append('\n---\n' + comment)
            return {'title': 'Auteur - Roman', 'content': '\n'.join(lines), 'category': 'KOReader'}

        def clippings():
            return {c['text']: c for c in self.request('GET', '/api/v1/clippings', user=user)[1]['items']}

        # An account is required; KOReader sends a `null` body even with GET
        self.assertEqual(self.raw_get(api + '?category=KOReader')[0], 401)
        self.assertEqual(self.request('GET', api + '?category=KOReader', 'null-body', headers=auth)[0], 200)
        status, notes = self.request('GET', api + '?category=KOReader', headers=auth)
        self.assertEqual((status, notes), (200, []))

        status, created = self.request('POST', api, note((12, '*Première phrase*', None, 'Chapitre 1'),
                                                          (30, '*Avec note*', 'Mon avis\nsur deux lignes', 'Chapitre 2')), headers=auth)
        self.assertEqual(status, 200, created)
        self.assertEqual((created['title'], created['category']), ('Auteur - Roman', 'KOReader'))
        items = clippings()
        self.assertEqual(set(items), {'Première phrase', 'Avec note'})
        self.assertEqual({c['document'] for c in items.values()}, {document})
        self.assertEqual((items['Avec note']['chapter'], items['Avec note']['note']), ('Chapitre 2', 'Mon avis\nsur deux lignes'))
        self.assertIsNone(items['Première phrase']['note'])
        self.assertEqual(time.strftime('%Y-%m-%d %H:%M', time.gmtime(items['Avec note']['created_at'])), '2026-10-21 21:15')
        self.assertEqual(self.request('GET', api + '?category=KOReader', headers=auth)[1][0]['id'], created['id'])
        self.assertEqual(self.request('GET', api + '?category=Autre', headers=auth)[1], [])

        # Exported again: a note set by another client is kept, a highlight removed on the reader is deleted
        first_id = items['Première phrase']['id']
        self.request('PUT', f'/api/v1/clippings/{document}', {'items': [{**items['Première phrase'], 'spine': 0, 'note': 'Depuis l’app'}]}, user=user)
        status, updated = self.request('PUT', f"{api}/{created['id']}", note((12, '*Première phrase*', None, 'Chapitre 1'),
                                                                            (50, '*Nouvelle*', None, 'Chapitre 3')), headers=auth)
        self.assertEqual(status, 200, updated)
        items = clippings()
        self.assertEqual(set(items), {'Première phrase', 'Nouvelle'})
        self.assertEqual((items['Première phrase']['id'], items['Première phrase']['note']), (first_id, 'Depuis l’app'))

        # Unknown notes, other accounts and other content
        self.assertEqual(self.request('PUT', f'{api}/999999', note(), headers=auth)[0], 404)
        self.create_user('notes2')
        other = self.basic('notes2')
        self.assertEqual(self.request('GET', f"{api}/{created['id']}", headers=other)[0], 404)
        self.assertEqual(self.request('POST', api, {'title': 'x', 'content': 'pas une note KOReader'}, headers=auth)[0], 400)
        self.assertEqual(self.request('POST', api, {'content': 12}, headers=auth)[0], 400)

        # A book outside the library and never synced is listed with its clippings
        status, _ = self.request('POST', api, {'title': 'Inconnu', 'category': 'KOReader', 'content':
                                               '# Livre absent\n##### Quelqu’un\n\n## Début\n### Page 3 @ bad date\nTexte seul'}, headers=auth)
        self.assertEqual(status, 200)
        absent = next(c for c in clippings().values() if c['text'] == 'Texte seul')
        self.assertEqual(self.books(user)[absent['document']]['title'], 'Livre absent')

    def test_clippings_in_the_web_ui(self):
        user = self.create_user('webclips')
        auth = self.basic('webclips')
        document = self.library_index.koreader_partial_md5(self.novel_path)
        self.put_progress(user, document, 0.3)
        clips = [
            {'id': 'c' * 32, 'spine': 3, 'start_page': 40, 'chapter': 'Chapitre 3', 'text': 'Plus loin', 'created_at': 1790000500},
            {'id': 'd' * 32, 'spine': 1, 'start_page': 5, 'chapter': 'Chapitre 1', 'text': 'Au début\nsur deux lignes',
             'note': 'Ma note', 'created_at': 1790000900},
        ]
        self.assertEqual(self.request('PUT', f'/api/v1/clippings/{document}', {'items': clips}, user=user)[0], 200)
        self.request('PUT', f"/api/v1/clippings/{'e' * 32}", {'items': [{'id': 'f' * 32, 'spine': 0, 'text': 'Ailleurs',
                                                                         'created_at': 1790000100}]}, user=user)

        # An account is always required
        self.assertEqual(self.raw_get('/webui/clippings')[0], 401)
        # Every book, newest clipping first; clippings in reading order
        status, data = self.request('GET', '/webui/clippings', headers=auth)
        self.assertEqual(status, 200, data)
        roman = data['groups'][0]
        self.assertEqual((roman['book']['title'], roman['book']['path']), ('Roman', 'Roman.epub'))
        self.assertEqual([c['text'] for c in roman['clippings']], ['Au début\nsur deux lignes', 'Plus loin'])
        self.assertEqual((roman['clippings'][0]['note'], roman['clippings'][0]['page']), ('Ma note', 5))
        self.assertEqual(data['groups'][1]['book']['path'], None)
        # One library book (book sheet), unknown books and other readers
        status, data = self.request('GET', '/webui/clippings?path=Roman.epub', headers=auth)
        self.assertEqual([g['book']['document'] for g in data['groups']], [document])
        self.assertEqual(self.request('GET', '/webui/clippings?path=Le%20Rouge%20et%20le%20Noir.epub', headers=auth)[1], {'groups': []})
        self.assertEqual(self.request('GET', '/webui/clippings?path=../x.epub', headers=auth)[0], 404)
        self.create_user('webclips2')
        self.assertEqual(self.request('GET', '/webui/clippings', headers=self.basic('webclips2'))[1], {'groups': []})

        # Markdown export of a book
        status, content_type, body = self.raw_get('/webui/clippings/export?path=Roman.epub', auth)
        self.assertEqual((status, content_type), (200, 'text/markdown; charset=utf-8'))
        self.assertEqual(body.decode(), '## Roman — Auteur\n\n> Au début\n> sur deux lignes\n\nMa note\n\n*Chapitre 1 · p. 5*'
                                        '\n\n> Plus loin\n\n*Chapitre 3 · p. 40*\n')
        status, _, body = self.raw_get('/webui/clippings/export', auth)
        self.assertIn(b'\n\n---\n\n', body)

        # The page: shared header and clipping partials included server-side
        status, content_type, body = self.raw_get('/clippings', auth)
        self.assertEqual((status, content_type), (200, 'text/html; charset=utf-8'))
        self.assertNotIn(b'<!--#include', body)
        self.assertIn(b'>Surlignages</h1>', body)
        self.assertIn(b'clip-text', body)

    def test_print_pages_without_spine(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'nospine.epub')
            create_novel_epub(path, [3600], spine=False)
            self.assertEqual(self.library_index.estimate_print_pages(path), 2)
            self.assertIsNone(self.library_index.estimate_print_pages(os.path.join(tmp, 'missing.epub')))

    def test_reader_metadata_and_filename_hash(self):
        user = self.create_user('metadata')
        by_name = md5('Le Rouge et le Noir.epub')
        self.put_progress(user, by_name, 0.1, metadata={'filename': 'Le Rouge et le Noir.epub', 'title': 'Titre', 'authors': 'A'})
        # Same book synced under its content hash becomes an alias of the first one
        partial = self.library_index.koreader_partial_md5(self.book_path)
        self.put_progress(user, partial, 0.2, metadata={'filename': 'Le Rouge et le Noir.epub'})
        books = self.books(user)
        self.assertEqual(list(books), [by_name])
        self.assertEqual(books[by_name]['aliases'], [partial])
        self.assertEqual(books[by_name]['title'], 'Titre')

    # ------------------------------------------------------- status and dates

    def test_status_dates_and_info(self):
        user = self.create_user('status')
        document = '1' * 32
        self.put_progress(user, document, 0.99, metadata={'title': 'Fini'})
        self.assertEqual(self.books(user)[document]['status'], 'finished')

        status, data = self.request('PUT', f'/api/v1/documents/{document}/status', {'status': 'dnf'}, user=user)
        self.assertEqual((status, data['status']), (200, 'dnf'))
        self.assertEqual(self.books(user)[document]['status'], 'dnf')
        status, _ = self.request('PUT', f'/api/v1/documents/{document}/status', {'status': 'lost'}, user=user)
        self.assertEqual(status, 403)

        status, data = self.request('PUT', f'/api/v1/documents/{document}/dates',
                                    {'start_date': '2026-10-02', 'finished_date': '2026-10-01'}, user=user)
        self.assertEqual((status, data['code']), (400, 2003))
        status, data = self.request('PUT', f'/api/v1/documents/{document}/dates',
                                    {'start_date': '2026-10-01', 'finished_date': '2026-10-02'}, user=user)
        self.assertEqual((status, data['status']), (200, 'finished'))

        status, data = self.request('PUT', f'/api/v1/documents/{document}/info',
                                    {'page_count': 320, 'title': 'Nouveau titre', 'cover_url': 'https://img.example/c.jpg'}, user=user)
        self.assertEqual(status, 200)
        book = self.books(user)[document]
        self.assertEqual((book['page_count'], book['title'], book['cover_url']), (320, 'Nouveau titre', 'https://img.example/c.jpg'))
        status, data = self.request('GET', f'/api/v1/documents/{document}/cover', user=user)
        self.assertEqual(data, {'url': 'https://img.example/c.jpg', 'pages': 320})

    def test_books_without_progress_for_30_days_are_paused(self):
        user = self.create_user('paused')
        document = '2' * 32
        self.put_progress(user, document, 0.3, metadata={'title': 'Old'})
        with sqlite3.connect(self.db_path) as conn:
            conn.execute('UPDATE progress_log SET at = at - 31 * 86400 WHERE user = ?', (user,))
        conn.close()
        self.assertEqual(self.books(user)[document]['status'], 'paused')
        self.put_progress(user, document, 0.35)
        self.assertEqual(self.books(user)[document]['status'], 'reading')

    # ------------------------------------------------------- merge and delete

    def test_merge_unmerge_and_delete(self):
        user = self.create_user('merge')
        first, second = '3' * 32, '4' * 32
        self.put_progress(user, first, 0.6, device_id='reader-a', metadata={'title': 'Copy A'})
        self.put_progress(user, second, 0.2, device_id='reader-b', metadata={'filename': 'b.epub'})

        status, data = self.request('POST', '/api/v1/documents/merge', {'document': second, 'into': first}, user=user)
        self.assertEqual((status, data), (200, {'document': first, 'merged': second, 'stats': True}))
        books = self.books(user)
        self.assertEqual(list(books), [first])
        self.assertEqual((books[first]['aliases'], books[first]['filename']), ([second], 'b.epub'))
        # The alias hash is served the furthest progress
        status, data = self.request('GET', f'/syncs/progress/{second}', user=user)
        self.assertEqual((data['document'], data['percentage']), (second, 0.6))
        status, _ = self.request('POST', '/api/v1/documents/merge', {'document': second, 'into': first}, user=user)
        self.assertEqual(status, 403)

        status, data = self.request('DELETE', f'/api/v1/documents/merge/{second}', user=user)
        self.assertEqual((status, data['unmerged']), (200, True))
        status, _ = self.request('DELETE', f'/api/v1/documents/merge/{second}', user=user)
        self.assertEqual(status, 404)

        status, data = self.request('DELETE', f'/api/v1/progress/{first}', user=user)
        self.assertEqual((status, data['deleted']), (200, True))
        self.assertEqual(self.books(user), {})
        status, data = self.request('DELETE', f'/api/v1/progress/{first}', user=user)
        self.assertEqual((status, data['message']), (404, 'Unknown document'))

    # -------------------------------------------------------------- clippings

    def test_clippings_firmware_sync(self):
        user = self.create_user('clips')
        url = f"/api/v1/clippings/{'5' * 32}"
        status, probe = self.request('GET', f'{url}?cursor=0&limit=1&format=reader', user=user)
        self.assertEqual((status, probe['sync_version'], probe['cursor'], probe['more'], probe['items']), (200, 2, 0, False, []))

        for clip_id, text in (('a' * 32, 'Premier passage'), ('b' * 32, 'Second passage')):
            clip = {'id': clip_id, 'spine': 2, 'start_page': 1, 'end_page': 1, 'pages': 30, 'start_word': 4,
                    'end_word': 9, 'words': 120, 'para': 12, 'chapter': 'Chapitre II', 'text': text,
                    'created_at': 1790000000, 'layout_signature': 77, 'start_offset': 10, 'end_offset': 40}
            status, data = self.request('PUT', url, {'items': [clip]}, user=user)
            self.assertEqual((status, data['accepted']), (200, 1))

        # Firmware pull loop: one item per page, cursor strictly increasing
        cursor, pulled = 0, []
        while True:
            status, page = self.request('GET', f'{url}?limit=1&format=reader&cursor={cursor}', user=user)
            self.assertEqual(status, 200)
            self.assertLessEqual(len(page['items']), 1)
            for item in page['items']:
                self.assertGreater(page['cursor'], cursor)
                self.assertNotIn('note', item)
                self.assertIsInstance(item['deleted'], int)
                pulled.append(item['text'])
            cursor = page['cursor']
            if not page['more']:
                break
        self.assertEqual(pulled, ['Premier passage', 'Second passage'])

        # A deleted clipping never comes back, even if re-uploaded
        status, _ = self.request('PUT', url, {'items': [{'id': 'a' * 32, 'deleted': 1}]}, user=user)
        self.assertEqual(status, 200)
        self.request('PUT', url, {'items': [{'id': 'a' * 32, 'spine': 1, 'text': 'retour'}]}, user=user)
        status, data = self.request('GET', '/api/v1/clippings', user=user)
        self.assertEqual([item['text'] for item in data['items']], ['Second passage'])

        status, data = self.request('PUT', url, {'items': [{'id': 'c' * 32, 'spine': 1, 'text': ''}]}, user=user)
        self.assertEqual((status, data['code']), (403, 2003))
        status, data = self.request('GET', f'{url}?cursor=abc', user=user)
        self.assertEqual((status, data['message']), (403, 'Invalid cursor'))

    def test_bookmarks_round_trip(self):
        user = self.create_user('bookmarks')
        url = f"/api/v1/bookmarks/{'6' * 32}"
        bookmark = {'id': '9f86d081884c7d65', 'xpath': '/body/p[3]', 'percentage': 0.35, 'summary': 'Ici', 'si': 3}
        status, data = self.request('PUT', url, {'items': [bookmark]}, user=user)
        self.assertEqual((status, data['accepted']), (200, 1))
        status, data = self.request('GET', url, user=user)
        self.assertEqual((data['items'][0]['xpath'], data['items'][0]['si'], data['items'][0]['pc']), ('/body/p[3]', 3, None))

    # ------------------------------------------------------------------ stats

    def test_stats_summary_book_stats_and_activity(self):
        user = self.create_user('stats')
        status, summary = self.request('GET', '/api/v1/stats/summary', user=user)
        self.assertEqual((status, summary['devices'], summary['daily']), (200, [], []))

        snapshot = {'device_id': 'crossink-1', 'device': 'CrossInk', 'v': 5, 'sessions': 10, 'seconds': 3600,
                    'pages': 120, 'completed': 1, 'tod': [0, 1, 2, 3], 'dow': [1, 1, 1, 1, 1, 1, 1],
                    'anchor_day': 9700, 'history_b64': 'Aw==', 'streak': 2,
                    'daily': [{'date': '2026-10-19', 'seconds': 90}]}
        status, data = self.request('PUT', '/api/v1/stats/global', snapshot, user=user)
        self.assertEqual((status, data['accepted_daily']), (200, 1))
        status, data = self.request('PUT', '/api/v1/stats/global', {**snapshot, 'daily': None}, user=user)
        self.assertEqual(status, 403)
        status, summary = self.request('GET', '/api/v1/stats/summary', user=user)
        self.assertEqual((summary['seconds'], summary['current_streak'], summary['daily']),
                         (3600, 2, [{'date': '2026-10-19', 'seconds': 90, 'minutes': 1.5}]))

        document = '7' * 32
        book = {'document': document, 'v': 1, 'sessions': 3, 'seconds': 900, 'pages': 40, 'avg_fwd': 30,
                'pace_n': 2, 'eta': 0, 'start_date': 0, 'finished_date': 0, 'completed': True,
                'start_manual': False, 'finish_manual': False, 'tod': [0, 0, 0, 0], 'dow': [0] * 7}
        status, data = self.request('PUT', '/api/v1/stats/books', {'device_id': 'crossink-1', 'items': [book]}, user=user)
        self.assertEqual((status, data['accepted']), (200, 1))
        status, data = self.request('GET', f'/api/v1/stats/books/{document}', user=user)
        self.assertEqual((data['combined']['sessions'], data['combined']['completed']), (3, True))

        # Completed in the device statistics -> finished in the library
        self.put_progress(user, document, 0.5, metadata={'title': 'Stats'})
        self.assertEqual(self.books(user)[document]['status'], 'finished')

        self.request('PUT', f'/api/v1/documents/{document}/info', {'page_count': 200}, user=user)
        self.put_progress(user, document, 0.75)
        status, activity = self.request('GET', '/api/v1/stats/activity?tz=-120', user=user)
        self.assertEqual(status, 200)
        self.assertEqual(activity['books'][0]['pages_read'], 150)
        self.assertEqual(sum(day['pages'] for day in activity['days']), 50)
        self.assertTrue(activity['reading_days'])

    # --------------------------------------------------- account & connectors

    def test_account_password_data_and_deletion(self):
        user = self.create_user('account')
        self.put_progress(user, '8' * 32, 0.4)
        status, data = self.request('POST', '/api/v1/account/password', {'key': md5('nouveau')}, user=user)
        self.assertEqual((status, data), (200, {'username': user}))
        self.assertEqual(self.request('GET', '/users/auth', user=user)[0], 401)
        self.assertEqual(self.request('GET', '/users/auth', user=user, password='nouveau')[0], 200)

        status, _ = self.request('DELETE', '/api/v1/account/data', user=user, password='nouveau')
        self.assertEqual(status, 200)
        self.assertEqual(self.request('GET', '/api/v1/progress', user=user, password='nouveau')[1], {'items': []})
        status, _ = self.request('DELETE', '/api/v1/account', user=user, password='nouveau')
        self.assertEqual(status, 200)
        self.assertEqual(self.request('GET', '/users/auth', user=user, password='nouveau')[0], 401)

    def test_online_metadata_fallback(self):
        from unittest import mock
        import test_online_metadata as samples
        from controllers import online_metadata

        service = online_metadata.OnlineMetadata.for_db(self.db_path)
        fake = samples.FakeFetch(samples.TestService.ROUTES)
        user = self.create_user('online')
        document = 'a1' * 16
        self.put_progress(user, document, 0.3, metadata={'title': 'The Martian', 'authors': 'Andy Weir'})
        with mock.patch.object(service, 'fetch', fake), mock.patch.object(service, 'limiter', samples.NoWaitLimiter()):
            # Disabled by default: no lookup, no change
            item = self.books(user)[document]
            service.wait_idle()
            self.assertEqual((item['cover_url'], item['moods'], fake.calls), (None, [], []))
            with mock.patch.dict(os.environ, {'ONLINE_METADATA': 'true', 'HARDCOVER_API_KEY': 'secret'}):
                self.assertIsNone(self.books(user)[document]['cover_url'])  # lookup queued, answered from the cache
                service.wait_idle()
                item = self.books(user)[document]
                self.assertEqual(item['cover_url'], 'https://a/martian/600x600bb.jpg')
                self.assertEqual((item['page_count'], item['hardcover_slug'], item['series']), (384, 'the-martian', 'Mars'))
                self.assertEqual(item['moods'], ['Adventurous', 'Funny', 'Tense'])
                self.assertEqual(self.request('GET', f'/api/v1/documents/{document}/cover', user=user)[1],
                                 {'url': 'https://a/martian/600x600bb.jpg', 'pages': 384})
                self.assertEqual(self.request('GET', f'/api/v1/documents/{document}/about', user=user)[1],
                                 {'description': 'Six days ago...'})
                self.assertEqual(self.request('GET', f'/api/v1/documents/{document}/next', user=user)[1],
                                 {'next': None, 'pending': True})
                service.wait_idle()
                status, data = self.request('GET', f'/api/v1/documents/{document}/next', user=user)
                self.assertEqual((data['series'], data['next']['title']), ('Mars', 'Mars 2'))
                status, data = self.request('GET', f'/api/v1/documents/{document}/cover/candidates?q=Martian', user=user)
                self.assertEqual(data['items'][0]['source'], 'Apple Books')
            # The library EPUB keeps priority for a matched book
            self.put_progress(user, md5('Le Rouge et le Noir.epub'), 0.1, metadata={'filename': 'Le Rouge et le Noir.epub'})
            calls = len(fake.calls)
            with mock.patch.dict(os.environ, {'ONLINE_METADATA': 'true'}):
                self.books(user)
                service.wait_idle()
            self.assertEqual(len(fake.calls), calls)

    def test_connectors_and_unknown_routes(self):
        user = self.create_user('connectors')
        self.assertEqual(self.request('GET', '/api/v1/connectors', user=user)[1], {'encryption': 'enabled', 'connectors': []})
        status, data = self.request('GET', f"/api/v1/documents/{'9' * 32}/matches", user=user)
        self.assertEqual(data, {'document': '9' * 32, 'services': []})
        self.assertEqual(self.request('GET', f"/api/v1/documents/{'9' * 32}/next", user=user)[1], {'next': None})
        status, data = self.request('POST', '/api/v1/connectors/hardcover/sync', user=user)
        self.assertEqual((status, data['message']), (404, 'Unknown connector'))
        status, data = self.request('GET', '/api/v1/nothing', user=user)
        self.assertEqual((status, data['code']), (404, 2003))
        self.assertEqual(self.request('GET', '/api/v1/progress')[0], 401)


class TestSyncStorageMigration(unittest.TestCase):
    def test_migrates_single_row_sync_records(self):
        storage_mod = importlib.import_module('controllers.sync_storage')
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'old.db')
            conn = sqlite3.connect(path)
            conn.execute('CREATE TABLE users (username TEXT PRIMARY KEY, password_md5 TEXT NOT NULL)')
            conn.execute('CREATE TABLE sync_records (user TEXT NOT NULL, document TEXT NOT NULL, percentage REAL, '
                         'progress TEXT, device TEXT, device_id TEXT, timestamp REAL, PRIMARY KEY (user, document))')
            conn.execute("INSERT INTO users VALUES ('alice', ?)", (md5('secret'),))
            conn.execute("INSERT INTO sync_records VALUES ('alice', 'doc1', 0.4, '/p', 'Kobo', NULL, 1790000000.7)")
            conn.commit()
            conn.close()

            storage = storage_mod.SyncStorage(path)
            row = storage.latest_progress('alice', 'doc1')
            self.assertEqual((row['device_id'], row['percentage'], row['updated_at']), ('Kobo', 0.4, 1790000000))
            self.assertEqual(storage.authenticate('alice', md5('secret')), 'alice')
            log_rows, _, _, _ = storage.activity_inputs('alice')
            self.assertEqual(len(log_rows), 1)


if __name__ == '__main__':
    unittest.main()
