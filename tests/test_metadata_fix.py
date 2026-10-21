import contextlib
import csv
import io
import os
import sys
import tempfile
import unittest
import zipfile
from unittest import mock

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from controllers import metadata_fix  # noqa: E402
from controllers.opds import BookMetadata  # noqa: E402


def create_epub(path, title='', author='', subjects=(), series=None):
    """Minimal EPUB 2 (calibre style) with a mimetype entry first and stored."""
    metadata = ''
    if title:
        metadata += f'<dc:title>{title}</dc:title>'
    if author:
        metadata += f'<dc:creator opf:file-as="X, Y" opf:role="aut">{author}</dc:creator>'
    metadata += ''.join(f'<dc:subject>{subject}</dc:subject>' for subject in subjects)
    if series:
        metadata += f'<meta name="calibre:series" content="{series}"/><meta name="calibre:series_index" content="1"/>'
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(zipfile.ZipInfo('mimetype'), 'application/epub+zip', compress_type=zipfile.ZIP_STORED)
        zf.writestr('META-INF/container.xml', """<?xml version='1.0'?>
<container version='1.0' xmlns='urn:oasis:names:tc:opendocument:xmlns:container'>
    <rootfiles><rootfile full-path='OEBPS/content.opf' media-type='application/oebps-package+xml'/></rootfiles>
</container>""")
        zf.writestr('OEBPS/content.opf', f"""<?xml version='1.0' encoding='utf-8'?>
<package xmlns='http://www.idpf.org/2007/opf' version='2.0'>
    <metadata xmlns:dc='http://purl.org/dc/elements/1.1/' xmlns:opf='http://www.idpf.org/2007/opf'>
        {metadata}
    </metadata>
    <manifest><item id='c1' href='c1.xhtml' media-type='application/xhtml+xml'/></manifest>
    <spine><itemref idref='c1'/></spine>
</package>""")
        zf.writestr('OEBPS/c1.xhtml', '<html><body><p>Texte.</p></body></html>')


class TestMetadataFix(unittest.TestCase):
    """Export, check and application of the `python -m controllers.metadata_fix` CSV."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.library = os.path.join(self.tmp.name, 'books')
        patcher = mock.patch.object(metadata_fix, 'LIBRARY_DIR', self.library)
        patcher.start()
        self.addCleanup(patcher.stop)
        # Author profile: three Asimov books in science fiction
        for n in range(3):
            create_epub(self.path(f'asimov/{n}.epub'), f'Robots {n}', 'Isaac Asimov', ['Science-fiction'])
        for n in range(3):
            create_epub(self.path(f'polars/{n}.epub'), f'Polar {n}', f'Auteur {n}', ['Policier'])
        create_epub(self.path('asimov/fondation.epub'), 'Fondation', 'Asimov, Isaac')
        create_epub(self.path('divers/inconnu.epub'), 'untitled', 'Unknown')
        create_epub(self.path('divers/rare.epub'), 'Ubu Roi', 'Alfred Jarry', ['Drama'])
        create_epub(self.path('divers/serie.epub'), 'Sac d\'os', 'Stephen King', series='Fantastique')

    def path(self, relative):
        return os.path.join(self.library, relative)

    def books(self):
        return [metadata_fix.Book(os.path.join(root, name), BookMetadata.extract_epub_info(os.path.join(root, name)))
                for root, _, names in os.walk(self.library) for name in names]

    def rows(self):
        return {row['chemin']: row for row in metadata_fix.export_rows(self.books())}

    def test_export_lists_problems_and_suggestions(self):
        rows = self.rows()
        self.assertEqual(set(rows), {'asimov/fondation.epub', 'divers/inconnu.epub', 'divers/rare.epub', 'divers/serie.epub'})
        self.assertEqual(rows['asimov/fondation.epub']['probleme'], 'aucun genre')
        self.assertEqual(rows['asimov/fondation.epub']['suggestion'], 'Science-fiction')
        self.assertEqual(rows['divers/inconnu.epub']['probleme'], 'aucun genre ; auteur manquant ; titre à nettoyer')
        self.assertEqual(rows['divers/rare.epub']['probleme'], 'genre non affiché (moins de 3 livres) : Drama')
        self.assertEqual(rows['divers/rare.epub']['suggestion'], '')

        out = os.path.join(self.tmp.name, 'livres.csv')
        metadata_fix.write_csv(out, list(rows.values()))
        with open(out, encoding='utf-8', newline='') as f:
            self.assertEqual(next(csv.reader(f)), metadata_fix.CSV_COLUMNS)

    def test_subjects_to_genres(self):
        self.assertEqual(metadata_fix.subjects_to_genres(['Roman policier français', 'Fiction']), ['Policier'])
        # Whole subjects: "Romans, nouvelles" is not the short stories genre
        self.assertEqual(metadata_fix.subjects_to_genres(['Romans, nouvelles']), [])
        self.assertEqual(metadata_fix.subjects_to_genres(['Fiction / Science Fiction / General']), ['Science-fiction'])

    def test_plan_checks_the_rows(self):
        rows = [
            {'chemin': 'asimov/fondation.epub', 'genres': 'SF; Inventé', 'titre_corrige': 'Fondation', 'auteur_corrige': ''},
            {'chemin': '../dehors.epub', 'genres': 'Policier'},
            {'chemin': 'asimov/absent.epub', 'genres': 'Policier'},
            {'chemin': 'polars/0.epub', 'genres': 'Policier', 'titre_corrige': '', 'auteur_corrige': 'Auteur 0'},
        ]
        changes, warnings = metadata_fix.plan_changes(rows)
        # SF is an alias of Science-fiction; unchanged title, genre already there, same author: no change
        self.assertEqual([(c.book.relative, c.genres, c.title, c.author) for c in changes],
                         [('asimov/fondation.epub', ['Science-fiction'], None, None)])
        self.assertEqual(len(warnings), 3)
        self.assertIn('Inventé', warnings[0])

    def test_suggestions_only_when_accepted(self):
        rows = [{'chemin': 'asimov/fondation.epub', 'suggestion': 'Science-fiction', 'genres': ''}]
        self.assertEqual(metadata_fix.plan_changes(rows)[0], [])
        changes, _ = metadata_fix.plan_changes(rows, accept_suggestions=True)
        self.assertEqual(changes[0].genres, ['Science-fiction'])

    def test_apply_writes_the_opf_and_keeps_the_file_valid(self):
        path = self.path('divers/inconnu.epub')
        os.utime(path, (1_000_000_000, 1_000_000_000))
        csv_path = os.path.join(self.tmp.name, 'livres.csv')
        metadata_fix.write_csv(csv_path, [{
            'chemin': 'divers/inconnu.epub', 'genres': 'Essai', 'titre_corrige': 'The Art of Deception & co',
            'auteur_corrige': 'Kevin Mitnick',
        }])
        with contextlib.redirect_stdout(io.StringIO()) as out, contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(metadata_fix.main(['apply', csv_path]), 0)
        self.assertIn('simulation', out.getvalue())
        self.assertEqual(BookMetadata.extract_epub_info(path)['title'], 'untitled')

        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(metadata_fix.main(['apply', csv_path, '--write']), 0)
        info = BookMetadata.extract_epub_info(path)
        self.assertEqual((info['title'], info['author'], info['genres']), ('The Art of Deception & co', 'Kevin Mitnick', ['Essai']))
        self.assertEqual(os.path.getmtime(path), 1_000_000_000)
        with zipfile.ZipFile(path) as zf:
            first = zf.infolist()[0]
            self.assertEqual((first.filename, first.compress_type), ('mimetype', zipfile.ZIP_STORED))
            self.assertIsNone(zf.testzip())
            # The stale sort name of the old author is dropped
            self.assertNotIn('file-as', zf.read('OEBPS/content.opf').decode())

    def test_genre_named_series_is_dropped(self):
        change = metadata_fix.Change(metadata_fix.Book(self.path('divers/serie.epub'), {}), ['Fantastique', 'Horreur'])
        metadata_fix.rewrite_epub(change)
        info = BookMetadata.extract_epub_info(self.path('divers/serie.epub'))
        self.assertEqual((info['genres'], info['series']), (['Fantastique', 'Horreur'], None))

    def test_unreadable_opf_is_reported(self):
        self.assertIsNone(metadata_fix.edit_opf('<package><metadata/></package>', ['Essai']))
        path = self.path('divers/casse.epub')
        with open(path, 'wb') as f:
            f.write(b'pas un zip')
        changes, warnings = metadata_fix.plan_changes([{'chemin': 'divers/casse.epub', 'genres': 'Essai'}])
        self.assertEqual(changes, [])
        self.assertIn('illisible', warnings[0])


if __name__ == '__main__':
    unittest.main()
