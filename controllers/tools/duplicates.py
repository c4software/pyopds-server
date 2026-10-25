"""Detect (and optionally remove) duplicate EPUB files of the library.

Command line, from the application directory (in the Docker image: `docker exec cops python -m
controllers.tools.duplicates`):

    python -m controllers.tools.duplicates                     # report only
    python -m controllers.tools.duplicates --json              # machine-readable report
    python -m controllers.tools.duplicates --move-to /config/duplicates   # move the extra copies away
    python -m controllers.tools.duplicates --delete            # delete the extra copies

Two kinds of duplicates are found:
- `identical`: files with the same content (SHA-256), wherever they are stored;
- `same_book`: different files whose metadata describe the same book (same normalized title,
  author and series position), e.g. two editions or two conversions of one book. With
  `--identical-only`, only the first kind is reported and removed;
- `to_check`: same metadata but texts of very different lengths (a single volume of a book
  published in several, an abridged edition…): reported, never removed.

In each group one file is kept (see `choose_keeper`): a file already synced by a reader first,
so that the reading progress stays attached to a library book. The others are the duplicates.
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import unicodedata
from dataclasses import dataclass, field

from controllers.library.index import estimate_print_pages, filename_md5, koreader_partial_md5
from controllers.catalog.opds import LIBRARY_DIR, BookMetadata, BookScanner
from controllers.sync.storage import KOREADER_SYNC_DB_PATH, SyncStorage

HASH_CHUNK_SIZE = 1024 * 1024
# Text length ratio (longest / shortest) beyond which files with the same metadata are not the same text
MAX_LENGTH_RATIO = 1.25


@dataclass
class BookFile:
    """An EPUB of the library with what the duplicate detection needs."""

    path: str
    size: int
    mtime: float
    title: str | None = None
    author: str | None = None
    series_index: int | float | None = None
    has_cover: bool = False
    # Known to the sync database (read on a reader): its progress refers to this file
    synced: bool = False

    @property
    def relative(self) -> str:
        """Path relative to LIBRARY_DIR (shown in reports)."""
        return os.path.relpath(self.path, LIBRARY_DIR)

    @property
    def name_matches_title(self) -> bool:
        """File name containing the title: readable, not garbled by a wrong encoding (e.g. "Odyss‚e")."""
        title = normalize_text(self.title)
        return bool(title) and title in normalize_text(os.path.splitext(os.path.basename(self.path))[0])


@dataclass
class DuplicateGroup:
    """Files of one book: `keep` stays in the library, `duplicates` are the extra copies."""

    kind: str
    keep: BookFile
    duplicates: list[BookFile] = field(default_factory=list)

    def to_dict(self) -> dict:
        """JSON-serializable form of the group."""
        return {
            'kind': self.kind,
            'title': self.keep.title,
            'author': self.keep.author,
            'keep': self.keep.relative,
            'duplicates': [book.relative for book in self.duplicates],
            # Duplicates read on a reader: their progress no longer matches a library book once removed
            'synced_duplicates': [book.relative for book in self.duplicates if book.synced],
        }


def normalize_text(value: str | None) -> str:
    """Lowercase, accent and punctuation free text with collapsed spaces."""
    if not value:
        return ''
    text = unicodedata.normalize('NFKD', value)
    text = ''.join(char for char in text if not unicodedata.combining(char)).lower()
    return ' '.join(re.sub(r'[\W_]+', ' ', text).split())


def author_key(author: str | None) -> str:
    """Author key independent of the word order ("Asimov, Isaac" == "Isaac Asimov")."""
    words = normalize_text(author).split()
    return ' '.join(sorted(words)) if words and words != ['unknown'] else ''


def book_key(book: BookFile) -> tuple[str, str, str] | None:
    """Key of the book described by the metadata, None when the title is unknown."""
    title = normalize_text(book.title)
    if not title:
        return None
    index = '' if book.series_index is None else str(float(book.series_index))
    return title, author_key(book.author), index


def file_sha256(path: str) -> str | None:
    """SHA-256 of the file content, None when it cannot be read."""
    digest = hashlib.sha256()
    try:
        with open(path, 'rb') as f:
            while chunk := f.read(HASH_CHUNK_SIZE):
                digest.update(chunk)
    except OSError:
        return None
    return digest.hexdigest()


def load_book(path: str) -> BookFile | None:
    """Read the size, date and metadata of a library EPUB, None when the file is missing."""
    try:
        stat = os.stat(path)
    except OSError:
        return None
    info = BookMetadata.extract_epub_info(path) or {}
    try:
        has_cover = BookMetadata.extract_epub_cover(path)[0] is not None
    except Exception:
        has_cover = False
    return BookFile(path=path, size=stat.st_size, mtime=stat.st_mtime, title=info.get('title'),
                    author=info.get('author'), series_index=info.get('series_index'), has_cover=has_cover)


def mark_synced(books: list[BookFile], db_path: str = KOREADER_SYNC_DB_PATH) -> None:
    """Flag the books referenced by the sync database (document hash, device file name or
    association made by hand); nothing when the database does not exist."""
    if not os.path.isfile(db_path):
        return
    documents, filenames, library_paths = SyncStorage(db_path).synced_references()
    for book in books:
        name = os.path.basename(book.path)
        book.synced = (book.relative in library_paths or name in filenames or filename_md5(book.path) in documents
                       or koreader_partial_md5(book.path) in documents)


def choose_keeper(books: list[BookFile]) -> BookFile:
    """File kept in a group: synced by a reader first, then with a cover, then with a readable
    file name, then readable metadata, then the largest (most complete) file, then the oldest,
    then the shortest path."""
    return min(books, key=lambda book: (not book.synced, not book.has_cover, not book.name_matches_title,
                                        not book.title, -book.size, book.mtime,
                                        book.relative.count(os.sep), book.relative))


def _group(kind: str, books: list[BookFile]) -> DuplicateGroup:
    keep = choose_keeper(books)
    return DuplicateGroup(kind, keep, sorted((book for book in books if book is not keep), key=lambda b: b.relative))


def same_text_length(books: list[BookFile]) -> bool:
    """Whether the books have texts of similar lengths (False when one cannot be measured)."""
    pages = [estimate_print_pages(book.path) for book in books]
    if not all(pages):
        return False
    return max(pages) / min(pages) <= MAX_LENGTH_RATIO


def find_duplicates(books: list[BookFile], identical_only: bool = False) -> list[DuplicateGroup]:
    """Duplicate groups of the books: identical files first, then the same books."""
    groups: list[DuplicateGroup] = []
    by_size: dict[int, list[BookFile]] = {}
    for book in books:
        by_size.setdefault(book.size, []).append(book)
    by_hash: dict[str, list[BookFile]] = {}
    for same_size in by_size.values():
        if len(same_size) < 2:
            continue
        for book in same_size:
            digest = file_sha256(book.path)
            if digest:
                by_hash.setdefault(digest, []).append(book)
    removed: set[str] = set()
    for same_content in by_hash.values():
        if len(same_content) > 1:
            group = _group('identical', same_content)
            groups.append(group)
            removed.update(book.path for book in group.duplicates)
    if not identical_only:
        by_book: dict[tuple[str, str, str], list[BookFile]] = {}
        for book in books:
            key = book_key(book)
            if key and book.path not in removed:
                by_book.setdefault(key, []).append(book)
        for same in by_book.values():
            if len(same) > 1:
                groups.append(_group('same_book' if same_text_length(same) else 'to_check', same))
    return sorted(groups, key=lambda group: (group.kind, group.keep.relative.lower()))


def remove_duplicates(groups: list[DuplicateGroup], move_to: str | None = None) -> list[str]:
    """Delete the duplicates of the groups, or move them under `move_to` (keeping their path
    relative to the library). Groups to check are left untouched. Returns the errors, the
    empty folders left are removed."""
    errors: list[str] = []
    for group in groups:
        if group.kind == 'to_check':
            continue
        for book in group.duplicates:
            try:
                if move_to:
                    target = os.path.join(move_to, book.relative)
                    os.makedirs(os.path.dirname(target), exist_ok=True)
                    shutil.move(book.path, target)
                else:
                    os.remove(book.path)
            except OSError as exc:
                errors.append(f'{book.relative}: {exc}')
                continue
            folder = os.path.dirname(book.path)
            library = os.path.realpath(LIBRARY_DIR)
            while os.path.realpath(folder).startswith(library + os.sep):
                try:
                    os.rmdir(folder)
                except OSError:
                    break
                folder = os.path.dirname(folder)
    return errors


def _human_size(size: int) -> str:
    for unit in ('o', 'Kio', 'Mio'):
        if size < 1024:
            return f'{size:.0f} {unit}'
        size /= 1024
    return f'{size:.1f} Gio'


def print_report(groups: list[DuplicateGroup]) -> None:
    """Human-readable report of the duplicate groups."""
    labels = {'identical': 'Fichiers identiques', 'same_book': 'Même livre (fichiers différents)',
              'to_check': 'À vérifier, jamais supprimés (mêmes métadonnées, textes de longueurs différentes)'}
    for kind, label in labels.items():
        kind_groups = [group for group in groups if group.kind == kind]
        if not kind_groups:
            continue
        print(f'== {label} : {len(kind_groups)} groupe(s)')
        for group in kind_groups:
            print(f'\n{group.keep.title or "?"} — {group.keep.author or "?"}')
            for role, book in [('garder ', group.keep), *(('doublon', book) for book in group.duplicates)]:
                synced = ', synchronisé' if book.synced else ''
                print(f'  {role}   {book.relative} ({_human_size(book.size)}{synced})')
        print()
    removable = [group for group in groups if group.kind != 'to_check']
    count = sum(len(group.duplicates) for group in removable)
    size = sum(book.size for group in removable for book in group.duplicates)
    print(f'{count} doublon(s), {_human_size(size)} ; {len(groups) - len(removable)} groupe(s) à vérifier')


def main(argv: list[str] | None = None) -> int:
    """Command line entry point (see the module docstring)."""
    parser = argparse.ArgumentParser(prog='python -m controllers.tools.duplicates',
                                     description=f'Doublons des EPUB de la bibliothèque ({LIBRARY_DIR}).')
    parser.add_argument('--identical-only', action='store_true', help='seulement les fichiers identiques')
    parser.add_argument('--json', action='store_true', help='rapport JSON')
    action = parser.add_mutually_exclusive_group()
    action.add_argument('--delete', action='store_true', help='supprimer les doublons')
    action.add_argument('--move-to', metavar='DOSSIER', help='déplacer les doublons dans ce dossier (hors bibliothèque)')
    args = parser.parse_args(argv)

    library = os.path.realpath(LIBRARY_DIR)
    if args.move_to and os.path.realpath(args.move_to).startswith(library + os.sep):
        parser.error('--move-to doit être hors de la bibliothèque (sinon les doublons restent au catalogue)')
    books = [book for path in BookScanner.get_instance().collect_all_epub_paths() if (book := load_book(path))]
    mark_synced(books)
    groups = find_duplicates(books, identical_only=args.identical_only)
    if args.json:
        json.dump([group.to_dict() for group in groups], sys.stdout, ensure_ascii=False, indent=2)
        print()
    else:
        print_report(groups)
    if args.delete or args.move_to:
        errors = remove_duplicates(groups, args.move_to)
        for error in errors:
            print(f'erreur : {error}', file=sys.stderr)
        removed = sum(len(group.duplicates) for group in groups if group.kind != 'to_check') - len(errors)
        verb = f'déplacé(s) dans {args.move_to}' if args.move_to else 'supprimé(s)'
        print(f'{removed} doublon(s) {verb}', file=sys.stderr)
        return 1 if errors else 0
    return 0


if __name__ == '__main__':
    sys.exit(main())
