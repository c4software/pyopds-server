import contextlib
import io
import os
import tempfile
import unittest

from support import create_chapter_epub, use_library_dir  # (first: test database, project on sys.path)


from controllers.tools import duplicates  # noqa: E402
from controllers.library.index import koreader_partial_md5  # noqa: E402
from controllers.sync.storage import SyncStorage  # noqa: E402


def create_epub(path, title, author, text='Il était une fois. ' * 2000, extra=''):
    """EPUB with one chapter of `text` (`extra` changes the bytes, not the length)."""
    create_chapter_epub(path, title, author, chapters=1, bodies={1: f'<p>{text}</p><!-- {extra} -->'})


class TestDuplicates(unittest.TestCase):
    """Duplicate detection and removal of the `python -m controllers.tools.duplicates` command."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.library = os.path.join(self.tmp.name, 'books')
        os.makedirs(self.library)
        use_library_dir(self, self.library)
        self.addCleanup(self.tmp.cleanup)

    def path(self, relative):
        return os.path.join(self.library, relative)

    def scan(self, **kwargs):
        return duplicates.find_duplicates(duplicates.load_books(), **kwargs)

    def test_normalized_keys(self):
        def key(title, author):
            return duplicates.book_key(duplicates.BookFile('x.epub', 'x.epub', 0, 0, title, author))
        self.assertEqual(key("L'Odyssée  de l'Espace !", 'Asimov, Isaac'), ('l odyssee de l espace', 'asimov isaac', ''))
        self.assertEqual(key('Livre', 'Isaac Asimov'), key('LIVRE', 'Asimov, Isaac'))
        self.assertEqual(key('Livre', 'Unknown')[1], '')

    def test_identical_files(self):
        create_epub(self.path('2025/Livre.epub'), 'Livre', 'Auteur')
        os.makedirs(self.path('2026'))
        with open(self.path('2025/Livre.epub'), 'rb') as src, open(self.path('2026/Copie.epub'), 'wb') as dst:
            dst.write(src.read())
        groups = self.scan()
        self.assertEqual([group.kind for group in groups], ['identical'])
        self.assertEqual(len(groups[0].duplicates), 1)

    def test_same_book_keeps_readable_name(self):
        create_epub(self.path('A/Odyssée - Clarke.epub'), "L'Odyssée", 'Clarke, Arthur', extra='a')
        create_epub(self.path('B/Clarke - Odyss‚e.epub'), "L'Odyssée", 'Arthur Clarke', extra='b')
        create_epub(self.path('C/Autre.epub'), 'Autre livre', 'Arthur Clarke')
        groups = self.scan()
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0].kind, 'same_book')
        self.assertEqual(groups[0].keep.relative, os.path.join('A', 'Odyssée - Clarke.epub'))
        self.assertEqual(self.scan(identical_only=True), [])

    def test_different_lengths_to_check(self):
        create_epub(self.path('Ca T1.epub'), 'Ça', 'Stephen King', text='Mot. ' * 20000)
        create_epub(self.path('Ca T2.epub'), 'Ça', 'Stephen King', text='Mot. ' * 9000)
        groups = self.scan()
        self.assertEqual([group.kind for group in groups], ['to_check'])
        duplicates.remove_duplicates(groups)
        self.assertTrue(os.path.exists(self.path('Ca T1.epub')) and os.path.exists(self.path('Ca T2.epub')))

    def test_synced_file_is_kept(self):
        create_epub(self.path('Bon/Livre - Auteur.epub'), 'Livre', 'Auteur', extra='a')
        create_epub(self.path('Lu/xx.epub'), 'Livre', 'Auteur', extra='b')
        db_path = os.path.join(self.tmp.name, 'sync.db')
        storage = SyncStorage(db_path)
        with storage._get_connection() as conn:
            conn.execute("INSERT INTO progress (user, document, device_id, percentage, progress, updated_at) "
                         "VALUES ('u', ?, 'd', 0.5, '', 0)", (koreader_partial_md5(self.path('Lu/xx.epub')),))
        books = duplicates.load_books()
        duplicates.mark_synced(books, db_path)
        groups = duplicates.find_duplicates(books)
        self.assertEqual(groups[0].keep.relative, os.path.join('Lu', 'xx.epub'))

    def test_move_and_delete(self):
        create_epub(self.path('A/Livre.epub'), 'Livre', 'Auteur', extra='a')
        create_epub(self.path('B/Sous/Livre.epub'), 'Livre', 'Auteur', extra='bb')
        trash = os.path.join(self.tmp.name, 'trash')
        groups = self.scan()
        moved = groups[0].duplicates[0].relative
        self.assertEqual(duplicates.remove_duplicates(groups, trash), [])
        self.assertTrue(os.path.isfile(os.path.join(trash, moved)))
        self.assertFalse(os.path.exists(self.path(os.path.dirname(moved))))  # empty folders removed
        self.assertTrue(os.path.isdir(self.library))
        self.assertEqual(self.scan(), [])

    def test_cli_refuses_trash_inside_library(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            duplicates.main(['--move-to', self.path('doublons')])


if __name__ == '__main__':
    unittest.main()
