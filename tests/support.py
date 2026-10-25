"""Shared test setup and helpers, imported first by the test modules: the project root on sys.path, the
library metadata of the test libraries in a temporary database (controllers/library/store.py, never next
to a real sync database), test EPUBs, the threaded test server and HTTP helpers."""
import base64
import http.client
import json
import os
import socketserver
import sys
import tempfile
import zipfile

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)
os.environ.setdefault('LIBRARY_DB_PATH', os.path.join(tempfile.mkdtemp(prefix='pyopds-tests-'), 'library.db'))


class ThreadedTCPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True


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


def create_chapter_epub(path, title, author, chapters=2, toc=None, bodies=None):
    """EPUB with a manifest, a spine and `chapters` XHTML chapters (OEBPS/ch<n>.xhtml); `toc` ({href: label})
    adds an EPUB 3 table of contents (OEBPS/nav.xhtml, first in the spine), `bodies` ({n: body HTML})
    replaces the five paragraphs of chapters."""
    manifest = ''.join(f'<item id="c{n}" href="ch{n}.xhtml" media-type="application/xhtml+xml"/>' for n in range(1, chapters + 1))
    spine = ''.join(f'<itemref idref="c{n}"/>' for n in range(1, chapters + 1))
    if toc:
        manifest = '<item id="nav" href="nav.xhtml" properties="nav" media-type="application/xhtml+xml"/>' + manifest
        spine = '<itemref idref="nav"/>' + spine
    with zipfile.ZipFile(path, 'w') as zf:
        zf.writestr('mimetype', 'application/epub+zip')
        zf.writestr('META-INF/container.xml', '<?xml version="1.0"?><container version="1.0" '
                    'xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles><rootfile '
                    'full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles></container>')
        zf.writestr('OEBPS/content.opf', f"""<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0"><metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
<dc:title>{title}</dc:title><dc:creator>{author}</dc:creator></metadata>
<manifest>{manifest}</manifest><spine>{spine}</spine></package>""")
        if toc:
            links = ''.join(f'<li><a href="{href}">{label}</a></li>' for href, label in toc.items())
            zf.writestr('OEBPS/nav.xhtml', '<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">'
                                           f'<body><nav epub:type="toc"><ol>{links}</ol></nav></body></html>')
        for n in range(1, chapters + 1):
            body = (bodies or {}).get(n) or ''.join(f'<p>Chapitre {n}, paragraphe {p}.</p>' for p in range(1, 6))
            zf.writestr(f'OEBPS/ch{n}.xhtml', f'<?xml version="1.0" encoding="UTF-8"?><html xmlns="http://www.w3.org/1999/xhtml">'
                                              f'<head><title>{n}</title></head><body>{body}</body></html>', zipfile.ZIP_DEFLATED)
