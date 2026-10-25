"""Fix the metadata of the library EPUBs through a CSV file: missing genres, authors and titles.

Command line, from the application directory (in the Docker image: `docker exec cops python -m
controllers.tools.metadata_fix ...`):

    python -m controllers.tools.metadata_fix export livres.csv            # books to fix, with suggestions
    python -m controllers.tools.metadata_fix export livres.csv --online   # suggestions from Open Library too
    python -m controllers.tools.metadata_fix apply livres.csv             # check the completed file (dry run)
    python -m controllers.tools.metadata_fix apply livres.csv --write     # write the changes into the EPUBs

`export` lists the books with no genre shown in the catalog, a missing author or a title to clean,
and suggests genres from the author's other books (Open Library with `--online`). The file is
completed by hand or by another tool (columns `genres`, `titre_corrige`, `auteur_corrige`, see
CSV_COLUMNS and the README), then `apply` writes them into the OPF of the EPUBs: dc:subject,
dc:title and dc:creator. The file modification time is kept (the books do not come back in
"Recent Books"): restart the server afterwards so it reads the new metadata. A rewritten EPUB
gets a new KOReader document hash; the server still finds synced books by their file name.
"""
import argparse
import csv
import os
import re
import shutil
import sys
import tempfile
import urllib.parse
import zipfile
from dataclasses import dataclass, field
from xml.sax.saxutils import escape

from controllers.library.genres import genre_key, get_genre_config, normalize_genres
from controllers.library.index import LibraryIndex, description_text, release_year
from controllers.library.metadata_rules import author_key
from controllers.catalog.opds import LIBRARY_DIR, BookMetadata, library_relative_path
from controllers.library.store import Library, forget

CSV_COLUMNS = ['chemin', 'probleme', 'titre', 'auteur', 'serie', 'tome', 'annee', 'description',
               'suggestion', 'genres', 'titre_corrige', 'auteur_corrige']
MAX_GENRES = 3
DESCRIPTION_LENGTH = 500
# Authors with at least this many books with genres get a profile; a genre of the profile is on
# at least PROFILE_SHARE of them
PROFILE_MIN_BOOKS = 3
PROFILE_SHARE = 0.4
MISSING_AUTHORS = {'', 'unknown', 'inconnu', 'auteur inconnu', 'divers', 'divers roman', 'anonymous'}
NOISY_TITLE_RE = re.compile(r'(?i)^untitled$|\.epub$|french edition|grand format|z-lib')

# Online subjects rarely match genres.toml exactly ("French Detective and mystery stories")
KEYWORDS = [(re.compile(pattern), genre) for pattern, genre in [
    (r'\b(detective|mystery|mysteries|policiers?|polars?|crime fiction|enquetes? criminelles?)\b', 'Policier'),
    (r'\bscience fiction\b|\bspace opera\b|\banticipation\b|\buchronie\b|\btime travel\b', 'Science-fiction'),
    (r'\bfantasy\b', 'Fantasy'),
    (r'\bthrillers?\b|\bsuspense\b', 'Thriller'),
    (r'\bhorror\b|\bhorreur\b|\bepouvante\b', 'Horreur'),
    (r'\bhistorical fiction\b|\bromans? historiques?\b', 'Roman historique'),
    (r'\bjuvenile fiction\b|\bjeunesse\b', 'Jeunesse'),
    (r'\bespionage\b|\bespionnage\b|\bspy stories\b', 'Espionnage'),
    (r'\bhumou?r\b|\bhumoristique\b', 'Humour'),
    (r'\bbiograph', 'Biographie'),
    (r'\bsupernatural\b|\bparanormal\b|\bfantastique\b', 'Fantastique'),
    (r'\badventure stories\b|\broman d aventures?\b', 'Aventure'),
    (r'\blove stories\b|\bromance\b|\broman d amour\b', 'Romance'),
]]


@dataclass
class Book:
    """A library EPUB and its metadata (see `BookMetadata.opf_metadata`)."""

    path: str
    info: dict

    @property
    def relative(self) -> str:
        """Path relative to LIBRARY_DIR, the key of the CSV rows."""
        return library_relative_path(self.path, LIBRARY_DIR)


@dataclass
class Change:
    """What `apply` writes into one EPUB."""

    book: Book
    genres: list[str] = field(default_factory=list)
    title: str | None = None
    author: str | None = None

    @property
    def empty(self) -> bool:
        """True when nothing would change."""
        return not (self.genres or self.title or self.author)


def known_genres() -> dict[str, str]:
    """{genre key: displayed genre} of the genres of genres.toml (its aliases)."""
    return {genre_key(name): name for name in get_genre_config().aliases.values()}


def load_books() -> list[Book]:
    """Every EPUB of the library with its metadata, from the library database brought up to date with the
    files first (only the new or modified EPUBs are opened)."""
    library = Library.get_instance()
    library.sync()
    return [Book(book.path, book.meta) for book in library.snapshot.books.values()]


def problems(book: Book, genre_counts: dict[str, int]) -> list[str]:
    """What is wrong with a book: no genre (or none shown in the catalog), author, title."""
    found = []
    shown = [g for g in book.info['genres'] if genre_counts.get(genre_key(g), 0) >= get_genre_config().min_books]
    if not book.info['genres']:
        found.append('aucun genre')
    elif not shown:
        found.append('genre non affiché (moins de 3 livres) : ' + ', '.join(book.info['genres']))
    if (book.info.get('author') or '').strip().casefold() in MISSING_AUTHORS:
        found.append('auteur manquant')
    title = (book.info.get('title') or '').strip()
    if not title or NOISY_TITLE_RE.search(title):
        found.append('titre à nettoyer')
    return found


def author_profiles(books: list[Book]) -> dict[str, list[str]]:
    """{author key: genres} from the books that have genres: the genres of at least PROFILE_SHARE
    of an author's books, for authors with PROFILE_MIN_BOOKS such books or more."""
    by_author: dict[str, list[list[str]]] = {}
    for book in books:
        key = author_key(book.info.get('author'))
        if book.info['genres'] and key and key not in MISSING_AUTHORS:
            by_author.setdefault(key, []).append(book.info['genres'])
    profiles = {}
    for key, genre_lists in by_author.items():
        if len(genre_lists) < PROFILE_MIN_BOOKS:
            continue
        counts: dict[str, int] = {}
        for genres in genre_lists:
            for genre in genres:
                counts[genre] = counts.get(genre, 0) + 1
        common = [g for g, n in sorted(counts.items(), key=lambda kv: -kv[1]) if n >= PROFILE_SHARE * len(genre_lists)]
        if common:
            profiles[key] = common[:2]
    return profiles


def subjects_to_genres(subjects: list[str]) -> list[str]:
    """Genres of genres.toml found in online subjects ("Fiction / Science Fiction / General")."""
    known = known_genres()
    # Whole subjects only: "Romans, nouvelles" is not the "Nouvelles" genre
    parts = [re.sub(r'[,;]', ' ', part) for subject in subjects for part in re.split(r'\s*/\s*|\s+--\s+', subject or '')]
    found = [name for key, name in normalize_genres(parts) if key in known]
    counts: dict[str, int] = {}
    for part in parts:
        key = genre_key(part)
        for pattern, genre in KEYWORDS:
            if pattern.search(key):
                counts[genre] = counts.get(genre, 0) + 1
    found += [g for g, _ in sorted(counts.items(), key=lambda kv: -kv[1]) if g not in found]
    return found[:MAX_GENRES]


def open_library_genres(title: str, author: str) -> list[str]:
    """Genres of the best matching Open Library edition having some ([] when none)."""
    # Imported here: only `export --online` reaches the network
    from controllers.library.online_metadata import THRESHOLD, _fetch, score_candidate

    params = {'title': title, 'fields': 'title,author_name,subject', 'limit': 10}
    if author:
        params['author'] = author
    _, body = _fetch('https://openlibrary.org/search.json?' + urllib.parse.urlencode(params), timeout=15)
    docs = (body or {}).get('docs') or [] if isinstance(body, dict) else []
    candidates = sorted(
        ((score_candidate(title, author, d.get('title') or '', ', '.join(d.get('author_name') or [])), d) for d in docs),
        key=lambda scored: -scored[0],
    )
    for score, doc in candidates:
        if score < THRESHOLD:
            break
        # Genres of a single edition: mixing editions mixes books
        if genres := subjects_to_genres(doc.get('subject') or []):
            return genres
    return []


def export_rows(books: list[Book], online: bool = False) -> list[dict]:
    """CSV rows of the books to fix, sorted by problem then author and title."""
    genre_counts: dict[str, int] = {}
    for book in books:
        for genre in book.info['genres']:
            genre_counts[genre_key(genre)] = genre_counts.get(genre_key(genre), 0) + 1
    profiles = author_profiles(books)
    rows = []
    for book in books:
        found = problems(book, genre_counts)
        if not found:
            continue
        info = book.info
        title, author = (info.get('title') or '').strip(), (info.get('author') or '').strip()
        suggestion: list[str] = []
        if 'genre' in found[0]:
            suggestion = profiles.get(author_key(author), [])
            if not suggestion and online and title:
                suggestion = open_library_genres(title, '' if author.casefold() in MISSING_AUTHORS else author)
        description = description_text(info.get('description')) or ''
        rows.append({
            'chemin': book.relative,
            'probleme': ' ; '.join(found),
            'titre': title,
            'auteur': author,
            'serie': info.get('series') or '',
            'tome': '' if info.get('series_index') is None else info['series_index'],
            'annee': release_year(info.get('date')) or '',
            'description': ' '.join(description.split())[:DESCRIPTION_LENGTH],
            'suggestion': ';'.join(suggestion),
            'genres': '',
            'titre_corrige': '',
            'auteur_corrige': '',
        })
    return sorted(rows, key=lambda row: (row['probleme'], row['auteur'].casefold(), row['titre'].casefold()))


def write_csv(path: str, rows: list[dict]) -> None:
    """Write the rows (UTF-8, comma separated, header line)."""
    with open(path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


def plan_changes(rows: list[dict], accept_suggestions: bool = False) -> tuple[list[Change], list[str]]:
    """Changes to write from the completed CSV rows, and the warnings (unknown genres, missing files…).

    Genres already in the EPUB are not added twice; a title or author equal to the current one is
    no change. With `accept_suggestions`, the `suggestion` column is used when `genres` is empty.
    """
    # Displayed genre of each genre and alias of genres.toml ("SF" -> "Science-fiction")
    aliases = get_genre_config().aliases
    changes, warnings = [], []
    for number, row in enumerate(rows, start=2):
        relative = (row.get('chemin') or '').strip()
        path = LibraryIndex.resolve_relative(relative)
        if path is None:
            warnings.append(f'ligne {number} : fichier introuvable : {relative}')
            continue
        info = BookMetadata.extract_epub_info(path)
        if info is None:
            warnings.append(f'ligne {number} : EPUB illisible : {relative}')
            continue
        column = row.get('genres') or (row.get('suggestion') if accept_suggestions else '') or ''
        genres = []
        for name in (name.strip() for name in column.split(';')):
            if not name:
                continue
            display = aliases.get(genre_key(name))
            if display is None:
                warnings.append(f'ligne {number} : genre hors de genres.toml ignoré : {name}')
            elif display not in genres:
                genres.append(display)
        if len(genres) > MAX_GENRES:
            warnings.append(f'ligne {number} : plus de {MAX_GENRES} genres, seuls les {MAX_GENRES} premiers sont gardés')
        present = {genre_key(genre) for genre in info['genres']}
        title = (row.get('titre_corrige') or '').strip() or None
        author = (row.get('auteur_corrige') or '').strip() or None
        change = Change(
            Book(path, info),
            [genre for genre in genres[:MAX_GENRES] if genre_key(genre) not in present],
            title if title != (info.get('title') or '').strip() else None,
            author if author != (info.get('author') or '').strip() else None,
        )
        if not change.empty:
            changes.append(change)
    return changes, warnings


DC_PREFIX_RE = re.compile(r'xmlns:([A-Za-z0-9_-]+)=["\']http://purl\.org/dc/elements/1\.1/["\']')
METADATA_END_RE = re.compile(r'</(?:[A-Za-z0-9_-]+:)?metadata\s*>')


def _set_element(opf: str, tag: str, value: str) -> str:
    """OPF text with the first <tag> holding `value` (added before </metadata> when missing)."""
    elem = re.search(r'<' + re.escape(tag) + r'(\s[^>]*)?(/>|>.*?</' + re.escape(tag) + r'\s*>)', opf, re.S)
    if elem:
        # A sort name ("King, Stephen") would no longer match the new value
        attrs = re.sub(r'\s(?:opf:)?file-as=("[^"]*"|\'[^\']*\')', '', elem.group(1) or '')
        return opf[:elem.start()] + f'<{tag}{attrs}>{escape(value)}</{tag}>' + opf[elem.end():]
    end = METADATA_END_RE.search(opf)
    return opf[:end.start()] + f'<{tag}>{escape(value)}</{tag}>\n' + opf[end.start():]


def _drop_genre_series(opf: str) -> str:
    """OPF text without a calibre:series named after a genre ("Thriller"): it is not a series, and
    the subject of the same name would be hidden (see `normalize_genres`)."""
    series = re.search(r'<(?:opf:)?meta\s[^>]*name=["\']calibre:series["\'][^>]*content=["\']([^"\']*)["\'][^>]*/>', opf)
    if not series or genre_key(series.group(1)) not in known_genres():
        return opf
    return re.sub(r'\s*<(?:opf:)?meta\s[^>]*name=["\']calibre:series(?:_index)?["\'][^>]*/>', '', opf)


def edit_opf(opf: str, genres: list[str], title: str | None = None, author: str | None = None) -> str | None:
    """OPF text with the genres added (dc:subject) and the title / author replaced, None when the
    OPF has no Dublin Core namespace or no <metadata> element."""
    prefix = DC_PREFIX_RE.search(opf)
    end = METADATA_END_RE.search(opf)
    if not prefix or not end:
        return None
    dc = prefix.group(1)
    if title:
        opf = _set_element(opf, f'{dc}:title', title)
    if author:
        opf = _set_element(opf, f'{dc}:creator', author)
    if genres:
        opf = _drop_genre_series(opf)
        end = METADATA_END_RE.search(opf)
        head = opf[:end.start()].rstrip(' \t')
        if not head.endswith('\n'):
            head += '\n'
        opf = head + ''.join(f'<{dc}:subject>{escape(genre)}</{dc}:subject>\n' for genre in genres) + opf[end.start():]
    return opf


def rewrite_epub(change: Change) -> None:
    """Write the change into the EPUB: only its OPF is replaced (other entries, their order and
    compression kept), the result is checked before replacing the file, its mtime is kept.

    Raises:
        ValueError: When the OPF cannot be edited or the rewritten file does not read back as expected.
        OSError, zipfile.BadZipFile: When the file cannot be read or written.
    """
    path = change.book.path
    stat = os.stat(path)
    with zipfile.ZipFile(path) as zf:
        container = zf.read('META-INF/container.xml').decode('utf-8', 'replace')
        opf_name = re.search(r'full-path=["\']([^"\']+)["\']', container)
        if not opf_name:
            raise ValueError('container.xml sans OPF')
        opf_name = opf_name.group(1)
        raw = zf.read(opf_name)
        encoding = 'utf-8-sig' if raw.startswith(b'\xef\xbb\xbf') else 'utf-8'
        opf = edit_opf(raw.decode(encoding), change.genres, change.title, change.author)
        if opf is None:
            raise ValueError('OPF non modifiable (pas de <metadata> ni d\'espace de noms Dublin Core)')
        fd, tmp = tempfile.mkstemp(suffix='.epub', dir=os.path.dirname(path))
        os.close(fd)
        try:
            with zipfile.ZipFile(tmp, 'w') as out:
                for item in zf.infolist():
                    data = opf.encode(encoding) if item.filename == opf_name else zf.read(item)
                    # mimetype stays first and stored, as the EPUB specification requires
                    out.writestr(item, data, compress_type=item.compress_type)
            info = BookMetadata.extract_epub_info(tmp)
            if info is None:
                raise ValueError('EPUB réécrit illisible')
            if not all(genre in info['genres'] for genre in change.genres):
                raise ValueError(f'genres relus : {info["genres"]}')
            if change.title and info['title'] != change.title:
                raise ValueError(f'titre relu : {info["title"]!r}')
            shutil.copymode(path, tmp)
            os.replace(tmp, path)
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)
    os.utime(path, (stat.st_atime, stat.st_mtime))


def describe(change: Change) -> str:
    """One line summary of a change."""
    parts = []
    if change.genres:
        parts.append('genres : ' + ', '.join(change.genres))
    if change.title:
        parts.append(f'titre : {change.book.info.get("title") or "∅"} → {change.title}')
    if change.author:
        parts.append(f'auteur : {change.book.info.get("author") or "∅"} → {change.author}')
    return f'{change.book.relative}\n    ' + ' ; '.join(parts)


def main(argv: list[str] | None = None) -> int:
    """Command line entry point (see the module docstring)."""
    parser = argparse.ArgumentParser(prog='python -m controllers.tools.metadata_fix',
                                     description=f'Correction des métadonnées des EPUB de la bibliothèque ({LIBRARY_DIR}) par un CSV.')
    commands = parser.add_subparsers(dest='command', required=True)
    export = commands.add_parser('export', help='écrire le CSV des livres à corriger')
    export.add_argument('csv', help='fichier CSV à écrire')
    export.add_argument('--online', action='store_true', help='suggestions de genres depuis Open Library (titre et auteur envoyés)')
    apply = commands.add_parser('apply', help='appliquer le CSV complété (simulation sans --write)')
    apply.add_argument('csv', help='fichier CSV complété')
    apply.add_argument('--write', action='store_true', help='écrire dans les EPUB')
    apply.add_argument('--accept-suggestions', action='store_true', help='utiliser la colonne suggestion quand genres est vide')
    args = parser.parse_args(argv)

    if args.command == 'export':
        rows = export_rows(load_books(), online=args.online)
        write_csv(args.csv, rows)
        suggested = sum(1 for row in rows if row['suggestion'])
        print(f'{len(rows)} livre(s) à corriger écrits dans {args.csv} ({suggested} avec une suggestion de genres)')
        return 0

    try:
        with open(args.csv, encoding='utf-8-sig', newline='') as f:
            rows = list(csv.DictReader(f))
    except OSError as exc:
        print(f'erreur : {exc}', file=sys.stderr)
        return 1
    if rows and 'chemin' not in rows[0]:
        print('erreur : colonne « chemin » absente', file=sys.stderr)
        return 1
    changes, warnings = plan_changes(rows, accept_suggestions=args.accept_suggestions)
    for warning in warnings:
        print(f'attention : {warning}', file=sys.stderr)
    errors = 0
    written = []
    for change in changes:
        if args.write:
            try:
                rewrite_epub(change)
            except (OSError, ValueError, KeyError, zipfile.BadZipFile) as exc:
                errors += 1
                print(f'erreur : {change.book.relative} : {exc}', file=sys.stderr)
                continue
            written.append(change.book.relative)
        print(describe(change))
    if args.write:
        # Their modification time is kept: the server's database must read them again (controllers/library/store.py)
        forget(written)
        print(f'{len(changes) - errors} EPUB modifié(s), {errors} erreur(s). Redémarrez le serveur pour relire les métadonnées.')
    else:
        print(f'{len(changes)} EPUB à modifier (simulation : relancer avec --write pour écrire)')
    return 1 if errors else 0


if __name__ == '__main__':
    sys.exit(main())
