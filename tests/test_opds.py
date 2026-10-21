import base64
import contextlib
import hashlib
import http.client
import importlib
import io
import json
import os
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

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

def create_epub(path, title, author, date=None, subjects=()):
        container_xml = """<?xml version='1.0' encoding='UTF-8'?>
<container version='1.0' xmlns='urn:oasis:names:tc:opendocument:xmlns:container'>
    <rootfiles>
        <rootfile full-path='content.opf' media-type='application/oebps-package+xml'/>
    </rootfiles>
</container>
"""
        date_element = f"<dc:date>{date}</dc:date>" if date else ""
        date_element += ''.join(f"<dc:subject>{subject}</dc:subject>" for subject in subjects)
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
            self.assertIn(b'<?xml-stylesheet type="text/xsl" href="/opds_to_html.xslt"?>', body)

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
            self.assertIs(library.book_meta(path), meta)
            # details() reuses it: only the cover and the page estimate are read
            self.assertEqual(library.details(path)['description'], meta['description'])
        self.assertEqual([os.path.basename(p) for _, p in library.series_books('LA FONDATION')],
                         ['c-fondation-1.epub', 'b-fondation-1-5.epub', 'a-fondation-2.epub'])

    def test_xslt_is_valid_xml(self):
        tree = ET.parse(os.path.join(PROJECT_ROOT, 'static', 'opds_to_html.xslt'))
        node = shutil.which('node')
        if node is None:
            self.skipTest('node is not installed: inline scripts not checked')
        scripts = [element.text for element in tree.iter('script') if element.text and element.text.strip()]
        with tempfile.TemporaryDirectory() as tmp:
            for number, script in enumerate(scripts):
                path = os.path.join(tmp, f'script{number}.js')
                with open(path, 'w', encoding='utf-8') as f:
                    f.write(script)
                result = subprocess.run([node, '--check', path], capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)


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
        for module in ('controllers.opds', 'controllers.sync_storage', 'controllers.koreader_sync',
                       'controllers.library_index', 'controllers.recommendations', 'controllers.crosspoint_sync',
                       'controllers.opds_auth'):
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
            for user in ('reader', 'newbie', 'other'):
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
                         '/download/dune.epub', '/reading', '/opds/refresh', '/opds/recommendations'):
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
            conn.request('POST', '/login', body=json.dumps({'username': 'reader', 'password': self.password}),
                         headers={'Content-Type': 'application/json'})
            response = conn.getresponse()
            response.read()
            self.assertEqual(response.status, 204)
            set_cookies = response.headers.get_all('Set-Cookie')
            conn.close()
            session = next(c for c in set_cookies if c.startswith('pyopds_session='))
            self.assertIn('HttpOnly', session)
            self.assertIn('pyopds_user=reader', ' '.join(set_cookies))
            cookie = {'Cookie': session.split(';')[0]}
            status, _, body = self._request('GET', '/opds', headers={**browser, **cookie})
            self.assertEqual(status, 200)
            self.assertIn(b'urn:recommendations', body)
            # Tampered session: back to the sign-in page
            tampered = {'Cookie': session.split(';')[0].replace('pyopds_session=', 'pyopds_session=x')}
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


if __name__ == '__main__':
    unittest.main()
