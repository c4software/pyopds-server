"""Settings of the audiobook (« Écouter » in the web reader): the text-to-speech server, its model and
the default voice and reading tone.

The settings are read from a TOML file: `audiobook.toml` at the project root, completed by the file
named by AUDIOBOOK_CONFIG_PATH (same keys, its values win). String values may read the environment
("${TTS_API_KEY}"), so that a key stays out of the file. The audiobook is off while the server URL
or the model is empty.

Cloned voices (`[clones.<name>]`: a WAV recording and its transcript) are read by a voice cloning model
(`clone_model`, Qwen3-TTS "Base"); their reference recordings can be made from a written description by a
voice design model (`design_model`, python -m controllers.tools.voice_design).
"""
import functools
import os
import re
import tomllib
from dataclasses import dataclass, field, fields, replace
from pathlib import Path

DEFAULT_AUDIOBOOK_CONFIG_PATH = Path(__file__).resolve().parents[2] / 'audiobook.toml'

ENV_REF_RE = re.compile(r'\$\{([A-Za-z_][A-Za-z0-9_]*)\}')
# Formats the browsers play (format asked for -> Content-Type when the server sends none)
RESPONSE_FORMATS = {'wav': 'audio/wav', 'mp3': 'audio/mpeg', 'opus': 'audio/ogg', 'aac': 'audio/aac', 'flac': 'audio/flac'}
# Bounds of the numeric settings
MAX_CHARS_RANGE = (50, 4000)
TIMEOUT_RANGE = (1, 600)
CONCURRENCY_RANGE = (1, 32)


@dataclass(frozen=True)
class ClonedVoice:
    """Voice read by the cloning model from a reference recording.

    Attributes:
        audio: WAV recording of the voice (a few seconds of speech).
        text: Transcript of the recording, word for word.
    """
    audio: Path
    text: str


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
        clone_model: Voice cloning model, for the cloned voices.
        design_model: Voice design model (python -m controllers.tools.voice_design).
        clones: Cloned voices by name, offered with the voices of the model.
    """
    url: str = ''
    model: str = ''
    api_key: str = ''
    voice: str = ''
    instructions: str = ''
    response_format: str = 'wav'
    instructions_field: str = 'instructions'
    voices: tuple[str, ...] = ()
    max_chars: int = 1000
    timeout: int = 120
    concurrency: int = 2
    extra: dict = field(default_factory=dict)
    clone_model: str = ''
    design_model: str = ''
    clones: dict[str, ClonedVoice] = field(default_factory=dict)

    @property
    def enabled(self) -> bool:
        """Whether the audiobook is offered: a server and a model are configured."""
        return bool(self.url and self.model)

    @classmethod
    def from_dict(cls, data: dict, base: 'AudiobookConfig | None' = None, folder: Path | None = None) -> 'AudiobookConfig':
        """Build the settings from parsed TOML, completing `base` (its values are kept for the missing keys);
        relative paths of the cloned voices are read from `folder` (the TOML file's).

        Raises:
            ValueError: When a value has the wrong type or is out of bounds.
        """
        base = base or cls()
        known = {f.name for f in fields(cls)}
        if unknown := sorted(set(data) - known):
            raise ValueError(f'unknown setting(s): {", ".join(unknown)}')
        changes: dict = {}
        for name in ('url', 'model', 'api_key', 'voice', 'instructions', 'response_format', 'instructions_field',
                     'clone_model', 'design_model'):
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
        if 'clones' in data:
            changes['clones'] = base.clones | cls._clones(data['clones'], folder or Path.cwd())
        config = replace(base, **changes)
        if config.clones and not config.clone_model:
            raise ValueError('clone_model is required by [clones]')
        return config

    @staticmethod
    def _clones(table: object, folder: Path) -> dict[str, ClonedVoice]:
        """Cloned voices of a `[clones]` table: {name: {audio (path, relative to `folder`), text}}."""
        if not isinstance(table, dict):
            raise ValueError('[clones] must be a table')
        clones = {}
        for name, voice in table.items():
            if not name.strip() or not isinstance(voice, dict):
                raise ValueError(f'clones.{name} must be a table')
            audio, text = voice.get('audio'), voice.get('text')
            if not isinstance(audio, str) or not audio.strip() or not isinstance(text, str) or not text.strip():
                raise ValueError(f'clones.{name} needs an audio path and its text')
            if unknown := sorted(set(voice) - {'audio', 'text'}):
                raise ValueError(f'clones.{name}: unknown setting(s): {", ".join(unknown)}')
            clones[name.strip()] = ClonedVoice(folder / expand_env(audio).strip(), ' '.join(text.split()))
        return clones


def load_audiobook_config(path: str | os.PathLike, base: AudiobookConfig | None = None) -> AudiobookConfig:
    """Read the audiobook settings from a TOML file, completing `base` when given.

    Raises:
        OSError: When the file cannot be read.
        tomllib.TOMLDecodeError, ValueError: When the file is invalid.
    """
    with open(path, 'rb') as f:
        return AudiobookConfig.from_dict(tomllib.load(f), base, Path(path).resolve().parent)


def settings_folder() -> Path:
    """Folder of the settings file of AUDIOBOOK_CONFIG_PATH (the working directory without one): the files
    made by the command line tools (voices, audiobooks) are written there."""
    path = os.environ.get('AUDIOBOOK_CONFIG_PATH')
    return Path(path).resolve().parent if path else Path.cwd()


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


__all__ = ['AudiobookConfig', 'ClonedVoice', 'expand_env', 'get_audiobook_config', 'load_audiobook_config', 'settings_folder']
