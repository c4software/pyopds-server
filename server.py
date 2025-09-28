import os
import http.server
import socketserver
import datetime
import xml.etree.ElementTree as ET
import hashlib
from urllib.parse import unquote, quote
from ebooklib import epub

LIBRARY_DIR = os.environ.get('LIBRARY_DIR', 'books')
MAX_DEPTH = int(os.environ.get('MAX_DEPTH', '2'))
PORT = 8080


class BookMetadata:
    @staticmethod
    def extract_epub_metadata(epub_path):
        try:
            book = epub.read_epub(epub_path)
            title = book.get_metadata('DC', 'title')[0][0] if book.get_metadata('DC', 'title') else None
            author = book.get_metadata('DC', 'creator')[0][0] if book.get_metadata('DC', 'creator') else None
            return title, author
        except Exception:
            return None, None


class SecurityUtils:
    @staticmethod
    def is_within_library_dir(file_path):
        library_realpath = os.path.realpath(LIBRARY_DIR)
        file_realpath = os.path.realpath(file_path)
        return file_realpath.startswith(library_realpath + os.sep)
    
    @staticmethod
    def has_path_traversal(path):
        """Check if path contains dangerous traversal sequences"""
        dangerous_patterns = ['..', '~']
        
        # Check for dangerous patterns in the path
        for pattern in dangerous_patterns:
            if pattern in path:
                return True
        
        # Check each path component
        path_components = path.replace('\\', '/').split('/')
        for component in path_components:
            if component in dangerous_patterns or component.startswith('.'):
                return True
                
        return False


class OPDSFeedGenerator:
    @staticmethod
    def generate_feed(title, feed_id, links, entries):
        feed = ET.Element('feed', {
            'xmlns': 'http://www.w3.org/2005/Atom',
            'xmlns:opds': 'http://opds-spec.org/2010/catalog'
        })
        
        ET.SubElement(feed, 'title').text = title
        ET.SubElement(feed, 'id').text = feed_id
        ET.SubElement(feed, 'updated').text = datetime.datetime.utcnow().isoformat() + 'Z'
        
        for rel, href, type_ in links:
            ET.SubElement(feed, 'link', {'rel': rel, 'href': href, 'type': type_})
            
        for entry_data in entries:
            entry = ET.SubElement(feed, 'entry')
            ET.SubElement(entry, 'title').text = entry_data['title']
            ET.SubElement(entry, 'id').text = entry_data['id']
            
            if 'author' in entry_data:
                author = ET.SubElement(entry, 'author')
                ET.SubElement(author, 'name').text = entry_data['author']
                
            for rel, href, type_ in entry_data['links']:
                ET.SubElement(entry, 'link', {'rel': rel, 'href': href, 'type': type_})
                
        xml_string = ET.tostring(feed, encoding='unicode', method='xml')
        # Add the processing instruction for client-side XSLT
        processing_instruction = '<?xml-stylesheet type="text/xsl" href="/opds_to_html.xslt"?>\n'
        return processing_instruction + xml_string


class BookScanner:
    def __init__(self):
        self.metadata_extractor = BookMetadata()
    
    def scan_directory(self, directory_path, base_path=None, respect_depth_limit=True):
        if base_path is None:
            base_path = directory_path
            
        file_list = []
        
        for root, dirs, files in os.walk(directory_path):
            relative_path = os.path.relpath(root, base_path)
            depth = len(relative_path.split(os.sep)) if relative_path != '.' else 0
            
            if respect_depth_limit and depth > MAX_DEPTH:
                continue
                
            for file in files:
                if file.endswith('.epub'):
                    file_info = self._create_file_info(root, file, base_path)
                    if file_info:
                        file_list.append(file_info)
                        
        return sorted(file_list, key=lambda x: x['mtime'], reverse=True)
    
    def scan_recent_books(self, directory_path, limit=10):
        file_list = self.scan_directory(directory_path, respect_depth_limit=True)
        return file_list[:limit]
    
    def _create_file_info(self, root, filename, base_path):
        path = os.path.join(root, filename)
        relative_path = os.path.relpath(path, base_path)
        
        title, author = self.metadata_extractor.extract_epub_metadata(path)
        title = title or filename
        author = author or 'Unknown'
        
        return {
            'path': path,
            'relative_path': relative_path,
            'title': title,
            'author': author,
            'mtime': os.path.getmtime(path)
        }


class OPDSHandler(http.server.BaseHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        self.feed_generator = OPDSFeedGenerator()
        self.book_scanner = BookScanner()
        self.security = SecurityUtils()
        super().__init__(*args, **kwargs)
    
    def do_GET(self):
        if self.path == '/opds':
            self._handle_root_catalog()
        elif self.path == '/opds/books':
            self._handle_all_books()
        elif self.path == '/opds/recent':
            self._handle_recent_books()
        elif self.path.startswith('/opds/folder/'):
            self._handle_folder_catalog()
        elif self.path.startswith('/download/'):
            self._handle_download()
        elif self.path == '/opds_to_html.xslt':
            self._serve_xslt()
        else:
            self._send_error(404, 'Not found')
    
    def _handle_root_catalog(self):
        links = [
            ('self', '/opds', 'application/atom+xml;profile=opds-catalog;kind=navigation'),
            ('start', '/opds', 'application/atom+xml;profile=opds-catalog;kind=navigation')
        ]
        
        entries = self._get_root_entries()
        xml = self.feed_generator.generate_feed('My Library', 'urn:library-root', links, entries)
        
        self._send_xml_response(xml, 'navigation')
    
    def _get_root_entries(self):
        entries = [
            {
                'title': 'All Books',
                'id': 'urn:all-books',
                'links': [('subsection', '/opds/books', 'application/atom+xml;profile=opds-catalog;kind=acquisition')]
            },
            {
                'title': 'Recent Books',
                'id': 'urn:recent-books',
                'links': [('subsection', '/opds/recent', 'application/atom+xml;profile=opds-catalog;kind=acquisition')]
            }
        ]
        
        for folder in sorted(os.listdir(LIBRARY_DIR)):
            folder_path = os.path.join(LIBRARY_DIR, folder)
            if os.path.isdir(folder_path):
                folder_id = f'urn:folder:{hashlib.md5(folder.encode()).hexdigest()}'
                encoded_folder = quote(folder)
                entries.append({
                    'title': folder,
                    'id': folder_id,
                    'links': [('subsection', f'/opds/folder/{encoded_folder}', 'application/atom+xml;profile=opds-catalog;kind=acquisition')]
                })
                
        return entries
    
    def _handle_all_books(self):
        links = [
            ('self', '/opds/books', 'application/atom+xml;profile=opds-catalog;kind=acquisition'),
            ('start', '/opds', 'application/atom+xml;profile=opds-catalog;kind=navigation')
        ]
        
        file_list = self.book_scanner.scan_directory(LIBRARY_DIR)
        entries = self._create_book_entries(file_list)
        xml = self.feed_generator.generate_feed('All Books', 'urn:all-books', links, entries)
        
        self._send_xml_response(xml, 'acquisition')
    
    def _handle_recent_books(self):
        links = [
            ('self', '/opds/recent', 'application/atom+xml;profile=opds-catalog;kind=acquisition'),
            ('start', '/opds', 'application/atom+xml;profile=opds-catalog;kind=navigation')
        ]
        
        file_list = self.book_scanner.scan_recent_books(LIBRARY_DIR, limit=10)
        entries = self._create_book_entries(file_list)
        xml = self.feed_generator.generate_feed('Recent Books', 'urn:recent-books', links, entries)
        
        self._send_xml_response(xml, 'acquisition')
    
    def _handle_folder_catalog(self):
        folder_path = unquote(self.path[len('/opds/folder/'):])
        folder_full_path = os.path.join(LIBRARY_DIR, folder_path)
        
        if not self._validate_folder_access(folder_full_path):
            return
            
        links = [
            ('self', f'/opds/folder/{quote(folder_path)}', 'application/atom+xml;profile=opds-catalog;kind=acquisition'),
            ('start', '/opds', 'application/atom+xml;profile=opds-catalog;kind=navigation')
        ]
        
        entries = self._get_folder_entries(folder_path, folder_full_path)
        feed_id = f'urn:folder:{hashlib.md5(folder_path.encode()).hexdigest()}'
        title = os.path.basename(folder_path) or 'Library'
        xml = self.feed_generator.generate_feed(title, feed_id, links, entries)
        
        self._send_xml_response(xml, 'acquisition')
    
    def _validate_folder_access(self, folder_full_path):
        if not self.security.is_within_library_dir(folder_full_path) or not os.path.isdir(folder_full_path):
            self._send_error(404, 'Folder not found')
            return False
        return True
    
    def _get_folder_entries(self, folder_path, folder_full_path):
        entries = []
        
        subfolders = self._get_subfolders(folder_full_path)
        entries.extend(self._create_subfolder_entries(folder_path, subfolders))
        
        file_list = self.book_scanner.scan_directory(folder_full_path, LIBRARY_DIR)
        entries.extend(self._create_book_entries(file_list))
        
        return entries
    
    def _get_subfolders(self, folder_full_path):
        subfolders = []
        for item in os.listdir(folder_full_path):
            item_path = os.path.join(folder_full_path, item)
            if os.path.isdir(item_path):
                subfolders.append(item)
        return sorted(subfolders)
    
    def _create_subfolder_entries(self, parent_folder_path, subfolders):
        entries = []
        for subfolder in subfolders:
            subfolder_relative = os.path.join(parent_folder_path, subfolder)
            subfolder_id = f'urn:folder:{hashlib.md5(subfolder_relative.encode()).hexdigest()}'
            encoded_subfolder = quote(subfolder_relative.replace(os.sep, '/'))
            
            entries.append({
                'title': subfolder,
                'id': subfolder_id,
                'links': [('subsection', f'/opds/folder/{encoded_subfolder}', 'application/atom+xml;profile=opds-catalog;kind=acquisition')]
            })
            
        return entries
    
    def _create_book_entries(self, file_list):
        entries = []
        for file_info in file_list:
            book_id = f'urn:book:{hashlib.md5(file_info["relative_path"].encode()).hexdigest()}'
            encoded_path = quote(file_info['relative_path'].replace(os.sep, '/'))
            
            entry_links = [
                ('http://opds-spec.org/acquisition/open-access', f'/download/{encoded_path}', 'application/epub+zip')
            ]
            
            entries.append({
                'title': file_info['title'],
                'id': book_id,
                'links': entry_links,
                'author': file_info['author']
            })
            
        return entries
    
    def _handle_download(self):
        filename = unquote(self.path.split('/download/')[1])
        
        # Check for path traversal attempts
        if self.security.has_path_traversal(filename):
            self._send_error(403, 'Access denied: Invalid path')
            return
        
        file_path = os.path.join(LIBRARY_DIR, filename)
        
        if not self.security.is_within_library_dir(file_path):
            self._send_error(403, 'Access denied: Path traversal detected')
            return
            
        if self._is_valid_epub_file(filename, file_path):
            self._serve_file(file_path, filename)
        else:
            self._send_error(404, 'File not found')
    
    def _is_valid_epub_file(self, filename, file_path):
        return filename.endswith('.epub') and os.path.exists(file_path)
    
    def _serve_file(self, file_path, filename):
        self.send_response(200)
        self.send_header('Content-Type', 'application/epub+zip')
        self.send_header('Content-Disposition', f'attachment; filename="{os.path.basename(filename)}"')
        self.end_headers()
        
        with open(file_path, 'rb') as f:
            self.wfile.write(f.read())
    
    def _serve_xslt(self):
        try:
            xslt_path = 'opds_to_html.xslt'
            if not os.path.exists(xslt_path):
                self._send_error(404, "XSLT file not found")
                return

            self.send_response(200)
            self.send_header('Content-Type', 'application/xml')
            self.end_headers()
            with open(xslt_path, 'rb') as f:
                self.wfile.write(f.read())
        except Exception as e:
            self._send_error(500, f"Error serving XSLT file: {e}")

    def _send_xml_response(self, xml, catalog_kind):
        self.send_response(200)
        self.send_header('Content-Type', f'application/atom+xml;profile=opds-catalog;kind={catalog_kind}')
        self.send_header('Content-Type', 'application/xml')
        self.end_headers()
        self.wfile.write(xml.encode('utf-8'))

    def _send_error(self, code, message):
        self.send_response(code)
        self.send_header('Content-Type', 'application/xml')
        self.end_headers()
        error_xml = f'<?xml version="1.0" encoding="UTF-8"?><error><code>{code}</code><message>{message}</message></error>'
        self.wfile.write(error_xml.encode('utf-8'))


def main():
    if not os.path.exists(LIBRARY_DIR):
        os.makedirs(LIBRARY_DIR)
        
    with socketserver.TCPServer(("", PORT), OPDSHandler) as httpd:
        print(f"OPDS server started on port {PORT}")
        httpd.serve_forever()


if __name__ == '__main__':
    main()

