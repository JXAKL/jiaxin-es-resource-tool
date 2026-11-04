import tempfile
import unittest
from pathlib import Path

from organizer.engine import analyse, search_name
from organizer.forum import Forum, ForumError, SearchResponseError, search_page
from organizer.model import FileGroup
from organizer.store import Store


def empty(term='no match'):
    return {'posts': [], 'users': [], 'grouped_search_result':
            {'term': term, 'post_ids': [], 'more_full_page_results': False, 'error': None}}


class SearchResponseTests(unittest.TestCase):
    def test_empty_search_without_sideloaded_topics(self):
        self.assertEqual(search_page(empty()), ([], False))

    def test_empty_search_with_null_topics(self):
        self.assertEqual(search_page(dict(empty(), topics=None)), ([], False))

    def test_explicit_empty_topics(self):
        self.assertEqual(search_page({'topics': []}), ([], False))

    def test_server_error_in_http_200_body_is_not_no_match(self):
        data = empty()
        data['grouped_search_result']['error'] = 'Search is currently overloaded'
        with self.assertRaisesRegex(SearchResponseError, 'overloaded'):
            search_page(data)

    def test_invalid_shapes_are_not_cached_as_empty(self):
        for data in ({}, {'posts': []}, {'topics': {}}, {'success': False},
                     {'topics': [], 'posts': [{'id': 3}]},
                     {'posts': [], 'grouped_search_result': {'term': 'x', 'post_ids': [3]}},
                     {'topics': [{'id': 1}]}, {'topics': [{'id': True, 'title': 'bad'}]}):
            with self.subTest(data=data), self.assertRaises(SearchResponseError):
                search_page(data)

    def test_string_false_is_not_treated_as_more_pages(self):
        data = {'topics': [{'id': 1, 'title': 'Moonlight &amp; Music'}],
                'grouped_search_result': {'more_full_page_results': 'false'}}
        self.assertEqual(search_page(data), ([{'id': 1, 'title': 'Moonlight & Music'}], False))

    def test_download_labels_removed_only_for_retrieval(self):
        name = 'Iwara - [Blue Archive] NO.4 Kisaki キサキ Time after school 放課後のタイム [mraT9qCLMmnDyn] [Source]'
        clean = search_name(name)
        self.assertNotIn('mrat9qclmmndyn', clean)
        self.assertNotIn('iwara', clean)
        self.assertNotIn('source', clean)
        self.assertIn('blue archive', clean)
        self.assertIn('no 4', clean)
        self.assertIn('放課後のタイム', clean)
        self.assertIn('abc 001', search_name('Title [ABC-001]'))


class SearchBatchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / 'cache.db')

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def test_cache_and_pagination_end_when_empty_page_omits_topics(self):
        class Client:
            calls = 0
            def get(self, endpoint, params):
                self.calls += 1
                if params['page'] == 1:
                    return {'topics': [{'id': 1, 'title': 'Moonlight Dance'}],
                            'grouped_search_result': {'more_full_page_results': True}}
                return empty()
        client = Client()
        forum = Forum(client, self.store)
        expected = [{'id': 1, 'title': 'Moonlight Dance'}]
        self.assertEqual(forum.search('Moonlight Dance'), expected)
        self.assertEqual(forum.search('Moonlight Dance'), expected)
        self.assertEqual(client.calls, 2)

    def test_empty_result_continues_to_next_file(self):
        class Client:
            calls = []
            def get(self, endpoint, params):
                self.calls.append(params['q'])
                return empty(params['q'])
        client = Client()
        groups = [FileGroup('a', 'No Matches'), FileGroup('b', 'Another Name')]
        result = analyse(groups, self.store, client)
        self.assertEqual([m.status for m in result], ['未匹配', '未匹配'])
        self.assertGreaterEqual(len(client.calls), 2)
        self.assertTrue(any('another' in q for q in client.calls))
        self.assertGreater(self.store.attempted('b'), 0)

    def test_one_malformed_result_does_not_stop_other_files(self):
        class Client:
            def get(self, endpoint, params):
                return {'unexpected': []} if params['q'].startswith('bad') else empty()
        result = analyse([FileGroup('a', 'Bad Filename'), FileGroup('b', 'Good Filename')], self.store, Client())
        self.assertEqual(result[0].status, '搜索失败')
        self.assertTrue(result[0].error)
        self.assertFalse(result[0].selected)
        self.assertEqual(result[1].status, '未匹配')
        self.assertEqual(self.store.attempted('a'), 0)
        self.assertIsNone(self.store.search('2:bad filename in:title'))

    def test_repeated_malformed_results_stop_after_three_files(self):
        class Client:
            calls = 0
            def get(self, endpoint, params):
                self.calls += 1
                return {'unexpected': []}
        client = Client()
        groups = [FileGroup(str(i), f'Broken Query {i}') for i in range(10)]
        with self.assertRaisesRegex(ForumError, '连续 3 组'):
            analyse(groups, self.store, client)
        from organizer.names import search_plan
        self.assertEqual(client.calls, sum(len(search_plan(g)) for g in groups[:3]))
