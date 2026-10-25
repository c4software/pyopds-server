""""À lire ensuite" recommendations: scoring functions."""
import base64
import importlib
import unittest
import support  # noqa: F401  (first: test database, project on sys.path)


class TestRecommendationScoring(unittest.TestCase):
    """Pure scoring functions of controllers.reading.recommendations and Basic credentials parsing."""

    @classmethod
    def setUpClass(cls):
        cls.rec = importlib.import_module('controllers.reading.recommendations')

    def test_reading_signal(self):
        signal = self.rec.reading_signal
        self.assertEqual(signal('finished', 0.2), 'read')
        self.assertEqual(signal('reading', 0.95), 'read')
        self.assertEqual(signal('dnf', 0.99), 'dnf')
        self.assertEqual(signal('reading', 0.3), 'reading')
        self.assertEqual(signal('paused', 0.3), 'paused')

    def test_recency_favours_recent_reads(self):
        now = 1_000_000_000
        self.assertAlmostEqual(self.rec.recency_factor(now, now), 1.0)
        self.assertLess(self.rec.recency_factor(now - 365 * 86400, now), self.rec.recency_factor(now - 86400, now))
        self.assertGreaterEqual(self.rec.recency_factor(0, now), 0.5)

    def test_profile_ignores_abandoned_books(self):
        profile = self.rec.build_profile([
            {'author': 'A', 'genres': ['SF'], 'status': 'dnf', 'percentage': 0.5, 'updated_at': 0},
        ], 0)
        self.assertTrue(profile.is_empty)
        self.assertEqual(self.rec.recommend([{'path': 'x', 'author': 'A', 'genres': ['SF']}], profile), [])

    def test_scoring_ranks_series_author_and_genres(self):
        now = 1_000_000_000
        profile = self.rec.build_profile([
            {'author': 'Frank  Herbert', 'genres': ['Science-fiction'], 'series': 'Dune', 'series_index': '1',
             'status': 'finished', 'percentage': 1.0, 'updated_at': now},
            {'author': 'Old Author', 'genres': ['Policier'], 'status': None, 'percentage': 0.95,
             'updated_at': now - 3 * 365 * 86400},
        ], now)
        candidates = [
            {'path': 'genre', 'author': 'Dan Simmons', 'genres': ['Science-fiction']},
            {'path': 'author', 'author': 'frank herbert', 'genres': []},
            {'path': 'next', 'author': 'Frank Herbert', 'genres': ['Science-fiction'], 'series': 'dune', 'series_index': 2},
            {'path': 'later', 'author': 'Frank Herbert', 'genres': ['Science-fiction'], 'series': 'Dune', 'series_index': 4},
            {'path': 'old-genre', 'author': 'X', 'genres': ['Policier']},
            {'path': 'none', 'author': 'Y', 'genres': ['Cuisine']},
        ]
        ranked = self.rec.recommend(candidates, profile)
        self.assertEqual([book['path'] for book in ranked], ['next', 'later', 'author', 'genre', 'old-genre'])
        self.assertIn('Tome suivant de Dune', ranked[0]['reasons'])
        self.assertEqual(len(self.rec.recommend(candidates, profile, limit=2)), 2)

    def test_series_bonus_is_optional(self):
        profile = self.rec.build_profile(
            [{'author': 'A', 'genres': [], 'status': 'reading', 'percentage': 0.4, 'updated_at': 0}], 0)
        score, reasons = self.rec.score_candidate({'author': 'A', 'genres': []}, profile)
        self.assertGreater(score, 0)
        self.assertEqual(reasons, ['Même auteur : A'])

    def test_basic_credentials_parsing(self):
        auth = importlib.import_module('controllers.accounts.opds_auth')
        token = base64.b64encode('élise:p:ss wörd'.encode('utf-8')).decode()
        self.assertEqual(auth.parse_basic_credentials(f'Basic {token}'), ('élise', 'p:ss wörd'))
        self.assertIsNone(auth.parse_basic_credentials('Bearer abc'))
        self.assertIsNone(auth.parse_basic_credentials('Basic ###'))
        self.assertIsNone(auth.parse_basic_credentials(None))
