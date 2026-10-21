"""Normalize the author and series names read from the EPUBs.

The rules are read from a TOML file: `metadata.toml` at the project root, completed by the file
named by METADATA_CONFIG_PATH. Authors: "Last, First" is turned into "First Last", names written
in capitals or lowercase get title case, a trailing role ("(Auteur)") is dropped, then the
configured aliases merge the remaining variants. Series: ignored names (bogus series added by
metadata tools) are dropped and aliases merge spelling variants.
"""
import functools
import os
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from controllers.genres import genre_key

DEFAULT_METADATA_CONFIG_PATH = Path(__file__).resolve().parent.parent / 'metadata.toml'

# Trailing role of a creator name: "Clive Cussler (Auteur)", "Jean Dupont (trad.)"
ROLE_SUFFIX_RE = re.compile(r'\s*[(\[][^()\[\]]*[)\]]\s*$')


@dataclass(frozen=True)
class MetadataConfig:
    """Author and series normalization rules.

    Attributes:
        reverse_comma: Turn "Last, First" (a single comma) into "First Last".
        author_aliases: Author key -> displayed author.
        series_aliases: Series key -> displayed series.
        ignored_series: Series keys that are not real series.
    """
    reverse_comma: bool = True
    author_aliases: dict[str, str] = field(default_factory=dict)
    series_aliases: dict[str, str] = field(default_factory=dict)
    ignored_series: frozenset[str] = frozenset()

    @staticmethod
    def _aliases(table: object, name: str) -> dict[str, str]:
        """{key of each variant and of the displayed name: displayed name} of an aliases table."""
        if not isinstance(table, dict):
            raise ValueError(f'[{name}] must be a table')
        keys: dict[str, str] = {}
        for display, variants in table.items():
            if not isinstance(variants, list) or not all(isinstance(v, str) for v in variants):
                raise ValueError(f'{name}."{display}" must be a list of strings')
            for variant in [display, *variants]:
                if key := genre_key(variant):
                    keys[key] = display
        return keys

    @classmethod
    def from_dict(cls, data: dict, base: 'MetadataConfig | None' = None) -> 'MetadataConfig':
        """Build the rules from parsed TOML (`[authors]` reverse_comma and `[authors.aliases]`,
        `[series]` ignored and `[series.aliases]`).

        With `base`, the TOML completes those rules: `reverse_comma` overrides the base value,
        aliases and ignored series are added and win over the base ones.

        Raises:
            ValueError: When a value has the wrong type.
        """
        base = base or cls()
        authors = data.get('authors', {})
        series = data.get('series', {})
        if not isinstance(authors, dict) or not isinstance(series, dict):
            raise ValueError('[authors] and [series] must be tables')
        reverse_comma = authors.get('reverse_comma', base.reverse_comma)
        if not isinstance(reverse_comma, bool):
            raise ValueError('authors.reverse_comma must be true or false')
        ignored = series.get('ignored', [])
        if not isinstance(ignored, list) or not all(isinstance(v, str) for v in ignored):
            raise ValueError('series.ignored must be a list of strings')
        author_aliases = cls._aliases(authors.get('aliases', {}), 'authors.aliases')
        series_aliases = cls._aliases(series.get('aliases', {}), 'series.aliases')
        ignored_keys = frozenset(filter(None, map(genre_key, ignored)))
        return cls(
            reverse_comma,
            base.author_aliases | author_aliases,
            {k: v for k, v in base.series_aliases.items() if k not in ignored_keys} | series_aliases,
            (base.ignored_series - series_aliases.keys()) | ignored_keys,
        )


def load_metadata_config(path: str | os.PathLike, base: MetadataConfig | None = None) -> MetadataConfig:
    """Read the author and series rules from a TOML file, completing `base` when given.

    Raises:
        OSError: When the file cannot be read.
        tomllib.TOMLDecodeError, ValueError: When the file is invalid.
    """
    with open(path, 'rb') as f:
        return MetadataConfig.from_dict(tomllib.load(f), base)


@functools.cache
def get_metadata_config() -> MetadataConfig:
    """Rules of the server, loaded once: the default file completed by METADATA_CONFIG_PATH.

    An unreadable or invalid file is skipped (default rules, or none) instead of preventing the
    catalog from working.
    """
    try:
        config = load_metadata_config(DEFAULT_METADATA_CONFIG_PATH)
    except (OSError, ValueError) as e:  # tomllib.TOMLDecodeError is a ValueError
        print(f"Invalid default metadata configuration {DEFAULT_METADATA_CONFIG_PATH}: {e}")
        config = MetadataConfig()
    path = os.environ.get('METADATA_CONFIG_PATH')
    if not path:
        return config
    try:
        return load_metadata_config(path, config)
    except (OSError, ValueError) as e:
        print(f"Invalid metadata configuration {path}: {e}")
        return config


def _fix_case(name: str) -> str:
    """Title case for a name written all in capitals or all in lowercase, unchanged otherwise."""
    letters = [c for c in name if c.isalpha()]
    if letters and (all(c.isupper() for c in letters) or all(c.islower() for c in letters)):
        return re.sub(r"[^\W\d_]+", lambda m: m.group(0).capitalize(), name)
    return name


def normalize_author_name(author: str | None, config: MetadataConfig | None = None) -> str | None:
    """Displayed name of an EPUB creator: "King,Stephen", "STEPHEN KING" -> "Stephen King".

    The aliases are looked up on the raw name first, then on the reordered one.
    """
    if not author:
        return author
    config = config or get_metadata_config()
    name = ' '.join(ROLE_SUFFIX_RE.sub('', author).split()) or ' '.join(author.split())
    if alias := config.author_aliases.get(genre_key(name)):
        return alias
    if config.reverse_comma and name.count(',') == 1:
        last, first = (part.strip() for part in name.split(','))
        if last and first:
            name = f'{first} {last}'
    name = _fix_case(name)
    return config.author_aliases.get(genre_key(name), name)


def author_key(author: str | None) -> str:
    """Comparison key of an author, independent of case, accents and word order
    ("Hobb Robin" == "Robin Hobb"); '' for a missing name."""
    return ' '.join(sorted(genre_key(normalize_author_name(author) or '').split()))


def normalize_series_name(series: str | None, config: MetadataConfig | None = None) -> str | None:
    """Displayed name of a series, None when it is an ignored (bogus) series."""
    if not series:
        return None
    config = config or get_metadata_config()
    name = ' '.join(series.split())
    key = genre_key(name)
    if not key or key in config.ignored_series:
        return None
    return config.series_aliases.get(key, name)


__all__ = [
    'MetadataConfig',
    'author_key',
    'get_metadata_config',
    'load_metadata_config',
    'normalize_author_name',
    'normalize_series_name',
]
