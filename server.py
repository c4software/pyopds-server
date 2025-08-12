import os
import http.server
import socketserver
import datetime
import xml.etree.ElementTree as ET
import hashlib
from urllib.parse import unquote, quote
from ebooklib import epub

LIBRARY_DIR = os.environ.get('LIBRARY_DIR', 'books')
MAX_DEPTH = int(os.environ.get('MAX_DEPTH', '2'))  # Profondeur max par défaut : 2
PORT = 8080

def generate_opds_feed(title, feed_id, links, entries):
    feed = ET.Element('feed', {'xmlns': 'http://www.w3.org/2005/Atom', 
                               'xmlns:opds': 'http://opds-spec.org/2010/catalog'})
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
    return ET.tostring(feed, encoding='unicode', method='xml')

def extract_epub_metadata(epub_path):
    try:
        book = epub.read_epub(epub_path)
        title = book.get_metadata('DC', 'title')[0][0] if book.get_metadata('DC', 'title') else None
        author = book.get_metadata('DC', 'creator')[0][0] if book.get_metadata('DC', 'creator') else None
        return title, author
    except Exception:
        return None, None

def is_within_library_dir(file_path):
    """Vérifie que le chemin est contenu dans LIBRARY_DIR pour éviter le path traversal."""
    library_realpath = os.path.realpath(LIBRARY_DIR)
    file_realpath = os.path.realpath(file_path)
    return file_realpath.startswith(library_realpath + os.sep)

class OPDSHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/opds':
            links = [
                ('self', '/opds', 'application/atom+xml;profile=opds-catalog;kind=navigation'),
                ('start', '/opds', 'application/atom+xml;profile=opds-catalog;kind=navigation')
            ]
            entries = [
                {'title': 'Tous les livres', 'id': 'urn:all-books', 
                 'links': [('subsection', '/opds/books', 'application/atom+xml;profile=opds-catalog;kind=acquisition')]}
            ]
            xml = generate_opds_feed('Ma bibliothèque', 'urn:library-root', links, entries)
            self.send_response(200)
            self.send_header('Content-Type', 'application/atom+xml;profile=opds-catalog;kind=navigation')
            self.end_headers()
            self.wfile.write(xml.encode('utf-8'))
        
        elif self.path == '/opds/books':
            links = [
                ('self', '/opds/books', 'application/atom+xml;profile=opds-catalog;kind=acquisition'),
                ('start', '/opds', 'application/atom+xml;profile=opds-catalog;kind=navigation')
            ]
            # Collecter les fichiers avec leurs métadonnées et date de modification
            file_list = []
            for root, dirs, files in os.walk(LIBRARY_DIR):
                # Calculer la profondeur du dossier courant
                relative_path = os.path.relpath(root, LIBRARY_DIR)
                depth = len(relative_path.split(os.sep)) if relative_path != '.' else 0
                if depth > MAX_DEPTH:
                    continue  # Ignorer les dossiers trop profonds
                for file in files:
                    if file.endswith('.epub'):
                        path = os.path.join(root, file)
                        relative_path = os.path.relpath(path, LIBRARY_DIR)
                        title, author = extract_epub_metadata(path)
                        if title is None:
                            title = file
                        if author is None:
                            author = 'Inconnu'
                        mtime = os.path.getmtime(path)
                        file_list.append({
                            'path': path,
                            'relative_path': relative_path,
                            'title': title,
                            'author': author,
                            'mtime': mtime
                        })
            # Trier par date de modification (plus récent en premier)
            file_list.sort(key=lambda x: x['mtime'], reverse=True)
            # Générer les entrées OPDS
            entries = []
            for file_info in file_list:
                book_id = 'urn:book:' + hashlib.md5(file_info['relative_path'].encode()).hexdigest()
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
            xml = generate_opds_feed('Tous les livres', 'urn:all-books', links, entries)
            self.send_response(200)
            self.send_header('Content-Type', 'application/atom+xml;profile=opds-catalog;kind=acquisition')
            self.end_headers()
            self.wfile.write(xml.encode('utf-8'))
        
        elif self.path.startswith('/download/'):
            filename = unquote(self.path.split('/download/')[1])
            file_path = os.path.join(LIBRARY_DIR, filename)
            # Vérification contre le path traversal
            if not is_within_library_dir(file_path):
                self.send_error(403, 'Access denied: Path traversal detected')
                return
            if filename.endswith('.epub') and os.path.exists(file_path):
                self.send_response(200)
                self.send_header('Content-Type', 'application/epub+zip')
                self.send_header('Content-Disposition', f'attachment; filename="{os.path.basename(filename)}"')
                self.end_headers()
                with open(file_path, 'rb') as f:
                    self.wfile.write(f.read())
            else:
                self.send_error(404, 'File not found')
        
        else:
            self.send_error(404, 'Not found')

if __name__ == '__main__':
    if not os.path.exists(LIBRARY_DIR):
        os.makedirs(LIBRARY_DIR)
    with socketserver.TCPServer(("", PORT), OPDSHandler) as httpd:
        print(f"Serveur OPDS démarré sur le port {PORT}")
        httpd.serve_forever()
