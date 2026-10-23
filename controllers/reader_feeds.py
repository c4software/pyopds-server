"""Personal OPDS feeds of the signed-in reader, built from the reading sync.

- « En cours » (/opds/reading): library books being read, most recently synced first.
- « Suite de vos séries » (/opds/series-next): the next library volume of each series the reader
  read or started.
- « Masquer les livres lus »: facet of the book lists (opds:facetGroup « Livres lus »), remembered
  per reader (preference `hide_read`), that leaves out the books the reader finished.

The root catalog shows the feeds as tiles first (with « À lire ensuite »); OPDS clients without
credentials see them too, opening them asks for the account (routes with auth='user').
"""
from dataclasses import dataclass, field
from urllib.parse import parse_qs, urlencode, urlparse

from controllers.opds import (
    ACQUISITION_FEED, START_LINK, OPDSController, format_series_index, navigation_tile, prepend_summary,
)
from controllers.reading_state import ReadingState, ReadingStateService

FACET_REL = 'http://opds-spec.org/facet'
HIDE_READ_PREFERENCE = 'hide_read'
READ_FACET_GROUP = 'Livres lus'


@dataclass
class ReadFilter:
    """Books left out of a list (`exclude`, EPUB paths) and the facet links that toggle the filter."""
    exclude: frozenset[str] = frozenset()
    links: list[tuple] = field(default_factory=list)


def _user(request_handler) -> str | None:
    return getattr(request_handler, 'opds_user', None)


def _state(user: str) -> ReadingState:
    return ReadingStateService.get_instance().for_user(user)


def read_filter(request_handler) -> ReadFilter:
    """« Masquer les livres lus » filter of a book list for the signed-in reader (nothing without one).

    `?read=hide|show` (the facet links) switches the filter and remembers the choice for the reader's
    next lists; without it, the remembered choice applies.
    """
    from controllers.koreader_sync import get_sync_storage

    user = _user(request_handler)
    if not user:
        return ReadFilter()
    parsed = urlparse(request_handler.path)
    query = parse_qs(parsed.query)
    storage = get_sync_storage()
    choice = (query.get('read') or [''])[0]
    if choice in ('hide', 'show'):
        hide = choice == 'hide'
        storage.set_preference(user, HIDE_READ_PREFERENCE, '1' if hide else '0')
    else:
        hide = storage.get_preference(user, HIDE_READ_PREFERENCE) == '1'

    # The facets reopen the same list (same parameters) on its first page
    kept = [(key, value) for key, values in query.items() if key not in ('page', 'read') for value in values]

    def facet(value: str, title: str, active: bool) -> tuple:
        href = f"{parsed.path}?{urlencode([*kept, ('read', value), ('page', '1')])}"
        attrs = {'opds:facetGroup': READ_FACET_GROUP}
        if active:
            attrs['opds:activeFacet'] = 'true'
        return FACET_REL, href, ACQUISITION_FEED, title, attrs

    links = [facet('show', 'Tous les livres', not hide), facet('hide', 'Masquer les livres lus', hide)]
    return ReadFilter(_state(user).read_paths if hide else frozenset(), links)


def series_continuations(state: ReadingState) -> list[dict]:
    """Next library volume of each series the reader read or started, newest series first.

    Each item: {'path', 'series', 'position' (of the next volume), 'reached' (furthest volume opened)}.
    A volume the reader already opened is never suggested; abandoned books (dnf) do not count.
    """
    from controllers.library_index import LibraryIndex, series_key

    library = LibraryIndex.get_instance()
    opened = state.opened_paths
    furthest: dict[str, tuple[float, str]] = {}
    order: list[str] = []
    for book in state.books:
        key = series_key(book.get('series'))
        position = book.get('series_index')
        if not key or position is None or book['signal'] not in ('read', 'reading', 'paused'):
            continue
        if key not in furthest:
            order.append(key)
        if key not in furthest or position > furthest[key][0]:
            furthest[key] = (position, book['series'])
    items = []
    for key in order:
        reached, name = furthest[key]
        position = reached
        # Skip the volumes already opened (e.g. read out of order)
        while True:
            found = library.next_in_series(name, position)
            if not found or found[1] not in opened:
                break
            position = found[0]
        if found:
            items.append({'path': found[1], 'series': name, 'position': found[0], 'reached': reached})
    return items


class ReaderFeedsController(OPDSController):
    """« En cours » and « Suite de vos séries » feeds (routes with auth='user')."""

    def _in_library(self, items: list[dict]) -> list[dict]:
        """Items whose 'path' is a library EPUB (never serve a path outside LIBRARY_DIR)."""
        return [item for item in items if self.security.is_within_library_dir(item['path'])]

    def _book_list(self, items: list[dict]) -> list[dict]:
        """Book dicts (path, relative_path, title, author) of the items' EPUBs, for _create_book_entries."""
        return [self.book_scanner.book_info(item['path']) for item in items]

    def _send_feed(self, title: str, feed_id: str, path: str, entries: list[dict], subtitle: str | None) -> None:
        links = [('self', path, ACQUISITION_FEED), START_LINK]
        xml = self.feed_generator.generate_feed(title, feed_id, links, entries, subtitle=subtitle)
        self._send_xml_response(xml, 'acquisition')

    def show_reading(self) -> None:
        """GET /opds/reading: library books being read by the signed-in reader, most recent first."""
        books = self._in_library(_state(self.request.opds_user).with_signal('reading'))
        entries = self._create_book_entries(self._book_list(books))
        for entry, book in zip(entries, books):
            if book.get('percentage'):
                prepend_summary(entry, f"Lu à {round(book['percentage'] * 100)} %")
        subtitle = None if entries else 'Aucun livre de la bibliothèque en cours de lecture.'
        self._send_feed('En cours', 'urn:reading', '/opds/reading', entries, subtitle)

    def show_series_next(self) -> None:
        """GET /opds/series-next: next library volume of each series the signed-in reader read or started."""
        items = self._in_library(series_continuations(_state(self.request.opds_user)))
        entries = self._create_book_entries(self._book_list(items))
        for entry, item in zip(entries, items):
            prepend_summary(entry, f"Tome {format_series_index(item['position'])} de {item['series']} · "
                                   f"vous en êtes au tome {format_series_index(item['reached'])}")
        subtitle = None if entries else 'Aucune suite à lire dans la bibliothèque pour vos séries.'
        self._send_feed('Suite de vos séries', 'urn:series-next', '/opds/series-next', entries, subtitle)


def root_entries(request_handler) -> list[dict]:
    """« En cours » and « Suite de vos séries » tiles of the root catalog (before « À lire ensuite »).

    Same visibility as « À lire ensuite »: signed-in readers (with a cover mosaic) and OPDS clients;
    browsers without an account get the header's sign-in button instead.
    """
    from controllers.opds_auth import wants_html

    user = _user(request_handler)
    if not user and wants_html(request_handler):
        return []
    reading: list[str] = []
    series: list[str] = []
    if user:
        try:
            state = _state(user)
            reading = [book['path'] for book in state.with_signal('reading')]
            series = [item['path'] for item in series_continuations(state)]
        except Exception:
            # The tiles are a bonus: never break the root catalog because of them
            reading, series = [], []
    return [
        navigation_tile('En cours', 'urn:reading', '/opds/reading', ACQUISITION_FEED, reading),
        navigation_tile('Suite de vos séries', 'urn:series-next', '/opds/series-next', ACQUISITION_FEED, series),
    ]


__all__ = ['ReadFilter', 'ReaderFeedsController', 'read_filter', 'root_entries', 'series_continuations']
