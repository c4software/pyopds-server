"""Nextcloud Notes API (v1) emulation receiving KOReader's highlights export.

KOReader's exporter (Export highlights → Nextcloud Notes) sends one Markdown note per book to
`<host>/index.php/apps/notes/api/v1/notes` with HTTP Basic: with this server as host and the sync
account as credentials, every highlight of the note becomes a clipping of the book, shown by the
CrossPoint Sync app and the reading page. Re-exporting a book replaces its note: new highlights are
added, edited ones updated and the ones removed on the reader deleted (only those of the note).
"""
import datetime
import hashlib
import os
import re

from controllers.crosspoint_sync import CLIPPING_CHAPTER_MAX, CLIPPING_TEXT_MAX_BYTES
from controllers.koreader_sync import MAX_BODY_BYTES, SyncControllerBase, utf8_length
from controllers.sync_storage import md5_hex

NOTE_TITLE_MAX = 512
CATEGORY_MAX = 128

PAGE_HEADER_RE = re.compile(r'^### Page (.*?) @ (.*)$')
# Highlight formatting of KOReader's Markdown export (template/md.lua), longest wrappers first
FORMATTERS = (
    ('**_', '_**'), ('**', '**'), ('==', '=='), ('++', '++'), ('<u>', '</u>'), ('~~', '~~'), ('*', '*'),
)
# os.date('%d %B %Y %I:%M:%S %p') of the reader (English month names)
DATE_FORMAT = '%d %B %Y %I:%M:%S %p'


def strip_formatting(text: str) -> str:
    """Highlighted text without the Markdown wrapper KOReader puts around it (bold, italic…)."""
    for prefix, suffix in FORMATTERS:
        if len(text) > len(prefix) + len(suffix) and text.startswith(prefix) and text.endswith(suffix):
            return text[len(prefix):-len(suffix)]
    return text


def truncate_utf8(value: str, max_bytes: int) -> str:
    """Cut a string to at most `max_bytes` UTF-8 bytes, on a character boundary."""
    if utf8_length(value) <= max_bytes:
        return value
    return value.encode('utf-8')[:max_bytes].decode('utf-8', errors='ignore')


def parse_note(content: str) -> dict:
    """Parse a KOReader Markdown note: {title, author, highlights: [{id, chapter, page, created_at, text, note}]}.

    Layout (template/md.lua): `# title`, `##### author`, then `## chapter` headers and, for each
    highlight, `### Page <page> @ <date>`, the text, and `---` followed by the note when there is one.
    """
    title = author = None
    chapter = ''
    highlights: list[dict] = []
    current: dict | None = None

    def close() -> None:
        if current is None:
            return
        body = '\n'.join(current.pop('lines')).strip('\n')
        text, separator, note = body.partition('\n\n---\n')
        text = strip_formatting(text.strip())
        if not text:
            return
        try:
            created_at = int(datetime.datetime.strptime(current['date'], DATE_FORMAT).replace(tzinfo=datetime.UTC).timestamp())
        except ValueError:
            created_at = 0
        page = current['page']
        highlights.append({
            # Stable across exports: the same highlight keeps its clipping
            'id': 'koreader-' + hashlib.md5(f"{page}\n{current['date']}\n{text}".encode('utf-8')).hexdigest()[:32],
            'chapter': current['chapter'][:CLIPPING_CHAPTER_MAX],
            'page': int(page) if page.isdigit() and int(page) < 2 ** 32 else 0,
            'created_at': max(created_at, 0),
            'text': truncate_utf8(text, CLIPPING_TEXT_MAX_BYTES),
            'note': truncate_utf8(note.strip(), CLIPPING_TEXT_MAX_BYTES) if separator and note.strip() else None,
        })

    for line in content.split('\n'):
        if title is None and line.startswith('# '):
            title = line[2:].strip()
            continue
        if author is None and current is None and line.startswith('##### '):
            author = line[6:].strip()
            continue
        match = PAGE_HEADER_RE.match(line)
        if match:
            close()
            current = {'page': match.group(1).strip(), 'date': match.group(2).strip(), 'chapter': chapter, 'lines': []}
        elif line == '##' or (line.startswith('## ') and not line.startswith('### ')):
            close()
            current = None
            chapter = line[3:].strip()
        elif current is not None:
            current['lines'].append(line)
    close()
    return {'title': title, 'author': author, 'highlights': highlights}


def note_json(row: dict) -> dict:
    """Nextcloud Notes API representation of a stored note."""
    return {
        'id': row['id'],
        'etag': md5_hex(f"{row['modified']}:{row['content']}"),
        'readonly': False,
        'modified': row['modified'],
        'title': row['title'],
        'category': row['category'],
        'content': row['content'],
        'favorite': False,
    }


class NextcloudNotesController(SyncControllerBase):
    """`/index.php/apps/notes/api/v1/notes` endpoints used by KOReader's Nextcloud Notes export
    (routes with auth='user': sync account in HTTP Basic, user in request_handler.opds_user)."""

    def _discard_body(self) -> None:
        """Read the body KOReader sends even with GET (`null`), so that closing the socket does not reset it."""
        try:
            length = int(self.request.headers.get('Content-Length') or 0)
        except ValueError:
            return
        if 0 < length <= MAX_BODY_BYTES:
            self.request.rfile.read(length)

    def _note_body(self) -> dict | None:
        """Validated {title, category, content} of a POST / PUT, or answer 400 (returns None)."""
        ok, body = self._read_json()
        if not ok or not isinstance(body, dict):
            self._send_json_response({'message': 'Invalid request'}, 400)
            return None
        fields = {key: body.get(key, '') for key in ('title', 'category', 'content')}
        if not all(isinstance(value, str) for value in fields.values()):
            self._send_json_response({'message': 'Invalid request'}, 400)
            return None
        fields['title'] = fields['title'][:NOTE_TITLE_MAX]
        fields['category'] = fields['category'][:CATEGORY_MAX]
        return fields

    def _book_document(self, user: str, title: str, author: str | None) -> str:
        """Document of the book of a note: the library EPUB with this title (as synced by the user, else
        KOReader's id of the file), else the user's book with this title, else an id of its own."""
        from controllers.library_index import LibraryIndex
        from controllers.opds import library_relative_path

        epub = LibraryIndex.get_instance().find_by_title(title, author)
        if epub:
            relative = library_relative_path(epub)
            document = self.sync_storage.epub_document(user, epub, relative)
            info = {'title': title, 'author': author, 'filename': os.path.basename(epub), 'library_path': relative}
            return self.sync_storage.remember_document(user, document, info)
        document = self.sync_storage.find_document_by_title(user, title)
        if document:
            return document
        return self.sync_storage.remember_document(user, md5_hex(f'koreader-notes\n{title}\n{author or ""}'), {'title': title, 'author': author})

    def _import(self, user: str, content: str, previous: dict | None) -> str | None:
        """Store the highlights of a note as clippings of its book (deleting those no longer in the note);
        returns the book's document, None when the note has no title."""
        parsed = parse_note(content)
        if not parsed['title']:
            return None
        document = self._book_document(user, parsed['title'], parsed['author'])
        old: dict[str, dict] = {}
        if previous:
            old = {h['id']: h for h in parse_note(previous['content'])['highlights']}
            if previous['document'] != document:
                # The note now belongs to another book: its clippings move with it
                self.sync_storage.store_clippings(user, previous['document'], [{'id': i, 'deleted': True} for i in old])
                old = {}
        operations = []
        new_ids = set()
        for highlight in parsed['highlights']:
            new_ids.add(highlight['id'])
            if old.get(highlight['id']) == highlight:
                continue
            operation = {
                'id': highlight['id'], 'deleted': False, 'spine': 0, 'start_page': highlight['page'],
                'end_page': highlight['page'], 'pages': 1, 'start_word': 0, 'end_word': 0, 'words': 0,
                'created_at': highlight['created_at'], 'layout_signature': 0, 'para': None,
                'start_offset': None, 'end_offset': None, 'text': highlight['text'], 'chapter': highlight['chapter'],
            }
            # A note written or removed on the reader replaces the clipping's; otherwise the app's note is kept
            if highlight['note'] is not None or old.get(highlight['id'], {}).get('note') is not None:
                operation['note'] = highlight['note']
            operations.append(operation)
        operations += [{'id': i, 'deleted': True} for i in old if i not in new_ids]
        if operations:
            self.sync_storage.store_clippings(user, document, operations)
        return document

    def notes_list(self) -> None:
        """GET /index.php/apps/notes/api/v1/notes[?category=]: notes received from KOReader."""
        self._discard_body()
        self._send_json_response([note_json(row) for row in self.sync_storage.list_notes(self.request.opds_user, self._query('category'))])

    def note_show(self) -> None:
        """GET /index.php/apps/notes/api/v1/notes/{id}: one note."""
        self._discard_body()
        row = self.sync_storage.get_note(self.request.opds_user, int(self._param('id')))
        if not row:
            self._send_json_response({'message': 'Note not found'}, 404)
            return
        self._send_json_response(note_json(row))

    def note_create(self) -> None:
        """POST /index.php/apps/notes/api/v1/notes {title, category, content}: a book's highlights."""
        user = self.request.opds_user
        fields = self._note_body()
        if fields is None:
            return
        document = self._import(user, fields['content'], None)
        if document is None:
            self._send_json_response({'message': 'Not a KOReader highlights note'}, 400)
            return
        self._send_json_response(note_json(self.sync_storage.save_note(user, None, document, **fields)))

    def note_update(self) -> None:
        """PUT /index.php/apps/notes/api/v1/notes/{id} {title, category, content}: a book exported again."""
        user = self.request.opds_user
        previous = self.sync_storage.get_note(user, int(self._param('id')))
        if not previous:
            self._send_json_response({'message': 'Note not found'}, 404)
            return
        fields = self._note_body()
        if fields is None:
            return
        document = self._import(user, fields['content'], previous)
        if document is None:
            self._send_json_response({'message': 'Not a KOReader highlights note'}, 400)
            return
        self._send_json_response(note_json(self.sync_storage.save_note(user, previous['id'], document, **fields)))


__all__ = ['NextcloudNotesController', 'parse_note', 'strip_formatting']
