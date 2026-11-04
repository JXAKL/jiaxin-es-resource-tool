import tempfile
import unittest
from pathlib import Path

from organizer.engine import search_ids
from organizer.forum import Forum, SearchTooBroad
from organizer.model import FileGroup, Topic
from organizer.names import search_plan
from organizer.store import Store


class PreciseSearchTests(unittest.TestCase):
    def test_reported_name_excludes_bare_generic_words(self):
        plan = search_plan(FileGroup('x', 'Iwara - Eve金色の闇-To LOVE [f5eM423lJsAG2y] [Source]'))
        self.assertEqual(plan[0], 'f5eM423lJsAG2y')
        self.assertIn('金色の闇', plan)
        self.assertIn('eve 金色の闇', plan)
        self.assertFalse(set(plan) & {'eve', 'love', 'to love'})

    def test_verified_id_stops_before_other_queries(self):
        group = FileGroup('x', 'Renamed [f5eM423lJsAG2y]')
        class Search:
            calls = []
            def search(self, query, **kwargs):
                self.calls.append(query)
                return [{'id': 1, 'title': 'New release'}]
            def topic(self, identity, **kwargs):
                return Topic(1, 'New release', 'Alpha', '', source_ids=['f5eM423lJsAG2y'], details_loaded=True)
        search = Search()
        self.assertEqual(search_ids(search, group, lambda *_: None), [1])
        self.assertEqual(search.calls, ['f5eM423lJsAG2y'])

    def test_search_hit_without_exact_id_does_not_early_stop(self):
        group = FileGroup('x', 'Renamed [f5eM423lJsAG2y]')
        class Search:
            calls = []
            def search(self, query, **kwargs):
                self.calls.append(query)
                return [{'id': 1, 'title': 'New release'}]
            def topic(self, identity, **kwargs):
                return Topic(1, 'New release', 'Alpha', '', source_ids=['f5em423ljsag2y'], details_loaded=True)
        search = Search()
        search_ids(search, group, lambda *_: None)
        self.assertGreater(len(search.calls), 1)

    def test_broad_first_page_is_not_paginated_or_cached_as_empty(self):
        with tempfile.TemporaryDirectory() as temp:
            store = Store(Path(temp) / 'cache.db')
            class Client:
                calls = 0
                def get(self, endpoint, params):
                    self.calls += 1
                    return {'topics': [{'id': i, 'title': 'Fixture'} for i in range(1, 51)],
                            'grouped_search_result': {'more_full_page_results': True}}
            client = Client()
            try:
                forum = Forum(client, store)
                for _ in range(2):
                    with self.assertRaises(SearchTooBroad):
                        forum.search('Fixture', pages=2, max_hits=20)
                self.assertEqual(client.calls, 1)
            finally:
                store.close()

    def test_broad_fragments_are_combined_with_bounded_request_count(self):
        class Search:
            calls = []
            def search(self, query, **kwargs):
                self.calls.append(query)
                raise SearchTooBroad('many results')
        search = Search()
        search_ids(search, FileGroup('x', '[Harbor Lights] Midnight Skyline'), lambda *_: None)
        self.assertLessEqual(len(search.calls), 6)
        self.assertTrue(any('harbor lights' in q and 'midnight skyline' in q for q in search.calls))

    def test_same_media_id_from_competing_authors_does_not_early_stop(self):
        group = FileGroup('x', 'Renamed [f5eM423lJsAG2y]')
        class Search:
            calls = []
            def search(self, query, **kwargs):
                self.calls.append(query)
                return [{'id': i, 'title': 'New release'} for i in (1, 2)]
            def topic(self, identity, **kwargs):
                return Topic(identity, 'New release', str(identity), '', source_ids=['f5eM423lJsAG2y'], details_loaded=True)
        search = Search()
        search_ids(search, group, lambda *_: None)
        self.assertGreater(len(search.calls), 1)


if __name__ == '__main__':
    unittest.main()
