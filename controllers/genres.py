"""Normalize EPUB subjects (dc:subject) into browsable genres."""
import re
import unicodedata

# Genres listed in the catalog need at least this many books (hides series names and typos)
MIN_GENRE_BOOKS = 3

# Normalized key -> displayed genre: merges spelling variants of the same genre
GENRE_ALIASES = {
    'sf': 'Science-fiction',
    'science fiction': 'Science-fiction',
    'sciencefiction': 'Science-fiction',
    'science fiction fantasy': 'Science-fiction',
    'policier': 'Policier',
    'policiers': 'Policier',
    'polar': 'Policier',
    'polars': 'Policier',
    'roman policier': 'Policier',
    'romans policiers': 'Policier',
    'crime': 'Policier',
    'mystery': 'Policier',
    'detective': 'Policier',
    'fantasy': 'Fantasy',
    'heroic fantasy': 'Heroic fantasy',
    'fr scifi': 'Science-fiction',
    'anticipation': 'Science-fiction',
    'space opera': 'Science-fiction',
    'policier crime mystere': 'Policier',
    'policier mystere': 'Policier',
    'policier thriller': 'Policier',
    'heroique fantaisie': 'Heroic fantasy',
    'fr heroique': 'Heroic fantasy',
    'fantastique': 'Fantastique',
    'roman fantastique': 'Fantastique',
    'fantastique urban': 'Urban fantasy',
    'urban': 'Urban fantasy',
    'urban fantasy': 'Urban fantasy',
    'terreur': 'Horreur',
    'roman historique': 'Roman historique',
    'romans historiques': 'Roman historique',
    'historique': 'Roman historique',
    'recit historique': 'Roman historique',
    'historical fiction': 'Roman historique',
    'action aventure': 'Aventure',
    'autobiographie': 'Biographie',
    'litterature francaise': 'Littérature française',
    'litt francaise': 'Littérature française',
    'lit fr': 'Littérature française',
    'french fiction': 'Littérature française',
    'litterature etrangere': 'Littérature étrangère',
    'litt etrangere': 'Littérature étrangère',
    'litterature americaine': 'Littérature américaine',
    'auteur e americain e': 'Littérature américaine',
    'policier historique': 'Policier historique',
    'jeunesse': 'Jeunesse',
    'juvenile fiction': 'Jeunesse',
    'horreur': 'Horreur',
    'horror': 'Horreur',
    'thriller': 'Thriller',
    'thrillers': 'Thriller',
    'humour': 'Humour',
    'humor': 'Humour',
    'nouvelles': 'Nouvelles',
    'short stories': 'Nouvelles',
    'aventure': 'Aventure',
    'aventures': 'Aventure',
    'adventure': 'Aventure',
    'roman noir': 'Roman noir',
    'bit lit': 'Bit-lit',
    'bitlit': 'Bit-lit',
    'romance': 'Romance',
    'sentimental': 'Romance',
    'espionnage': 'Espionnage',
    'biographie': 'Biographie',
    'biography': 'Biographie',
}

# Subjects carrying no genre information
IGNORED_GENRES = {
    'fiction', 'roman', 'romans', 'general', 'generalites', 'litterature', 'literature',
    'ebook', 'ebooks', 'epub', 'livre', 'livres', 'book', 'books', 'french', 'francais',
    'non classifiable', 'divers', 'autre', 'autres', 'unknown', 'inconnu',
    # Reading states and library bookkeeping
    'lu', 'non lu', 'a lire', 'fait maison', 'readers', 'fr roman',
}


def genre_key(value: str) -> str:
    """Comparison key of a genre: lowercase, no accents, words separated by single spaces."""
    without_accents = unicodedata.normalize('NFKD', value).encode('ascii', 'ignore').decode('ascii')
    return ' '.join(re.sub(r'[^a-z0-9]+', ' ', without_accents.lower()).split())


def normalize_genres(subjects: list[str], series: str | None = None) -> list[tuple[str, str]]:
    """Turn raw dc:subject values into unique (key, displayed name) genres.

    Subjects holding several values ("Fantasy, Aventure") are split, technical
    tags ("lang:fr", "state:toread") are dropped except "genre:..." ones, and
    spelling variants are merged through GENRE_ALIASES. A subject equal to the
    book's series (often added as a tag by Calibre) is not a genre.
    """
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
            if not key or key in IGNORED_GENRES or key.isdigit() or key == series_key:
                continue
            display = GENRE_ALIASES.get(key)
            if display:
                key = genre_key(display)
            else:
                display = value[0].upper() + value[1:]
            genres.setdefault(key, display)
    return list(genres.items())


__all__ = [
    'GENRE_ALIASES',
    'MIN_GENRE_BOOKS',
    'genre_key',
    'normalize_genres',
]
