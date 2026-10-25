"""OPDS catalog: feeds, navigation, search, covers, web view assets."""
import email.utils
import gzip
import http.client
import importlib
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import unittest
import xml.etree.ElementTree as ET
from unittest import mock
from support import ThreadedTCPServer, create_epub, create_rich_epub, http_request, PROJECT_ROOT  # noqa: F401  (first: test database, project on sys.path)


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
        importlib.reload(importlib.import_module('controllers.catalog.opds'))
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
        importlib.reload(importlib.import_module('controllers.catalog.opds'))
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
            self.assertRegex(script.get('src'), r'^/static/vendor/xslt-polyfill\.min\.js\?v=\d+$')
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
        names = sorted(name for name in os.listdir(catalog_dir) if name.endswith(('.js', '.css')))
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
                     '/static/../controllers/catalog/webui.py', '/static/catalog/main..js'):
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
        for module in ('controllers.catalog.opds', 'controllers.library.index'):
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
        importlib.reload(importlib.import_module('controllers.catalog.opds'))
        importlib.reload(importlib.import_module('controllers.library.index'))
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
        library_index = importlib.import_module('controllers.library.index')
        library = library_index.LibraryIndex.get_instance()
        path = os.path.join(self.library_dir.name, 'a-fondation-2.epub')
        meta = library.book_meta(path)
        self.assertEqual((meta['year'], meta['rating'], meta['series_index']), (1952, 4.5, 2))
        opds = importlib.import_module('controllers.catalog.opds')
        with mock.patch.object(opds.BookMetadata, 'extract_epub_info', side_effect=AssertionError('read again')):
            self.assertEqual(library.book_meta(path), meta)
            # details() reuses it: only the cover and the page estimate are read
            self.assertEqual(library.details(path)['description'], meta['description'])
        self.assertEqual([os.path.basename(p) for _, p in library.series_books('LA FONDATION')],
                         ['c-fondation-1.epub', 'b-fondation-1-5.epub', 'a-fondation-2.epub'])

    def test_xslt_is_valid_xml_without_inline_code(self):
        tree = ET.parse(os.path.join(PROJECT_ROOT, 'static', 'catalog', 'opds_to_html.xslt'))
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
