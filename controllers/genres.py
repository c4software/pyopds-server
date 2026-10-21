"""Normalize EPUB subjects (dc:subject) into browsable genres.

The correspondence between subjects and genres is read from a TOML file:
`genres.toml` at the project root, or the file named by GENRES_CONFIG_PATH.
"""
import functools
import os
import re
import tomllib
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_GENRES_CONFIG_PATH = Path(__file__).resolve().parent.parent / 'genres.toml'


def genre_key(value: str) -> str:
    """Comparison key of a genre: lowercase, no accents, words separated by single spaces."""
    without_accents = unicodedata.normalize('NFKD', value).encode('ascii', 'ignore').decode('ascii')
    return ' '.join(re.sub(r'[^a-z0-9]+', ' ', without_accents.lower()).split())


@dataclass(frozen=True)
class GenreConfig:
    """Genre normalization rules.

    Attributes:
        min_books: Genres listed in the catalog need at least this many books.
        aliases: Subject key -> displayed genre, merging spelling variants.
        ignored: Subject keys carrying no genre information.
    """
    min_books: int = 3
    aliases: dict[str, str] = field(default_factory=dict)
    ignored: frozenset[str] = frozenset()

    @classmethod
    def from_dict(cls, data: dict) -> 'GenreConfig':
        """Build the rules from parsed TOML (`min_books`, `ignored`, `[aliases]` genre = [subjects]).

        Raises:
            ValueError: When a value has the wrong type.
        """
        min_books = data.get('min_books', 3)
        ignored = data.get('ignored', [])
        aliases = data.get('aliases', {})
        if isinstance(min_books, bool) or not isinstance(min_books, int) or min_books < 1:
            raise ValueError('min_books must be a positive integer')
        if not isinstance(ignored, list) or not all(isinstance(v, str) for v in ignored):
            raise ValueError('ignored must be a list of strings')
        if not isinstance(aliases, dict):
            raise ValueError('[aliases] must be a table')
        alias_keys: dict[str, str] = {}
        for display, subjects in aliases.items():
            if not isinstance(subjects, list) or not all(isinstance(v, str) for v in subjects):
                raise ValueError(f'aliases."{display}" must be a list of strings')
            # The displayed name is always an alias of itself
            for subject in [display, *subjects]:
                if key := genre_key(subject):
                    alias_keys[key] = display
        return cls(min_books, alias_keys, frozenset(filter(None, map(genre_key, ignored))))


def load_genre_config(path: str | os.PathLike) -> GenreConfig:
    """Read the genre rules from a TOML file.

    Raises:
        OSError: When the file cannot be read.
        tomllib.TOMLDecodeError, ValueError: When the file is invalid.
    """
    with open(path, 'rb') as f:
        return GenreConfig.from_dict(tomllib.load(f))


@functools.cache
def get_genre_config() -> GenreConfig:
    """Genre rules of the server, loaded once from GENRES_CONFIG_PATH or the default file.

    An unreadable or invalid custom file falls back to the default rules
    instead of preventing the catalog from working.
    """
    path = os.environ.get('GENRES_CONFIG_PATH') or DEFAULT_GENRES_CONFIG_PATH
    try:
        return load_genre_config(path)
    except (OSError, ValueError) as e:  # tomllib.TOMLDecodeError is a ValueError
        print(f"Invalid genre configuration {path}: {e}")
    try:
        return load_genre_config(DEFAULT_GENRES_CONFIG_PATH)
    except (OSError, ValueError) as e:
        print(f"Invalid default genre configuration {DEFAULT_GENRES_CONFIG_PATH}: {e}")
        return GenreConfig()


def normalize_genres(subjects: list[str], series: str | None = None,
                     config: GenreConfig | None = None) -> list[tuple[str, str]]:
    """Turn raw dc:subject values into unique (key, displayed name) genres.

    Subjects holding several values ("Fantasy, Aventure") are split, technical
    tags ("lang:fr", "state:toread") are dropped except "genre:..." ones, and
    spelling variants are merged through the configured aliases. A subject equal
    to the book's series (often added as a tag by Calibre) is not a genre.
    """
    config = config or get_genre_config()
    series_key = genre_key(series) if series else None
    genres: dict[str, str] = {}
    for subject in subjects:
        for raw in re.split(r'[,;]', subject or ''):
            value = raw.strip()
            if ':' in value:
                prefix, _, rest = value.partition(':')
                if genre_key(prefix) != 'genre':
                    continue
                value = rest.strip()
            key = genre_key(value)
            if not key or key in config.ignored or key.isdigit() or key == series_key:
                continue
            display = config.aliases.get(key)
            if display:
                key = genre_key(display)
            else:
                display = value[0].upper() + value[1:]
            genres.setdefault(key, display)
    return list(genres.items())


__all__ = [
    'GenreConfig',
    'genre_key',
    'get_genre_config',
    'load_genre_config',
    'normalize_genres',
]
