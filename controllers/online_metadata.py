"""Optional online book details, used only when the library and the sync data give nothing.

Port of crosspoint-sync's lookups (src/models/cover.ts, src/models/hardcover-catalog.ts,
src/connectors/matching.ts), off by default:

* ``ONLINE_METADATA=true``: cover (iTunes Search, then Open Library), print page count and
  description (Open Library), without any key.
* ``HARDCOVER_API_KEY``: moods, genres, content warnings, rating, series, release year and
  description from Hardcover's catalog, and the next book of a series.

Only the book's title, author and ISBN are sent to those services, never anything about the user.
Results (and misses) are cached in the ``catalog_cache`` table of the sync database, shared by
every user. Lookups run in a single background thread: the request handlers only read the cache,
except the cover picker (``cover_candidates``), which searches live like upstream does.
"""
import concurrent.futures
import contextlib
import datetime
import json
import os
import queue
import re
import sqlite3
import threading
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterator

USER_AGENT = 'PyOPDS-Server (+https://github.com/c4software/pyopds-server)'
TIMEOUT = 5
THRESHOLD = 0.6
HARDCOVER_ENDPOINT = 'https://api.hardcover.app/v1/graphql'

OPEN_LIBRARY_SUCCESS_TTL = 30 * 86400
OPEN_LIBRARY_MISS_TTL = 7 * 86400
HARDCOVER_TTL = 30 * 86400  # successes and misses alike
SERIES_TTL = 7 * 86400
ENRICH_PER_REQUEST = 10
CANDIDATES_MAX = 12
DESCRIPTION_MAX = 6000

HARDCOVER_MIN_GAP = 1.1
HARDCOVER_DAILY_CAP = 4000
HARDCOVER_PAUSE_AFTER_429 = 600

CACHE_SCHEMA = """
CREATE TABLE IF NOT EXISTS catalog_cache (
    key TEXT PRIMARY KEY,
    data TEXT,
    checked_at INTEGER NOT NULL
);
"""

HARDCOVER_SEARCH_QUERY = (
    'query ($q: String!) { search(query: $q, query_type: "Book", per_page: 5, page: 1) { results } }'
)
# Hardcover's "books in a series as the website shows them": one book per position.
HARDCOVER_SERIES_QUERY = """query ($id: Int!) {
  book_series(
    where: {series_id: {_eq: $id}, compilation: {_eq: false}, book: {canonical_id: {_is_null: true}, is_partial_book: {_eq: false}}}
    distinct_on: position
    order_by: [{position: asc}, {book: {users_count: desc}}]
  ) { position book { title slug release_year cached_contributors cached_image } }
}"""

# (status, parsed JSON body or None); status 0 means the request could not be made.
type FetchResult = tuple[int, object]
type Fetch = Callable[..., FetchResult]


# ------------------------------------------------------------------ configuration

def online_enabled() -> bool:
    """True when ONLINE_METADATA enables the keyless sources (iTunes, Open Library)."""
    return os.environ.get('ONLINE_METADATA', '').strip().lower() in ('1', 'true', 'yes', 'on')


def hardcover_key() -> str | None:
    """The operator's Hardcover API key (HARDCOVER_API_KEY), if any."""
    key = os.environ.get('HARDCOVER_API_KEY', '').strip()
    return key or None


def any_enabled() -> bool:
    """True when at least one online source is configured."""
    return online_enabled() or hardcover_key() is not None


# ---------------------------------------------------------------------- matching

def normalize_text(value: str) -> str:
    """Fold diacritics, lowercase, drop punctuation, collapse whitespace (any script)."""
    folded = ''.join(c for c in unicodedata.normalize('NFKD', value) if not unicodedata.category(c).startswith('M'))
    return re.sub(r'\s+', ' ', re.sub(r'[^\w\s]|_', ' ', folded.lower())).strip()


def core_title(title: str) -> str:
    """Strip the subtitle after a colon/dash and a trailing "(Series, Book 2)" suffix."""
    value = re.sub(r'\s*[:—-]\s.*$', '', title)
    value = re.sub(r'\s*\((?:[^)]*\b(?:book|vol|volume|part|no)\b[^)]*)\)\s*$', '', value, flags=re.IGNORECASE)
    return value.strip()


def normalize_author(author: str) -> str:
    """"Last, First" -> "first last", normalized."""
    trimmed = author.strip()
    if trimmed.count(',') == 1 and trimmed.index(',') > 0:
        last, first = (part.strip() for part in trimmed.split(','))
        return normalize_text(f'{first} {last}')
    return normalize_text(trimmed)


def _token_overlap(a: str, b: str) -> float:
    """Jaccard overlap of the words of two normalized strings."""
    sa, sb = set(a.split()), set(b.split())
    if not sa or not sb:
        return 0.0
    inter = len(sa & sb)
    return inter / (len(sa) + len(sb) - inter)


def score_candidate(want_title: str, want_author: str, title: str, author: str | None) -> float:
    """Similarity of a search result to the wanted book: title dominates, author disambiguates."""
    wt, ct = normalize_text(core_title(want_title)), normalize_text(core_title(title))
    title_score = _token_overlap(wt, ct) if wt and ct else 0.0
    wa = normalize_author(want_author) if want_author else ''
    ca = normalize_author(author) if author else ''
    if not wa or not ca:
        return title_score * 0.85  # title only: capped, title collisions are common
    return title_score * 0.7 + _token_overlap(wa, ca) * 0.3


def cache_key(title: str, author: str) -> str:
    """Cache key of a book shared by every user: normalized core title and author."""
    return f'{normalize_text(core_title(title))}|{normalize_author(author or "")}'


def clean_isbn(value: object) -> str | None:
    """ISBN-10/13 without separators, or None."""
    if not isinstance(value, str):
        return None
    isbn = re.sub(r'[^0-9Xx]', '', value).upper()
    return isbn if len(isbn) in (10, 13) else None


def book_query(title: str | None, author: str | None, isbn: str | None = None,
               filename: str | None = None) -> tuple[str, str, str | None] | None:
    """(title, author, isbn) to search for: the EPUB title, else the file name; None without either."""
    isbn = clean_isbn(isbn)
    if not title and filename:
        title = re.sub(r'\s*-\s*', ' ', re.sub(r'\.[a-z0-9]+$', '', filename, flags=re.IGNORECASE)).strip()
    if not title and not isbn:
        return None
    return title or '', author or '', isbn


def book_key(title: str, author: str, isbn: str | None) -> str:
    """Cache key of a book: title|author, or the ISBN when the title is unknown."""
    return cache_key(title, author) if title else f'isbn:{isbn}'


def _best[T](title: str, author: str, candidates: list[T], has: Callable[[T], object]) -> T | None:
    """Best scoring candidate above the threshold among those having the wanted field."""
    top, top_score = None, 0.0
    for cand in candidates:
        if not has(cand):
            continue
        score = score_candidate(title, author, cand['title'], cand.get('author'))
        if score >= THRESHOLD and (top is None or score > top_score):
            top, top_score = cand, score
    return top


# ------------------------------------------------------------------------ network

def _fetch(url: str, method: str = 'GET', headers: dict | None = None, body: object = None,
           timeout: float = TIMEOUT) -> FetchResult:
    """JSON request with an identifiable User-Agent: (status, parsed body), status 0 on network error."""
    data = json.dumps(body).encode('utf-8') if body is not None else None
    request_headers = {'User-Agent': USER_AGENT, 'Accept': 'application/json'}
    if data is not None:
        request_headers['Content-Type'] = 'application/json'
    request_headers.update(headers or {})
    request = urllib.request.Request(url, data=data, method=method, headers=request_headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status, raw = response.status, response.read()
    except urllib.error.HTTPError as e:
        return e.code, None
    except (OSError, ValueError):
        return 0, None
    try:
        return status, json.loads(raw)
    except ValueError:
        return status, None


class RateLimiter:
    """In-process request budget for Hardcover: a gap between requests, a daily cap, a pause after 429."""

    def __init__(self, min_gap: float = HARDCOVER_MIN_GAP, daily_cap: int = HARDCOVER_DAILY_CAP,
                 pause: float = HARDCOVER_PAUSE_AFTER_429,
                 clock: Callable[[], float] = time.time, sleep: Callable[[float], None] = time.sleep):
        self.min_gap, self.daily_cap, self.pause = min_gap, daily_cap, pause
        self.clock, self.sleep = clock, sleep
        self._lock = threading.Lock()
        self._next_slot = 0.0
        self._day = ''
        self._used = 0
        self._paused_until = 0.0

    def acquire(self) -> bool:
        """Reserve a request slot (waiting for the gap), or False when the budget is spent or paused."""
        with self._lock:
            now = self.clock()
            today = datetime.datetime.fromtimestamp(now, datetime.UTC).strftime('%Y-%m-%d')
            if today != self._day:
                self._day, self._used = today, 0
            if self._used >= self.daily_cap or now < self._paused_until:
                return False
            self._used += 1
            wait = max(0.0, self._next_slot - now)
            self._next_slot = max(now, self._next_slot) + self.min_gap
        if wait:
            self.sleep(wait)
        return True

    def back_off(self) -> None:
        """Stop requests for a while (the service answered 429)."""
        with self._lock:
            self._paused_until = self.clock() + self.pause


HARDCOVER_LIMITER = RateLimiter()


# ------------------------------------------------------------------------ parsing

def _store_for(text: str) -> str:
    """Apple Books country store for a title (a Japanese book is only in the Japanese store)."""
    if re.search(r'[぀-ヿ]', text):
        return 'jp'
    if re.search(r'[가-힯]', text):
        return 'kr'
    if re.search(r'[一-鿿]', text):
        return 'tw'
    return 'us'


def parse_itunes(body: object) -> list[dict]:
    """iTunes Search results as candidates with a 600 px artwork URL."""
    results = body.get('results') if isinstance(body, dict) else None
    out = []
    for r in results if isinstance(results, list) else []:
        if not isinstance(r, dict) or not isinstance(r.get('trackName'), str):
            continue
        artwork = r.get('artworkUrl100')
        out.append({
            'title': r['trackName'],
            'author': r.get('artistName') if isinstance(r.get('artistName'), str) else None,
            'url': artwork.replace('100x100bb', '600x600bb') if isinstance(artwork, str) else None,
            'pages': None,
        })
    return out


def _cover_id_url(cover_id: object) -> str | None:
    return f'https://covers.openlibrary.org/b/id/{cover_id}-L.jpg' if isinstance(cover_id, int) and cover_id > 0 else None


def _positive_int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None


def parse_open_library_search(body: object) -> list[dict]:
    """Open Library search.json docs as candidates (cover, median page count, work key)."""
    docs = body.get('docs') if isinstance(body, dict) else None
    out = []
    for d in docs if isinstance(docs, list) else []:
        if not isinstance(d, dict) or not isinstance(d.get('title'), str):
            continue
        authors = d.get('author_name')
        out.append({
            'title': d['title'],
            'author': authors[0] if isinstance(authors, list) and authors and isinstance(authors[0], str) else None,
            'url': _cover_id_url(d.get('cover_i')),
            'pages': _positive_int(d.get('number_of_pages_median')),
            'work': d.get('key') if isinstance(d.get('key'), str) and d['key'].startswith('/works/') else None,
        })
    return out


def parse_open_library_edition(body: object) -> dict | None:
    """Open Library /isbn/{isbn}.json edition: cover, page count, work key, description."""
    if not isinstance(body, dict):
        return None
    covers = body.get('covers')
    works = body.get('works')
    work = works[0].get('key') if isinstance(works, list) and works and isinstance(works[0], dict) else None
    return {
        'title': body.get('title') if isinstance(body.get('title'), str) else None,
        'url': _cover_id_url(covers[0]) if isinstance(covers, list) and covers else None,
        'pages': _positive_int(body.get('number_of_pages')),
        'work': work if isinstance(work, str) and work.startswith('/works/') else None,
        'description': parse_description(body.get('description')),
    }


def parse_description(value: object) -> str | None:
    """Open Library / Hardcover description: a string or {"value": string}, trimmed and capped."""
    if isinstance(value, dict):
        value = value.get('value')
    if isinstance(value, str) and value.strip():
        return value.strip()[:DESCRIPTION_MAX]
    return None


def _tidy(values: object, maximum: int) -> list[str]:
    """User-entered Hardcover tags: trimmed, deduplicated case-insensitively, first letter capitalized."""
    out: list[str] = []
    seen: set[str] = set()
    for v in values if isinstance(values, list) else []:
        if not isinstance(v, str) or not v.strip():
            continue
        key = v.strip().lower()
        if key in seen:
            continue
        seen.add(key)
        text = v.strip()
        out.append(text[0].upper() + text[1:])
        if len(out) >= maximum:
            break
    return out


def _number(value: object) -> float | int | None:
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def parse_hardcover_book(doc: object) -> dict | None:
    """A Hardcover search document as book details (port of upstream toBook)."""
    if not isinstance(doc, dict) or not doc.get('id') or not isinstance(doc.get('title'), str):
        return None
    authors = [a for a in doc.get('author_names') or [] if isinstance(a, str)] if isinstance(doc.get('author_names'), list) else []
    featured = doc.get('featured_series') if isinstance(doc.get('featured_series'), dict) else {}
    featured_series = featured.get('series') if isinstance(featured.get('series'), dict) else {}
    series_ids = doc.get('series_ids') if isinstance(doc.get('series_ids'), list) else []
    series_id = featured_series.get('id') if isinstance(featured_series.get('id'), int) else (
        series_ids[0] if series_ids and isinstance(series_ids[0], int) else None)
    series_names = doc.get('series_names') if isinstance(doc.get('series_names'), list) else []
    rating = _number(doc.get('rating'))
    ratings_count = _number(doc.get('ratings_count')) or 0
    image = doc.get('image') if isinstance(doc.get('image'), dict) else {}
    return {
        'id': str(doc['id']),
        'slug': doc.get('slug') if isinstance(doc.get('slug'), str) else None,
        'title': doc['title'],
        'author': authors[0] if authors else None,
        'moods': _tidy(doc.get('moods'), 12),
        'genres': _tidy(doc.get('genres'), 6),
        'content_warnings': _tidy(doc.get('content_warnings'), 8),
        'rating': round(rating, 2) if rating is not None and ratings_count > 0 else None,
        'series': series_names[0] if series_names and isinstance(series_names[0], str) else None,
        'series_id': series_id,
        'series_position': _number(doc.get('featured_series_position')),
        'release_year': doc.get('release_year') if isinstance(doc.get('release_year'), int) else None,
        'pages': _positive_int(doc.get('pages')),
        'cover': image.get('url') if isinstance(image.get('url'), str) else None,
        'description': parse_description(doc.get('description')),
    }


def best_hardcover_match(body: object, title: str, author: str) -> dict | None:
    """Best Hardcover search hit for the title/author (alternative titles count), or None."""
    data = body.get('data') if isinstance(body, dict) else None
    results = ((data or {}).get('search') or {}).get('results') if isinstance(data, dict) else None
    if isinstance(results, str):
        try:
            results = json.loads(results)
        except ValueError:
            results = None
    hits = results.get('hits') if isinstance(results, dict) else None
    top, top_score = None, 0.0
    for hit in hits if isinstance(hits, list) else []:
        doc = hit.get('document') if isinstance(hit, dict) else None
        book = parse_hardcover_book(doc)
        if not book:
            continue
        alternatives = doc.get('alternative_titles') if isinstance(doc.get('alternative_titles'), list) else []
        titles = [book['title'], *(t for t in alternatives if isinstance(t, str))]
        score = max(score_candidate(title, author, t, book['author']) for t in titles)
        if score >= THRESHOLD and (top is None or score > top_score):
            top, top_score = book, score
    return top


def parse_hardcover_series(body: object) -> list[dict] | None:
    """Books of a Hardcover series in order, or None when the answer is unusable."""
    if not isinstance(body, dict) or body.get('errors'):
        return None
    rows = (body.get('data') or {}).get('book_series') if isinstance(body.get('data'), dict) else None
    out = []
    for r in rows if isinstance(rows, list) else []:
        book = r.get('book') if isinstance(r, dict) else None
        position = _number(r.get('position')) if isinstance(r, dict) else None
        if position is None or not isinstance(book, dict) or not isinstance(book.get('title'), str):
            continue
        contributors = book.get('cached_contributors')
        who = None
        if isinstance(contributors, list) and contributors and isinstance(contributors[0], dict):
            who = (contributors[0].get('author') or {}).get('name')
        image = book.get('cached_image') if isinstance(book.get('cached_image'), dict) else {}
        out.append({
            'position': position,
            'title': book['title'],
            'author': who if isinstance(who, str) else None,
            'year': book.get('release_year') if isinstance(book.get('release_year'), int) else None,
            'slug': book.get('slug') if isinstance(book.get('slug'), str) else None,
            'cover': image.get('url') if isinstance(image.get('url'), str) else None,
        })
    return out


def next_after(books: list[dict], position: float) -> dict | None:
    """The book after `position`: the next whole-numbered entry (skipping novellas like 1.5), else the next one."""
    later = sorted((b for b in books if b['position'] > position), key=lambda b: b['position'])
    return next((b for b in later if float(b['position']).is_integer()), later[0] if later else None)


# ------------------------------------------------------------------------ service

class OnlineMetadata:
    """Cached online lookups plus the background enrichment worker, one per sync database."""

    _instances: dict[str, 'OnlineMetadata'] = {}
    _instances_lock = threading.Lock()

    @classmethod
    def for_db(cls, db_path: str) -> 'OnlineMetadata':
        """The shared service of a sync database."""
        with cls._instances_lock:
            if db_path not in cls._instances:
                cls._instances[db_path] = cls(db_path)
            return cls._instances[db_path]

    def __init__(self, db_path: str, fetch: Fetch | None = None, limiter: RateLimiter | None = None,
                 clock: Callable[[], float] = time.time):
        self.db_path = db_path
        self.fetch: Fetch = fetch or _fetch
        self.limiter = limiter or HARDCOVER_LIMITER
        self.clock = clock
        self._queue: queue.Queue[tuple] = queue.Queue()
        self._pending: set[tuple] = set()
        self._lock = threading.Lock()
        self._worker: threading.Thread | None = None
        self._schema_ready = False

    # ------------------------------------------------------------- cache

    @contextlib.contextmanager
    def _get_connection(self) -> Iterator[sqlite3.Connection]:
        """Open a connection to the sync database (cache table created on first use)."""
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            if not self._schema_ready:
                conn.executescript(CACHE_SCHEMA)
                self._schema_ready = True
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _now(self) -> int:
        return int(self.clock())

    def cache_get(self, keys: list[str]) -> dict[str, tuple[object, int]]:
        """Cached entries {key: (data or None for a miss, checked_at)}."""
        keys = list(dict.fromkeys(keys))
        if not keys:
            return {}
        out: dict[str, tuple[object, int]] = {}
        with self._get_connection() as conn:
            for start in range(0, len(keys), 500):
                chunk = keys[start:start + 500]
                rows = conn.execute(
                    f'SELECT key, data, checked_at FROM catalog_cache WHERE key IN ({",".join("?" * len(chunk))})', chunk
                ).fetchall()
                for row in rows:
                    try:
                        data = json.loads(row['data']) if row['data'] else None
                    except ValueError:
                        data = None
                    out[row['key']] = (data, row['checked_at'])
        return out

    def cache_put(self, key: str, data: object) -> None:
        """Remember a result (None for a miss) checked now."""
        with self._get_connection() as conn:
            conn.execute(
                'INSERT INTO catalog_cache (key, data, checked_at) VALUES (?, ?, ?) '
                'ON CONFLICT(key) DO UPDATE SET data = excluded.data, checked_at = excluded.checked_at',
                (key, json.dumps(data) if data is not None else None, self._now()),
            )

    def _fresh(self, entry: tuple[object, int] | None, success_ttl: int, miss_ttl: int) -> bool:
        """True when a cached entry exists and has not expired."""
        if entry is None:
            return False
        data, checked_at = entry
        return self._now() - checked_at < (success_ttl if data is not None else miss_ttl)

    def _stale_sources(self, entries: dict, key: str) -> bool:
        """True when an enabled source has no fresh cache entry for this book."""
        if online_enabled() and not self._fresh(entries.get(f'ol:{key}'), OPEN_LIBRARY_SUCCESS_TTL, OPEN_LIBRARY_MISS_TTL):
            return True
        return hardcover_key() is not None and not self._fresh(entries.get(f'hc:{key}'), HARDCOVER_TTL, HARDCOVER_TTL)

    @staticmethod
    def _merge(ol: object, hc: object) -> dict:
        """Book details from the cached Open Library/iTunes and Hardcover entries (stale data is still served)."""
        ol = ol if isinstance(ol, dict) and online_enabled() else {}
        hc = hc if isinstance(hc, dict) and hardcover_key() else {}
        info = {
            'cover_url': ol.get('cover_url') or hc.get('cover'),
            'page_count': hc.get('pages') or ol.get('page_count'),
            'description': hc.get('description') or ol.get('description'),
            'moods': hc.get('moods') or [],
            'genres': hc.get('genres') or [],
            'content_warnings': hc.get('content_warnings') or [],
            'rating': hc.get('rating'),
            'series': hc.get('series'),
            'series_id': hc.get('series_id'),
            'series_position': hc.get('series_position'),
            'release_year': hc.get('release_year'),
            'hardcover_slug': hc.get('slug'),
        }
        return info

    def cached_info(self, title: str | None, author: str | None, isbn: str | None = None,
                    filename: str | None = None, enqueue: bool = False) -> dict:
        """Cached online details of a book ({} when disabled or unknown); never touches the network.

        With `enqueue`, a book missing from the cache is queued for the background lookup.
        """
        if not any_enabled():
            return {}
        query = book_query(title, author, isbn, filename)
        if not query:
            return {}
        key = book_key(*query)
        try:
            entries = self.cache_get([f'ol:{key}', f'hc:{key}'])
        except sqlite3.Error:
            return {}
        if enqueue and self._stale_sources(entries, key):
            self.enqueue_book(*query)
        return self._merge(entries.get(f'ol:{key}', (None, 0))[0], entries.get(f'hc:{key}', (None, 0))[0])

    def fill_progress_items(self, items: list[dict], isbns: list[str | None] | None = None) -> None:
        """Fill the empty fields of GET /api/v1/progress items from the cache, in place.

        Up to ENRICH_PER_REQUEST books without a library match and without fresh cached data are
        queued for the background lookup (like upstream enrichSoon).
        """
        if not any_enabled() or not items:
            return
        isbns = isbns or [None] * len(items)
        queries = [book_query(it.get('title'), it.get('author'), isbn, it.get('filename')) for it, isbn in zip(items, isbns)]
        keys = [book_key(*q) if q else None for q in queries]
        try:
            entries = self.cache_get([f'{p}:{k}' for k in keys if k for p in ('ol', 'hc')])
        except sqlite3.Error:
            return
        queued = 0
        for item, query, key in zip(items, queries, keys):
            if not key:
                continue
            if queued < ENRICH_PER_REQUEST and item.get('library_path') is None and self._stale_sources(entries, key):
                if self.enqueue_book(*query):
                    queued += 1
            info = self._merge(entries.get(f'ol:{key}', (None, 0))[0], entries.get(f'hc:{key}', (None, 0))[0])
            for field in ('cover_url', 'page_count', 'rating', 'release_year', 'hardcover_slug',
                          'moods', 'genres', 'content_warnings'):
                if not item.get(field) and info.get(field):
                    item[field] = info[field]
            if item.get('series') is None and info.get('series'):
                item['series'] = info['series']
                item['series_position'] = info['series_position']

    def next_in_series(self, title: str | None, author: str | None, isbn: str | None = None,
                       filename: str | None = None) -> dict | None:
        """GET /documents/{d}/next answer from the cached Hardcover series, or None without a lookup.

        Returns {'series', 'next'} when the series is cached, {'next': None, 'pending': True} while
        the series is being fetched in the background.
        """
        if not hardcover_key():
            return None
        info = self.cached_info(title, author, isbn, filename)
        series_id, position = info.get('series_id'), info.get('series_position')
        if not isinstance(series_id, int) or position is None:
            return None
        try:
            entry = self.cache_get([f'series:{series_id}']).get(f'series:{series_id}')
        except sqlite3.Error:
            return None
        if not self._fresh(entry, SERIES_TTL, 0):
            self.enqueue_series(series_id)
        if entry is None or not isinstance(entry[0], list):
            return {'next': None, 'pending': True}
        return {'series': info.get('series'), 'next': next_after(entry[0], position)}

    # ------------------------------------------------------- background

    def enqueue_book(self, title: str, author: str, isbn: str | None = None) -> bool:
        """Queue a book for the background lookup (once at a time); False when already queued."""
        return self._enqueue(('book', title, author, isbn))

    def enqueue_series(self, series_id: int) -> bool:
        """Queue a Hardcover series for the background fetch."""
        return self._enqueue(('series', series_id))

    def _enqueue(self, job: tuple) -> bool:
        with self._lock:
            if job in self._pending:
                return False
            self._pending.add(job)
            if self._worker is None or not self._worker.is_alive():
                self._worker = threading.Thread(target=self._run, name='online-metadata', daemon=True)
                self._worker.start()
        self._queue.put(job)
        return True

    def _run(self) -> None:
        """Worker loop: one lookup at a time, errors never stop it."""
        while True:
            job = self._queue.get()
            try:
                if job[0] == 'book':
                    self.refresh_book(*job[1:])
                elif job[0] == 'series':
                    self.refresh_series(job[1])
            except Exception as e:  # noqa: BLE001 - a failed lookup must not kill the worker
                print(f'Online metadata lookup failed: {e}')
            finally:
                with self._lock:
                    self._pending.discard(job)
                self._queue.task_done()

    def wait_idle(self) -> None:
        """Block until every queued lookup is done (tests)."""
        self._queue.join()

    def refresh_book(self, title: str, author: str, isbn: str | None = None) -> None:
        """Look the book up on the enabled sources whose cache entry is missing or expired (network)."""
        key = book_key(title, author, isbn)
        entries = self.cache_get([f'ol:{key}', f'hc:{key}'])
        if online_enabled() and not self._fresh(entries.get(f'ol:{key}'), OPEN_LIBRARY_SUCCESS_TTL, OPEN_LIBRARY_MISS_TTL):
            found = self.lookup_open_library(title, author, isbn)
            if found != 'later':
                self.cache_put(f'ol:{key}', found)
        api_key = hardcover_key()
        if api_key and title and not self._fresh(entries.get(f'hc:{key}'), HARDCOVER_TTL, HARDCOVER_TTL):
            found = self.search_hardcover(title, author, api_key)
            if found != 'later':
                self.cache_put(f'hc:{key}', found)

    def refresh_series(self, series_id: int) -> None:
        """Fetch a Hardcover series and cache it (network); a failure keeps the previous list."""
        api_key = hardcover_key()
        if not api_key:
            return
        books = self.fetch_hardcover_series(series_id, api_key)
        if books != 'later':
            self.cache_put(f'series:{series_id}', books)

    # ------------------------------------------------------- sources

    def _get_json(self, url: str) -> FetchResult:
        return self.fetch(url, headers={'User-Agent': USER_AGENT}, timeout=TIMEOUT)

    def itunes_candidates(self, title: str, author: str) -> list[dict] | None:
        """iTunes Search ebook results, or None on a network error."""
        term = f'{title} {author}'.strip()
        query = urllib.parse.urlencode({'term': term, 'entity': 'ebook', 'limit': '10', 'country': _store_for(term)})
        status, body = self._get_json(f'https://itunes.apple.com/search?{query}')
        return None if status == 0 else parse_itunes(body) if status == 200 else []

    def open_library_candidates(self, title: str, author: str) -> list[dict] | None:
        """Open Library search results, or None on a network error."""
        params = {'title': title, 'limit': '10', 'fields': 'key,title,author_name,cover_i,number_of_pages_median'}
        if author:
            params['author'] = author
        status, body = self._get_json(f'https://openlibrary.org/search.json?{urllib.parse.urlencode(params)}')
        return None if status == 0 else parse_open_library_search(body) if status == 200 else []

    def open_library_description(self, work: str | None) -> str | None:
        """Description of an Open Library work (/works/{id}.json)."""
        if not work:
            return None
        status, body = self._get_json(f'https://openlibrary.org{work}.json')
        return parse_description(body.get('description')) if status == 200 and isinstance(body, dict) else None

    def lookup_open_library(self, title: str, author: str, isbn: str | None = None) -> dict | None | str:
        """Cover, print page count and description (iTunes + Open Library); None for a miss, 'later' when offline."""
        cover = pages = description = work = None
        offline = False
        if isbn:
            status, body = self._get_json(f'https://openlibrary.org/isbn/{isbn}.json')
            offline = status == 0
            edition = parse_open_library_edition(body) if status == 200 else None
            if edition:
                cover, pages, work, description = edition['url'], edition['pages'], edition['work'], edition['description']
        if title:
            if not cover:
                apple = self.itunes_candidates(title, author)
                offline = offline or apple is None
                cover = (_best(title, author, apple or [], lambda c: c['url']) or {}).get('url')
            if not cover or not pages or not work:
                ol = self.open_library_candidates(title, author)
                offline = offline or ol is None
                ol = ol or []
                cover = cover or (_best(title, author, ol, lambda c: c['url']) or {}).get('url')
                pages = pages or (_best(title, author, ol, lambda c: c['pages']) or {}).get('pages')
                work = work or (_best(title, author, ol, lambda c: c['work']) or {}).get('work')
                # A series prefix ("Game Changers 6: The Long Game") hides the book from Open Library
                short = ': '.join(title.split(': ')[1:])
                if not pages and short:
                    pages = (_best(short, author, self.open_library_candidates(short, author) or [], lambda c: c['pages']) or {}).get('pages')
        if not description:
            description = self.open_library_description(work)
        if not (cover or pages or description):
            return 'later' if offline else None
        return {'cover_url': cover, 'page_count': pages, 'description': description}

    def cover_candidates(self, title: str, author: str) -> list[dict]:
        """Every cover iTunes and Open Library offer for a title, best matches first (live, cover picker)."""
        if not online_enabled() or not title:
            return []
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            apple_future = pool.submit(self.itunes_candidates, title, author)
            ol_future = pool.submit(self.open_library_candidates, title, author)
            try:
                apple = apple_future.result(timeout=TIMEOUT * 2) or []
            except Exception:  # noqa: BLE001
                apple = []
            try:
                ol = ol_future.result(timeout=TIMEOUT * 2) or []
            except Exception:  # noqa: BLE001
                ol = []
        tagged = [{**c, 'source': 'Apple Books'} for c in apple] + [{**c, 'source': 'Open Library'} for c in ol]
        scored = sorted(
            (c for c in tagged if c.get('url')),
            key=lambda c: score_candidate(title, author, c['title'], c.get('author')),
            reverse=True,
        )
        seen: set[str] = set()
        out = []
        for c in scored:
            if c['url'] in seen:
                continue
            seen.add(c['url'])
            out.append({'url': c['url'], 'title': c['title'], 'author': c.get('author'),
                        'source': c['source'], 'pages': c.get('pages')})
            if len(out) >= CANDIDATES_MAX:
                break
        return out

    def _hardcover_post(self, query: str, variables: dict, api_key: str) -> object | str:
        """Rate-limited Hardcover GraphQL request: the JSON body, or 'later'."""
        if not self.limiter.acquire():
            return 'later'
        status, body = self.fetch(
            HARDCOVER_ENDPOINT, method='POST',
            headers={'authorization': api_key if api_key.startswith('Bearer ') else f'Bearer {api_key}',
                     'User-Agent': USER_AGENT},
            body={'query': query, 'variables': variables}, timeout=TIMEOUT,
        )
        if status == 429:
            self.limiter.back_off()
            return 'later'
        if status != 200 or not isinstance(body, dict) or body.get('errors'):
            return 'later'
        return body

    def search_hardcover(self, title: str, author: str, api_key: str) -> dict | None | str:
        """Best Hardcover match for a title/author, None for a confident miss, 'later' when not possible now."""
        body = self._hardcover_post(HARDCOVER_SEARCH_QUERY, {'q': f'{core_title(title)} {author}'.strip()}, api_key)
        if body == 'later':
            return 'later'
        return best_hardcover_match(body, title, author)

    def fetch_hardcover_series(self, series_id: int, api_key: str) -> list[dict] | str:
        """Books of a Hardcover series in order, or 'later'."""
        body = self._hardcover_post(HARDCOVER_SERIES_QUERY, {'id': series_id}, api_key)
        if body == 'later':
            return 'later'
        books = parse_hardcover_series(body)
        return 'later' if books is None else books


def service(db_path: str) -> OnlineMetadata:
    """The shared online metadata service of a sync database."""
    return OnlineMetadata.for_db(db_path)


__all__ = [
    'OnlineMetadata',
    'RateLimiter',
    'any_enabled',
    'cache_key',
    'core_title',
    'hardcover_key',
    'next_after',
    'normalize_author',
    'normalize_text',
    'online_enabled',
    'score_candidate',
    'service',
]
