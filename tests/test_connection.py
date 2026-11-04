import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from organizer.engine import analyse
from organizer.forum import BrowserClient, Cancelled, ConnectionFailure, connection_code
from organizer.model import FileGroup
from organizer.store import Store


class ConnectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.state = Path(self.temp.name)
        self.events = []
        self.stop = threading.Event()
        self.client = BrowserClient(self.state, self.stop, lambda *entry: self.events.append(entry), headful=False)
        self.client.page = Mock()
        self.client.wait = Mock()
        self.client.pace = Mock()  # Transport tests; pacing has its own clock-based tests.

    def tearDown(self):
        self.temp.cleanup()

    def test_closed_connection_retries_then_succeeds(self):
        self.client.page.goto.side_effect = [Exception('net::ERR_CONNECTION_CLOSED'),
                                             Exception('net::ERR_CONNECTION_RESET'), 'response']
        self.assertEqual(self.client.open_home(), 'response')
        self.assertEqual(self.client.page.goto.call_count, 3)
        self.assertEqual([call.args[0] for call in self.client.wait.call_args_list], [3, 6])

    def test_navigation_retries_beyond_three_without_confirmation(self):
        self.client.page.goto.side_effect = [Exception('net::ERR_CONNECTION_CLOSED')] * 5 + ['response']
        self.client._wait_for_user = Mock()
        self.assertEqual(self.client.open_home(), 'response')
        self.assertEqual(self.client.page.goto.call_count, 6)
        self.client._wait_for_user.assert_not_called()
        self.assertEqual(self.client.diagnostics.stats['recoveries'], 1)

    def test_manual_refresh_interrupt_is_recoverable(self):
        self.client.page.goto.side_effect = [Exception('net::ERR_ABORTED'), 'response']
        self.assertEqual(self.client.open_home(), 'response')
        self.assertEqual(self.client.page.goto.call_count, 2)

    def test_browser_is_retained_during_automatic_retries(self):
        self.client.headful = True
        self.client.resume = threading.Event()
        self.client.browser = Mock()
        self.client.page.goto.side_effect = [Exception('net::ERR_CONNECTION_CLOSED')] * 3 + ['response']
        def emit(kind, value):
            if kind == 'login':
                self.client.browser.close.assert_not_called()
                self.client.resume.set()
        self.client.emit = emit
        self.assertEqual(self.client.open_home(), 'response')
        self.assertEqual(self.client.page.goto.call_count, 4)
        self.client.browser.close.assert_not_called()

    def test_stop_interrupts_retry(self):
        self.client.page.goto.side_effect = Exception('net::ERR_CONNECTION_CLOSED')
        self.client.wait.side_effect = lambda _: self.stop.set()
        with self.assertRaises(Cancelled):
            self.client.open_home()
        self.assertEqual(self.client.page.goto.call_count, 1)

    def test_certificate_and_programming_errors_are_not_retried(self):
        for error in (Exception('net::ERR_CERT_AUTHORITY_INVALID'), ValueError('invalid argument')):
            self.client.page.goto.reset_mock(side_effect=True)
            self.client.page.goto.side_effect = error
            with self.assertRaises(type(error)):
                self.client.open_home()
            self.assertEqual(self.client.page.goto.call_count, 1)
            self.assertIsNone(connection_code(error))

    def test_fetch_reconnects_without_losing_login_success(self):
        self.client.page.evaluate.side_effect = [Exception('TypeError: Failed to fetch'),
            {'status': 200, 'retry': None, 'text': '{"current_user":{"username":"Example"}}'}]
        result = self.client.get('/session/current.json')
        self.assertEqual(result['current_user']['username'], 'Example')
        self.assertEqual(self.client.page.evaluate.call_count, 2)
        self.assertTrue(self.client._session_confirmed)

    def test_fetch_failure_can_be_stopped_during_retry(self):
        self.client.page.evaluate.side_effect = Exception('TypeError: Failed to fetch')
        self.client.wait.side_effect = lambda _: self.stop.set()
        with self.assertRaises(Cancelled):
            self.client.get('/search.json')
        self.assertEqual(self.client.page.evaluate.call_count, 1)

    def test_failed_startup_preserves_session_and_cleans_resources(self):
        session = self.state / 'session.json'
        session.write_text('existing fixture state', encoding='utf-8')
        runtime, browser, context, page = Mock(), Mock(), Mock(), Mock()
        runtime.chromium.launch.return_value = browser
        browser.new_context.return_value = context
        context.new_page.return_value = page
        page.goto.side_effect = Exception('net::ERR_CONNECTION_CLOSED')
        self.client.wait.side_effect = lambda _: self.stop.set()
        browser.close.side_effect = RuntimeError('cleanup failed')
        with patch('playwright.sync_api.sync_playwright') as starter:
            starter.return_value.start.return_value = runtime
            with self.assertRaises(Cancelled):
                self.client.__enter__()
        self.assertEqual(session.read_text(encoding='utf-8'), 'existing fixture state')
        context.storage_state.assert_not_called()
        browser.close.assert_called_once()
        runtime.stop.assert_called_once()

    def test_incomplete_session_write_preserves_existing_file(self):
        session = self.state / 'session.json'
        session.write_text('previous fixture state', encoding='utf-8')
        self.client.context = Mock()
        self.client._session_confirmed = True
        def save(path):
            Path(path).write_text('partial', encoding='utf-8')
            raise OSError('simulated disk error')
        self.client.context.storage_state.side_effect = save
        with self.assertRaises(OSError):
            self.client.save_session()
        self.assertEqual(session.read_text(encoding='utf-8'), 'previous fixture state')
        self.assertEqual(list(self.state.glob('*.tmp')), [])

    def test_unconfirmed_session_is_not_saved(self):
        self.client.context = Mock()
        self.client.save_session()
        self.client.context.storage_state.assert_not_called()

    def test_confirmed_session_is_atomically_saved(self):
        self.client.context = Mock()
        self.client._session_confirmed = True
        self.client.context.storage_state.side_effect = lambda path: Path(path).write_text('new fixture state', encoding='utf-8')
        self.client.save_session()
        self.assertEqual((self.state / 'session.json').read_text(encoding='utf-8'), 'new fixture state')

    def test_topic_connection_failure_is_not_marked_as_searched(self):
        class Client:
            def get(self, endpoint, params=None):
                if endpoint == '/search.json':
                    return {'topics': [{'id': 1, 'title': 'Moonlight Dance'}]}
                raise ConnectionFailure('lost connection')
        store = Store(self.state / 'cache.db')
        try:
            with self.assertRaises(ConnectionFailure):
                analyse([FileGroup('group', 'Moonlight Dance')], store, Client())
            self.assertEqual(store.attempted('group'), 0)
        finally:
            store.close()
