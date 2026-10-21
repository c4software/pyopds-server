"""Local "read next" recommendations computed from the user's reading sync history.

No third-party service: the library EPUBs the user never opened are scored against a profile
built from the books read or being read (same author, shared genres, next volume of a series),
each signal weighted by how recently the book was read.

The scoring functions are pure (plain dicts in, plain values out); RecommendationService wires
them to the sync storage, the LibraryIndex (document hash -> EPUB) and the BookScanner indexes.
"""
import math
import threading
import time
from dataclasses import dataclass, field

from controllers.genres import genre_key

FINISHED_RATIO = 0.9
RECENCY_HALF_LIFE_DAYS = 180.0
SIGNAL_WEIGHTS = {'read': 1.0, 'reading': 0.8, 'paused': 0.4}
AUTHOR_WEIGHT = 4.0
GENRE_WEIGHT = 2.0
SERIES_NEXT_BONUS = 6.0
SERIES_LATER_BONUS = 2.0
SERIES_UNKNOWN_INDEX_BONUS = 1.5
RECOMMENDATIONS_LIMIT = 30
CACHE_TTL = 300
# Candidates whose series is looked up (LibraryIndex.details opens the EPUB once, then caches it)
MAX_SERIES_LOOKUPS = 200
HISTORY_LIMIT = 100_000


def normalize_author(author: str | None) -> str:
    """Comparison key of an author name (case and spacing insensitive); '' for unknown authors."""
    if not author:
        return ''
    key = ' '.join(author.split()).casefold()
    return '' if key == 'unknown' else key


def normalize_series(series: object) -> str:
    """Comparison key of a series name ('' when absent)."""
    return ' '.join(series.split()).casefold() if isinstance(series, str) else ''


def series_number(value: object) -> float | None:
    """Series index as a number ('2', 2, '2.5'), None when missing or invalid."""
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def reading_signal(status: str | None, percentage: float | None) -> str | None:
    """Classify a synced book: 'read', 'reading', 'paused', 'dnf' (abandoned) or None.

    A book is read when marked finished or read to 90 % or more; abandoned (dnf) books never count as read.
    """
    if status == 'dnf':
        return 'dnf'
    if status == 'finished' or (percentage or 0) >= FINISHED_RATIO:
        return 'read'
    if status == 'paused':
        return 'paused'
    return 'reading'


def recency_factor(updated_at: int | float | None, now: float) -> float:
    """1 for a book read today, decaying to 0.5 (half-life RECENCY_HALF_LIFE_DAYS) for old reads."""
    if not updated_at:
        return 0.5
    age_days = max(0.0, (now - float(updated_at)) / 86400)
    return 0.5 + 0.5 * math.pow(0.5, age_days / RECENCY_HALF_LIFE_DAYS)


@dataclass
class ReadingProfile:
    """What the user likes: weighted authors and genres, furthest volume reached per series."""
    authors: dict[str, float] = field(default_factory=dict)
    genres: dict[str, float] = field(default_factory=dict)
    series: dict[str, float | None] = field(default_factory=dict)
    series_names: dict[str, str] = field(default_factory=dict)
    author_names: dict[str, str] = field(default_factory=dict)
    genre_names: dict[str, str] = field(default_factory=dict)
    books: int = 0

    @property
    def is_empty(self) -> bool:
        """True when no read / in progress book contributes to the profile."""
        return self.books == 0


def build_profile(history: list[dict], now: float) -> ReadingProfile:
    """Build the reading profile from history books.

    Each history item: {'author', 'genres': [names], 'series', 'series_index', 'status', 'percentage',
    'updated_at'}. Abandoned books (dnf) are ignored.
    """
    profile = ReadingProfile()
    for book in history:
        signal = reading_signal(book.get('status'), book.get('percentage'))
        if signal not in SIGNAL_WEIGHTS:
            continue
        weight = SIGNAL_WEIGHTS[signal] * recency_factor(book.get('updated_at'), now)
        profile.books += 1
        author = normalize_author(book.get('author'))
        if author:
            profile.authors[author] = profile.authors.get(author, 0.0) + weight
            profile.author_names.setdefault(author, ' '.join(book['author'].split()))
        for name in book.get('genres') or []:
            key = genre_key(name)
            if key:
                profile.genres[key] = profile.genres.get(key, 0.0) + weight
                profile.genre_names.setdefault(key, name)
        series = normalize_series(book.get('series'))
        if series:
            index = series_number(book.get('series_index'))
            previous = profile.series.get(series)
            if series not in profile.series or (index is not None and (previous is None or index > previous)):
                profile.series[series] = index
            profile.series_names.setdefault(series, ' '.join(book['series'].split()))
    return profile


def score_candidate(candidate: dict, profile: ReadingProfile) -> tuple[float, list[str]]:
    """Score an unopened book {'author', 'genres', 'series'?, 'series_index'?} against the profile.

    Returns (score, human readable reasons); 0 means "nothing in common".
    """
    score = 0.0
    reasons: list[str] = []

    author = normalize_author(candidate.get('author'))
    if author and author in profile.authors:
        # Saturating: the 3rd book read of an author adds little over the 2nd
        score += AUTHOR_WEIGHT * (1 - math.exp(-profile.authors[author]))
        reasons.append(f'Même auteur : {profile.author_names[author]}')

    genres = {genre_key(name): name for name in candidate.get('genres') or [] if genre_key(name)}
    if genres and profile.genres:
        top = max(profile.genres.values())
        matched = [key for key in genres if key in profile.genres]
        if matched:
            affinity = sum(profile.genres[key] / top for key in matched)
            score += GENRE_WEIGHT * affinity / math.sqrt(len(genres))
            reasons.append('Genres : ' + ', '.join(genres[key] for key in matched))

    series = normalize_series(candidate.get('series'))
    if series and series in profile.series:
        reached = profile.series[series]
        index = series_number(candidate.get('series_index'))
        name = profile.series_names[series]
        if index is None or reached is None:
            score += SERIES_UNKNOWN_INDEX_BONUS
            reasons.append(f'Série entamée : {name}')
        elif reached < index <= reached + 1:
            score += SERIES_NEXT_BONUS
            reasons.append(f'Tome suivant de {name}')
        elif index > reached:
            score += SERIES_LATER_BONUS
            reasons.append(f'Suite de {name}')

    return score, reasons


def recommend(candidates: list[dict], profile: ReadingProfile, limit: int = RECOMMENDATIONS_LIMIT) -> list[dict]:
    """Best scored candidates (copies with 'score' and 'reasons'), highest score first, ties by title."""
    if profile.is_empty:
        return []
    scored = []
    for candidate in candidates:
        score, reasons = score_candidate(candidate, profile)
        if score > 0:
            scored.append({**candidate, 'score': round(score, 4), 'reasons': reasons})
    scored.sort(key=lambda item: (-item['score'], (item.get('title') or item.get('path') or '').casefold()))
    return scored[:limit]


@dataclass
class Recommendations:
    """Result for a user: recommended books (path, title, author, score, reasons) and an explanation."""
    books: list[dict]
    history_count: int
    message: str | None = None


class RecommendationService:
    """Compute and cache the recommendations of a sync user.

    The cache entry lives CACHE_TTL seconds and is dropped as soon as the user's sync history changes
    (fingerprint of the progress rows, a single SQL query), so a new sync is reflected immediately.
    """

    _instance: 'RecommendationService | None' = None

    @classmethod
    def get_instance(cls) -> 'RecommendationService':
        """Get or create the singleton service."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def __init__(self):
        self._cache: dict[str, tuple[float, int, Recommendations]] = {}
        self._lock = threading.Lock()

    def invalidate(self, user: str | None = None) -> None:
        """Forget the cached recommendations of a user (of everyone when None)."""
        with self._lock:
            if user is None:
                self._cache.clear()
            else:
                self._cache.pop(user, None)

    def for_user(self, user: str) -> Recommendations:
        """Recommendations of a user, cached while fresh (CACHE_TTL) and while the sync history is unchanged."""
        # Imported here: tests reload controllers.koreader_sync
        from controllers.koreader_sync import get_sync_storage

        rows, aliases = get_sync_storage().list_progress(user, HISTORY_LIMIT)
        fingerprint = hash(tuple(
            (row['document'], row.get('status'), row.get('percentage'), row.get('updated_at'), row.get('library_path'))
            for row in rows
        ))
        with self._lock:
            cached = self._cache.get(user)
            if cached and cached[1] == fingerprint and time.time() - cached[0] < CACHE_TTL:
                return cached[2]
        result = self._compute(rows, aliases)
        with self._lock:
            self._cache[user] = (time.time(), fingerprint, result)
        return result

    @staticmethod
    def _compute(rows: list[dict], aliases: dict[str, list[str]]) -> Recommendations:
        # Imported here: library_index imports controllers.opds, which imports this module lazily
        from controllers.library_index import LibraryIndex
        from controllers.opds import BookScanner

        library = LibraryIndex.get_instance()
        scanner = BookScanner.get_instance()

        history: list[dict] = []
        opened: set[str] = set()
        for row in rows:
            try:
                path = (row.get('library_path') and library.resolve_relative(row['library_path'])) or library.find(
                    [row['document'], *aliases.get(row['document'], [])], row.get('filename')
                )
                if not path or path in opened:
                    continue
                opened.add(path)
                details = library.book_meta(path)
            except OSError:
                continue
            history.append({
                'path': path,
                'author': details.get('author') or row.get('author'),
                'genres': details.get('genres') or [],
                'series': details.get('series'),
                'series_index': details.get('series_index'),
                'status': row.get('status'),
                'percentage': row.get('percentage'),
                'updated_at': row.get('updated_at'),
            })

        if not history:
            return Recommendations([], 0, 'Aucun historique de lecture synchronisé ne correspond à un livre de la '
                                          'bibliothèque : lisez et synchronisez un livre pour obtenir des suggestions.')
        profile = build_profile(history, time.time())
        if profile.is_empty:
            return Recommendations([], len(history), 'Aucun livre lu ou en cours : les livres abandonnés ne servent '
                                                     'pas aux suggestions.')

        candidates = []
        series_lookups = 0
        for path, (author, genres) in scanner.get_path_metadata_index().items():
            if path in opened:
                continue
            candidate = {'path': path, 'author': author, 'genres': genres}
            # Series only matter for authors already read: look them up there (bounded, cached by LibraryIndex)
            if profile.series and normalize_author(author) in profile.authors and series_lookups < MAX_SERIES_LOOKUPS:
                series_lookups += 1
                try:
                    details = library.book_meta(path)
                    candidate['series'] = details.get('series')
                    candidate['series_index'] = details.get('series_index')
                except OSError:
                    pass
            candidates.append(candidate)

        books = recommend(candidates, profile)
        for book in books:
            info = scanner.book_info(book['path'])
            book.update(relative_path=info['relative_path'], title=info['title'], author=info['author'])
        message = None if books else 'Aucun livre non lu de la bibliothèque ne ressemble à vos lectures.'
        return Recommendations(books, len(history), message)


__all__ = [
    'Recommendations',
    'RecommendationService',
    'ReadingProfile',
    'build_profile',
    'reading_signal',
    'recency_factor',
    'recommend',
    'score_candidate',
]
