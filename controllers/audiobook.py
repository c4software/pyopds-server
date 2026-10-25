"""Audiobook of the web reader (« Écouter »): the page reads the book aloud a few sentences at a time,
each one synthesized by the text-to-speech server of audiobook.toml (controllers.audiobook_config) just
before it is played. The book is never generated whole, and nothing is kept on the server.

Each reader gives every book its own voice and reading tone (a free prompt: « voix grave, lente, ton
de conteur »), kept with the reader's preferences of the sync database; the books without one use
the defaults of the settings. Signed-in readers only: every request costs the speech server a
generation. Off (404) while no server is configured.
"""
import json

from controllers.audiobook_config import get_audiobook_config
from controllers.http_io import read_json
from controllers.webui import WebUIController

PREFERENCE_PREFIX = 'audiobook:'
VOICE_MAX = 100
INSTRUCTIONS_MAX = 1000
# Body of a speech request: the text (max_chars at most, up to 4 UTF-8 bytes each, JSON-escaped) and the tone
SPEECH_BODY_MAX = 64 * 1024


def book_voice(stored: str | None) -> dict[str, str]:
    """{voice, instructions} kept for a book ('' for the defaults), from its stored preference."""
    try:
        data = json.loads(stored) if stored else {}
    except ValueError:
        data = {}
    if not isinstance(data, dict):
        data = {}
    return {name: data[name] if isinstance(data.get(name), str) else '' for name in ('voice', 'instructions')}


def voice_fields(body: dict) -> dict[str, str] | None:
    """{voice, instructions} of a request body, trimmed (absent: ''); None when a field is invalid."""
    fields = {}
    for name, limit in (('voice', VOICE_MAX), ('instructions', INSTRUCTIONS_MAX)):
        value = body.get(name, '')
        if not isinstance(value, str) or len(value.strip()) > limit:
            return None
        fields[name] = value.strip()
    return fields


class AudiobookController(WebUIController):
    """Settings of the audiobook per book, voices of the speech server and synthesis of the text read."""

    def settings(self) -> None:
        """GET /webui/audiobook?path=<EPUB relative to LIBRARY_DIR>: {voice, instructions} of the book for the
        signed-in reader ('' when it uses the defaults), the `defaults` of the server and the largest text
        of one request (`max_chars`); 404 when the audiobook is not configured."""
        if not self._enabled():
            return
        book = self._book(self._query('path'))
        if book:
            storage, key = book
            self._send_json(self._settings_body(book_voice(storage.get_preference(self.request.opds_user, key))))

    def settings_update(self) -> None:
        """PUT /webui/audiobook {path, voice, instructions}: voice and reading tone of the book for the
        signed-in reader (empty: the defaults); answers like GET."""
        if not self._enabled():
            return
        body = self._read_json()
        if body is None:
            return
        fields = voice_fields(body)
        if fields is None or not isinstance(body.get('path'), str):
            self._send_json({'message': 'Champs invalides'}, 400)
            return
        book = self._book(body['path'])
        if book:
            storage, key = book
            storage.set_preference(self.request.opds_user, key, json.dumps(fields, ensure_ascii=False))
            self._send_json(self._settings_body(fields))

    def voices(self) -> None:
        """GET /webui/audiobook/voices: {voices} offered to the reader (those of the settings, else the
        server's listing; [] when it has none: the reader types a voice name)."""
        if not self._enabled():
            return
        from controllers.speech_client import get_speech_client

        self._send_json({'voices': get_speech_client().voices()})

    def speech(self) -> None:
        """POST /webui/audiobook/speech {text, voice, instructions}: the audio of `text` (max_chars at most)
        read with that voice and tone (empty: the defaults). 502 / 503 / 504 when the speech server fails."""
        if not self._enabled():
            return
        from controllers.speech_client import SpeechError, get_speech_client

        config = get_audiobook_config()
        _, body = read_json(self.request, SPEECH_BODY_MAX)
        fields = voice_fields(body) if isinstance(body, dict) else None
        text = body.get('text') if isinstance(body, dict) else None
        if fields is None or not isinstance(text, str) or not text.strip() or len(text) > config.max_chars:
            self._send_json({'message': 'Champs invalides'}, 400)
            return
        try:
            audio, content_type = get_speech_client().synthesize(
                ' '.join(text.split()), fields['voice'] or config.voice, fields['instructions'] or config.instructions)
        except SpeechError as e:
            self._send_json({'message': e.message}, e.status)
            return
        self._send_body(audio, content_type, 200, {'Cache-Control': 'private, no-store'})

    def _enabled(self) -> bool:
        """Whether a speech server is configured; answers 404 otherwise."""
        if get_audiobook_config().enabled:
            return True
        self._send_json({'message': 'Livre audio non configuré'}, 404)
        return False

    def _book(self, relative: str) -> tuple | None:
        """(sync storage, preference key) of a library EPUB for the signed-in reader; answers 404 otherwise.
        The key names the reader's document of the EPUB, so that a book moved in the library keeps its voice."""
        book = self._library_book(relative)
        if not book:
            return None
        storage, document, epub, relative = book
        document = document or storage.epub_document(self.request.opds_user, epub, relative)
        return storage, PREFERENCE_PREFIX + document

    @staticmethod
    def _settings_body(fields: dict[str, str]) -> dict:
        config = get_audiobook_config()
        return {**fields, 'defaults': {'voice': config.voice, 'instructions': config.instructions},
                'max_chars': config.max_chars}


__all__ = ['AudiobookController', 'book_voice', 'voice_fields']
