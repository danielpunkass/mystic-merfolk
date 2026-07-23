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
import datetime
import json
import pathlib
import sys
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer


class Handler(SimpleHTTPRequestHandler):
    def send_head(self):
        if self.path.startswith('/beach/'):
            self.path = '/index.html'
        return super().send_head()


def warn_if_stale():
    # The committed data/ is refreshed by the GitHub Action, not locally, so a
    # clone that hasn't pulled recently serves old readings — which looks like
    # "the dashboard is missing the latest tests" when compared to the state
    # site. Surface the data age up front.
    try:
        meta = json.loads(pathlib.Path(__file__).with_name('data').joinpath('meta.json').read_text())
        synced = datetime.datetime.fromisoformat(meta['lastSynced'])
        age = datetime.datetime.now(datetime.timezone.utc) - synced
        if age > datetime.timedelta(hours=2):
            days = age.total_seconds() / 86400
            print(f"NOTE: local data/ was last synced {meta['lastSynced']} "
                  f"({days:.1f} days ago). The auto-sync commits to GitHub, "
                  f"not this clone — run `git pull` for current readings.")
    except Exception as e:  # noqa: BLE001 — advisory only; never block serving
        print(f"(could not check data freshness: {e})")


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
    server = ThreadingHTTPServer(('', port), Handler)
    warn_if_stale()
    print(f"Serving on http://localhost:{port}/ (beach permalinks enabled)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == '__main__':
    main()
