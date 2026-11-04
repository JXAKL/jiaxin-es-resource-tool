"""Real Chrome, loopback only: verify pacing and a global overload pause."""
import json
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from organizer.forum import BrowserClient, ServiceUnavailable


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_GET(self):
        if self.path == '/favicon.ico':
            self.send_response(204)
            self.end_headers()
            return
        self.server.requests.append((self.path, time.monotonic()))
        if self.path.startswith('/search.json'):
            body = json.dumps({'grouped_search_result': {
                'error': 'Site is under extreme load, search is disabled, try again later'}}).encode()
            mime = 'application/json'
        else:
            body, mime = b'<title>Local overload fixture</title>', 'text/html'
        self.send_response(200)
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main():
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    server.requests = []
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with tempfile.TemporaryDirectory() as temp, patch('organizer.forum.BASE', f'http://127.0.0.1:{server.server_port}'):
            client = BrowserClient(Path(temp), threading.Event(), lambda *_: None, headful=False, browser_channel='chrome')
            with client:
                try:
                    client.get('/search.json', {'q': 'local fixture'})
                    raise AssertionError('Overload must stop the request stream')
                except ServiceUnavailable:
                    pass
                assert len(server.requests) == 2
                gap = server.requests[1][1] - server.requests[0][1]
                assert gap >= 8, gap
                policy = json.loads((Path(temp) / 'network-policy.json').read_text())
                assert policy['cooldown'] > time.time()
                diagnostic = next((Path(temp) / 'diagnostics').glob('http-*.json'))
                assert json.loads(diagnostic.read_text(encoding='utf-8'))['status'] == 200
            print('LOCAL_SITE_PAUSE_OK', json.dumps({'browser': 'chrome', 'requests': len(server.requests),
                  'gap_seconds': round(gap, 2), 'overload_stops_stream': True, 'cooldown_persisted': True}))
    finally:
        server.shutdown()
        server.server_close()


if __name__ == '__main__':
    main()
