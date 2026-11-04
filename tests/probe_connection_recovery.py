"""Actual Chromium transport recovery test; ONLY connects to a loopback fixture."""
import json
import socket
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from organizer.forum import BrowserClient, Cancelled


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        is_api = self.path.startswith('/search.json')
        if not (self.server.api_ready if is_api else self.server.home_ready):
            self.connection.shutdown(socket.SHUT_RDWR)
            self.connection.close()
            return
        body = (b'{"topics":[],"posts":[],"grouped_search_result":{"term":"fixture","error":null}}'
                if is_api else b'<!doctype html><html><title>Local connection recovery test</title><body>Local test fixture only.</body></html>')
        self.send_response(200)
        self.send_header('Content-Type', 'application/json' if is_api else 'text/html')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main():
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    server.home_ready = False
    server.api_ready = False
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f'http://127.0.0.1:{server.server_port}'
    report = {'fixture_origin': base, 'navigation_failures': 0, 'fetch_failures': 0, 'retained_during_pause': False}
    phase = 'startup'
    auto_failures = 0
    stop, resume = threading.Event(), threading.Event()
    client = None
    def emit(kind, value):
        nonlocal phase, auto_failures
        if kind == 'log' and '首页连接中断' in value:
            report['navigation_failures'] += 1
            if phase == 'startup':
                server.home_ready = True
            elif phase == 'automatic':
                auto_failures += 1
                report['retained_during_pause'] = client.browser.is_connected() and not client.page.is_closed()
                if auto_failures >= 3:
                    server.home_ready = True
            elif phase == 'cancel':
                stop.set()
        elif kind == 'log' and '读取 /search.json 时连接中断' in value:
            report['fetch_failures'] += 1
            server.api_ready = True
        elif kind == 'login':
            raise AssertionError('Transport failures must not require confirmation')
        print(kind, value, flush=True)
    try:
        with tempfile.TemporaryDirectory() as temp, patch('organizer.forum.BASE', base):
            client = BrowserClient(Path(temp), stop, emit, resume, headful=False, browser_channel='chromium', interval=0, search_interval=0, per_minute=0)
            with client:
                assert client.page.url.startswith(base), 'Test must remain on loopback'
                assert report['navigation_failures'] >= 1
                assert client.get('/search.json')['topics'] == []
                assert report['fetch_failures'] >= 1
                phase = 'automatic'
                server.home_ready = False
                client.open_home()
                assert report['retained_during_pause']
                phase = 'cancel'
                server.home_ready = False
                try:
                    client.open_home()
                    raise AssertionError('Stop must interrupt retries')
                except Cancelled:
                    report['stop_interrupts_retry'] = True
            report['browser_closed_after_stop'] = not client.browser.is_connected()
            assert report['browser_closed_after_stop']
        print('LOCAL_BROWSER_RECOVERY_OK', json.dumps(report), flush=True)
    finally:
        server.shutdown()
        server.server_close()


if __name__ == '__main__':
    main()
