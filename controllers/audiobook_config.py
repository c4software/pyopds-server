"""Settings of the audiobook (« Écouter » in the web reader): the text-to-speech server, its model and
the default voice and reading tone.

The settings are read from a TOML file: `audiobook.toml` at the project root, completed by the file
named by AUDIOBOOK_CONFIG_PATH (same keys, its values win). String values may read the environment
("${TTS_API_KEY}"), so that a key stays out of the file. The audiobook is off while the server URL
or the model is empty.
"""
import functools
import os
import re
import tomllib
from dataclasses import dataclass, field, fields, replace
from pathlib import Path

DEFAULT_AUDIOBOOK_CONFIG_PATH = Path(__file__).resolve().parent.parent / 'audiobook.toml'

ENV_REF_RE = re.compile(r'\$\{([A-Za-z_][A-Za-z0-9_]*)\}')
# Formats the browsers play (format asked for -> Content-Type when the server sends none)
RESPONSE_FORMATS = {'wav': 'audio/wav', 'mp3': 'audio/mpeg', 'opus': 'audio/ogg', 'aac': 'audio/aac', 'flac': 'audio/flac'}
# Bounds of the numeric settings
MAX_CHARS_RANGE = (50, 4000)
TIMEOUT_RANGE = (1, 600)
CONCURRENCY_RANGE = (1, 32)


def expand_env(value: str) -> str:
    """Replace the "${NAME}" references of a setting by the environment variables (empty when unset)."""
    return ENV_REF_RE.sub(lambda match: os.environ.get(match.group(1), ''), value)


@dataclass(frozen=True)
class AudiobookConfig:
    """Text-to-speech server of the audiobook.

    Attributes:
        url: Base URL of an OpenAI-compatible server (/v1/audio/speech appended).
        model: Model of the requests.
        api_key: Bearer token ('' sends no Authorization header).
        voice: Default voice.
        instructions: Default reading tone.
        response_format: Audio format asked for.
        instructions_field: Request field carrying the reading tone ('' never sends it).
        voices: Voices offered to the reader (empty: asked to the server).
        max_chars: Characters sent per request at most.
        timeout: Seconds allowed for one request.
        concurrency: Requests sent to the server at the same time.
        extra: Fields added to every request.
    """
    url: str = ''
    model: str = ''
    api_key: str = ''
    voice: str = ''
    instructions: str = ''
    response_format: str = 'wav'
    instructions_field: str = 'instructions'
    voices: tuple[str, ...] = ()
    max_chars: int = 500
    timeout: int = 120
    concurrency: int = 2
    extra: dict = field(default_factory=dict)

    @property
    def enabled(self) -> bool:
        """Whether the audiobook is offered: a server and a model are configured."""
        return bool(self.url and self.model)

    @classmethod
    def from_dict(cls, data: dict, base: 'AudiobookConfig | None' = None) -> 'AudiobookConfig':
        """Build the settings from parsed TOML, completing `base` (its values are kept for the missing keys).

        Raises:
            ValueError: When a value has the wrong type or is out of bounds.
        """
        base = base or cls()
        known = {f.name for f in fields(cls)}
        if unknown := sorted(set(data) - known):
            raise ValueError(f'unknown setting(s): {", ".join(unknown)}')
        changes: dict = {}
        for name in ('url', 'model', 'api_key', 'voice', 'instructions', 'response_format', 'instructions_field'):
            if name in data:
                if not isinstance(data[name], str):
                    raise ValueError(f'{name} must be a string')
                changes[name] = expand_env(data[name]).strip()
        if 'url' in changes:
            changes['url'] = changes['url'].rstrip('/')
            if changes['url'] and not changes['url'].startswith(('http://', 'https://')):
                raise ValueError('url must start with http:// or https://')
        if changes.get('response_format', base.response_format) not in RESPONSE_FORMATS:
            raise ValueError(f'response_format must be one of {", ".join(RESPONSE_FORMATS)}')
        if 'voices' in data:
            voices = data['voices']
            if not isinstance(voices, list) or not all(isinstance(v, str) and v.strip() for v in voices):
                raise ValueError('voices must be a list of strings')
            changes['voices'] = tuple(v.strip() for v in voices)
        for name, (low, high) in (('max_chars', MAX_CHARS_RANGE), ('timeout', TIMEOUT_RANGE),
                                  ('concurrency', CONCURRENCY_RANGE)):
            if name in data:
                value = data[name]
                if not isinstance(value, int) or isinstance(value, bool) or not low <= value <= high:
                    raise ValueError(f'{name} must be an integer between {low} and {high}')
                changes[name] = value
        if 'extra' in data:
            if not isinstance(data['extra'], dict):
                raise ValueError('[extra] must be a table')
            changes['extra'] = base.extra | data['extra']
        return replace(base, **changes)


def load_audiobook_config(path: str | os.PathLike, base: AudiobookConfig | None = None) -> AudiobookConfig:
    """Read the audiobook settings from a TOML file, completing `base` when given.

    Raises:
        OSError: When the file cannot be read.
        tomllib.TOMLDecodeError, ValueError: When the file is invalid.
    """
    with open(path, 'rb') as f:
        return AudiobookConfig.from_dict(tomllib.load(f), base)


@functools.cache
def get_audiobook_config() -> AudiobookConfig:
    """Settings of the server, loaded once: the default file completed by AUDIOBOOK_CONFIG_PATH.

    An unreadable or invalid file is skipped (the audiobook stays off, or keeps the default settings)
    instead of preventing the server from working.
    """
    try:
        config = load_audiobook_config(DEFAULT_AUDIOBOOK_CONFIG_PATH)
    except (OSError, ValueError) as e:  # tomllib.TOMLDecodeError is a ValueError
        print(f"Invalid default audiobook configuration {DEFAULT_AUDIOBOOK_CONFIG_PATH}: {e}")
        config = AudiobookConfig()
    path = os.environ.get('AUDIOBOOK_CONFIG_PATH')
    if not path:
        return config
    try:
        return load_audiobook_config(path, config)
    except (OSError, ValueError) as e:
        print(f"Invalid audiobook configuration {path}: {e}")
        return config


__all__ = ['AudiobookConfig', 'expand_env', 'get_audiobook_config', 'load_audiobook_config']
