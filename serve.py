#!/usr/bin/env python3
"""Local dev server for the dashboard.

Like `python3 -m http.server`, but with one addition: in production the
/beach/<slug>/ permalinks are pre-rendered copies of index.html generated at
deploy time (see sync.yml), which don't exist in the repo — so under a plain
static server those URLs 404. This server answers any /beach/ path with
index.html instead, mirroring the deployed behavior. `<base href="/">` in
index.html keeps the app's data/ and archive/ fetches rooted, and the JS reads
the slug from location.pathname, so the right beach loads.

Usage: python3 serve.py [port]   (default 8000)
"""
import sys
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer


class Handler(SimpleHTTPRequestHandler):
    def send_head(self):
        if self.path.startswith('/beach/'):
            self.path = '/index.html'
        return super().send_head()


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
    server = ThreadingHTTPServer(('', port), Handler)
    print(f"Serving on http://localhost:{port}/ (beach permalinks enabled)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == '__main__':
    main()
