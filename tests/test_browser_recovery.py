import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from organizer.forum import BrowserClient, Cancelled, ConnectionFailure


class BrowserRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.client = BrowserClient(Path(self.temp.name), threading.Event(), Mock(), interval=0)
        self.client.page = Mock()
        self.client.wait = Mock()
        self.client.pace = Mock()

    def tearDown(self):
        self.temp.cleanup()

    def test_navigation_context_destruction_retries_same_request(self):
        self.client.page.evaluate.side_effect = [Exception('Execution context was destroyed, most likely because of a navigation'),
                                                 {'status':200, 'text':'{"ok":true}'}]
        result = self.client.fetch('https://example.test/t/1.json', '/t/1.json')
        self.assertEqual(result['status'], 200)
        self.assertEqual(self.client.page.evaluate.call_count, 2)
        self.client.page.wait_for_load_state.assert_called_once()

    def test_restart_retains_channel_session_and_bounds(self):
        state = self.client.state_dir / 'session.json'
        state.write_text('{"cookies":[],"origins":[]}', encoding='utf-8')
        self.client.playwright = Mock()
        self.client.browser = Mock()
        self.client.open_home = Mock()
        for _ in range(3):
            self.assertTrue(self.client.recover_browser(Exception('Page crashed')))
        self.client.playwright.chromium.launch.assert_called_with(headless=False, channel='chrome')
        self.client.browser.new_context.assert_called_with(storage_state=str(state), locale='zh-CN')
        with self.assertRaises(ConnectionFailure):
            self.client.recover_browser(Exception('Page crashed'))
        self.assertEqual(self.client.playwright.chromium.launch.call_count, 3)

    def test_stop_during_recovery_does_not_launch(self):
        self.client.playwright = Mock()
        self.client.wait.side_effect = lambda _: self.client.stop.set()
        with self.assertRaises(Cancelled):
            self.client.recover_browser(Exception('Page crashed'))
        self.client.playwright.chromium.launch.assert_not_called()

    def test_web_reader_recreated_after_restart(self):
        self.client._web_reader = Mock()
        self.client._web_reader.read.side_effect = Exception('Page crashed')
        def recover(_):
            self.client._web_reader = None
            return True
        self.client.recover_browser = recover
        with patch('organizer.web_reader.WebReader') as reader:
            reader.return_value.read.return_value = {'ok':True}
            self.assertEqual(self.client.read_web('/t/1.json'), {'ok':True})
            reader.assert_called_once()

    def test_home_recovery_does_not_visit_home_twice(self):
        self.client.page.goto.side_effect = [Exception('Page crashed'), Mock(status=200)]
        self.client.recover_browser = Mock(return_value=True)
        self.client.open_home()
        self.client.recover_browser.assert_called_once_with(unittest.mock.ANY, restore_home=False)
        self.assertEqual(self.client.page.goto.call_count, 2)

    def test_repeated_navigation_losses_are_bounded(self):
        for _ in range(3):
            self.client.recover_browser(Exception('Execution context was destroyed'))
        with self.assertRaises(ConnectionFailure):
            self.client.recover_browser(Exception('Execution context was destroyed'))


if __name__ == '__main__':
    unittest.main()
