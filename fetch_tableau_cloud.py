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
    "allSamples": { "headers": [...], "beaches": { "<name>": { "town", "rows" } } },
    "beaches": [ { "name", "town", "status" }, ... ],
    "testResultsRowCount": <int>,
    "errors": [ ... ]
  }

`samples`/`samplesCsv`/`status` cover the default beach (for the per-beach archive
and the SW-compat status.json); `allSamples`/`beaches` cover every beach in the
workbook and power the front-end's Town/Beach selector.

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
# Massachusetts bathing-beach standards (105 CMR 445) differ by water type — e.g.
# the Enterococci single-sample limit is 61 CFU/100 ml at freshwater beaches but
# 104 at marine beaches — and the Map worksheet's "Marine or Freshwater" field
# says which standard applies to each beach (see build_water_types). A beach with
# no Map match falls back to the freshwater values (the pre-split behavior).
SINGLE_SAMPLE_THRESHOLDS = {
    "Freshwater": {"Enterococci": "61", "E. Coli": "235"},
    "Marine": {"Enterococci": "104"},
}
GEOMEAN_THRESHOLDS = {
    "Freshwater": {"Enterococci": "33", "E. Coli": "126"},
    "Marine": {"Enterococci": "35"},
}
DEFAULT_WATER_TYPE = "Freshwater"


def _threshold(table: dict, water_type: str, indicator: str) -> str:
    """Threshold for a water type + indicator, falling back to the freshwater
    value for the indicator (marine beaches are only tested with Enterococci, so
    an unexpected combo means bad upstream data, not a real marine standard),
    then to freshwater Enterococci."""
    by_type = table.get(water_type) or table[DEFAULT_WATER_TYPE]
    return (by_type.get(indicator)
            or table[DEFAULT_WATER_TYPE].get(indicator)
            or table[DEFAULT_WATER_TYPE]["Enterococci"])

PAGE_TIMEOUT_MS = 60_000
RESULT_POLL_SECONDS = 60
# One initial attempt plus this many retries. The viz occasionally never reaches
# "firstinteractive" within the poll window (status stays "init") — a transient
# headless-render flake, not a real block — so reloading usually succeeds.
FETCH_RETRIES = 2
RETRY_BACKOFF_SECONDS = 3


def fetch_public_token() -> str:
    req = urllib.request.Request(
        TOKEN_URL,
        headers={
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://www.mass.gov/",
            "Origin": "https://www.mass.gov",
        },
    )
    # The token endpoint occasionally times out; a single blip shouldn't abort an
    # otherwise-healthy fetch, so retry a few times with a short backoff.
    import time
    last_err: Exception | None = None
    for attempt in range(1 + FETCH_RETRIES):
        if attempt:
            time.sleep(RETRY_BACKOFF_SECONDS)
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                payload = json.loads(r.read().decode("utf-8", "replace"))
            token = payload.get("token")
            if not token:
                raise RuntimeError(f"token endpoint returned no token: {payload!r}")
            return token
        except Exception as e:  # noqa: BLE001 — transient network/HTTP; retry
            last_err = e
    raise RuntimeError(f"token fetch failed after {1 + FETCH_RETRIES} attempts: {last_err}")


def _embed_html(token: str, view: str) -> str:
    # Loads a single worksheet and reads its full summary data via the
    # Embedding API. ignoreSelection + maxRows:0 returns every row unfiltered.
    #
    # Before reading, force a data-source refresh. DPH's shared "Beaches
    # DataSource" lags the live data by ~a day: getSummaryDataAsync() otherwise
    # returns a stale cached extract (e.g. newest reading a full day behind what
    # the workbook's own Download -> Crosstab produces). refreshAsync() re-queries
    # the source so the summary read matches the live dashboard. Best-effort: a
    # refresh failure still falls through to a read of whatever's cached rather
    # than failing the whole fetch.
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
    const refreshLog = [];
    try {{
      const dss = await ws.getDataSourcesAsync();
      for (const ds of dss) {{
        try {{ await ds.refreshAsync(); refreshLog.push("ok:" + ds.name); }}
        catch (e) {{ refreshLog.push("fail:" + String(e && e.message || e)); }}
      }}
    }} catch (e) {{ refreshLog.push("getDataSources:" + String(e && e.message || e)); }}
    const dt = await ws.getSummaryDataAsync({{ maxRows: 0, ignoreSelection: true }});
    window.__r = {{
      status: "ok",
      refreshLog,
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
    """Load an embed page for one worksheet and return its summary-data result.

    Re-navigating reloads the viz from scratch (the inline script resets
    window.__r), so on a transient failure we simply reload and try again, up to
    FETCH_RETRIES extra times before giving up.
    """
    import time
    last = None
    for attempt in range(1 + FETCH_RETRIES):
        if attempt:
            time.sleep(RETRY_BACKOFF_SECONDS)
        result = None
        try:
            page.goto(f"{base_url}/{view}.html", wait_until="load", timeout=PAGE_TIMEOUT_MS)
            for _ in range(RESULT_POLL_SECONDS):
                result = page.evaluate("() => window.__r")
                if result and result.get("status") in ("ok", "error", "loaderror"):
                    break
                time.sleep(1)
        except Exception as e:  # noqa: BLE001 — a goto/eval timeout is a transient
            # flake; record it and reload rather than aborting the whole fetch.
            result = {"status": "loaderror", "message": str(e)}
        if result and result.get("status") == "ok":
            return result
        last = result
    raise RuntimeError(
        f"{view}: {json.dumps(last)[:300] if last else 'no result'} "
        f"(after {1 + FETCH_RETRIES} attempts)"
    )


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


SAMPLE_HEADERS = ["Date and Time", "Indicator", "Threshold: Single-Sample", "Results"]


def _row_sort_key(row: list[str]):
    # Sort newest first to match the legacy table ordering.
    from datetime import datetime
    for fmt in ("%m/%d/%Y %I:%M:%S %p", "%m/%d/%Y"):
        try:
            return datetime.strptime(row[0], fmt)
        except ValueError:
            continue
    return datetime.min


def build_all_samples(test_results: dict, water_types: dict | None = None) -> dict:
    """Group every beach's readings into {headers, beaches: {name: {town, rows}}}.

    The TestResultsTable worksheet carries all beaches statewide; we keep them
    all so the front-end can offer a Town/Beach selector and filter client-side.
    water_types maps beach name -> "Marine"/"Freshwater" (from the Map worksheet)
    so each beach gets the threshold standard that actually applies to it.
    """
    water_types = water_types or {}
    cols = test_results["columns"]
    i_name = _col(cols, "Name")
    i_date = _col(cols, "Date")
    i_ind = _col(cols, "Indicator")
    i_res = _col(cols, "AGG(Results (CFU/100 ml))", "Results")
    try:
        i_town = _col(cols, "Town")
    except KeyError:
        i_town = None
    try:
        i_gm = _col(cols, "AGG(GeoMean (CFU/100 ml))", "GeoMean")
    except KeyError:
        i_gm = None

    # name -> {town, items: [(date, indicator, threshold, result, geomean), ...]}
    raw: dict = {}
    for r in test_results["rows"]:
        name = (r[i_name] or "").strip()
        if not name:
            continue
        date = _normalize_date(r[i_date])
        if not date or date.lower() == "null":
            continue  # no reading for this row
        indicator = (r[i_ind] or "").strip()
        result = (r[i_res] or "").strip()
        water_type = water_types.get(name, DEFAULT_WATER_TYPE)
        threshold = _threshold(SINGLE_SAMPLE_THRESHOLDS, water_type, indicator)
        town = (r[i_town].strip() if i_town is not None and r[i_town] else "")
        geomean = (r[i_gm].strip() if i_gm is not None and r[i_gm] else "")
        d = raw.setdefault(name, {"town": town, "items": []})
        if town and not d["town"]:
            d["town"] = town
        d["items"].append((date, indicator, threshold, result, geomean))

    beaches: dict = {}
    for name, d in raw.items():
        items = sorted(d["items"], key=lambda it: _row_sort_key([it[0]]), reverse=True)
        entry = {
            "town": d["town"],
            "rows": [[it[0], it[1], it[2], it[3]] for it in items],
        }
        # The geometric mean is cumulative, so the most recent reading carries the
        # current-season value. Surface just that latest non-null geomean.
        for it in items:
            gm = it[4]
            if gm and gm.lower() != "null":
                entry["geoMean"] = {
                    "date": it[0],
                    "indicator": it[1],
                    "threshold": _threshold(
                        GEOMEAN_THRESHOLDS,
                        water_types.get(name, DEFAULT_WATER_TYPE),
                        it[1]),
                    "value": gm,
                }
                break
        beaches[name] = entry

    return {"headers": list(SAMPLE_HEADERS), "beaches": beaches}


def build_all_statuses(map_data: dict) -> list[dict]:
    """Every beach's current status from the Map worksheet: [{name, town, status}]."""
    cols = map_data["columns"]
    i_name = _col(cols, "Beach Name", "Name")
    i_status = _col(cols, "Beach Status", "Status")
    try:
        i_town = _col(cols, "Town")
    except KeyError:
        i_town = None
    out: list[dict] = []
    seen: set[str] = set()
    for r in map_data["rows"]:
        name = (r[i_name] or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        out.append({
            "name": name,
            "status": (r[i_status] or "").strip(),
            "town": (r[i_town].strip() if i_town is not None and r[i_town] else ""),
        })
    return out


def build_water_types(map_data: dict) -> dict:
    """Map full beach name -> "Marine" / "Freshwater" from the Map worksheet,
    which determines the threshold standard that applies (105 CMR 445)."""
    cols = map_data["columns"]
    i_name = _col(cols, "Beach Name", "Name")
    try:
        i_type = _col(cols, "Marine or Freshwater")
    except KeyError:
        return {}
    out: dict[str, str] = {}
    for r in map_data["rows"]:
        name = (r[i_name] or "").strip()
        wtype = (r[i_type] or "").strip()
        if name and wtype and name not in out:
            out[name] = wtype
    return out


def build_closure_reasons(closure_data: dict) -> dict:
    """Map full beach name -> stated closure reason from the Closures dashboard's
    ClosureTable worksheet (e.g. "Bacterial Exceedance", "Harmful Cyanobacteria
    Bloom", "CSO/SSO event"). Keyed by the same full beach name used by the Map
    and TestResultsTable worksheets, so it joins directly onto a beach's status.
    """
    cols = closure_data["columns"]
    i_beach = _col(cols, "Beach", "Beach Name", "Name")
    i_reason = _col(cols, "Closure Reason", "Reason")
    out: dict[str, str] = {}
    for r in closure_data["rows"]:
        name = (r[i_beach] or "").strip()
        reason = (r[i_reason] or "").strip()
        if name and reason and name not in out:
            out[name] = reason
    return out


def build_beach_index(all_samples: dict, statuses: list[dict]) -> list[dict]:
    """Beaches with readings, carrying their Map status when one exists, sorted by
    town then name, for the front-end Town/Beach selector.

    The Map worksheet names some locations at the site / water-body level (e.g.
    "Lake Dennison State Park (DCR)") while TestResultsTable reports readings at
    the individual sampling-point level (e.g. "... @Day Use Beach"). Those
    parent/aggregate Map entries have a status but no point-level readings, so we
    exclude such status-only entries — the selector lists only beaches that
    actually have data. A reading point with no matching Map status is kept with
    an empty status.
    """
    status_by_name = {s["name"]: s for s in statuses}
    out: list[dict] = []
    for name, entry in (all_samples.get("beaches") or {}).items():
        st = status_by_name.get(name)
        beach = {
            "name": name,
            "town": entry.get("town") or (st.get("town", "") if st else ""),
            "status": st.get("status", "") if st else "",
        }
        if st and st.get("reason"):
            beach["reason"] = st["reason"]
        out.append(beach)
    return sorted(out, key=lambda b: (b["town"].lower(), b["name"].lower()))


def build_samples(test_results: dict, beach_name: str,
                  water_types: dict | None = None) -> tuple[dict, str]:
    cols = test_results["columns"]
    i_name = _col(cols, "Name")
    i_date = _col(cols, "Date")
    i_ind = _col(cols, "Indicator")
    i_res = _col(cols, "AGG(Results (CFU/100 ml))", "Results")

    water_type = (water_types or {}).get(beach_name, DEFAULT_WATER_TYPE)
    rows = []
    for r in test_results["rows"]:
        if r[i_name] != beach_name:
            continue
        date = _normalize_date(r[i_date])
        indicator = (r[i_ind] or "").strip()
        result = (r[i_res] or "").strip()
        if not date or date.lower() == "null":
            continue  # no reading for this row
        threshold = _threshold(SINGLE_SAMPLE_THRESHOLDS, water_type, indicator)
        rows.append([date, indicator, threshold, result])

    rows.sort(key=_row_sort_key, reverse=True)

    headers = list(SAMPLE_HEADERS)
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
    for view in ("TestResultsTable", "Map", "Closures"):
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

                # The Map worksheet is read before the sample builders because it
                # carries each beach's "Marine or Freshwater" designation, which
                # selects the threshold standard the builders stamp on each row.
                # Still best-effort: on a Map failure the builders fall back to
                # freshwater thresholds for everyone (the pre-split behavior).
                statuses: list[dict] = []
                water_types: dict = {}
                map_data = None
                try:
                    map_data = _read_worksheet(page, base_url, "Map")
                    statuses = build_all_statuses(map_data)
                    water_types = build_water_types(map_data)
                except Exception as e:  # noqa: BLE001
                    errors.append(f"status: {e}")

                # Default beach (back-compat: drives the per-beach archive CSV and
                # the SW-compat status.json) plus the full all-beaches dataset that
                # powers the front-end Town/Beach selector.
                samples, samples_csv = build_samples(test_results, beach_name, water_types)
                out["samples"] = samples
                out["samplesCsv"] = samples_csv
                all_samples = build_all_samples(test_results, water_types)
                out["allSamples"] = all_samples

                if map_data is not None:
                    status = build_status(map_data, beach_name)
                    if status:
                        out["status"] = status
                    else:
                        errors.append("status: beach not found in Map worksheet")

                # Stated closure reason (e.g. "Bacterial Exceedance") from the Closures
                # dashboard's ClosureTable, attached to any currently-Closed beach.
                # Best-effort: a failure here means no reason is shown, never a failed
                # sync. Gated on the live status so a beach that has since reopened
                # doesn't carry a stale seasonal reason.
                try:
                    closure_data = _read_worksheet(page, base_url, "Closures")
                    reasons = build_closure_reasons(closure_data)
                    for s in statuses:
                        if s.get("status", "").strip().lower() == "closed":
                            r = reasons.get(s["name"])
                            if r:
                                s["reason"] = r
                    cur = out.get("status")
                    if cur and cur.get("status", "").strip().lower() == "closed":
                        r = reasons.get(cur["name"])
                        if r:
                            cur["reason"] = r
                except Exception as e:  # noqa: BLE001
                    errors.append(f"closures: {e}")

                out["beaches"] = build_beach_index(all_samples, statuses)
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
