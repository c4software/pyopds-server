import base64
import contextlib
import datetime
import email.utils
import gzip
import hashlib
import http.client
import http.server
import importlib
import io
import json
import os
import re
import shutil
import socketserver
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.parse
import xml.etree.ElementTree as ET
import zipfile
from unittest import mock
import support  # noqa: F401  (temporary library database, before any library is opened)

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

def create_epub(path, title, author, date=None, subjects=(), series=None, series_index=None):
        container_xml = """<?xml version='1.0' encoding='UTF-8'?>
<container version='1.0' xmlns='urn:oasis:names:tc:opendocument:xmlns:container'>
    <rootfiles>
        <rootfile full-path='content.opf' media-type='application/oebps-package+xml'/>
    </rootfiles>
</container>
"""
        date_element = f"<dc:date>{date}</dc:date>" if date else ""
        date_element += ''.join(f"<dc:subject>{subject}</dc:subject>" for subject in subjects)
        if series:
            date_element += (f'<meta name="calibre:series" content="{series}"/>'
                             f'<meta name="calibre:series_index" content="{series_index}"/>')
        opf_template = f"""<?xml version='1.0' encoding='UTF-8'?>
<package xmlns='http://www.idpf.org/2007/opf' version='3.0'>
    <metadata xmlns:dc='http://purl.org/dc/elements/1.1/'>
        <dc:title>{{title}}</dc:title>
        <dc:creator>{{author}}</dc:creator>
        {date_element}
    </metadata>
    <manifest/>
    <spine/>
</package>
"""
        base_dir = os.path.dirname(path)
        if base_dir and not os.path.exists(base_dir):
                os.makedirs(base_dir, exist_ok=True)
        with zipfile.ZipFile(path, 'w') as zf:
                zf.writestr('mimetype', 'application/epub+zip')
                zf.writestr('META-INF/container.xml', container_xml)
                zf.writestr('content.opf', opf_template.format(title=title, author=author))

class ThreadedTCPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True

class TestOPDSCatalog(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._orig_env = {
            'LIBRARY_DIR': os.environ.get('LIBRARY_DIR'),
            'PAGE_SIZE': os.environ.get('PAGE_SIZE'),
        }
        cls.library_dir = tempfile.TemporaryDirectory()
        os.environ['LIBRARY_DIR'] = cls.library_dir.name
        os.environ['PAGE_SIZE'] = '1'
        # Reload order to keep class identity consistent across routes/server
        importlib.reload(importlib.import_module('controllers.opds'))
        importlib.reload(importlib.import_module('routes'))
        cls.server = importlib.reload(importlib.import_module('server'))
        cls.alpha_path = os.path.join(cls.library_dir.name, 'alpha.epub')
        create_epub(cls.alpha_path, 'Alpha Title', 'Author One', date='2023-01-15')
        subfolder_path = os.path.join(cls.library_dir.name, 'Subfolder')
        os.makedirs(subfolder_path, exist_ok=True)
        cls.beta_path = os.path.join(subfolder_path, 'beta.epub')
        create_epub(cls.beta_path, 'Beta Title', 'Author Two', date='2024-06-20')
        # French accented book for UTF-8 testing
        cls.french_path = os.path.join(cls.library_dir.name, 'french.epub')
        create_epub(cls.french_path, 'Les Misérables — Édition complète', 'Victor Hugo', date='2022-03-10')
        now = time.time()
        os.utime(cls.alpha_path, (now - 200, now - 200))
        os.utime(cls.beta_path, (now - 50, now - 50))
        os.utime(cls.french_path, (now - 100, now - 100))
        cls.httpd = ThreadedTCPServer(('localhost', 0), cls.server.UnifiedHandler)
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()
        time.sleep(0.1)

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.thread.join(timeout=1)
        cls.library_dir.cleanup()
        for key, value in cls._orig_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        importlib.reload(importlib.import_module('controllers.opds'))
        importlib.reload(importlib.import_module('server'))

    def _get(self, path, headers=None):
        conn = http.client.HTTPConnection('localhost', self.port, timeout=5)
        conn.request('GET', path, headers=headers or {})
        response = conn.getresponse()
        body = response.read()
        resp_headers = dict(response.getheaders())
        status = response.status
        conn.close()
        return status, resp_headers, body

    def _parse_feed(self, body):
        xml_text = body.decode('utf-8')
        # Skip XML declaration and processing instructions
        lines = xml_text.split('\n')
        content_lines = []
        for line in lines:
            stripped = line.strip()
            if stripped and not stripped.startswith('<?'):
                content_lines.append(line)
        xml_content = '\n'.join(content_lines)
        return ET.fromstring(xml_content)

    def test_root_catalog_includes_sections_and_folder(self):
        status, headers, body = self._get('/opds')
        self.assertEqual(status, 200)
        content_type = headers.get('Content-Type', '')
        self.assertIn('application/xml', content_type)
        self.assertIn('charset=utf-8', content_type)
        # Connection: close must be present for HTTP client compatibility
        self.assertEqual(headers.get('Connection'), 'close')
        # Verify XML declaration is present
        xml_text = body.decode('utf-8')
        self.assertTrue(xml_text.startswith('<?xml version="1.0" encoding="UTF-8"?>'))
        ns = {'atom': 'http://www.w3.org/2005/Atom'}
        feed = self._parse_feed(body)
        entry_titles = {entry.find('atom:title', ns).text for entry in feed.findall('atom:entry', ns)}
        self.assertIn('All Books', entry_titles)
        self.assertIn('Recent Books', entry_titles)
        self.assertIn('By Year', entry_titles)
        self.assertIn('By Author', entry_titles)
        self.assertIn('Subfolder', entry_titles)

    def test_navigation_tiles_list_mosaic_covers(self):
        ns = {'atom': 'http://www.w3.org/2005/Atom'}

        def mosaic_covers(path, headers={'Accept': 'text/html,application/xhtml+xml'}):
            _, _, body = self._get(path, headers=headers)
            return {
                entry.find('atom:title', ns).text: {
                    link.get('href') for link in entry.findall('atom:link[@rel="urn:pyopds:mosaic-cover"]', ns)
                }
                for entry in self._parse_feed(body).findall('atom:entry', ns)
            }

        root = mosaic_covers('/opds')
        self.assertEqual(root['All Books'], {'/cover/alpha.epub', '/cover/french.epub', '/cover/Subfolder/beta.epub'})
        self.assertEqual(root['Subfolder'], {'/cover/Subfolder/beta.epub'})
        self.assertEqual(mosaic_covers('/opds/by-year')['2023 (1 books)'], {'/cover/alpha.epub'})
        self.assertEqual(mosaic_covers('/opds/by-author/letter/A?page=1')['Author One (1 livres)'], {'/cover/alpha.epub'})

    def test_opds_clients_get_no_mosaic_covers(self):
        # Mosaic covers are for the web view only: some OPDS clients list every link of a navigation entry
        for path in ('/opds', '/opds/by-year', '/opds/by-author/letter/A?page=1'):
            _, _, body = self._get(path, headers={'Accept': 'application/atom+xml'})
            rels = {link.get('rel') for link in self._parse_feed(body).iter('{http://www.w3.org/2005/Atom}link')}
            self.assertNotIn('urn:pyopds:mosaic-cover', rels, path)

    def test_xslt_polyfill_fetch_still_gets_mosaic_covers(self):
        # The XSLT polyfill fetches the feed again with fetch(): no text/html, but Fetch metadata headers
        _, _, body = self._get('/opds', headers={'Accept': '*/*', 'Sec-Fetch-Mode': 'cors'})
        rels = {link.get('rel') for link in self._parse_feed(body).iter('{http://www.w3.org/2005/Atom}link')}
        self.assertIn('urn:pyopds:mosaic-cover', rels)

    def test_feeds_load_the_xslt_polyfill(self):
        # Browsers without native XSLT (Chrome 158+) transform the feed with the polyfill script
        for path in ('/opds', '/opds/books', '/opds/search?q=alpha'):
            _, _, body = self._get(path)
            script = self._parse_feed(body).find('{http://www.w3.org/1999/xhtml}script')
            self.assertIsNotNone(script, path)
            self.assertRegex(script.get('src'), r'^/static/xslt-polyfill\.min\.js\?v=\d+$')
            self.assertRegex(body, rb'<\?xml-stylesheet type="text/xsl" href="/opds_to_html\.xslt\?v=[0-9a-f]{12}"\?>')

    def test_xslt_and_covers_are_cached_by_the_browsers(self):
        # The feeds name the current XSLT version: cached for good, gzip, ETag on the plain URL
        _, _, body = self._get('/opds')
        href = re.search(rb'href="(/opds_to_html\.xslt\?v=([0-9a-f]+))"', body)
        status, headers, data = self._get(href.group(1).decode(), {'Accept-Encoding': 'gzip'})
        self.assertEqual((status, headers['Content-Encoding']), (200, 'gzip'))
        self.assertIn('immutable', headers['Cache-Control'])
        self.assertIn(b'xsl:stylesheet', gzip.decompress(data))
        self.assertEqual(headers['ETag'], '"' + href.group(2).decode() + '"')
        status, headers, _ = self._get('/opds_to_html.xslt?v=old')
        self.assertEqual(headers['Cache-Control'], 'no-cache')
        self.assertEqual(self._get('/opds_to_html.xslt', {'If-None-Match': headers['ETag']})[0], 304)
        # Covers: a week, then revalidated by the EPUB's date
        epub_date = email.utils.formatdate(os.path.getmtime(os.path.join(self.library_dir.name, 'alpha.epub')), usegmt=True)
        status, headers, _ = self._get('/cover/alpha.epub', {'If-Modified-Since': epub_date})
        self.assertEqual((status, headers['Last-Modified']), (304, epub_date))
        self.assertIn('max-age=604800', headers['Cache-Control'])

    def test_served_xslt_versions_its_static_assets(self):
        # Every /static/ script and stylesheet of the XSLT gets ?v=<mtime>: a deployment changes their URLs
        status, headers, body = self._get('/opds_to_html.xslt')
        self.assertEqual(status, 200)
        self.assertEqual(headers.get('Cache-Control'), 'no-cache')
        tree = ET.fromstring(body)
        refs = [element.get('src') or element.get('href') for element in tree.iter()
                if element.tag in ('script', 'link') and (element.get('src') or element.get('href') or '').startswith('/static/')]
        self.assertIn('/static/catalog/main.js', {ref.partition('?')[0] for ref in refs})
        self.assertIn('/static/catalog/catalog.css', {ref.partition('?')[0] for ref in refs})
        for ref in refs:
            self.assertRegex(ref, r'^/static/[\w./-]+\?v=\d+$')
            name = ref[len('/static/'):].partition('?')[0]
            self.assertEqual(ref.partition('=')[2], str(int(os.path.getmtime(os.path.join(PROJECT_ROOT, 'static', name)))))
            # ... and is served
            status, _, _ = self._get(ref)
            self.assertEqual(status, 200, ref)

    def test_catalog_assets_are_served(self):
        catalog_dir = os.path.join(PROJECT_ROOT, 'static', 'catalog')
        names = sorted(os.listdir(catalog_dir))
        self.assertIn('main.js', names)
        for name in names:
            content_type = 'text/css; charset=utf-8' if name.endswith('.css') else 'text/javascript; charset=utf-8'
            with open(os.path.join(catalog_dir, name), 'rb') as f:
                data = f.read()
            # Versioned: immutable, gzip-compressed when accepted
            status, headers, body = self._get(f'/static/catalog/{name}?v=1', headers={'Accept-Encoding': 'gzip'})
            self.assertEqual((status, headers.get('Content-Type')), (200, content_type), name)
            self.assertEqual(headers.get('Cache-Control'), 'public, max-age=31536000, immutable')
            self.assertEqual(headers.get('Content-Encoding'), 'gzip')
            self.assertEqual(gzip.decompress(body), data)
            # Plain URL: revalidated, then 304
            status, headers, body = self._get(f'/static/catalog/{name}')
            self.assertEqual((status, body, headers.get('Cache-Control')), (200, data, 'no-cache'))
            status, _, _ = self._get(f'/static/catalog/{name}', headers={'If-Modified-Since': headers['Last-Modified']})
            self.assertEqual(status, 304)

    def test_static_subfolder_refuses_traversal(self):
        for path in ('/static/catalog/../server.py', '/static/catalog/..%2Fserver.py', '/static/catalog/%2e%2e/x.js',
                     '/static/../server.py', '/static/catalog/.hidden.js', '/static/.catalog/main.js',
                     '/static/catalog/sub/main.js', '/static/catalog/main.py', '/static/catalog/missing.js',
                     '/static/catalog/', '/static/catalog//main.js', '/static/catalog/main.js/',
                     '/static/../controllers/webui.py', '/static/catalog/main..js'):
            status, _, _ = self._get(path)
            self.assertEqual(status, 404, path)

    def test_all_books_feed_paginates_and_lists_books(self):
        ns = {'atom': 'http://www.w3.org/2005/Atom'}
        status, headers, body = self._get('/opds/books?page=1')
        self.assertEqual(status, 200)
        self.assertIn('application/xml', headers.get('Content-Type'))
        self.assertIn('charset=utf-8', headers.get('Content-Type'))
        feed = self._parse_feed(body)
        entries = feed.findall('atom:entry', ns)
        self.assertEqual(len(entries), 1)
        first_entry = entries[0]
        self.assertEqual(first_entry.find('atom:title', ns).text, 'Alpha Title')
        link_hrefs = {link.get('href') for link in first_entry.findall('atom:link', ns)}
        self.assertIn('/download/alpha.epub', link_hrefs)
        # OPDS clients (KoReader, CrossPoint Sync app) only show covers declared in the feed
        rels = {link.get('rel'): link.get('href') for link in first_entry.findall('atom:link', ns)}
        self.assertEqual(rels['http://opds-spec.org/image'], '/cover/alpha.epub')
        self.assertEqual(rels['http://opds-spec.org/image/thumbnail'], '/cover/alpha.epub')
        pagination_links = {link.get('rel'): link.get('href') for link in feed.findall('atom:link', ns)}
        self.assertIn('next', pagination_links)
        self.assertTrue(pagination_links['next'].endswith('page=2'))
        # The CrossPoint Sync app would list first/last as navigation rows
        self.assertNotIn('first', pagination_links)
        self.assertNotIn('last', pagination_links)
        status2, _, body2 = self._get('/opds/books?page=2')
        self.assertEqual(status2, 200)
        feed2 = self._parse_feed(body2)
        entries2 = feed2.findall('atom:entry', ns)
        self.assertEqual(len(entries2), 1)
        self.assertEqual(entries2[0].find('atom:title', ns).text, 'Beta Title')
        link_hrefs2 = {link.get('href') for link in entries2[0].findall('atom:link', ns)}
        self.assertIn('/download/Subfolder/beta.epub', link_hrefs2)

    def test_folder_recent_and_download_endpoints(self):
        ns = {'atom': 'http://www.w3.org/2005/Atom'}
        status, headers, body = self._get('/opds/folder/Subfolder?page=1')
        self.assertEqual(status, 200)
        self.assertIn('application/xml', headers.get('Content-Type'))
        self.assertIn('charset=utf-8', headers.get('Content-Type'))
        feed = self._parse_feed(body)
        entries = feed.findall('atom:entry', ns)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].find('atom:title', ns).text, 'Beta Title')
        link_hrefs = {link.get('href') for link in entries[0].findall('atom:link', ns)}
        self.assertIn('/download/Subfolder/beta.epub', link_hrefs)
        status_recent, headers_recent, body_recent = self._get('/opds/recent')
        self.assertEqual(status_recent, 200)
        self.assertIn('application/xml', headers_recent.get('Content-Type'))
        self.assertIn('charset=utf-8', headers_recent.get('Content-Type'))
        recent_feed = self._parse_feed(body_recent)
        recent_titles = [entry.find('atom:title', ns).text for entry in recent_feed.findall('atom:entry', ns)]
        self.assertGreaterEqual(len(recent_titles), 2)
        self.assertEqual(recent_titles[0], 'Beta Title')
        status_download, download_headers, download_body = self._get('/download/Subfolder/beta.epub')
        self.assertEqual(status_download, 200)
        self.assertEqual(download_headers.get('Content-Type'), 'application/epub+zip')
        self.assertGreater(len(download_body), 0)
        status_forbidden, _, _ = self._get('/download/../server.py')
        self.assertEqual(status_forbidden, 403)

    def test_by_year_catalog_lists_years(self):
        """Test /opds/by-year returns a catalog of years."""
        ns = {'atom': 'http://www.w3.org/2005/Atom'}
        status, headers, body = self._get('/opds/by-year')
        self.assertEqual(status, 200)
        self.assertIn('application/xml', headers.get('Content-Type'))
        self.assertIn('charset=utf-8', headers.get('Content-Type'))
        feed = self._parse_feed(body)
        entries = feed.findall('atom:entry', ns)
        self.assertGreaterEqual(len(entries), 2)
        # Check that years 2023 and 2024 are in the titles
        entry_titles = [entry.find('atom:title', ns).text for entry in entries]
        year_found_2023 = any('2023' in t for t in entry_titles)
        year_found_2024 = any('2024' in t for t in entry_titles)
        self.assertTrue(year_found_2023, "Year 2023 should be in catalog")
        self.assertTrue(year_found_2024, "Year 2024 should be in catalog")

    def test_by_year_books_lists_books_for_year(self):
        """Test /opds/by-year/2023 returns books from that year."""
        ns = {'atom': 'http://www.w3.org/2005/Atom'}
        status, headers, body = self._get('/opds/by-year/2023?page=1')
        self.assertEqual(status, 200)
        self.assertIn('application/xml', headers.get('Content-Type'))
        self.assertIn('charset=utf-8', headers.get('Content-Type'))
        feed = self._parse_feed(body)
        entries = feed.findall('atom:entry', ns)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].find('atom:title', ns).text, 'Alpha Title')

    def test_by_author_catalog_lists_letters(self):
        """Test /opds/by-author returns A-Z letter navigation."""
        ns = {'atom': 'http://www.w3.org/2005/Atom'}
        status, headers, body = self._get('/opds/by-author')
        self.assertEqual(status, 200)
        self.assertIn('application/xml', headers.get('Content-Type'))
        self.assertIn('charset=utf-8', headers.get('Content-Type'))
        feed = self._parse_feed(body)
        entries = feed.findall('atom:entry', ns)
        # Should have A-Z + #
        self.assertEqual(len(entries), 27)
        entry_titles = [entry.find('atom:title', ns).text for entry in entries]
        self.assertIn('A', entry_titles)
        self.assertIn('B', entry_titles)

    def test_by_author_letter_lists_authors(self):
        """Test /opds/by-author/letter/A returns authors starting with A."""
        ns = {'atom': 'http://www.w3.org/2005/Atom'}
        status, headers, body = self._get('/opds/by-author/letter/A?page=1')
        self.assertEqual(status, 200)
        self.assertIn('application/xml', headers.get('Content-Type'))
        self.assertIn('charset=utf-8', headers.get('Content-Type'))
        feed = self._parse_feed(body)
        entries = feed.findall('atom:entry', ns)
        # PAGE_SIZE=1, so only 1 author per page
        self.assertGreaterEqual(len(entries), 1)
        entry_titles = [entry.find('atom:title', ns).text for entry in entries]
        # Author should start with 'Author'
        self.assertTrue(all('Author' in t for t in entry_titles))

    def test_by_author_books_lists_books_for_author(self):
        """Test /opds/by-author/Author%20One returns books by that author."""
        ns = {'atom': 'http://www.w3.org/2005/Atom'}
        status, headers, body = self._get('/opds/by-author/Author%20One?page=1')
        self.assertEqual(status, 200)
        self.assertIn('application/xml', headers.get('Content-Type'))
        self.assertIn('charset=utf-8', headers.get('Content-Type'))
        feed = self._parse_feed(body)
        entries = feed.findall('atom:entry', ns)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].find('atom:title', ns).text, 'Alpha Title')

    def test_utf8_encoding_with_french_accents(self):
        """Test that French accented characters are correctly encoded in OPDS feeds."""
        import xml.parsers.expat as expat_mod
        ns = {'atom': 'http://www.w3.org/2005/Atom'}

        # Fetch all books (paginate to find the French book)
        all_titles = []
        for page in range(1, 10):
            status, headers, body = self._get(f'/opds/books?page={page}')
            if status != 200:
                break
            feed = self._parse_feed(body)
            entries = feed.findall('atom:entry', ns)
            if not entries:
                break
            for entry in entries:
                all_titles.append(entry.find('atom:title', ns).text)

        french_title = 'Les Misérables — Édition complète'
        self.assertIn(french_title, all_titles, "French book with accents should be in the catalog")

        # Verify the raw XML is valid UTF-8 with correct declaration
        status, headers, body = self._get('/opds/books?page=1')
        xml_text = body.decode('utf-8')
        self.assertTrue(xml_text.startswith('<?xml version="1.0" encoding="UTF-8"?>'))

        # Verify expat (same parser as CrossPoint) can parse the full response
        parsed_titles = []
        state = {'in_entry': False, 'in_title': False, 'text': ''}

        def start_el(name, attrs):
            if name == 'entry':
                state['in_entry'] = True
            elif state['in_entry'] and name == 'title':
                state['in_title'] = True
                state['text'] = ''

        def end_el(name):
            if name == 'title' and state['in_title']:
                parsed_titles.append(state['text'])
                state['in_title'] = False
            elif name == 'entry':
                state['in_entry'] = False

        def char_data(data):
            if state['in_title']:
                state['text'] += data

        p = expat_mod.ParserCreate()
        p.StartElementHandler = start_el
        p.EndElementHandler = end_el
        p.CharacterDataHandler = char_data
        # Parse the raw bytes exactly as CrossPoint would (expat with no namespace processing)
        p.Parse(body, True)
        self.assertTrue(len(parsed_titles) > 0, "Expat should parse at least one entry title")



class TestGenres(unittest.TestCase):
    def test_normalize_genres_merges_variants_and_drops_tags(self):
        genres = importlib.import_module('controllers.genres')
        self.assertEqual(
            genres.normalize_genres(['SF', 'Science fiction, Aventure', 'genre:sciencefiction', 'lang:fr',
                                     'state:toread', 'Fiction', 'roman policier; Polar', 'Policier historique']),
            [('science fiction', 'Science-fiction'), ('aventure', 'Aventure'), ('policier', 'Policier'),
             ('policier historique', 'Policier historique')],
        )
        self.assertEqual(genres.normalize_genres(['  ', '1999', 'interest:3']), [])
        self.assertEqual(genres.normalize_genres(['Fantastique', 'fantastique']), [('fantastique', 'Fantastique')])

    def test_custom_genre_config_file(self):
        genres = importlib.import_module('controllers.genres')
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'genres.toml')
            with open(path, 'w', encoding='utf-8') as f:
                f.write('min_books = 5\nignored = ["Divers"]\n[aliases]\n"Imaginaire" = ["SF", "Fantasy"]\n')
            config = genres.load_genre_config(path)
            self.assertEqual(config.min_books, 5)
            self.assertEqual(
                genres.normalize_genres(['sf', 'fantasy', 'imaginaire', 'divers', 'Polar'], config=config),
                [('imaginaire', 'Imaginaire'), ('polar', 'Polar')],
            )

            default = genres.load_genre_config(genres.DEFAULT_GENRES_CONFIG_PATH)
            genres.get_genre_config.cache_clear()
            try:
                with mock.patch.dict(os.environ, {'GENRES_CONFIG_PATH': path}):
                    merged = genres.get_genre_config()
                # The custom file completes the default rules and wins over them
                self.assertEqual(merged.min_books, 5)
                self.assertEqual(
                    genres.normalize_genres(['sf', 'Polar', 'divers', 'Fiction'], config=merged),
                    [('imaginaire', 'Imaginaire'), ('policier', 'Policier')],
                )
                self.assertIn('policier', merged.aliases)
                self.assertNotIn('fantasy', merged.ignored)
                genres.get_genre_config.cache_clear()
                with open(path, 'w', encoding='utf-8') as f:
                    f.write('min_books = "trois"\n')
                with mock.patch.dict(os.environ, {'GENRES_CONFIG_PATH': path}), \
                        contextlib.redirect_stdout(io.StringIO()):
                    # Invalid custom file: the default rules still apply
                    self.assertEqual(genres.get_genre_config(), default)
            finally:
                genres.get_genre_config.cache_clear()


class TestGenreCatalog(unittest.TestCase):
    ns = {'atom': 'http://www.w3.org/2005/Atom'}

    @classmethod
    def setUpClass(cls):
        cls._orig_env = {'LIBRARY_DIR': os.environ.get('LIBRARY_DIR'), 'PAGE_SIZE': os.environ.get('PAGE_SIZE')}
        cls.library_dir = tempfile.TemporaryDirectory()
        os.environ['LIBRARY_DIR'] = cls.library_dir.name
        os.environ['PAGE_SIZE'] = '2'
        importlib.reload(importlib.import_module('controllers.opds'))
        importlib.reload(importlib.import_module('routes'))
        cls.server = importlib.reload(importlib.import_module('server'))
        books = {
            'a': ['SF'], 'b': ['Science-fiction, Aventure'], 'c': ['genre:sciencefiction', 'lang:fr'],
            'd': ['Policier'], 'e': ['polar'], 'f': ['Roman policier'], 'g': ['Lancedragon'], 'h': [],
        }
        for name, subjects in books.items():
            create_epub(os.path.join(cls.library_dir.name, f'{name}.epub'), f'Book {name}', 'Author', subjects=subjects)
        cls.httpd = ThreadedTCPServer(('localhost', 0), cls.server.UnifiedHandler)
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()
        time.sleep(0.1)

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.thread.join(timeout=1)
        cls.library_dir.cleanup()
        for key, value in cls._orig_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        importlib.reload(importlib.import_module('controllers.opds'))
        importlib.reload(importlib.import_module('server'))

    def _feed(self, path):
        conn = http.client.HTTPConnection('localhost', self.port, timeout=5)
        conn.request('GET', path)
        response = conn.getresponse()
        body = response.read()
        conn.close()
        if response.status != 200:
            return response.status, None
        text = '\n'.join(line for line in body.decode('utf-8').split('\n') if not line.strip().startswith('<?'))
        return response.status, ET.fromstring(text)

    def titles(self, feed):
        return [entry.find('atom:title', self.ns).text for entry in feed.findall('atom:entry', self.ns)]

    def test_root_links_to_genres(self):
        _, feed = self._feed('/opds')
        self.assertIn('By Genre', self.titles(feed))

    def test_genre_catalog_lists_merged_genres(self):
        status, feed = self._feed('/opds/by-genre')
        self.assertEqual(status, 200)
        # Aventure and Lancedragon have fewer than 3 books, lang:fr is a technical tag
        self.assertEqual(self.titles(feed), ['Policier (3 books)', 'Science-fiction (3 books)'])
        hrefs = [entry.find('atom:link', self.ns).get('href') for entry in feed.findall('atom:entry', self.ns)]
        self.assertEqual(hrefs, ['/opds/by-genre/Policier?page=1', '/opds/by-genre/Science-fiction?page=1'])

    def test_genre_books_are_paginated(self):
        status, feed = self._feed('/opds/by-genre/Science-fiction?page=1')
        self.assertEqual(status, 200)
        self.assertEqual(feed.find('atom:title', self.ns).text, 'Genre Science-fiction (Page 1 of 2)')
        self.assertEqual(self.titles(feed), ['Book a', 'Book b'])
        rels = {link.get('rel'): link.get('href') for link in feed.findall('atom:link', self.ns)}
        self.assertEqual(rels['next'], '/opds/by-genre/Science-fiction?page=2')
        self.assertEqual(rels['up'], '/opds/by-genre')
        _, page2 = self._feed('/opds/by-genre/Science-fiction?page=2')
        self.assertEqual(self.titles(page2), ['Book c'])

    def test_genre_lookup_accepts_variants_and_hidden_genres(self):
        _, feed = self._feed('/opds/by-genre/sf?page=1')
        self.assertEqual(self.titles(feed), ['Book a', 'Book b'])
        _, feed = self._feed('/opds/by-genre/Lancedragon')
        self.assertEqual(self.titles(feed), ['Book g'])
        self.assertEqual(self._feed('/opds/by-genre/Inconnu')[0], 404)


def create_rich_epub(path, title, author, date=None, subjects=(), series=None, series_index=None,
                     rating=None, description=None):
    """EPUB with Calibre metadata (series, series index, rating) and an escaped HTML description."""
    from xml.sax.saxutils import escape
    metadata = [f'<dc:title>{escape(title)}</dc:title>', f'<dc:creator>{escape(author)}</dc:creator>']
    if date:
        metadata.append(f'<dc:date>{date}</dc:date>')
    metadata += [f'<dc:subject>{escape(subject)}</dc:subject>' for subject in subjects]
    if description:
        metadata.append(f'<dc:description>{escape(description)}</dc:description>')
    if series:
        metadata.append(f'<meta name="calibre:series" content="{escape(series)}"/>')
        if series_index is not None:
            metadata.append(f'<meta name="calibre:series_index" content="{series_index}"/>')
    if rating is not None:
        metadata.append(f'<meta name="calibre:rating" content="{rating}"/>')
    opf = (
        "<?xml version='1.0' encoding='UTF-8'?>"
        "<package xmlns='http://www.idpf.org/2007/opf' version='2.0'>"
        "<metadata xmlns:dc='http://purl.org/dc/elements/1.1/' xmlns:opf='http://www.idpf.org/2007/opf'>"
        + ''.join(metadata) + "</metadata><manifest/><spine/></package>"
    )
    container = ("<?xml version='1.0'?><container version='1.0' xmlns='urn:oasis:names:tc:opendocument:xmlns:container'>"
                 "<rootfiles><rootfile full-path='content.opf' media-type='application/oebps-package+xml'/></rootfiles></container>")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with zipfile.ZipFile(path, 'w') as zf:
        zf.writestr('mimetype', 'application/epub+zip')
        zf.writestr('META-INF/container.xml', container)
        zf.writestr('content.opf', opf)


class TestBookDetailsInFeeds(unittest.TestCase):
    """Description, genres, year, series and rating of the book entries, and the series feed."""

    ns = {'atom': 'http://www.w3.org/2005/Atom', 'dc': 'http://purl.org/dc/terms/', 'pyopds': 'urn:pyopds:metadata'}

    @classmethod
    def setUpClass(cls):
        cls._orig_env = {'LIBRARY_DIR': os.environ.get('LIBRARY_DIR'), 'PAGE_SIZE': os.environ.get('PAGE_SIZE')}
        cls.library_dir = tempfile.TemporaryDirectory()
        os.environ['LIBRARY_DIR'] = cls.library_dir.name
        os.environ['PAGE_SIZE'] = '10'
        root = cls.library_dir.name
        # File names in the reverse order of the volumes: the series feed sorts by position
        create_rich_epub(os.path.join(root, 'a-fondation-2.epub'), 'Fondation et Empire', 'Isaac Asimov',
                         date='1952-01-01', subjects=['Science Fiction'], series='La Fondation', series_index='2',
                         rating='9', description='<p>Un <b>roman</b> &amp; plus</p><p>Suite</p>')
        create_rich_epub(os.path.join(root, 'b-fondation-1-5.epub'), 'Nouvelle', 'Isaac Asimov',
                         series='La Fondation', series_index='1.5')
        create_rich_epub(os.path.join(root, 'Cycle', 'c-fondation-1.epub'), 'Fondation', 'Isaac Asimov',
                         date='1951', series='La Fondation', series_index='1.0', rating='0')
        create_rich_epub(os.path.join(root, 'd-seul.epub'), 'Livre seul', 'Autre Auteur')
        for module in ('controllers.opds', 'controllers.library_index'):
            importlib.reload(importlib.import_module(module))
        importlib.reload(importlib.import_module('routes'))
        cls.server = importlib.reload(importlib.import_module('server'))
        cls.httpd = ThreadedTCPServer(('localhost', 0), cls.server.UnifiedHandler)
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()
        time.sleep(0.1)

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.thread.join(timeout=1)
        cls.library_dir.cleanup()
        for key, value in cls._orig_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        importlib.reload(importlib.import_module('controllers.opds'))
        importlib.reload(importlib.import_module('controllers.library_index'))
        importlib.reload(importlib.import_module('server'))

    def _feed(self, path):
        status, _, body = http_request(self.port, 'GET', path)
        if status != 200:
            return status, None
        return status, ET.fromstring(body)

    def _entries(self, feed):
        return {entry.find('atom:title', self.ns).text: entry for entry in feed.findall('atom:entry', self.ns)}

    def test_book_entry_carries_details(self):
        status, feed = self._feed('/opds/books?page=1')
        self.assertEqual(status, 200)
        entry = self._entries(feed)['Fondation et Empire']
        self.assertEqual(entry.find('atom:summary', self.ns).get('type'), 'text')
        self.assertEqual(entry.find('atom:summary', self.ns).text, 'Un roman & plus\n\nSuite')
        self.assertEqual([(c.get('term'), c.get('label')) for c in entry.findall('atom:category', self.ns)],
                         [('Science-fiction', 'Science-fiction')])
        self.assertEqual(entry.find('dc:issued', self.ns).text, '1952')
        series = entry.find('pyopds:series', self.ns)
        self.assertEqual((series.text, series.get('index')), ('La Fondation', '2'))
        self.assertEqual(entry.find('pyopds:rating', self.ns).text, '4.5')
        # Navigation from the book ("More like this" of the CrossPoint Sync app): series, author, genres
        related = [(link.get('href'), link.get('title')) for link in entry.findall('atom:link[@rel="related"]', self.ns)]
        self.assertEqual(related, [
            ('/opds/series?name=La%20Fondation', 'Série : La Fondation (tome 2)'),
            ('/opds/by-author/Isaac%20Asimov?page=1', 'Auteur : Isaac Asimov'),
            ('/opds/by-genre/Science-fiction?page=1', 'Genre : Science-fiction'),
        ])
        self.assertTrue(all('kind=acquisition' in link.get('type') for link in entry.findall('atom:link[@rel="related"]', self.ns)))
        for href, _ in related:
            self.assertEqual(self._feed(href)[0], 200, href)
        # Book without metadata: nothing invented, only its author
        alone = self._entries(feed)['Livre seul']
        for path in ('atom:summary', 'atom:category', 'dc:issued', 'pyopds:series', 'pyopds:rating'):
            self.assertIsNone(alone.find(path, self.ns), path)
        self.assertEqual([link.get('title') for link in alone.findall('atom:link[@rel="related"]', self.ns)],
                         [f"Auteur : {alone.find('atom:author/atom:name', self.ns).text}"])
        # A rating of 0 means "not rated"
        self.assertIsNone(self._entries(feed)['Fondation'].find('pyopds:rating', self.ns))

    def test_details_in_other_book_feeds(self):
        for path in ('/opds/recent', '/opds/by-year/1952', '/opds/by-author/Isaac%20Asimov',
                     '/opds/by-genre/Science-fiction', '/opds/search?q=empire', '/opds/folder/'):
            status, feed = self._feed(path)
            self.assertEqual(status, 200, path)
            entry = self._entries(feed)['Fondation et Empire']
            self.assertEqual(entry.find('pyopds:series', self.ns).text, 'La Fondation', path)
            self.assertIsNotNone(entry.find('atom:summary', self.ns), path)
        # Folder feeds now list covers like the other feeds
        _, feed = self._feed('/opds/folder/Cycle')
        rels = {link.get('rel') for link in self._entries(feed)['Fondation'].findall('atom:link', self.ns)}
        self.assertIn('http://opds-spec.org/image', rels)
        self.assertIn('related', rels)

    def test_series_feed_sorted_by_position(self):
        status, feed = self._feed('/opds/series?name=La%20Fondation')
        self.assertEqual(status, 200)
        self.assertEqual(feed.find('atom:title', self.ns).text, 'Série : La Fondation')
        titles = [entry.find('atom:title', self.ns).text for entry in feed.findall('atom:entry', self.ns)]
        self.assertEqual(titles, ['Fondation', 'Nouvelle', 'Fondation et Empire'])
        # Names compared normalized (case, accents)
        _, feed = self._feed('/opds/series?name=la%20fondation')
        self.assertEqual(len(feed.findall('atom:entry', self.ns)), 3)

    def test_series_feed_unknown_or_invalid(self):
        for name in ('Inconnue', '', '../../etc/passwd', '..%2F..%2Fa-fondation-2.epub'):
            self.assertEqual(self._feed(f'/opds/series?name={name}')[0], 404, name)
        self.assertEqual(self._feed('/opds/series')[0], 404)

    def test_search_finds_series(self):
        _, feed = self._feed('/opds/search?q=fondation')
        self.assertEqual(len(feed.findall('atom:entry', self.ns)), 3)
        _, feed = self._feed('/opds/search?q=la%20fondation')
        self.assertEqual(len(feed.findall('atom:entry', self.ns)), 3)

    def test_book_meta_is_cached_by_mtime(self):
        library_index = importlib.import_module('controllers.library_index')
        library = library_index.LibraryIndex.get_instance()
        path = os.path.join(self.library_dir.name, 'a-fondation-2.epub')
        meta = library.book_meta(path)
        self.assertEqual((meta['year'], meta['rating'], meta['series_index']), (1952, 4.5, 2))
        opds = importlib.import_module('controllers.opds')
        with mock.patch.object(opds.BookMetadata, 'extract_epub_info', side_effect=AssertionError('read again')):
            self.assertEqual(library.book_meta(path), meta)
            # details() reuses it: only the cover and the page estimate are read
            self.assertEqual(library.details(path)['description'], meta['description'])
        self.assertEqual([os.path.basename(p) for _, p in library.series_books('LA FONDATION')],
                         ['c-fondation-1.epub', 'b-fondation-1-5.epub', 'a-fondation-2.epub'])

    def test_xslt_is_valid_xml_without_inline_code(self):
        tree = ET.parse(os.path.join(PROJECT_ROOT, 'static', 'opds_to_html.xslt'))
        # Page code lives in static/catalog/: no <style>, a single small inline script (theme before the first paint)
        self.assertEqual(list(tree.iter('style')), [])
        inline = [element.text for element in tree.iter('script') if element.text and element.text.strip()]
        self.assertEqual(len(inline), 1)
        self.assertLessEqual(len(inline[0].strip().splitlines()), 10)
        self.assertIn("classList.toggle('dark'", inline[0])
        # Every referenced static file exists
        for element in tree.iter():
            ref = element.get('src') or element.get('href') or ''
            if element.tag in ('script', 'link') and ref.startswith('/static/'):
                self.assertTrue(os.path.isfile(os.path.join(PROJECT_ROOT, 'static', ref[len('/static/'):])), ref)
        node = shutil.which('node')
        if node is None:
            self.skipTest('node is not installed: scripts not checked')
        catalog_dir = os.path.join(PROJECT_ROOT, 'static', 'catalog')
        with tempfile.TemporaryDirectory() as tmp:
            paths = [os.path.join(catalog_dir, name) for name in sorted(os.listdir(catalog_dir)) if name.endswith('.js')]
            for number, script in enumerate(inline):
                paths.append(os.path.join(tmp, f'inline{number}.js'))
                with open(paths[-1], 'w', encoding='utf-8') as f:
                    f.write(script)
            for path in paths:
                result = subprocess.run([node, '--check', path], capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, f'{path}: {result.stderr}')


def basic_header(user: str, password: str) -> dict:
    """Authorization header for HTTP Basic, UTF-8 encoded."""
    token = base64.b64encode(f'{user}:{password}'.encode('utf-8')).decode('ascii')
    return {'Authorization': f'Basic {token}'}


def http_request(port, method, path, body=None, headers=None):
    """Send a request to the test server; returns (status, headers, body)."""
    conn = http.client.HTTPConnection('localhost', port, timeout=10)
    headers = dict(headers or {})
    payload = None
    if body is not None:
        payload = json.dumps(body)
        headers['Content-Type'] = 'application/json'
    conn.request(method, path, body=payload, headers=headers)
    response = conn.getresponse()
    data = response.read()
    result = response.status, dict(response.getheaders()), data
    conn.close()
    return result


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
        for module in ('controllers.opds', 'controllers.sync_storage', 'controllers.koreader_sync',
                       'controllers.library_index', 'controllers.reading_state', 'controllers.recommendations',
                       'controllers.crosspoint_sync', 'controllers.opds_auth', 'controllers.reader_feeds'):
            importlib.reload(importlib.import_module(module))
        importlib.reload(importlib.import_module('routes'))
        cls.server = importlib.reload(importlib.import_module('server'))
        cls.koreader_sync = importlib.import_module('controllers.koreader_sync')
        sync_storage = importlib.import_module('controllers.sync_storage')
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
        importlib.reload(importlib.import_module('controllers.opds'))
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
            self.assertEqual(self._request('GET', '/static/reading.js')[0], 200)

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
        self.assertIn('controllers.opds', loaded)
        for module in ('controllers.koreader_sync', 'controllers.sync_storage', 'controllers.crosspoint_sync',
                       'controllers.web_reader', 'controllers.reader_feeds', 'controllers.clippings', 'controllers.online_metadata',
                       'controllers.audiobook', 'controllers.audiobook_config', 'controllers.speech_client'):
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
            self.assertIn(b'/static/login.js', body)

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
        opds_auth = importlib.import_module('controllers.opds_auth')
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


class TestRecommendationScoring(unittest.TestCase):
    """Pure scoring functions of controllers.recommendations and Basic credentials parsing."""

    @classmethod
    def setUpClass(cls):
        cls.rec = importlib.import_module('controllers.recommendations')

    def test_reading_signal(self):
        signal = self.rec.reading_signal
        self.assertEqual(signal('finished', 0.2), 'read')
        self.assertEqual(signal('reading', 0.95), 'read')
        self.assertEqual(signal('dnf', 0.99), 'dnf')
        self.assertEqual(signal('reading', 0.3), 'reading')
        self.assertEqual(signal('paused', 0.3), 'paused')

    def test_recency_favours_recent_reads(self):
        now = 1_000_000_000
        self.assertAlmostEqual(self.rec.recency_factor(now, now), 1.0)
        self.assertLess(self.rec.recency_factor(now - 365 * 86400, now), self.rec.recency_factor(now - 86400, now))
        self.assertGreaterEqual(self.rec.recency_factor(0, now), 0.5)

    def test_profile_ignores_abandoned_books(self):
        profile = self.rec.build_profile([
            {'author': 'A', 'genres': ['SF'], 'status': 'dnf', 'percentage': 0.5, 'updated_at': 0},
        ], 0)
        self.assertTrue(profile.is_empty)
        self.assertEqual(self.rec.recommend([{'path': 'x', 'author': 'A', 'genres': ['SF']}], profile), [])

    def test_scoring_ranks_series_author_and_genres(self):
        now = 1_000_000_000
        profile = self.rec.build_profile([
            {'author': 'Frank  Herbert', 'genres': ['Science-fiction'], 'series': 'Dune', 'series_index': '1',
             'status': 'finished', 'percentage': 1.0, 'updated_at': now},
            {'author': 'Old Author', 'genres': ['Policier'], 'status': None, 'percentage': 0.95,
             'updated_at': now - 3 * 365 * 86400},
        ], now)
        candidates = [
            {'path': 'genre', 'author': 'Dan Simmons', 'genres': ['Science-fiction']},
            {'path': 'author', 'author': 'frank herbert', 'genres': []},
            {'path': 'next', 'author': 'Frank Herbert', 'genres': ['Science-fiction'], 'series': 'dune', 'series_index': 2},
            {'path': 'later', 'author': 'Frank Herbert', 'genres': ['Science-fiction'], 'series': 'Dune', 'series_index': 4},
            {'path': 'old-genre', 'author': 'X', 'genres': ['Policier']},
            {'path': 'none', 'author': 'Y', 'genres': ['Cuisine']},
        ]
        ranked = self.rec.recommend(candidates, profile)
        self.assertEqual([book['path'] for book in ranked], ['next', 'later', 'author', 'genre', 'old-genre'])
        self.assertIn('Tome suivant de Dune', ranked[0]['reasons'])
        self.assertEqual(len(self.rec.recommend(candidates, profile, limit=2)), 2)

    def test_series_bonus_is_optional(self):
        profile = self.rec.build_profile(
            [{'author': 'A', 'genres': [], 'status': 'reading', 'percentage': 0.4, 'updated_at': 0}], 0)
        score, reasons = self.rec.score_candidate({'author': 'A', 'genres': []}, profile)
        self.assertGreater(score, 0)
        self.assertEqual(reasons, ['Même auteur : A'])

    def test_basic_credentials_parsing(self):
        auth = importlib.import_module('controllers.opds_auth')
        token = base64.b64encode('élise:p:ss wörd'.encode('utf-8')).decode()
        self.assertEqual(auth.parse_basic_credentials(f'Basic {token}'), ('élise', 'p:ss wörd'))
        self.assertIsNone(auth.parse_basic_credentials('Bearer abc'))
        self.assertIsNone(auth.parse_basic_credentials('Basic ###'))
        self.assertIsNone(auth.parse_basic_credentials(None))


def create_chapter_epub(path, title, author, chapters=2):
    """EPUB with a manifest, a spine and `chapters` XHTML chapters (OEBPS/ch<n>.xhtml)."""
    manifest = ''.join(f'<item id="c{n}" href="ch{n}.xhtml" media-type="application/xhtml+xml"/>' for n in range(1, chapters + 1))
    spine = ''.join(f'<itemref idref="c{n}"/>' for n in range(1, chapters + 1))
    with zipfile.ZipFile(path, 'w') as zf:
        zf.writestr('mimetype', 'application/epub+zip')
        zf.writestr('META-INF/container.xml', '<?xml version="1.0"?><container version="1.0" '
                    'xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles><rootfile '
                    'full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles></container>')
        zf.writestr('OEBPS/content.opf', f"""<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0"><metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
<dc:title>{title}</dc:title><dc:creator>{author}</dc:creator></metadata>
<manifest>{manifest}</manifest><spine>{spine}</spine></package>""")
        for n in range(1, chapters + 1):
            body = ''.join(f'<p>Chapitre {n}, paragraphe {p}.</p>' for p in range(1, 6))
            zf.writestr(f'OEBPS/ch{n}.xhtml', f'<?xml version="1.0" encoding="UTF-8"?><html xmlns="http://www.w3.org/1999/xhtml">'
                                              f'<head><title>{n}</title></head><body>{body}</body></html>', zipfile.ZIP_DEFLATED)


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
        for module in ('controllers.opds', 'controllers.sync_storage', 'controllers.koreader_sync',
                       'controllers.library_index', 'controllers.crosspoint_sync', 'controllers.opds_auth',
                       'controllers.web_reader'):
            importlib.reload(importlib.import_module(module))
        importlib.reload(importlib.import_module('routes'))
        cls.server = importlib.reload(importlib.import_module('server'))
        cls.koreader_sync = importlib.import_module('controllers.koreader_sync')
        cls.web_reader = importlib.import_module('controllers.web_reader')
        cls.library_index = importlib.import_module('controllers.library_index')
        sync_storage = importlib.import_module('controllers.sync_storage')
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
        importlib.reload(importlib.import_module('controllers.opds'))
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


class FakeSpeechServer(http.server.BaseHTTPRequestHandler):
    """OpenAI-compatible text-to-speech server of the audiobook tests: records the requests, answers WAV bytes,
    an error when the text says "erreur", and a voice listing."""

    requests: list = []

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        FakeSpeechServer.requests.append({'path': self.path, 'body': body, 'auth': self.headers.get('Authorization')})
        if 'erreur' in body['input']:
            self._answer(500, b'{"error": {"message": "GPU indisponible"}}', 'application/json')
        else:
            self._answer(200, b'RIFF-audio:' + body['input'].encode('utf-8'), 'audio/wav')

    def do_GET(self):
        FakeSpeechServer.requests.append({'path': self.path, 'auth': self.headers.get('Authorization')})
        self._answer(200, b'{"data": [{"id": "vivian"}, {"id": "ryan"}]}', 'application/json')

    def _answer(self, status, body, content_type):
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class TestAudiobookConfig(unittest.TestCase):
    """audiobook.toml: settings of the speech server, completed by AUDIOBOOK_CONFIG_PATH."""

    def setUp(self):
        self.config = importlib.import_module('controllers.audiobook_config')

    def test_default_file_keeps_the_audiobook_off(self):
        config = self.config.load_audiobook_config(self.config.DEFAULT_AUDIOBOOK_CONFIG_PATH)
        self.assertFalse(config.enabled)
        self.assertEqual(config.response_format, 'wav')

    def test_completing_file_reads_the_environment_and_wins(self):
        base = self.config.AudiobookConfig(voice='vivian', extra={'language': 'French'})
        with mock.patch.dict(os.environ, {'TEST_TTS_KEY': 'secret'}):
            config = self.config.AudiobookConfig.from_dict({
                'url': 'http://proxy:8000/', 'model': 'bigchuck/tts', 'api_key': '${TEST_TTS_KEY}', 'max_chars': 300,
                'extra': {'speed': 1.1},
            }, base)
        self.assertTrue(config.enabled)
        self.assertEqual((config.url, config.api_key, config.voice, config.max_chars), ('http://proxy:8000', 'secret', 'vivian', 300))
        self.assertEqual(config.extra, {'language': 'French', 'speed': 1.1})

    def test_invalid_values_are_refused(self):
        for data in ({'url': 'proxy:8000'}, {'max_chars': 10}, {'response_format': 'pcm'}, {'voices': 'vivian'},
                     {'timeout': True}, {'unknown': 1}):
            with self.subTest(data=data), self.assertRaises(ValueError):
                self.config.AudiobookConfig.from_dict(data)


class TestAudiobook(unittest.TestCase):
    """Audiobook of the web reader: voice and tone per book, synthesis relayed to the speech server."""

    password = 'ecoute'

    @classmethod
    def setUpClass(cls):
        cls._orig_env = {key: os.environ.get(key) for key in ('LIBRARY_DIR', 'OPDS_AUTH', 'OPDS_SESSION_SECRET', 'AUDIOBOOK_CONFIG_PATH')}
        cls.library_dir = tempfile.TemporaryDirectory()
        cls.db_dir = tempfile.TemporaryDirectory()
        os.environ['LIBRARY_DIR'] = cls.library_dir.name
        os.environ.pop('OPDS_AUTH', None)
        os.environ['OPDS_SESSION_SECRET'] = 'test-session-secret'
        cls.epub = os.path.join(cls.library_dir.name, 'Contes', 'conte.epub')
        os.makedirs(os.path.dirname(cls.epub))
        create_chapter_epub(cls.epub, 'Le Conte', 'Un Auteur')
        # Fake speech server, named by the completing settings file
        cls.speech = http.server.ThreadingHTTPServer(('localhost', 0), FakeSpeechServer)
        cls.speech_thread = threading.Thread(target=cls.speech.serve_forever, daemon=True)
        cls.speech_thread.start()
        config_path = os.path.join(cls.db_dir.name, 'audiobook.toml')
        with open(config_path, 'w', encoding='utf-8') as f:
            f.write(f'url = "http://localhost:{cls.speech.server_address[1]}"\nmodel = "bigchuck/tts"\napi_key = "jeton"\n'
                    'voice = "vivian"\ninstructions = "Ton posé"\nmax_chars = 100\n[extra]\nlanguage = "French"\n')
        os.environ['AUDIOBOOK_CONFIG_PATH'] = config_path
        for module in ('controllers.opds', 'controllers.sync_storage', 'controllers.koreader_sync',
                       'controllers.library_index', 'controllers.crosspoint_sync', 'controllers.opds_auth',
                       'controllers.audiobook_config', 'controllers.speech_client', 'controllers.audiobook'):
            importlib.reload(importlib.import_module(module))
        importlib.reload(importlib.import_module('routes'))
        cls.server = importlib.reload(importlib.import_module('server'))
        cls.koreader_sync = importlib.import_module('controllers.koreader_sync')
        cls.audiobook = importlib.import_module('controllers.audiobook')
        sync_storage = importlib.import_module('controllers.sync_storage')
        cls.koreader_sync.SyncControllerBase._sync_storage_instance = sync_storage.SyncStorage(
            os.path.join(cls.db_dir.name, 'sync.db'))
        cls.httpd = ThreadedTCPServer(('localhost', 0), cls.server.UnifiedHandler)
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()
        time.sleep(0.1)
        with contextlib.redirect_stdout(io.StringIO()):
            status, _, _ = http_request(cls.port, 'POST', '/users/create', {'username': 'auditeur', 'password': cls.password})
            assert status == 201, status

    @classmethod
    def tearDownClass(cls):
        for server, thread in ((cls.httpd, cls.thread), (cls.speech, cls.speech_thread)):
            server.shutdown()
            server.server_close()
            thread.join(timeout=1)
        cls.koreader_sync.SyncControllerBase._sync_storage_instance = None
        cls.library_dir.cleanup()
        cls.db_dir.cleanup()
        for key, value in cls._orig_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        importlib.reload(importlib.import_module('controllers.audiobook_config'))
        importlib.reload(importlib.import_module('controllers.speech_client'))
        importlib.reload(importlib.import_module('controllers.opds'))
        importlib.reload(importlib.import_module('server'))

    def setUp(self):
        FakeSpeechServer.requests = []

    def _request(self, method, path, body=None, headers=None):
        return http_request(self.port, method, path, body, headers)

    def _auth(self):
        return basic_header('auditeur', self.password)

    def test_voice_and_tone_are_kept_per_book(self):
        url = '/webui/audiobook?path=' + urllib.parse.quote('Contes/conte.epub')
        # An account is always required (every synthesis costs the speech server a generation)
        self.assertEqual(self._request('GET', url)[0], 401)
        status, _, body = self._request('GET', url, headers=self._auth())
        self.assertEqual(status, 200, body)
        self.assertEqual(json.loads(body), {'voice': '', 'instructions': '', 'max_chars': 100,
                                            'defaults': {'voice': 'vivian', 'instructions': 'Ton posé'}})
        settings = {'path': 'Contes/conte.epub', 'voice': ' ryan ', 'instructions': 'Voix grave, ton de conteur'}
        status, _, body = self._request('PUT', '/webui/audiobook', settings, self._auth())
        self.assertEqual(status, 200, body)
        self.assertEqual(json.loads(self._request('GET', url, headers=self._auth())[2])['voice'], 'ryan')
        self.assertEqual(json.loads(self._request('GET', url, headers=self._auth())[2])['instructions'], 'Voix grave, ton de conteur')
        # Invalid fields, books outside the library
        too_long = dict(settings, instructions='x' * 1001)
        self.assertEqual(self._request('PUT', '/webui/audiobook', too_long, self._auth())[0], 400)
        self.assertEqual(self._request('PUT', '/webui/audiobook', dict(settings, path='../sync.db'), self._auth())[0], 404)
        self.assertEqual(self._request('GET', '/webui/audiobook?path=absent.epub', headers=self._auth())[0], 404)

    def test_speech_is_relayed_to_the_speech_server(self):
        status, headers, body = self._request('POST', '/webui/audiobook/speech',
                                              {'text': 'Il était  une fois.', 'voice': '', 'instructions': 'Chuchoté'}, self._auth())
        self.assertEqual(status, 200, body)
        self.assertEqual((headers['Content-Type'], body), ('audio/wav', 'RIFF-audio:Il était une fois.'.encode('utf-8')))
        sent = FakeSpeechServer.requests[-1]
        self.assertEqual((sent['path'], sent['auth']), ('/v1/audio/speech', 'Bearer jeton'))
        self.assertEqual(sent['body'], {'language': 'French', 'model': 'bigchuck/tts', 'input': 'Il était une fois.',
                                        'response_format': 'wav', 'voice': 'vivian', 'instructions': 'Chuchoté'})
        # The server's error, a text too long or empty, no account
        status, _, body = self._request('POST', '/webui/audiobook/speech', {'text': 'Une erreur.'}, self._auth())
        self.assertEqual(status, 502)
        self.assertIn('GPU indisponible', json.loads(body)['message'])
        self.assertEqual(self._request('POST', '/webui/audiobook/speech', {'text': 'x' * 101}, self._auth())[0], 400)
        self.assertEqual(self._request('POST', '/webui/audiobook/speech', {'text': '  '}, self._auth())[0], 400)
        self.assertEqual(self._request('POST', '/webui/audiobook/speech', {'text': 'Bonjour.'})[0], 401)

    def test_voices_of_the_speech_server(self):
        status, _, body = self._request('GET', '/webui/audiobook/voices', headers=self._auth())
        self.assertEqual(status, 200, body)
        self.assertEqual(json.loads(body), {'voices': ['vivian', 'ryan']})
        self.assertEqual(FakeSpeechServer.requests[-1]['path'], '/v1/audio/voices?model=bigchuck%2Ftts')

    def test_unconfigured_audiobook_answers_404(self):
        config = importlib.import_module('controllers.audiobook_config')
        with mock.patch.object(self.audiobook, 'get_audiobook_config', return_value=config.AudiobookConfig()):
            self.assertEqual(self._request('GET', '/webui/audiobook?path=Contes/conte.epub', headers=self._auth())[0], 404)
            self.assertEqual(self._request('POST', '/webui/audiobook/speech', {'text': 'Bonjour.'}, self._auth())[0], 404)
        self.assertEqual(FakeSpeechServer.requests, [])

    def test_reader_page_loads_the_audiobook(self):
        page = self._request('GET', '/read?path=Contes/conte.epub')[2].decode('utf-8')
        self.assertIn('/static/reader/audiobook.js?v=', page)
        self.assertIn('class="reader-player"', page)
        self.assertEqual(self._request('GET', '/static/foliate/tts.js')[0], 200)


class TestLibrarySync(unittest.TestCase):
    """Library metadata stored in SQLite (controllers/library.py): one sync rule, restarts without reading EPUBs."""

    def setUp(self):
        self.library_dir = tempfile.TemporaryDirectory()
        self.db_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.library_dir.cleanup)
        self.addCleanup(self.db_dir.cleanup)
        self.library = importlib.import_module('controllers.library')
        self.opds = importlib.import_module('controllers.opds')
        self.db_path = os.path.join(self.db_dir.name, 'library.db')
        create_epub(self.path('a.epub'), 'Alpha', 'Author One', date='2020-01-01')
        create_epub(self.path('sub/b.epub'), 'Beta', 'Author Two')

    def path(self, name):
        path = os.path.join(self.library_dir.name, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        return path

    def open_library(self):
        return self.library.Library(self.library_dir.name, self.db_path)

    def test_sync_reads_only_new_modified_and_removed_books(self):
        library = self.open_library()
        snapshot = library.snapshot
        self.assertEqual(sorted(book.title for book in snapshot.books.values()), ['Alpha', 'Beta'])
        self.assertEqual(snapshot.books[self.path('a.epub')].info()['year'], '2020')
        # Nothing changed: no EPUB opened, same snapshot
        with mock.patch.object(self.opds.BookMetadata, 'extract_epub_info', side_effect=AssertionError('read again')):
            self.assertEqual(library.sync()['total'], 2)
        self.assertIs(library.snapshot, snapshot)
        # Added, modified (size or date) and removed books
        create_epub(self.path('c.epub'), 'Gamma', 'Author One')
        create_epub(self.path('a.epub'), 'Alpha 2', 'Author One')
        os.utime(self.path('a.epub'), (1_000_000_000, 1_000_000_000))
        os.remove(self.path('sub/b.epub'))
        result = library.sync()
        self.assertEqual((result['added'], result['updated'], result['removed'], result['total']), (1, 1, 1, 2))
        self.assertGreater(library.snapshot.version, snapshot.version)
        self.assertEqual(sorted(book.title for book in library.snapshot.books.values()), ['Alpha 2', 'Gamma'])
        self.assertEqual(library.snapshot.catalog.authors, {'Author One': [self.path('a.epub'), self.path('c.epub')]})

    def test_restart_reads_the_database_not_the_epubs(self):
        self.open_library().sync()
        with mock.patch.object(self.opds.BookMetadata, 'extract_epub_info', side_effect=AssertionError('read again')):
            library = self.open_library()
            self.assertEqual(sorted(book.title for book in library.snapshot.books.values()), ['Alpha', 'Beta'])
            self.assertEqual(library.sync()['added'], 0)
        # Another library in the same database: its books are read again
        other = tempfile.TemporaryDirectory()
        self.addCleanup(other.cleanup)
        create_epub(os.path.join(other.name, 'z.epub'), 'Zeta', 'Author Z')
        library = self.library.Library(other.name, self.db_path)
        self.assertEqual([book.title for book in library.snapshot.books.values()], ['Zeta'])


if __name__ == '__main__':
    unittest.main()
