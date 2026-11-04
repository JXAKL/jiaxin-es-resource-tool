"""Real Chromium restart on localhost only; never contacts the forum."""
import json
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from organizer.forum import BrowserClient


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = json.dumps({'cookie_restored':'fixture=kept' in self.headers.get('Cookie', '')}).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_):
        pass


def main():
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f'http://127.0.0.1:{server.server_port}'
    try:
        with tempfile.TemporaryDirectory() as root, patch('organizer.forum.BASE', base):
            with BrowserClient(Path(root), threading.Event(), lambda *_: None, browser_channel='chromium',
                               headful=False, interval=0, search_interval=0, per_minute=0) as client:
                client.context.add_cookies([{'name':'fixture','value':'kept','url':base}])
                client._session_confirmed = True
                client.save_session()
                client.browser.close()
                result = client.fetch(base+'/t/1.json', '/t/1.json')
                assert json.loads(result['text'])['cookie_restored']
                assert len(client._browser_restarts) == 1
                print('BROWSER_RESTART_OK: restored same channel and saved session; pending request completed')
    finally:
        server.shutdown(); server.server_close()


if __name__ == '__main__':
    main()
