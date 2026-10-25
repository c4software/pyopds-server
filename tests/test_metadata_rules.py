import contextlib
import importlib
import io
import os
import sys
import tempfile
import unittest
from unittest import mock
import support  # noqa: F401  (temporary library database, before any library is opened)

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from controllers import metadata_rules  # noqa: E402
from controllers.metadata_rules import MetadataConfig, normalize_author_name, normalize_series_name  # noqa: E402
from tests.test_opds import create_rich_epub  # noqa: E402


class TestMetadataRules(unittest.TestCase):
    """Author and series normalization (metadata.toml)."""

    def test_author_names(self):
        config = MetadataConfig()
        for raw, expected in [
            ('King,Stephen', 'Stephen King'),
            ('King, Stephen', 'Stephen King'),
            ('STEPHEN KING', 'Stephen King'),
            ('  Stephen   King ', 'Stephen King'),
            ('Clive Cussler (Auteur)', 'Clive Cussler'),
            ('Zimmer Bradley,Marion', 'Marion Zimmer Bradley'),
            ('JEAN-CHRISTOPHE GRANGÉ', 'Jean-Christophe Grangé'),
            ('Lois McMaster Bujold', 'Lois McMaster Bujold'),
            ('JACQ Christian', 'JACQ Christian'),
            ('Dupont, Durand, Martin', 'Dupont, Durand, Martin'),
            ('Unknown', 'Unknown'),
        ]:
            with self.subTest(raw=raw):
                self.assertEqual(normalize_author_name(raw, config), expected)
        self.assertIsNone(normalize_author_name(None, config))
        self.assertEqual(normalize_author_name('King, Stephen', MetadataConfig(reverse_comma=False)), 'King, Stephen')

    def test_author_aliases(self):
        config = MetadataConfig.from_dict({'authors': {'aliases': {
            'Robin Hobb': ['Hobb Robin'], 'David Weber': ['Weber David'],
        }}})
        self.assertEqual(normalize_author_name('Hobb Robin', config), 'Robin Hobb')
        self.assertEqual(normalize_author_name('HOBB ROBIN', config), 'Robin Hobb')
        # Matched after reordering ("David, Weber" -> "Weber David") or as written
        self.assertEqual(normalize_author_name('David, Weber', config), 'David Weber')
        self.assertEqual(normalize_author_name('Weber,David', config), 'David Weber')

    def test_series(self):
        config = MetadataConfig.from_dict({'series': {
            'ignored': ['Novels'], 'aliases': {"L'Assassin royal": ['Assasin Royal', 'Assassin Royal']},
        }})
        self.assertIsNone(normalize_series_name('Novels', config))
        self.assertIsNone(normalize_series_name('  novels ', config))
        self.assertIsNone(normalize_series_name('', config))
        self.assertEqual(normalize_series_name('Assasin Royal', config), "L'Assassin royal")
        self.assertEqual(normalize_series_name('assassin  royal', config), "L'Assassin royal")
        self.assertEqual(normalize_series_name('La Fondation', config), 'La Fondation')

    def test_invalid_values(self):
        for data in ({'authors': {'reverse_comma': 'yes'}}, {'series': {'ignored': 'Novels'}},
                     {'authors': {'aliases': {'A': 'B'}}}, {'series': []}):
            with self.subTest(data=data), self.assertRaises(ValueError):
                MetadataConfig.from_dict(data)

    def test_custom_config_file_completes_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'metadata.toml')
            with open(path, 'w', encoding='utf-8') as f:
                f.write('[authors.aliases]\n"Robin Hobb" = ["Hobb Robin"]\n'
                        '[series]\nignored = ["camus"]\n[series.aliases]\n"Novels" = ["Romans courts"]\n')
            metadata_rules.get_metadata_config.cache_clear()
            try:
                with mock.patch.dict(os.environ, {'METADATA_CONFIG_PATH': path}):
                    config = metadata_rules.get_metadata_config()
                self.assertTrue(config.reverse_comma)
                self.assertEqual(normalize_author_name('Hobb Robin', config), 'Robin Hobb')
                self.assertIsNone(normalize_series_name('Camus', config))
                # An alias of the custom file wins over a default ignored series
                self.assertEqual(normalize_series_name('Novels', config), 'Novels')
                self.assertIsNone(normalize_series_name('Novel', config))

                metadata_rules.get_metadata_config.cache_clear()
                with open(path, 'w', encoding='utf-8') as f:
                    f.write('[authors]\nreverse_comma = 1\n')
                default = metadata_rules.load_metadata_config(metadata_rules.DEFAULT_METADATA_CONFIG_PATH)
                with mock.patch.dict(os.environ, {'METADATA_CONFIG_PATH': path}), \
                        contextlib.redirect_stdout(io.StringIO()):
                    # Invalid custom file: the default rules still apply
                    self.assertEqual(metadata_rules.get_metadata_config(), default)
            finally:
                metadata_rules.get_metadata_config.cache_clear()


class TestNormalizedLibrary(unittest.TestCase):
    """The rules apply to the metadata read from the EPUBs and merge the author index."""

    @classmethod
    def setUpClass(cls):
        cls._orig_library = os.environ.get('LIBRARY_DIR')
        cls.library_dir = tempfile.TemporaryDirectory()
        os.environ['LIBRARY_DIR'] = cls.library_dir.name
        cls.opds = importlib.reload(importlib.import_module('controllers.opds'))
        root = cls.library_dir.name
        create_rich_epub(os.path.join(root, 'a.epub'), 'Ça', 'King,Stephen', series='Novels', series_index='3')
        create_rich_epub(os.path.join(root, 'b.epub'), 'Shining', 'Stephen King')
        create_rich_epub(os.path.join(root, 'c.epub'), 'Carrie', 'STEPHEN KING')
        create_rich_epub(os.path.join(root, 'd.epub'), 'Le Poète', 'Connelly, Michaël')
        create_rich_epub(os.path.join(root, 'e.epub'), 'Les Égouts de Los Angeles', 'Michael Connelly')
        create_rich_epub(os.path.join(root, 'f.epub'), 'Créance de sang', 'Michael Connelly')
        create_rich_epub(os.path.join(root, 'g.epub'), 'Wonderland Avenue', 'Connelly Michael')

    @classmethod
    def tearDownClass(cls):
        cls.library_dir.cleanup()
        if cls._orig_library is None:
            os.environ.pop('LIBRARY_DIR', None)
        else:
            os.environ['LIBRARY_DIR'] = cls._orig_library
        importlib.reload(importlib.import_module('controllers.opds'))

    def test_epub_metadata_is_normalized(self):
        info = self.opds.BookMetadata.extract_epub_info(os.path.join(self.library_dir.name, 'a.epub'))
        self.assertEqual(info['author'], 'Stephen King')
        self.assertIsNone(info['series'])
        self.assertIsNone(info['series_index'])

    def test_author_index_merges_variants(self):
        scanner = self.opds.BookScanner()
        self.assertEqual(scanner.get_authors_with_counts(), [('Michael Connelly', 4), ('Stephen King', 3)])
        # Any spelling of the author finds all their books
        books, total = scanner.get_books_for_author('Michaël Connelly', 1, 10)
        self.assertEqual(total, 4)
        self.assertEqual(len(scanner.get_indexed_paths('author', 'stephen king')), 3)
        self.assertEqual(len(scanner.get_indexed_paths('author', 'KING Stephen')), 3)

    def test_author_key(self):
        self.assertEqual(metadata_rules.author_key('Hobb Robin'), metadata_rules.author_key('Robin Hobb'))
        self.assertEqual(metadata_rules.author_key('Connelly, Michaël'), 'connelly michael')
        self.assertEqual(metadata_rules.author_key(None), '')


if __name__ == '__main__':
    unittest.main()
