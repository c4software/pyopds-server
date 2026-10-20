import http.client
import importlib
import os
import socketserver
import sys
import tempfile
import threading
import time
import unittest
import xml.etree.ElementTree as ET
import zipfile

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

def create_epub(path, title, author, date=None):
        container_xml = """<?xml version='1.0' encoding='UTF-8'?>
<container version='1.0' xmlns='urn:oasis:names:tc:opendocument:xmlns:container'>
    <rootfiles>
        <rootfile full-path='content.opf' media-type='application/oebps-package+xml'/>
    </rootfiles>
</container>
"""
        date_element = f"<dc:date>{date}</dc:date>" if date else ""
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


if __name__ == '__main__':
    unittest.main()
