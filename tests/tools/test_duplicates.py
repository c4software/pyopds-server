import contextlib
import io
import os
import tempfile
import unittest
import zipfile
from unittest import mock

import support  # noqa: F401  (first: test database, project on sys.path)


from controllers.tools import duplicates  # noqa: E402
from controllers.library.index import koreader_partial_md5  # noqa: E402
from controllers.sync.storage import SyncStorage  # noqa: E402


def create_epub(path, title, author, text='Il était une fois. ' * 2000, extra=''):
    """Minimal EPUB with one chapter of `text` (`extra` changes the bytes, not the length)."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with zipfile.ZipFile(path, 'w') as zf:
        zf.writestr('mimetype', 'application/epub+zip')
        zf.writestr('META-INF/container.xml', """<?xml version='1.0'?>
<container version='1.0' xmlns='urn:oasis:names:tc:opendocument:xmlns:container'>
    <rootfiles><rootfile full-path='content.opf' media-type='application/oebps-package+xml'/></rootfiles>
</container>""")
        zf.writestr('content.opf', f"""<?xml version='1.0'?>
<package xmlns='http://www.idpf.org/2007/opf' version='3.0'>
    <metadata xmlns:dc='http://purl.org/dc/elements/1.1/'>
        <dc:title>{title}</dc:title><dc:creator>{author}</dc:creator>
    </metadata>
    <manifest><item id='c1' href='c1.xhtml' media-type='application/xhtml+xml'/></manifest>
    <spine><itemref idref='c1'/></spine>
</package>""")
        zf.writestr('c1.xhtml', f'<html><body><p>{text}</p><!-- {extra} --></body></html>')


class TestDuplicates(unittest.TestCase):
    """Duplicate detection and removal of the `python -m controllers.tools.duplicates` command."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.library = os.path.join(self.tmp.name, 'books')
        patcher = mock.patch.object(duplicates, 'LIBRARY_DIR', self.library)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.tmp.cleanup)

    def path(self, relative):
        return os.path.join(self.library, relative)

    def scan(self, **kwargs):
        books = [duplicates.load_book(os.path.join(root, name))
                 for root, _, names in os.walk(self.library) for name in names]
        return duplicates.find_duplicates(books, **kwargs)

    def test_normalized_keys(self):
        self.assertEqual(duplicates.normalize_text("L'Odyssée  de l'Espace !"), 'l odyssee de l espace')
        self.assertEqual(duplicates.author_key('Asimov, Isaac'), duplicates.author_key('Isaac Asimov'))
        self.assertEqual(duplicates.author_key('Unknown'), '')

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
        books = [duplicates.load_book(self.path('Bon/Livre - Auteur.epub')), duplicates.load_book(self.path('Lu/xx.epub'))]
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
