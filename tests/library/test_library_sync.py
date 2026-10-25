"""Library metadata database: syncs of the EPUB folder."""
import importlib
import os
import tempfile
import unittest
from unittest import mock
from support import create_epub  # noqa: F401  (first: test database, project on sys.path)


class TestLibrarySync(unittest.TestCase):
    """Library metadata stored in SQLite (controllers/library/store.py): one sync rule, restarts without reading EPUBs."""

    def setUp(self):
        self.library_dir = tempfile.TemporaryDirectory()
        self.db_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.library_dir.cleanup)
        self.addCleanup(self.db_dir.cleanup)
        self.library = importlib.import_module('controllers.library.store')
        self.opds = importlib.import_module('controllers.catalog.opds')
        self.db_path = os.path.join(self.db_dir.name, 'library.db')
        create_epub(self.path('a.epub'), 'Alpha', 'Author One', date='2020-01-01')
        create_epub(self.path('sub/b.epub'), 'Beta', 'Author Two')

    def path(self, name):
        path = os.path.join(self.library_dir.name, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        return path

    def open_library(self):
        return self.library.Library(self.library_dir.name, self.db_path)

    def test_sync_reads_only_new_modified_and_removed_books(self):
        library = self.open_library()
        snapshot = library.snapshot
        self.assertEqual(sorted(book.title for book in snapshot.books.values()), ['Alpha', 'Beta'])
        self.assertEqual(snapshot.books[self.path('a.epub')].info()['year'], '2020')
        # Nothing changed: no EPUB opened, same snapshot
        with mock.patch.object(self.opds.BookMetadata, 'extract_epub_info', side_effect=AssertionError('read again')):
            self.assertEqual(library.sync()['total'], 2)
        self.assertIs(library.snapshot, snapshot)
        # Added, modified (size or date) and removed books
        create_epub(self.path('c.epub'), 'Gamma', 'Author One')
        create_epub(self.path('a.epub'), 'Alpha 2', 'Author One')
        os.utime(self.path('a.epub'), (1_000_000_000, 1_000_000_000))
        os.remove(self.path('sub/b.epub'))
        result = library.sync()
        self.assertEqual((result['added'], result['updated'], result['removed'], result['total']), (1, 1, 1, 2))
        self.assertGreater(library.snapshot.version, snapshot.version)
        self.assertEqual(sorted(book.title for book in library.snapshot.books.values()), ['Alpha 2', 'Gamma'])
        self.assertEqual(library.snapshot.catalog.authors, {'Author One': [self.path('a.epub'), self.path('c.epub')]})

    def test_restart_reads_the_database_not_the_epubs(self):
        self.open_library().sync()
        with mock.patch.object(self.opds.BookMetadata, 'extract_epub_info', side_effect=AssertionError('read again')):
            library = self.open_library()
            self.assertEqual(sorted(book.title for book in library.snapshot.books.values()), ['Alpha', 'Beta'])
            self.assertEqual(library.sync()['added'], 0)
        # Another library in the same database: its books are read again
        other = tempfile.TemporaryDirectory()
        self.addCleanup(other.cleanup)
        create_epub(os.path.join(other.name, 'z.epub'), 'Zeta', 'Author Z')
        library = self.library.Library(other.name, self.db_path)
        self.assertEqual([book.title for book in library.snapshot.books.values()], ['Zeta'])


if __name__ == '__main__':
    unittest.main()
