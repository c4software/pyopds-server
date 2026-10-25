"""Text of a book read by the audiobook: its chapters (the content documents of the spine, titled by the
table of contents) and the requests of the speech server (a few paragraphs, up to `max_chars` each).

Pure functions on an EPUB file, with the standard library only; shared by the web reader's synthesis
(speech_text) and the audiobook export tool (python -m controllers.tools.audiobook_export).
"""
import posixpath
import re
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass
from urllib.parse import unquote

from controllers.catalog.opds import BookMetadata
from controllers.library.index import PlainTextParser, content_documents, plain_text

OPF_NS = {'opf': 'http://www.idpf.org/2007/opf'}
NCX_NS = {'ncx': 'http://www.daisy.org/z3986/2005/ncx/'}
XHTML_NS = {'x': 'http://www.w3.org/1999/xhtml', 'epub': 'http://www.idpf.org/2007/ops'}
HEADING_TAGS = ('h1', 'h2', 'h3')
TITLE_MAX = 120
# End of a sentence: punctuation, closing quotes or brackets, then spaces
SENTENCE_END_RE = re.compile(r'(?<=[.!?…;:»”")\]])\s+')


@dataclass(frozen=True)
class Chapter:
    """A chapter of a book: one content document of the spine with text.

    Attributes:
        title: Its title (table of contents, else its first heading, else "Chapitre <n>").
        text: Its paragraphs, one per line.
    """
    title: str
    text: str


class _ChapterParser(PlainTextParser):
    """Plain text of a content document, and the text of its first heading."""

    def __init__(self):
        super().__init__()
        self.heading: str | None = None
        self._heading_parts: list[str] | None = None

    def handle_starttag(self, tag, attrs):
        super().handle_starttag(tag, attrs)
        if tag in HEADING_TAGS and self.heading is None and self._heading_parts is None:
            self._heading_parts = []

    def handle_endtag(self, tag):
        super().handle_endtag(tag)
        if tag in HEADING_TAGS and self._heading_parts is not None:
            self.heading = ' '.join(''.join(self._heading_parts).split()) or None
            self._heading_parts = None

    def handle_data(self, data):
        super().handle_data(data)
        if self._heading_parts is not None and not self._skip_depth:
            self._heading_parts.append(data)


def speech_text(text: str) -> str:
    """Text sent to the speech server: spaces collapsed in each line, the line breaks between paragraphs kept
    (a pause for the voice), empty lines dropped."""
    return '\n'.join(line for line in (' '.join(part.split()) for part in text.splitlines()) if line)


def _pieces(paragraph: str, max_chars: int) -> list[str]:
    """A paragraph cut in pieces of `max_chars` at most: at the ends of sentences, else between words."""
    if len(paragraph) <= max_chars:
        return [paragraph]
    pieces: list[str] = []
    for sentence in SENTENCE_END_RE.split(paragraph):
        while len(sentence) > max_chars:
            cut = sentence.rfind(' ', 0, max_chars + 1)
            cut = cut if cut > 0 else max_chars
            pieces.append(sentence[:cut].strip())
            sentence = sentence[cut:].strip()
        if pieces and len(pieces[-1]) + 1 + len(sentence) <= max_chars:
            pieces[-1] += ' ' + sentence
        elif sentence:
            pieces.append(sentence)
    return pieces


def speech_chunks(text: str, max_chars: int) -> list[str]:
    """Requests of a text to the speech server: whole paragraphs grouped up to `max_chars` characters (line
    breaks kept between them), a longer paragraph cut at its sentences."""
    chunks: list[str] = []
    current = ''
    for paragraph in speech_text(text).split('\n'):
        for piece in _pieces(paragraph, max_chars):
            if current and len(current) + 1 + len(piece) <= max_chars:
                current += '\n' + piece
            else:
                if current:
                    chunks.append(current)
                current = piece
    if current:
        chunks.append(current)
    return chunks


def _document_path(folder: str, href: str) -> str:
    """Path in the archive of a link (`href` relative to `folder`), without its anchor."""
    return posixpath.normpath(posixpath.join(folder, unquote(href.split('#')[0])))


def _label(text: str) -> str:
    return ' '.join(text.split())[:TITLE_MAX]


def _nav_path(items: list[ET.Element], opf_dir: str) -> str | None:
    """Path of the EPUB 3 table of contents (manifest item with the "nav" property), None without one."""
    nav = next((item for item in items if 'nav' in (item.get('properties') or '').split() and item.get('href')), None)
    return _document_path(opf_dir, nav.get('href')) if nav is not None else None


def toc_titles(zf: zipfile.ZipFile, opf_root: ET.Element, opf_dir: str) -> dict[str, str]:
    """{content document path: title} of the table of contents of an EPUB (EPUB 3 nav, else EPUB 2 NCX); the
    first entry pointing into a document names it."""
    items = opf_root.findall('.//opf:manifest/opf:item', OPF_NS)
    titles: dict[str, str] = {}
    try:
        if nav := _nav_path(items, opf_dir):
            root = ET.fromstring(zf.read(nav))
            navs = list(root.iter('{%s}nav' % XHTML_NS['x']))
            tocs = [n for n in navs if n.get('{%s}type' % XHTML_NS['epub']) == 'toc'] or navs
            for link in tocs[0].iter('{%s}a' % XHTML_NS['x']) if tocs else ():
                if link.get('href') and (label := _label(''.join(link.itertext()))):
                    titles.setdefault(_document_path(posixpath.dirname(nav), link.get('href')), label)
        if not titles:
            spine = opf_root.find('.//opf:spine', OPF_NS)
            ncx_id = spine.get('toc') if spine is not None else None
            ncx = next((item for item in items if item.get('href') and (
                item.get('id') == ncx_id or item.get('media-type') == 'application/x-dtbncx+xml')), None)
            if ncx is not None:
                path = _document_path(opf_dir, ncx.get('href'))
                for point in ET.fromstring(zf.read(path)).iter('{%s}navPoint' % NCX_NS['ncx']):
                    text, content = point.find('ncx:navLabel/ncx:text', NCX_NS), point.find('ncx:content', NCX_NS)
                    if text is not None and content is not None and content.get('src') and (label := _label(''.join(text.itertext()))):
                        titles.setdefault(_document_path(posixpath.dirname(path), content.get('src')), label)
    except (KeyError, ET.ParseError):
        pass
    return titles


def book_chapters(path: str) -> list[Chapter]:
    """Chapters of an EPUB, in reading order: the content documents of the spine with text (the table of
    contents itself excepted).

    Raises:
        OSError, zipfile.BadZipFile: When the file cannot be read.
    """
    chapters = []
    with zipfile.ZipFile(path) as zf:
        opf_root, opf_dir = BookMetadata._parse_opf_from_epub(zf)
        titles, nav = {}, None
        if opf_root is not None:
            titles = toc_titles(zf, opf_root, opf_dir or '')
            nav = _nav_path(opf_root.findall('.//opf:manifest/opf:item', OPF_NS), opf_dir or '')
        for name in content_documents(zf):
            if name == nav:
                continue
            parser = _ChapterParser()
            parser.feed(zf.read(name).decode('utf-8', errors='replace'))
            parser.close()
            text = speech_text(plain_text(parser.parts))
            if text:
                title = titles.get(name) or (parser.heading or '')[:TITLE_MAX] or f'Chapitre {len(chapters) + 1}'
                chapters.append(Chapter(title, text))
    return chapters


__all__ = ['Chapter', 'book_chapters', 'speech_chunks', 'speech_text', 'toc_titles']
