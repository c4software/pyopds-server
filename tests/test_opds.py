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

def create_epub(path, title, author):
        container_xml = """<?xml version='1.0' encoding='UTF-8'?>
<container version='1.0' xmlns='urn:oasis:names:tc:opendocument:xmlns:container'>
    <rootfiles>
        <rootfile full-path='content.opf' media-type='application/oebps-package+xml'/>
    </rootfiles>
</container>
"""
        opf_template = """<?xml version='1.0' encoding='UTF-8'?>
<package xmlns='http://www.idpf.org/2007/opf' version='3.0'>
    <metadata xmlns:dc='http://purl.org/dc/elements/1.1/'>
        <dc:title>{title}</dc:title>
        <dc:creator>{author}</dc:creator>
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
        create_epub(cls.alpha_path, 'Alpha Title', 'Author One')
        subfolder_path = os.path.join(cls.library_dir.name, 'Subfolder')
        os.makedirs(subfolder_path, exist_ok=True)
        cls.beta_path = os.path.join(subfolder_path, 'beta.epub')
        create_epub(cls.beta_path, 'Beta Title', 'Author Two')
        now = time.time()
        os.utime(cls.alpha_path, (now - 200, now - 200))
        os.utime(cls.beta_path, (now - 50, now - 50))
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

    def _get(self, path):
        conn = http.client.HTTPConnection('localhost', self.port, timeout=5)
        conn.request('GET', path)
        response = conn.getresponse()
        body = response.read()
        headers = dict(response.getheaders())
        status = response.status
        conn.close()
        return status, headers, body

    def _parse_feed(self, body):
        xml_text = body.decode('utf-8')
        if xml_text.startswith('<?xml-stylesheet'):
            xml_text = xml_text.split('\n', 1)[1]
        return ET.fromstring(xml_text)

    def test_root_catalog_includes_sections_and_folder(self):
        status, headers, body = self._get('/opds')
        self.assertEqual(status, 200)
        self.assertEqual(headers.get('Content-Type'), 'application/xml;profile=opds-catalog;kind=navigation')
        ns = {'atom': 'http://www.w3.org/2005/Atom'}
        feed = self._parse_feed(body)
        entry_titles = {entry.find('atom:title', ns).text for entry in feed.findall('atom:entry', ns)}
        self.assertIn('All Books', entry_titles)
        self.assertIn('Recent Books', entry_titles)
        self.assertIn('Subfolder', entry_titles)

    def test_all_books_feed_paginates_and_lists_books(self):
        ns = {'atom': 'http://www.w3.org/2005/Atom'}
        status, headers, body = self._get('/opds/books?page=1')
        self.assertEqual(status, 200)
        self.assertEqual(headers.get('Content-Type'), 'application/xml;profile=opds-catalog;kind=acquisition')
        feed = self._parse_feed(body)
        entries = feed.findall('atom:entry', ns)
        self.assertEqual(len(entries), 1)
        first_entry = entries[0]
        self.assertEqual(first_entry.find('atom:title', ns).text, 'Alpha Title')
        link_hrefs = {link.get('href') for link in first_entry.findall('atom:link', ns)}
        self.assertIn('/download/alpha.epub', link_hrefs)
        pagination_links = {link.get('rel'): link.get('href') for link in feed.findall('atom:link', ns)}
        self.assertIn('next', pagination_links)
        self.assertTrue(pagination_links['next'].endswith('page=2'))
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
        self.assertEqual(headers.get('Content-Type'), 'application/xml;profile=opds-catalog;kind=acquisition')
        feed = self._parse_feed(body)
        entries = feed.findall('atom:entry', ns)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].find('atom:title', ns).text, 'Beta Title')
        link_hrefs = {link.get('href') for link in entries[0].findall('atom:link', ns)}
        self.assertIn('/download/Subfolder/beta.epub', link_hrefs)
        status_recent, headers_recent, body_recent = self._get('/opds/recent')
        self.assertEqual(status_recent, 200)
        self.assertEqual(headers_recent.get('Content-Type'), 'application/xml;profile=opds-catalog;kind=acquisition')
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

if __name__ == '__main__':
    unittest.main()
