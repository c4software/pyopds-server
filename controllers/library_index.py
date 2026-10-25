"""Map KOReader document hashes to EPUB files of the library."""
import hashlib
import os
import posixpath
import re
import time
import zipfile
from html.parser import HTMLParser
from urllib.parse import unquote

from controllers.background import CACHE_LIFETIME, keep_fresh, shared_instance
from controllers.genres import genre_key
from controllers.opds import LIBRARY_DIR, BookMetadata, BookScanner, SecurityUtils, library_relative_path

CHUNK_SIZE = 1024
# Books whose light metadata (book_meta) stays cached; the oldest entries are dropped beyond it
META_CACHE_MAX = 4096
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


class _PlainTextParser(HTMLParser):
    """Plain text of an HTML fragment: paragraphs separated by a blank line, <br> kept as a line break."""

    def __init__(self):
        super().__init__()
        self.parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style'):
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
        if tag in ('script', 'style'):
            self._skip_depth = max(0, self._skip_depth - 1)
        elif tag in BLOCK_TAGS:
            self.parts.append('\n\n')

    def handle_data(self, data):
        if not self._skip_depth:
            self.parts.append(re.sub(r'\s+', ' ', data))


def description_text(value: str | None, max_length: int = DESCRIPTION_MAX_LENGTH) -> str | None:
    """Plain text of an EPUB description (often HTML), line breaks kept, cut at `max_length` characters."""
    if not value or not value.strip():
        return None
    if '<' in value:
        parser = _PlainTextParser()
        parser.feed(value)
        parser.close()
        text = ''.join(parser.parts)
    else:
        text = value
    lines = [' '.join(line.split()) for line in text.split('\n')]
    text = re.sub(r'\n{3,}', '\n\n', '\n'.join(lines)).strip()
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


def _content_documents(zf: zipfile.ZipFile) -> list[str]:
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
            for name in _content_documents(zf):
                parser = _TextLengthParser()
                parser.feed(zf.read(name).decode('utf-8', errors='replace'))
                parser.close()
                length += parser.length
    except (OSError, zipfile.BadZipFile, KeyError, ValueError):
        return None
    return max(1, round(length / CHARS_PER_PAGE)) if length else None


class LibraryIndex:
    """Lazily built index {document hash or file name: EPUB path}, refreshed every few minutes."""

    _instance = None

    @classmethod
    def get_instance(cls) -> 'LibraryIndex':
        """Get or create the singleton index."""
        return shared_instance(cls)

    def __init__(self):
        self._by_key: dict[str, str] = {}
        self._hash_cache: dict[str, tuple[float, int, str | None]] = {}
        self._details_cache: dict[str, tuple[float, dict]] = {}
        # {path: (mtime, book_meta)}: light OPF metadata of the books shown in the feeds
        self._meta_cache: dict[str, tuple[float, dict]] = {}
        self._built_at = 0.0
        # {path: (mtime, series, series index, title, author)}, {series key: [(series index or None, path)]}
        # and {title key: [path]}
        self._series_cache: dict[str, tuple[float, str | None, int | float | None, str | None, str | None]] = {}
        self._series_index: dict[str, list[tuple[int | float | None, str]]] = {}
        self._title_index: dict[str, list[str]] = {}
        self._series_built_at = 0.0

    def _refresh(self) -> None:
        """Hash index usable: built on first use, then rebuilt in the background every few minutes."""
        keep_fresh('library-hashes', lambda: self._built_at, CACHE_LIFETIME, self._rebuild)

    def _rebuild(self) -> None:
        by_key: dict[str, str] = {}
        for path in BookScanner.get_instance().collect_all_epub_paths():
            try:
                stat = os.stat(path)
            except OSError:
                continue
            cached = self._hash_cache.get(path)
            if cached and cached[0] == stat.st_mtime and cached[1] == stat.st_size:
                partial = cached[2]
            else:
                partial = koreader_partial_md5(path)
                self._hash_cache[path] = (stat.st_mtime, stat.st_size, partial)
            if partial:
                by_key.setdefault(partial, path)
            by_key.setdefault(filename_md5(path), path)
            by_key.setdefault(os.path.basename(path), path)
        self._by_key = by_key
        self._built_at = time.time()

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
        self._refresh()
        for key in [*documents, filename]:
            if key and key in self._by_key and os.path.exists(self._by_key[key]):
                return self._by_key[key]
        return None

    def locate(self, documents: list[str], filename: str | None = None, library_path: str | None = None) -> str | None:
        """EPUB of a synced book: associated by hand (library_path relative to LIBRARY_DIR), else matching
        one of its document hashes or its device file name; None when none matches."""
        return (library_path and self.resolve_relative(library_path)) or self.find(documents, filename)

    def book_meta(self, path: str) -> dict:
        """Light metadata of a library EPUB, read from its OPF only (no cover, no page estimate).

        Keys: title, author, genres, series, series_index, description (plain text), year and
        rating (0-5, None when not rated). Cached per file modification time; raises OSError when
        the file is missing.
        """
        mtime = os.path.getmtime(path)
        cached = self._meta_cache.get(path)
        if cached and cached[0] == mtime:
            return cached[1]
        info = BookMetadata.extract_epub_info(path) or {}
        rating = info.get('rating')
        meta = {
            'title': info.get('title'),
            'author': info.get('author'),
            'genres': info.get('genres', []),
            'series': info.get('series'),
            'series_index': info.get('series_index'),
            'description': description_text(info.get('description')),
            'year': release_year(info.get('date')),
            # calibre:rating counts half stars (0-10); 0 means "not rated"
            'rating': min(5.0, rating / 2) if rating and rating > 0 else None,
        }
        self._meta_cache.pop(path, None)
        if len(self._meta_cache) >= META_CACHE_MAX:
            del self._meta_cache[next(iter(self._meta_cache))]
        self._meta_cache[path] = (mtime, meta)
        self._series_cache[path] = (mtime, meta['series'], meta['series_index'], meta['title'], meta['author'])
        return meta

    def details(self, path: str) -> dict:
        """Title, author, genres, series, description, year, rating (see `book_meta`), cover presence
        and estimated print pages, cached per file modification time."""
        mtime = os.path.getmtime(path)
        cached = self._details_cache.get(path)
        if cached and cached[0] == mtime:
            return cached[1]
        cover, _ = BookMetadata.extract_epub_cover(path)
        details = {
            **self.book_meta(path),
            'has_cover': cover is not None,
            'page_count': estimate_print_pages(path),
            'relative_path': library_relative_path(path),
        }
        self._details_cache[path] = (mtime, details)
        return details

    def _refresh_series(self) -> None:
        """Series and title indexes usable: built on first use, then rebuilt in the background every few minutes."""
        keep_fresh('library-series', lambda: self._series_built_at, CACHE_LIFETIME, self._rebuild_series)

    def _rebuild_series(self) -> None:
        """Build the {series key: books} and {title key: books} indexes, reading only new or changed OPFs."""
        series_cache: dict[str, tuple[float, str | None, int | float | None, str | None, str | None]] = {}
        index: dict[str, list[tuple[int | float | None, str]]] = {}
        titles: dict[str, list[str]] = {}
        for path in BookScanner.get_instance().collect_all_epub_paths():
            try:
                mtime = os.path.getmtime(path)
            except OSError:
                continue
            # Shared with book_meta: an OPF read by a feed is not read again here, and conversely
            cached = self._series_cache.get(path)
            if not cached or cached[0] != mtime:
                info = BookMetadata.extract_epub_info(path) or {}
                cached = (mtime, info.get('series'), info.get('series_index'), info.get('title'), info.get('author'))
            series_cache[path] = cached
            _, series, position, title, _ = cached
            if series_key(series):
                index.setdefault(series_key(series), []).append((position, path))
            if series_key(title):
                titles.setdefault(series_key(title), []).append(path)
        self._series_cache = series_cache
        self._series_index = index
        self._title_index = titles
        self._series_built_at = time.time()

    def invalidate(self) -> None:
        """Rebuild the hash and series indexes on their next use (cached metadata stays, checked by mtime)."""
        self._built_at = 0.0
        self._series_built_at = 0.0

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
        self._refresh_series()
        books = [book for book in self._series_index.get(key, []) if os.path.exists(book[1])]
        return sorted(books, key=self._series_order)

    def find_by_title(self, title: str, author: str | None = None) -> str | None:
        """Library EPUB whose title matches `title` (accents, case and punctuation ignored); among several,
        the one sharing the most author name words with `author`. None when no title matches."""
        key = series_key(title)
        if not key:
            return None
        self._refresh_series()
        paths = [path for path in self._title_index.get(key, []) if os.path.exists(path)]
        if len(paths) <= 1:
            return paths[0] if paths else None
        wanted = set(series_key(author).split())

        def shared(path: str) -> int:
            return len(wanted & set(series_key(self._series_cache[path][4]).split()))

        return max(paths, key=shared)

    def next_in_series(self, series: str, position: int | float, exclude: str | None = None) -> tuple[int | float, str] | None:
        """(position, path) of the library book coming after `position` in `series` (names compared
        normalized): the next whole-numbered one (skipping novellas like 1.5), else whatever comes next."""
        key = series_key(series)
        if not key:
            return None
        self._refresh_series()
        excluded = os.path.realpath(exclude) if exclude else None

        def same(path: str) -> bool:
            return excluded is not None and os.path.realpath(path) == excluded

        later = sorted(
            (book for book in self._series_index.get(key, [])
             if book[0] is not None and book[0] > position and not same(book[1])),
            key=self._series_order,
        )
        later = [book for book in later if os.path.exists(book[1])]
        return next((book for book in later if float(book[0]).is_integer()), later[0] if later else None)


__all__ = [
    'LibraryIndex',
    'description_text',
    'document_hashes',
    'estimate_print_pages',
    'filename_md5',
    'koreader_partial_md5',
    'release_year',
    'series_key',
]
