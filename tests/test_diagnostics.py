import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from organizer.diagnostics import NetworkDiagnostics, append_log
from organizer.forum import BrowserClient, Cancelled


class DiagnosticsTests(unittest.TestCase):
    def test_statistics_persist_and_legacy_samples_stay_separate(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'http-old.json').write_text('{"endpoint":"/search.json","status":504}')
            diag = NetworkDiagnostics(root)
            diag.event('request', '/search.json', status=504, elapsed_seconds=30.3, requests_last_minute=2)
            diag.event('retry', '/search.json', attempt=1)
            diag.event('request', '/search.json', status=200, elapsed_seconds=.5, requests_last_minute=1)
            diag.event('recovered', '/search.json', attempts=1)
            again = NetworkDiagnostics(root)
            self.assertEqual(again.stats['requests'], 2)
            self.assertEqual(again.stats['endpoints']['/search.json'], {'total': 2, 'failed': 1})
            self.assertEqual(again.stats['recoveries'], 1)
            text = (root / '网络规律汇总.txt').read_text(encoding='utf-8')
            self.assertIn('50.0%', text)
            self.assertIn('30–40s', text)
            self.assertIn('旧版诊断', text)

    def test_sensitive_headers_body_and_query_not_written(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            client = BrowserClient(root, threading.Event(), Mock(), interval=0, per_minute=0)
            result = {'status': 504, 'headers': {'server': 'nginx', 'set-cookie': 'SECRET_COOKIE', 'authorization': 'SECRET_AUTH'},
                      'text': 'nginx gateway timeout PRIVATE_BODY'}
            client._record_request('/search.json', 0, result, url='https://example.invalid/search.json?q=PRIVATE_QUERY')
            stored = (root / 'diagnostics' / 'requests.jsonl').read_text()
            for secret in ('SECRET_COOKIE', 'SECRET_AUTH', 'PRIVATE_BODY', 'PRIVATE_QUERY'):
                self.assertNotIn(secret, stored)
            self.assertIn('gateway timeout', stored)

    def test_rate_snapshot_is_taken_at_request_start(self):
        with tempfile.TemporaryDirectory() as folder:
            client = BrowserClient(Path(folder), threading.Event(), Mock(), interval=0, per_minute=0)
            client.pacer.data['recent'] = [970, 980]
            with patch('organizer.forum.time.time', return_value=1000):
                client.pace('/t/1.json')
            with patch('organizer.forum.time.time', return_value=1040):
                client._record_request('/t/1.json', 0, {'status': 504})
            self.assertEqual(client.diagnostics.stats['last_failure']['requests_last_minute'], 3)

    def test_repeated_query_failures_are_grouped_by_digest(self):
        with tempfile.TemporaryDirectory() as folder:
            diag = NetworkDiagnostics(Path(folder))
            query = diag.query_id('https://example.invalid/search.json?q=sample')
            for _ in range(2):
                diag.event('request', '/search.json', status=504, query_id=query)
            self.assertEqual(diag.stats['query_failures'][query], 2)
            self.assertIn(query + ': 2 次失败', (Path(folder) / '网络规律汇总.txt').read_text(encoding='utf-8'))

    def test_rotating_logs_keep_bounded_history(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'runtime.log'
            for i in range(10):
                append_log(path, str(i) * 20, limit=10)
            self.assertEqual(len(list(Path(folder).iterdir())), 4)

    def test_login_challenge_remains_separate_from_automatic_retries(self):
        with tempfile.TemporaryDirectory() as folder:
            client = BrowserClient(Path(folder), threading.Event(), Mock())
            client.fetch = Mock(side_effect=[{'status': 403, 'text': 'verification'}, {'status': 200, 'text': '{}'}])
            client.login = Mock()
            client.service_pause = Mock()
            self.assertEqual(client.get('/search.json'), {})
            client.login.assert_called_once()
            client.service_pause.assert_not_called()

    def test_backoff_is_capped_and_stop_is_immediate(self):
        with tempfile.TemporaryDirectory() as folder:
            client = BrowserClient(Path(folder), threading.Event(), Mock(), cooldown_seconds=2)
            client.wait = Mock()
            for _ in range(12):
                client._retry('/search.json', 'fixture', base=2)
            self.assertLessEqual(client.pacer.data['cooldown'] - __import__('time').time(), 600.1)
            client.stop.set()
            with self.assertRaises(Cancelled):
                client._retry('/search.json', 'fixture')
