"""Map KOReader document hashes to EPUB files of the library."""
import hashlib
import os
import time

from controllers.opds import LIBRARY_DIR, BookMetadata, BookScanner

CHUNK_SIZE = 1024
INDEX_TTL = 300


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


def filename_md5(path: str) -> str:
    """KOReader's filename document id: MD5 of the file name."""
    return hashlib.md5(os.path.basename(path).encode('utf-8')).hexdigest()


class LibraryIndex:
    """Lazily built index {document hash or file name: EPUB path}, refreshed every few minutes."""

    _instance = None

    @classmethod
    def get_instance(cls) -> 'LibraryIndex':
        """Get or create the singleton index."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def __init__(self):
        self._by_key: dict[str, str] = {}
        self._hash_cache: dict[str, tuple[float, int, str | None]] = {}
        self._details_cache: dict[str, tuple[float, dict]] = {}
        self._built_at = 0.0

    def _refresh(self) -> None:
        if time.time() - self._built_at < INDEX_TTL:
            return
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

    def find(self, documents: list[str], filename: str | None = None) -> str | None:
        """Return the EPUB path matching one of the hashes, or the device file name."""
        self._refresh()
        for key in [*documents, filename]:
            if key and key in self._by_key and os.path.exists(self._by_key[key]):
                return self._by_key[key]
        return None

    def details(self, path: str) -> dict:
        """Title, author and whether a cover exists, cached per file modification time."""
        mtime = os.path.getmtime(path)
        cached = self._details_cache.get(path)
        if cached and cached[0] == mtime:
            return cached[1]
        title, author, _ = BookMetadata.extract_epub_metadata(path)
        cover, _ = BookMetadata.extract_epub_cover(path)
        details = {
            'title': title,
            'author': author,
            'has_cover': cover is not None,
            'relative_path': os.path.relpath(path, LIBRARY_DIR).replace(os.sep, '/'),
        }
        self._details_cache[path] = (mtime, details)
        return details


__all__ = [
    'LibraryIndex',
    'filename_md5',
    'koreader_partial_md5',
]
