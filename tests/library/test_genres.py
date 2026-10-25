"""Genres of the library: subject normalization and "By Genre" navigation."""
import contextlib
import http.client
import importlib
import io
import os
import tempfile
import threading
import time
import unittest
import xml.etree.ElementTree as ET
from unittest import mock
from support import ThreadedTCPServer, create_epub  # noqa: F401  (first: test database, project on sys.path)


class TestGenres(unittest.TestCase):
    def test_normalize_genres_merges_variants_and_drops_tags(self):
        genres = importlib.import_module('controllers.library.genres')
        self.assertEqual(
            genres.normalize_genres(['SF', 'Science fiction, Aventure', 'genre:sciencefiction', 'lang:fr',
                                     'state:toread', 'Fiction', 'roman policier; Polar', 'Policier historique']),
            [('science fiction', 'Science-fiction'), ('aventure', 'Aventure'), ('policier', 'Policier'),
             ('policier historique', 'Policier historique')],
        )
        self.assertEqual(genres.normalize_genres(['  ', '1999', 'interest:3']), [])
        self.assertEqual(genres.normalize_genres(['Fantastique', 'fantastique']), [('fantastique', 'Fantastique')])

    def test_custom_genre_config_file(self):
        genres = importlib.import_module('controllers.library.genres')
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'genres.toml')
            with open(path, 'w', encoding='utf-8') as f:
                f.write('min_books = 5\nignored = ["Divers"]\n[aliases]\n"Imaginaire" = ["SF", "Fantasy"]\n')
            config = genres.load_genre_config(path)
            self.assertEqual(config.min_books, 5)
            self.assertEqual(
                genres.normalize_genres(['sf', 'fantasy', 'imaginaire', 'divers', 'Polar'], config=config),
                [('imaginaire', 'Imaginaire'), ('polar', 'Polar')],
            )

            default = genres.load_genre_config(genres.DEFAULT_GENRES_CONFIG_PATH)
            genres.get_genre_config.cache_clear()
            try:
                with mock.patch.dict(os.environ, {'GENRES_CONFIG_PATH': path}):
                    merged = genres.get_genre_config()
                # The custom file completes the default rules and wins over them
                self.assertEqual(merged.min_books, 5)
                self.assertEqual(
                    genres.normalize_genres(['sf', 'Polar', 'divers', 'Fiction'], config=merged),
                    [('imaginaire', 'Imaginaire'), ('policier', 'Policier')],
                )
                self.assertIn('policier', merged.aliases)
                self.assertNotIn('fantasy', merged.ignored)
                genres.get_genre_config.cache_clear()
                with open(path, 'w', encoding='utf-8') as f:
                    f.write('min_books = "trois"\n')
                with mock.patch.dict(os.environ, {'GENRES_CONFIG_PATH': path}), \
                        contextlib.redirect_stdout(io.StringIO()):
                    # Invalid custom file: the default rules still apply
                    self.assertEqual(genres.get_genre_config(), default)
            finally:
                genres.get_genre_config.cache_clear()


class TestGenreCatalog(unittest.TestCase):
    ns = {'atom': 'http://www.w3.org/2005/Atom'}

    @classmethod
    def setUpClass(cls):
        cls._orig_env = {'LIBRARY_DIR': os.environ.get('LIBRARY_DIR'), 'PAGE_SIZE': os.environ.get('PAGE_SIZE')}
        cls.library_dir = tempfile.TemporaryDirectory()
        os.environ['LIBRARY_DIR'] = cls.library_dir.name
        os.environ['PAGE_SIZE'] = '2'
        importlib.reload(importlib.import_module('controllers.catalog.opds'))
        importlib.reload(importlib.import_module('routes'))
        cls.server = importlib.reload(importlib.import_module('server'))
        books = {
            'a': ['SF'], 'b': ['Science-fiction, Aventure'], 'c': ['genre:sciencefiction', 'lang:fr'],
            'd': ['Policier'], 'e': ['polar'], 'f': ['Roman policier'], 'g': ['Lancedragon'], 'h': [],
        }
        for name, subjects in books.items():
            create_epub(os.path.join(cls.library_dir.name, f'{name}.epub'), f'Book {name}', 'Author', subjects=subjects)
        cls.httpd = ThreadedTCPServer(('localhost', 0), cls.server.UnifiedHandler)
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()
        time.sleep(0.1)

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.thread.join(timeout=1)
        cls.library_dir.cleanup()
        for key, value in cls._orig_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        importlib.reload(importlib.import_module('controllers.catalog.opds'))
        importlib.reload(importlib.import_module('server'))

    def _feed(self, path):
        conn = http.client.HTTPConnection('localhost', self.port, timeout=5)
        conn.request('GET', path)
        response = conn.getresponse()
        body = response.read()
        conn.close()
        if response.status != 200:
            return response.status, None
        text = '\n'.join(line for line in body.decode('utf-8').split('\n') if not line.strip().startswith('<?'))
        return response.status, ET.fromstring(text)

    def titles(self, feed):
        return [entry.find('atom:title', self.ns).text for entry in feed.findall('atom:entry', self.ns)]

    def test_root_links_to_genres(self):
        _, feed = self._feed('/opds')
        self.assertIn('By Genre', self.titles(feed))

    def test_genre_catalog_lists_merged_genres(self):
        status, feed = self._feed('/opds/by-genre')
        self.assertEqual(status, 200)
        # Aventure and Lancedragon have fewer than 3 books, lang:fr is a technical tag
        self.assertEqual(self.titles(feed), ['Policier (3 books)', 'Science-fiction (3 books)'])
        hrefs = [entry.find('atom:link', self.ns).get('href') for entry in feed.findall('atom:entry', self.ns)]
        self.assertEqual(hrefs, ['/opds/by-genre/Policier?page=1', '/opds/by-genre/Science-fiction?page=1'])

    def test_genre_books_are_paginated(self):
        status, feed = self._feed('/opds/by-genre/Science-fiction?page=1')
        self.assertEqual(status, 200)
        self.assertEqual(feed.find('atom:title', self.ns).text, 'Genre Science-fiction (Page 1 of 2)')
        self.assertEqual(self.titles(feed), ['Book a', 'Book b'])
        rels = {link.get('rel'): link.get('href') for link in feed.findall('atom:link', self.ns)}
        self.assertEqual(rels['next'], '/opds/by-genre/Science-fiction?page=2')
        self.assertEqual(rels['up'], '/opds/by-genre')
        _, page2 = self._feed('/opds/by-genre/Science-fiction?page=2')
        self.assertEqual(self.titles(page2), ['Book c'])

    def test_genre_lookup_accepts_variants_and_hidden_genres(self):
        _, feed = self._feed('/opds/by-genre/sf?page=1')
        self.assertEqual(self.titles(feed), ['Book a', 'Book b'])
        _, feed = self._feed('/opds/by-genre/Lancedragon')
        self.assertEqual(self.titles(feed), ['Book g'])
        self.assertEqual(self._feed('/opds/by-genre/Inconnu')[0], 404)
