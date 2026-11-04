import json
import tempfile
import threading
import unittest
from datetime import datetime, timezone
from email.utils import format_datetime
from pathlib import Path
from unittest.mock import Mock, patch

from organizer.engine import analyse
from organizer.forum import BrowserClient, Forum, ServiceUnavailable, TopicUnavailable, Cancelled
from organizer.model import FileGroup, Topic
from organizer.pacing import RequestPacer, retry_seconds
from organizer.store import Store


class NetworkPolicyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name)
        self.client = BrowserClient(self.path, threading.Event(), lambda *_: None, headful=False)
        self.client.fetch = Mock()
        self.client.wait = Mock()

    def tearDown(self):
        self.temp.cleanup()

    def test_response_completion_search_gap_and_rolling_limit(self):
        pacer = RequestPacer(self.path / 'pace.json')
        pacer.started('/search.json', 1000)
        pacer.finished(1020)
        self.assertEqual(pacer.delay('/t/1.json', 1020), 8)
        pacer.finished(1001)
        self.assertEqual(pacer.delay('/search.json', 1001), 14)
        for t in (1000, 1008, 1016, 1024, 1032, 1040):
            pacer.started('/t/1.json', t)
        self.assertEqual(pacer.delay('/t/2.json', 1048), 12)

    def test_cooldown_survives_restart_and_honors_long_retry_after(self):
        pacer = RequestPacer(self.path / 'pace.json')
        pacer.cooldown(900, 1000)
        self.assertEqual(RequestPacer(pacer.path).delay('/search.json', 1100), 800)
        self.assertEqual(retry_seconds('900', 1000), 900)
        date = format_datetime(datetime.fromtimestamp(2000, timezone.utc), usegmt=True)
        self.assertEqual(retry_seconds(date, 1000), 1000)

    def test_configurable_intervals_have_no_hidden_floor_or_ceiling(self):
        pacer = RequestPacer(self.path / 'pace.json', .25, .5, 0)
        pacer.started('/search.json', 1000)
        pacer.finished(1000.1)
        self.assertAlmostEqual(pacer.delay('/t/1.json', 1000.1), .25)
        self.assertAlmostEqual(pacer.delay('/search.json', 1000.1), .4)
        slow = RequestPacer(pacer.path, 120, 180, 0)
        self.assertAlmostEqual(slow.delay('/search.json', 1001), 179)
        unlimited = RequestPacer(pacer.path, 0, 0, 0)
        for i in range(20):
            unlimited.started('/search.json', 1001 + i * .01)
        unlimited.finished(1002)
        self.assertEqual(unlimited.delay('/search.json', 1002), 0)

    def test_changed_rolling_limit_retains_more_than_six_requests(self):
        pacer = RequestPacer(self.path / 'pace.json', 0, 0, 10)
        for i in range(10):
            pacer.started('/t/1.json', 1000 + i)
        self.assertEqual(pacer.delay('/t/1.json', 1010), 50)
        tighter = RequestPacer(pacer.path, 0, 0, 2)
        self.assertEqual(tighter.delay('/t/1.json', 1010), 58)
        self.assertEqual(tighter.delay('/t/1.json', 1068), 0)

    def test_custom_cooldown_still_honors_server_retry_after(self):
        client = BrowserClient(self.path, threading.Event(), lambda *_: None, headful=False,
                               interval=0, search_interval=0, per_minute=0, cooldown_seconds=3)
        client.wait = Mock()
        with patch('organizer.forum.time.time', return_value=1000):
            client.service_pause('/search.json', 503, 'fixture')
        self.assertEqual(client.pacer.data['cooldown'], 1003)
        self.assertEqual(retry_seconds('25', 1000, minimum=3), 25)
        self.assertEqual(retry_seconds(None, 1000, minimum=3), 3)
        client.fetch = Mock(side_effect=[{'status': 503, 'retry': '25', 'text': '{}'}, {'status': 200, 'text': '{}'}])
        with patch('organizer.forum.time.time', return_value=1000):
            client.get('/search.json')
        self.assertEqual(client.pacer.data['cooldown'], 1025)

    def test_invalid_pacing_settings_rejected(self):
        for kwargs in ({'interval': -1}, {'interval': float('nan')}, {'search_interval': float('inf')},
                       {'per_minute': -1}, {'per_minute': 1.5}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                RequestPacer(self.path / 'pace.json', **kwargs)

    def test_overload_stops_whole_batch_before_other_fragments(self):
        class Client:
            calls = 0
            def get(self, *args):
                self.calls += 1
                return {'grouped_search_result': {'error': 'Site is under extreme load, search is disabled, try again later'}}
        client = Client()
        store = Store(self.path / 'db')
        try:
            with self.assertRaises(ServiceUnavailable):
                analyse([FileGroup('one', 'Moonlight Dance'), FileGroup('two', 'Harbor Lights')], store, client)
            self.assertEqual(client.calls, 1)
            self.assertEqual(store.attempted('one'), 0)
        finally:
            store.close()

    def test_service_errors_auto_retry_beyond_old_four_attempt_limit(self):
        for status in (408, 429, 404, 503, 504):
            with self.subTest(status=status):
                self.client.fetch = Mock(side_effect=[{'status': status, 'text': '{}', 'retry': '900'}] * 5 + [{'status': 200, 'text': '{}'}])
                self.client._wait_for_user = Mock()
                self.assertEqual(self.client.get('/search.json'), {})
                self.assertEqual(self.client.fetch.call_count, 6)
                self.client._wait_for_user.assert_not_called()
        self.assertEqual(self.client.diagnostics.stats['recoveries'], 5)

    def test_single_topic_404_is_not_treated_as_global_failure(self):
        self.client.fetch.return_value = {'status': 404, 'text': '{}'}
        for endpoint in ('/t/1.json', '/t/2.json'):
            with self.assertRaises(TopicUnavailable):
                self.client.get(endpoint)
        self.client.wait.side_effect = lambda _: self.client.stop.set()
        with self.assertRaises(Cancelled):
            self.client.get('/t/3.json')

    def test_html_404_retries_then_recovers(self):
        self.client.fetch.side_effect = [{'status': 404, 'text': '<html>Not found</html>'}, {'status': 200, 'text': '{}'}]
        self.assertEqual(self.client.get('/t/1.json'), {})
        self.assertEqual(self.client.fetch.call_count, 2)

    def test_refresh_fetches_each_topic_once_and_missing_topics_are_not_retried(self):
        store = Store(self.path / 'db')
        client = Mock()
        client.get.return_value = {'id': 1, 'title': 'Moonlight', 'post_stream': {'posts': [
            {'post_number': 1, 'username': 'Alpha', 'cooked': '<p>Moonlight</p>'}]}}
        try:
            forum = Forum(client, store, refresh=True)
            forum.topic(1, fresh=True)
            forum.topic(1, fresh=True)
            self.assertEqual(client.get.call_count, 1)
            client.get.side_effect = TopicUnavailable('missing')
            for _ in range(2):
                with self.assertRaises(TopicUnavailable):
                    forum.topic(2)
            self.assertEqual(client.get.call_count, 2)
        finally:
            store.close()

    def test_completed_author_does_not_request_profile(self):
        store = Store(self.path / 'db')
        try:
            store.author_progress('Alpha', 20, complete=True)
            client = Mock()
            Forum(client, store).author('Alpha')
            client.get.assert_not_called()
        finally:
            store.close()

    def test_chosen_browser_is_not_silently_replaced(self):
        runtime = Mock()
        runtime.chromium.launch.side_effect = RuntimeError('not installed')
        with patch('playwright.sync_api.sync_playwright') as start:
            start.return_value.start.return_value = runtime
            with self.assertRaisesRegex(Exception, '不会自动切换'):
                self.client.__enter__()
        runtime.chromium.launch.assert_called_once_with(channel='chrome', headless=True)

    def test_visible_pause_retains_browser_without_manual_continue(self):
        self.client.headful, self.client.resume = True, threading.Event()
        self.client.browser = Mock()
        self.client._wait_for_user = Mock()
        self.client.fetch.side_effect = [{'status': 503, 'text': '{}'}, {'status': 200, 'text': '{}'}]
        self.assertEqual(self.client.get('/search.json'), {})
        self.client._wait_for_user.assert_not_called()
        self.client.browser.close.assert_not_called()

    def test_refresh_search_is_reused_within_the_same_run(self):
        store = Store(self.path / 'db')
        try:
            client = Mock()
            client.get.return_value = {'topics': []}
            forum = Forum(client, store, refresh=True)
            forum.search('Moonlight')
            forum.search('Moonlight')
            self.assertEqual(client.get.call_count, 1)
        finally:
            store.close()


if __name__ == '__main__':
    unittest.main()
