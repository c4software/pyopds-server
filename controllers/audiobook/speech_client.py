"""Client of the text-to-speech server of the audiobook (OpenAI API: POST /v1/audio/speech, and the
GET /v1/audio/voices?model=… listing of llm-proxy and of local model servers).

The requests block the thread of the reader's request (the server answers each request in its own
thread); at most `concurrency` of them reach the server at the same time, for all the readers. A cloned
voice of the settings is read by the cloning model, its recording sent with every request (base64 WAV
`reference_audio` and its transcript `reference_text`, the fields of Qwen3-TTS "Base" servers).
"""
import base64
import json
import threading
import urllib.error
import urllib.parse
import urllib.request

from controllers.audiobook.config import RESPONSE_FORMATS, AudiobookConfig, ClonedVoice, get_audiobook_config

# Largest audio answer accepted (a few sentences weigh a few hundred KB, even in WAV)
AUDIO_MAX_BYTES = 32 * 1024 * 1024
VOICES_MAX_BYTES = 256 * 1024
# Largest reference recording of a cloned voice (a few seconds of speech)
REFERENCE_MAX_BYTES = 8 * 1024 * 1024
ERROR_MESSAGE_MAX = 300
USER_AGENT = 'PyOPDS-Server'


class SpeechError(Exception):
    """A request to the text-to-speech server failed: `status` is the HTTP status to answer the reader."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def error_message(raw: bytes) -> str:
    """Readable message of an error answer of the server ({"error": {"message"}}, {"message"}, or its text)."""
    text = raw.decode('utf-8', 'replace').strip()
    try:
        data = json.loads(text)
    except ValueError:
        return text[:ERROR_MESSAGE_MAX]
    if isinstance(data, dict):
        error = data.get('error')
        message = error.get('message') if isinstance(error, dict) else error if isinstance(error, str) else data.get('message') or data.get('detail')
        if isinstance(message, str):
            return message[:ERROR_MESSAGE_MAX]
    return text[:ERROR_MESSAGE_MAX]


def voice_names(data: object) -> list[str]:
    """Voices of a listing: {"data": [{"id"}]} (llm-proxy), {"voices": […]}, or a list, of names or of
    objects with an id or a name."""
    if isinstance(data, dict):
        data = data.get('data', data.get('voices'))
    if not isinstance(data, list):
        return []
    names = []
    for item in data:
        name = item.get('id') or item.get('name') if isinstance(item, dict) else item
        if isinstance(name, str) and name.strip() and name.strip() not in names:
            names.append(name.strip())
    return names


class SpeechClient:
    """Requests of the audiobook to the configured text-to-speech server."""

    def __init__(self, config: AudiobookConfig):
        self.config = config
        self._slots = threading.BoundedSemaphore(config.concurrency)
        # Base64 recordings of the cloned voices, by path: (modification time, base64)
        self._references: dict[str, tuple[float, str]] = {}
        self._references_lock = threading.Lock()

    def speech_body(self, text: str, voice: str, instructions: str) -> dict:
        """JSON body of /v1/audio/speech: the model, the text, the voice and the tone (when the server takes one);
        a cloned voice goes to the cloning model with its recording and transcript (no voice name nor tone).

        Raises:
            SpeechError: The recording of a cloned voice cannot be read (500).
        """
        body = {**self.config.extra, 'model': self.config.model, 'input': text,
                'response_format': self.config.response_format}
        if clone := self.config.clones.get(voice):
            body.update(model=self.config.clone_model, reference_audio=self._reference(voice, clone), reference_text=clone.text)
            return body
        if voice:
            body['voice'] = voice
        if instructions and self.config.instructions_field:
            body[self.config.instructions_field] = instructions
        return body

    def synthesize(self, text: str, voice: str, instructions: str) -> tuple[bytes, str]:
        """(audio, Content-Type) of `text` read with `voice` and the reading tone `instructions`.

        Raises:
            SpeechError: Server busy (503), unreachable or in error (502), too slow (504).
        """
        body = json.dumps(self.speech_body(text, voice, instructions)).encode('utf-8')
        request = urllib.request.Request(self.config.url + '/v1/audio/speech', data=body, method='POST',
                                         headers={**self._headers(), 'Content-Type': 'application/json'})
        if not self._slots.acquire(timeout=self.config.timeout):
            raise SpeechError(503, 'Synthèse vocale occupée, réessayez dans un instant')
        try:
            data, content_type = self._send(request, AUDIO_MAX_BYTES)
        finally:
            self._slots.release()
        if not content_type.startswith('audio/'):
            content_type = RESPONSE_FORMATS[self.config.response_format]
        return data, content_type

    def voices(self) -> list[str]:
        """Voices offered to the reader: the cloned ones first, then those of the settings, else the model's
        listing on the server (none when it has none)."""
        clones = list(self.config.clones)
        if self.config.voices:
            return clones + [v for v in self.config.voices if v not in clones]
        query = urllib.parse.urlencode({'model': self.config.model})
        request = urllib.request.Request(f'{self.config.url}/v1/audio/voices?{query}', headers=self._headers())
        try:
            data, _ = self._send(request, VOICES_MAX_BYTES)
            listed = voice_names(json.loads(data.decode('utf-8')))
        except (SpeechError, ValueError):
            listed = []
        return clones + [v for v in listed if v not in clones]

    def _reference(self, name: str, clone: ClonedVoice) -> str:
        """Base64 recording of a cloned voice, read again only when the file changes."""
        try:
            mtime = clone.audio.stat().st_mtime
            with self._references_lock:
                cached = self._references.get(str(clone.audio))
                if cached and cached[0] == mtime:
                    return cached[1]
            if clone.audio.stat().st_size > REFERENCE_MAX_BYTES:
                raise OSError('file too large')
            encoded = base64.b64encode(clone.audio.read_bytes()).decode('ascii')
        except OSError as e:
            raise SpeechError(500, f'Voix clonée « {name} » illisible : {e}')
        with self._references_lock:
            self._references[str(clone.audio)] = (mtime, encoded)
        return encoded

    def _headers(self) -> dict[str, str]:
        headers = {'User-Agent': USER_AGENT, 'Accept': 'audio/*, application/json'}
        if self.config.api_key:
            headers['Authorization'] = f'Bearer {self.config.api_key}'
        return headers

    def _send(self, request: urllib.request.Request, max_bytes: int) -> tuple[bytes, str]:
        """(body, Content-Type) of a successful answer, read up to `max_bytes`."""
        try:
            with urllib.request.urlopen(request, timeout=self.config.timeout) as response:
                data = response.read(max_bytes + 1)
                content_type = (response.headers.get('Content-Type') or '').split(';')[0].strip().lower()
        except urllib.error.HTTPError as e:
            with e:
                message = error_message(e.read(VOICES_MAX_BYTES))
            raise SpeechError(502, f'Synthèse vocale en erreur ({e.code}) : {message}' if message else f'Synthèse vocale en erreur ({e.code})')
        except TimeoutError:
            raise SpeechError(504, 'Synthèse vocale trop lente')
        except (urllib.error.URLError, OSError) as e:
            reason = getattr(e, 'reason', e)
            if isinstance(reason, TimeoutError):
                raise SpeechError(504, 'Synthèse vocale trop lente')
            raise SpeechError(502, f'Synthèse vocale injoignable : {reason}')
        if len(data) > max_bytes:
            raise SpeechError(502, 'Réponse de la synthèse vocale trop grande')
        return data, content_type


_client: SpeechClient | None = None
_client_lock = threading.Lock()


def get_speech_client() -> SpeechClient:
    """Client of the server's settings, created once even by concurrent requests (its concurrency limit is
    shared by every request)."""
    global _client
    with _client_lock:
        if _client is None:
            _client = SpeechClient(get_audiobook_config())
        return _client


__all__ = ['SpeechClient', 'SpeechError', 'error_message', 'get_speech_client', 'voice_names']
