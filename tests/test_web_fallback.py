import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from organizer.forum import BrowserClient, Forum, AuthorActivityUnavailable, Cancelled
from organizer.store import Store
from organizer.web_reader import WebReadError, WebReader, WebRateLimited


class WebFallbackTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.client = BrowserClient(self.root, threading.Event(), Mock(), interval=0, search_interval=0, per_minute=0)
        self.client.page = Mock()
        self.client.fetch = Mock()
        self.client.wait = Mock()
        self.client.read_web = Mock(return_value={'topics': []})

    def tearDown(self):
        self.temp.cleanup()

    def test_activity_404_does_not_retry_forever(self):
        self.client.fetch.return_value = {'status': 404, 'text': '{}'}
        with self.assertRaises(AuthorActivityUnavailable):
            self.client.get('/user_actions.json', {'username': 'Alpha', 'offset': 0})
        self.assertEqual(self.client.fetch.call_count, 3)
        self.assertEqual(self.client.wait.call_count, 2)

    def test_gateway_error_switches_to_web_and_success_does_not_extend_probe_deadline(self):
        self.client.fetch.return_value = {'status': 504, 'text': 'gateway timeout'}
        with patch('organizer.forum.time.monotonic', return_value=100):
            self.assertEqual(self.client.get('/search.json', {'q': 'fixture'}), {'topics': []})
        with patch('organizer.forum.time.monotonic', return_value=200):
            self.client.get('/search.json', {'q': 'another'})
        self.assertEqual(self.client.fetch.call_count, 1)
        self.assertEqual(self.client._web_until['/search.json'], 700)
        with patch('organizer.forum.time.monotonic', return_value=701):
            self.client.fetch.return_value = {'status': 200, 'text': '{"topics": []}'}
            self.client.get('/search.json', {'q': 'recovered'})
        self.assertEqual(self.client.fetch.call_count, 2)

    def author_clock(self):
        clock = [1000.]
        self.client.wait.side_effect = lambda seconds: clock.__setitem__(0, clock[0] + seconds)
        return patch('organizer.forum.time.time', side_effect=lambda: clock[0])

    def test_author_profile_probes_api_despite_previous_web_preference(self):
        self.client._web_until.update({'/u/': float('inf'), '/u/Alpha.json': float('inf')})
        self.client.fetch.side_effect = [{'status':504, 'text':'{}'}, {'status':504, 'text':'{}'},
                                         {'status':200, 'text':'{"user":{"username":"Alpha"}}'}]
        with self.author_clock():
            self.assertEqual(self.client.get('/u/Alpha.json')['user']['username'], 'Alpha')
        self.assertEqual([c.args[0] for c in self.client.wait.call_args_list], [30., 10.])
        self.client.read_web.assert_not_called()
        self.assertNotIn('/u/Alpha.json', self.client._web_until)

    def test_profile_web_only_after_three_failed_api_probes(self):
        order = []
        self.client.fetch.side_effect = lambda *args: (order.append('api') or {'status':404, 'text':'{}'})
        self.client.read_web.side_effect = lambda *args: (order.append('web') or {'user':{'username':'Alpha'}})
        with self.author_clock():
            self.client.get('/u/Alpha.json')
            self.client.get('/u/Alpha.json')
        self.assertEqual(order, ['api','api','api','web'] * 2)
        self.assertFalse(self.client._web_until)

    def test_incomplete_saved_author_retries_api_and_preserves_offset(self):
        store = Store(self.root / 'catalog.db')
        try:
            store.author_progress('Alpha', 42, error='网页检索补充索引；活动列表尚未完成')
            self.client.fetch.side_effect = [
                {'status':200, 'text':'{"user":{"username":"Alpha"}}'},
                {'status':200, 'text':'{"user_actions":[]}'}]
            Forum(self.client, store).author('Alpha')
            self.assertIn('offset=42', self.client.fetch.call_args_list[1].args[0])
            self.assertTrue(store.author('Alpha')['complete'])
            self.assertEqual(store.author('Alpha')['error'], '')
            self.client.read_web.assert_not_called()
        finally:
            store.close()

    def test_author_retry_respects_longer_server_wait(self):
        self.client.fetch.side_effect = [{'status':503, 'retry':'90', 'text':'{}'},
                                         {'status':503, 'retry':'60', 'text':'{}'},
                                         {'status':200, 'text':'{"user_actions":[]}'}]
        with self.author_clock():
            self.client.get('/user_actions.json')
        self.assertEqual([c.args[0] for c in self.client.wait.call_args_list], [90., 60.])

    def test_author_transport_failure_is_bounded(self):
        del self.client.fetch
        self.client.page.evaluate.side_effect = Exception('TypeError: Failed to fetch')
        with self.author_clock(), self.assertRaises(AuthorActivityUnavailable):
            self.client.get('/user_actions.json')
        self.assertEqual(self.client.page.evaluate.call_count, 3)
        self.client.read_web.assert_not_called()

    def test_author_retry_can_stop_during_wait(self):
        self.client.fetch.return_value = {'status':504, 'text':'{}'}
        self.client.wait.side_effect = lambda _: self.client.stop.set()
        with self.assertRaises(Cancelled):
            self.client.get('/u/Alpha.json')
        self.assertEqual(self.client.fetch.call_count, 1)
        self.client.read_web.assert_not_called()

    def test_malformed_activity_response_retries_instead_of_marking_empty(self):
        self.client.fetch.side_effect = [{'status':200, 'text':'{"success":false,"user_actions":[]}'},
                                         {'status':200, 'text':'{"wrong":[]}'},
                                         {'status':200, 'text':'{"user_actions":[]}'}]
        with self.author_clock():
            self.assertEqual(self.client.get('/user_actions.json'), {'user_actions':[]})
        self.assertEqual(self.client.fetch.call_count, 3)

    def test_limit_and_auth_are_not_bypassed(self):
        self.client.fetch.side_effect = [{'status': 429, 'retry': '900', 'text': '{}'},
                                         {'status': 200, 'text': '{"topics": []}'}]
        self.client.get('/search.json')
        self.client.read_web.assert_not_called()
        self.client.login = Mock()
        self.client.fetch.side_effect = [{'status': 403, 'text': 'verify'},
                                         {'status': 200, 'text': '{"topics": []}'}]
        self.client.get('/search.json')
        self.client.login.assert_called_once()
        self.client.read_web.assert_not_called()

    def test_failed_web_retains_automatic_retry_and_stop(self):
        self.client.read_web.side_effect = WebReadError('fixture')
        self.client.fetch.return_value = {'status': 504, 'text': '{}'}
        self.client.wait.side_effect = lambda _: self.client.stop.set()
        with self.assertRaises(Cancelled):
            self.client.get('/search.json')
        self.assertEqual(self.client.fetch.call_count, 1)

    def test_invalid_api_schema_tries_web_instead_of_empty_search(self):
        self.client.fetch.return_value = {'status': 200, 'text': '{"unexpected": []}'}
        self.client.get('/search.json')
        self.client.read_web.assert_called_once()

    def test_transport_failure_uses_web_and_preserves_query(self):
        del self.client.fetch
        self.client.page.evaluate.side_effect = Exception('TypeError: Failed to fetch')
        result = self.client.get('/search.json', {'q': 'Harbor Lights', 'page': 1})
        self.assertEqual(result, {'topics': []})
        self.client.read_web.assert_called_once_with('/search.json', {'q': 'Harbor Lights', 'page': '1'})

    def test_web_success_is_recorded_separately(self):
        self.client.fetch.return_value = {'status': 504, 'text': '{}'}
        self.client.get('/search.json')
        self.assertEqual(self.client.diagnostics.stats['web_successes'], 1)

    def test_author_web_failure_preserves_api_offset_and_continues_other_work(self):
        store = Store(self.root / 'catalog.db')
        try:
            store.author_progress('Alpha', 42)
            self.client.get = Mock(side_effect=[{'user': {'username': 'Alpha'}}, AuthorActivityUnavailable('404')])
            self.client.read_web.side_effect = WebReadError('broken page')
            forum = Forum(self.client, store)
            self.assertEqual(forum.author('Alpha'), 'Alpha')
            forum.author('Alpha')
            self.assertEqual(self.client.get.call_count, 2)
            saved = store.author('Alpha')
            self.assertEqual(saved['offset'], 42)
            self.assertFalse(saved['complete'])
            self.assertIn('网页暂不可用', saved['error'])
        finally:
            store.close()

    def test_author_web_filters_wrong_author_and_replies_and_keeps_incomplete(self):
        store = Store(self.root / 'catalog.db')
        try:
            self.client.get = Mock(side_effect=[{'user': {'username': 'Alpha'}}, AuthorActivityUnavailable('404')])
            self.client.read_web.return_value = {'topics': [
                {'id': 1, 'title': 'Valid work', 'username': 'Alpha', 'post_number': 1},
                {'id': 2, 'title': 'Someone else', 'username': 'Beta', 'post_number': 1},
                {'id': 3, 'title': 'A reply', 'username': 'Alpha', 'post_number': 2}],
                'grouped_search_result': {'more_full_page_results': False}}
            Forum(self.client, store).author('Alpha')
            self.assertEqual([t.id for t in store.topics()], [1])
            self.assertFalse(store.author('Alpha')['complete'])
            self.assertFalse(store.topic(1).details_loaded)
        finally:
            store.close()

    def test_author_fallback_stop_is_not_swallowed(self):
        store = Store(self.root / 'catalog.db')
        try:
            self.client.get = Mock(side_effect=[{'user': {'username': 'Alpha'}}, AuthorActivityUnavailable('404')])
            self.client.read_web.side_effect = Cancelled('stop')
            with self.assertRaises(Cancelled):
                Forum(self.client, store).author('Alpha')
        finally:
            store.close()

    def test_web_429_preserves_retry_after(self):
        del self.client.read_web
        self.client._web_reader = Mock()
        self.client._web_reader.read.side_effect = WebRateLimited('900')
        with patch('organizer.forum.time.time', return_value=1000):
            self.assertIsNone(self.client._try_web('/search.json'))
        self.assertEqual(self.client.pacer.data['cooldown'], 1900)

    def test_blank_page_is_not_an_empty_result(self):
        reader = WebReader(self.client, 'https://example.invalid')
        reader._snapshot = Mock(return_value={'ready': False})
        with patch('organizer.web_reader.time.monotonic', side_effect=[0, 1, 13]):
            with self.assertRaises(WebReadError):
                reader._wait('search')


if __name__ == '__main__':
    unittest.main()
