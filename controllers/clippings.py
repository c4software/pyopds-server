"""Clippings (highlights and notes) of the signed-in reader in the web UI.

The clippings synced by the readers (CrossPoint `/api/v1/clippings`, KOReader's Nextcloud Notes
export) grouped by book: the catalog book sheet (`?path=` of one EPUB), the `/clippings` page
(every book, searched in the browser) and their Markdown export. Pure functions for the grouping
and the Markdown; ClippingsController serves them (routes with auth='user').
"""
from controllers.webui import WebUIController

MARKDOWN_TYPE = 'text/markdown; charset=utf-8'


def reading_order(clipping: dict) -> tuple:
    """Sort key of a clipping in its book: spine item, then offset, paragraph or page (like the CrossPoint Sync app)."""
    position = next((clipping[key] for key in ('start_offset', 'paragraph_index', 'start_page')
                     if clipping.get(key) is not None), 0)
    return clipping.get('spine_index') or 0, position


def clipping_json(row: dict) -> dict:
    """Web UI representation of a stored clipping."""
    return {
        'id': row['id'],
        'text': row['text'],
        'note': row['note'] or None,
        'chapter': row['chapter_title'] or None,
        'page': row['start_page'] or None,
        'created_at': row['created_at'] or None,
    }


def group_clippings(clippings: list[dict], books: dict[str, dict]) -> list[dict]:
    """[{book, clippings}] of the `clippings` rows (newest first, as stored): books ordered by their newest
    clipping, clippings in reading order. `books` maps each document to its book ({document, title…})."""
    by_document: dict[str, list[dict]] = {}
    for row in clippings:
        by_document.setdefault(row['document'], []).append(row)
    return [
        {'book': books[document], 'clippings': [clipping_json(row) for row in sorted(rows, key=reading_order)]}
        for document, rows in by_document.items()
    ]


def clippings_markdown(groups: list[dict]) -> str:
    """Markdown of grouped clippings: a `##` title per book, each clipping quoted, then its note and chapter."""
    sections = []
    for group in groups:
        book = group['book']
        heading = f"## {book['title']}" + (f" — {book['author']}" if book.get('author') else '')
        quotes = []
        for clipping in group['clippings']:
            lines = ['> ' + clipping['text'].replace('\n', '\n> ')]
            if clipping['note']:
                lines.append(clipping['note'])
            caption = ' · '.join(part for part in (clipping['chapter'], clipping['page'] and f"p. {clipping['page']}") if part)
            if caption:
                lines.append(f'*{caption}*')
            quotes.append('\n\n'.join(lines))
        sections.append('\n\n'.join([heading, *quotes]))
    return '\n\n---\n\n'.join(sections) + '\n' if sections else ''


class ClippingsController(WebUIController):
    """/clippings page and its data: JSON (`/webui/clippings`) and Markdown export (`/webui/clippings/export`)."""

    def clippings_page(self) -> None:
        """GET /clippings: every clipping of the signed-in reader, grouped by book, with a search field."""
        self._serve('clippings.html')

    def clippings_list(self) -> None:
        """GET /webui/clippings[?path=<EPUB>]: {groups: [{book, clippings}]} of the signed-in reader (one book with `path`)."""
        groups = self._groups()
        if groups is not None:
            self._send_json({'groups': groups})

    def clippings_export(self) -> None:
        """GET /webui/clippings/export[?path=<EPUB>]: the same clippings as a Markdown file."""
        groups = self._groups()
        if groups is None:
            return
        name = groups[0]['book']['title'] if self._query('path') and groups else 'surlignages'
        filename = ''.join(ch if ch.isalnum() or ch in ' -_' else '_' for ch in name).strip() or 'surlignages'
        self._send_body(clippings_markdown(groups).encode('utf-8'), MARKDOWN_TYPE, 200, {
            'Content-Disposition': f'attachment; filename="{filename}.md"',
            'Cache-Control': 'no-store',
        })

    def _groups(self) -> list[dict] | None:
        """Grouped clippings of the reader, of one library book with `?path=`; None after answering 404."""
        from controllers.koreader_sync import get_sync_storage

        user = self.request.opds_user
        storage = get_sync_storage()
        rows = storage.all_clippings(user)
        if self._query('path'):
            book = self._library_book(self._query('path'))
            if not book:
                return None
            rows = [row for row in rows if row['document'] == book[1]] if book[1] else []
        return group_clippings(rows, self._books(storage, user, {row['document'] for row in rows}))

    @staticmethod
    def _books(storage, user: str, documents: set[str]) -> dict[str, dict]:
        """Book of each document: {document, title, author, path} (path: library EPUB relative to LIBRARY_DIR or None)."""
        from controllers.library_index import LibraryIndex
        from controllers.opds import library_relative_path

        library = LibraryIndex.get_instance()
        aliases = storage.document_aliases(user)
        books = {}
        for document in documents:
            info = storage.get_document(user, document) or {}
            meta, path = {}, None
            try:
                path = library.locate([document, *aliases.get(document, [])], info.get('filename'), info.get('library_path'))
                meta = library.book_meta(path) if path else {}
            except OSError:
                path = None
            books[document] = {
                'document': document,
                'title': info.get('title') or meta.get('title') or info.get('filename') or document,
                'author': info.get('author') or meta.get('author'),
                'path': library_relative_path(path) if path else None,
            }
        return books


__all__ = ['ClippingsController', 'clipping_json', 'clippings_markdown', 'group_clippings', 'reading_order']
