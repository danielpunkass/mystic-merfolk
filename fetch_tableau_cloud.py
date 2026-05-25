#!/usr/bin/env python3
"""
Browser-based fetch of live beach data from the Mass DPH "Beach Water Quality
Dashboard" on Tableau Cloud.

Why this exists
---------------
In 2026 DPH moved the live dashboard from the old Tableau Server
(`datavisualization.dph.mass.gov`, workbook `BeachesDashboard-CloudVersion-2025`)
to Tableau Cloud (`prod-useast-b.online.tableau.com`, site `eohhspublic`,
workbook `BeachWaterQualityDashboard`). The old per-beach CSV endpoints are
frozen at the 2025 season's end. The current season's readings live only in the
`TestResultsTable` worksheet of the new workbook, which is reachable via the
Tableau JS Embedding API's `getSummaryDataAsync()` — exactly what the dashboard's
"Download Full Dataset" extension uses. There is no static CSV endpoint for it
(the data loads lazily and underlying-data export is disabled), so we drive a
headless browser to read it the same way the official page does.

Access uses a public connected-app JWT minted by
`publicdashboardtoken.mass.gov` — the same token the mass.gov page requests.

Output
------
Prints JSON to stdout:
  {
    "samples": { "headers": [...], "rows": [[date, indicator, threshold, results], ...] },
    "samplesCsv": "<5-column CSV matching the legacy Results.csv shape>",
    "status":  { "name": ..., "status": ..., "town": ... },
    "testResultsRowCount": <int>,
    "errors": [ ... ]
  }

Requires Playwright (`pip install playwright` + `playwright install chromium`).
Kept separate from sync_water_data.py so that script stays stdlib-only; the sync
orchestrator invokes this as a subprocess and falls back gracefully if it fails.
"""

from __future__ import annotations

import functools
import http.server
import json
import socketserver
import sys
import threading
import urllib.request

BEACH_NAME = "Shannon Beach @ Upper Mystic (DCR)"

TOKEN_URL = (
    "https://publicdashboardtoken.mass.gov/tokens/requestpublicaccess"
    "?connectedapp=DPH-BCEH-BDD-BD"
)
VIEW_BASE = (
    "https://prod-useast-b.online.tableau.com/t/eohhspublic/views/"
    "BeachWaterQualityDashboard"
)
EMBED_API = "https://public.tableau.com/javascripts/api/tableau.embedding.3.latest.js"

# TestResultsTable carries Date/Indicator/GeoMean/Results but no threshold column.
# The single-sample thresholds are fixed per indicator (CFU/100 ml); these match
# what the legacy Results.csv reported. Default falls back to Enterococci's value.
SINGLE_SAMPLE_THRESHOLDS = {
    "Enterococci": "61",
    "E. Coli": "235",
}
DEFAULT_THRESHOLD = "61"

PAGE_TIMEOUT_MS = 60_000
RESULT_POLL_SECONDS = 60


def fetch_public_token() -> str:
    req = urllib.request.Request(
        TOKEN_URL,
        headers={
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://www.mass.gov/",
            "Origin": "https://www.mass.gov",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        payload = json.loads(r.read().decode("utf-8", "replace"))
    token = payload.get("token")
    if not token:
        raise RuntimeError(f"token endpoint returned no token: {payload!r}")
    return token


def _embed_html(token: str, view: str) -> str:
    # Loads a single worksheet and reads its full summary data via the
    # Embedding API. ignoreSelection + maxRows:0 returns every row unfiltered.
    return f"""<!DOCTYPE html><html><head><meta charset="utf-8">
<script type="module" src="{EMBED_API}"></script></head><body>
<tableau-viz id="viz" src="{VIEW_BASE}/{view}" token="{token}" toolbar="hidden"></tableau-viz>
<script type="module">
window.__r = {{ status: "init" }};
const viz = document.getElementById("viz");
viz.addEventListener("firstinteractive", async () => {{
  try {{
    const sheet = viz.workbook.activeSheet;
    const ws = sheet.sheetType === "worksheet"
      ? sheet
      : (sheet.worksheets || []).find(w => w.name === "{view}") || (sheet.worksheets || [])[0];
    const dt = await ws.getSummaryDataAsync({{ maxRows: 0, ignoreSelection: true }});
    window.__r = {{
      status: "ok",
      columns: dt.columns.map(c => c.fieldName),
      rows: dt.data.map(r => r.map(c => c.formattedValue)),
    }};
  }} catch (e) {{ window.__r = {{ status: "error", message: String(e && e.message || e) }}; }}
}});
viz.addEventListener("vizloaderror", e =>
  window.__r = {{ status: "loaderror", message: String(e.detail || e) }});
</script></body></html>"""


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args, **kwargs):  # silence per-request logging
        pass


def _serve_dir(directory: str) -> tuple[socketserver.TCPServer, int]:
    handler = functools.partial(_QuietHandler, directory=directory)
    httpd = socketserver.TCPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, httpd.server_address[1]


def _read_worksheet(page, base_url: str, view: str) -> dict:
    """Load an embed page for one worksheet and return its summary-data result."""
    import time
    page.goto(f"{base_url}/{view}.html", wait_until="load", timeout=PAGE_TIMEOUT_MS)
    result = None
    for _ in range(RESULT_POLL_SECONDS):
        result = page.evaluate("() => window.__r")
        if result and result.get("status") in ("ok", "error", "loaderror"):
            break
        time.sleep(1)
    if not result or result.get("status") != "ok":
        raise RuntimeError(f"{view}: {json.dumps(result)[:300] if result else 'no result'}")
    return result


def _normalize_date(value: str) -> str:
    # Tableau formats times with a narrow / non-breaking space before AM/PM.
    return value.replace(" ", " ").replace(" ", " ").strip()


def _col(columns: list[str], *candidates: str) -> int:
    for cand in candidates:
        for i, c in enumerate(columns):
            if c == cand:
                return i
    # loose contains match
    for cand in candidates:
        for i, c in enumerate(columns):
            if cand.lower() in c.lower():
                return i
    raise KeyError(f"none of {candidates} in {columns}")


def build_samples(test_results: dict, beach_name: str) -> tuple[dict, str]:
    cols = test_results["columns"]
    i_name = _col(cols, "Name")
    i_date = _col(cols, "Date")
    i_ind = _col(cols, "Indicator")
    i_res = _col(cols, "AGG(Results (CFU/100 ml))", "Results")

    rows = []
    for r in test_results["rows"]:
        if r[i_name] != beach_name:
            continue
        date = _normalize_date(r[i_date])
        indicator = (r[i_ind] or "").strip()
        result = (r[i_res] or "").strip()
        if not date or date.lower() == "null":
            continue  # no reading for this row
        threshold = SINGLE_SAMPLE_THRESHOLDS.get(indicator, DEFAULT_THRESHOLD)
        rows.append([date, indicator, threshold, result])

    # Sort newest first to match the legacy table ordering.
    def _key(row):
        from datetime import datetime
        for fmt in ("%m/%d/%Y %I:%M:%S %p", "%m/%d/%Y"):
            try:
                return datetime.strptime(row[0], fmt)
            except ValueError:
                continue
        return datetime.min
    rows.sort(key=_key, reverse=True)

    headers = ["Date and Time", "Indicator", "Threshold: Single-Sample", "Results"]
    samples = {"headers": headers, "rows": rows}

    # CSV in the legacy 5-column shape (duplicate Date column at index 3) so the
    # existing parse_samples_csv / archive_results_csv code paths work unchanged.
    csv_lines = ["Date and Time,Indicator,Threshold: Single-Sample,Date and Time,Results"]
    for d, ind, thr, res in rows:
        csv_lines.append(f"{d},{ind},{thr},{d},{res}")
    samples_csv = "\n".join(csv_lines) + "\n"
    return samples, samples_csv


def build_status(map_data: dict, beach_name: str) -> dict | None:
    cols = map_data["columns"]
    i_name = _col(cols, "Beach Name", "Name")
    i_status = _col(cols, "Beach Status", "Status")
    i_town = _col(cols, "Town")
    for r in map_data["rows"]:
        if r[i_name] == beach_name:
            return {
                "name": r[i_name],
                "status": (r[i_status] or "").strip(),
                "town": (r[i_town] or "").strip(),
            }
    return None


def fetch_all(beach_name: str = BEACH_NAME) -> dict:
    import tempfile
    from pathlib import Path
    from playwright.sync_api import sync_playwright

    errors: list[str] = []
    token = fetch_public_token()

    tmpdir = tempfile.mkdtemp(prefix="tableau-embed-")
    for view in ("TestResultsTable", "Map"):
        Path(tmpdir, f"{view}.html").write_text(_embed_html(token, view), encoding="utf-8")

    httpd, port = _serve_dir(tmpdir)
    base_url = f"http://127.0.0.1:{port}"
    out: dict = {"errors": errors}
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                page = browser.new_page()
                test_results = _read_worksheet(page, base_url, "TestResultsTable")
                out["testResultsRowCount"] = len(test_results["rows"])
                samples, samples_csv = build_samples(test_results, beach_name)
                out["samples"] = samples
                out["samplesCsv"] = samples_csv

                try:
                    map_data = _read_worksheet(page, base_url, "Map")
                    status = build_status(map_data, beach_name)
                    if status:
                        out["status"] = status
                    else:
                        errors.append("status: beach not found in Map worksheet")
                except Exception as e:  # noqa: BLE001
                    errors.append(f"status: {e}")
            finally:
                browser.close()
    finally:
        httpd.shutdown()
    return out


def main() -> int:
    try:
        out = fetch_all()
    except Exception as e:  # noqa: BLE001
        print(json.dumps({"errors": [f"fatal: {e}"]}))
        return 1
    print(json.dumps(out, ensure_ascii=False))
    return 0 if out.get("samples") else 1


if __name__ == "__main__":
    sys.exit(main())
