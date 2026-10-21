"""Tests of the optional online book details (controllers/online_metadata.py), without network."""
import os
import sys
import tempfile
import unittest
from unittest import mock

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from controllers import online_metadata as om  # noqa: E402

ITUNES = {'results': [
    {'trackName': 'Totally Different', 'artistName': 'Nobody', 'artworkUrl100': 'https://a/x/100x100bb.jpg'},
    {'trackName': 'The Martian', 'artistName': 'Andy Weir', 'artworkUrl100': 'https://a/martian/100x100bb.jpg'},
]}
OL_SEARCH = {'docs': [
    {'key': '/works/OL1W', 'title': 'The Martian', 'author_name': ['Andy Weir'], 'cover_i': 42, 'number_of_pages_median': 369},
    {'key': '/works/OL2W', 'title': 'Martian Chronicles', 'author_name': ['Ray Bradbury'], 'cover_i': 7},
]}
OL_WORK = {'description': {'type': '/type/text', 'value': '  Stranded on Mars.  '}}
OL_EDITION = {'title': 'The Martian', 'number_of_pages': 387, 'covers': [99], 'works': [{'key': '/works/OL1W'}]}
HC_DOC = {
    'id': 123, 'slug': 'the-martian', 'title': 'The Martian', 'author_names': ['Andy Weir'],
    'moods': ['adventurous', 'Funny', 'funny', 'tense'], 'genres': ['Science fiction', ''], 'content_warnings': ['Death'],
    'rating': 4.4167, 'ratings_count': 10, 'series_names': ['Mars'], 'featured_series': {'series': {'id': 77}},
    'featured_series_position': 1, 'release_year': 2011, 'pages': 384, 'image': {'url': 'https://hc/cover.jpg'},
    'description': 'Six days ago...',
}
HC_SEARCH = {'data': {'search': {'results': {'hits': [
    {'document': {'id': 1, 'title': 'Unrelated', 'author_names': ['Someone']}},
    {'document': HC_DOC},
]}}}}
HC_SERIES = {'data': {'book_series': [
    {'position': 1, 'book': {'title': 'The Martian', 'slug': 'the-martian', 'release_year': 2011}},
    {'position': 1.5, 'book': {'title': 'Novella', 'slug': 'novella'}},
    {'position': 2, 'book': {'title': 'Mars 2', 'slug': 'mars-2', 'release_year': 2020,
                             'cached_contributors': [{'author': {'name': 'Andy Weir'}}],
                             'cached_image': {'url': 'https://hc/2.jpg'}}},
]}}


class FakeFetch:
    """Records requests and answers from a route table: url substring -> (status, body)."""

    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def __call__(self, url, method='GET', headers=None, body=None, timeout=None):
        self.calls.append((url, method, headers, body))
        if url == om.HARDCOVER_ENDPOINT:
            route = 'series' if 'book_series' in body['query'] else 'search'
            return self.routes.get(route, (200, {}))
        for prefix, answer in self.routes.items():
            if prefix in url:
                return answer
        return 404, None


class NoWaitLimiter(om.RateLimiter):
    def __init__(self, **kwargs):
        super().__init__(sleep=lambda s: None, **kwargs)


class TestMatching(unittest.TestCase):
    def test_normalization(self):
        self.assertEqual(om.normalize_text("  L'Étranger — Édition_spéciale! "), 'l etranger edition speciale')
        self.assertEqual(om.normalize_text('変な家２'), '変な家2')
        self.assertEqual(om.core_title('The Martian: A Novel'), 'The Martian')
        self.assertEqual(om.core_title('Dune (Dune Chronicles, Book 1)'), 'Dune')
        self.assertEqual(om.normalize_author('Weir, Andy'), 'andy weir')
        self.assertEqual(om.cache_key('The Martian: A Novel', 'Weir, Andy'), 'the martian|andy weir')
        self.assertEqual(om.clean_isbn('978-2-07-036002-4'), '9782070360024')
        self.assertIsNone(om.clean_isbn('123'))

    def test_scoring(self):
        self.assertAlmostEqual(om.score_candidate('The Martian', 'Andy Weir', 'The Martian', 'Weir, Andy'), 1.0)
        self.assertAlmostEqual(om.score_candidate('The Martian', '', 'The Martian', 'Andy Weir'), 0.85)
        self.assertLess(om.score_candidate('The Martian', 'Andy Weir', 'Martian Chronicles', 'Ray Bradbury'), om.THRESHOLD)

    def test_book_query_from_filename(self):
        self.assertEqual(om.book_query(None, None, None, 'Andy Weir - The Martian.epub'), ('Andy Weir The Martian', '', None))
        self.assertIsNone(om.book_query(None, None))
        self.assertEqual(om.book_query(None, None, '0-553-41802-9'), ('', '', '0553418029'))

    def test_next_after_skips_novellas(self):
        books = om.parse_hardcover_series(HC_SERIES)
        self.assertEqual(om.next_after(books, 1)['title'], 'Mars 2')
        self.assertEqual(om.next_after(books, 1)['author'], 'Andy Weir')
        self.assertIsNone(om.next_after(books, 2))
        self.assertEqual(om.next_after([{'position': 2.5, 'title': 'x'}], 2)['title'], 'x')


class TestParsing(unittest.TestCase):
    def test_itunes_and_open_library(self):
        apple = om.parse_itunes(ITUNES)
        self.assertEqual(apple[1]['url'], 'https://a/martian/600x600bb.jpg')
        ol = om.parse_open_library_search(OL_SEARCH)
        self.assertEqual(ol[0], {'title': 'The Martian', 'author': 'Andy Weir', 'url': 'https://covers.openlibrary.org/b/id/42-L.jpg',
                                 'pages': 369, 'work': '/works/OL1W'})
        self.assertIsNone(ol[1]['pages'])
        self.assertEqual(om.parse_description(OL_WORK['description']), 'Stranded on Mars.')
        self.assertEqual(om.parse_description('Plain'), 'Plain')
        self.assertIsNone(om.parse_description({'value': ' '}))
        edition = om.parse_open_library_edition(OL_EDITION)
        self.assertEqual((edition['url'], edition['pages'], edition['work']),
                         ('https://covers.openlibrary.org/b/id/99-L.jpg', 387, '/works/OL1W'))
        self.assertEqual(om.parse_itunes('garbage'), [])
        self.assertEqual(om.parse_open_library_search({'docs': [None, {'title': 3}]}), [])

    def test_hardcover(self):
        book = om.best_hardcover_match(HC_SEARCH, 'The Martian', 'Andy Weir')
        self.assertEqual(book['slug'], 'the-martian')
        self.assertEqual(book['moods'], ['Adventurous', 'Funny', 'Tense'])
        self.assertEqual(book['genres'], ['Science fiction'])
        self.assertEqual(book['rating'], 4.42)
        self.assertEqual((book['series'], book['series_id'], book['series_position']), ('Mars', 77, 1))
        self.assertEqual((book['release_year'], book['pages']), (2011, 384))
        self.assertIsNone(om.parse_hardcover_book({**HC_DOC, 'ratings_count': 0}).get('rating'))
        self.assertIsNone(om.best_hardcover_match(HC_SEARCH, 'Dune', 'Frank Herbert'))
        # Translations only appear among the alternative titles
        translated = {'data': {'search': {'results': {'hits': [{'document': {**HC_DOC, 'alternative_titles': ["L'uomo di Marte"]}}]}}}}
        self.assertIsNotNone(om.best_hardcover_match(translated, "L'uomo di Marte", 'Andy Weir'))


class ServiceTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.now = 1_700_000_000
        self.env = mock.patch.dict(os.environ, {'ONLINE_METADATA': 'true', 'HARDCOVER_API_KEY': 'secret'})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def make(self, routes):
        fetch = FakeFetch(routes)
        service = om.OnlineMetadata(os.path.join(self.tmp.name, 'sync.db'), fetch=fetch,
                                    limiter=NoWaitLimiter(), clock=lambda: self.now)
        return service, fetch


class TestService(ServiceTestCase):
    ROUTES = {
        'itunes.apple.com': (200, ITUNES), 'search.json': (200, OL_SEARCH), '/works/OL1W.json': (200, OL_WORK),
        'search': (200, HC_SEARCH), 'series': (200, HC_SERIES),
    }

    def test_open_library_lookup(self):
        service, fetch = self.make(self.ROUTES)
        found = service.lookup_open_library('The Martian', 'Andy Weir')
        self.assertEqual(found, {'cover_url': 'https://a/martian/600x600bb.jpg', 'page_count': 369,
                                 'description': 'Stranded on Mars.'})
        self.assertTrue(all(call[2]['User-Agent'] == om.USER_AGENT for call in fetch.calls))

    def test_open_library_by_isbn(self):
        service, fetch = self.make({**self.ROUTES, '/isbn/': (200, OL_EDITION)})
        found = service.lookup_open_library('The Martian', 'Andy Weir', '9780553418026')
        self.assertEqual(found['cover_url'], 'https://covers.openlibrary.org/b/id/99-L.jpg')
        self.assertEqual(found['page_count'], 387)
        self.assertFalse(any('itunes' in call[0] or 'search.json' in call[0] for call in fetch.calls))

    def test_refresh_caches_and_merges(self):
        service, fetch = self.make(self.ROUTES)
        self.assertEqual(service.cached_info('The Martian', 'Andy Weir')['moods'], [])
        service.refresh_book('The Martian', 'Andy Weir')
        info = service.cached_info('The Martian: A Novel', 'Weir, Andy')  # same cache key
        self.assertEqual(info['cover_url'], 'https://a/martian/600x600bb.jpg')
        self.assertEqual(info['page_count'], 384)  # Hardcover's print count first
        self.assertEqual(info['description'], 'Six days ago...')
        self.assertEqual(info['hardcover_slug'], 'the-martian')
        hardcover = [c for c in fetch.calls if c[0] == om.HARDCOVER_ENDPOINT]
        self.assertEqual(hardcover[0][2]['authorization'], 'Bearer secret')
        # Fresh cache: no new request
        count = len(fetch.calls)
        service.refresh_book('The Martian', 'Andy Weir')
        self.assertEqual(len(fetch.calls), count)
        # Expired after 30 days
        self.now += om.OPEN_LIBRARY_SUCCESS_TTL + 1
        service.refresh_book('The Martian', 'Andy Weir')
        self.assertGreater(len(fetch.calls), count)

    def test_misses_are_remembered(self):
        service, fetch = self.make({'itunes.apple.com': (200, {'results': []}), 'search.json': (200, {'docs': []}),
                                    'search': (200, {'data': {'search': {'results': {'hits': []}}}})})
        service.refresh_book('Inconnu', 'Personne')
        count = len(fetch.calls)
        self.assertEqual(service.cache_get(['ol:inconnu|personne'])['ol:inconnu|personne'][0], None)
        self.now += om.OPEN_LIBRARY_MISS_TTL - 10
        service.refresh_book('Inconnu', 'Personne')
        self.assertEqual(len(fetch.calls), count)
        # Open Library retried after 7 days, Hardcover only after 30
        self.now += 20
        service.refresh_book('Inconnu', 'Personne')
        new = fetch.calls[count:]
        self.assertTrue(new)
        self.assertFalse(any(c[0] == om.HARDCOVER_ENDPOINT for c in new))

    def test_network_errors_are_not_cached(self):
        service, _ = self.make({'itunes.apple.com': (0, None), 'search.json': (0, None), 'search': (0, None)})
        service.refresh_book('The Martian', 'Andy Weir')
        self.assertEqual(service.cache_get(['ol:the martian|andy weir', 'hc:the martian|andy weir']), {})

    def test_hardcover_429_pauses(self):
        service, fetch = self.make({**self.ROUTES, 'search': (429, None)})
        self.assertEqual(service.search_hardcover('The Martian', 'Andy Weir', 'k'), 'later')
        count = len(fetch.calls)
        self.assertEqual(service.search_hardcover('The Martian', 'Andy Weir', 'k'), 'later')
        self.assertEqual(len(fetch.calls), count)  # paused: no request

    def test_fill_progress_items_and_background_queue(self):
        service, fetch = self.make(self.ROUTES)
        items = [
            {'title': 'The Martian', 'author': 'Andy Weir', 'filename': None, 'library_path': None, 'cover_url': None,
             'page_count': None, 'moods': [], 'genres': [], 'content_warnings': [], 'rating': None,
             'series': None, 'series_position': None, 'release_year': None, 'hardcover_slug': None},
            {'title': 'Library book', 'author': 'X', 'filename': None, 'library_path': 'x.epub', 'cover_url': 'local',
             'page_count': 10, 'moods': [], 'genres': ['Local'], 'content_warnings': [], 'rating': None,
             'series': None, 'series_position': None, 'release_year': None, 'hardcover_slug': None},
        ]
        service.fill_progress_items(items)
        self.assertIsNone(items[0]['cover_url'])  # nothing cached yet, request answered at once
        service.wait_idle()
        self.assertFalse(any('Library' in str(c) for c in fetch.calls))  # library matches are not looked up
        service.fill_progress_items(items)
        self.assertEqual(items[0]['cover_url'], 'https://a/martian/600x600bb.jpg')
        self.assertEqual((items[0]['series'], items[0]['series_position']), ('Mars', 1))
        self.assertEqual(items[0]['rating'], 4.42)
        self.assertEqual(items[1]['cover_url'], 'local')
        self.assertEqual(items[1]['genres'], ['Local'])

    def test_next_in_series(self):
        service, _ = self.make(self.ROUTES)
        self.assertIsNone(service.next_in_series('The Martian', 'Andy Weir'))  # book not looked up
        service.refresh_book('The Martian', 'Andy Weir')
        self.assertEqual(service.next_in_series('The Martian', 'Andy Weir'), {'next': None, 'pending': True})
        service.wait_idle()
        answer = service.next_in_series('The Martian', 'Andy Weir')
        self.assertEqual(answer['series'], 'Mars')
        self.assertEqual(answer['next']['slug'], 'mars-2')

    def test_cover_candidates(self):
        service, _ = self.make(self.ROUTES)
        items = service.cover_candidates('The Martian', 'Andy Weir')
        self.assertEqual([i['source'] for i in items[:2]], ['Apple Books', 'Open Library'])
        self.assertEqual(items[1]['pages'], 369)
        self.assertEqual(set(items[0]), {'url', 'title', 'author', 'source', 'pages'})


class TestDisabled(ServiceTestCase):
    def test_disabled_by_default_makes_no_request(self):
        with mock.patch.dict(os.environ, {}, clear=True), mock.patch('urllib.request.urlopen') as urlopen:
            service = om.OnlineMetadata(os.path.join(self.tmp.name, 'sync.db'))
            self.assertFalse(om.any_enabled())
            items = [{'title': 'The Martian', 'author': 'Andy Weir', 'library_path': None, 'cover_url': None}]
            service.fill_progress_items(items)
            self.assertEqual(service.cached_info('The Martian', 'Andy Weir', enqueue=True), {})
            self.assertEqual(service.cover_candidates('The Martian', 'Andy Weir'), [])
            self.assertIsNone(service.next_in_series('The Martian', 'Andy Weir'))
            service.wait_idle()
            urlopen.assert_not_called()
            self.assertIsNone(items[0]['cover_url'])

    def test_open_library_only_without_hardcover_key(self):
        with mock.patch.dict(os.environ, {'HARDCOVER_API_KEY': ''}):
            service, fetch = self.make(TestService.ROUTES)
            service.refresh_book('The Martian', 'Andy Weir')
            self.assertFalse(any(c[0] == om.HARDCOVER_ENDPOINT for c in fetch.calls))
            self.assertEqual(service.cached_info('The Martian', 'Andy Weir')['moods'], [])


class TestRateLimiter(unittest.TestCase):
    def test_gap_daily_cap_and_pause(self):
        now = [86400 * 100 + 10.0]
        waits = []
        limiter = om.RateLimiter(min_gap=1.1, daily_cap=3, pause=600, clock=lambda: now[0], sleep=waits.append)
        self.assertTrue(limiter.acquire())
        self.assertTrue(limiter.acquire())
        self.assertTrue(limiter.acquire())
        self.assertEqual([round(w, 1) for w in waits], [1.1, 2.2])
        self.assertFalse(limiter.acquire())  # daily cap
        now[0] += 86400  # next day
        self.assertTrue(limiter.acquire())
        limiter.back_off()
        self.assertFalse(limiter.acquire())
        now[0] += 601
        self.assertTrue(limiter.acquire())


class TestFetch(unittest.TestCase):
    def test_fetch_sends_user_agent_and_parses_json(self):
        response = mock.MagicMock()
        response.__enter__.return_value = response
        response.status = 200
        response.read.return_value = b'{"ok": true}'
        with mock.patch('urllib.request.urlopen', return_value=response) as urlopen:
            self.assertEqual(om._fetch('https://example.org/x', method='POST', body={'a': 1}), (200, {'ok': True}))
        request = urlopen.call_args[0][0]
        self.assertEqual(request.get_header('User-agent'), om.USER_AGENT)
        self.assertEqual(urlopen.call_args[1]['timeout'], om.TIMEOUT)

    def test_fetch_network_error(self):
        with mock.patch('urllib.request.urlopen', side_effect=OSError('down')):
            self.assertEqual(om._fetch('https://example.org/x'), (0, None))


if __name__ == '__main__':
    unittest.main()
