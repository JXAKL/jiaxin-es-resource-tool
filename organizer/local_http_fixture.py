"""Loopback test servers on ordinary browser-safe, unoccupied ports."""
from http.server import ThreadingHTTPServer


def local_server(handler):
    # Windows can allocate port 5061 for port=0; Chromium blocks that port.
    # Bind directly so concurrent checks cannot race a separate availability probe.
    for port in range(20000, 20200):
        try:
            return ThreadingHTTPServer(('127.0.0.1', port), handler)
        except OSError as error:
            if error.errno not in (13, 48, 98, 10013, 10048):
                raise
    raise OSError('No free loopback test port in 20000–20199')
