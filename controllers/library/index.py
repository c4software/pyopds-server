"""Lookups of library EPUBs (KOReader document hashes, titles, series) and EPUB helpers (document hashes,\ndescriptions, page estimates); the books come from the library snapshot (controllers.library.store)."""
import hashlib
import os
import posixpath
import re
import zipfile
from html.parser import HTMLParser
from urllib.parse import unquote

from controllers.core.background import shared_instance
from controllers.library.genres import genre_key
from controllers.catalog.opds import LIBRARY_DIR, BookMetadata, SecurityUtils, library_relative_path

CHUNK_SIZE = 1024
# Characters of text per printed page (~300 words, a typical paperback novel page)
CHARS_PER_PAGE = 1800
HTML_EXTENSIONS = ('.xhtml', '.html', '.htm')
DESCRIPTION_MAX_LENGTH = 6000
# Elements starting a new line (br) or a new paragraph (the others) in a description
BLOCK_TAGS = frozenset(('p', 'div', 'li', 'ul', 'ol', 'blockquote', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
                        'tr', 'table', 'section', 'article', 'pre', 'dd', 'dt'))


def koreader_partial_md5(path: str) -> str | None:
    """KOReader's binary document id: MD5 of 1 KiB samples at offsets 1024 << 2i, i = -1..10."""
    md5 = hashlib.md5()
    try:
        size = os.path.getsize(path)
        with open(path, 'rb') as f:
            for i in range(-1, 11):
                offset = CHUNK_SIZE >> 2 if i < 0 else CHUNK_SIZE << (2 * i)
                if offset >= size:
                    break
                f.seek(offset)
                md5.update(f.read(CHUNK_SIZE))
    except OSError:
        return None
    return md5.hexdigest()


def document_hashes(path: str) -> list[str]:
    """KOReader document ids of an EPUB: binary (partial MD5) first, then file name MD5."""
    return [h for h in (koreader_partial_md5(path), filename_md5(path)) if h]


def filename_md5(path: str) -> str:
    """KOReader's filename document id: MD5 of the file name."""
    return hashlib.md5(os.path.basename(path).encode('utf-8')).hexdigest()


class _TextLengthParser(HTMLParser):
    """Count the visible text characters of an (X)HTML document, whitespace collapsed."""

    def __init__(self):
        super().__init__()
        self.length = 0
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style', 'head'):
            self._skip_depth += 1

    def handle_endtag(self, tag):
        if tag in ('script', 'style', 'head') and self._skip_depth:
            self._skip_depth -= 1

    def handle_data(self, data):
        if not self._skip_depth:
            self.length += len(' '.join(data.split()))


class PlainTextParser(HTMLParser):
    """Plain text of an HTML document or fragment: paragraphs separated by a blank line, <br> kept as a line
    break, <head>, scripts and styles skipped."""

    def __init__(self):
        super().__init__()
        self.parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style', 'head'):
            self._skip_depth += 1
        elif tag == 'br':
            self.parts.append('\n')
        elif tag in BLOCK_TAGS:
            self.parts.append('\n\n')
            if tag == 'li':
                self.parts.append('• ')

    def handle_startendtag(self, tag, attrs):
        if tag == 'br':
            self.parts.append('\n')

    def handle_endtag(self, tag):
        if tag in ('script', 'style', 'head'):
            self._skip_depth = max(0, self._skip_depth - 1)
        elif tag in BLOCK_TAGS:
            self.parts.append('\n\n')

    def handle_data(self, data):
        if not self._skip_depth:
            self.parts.append(re.sub(r'\s+', ' ', data))


def plain_text(parts: list[str]) -> str:
    """Text of the parts of a PlainTextParser (or of a plain string): spaces collapsed in each line, at most
    one blank line between paragraphs."""
    lines = [' '.join(line.split()) for line in ''.join(parts).split('\n')]
    return re.sub(r'\n{3,}', '\n\n', '\n'.join(lines)).strip()


def description_text(value: str | None, max_length: int = DESCRIPTION_MAX_LENGTH) -> str | None:
    """Plain text of an EPUB description (often HTML), line breaks kept, cut at `max_length` characters."""
    if not value or not value.strip():
        return None
    if '<' in value:
        parser = PlainTextParser()
        parser.feed(value)
        parser.close()
        text = plain_text(parser.parts)
    else:
        text = plain_text([value])
    if len(text) > max_length:
        cut = text[:max_length]
        text = (cut[:cut.rfind(' ')] if ' ' in cut[max_length // 2:] else cut).rstrip() + '…'
    return text or None


def series_key(series: str | None) -> str:
    """Comparison key of a series name: accents, case and punctuation ignored (casefolded when non-Latin)."""
    if not series:
        return ''
    return genre_key(series) or ' '.join(series.casefold().split())


def release_year(date: str | None) -> int | None:
    """Year of a dc:date value, None when missing or Calibre's "undefined" date (year 101)."""
    year = (date or '').strip()[:4]
    return int(year) if len(year) == 4 and year.isdigit() and int(year) not in (0, 101) else None


def content_documents(zf: zipfile.ZipFile) -> list[str]:
    """Reading-order (spine) content documents of an EPUB, or every (X)HTML file without a spine."""
    names = set(zf.namelist())
    opf_root, opf_dir = BookMetadata._parse_opf_from_epub(zf)
    if opf_root is not None:
        ns = {'opf': 'http://www.idpf.org/2007/opf'}
        hrefs = {item.get('id'): item.get('href') for item in opf_root.findall('.//opf:manifest/opf:item', ns)}
        documents = []
        for itemref in opf_root.findall('.//opf:spine/opf:itemref', ns):
            href = hrefs.get(itemref.get('idref'))
            if href:
                path = posixpath.normpath(posixpath.join(opf_dir or '', unquote(href)))
                if path in names:
                    documents.append(path)
        if documents:
            return documents
    return sorted(name for name in names if name.lower().endswith(HTML_EXTENSIONS))


def estimate_print_pages(path: str) -> int | None:
    """Estimate the printed page count of an EPUB from the length of its text."""
    try:
        with zipfile.ZipFile(path) as zf:
            length = 0
            for name in content_documents(zf):
                parser = _TextLengthParser()
                parser.feed(zf.read(name).decode('utf-8', errors='replace'))
                parser.close()
                length += parser.length
    except (OSError, zipfile.BadZipFile, KeyError, ValueError):
        return None
    return max(1, round(length / CHARS_PER_PAGE)) if length else None


class LibraryIndex:
    """Lookups of library EPUBs (by KOReader document hash, title, series) and their metadata, all read from
    the library snapshot (controllers.library.store: metadata stored in library.db)."""

    _instance = None

    @classmethod
    def get_instance(cls) -> 'LibraryIndex':
        """Get or create the singleton index."""
        return shared_instance(cls)

    @staticmethod
    def _library():
        from controllers.library.store import Library
        return Library.get_instance()

    @classmethod
    def _present(cls, path: str) -> bool:
        """Whether a book of the snapshot is still on disk (a sync is started otherwise)."""
        if os.path.exists(path):
            return True
        cls._library().sync_later()
        return False

    @staticmethod
    def resolve_relative(relative_path: str) -> str | None:
        """Absolute path of a library EPUB given relative to LIBRARY_DIR, or None if invalid or missing."""
        if not relative_path.lower().endswith('.epub') or SecurityUtils.has_path_traversal(relative_path):
            return None
        path = os.path.join(LIBRARY_DIR, relative_path)
        if not SecurityUtils.is_within_library_dir(path) or not os.path.isfile(path):
            return None
        return path

    def find(self, documents: list[str], filename: str | None = None) -> str | None:
        """Return the EPUB path matching one of the hashes, or the device file name."""
        by_key = self._library().snapshot.by_key
        for key in [*documents, filename]:
            if key and key in by_key and self._present(by_key[key]):
                return by_key[key]
        return None

    def locate(self, documents: list[str], filename: str | None = None, library_path: str | None = None) -> str | None:
        """EPUB of a synced book: associated by hand (library_path relative to LIBRARY_DIR), else matching
        one of its document hashes or its device file name; None when none matches."""
        return (library_path and self.resolve_relative(library_path)) or self.find(documents, filename)

    def book_meta(self, path: str) -> dict:
        """Light metadata of a library EPUB (no cover, no page estimate): title, author, genres, series,
        series_index, description (plain text), year and rating (0-5, None when not rated). From the
        database; a file not synced yet is read (and a sync started). Raises OSError when it is missing."""
        from controllers.library.store import Book, read_metadata
        if not self._present(path):
            raise FileNotFoundError(path)
        library = self._library()
        book = library.book(path)
        if book is None:
            library.sync_later()
            book = Book(path, library_relative_path(path), 0, 0, None, read_metadata(path))
        return book.book_meta()

    def details(self, path: str) -> dict:
        """`book_meta` plus cover presence and estimated print pages (read once, then stored)."""
        return {**self.book_meta(path), **self._library().details(path), 'relative_path': library_relative_path(path)}

    @staticmethod
    def _series_order(book: tuple[int | float | None, str]) -> tuple:
        """Sort key of a (position, path) series book: by position (unnumbered last), then file name."""
        return (book[0] is None, book[0] or 0, os.path.basename(book[1]).lower())

    def series_books(self, series: str) -> list[tuple[int | float | None, str]]:
        """(position, path) of the library books of `series` (names compared normalized), sorted by
        position then file name; books without a position come last."""
        key = series_key(series)
        if not key:
            return []
        books = [book for book in self._library().snapshot.series.get(key, []) if self._present(book[1])]
        return sorted(books, key=self._series_order)

    def find_by_title(self, title: str, author: str | None = None) -> str | None:
        """Library EPUB whose title matches `title` (accents, case and punctuation ignored); among several,
        the one sharing the most author name words with `author`. None when no title matches."""
        key = series_key(title)
        if not key:
            return None
        snapshot = self._library().snapshot
        paths = [path for path in snapshot.titles.get(key, []) if self._present(path)]
        if len(paths) <= 1:
            return paths[0] if paths else None
        wanted = set(series_key(author).split())

        def shared(path: str) -> int:
            return len(wanted & set(series_key(snapshot.books[path].meta.get('author')).split()))

        return max(paths, key=shared)

    def next_in_series(self, series: str, position: int | float, exclude: str | None = None) -> tuple[int | float, str] | None:
        """(position, path) of the library book coming after `position` in `series` (names compared
        normalized): the next whole-numbered one (skipping novellas like 1.5), else whatever comes next."""
        key = series_key(series)
        if not key:
            return None
        excluded = os.path.realpath(exclude) if exclude else None

        def same(path: str) -> bool:
            return excluded is not None and os.path.realpath(path) == excluded

        later = sorted(
            (book for book in self._library().snapshot.series.get(key, [])
             if book[0] is not None and book[0] > position and not same(book[1])),
            key=self._series_order,
        )
        later = [book for book in later if self._present(book[1])]
        return next((book for book in later if float(book[0]).is_integer()), later[0] if later else None)


__all__ = [
    'LibraryIndex',
    'PlainTextParser',
    'content_documents',
    'description_text',
    'document_hashes',
    'estimate_print_pages',
    'filename_md5',
    'koreader_partial_md5',
    'plain_text',
    'release_year',
    'series_key',
]
