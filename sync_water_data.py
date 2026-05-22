#!/usr/bin/env python3
"""
Fetch the latest beach water-quality data from Massachusetts DPH and CSO incident
data from MassDEP, then write static JSON files under data/ for the dashboard.

Designed to run on a schedule (GitHub Actions) so the deployed site is always
"as fresh as the last sync" without any runtime server-side code.

Outputs:
    data/samples.json         { headers, rows }                — sample readings
    data/status.json          { name, status, town }           — current beach status
    data/cso.json             { results, rowCount, windowStart } — CSO incidents
    data/meta.json            { lastSynced, season, ... }      — sync metadata
    archive/<beach>/<year>.csv                                  — appended/deduped per-year CSV

Stdlib only; no third-party deps.
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
DATA_DIR = REPO_ROOT / "data"
ARCHIVE_DIR = REPO_ROOT / "archive"

BEACH_NAME = "Shannon Beach @ Upper Mystic (DCR)"
MUNICIPALITY = "WINCHESTER"

RESULTS_URL = (
    "https://datavisualization.dph.mass.gov/views/"
    "BeachesDashboard-CloudVersion-2025/Results.csv?refresh=y"
)
BEACHLIST_URL = (
    "https://datavisualization.dph.mass.gov/views/"
    "BeachesDashboard-CloudVersion-2025/BeachList.csv?:refresh=y"
)
CSO_URL_TEMPLATE = (
    "https://eeaonline.eea.state.ma.us/dep/CSOAPI/api/Incident/"
    "GetIncidentsBySearchFields/?municipality={muni}&pageNumber=1"
    "&incidentFromDate={from_date}"
)

DPH_REFERER = "https://datavisualization.dph.mass.gov"
CSO_REFERER = "https://eeaonline.eea.state.ma.us/portal/dep/cso-data-portal/"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
)

SCHEMA_VERSION = 1
HTTP_TIMEOUT = 60


# ---------- season helpers ----------

def _memorial_day(year: int) -> dt.date:
    may31 = dt.date(year, 5, 31)
    return may31 - dt.timedelta(days=may31.weekday())  # back to Monday


def _labor_day(year: int) -> dt.date:
    sep1 = dt.date(year, 9, 1)
    return sep1 + dt.timedelta(days=(7 - sep1.weekday()) % 7)  # forward to Monday


# One-week buffer on each end catches early/late readings the state may publish
# outside the official Memorial Day–Labor Day window.
_SEASON_BUFFER = dt.timedelta(days=7)


def _season_start(year: int) -> dt.date:
    return _memorial_day(year) - _SEASON_BUFFER


def _season_end(year: int) -> dt.date:
    return _labor_day(year) + _SEASON_BUFFER


def is_in_season(today: dt.date | None = None) -> bool:
    today = today or dt.date.today()
    return _season_start(today.year) <= today <= _season_end(today.year)


def most_recent_season_year(today: dt.date | None = None) -> int:
    today = today or dt.date.today()
    return today.year if today > _season_end(today.year) else today.year - 1


# ---------- http ----------

def http_get(url: str, referer: str, *, accept: str = "*/*") -> bytes:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Referer": referer,
            "Accept": accept,
        },
    )
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
        return resp.read()


# ---------- fetchers ----------

def fetch_samples_csv() -> str:
    url = RESULTS_URL + "&Name=" + urllib.parse.quote(BEACH_NAME)
    body = http_get(url, DPH_REFERER, accept="text/csv")
    return body.decode("utf-8-sig", errors="replace")


def fetch_status_csv() -> str:
    url = BEACHLIST_URL + "&Name=" + urllib.parse.quote(BEACH_NAME)
    body = http_get(url, DPH_REFERER, accept="text/csv")
    return body.decode("utf-8-sig", errors="replace")


def fetch_cso(window_days: int = 14) -> tuple[dict, str]:
    window_start = dt.date.today() - dt.timedelta(days=window_days)
    # The CSO API expects DD/MM/YYYY (see beachdata.php and mystic.html)
    from_date = f"{window_start.day:02d}/{window_start.month:02d}/{window_start.year}"
    url = CSO_URL_TEMPLATE.format(
        muni=urllib.parse.quote(MUNICIPALITY),
        from_date=urllib.parse.quote(from_date),
    )
    body = http_get(url, CSO_REFERER, accept="application/json")
    return json.loads(body.decode("utf-8")), window_start.isoformat()


# ---------- parsers / transformers ----------

def parse_samples_csv(csv_text: str) -> dict:
    """
    Parse the DPH Results.csv. It has a duplicate date column at index 3 which
    the front-end currently skips — we do the same here so consumers see clean
    {Date, Indicator, Threshold, Results} rows.
    """
    reader = csv.reader(io.StringIO(csv_text))
    rows = [r for r in reader if any(c.strip() for c in r)]
    if not rows:
        return {"headers": [], "rows": []}

    def squash(row: list[str]) -> list[str]:
        # Drop the duplicate Date column (original index 3) when present.
        if len(row) >= 5:
            return [row[0], row[1], row[2], row[4]]
        return row

    headers = squash(rows[0])
    # Normalize threshold header which upstream sometimes labels
    # "Threshold: Single-Sample" — keep upstream label verbatim.
    data = [squash(r) for r in rows[1:]]
    return {"headers": headers, "rows": data}


def parse_status_csv(csv_text: str) -> dict | None:
    reader = csv.reader(io.StringIO(csv_text))
    rows = [r for r in reader if any(c.strip() for c in r)]
    if len(rows) < 2:
        return None
    # First data row is the beach we asked for (filtered by ?Name= upstream).
    cells = [c.strip() for c in rows[1]]
    if len(cells) < 3:
        return None
    return {"name": cells[0], "status": cells[1], "town": cells[2]}


# ---------- archive merge ----------

def _sanitize_beach_key(name: str) -> str:
    key = re.sub(r"[^a-zA-Z0-9]+", "_", name)
    return key.strip("_")


def _row_year(row: list[str]) -> int | None:
    if not row or not row[0]:
        return None
    m = re.match(r"^\s*\"?(\d{1,2})/(\d{1,2})/(\d{4})", row[0])
    return int(m.group(3)) if m else None


def _row_timestamp(line: str) -> float:
    m = re.match(
        r'^"?(\d{1,2})/(\d{1,2})/(\d{4})\s+(\d{1,2}):(\d{2}):(\d{2})\s*(AM|PM)?',
        line, re.IGNORECASE,
    )
    if not m:
        return 0.0
    month, day, year, hour, minute, sec, ampm = m.groups()
    h = int(hour)
    if ampm:
        ap = ampm.upper()
        if ap == "PM" and h < 12:
            h += 12
        if ap == "AM" and h == 12:
            h = 0
    try:
        return dt.datetime(
            int(year), int(month), int(day), h, int(minute), int(sec)
        ).timestamp()
    except ValueError:
        return 0.0


def archive_results_csv(csv_text: str, beach_name: str) -> None:
    """
    Mirror beachdata.php archiveResultsCsv: split upstream rows by year, merge
    with existing per-year archive (dedupe by exact line), sort descending.
    """
    lines = [ln for ln in re.split(r"\r\n|\r|\n", csv_text.strip()) if ln.strip()]
    if len(lines) < 2:
        return

    header = lines[0]
    rows_by_year: dict[str, list[str]] = {}
    for line in lines[1:]:
        m = re.match(r'^"?\d{1,2}/\d{1,2}/(\d{4})', line)
        if m:
            rows_by_year.setdefault(m.group(1), []).append(line)

    beach_key = _sanitize_beach_key(beach_name)
    out_dir = ARCHIVE_DIR / beach_key
    out_dir.mkdir(parents=True, exist_ok=True)

    for year, new_rows in rows_by_year.items():
        path = out_dir / f"{year}.csv"
        merged: dict[str, None] = {}
        if path.exists():
            existing = path.read_text(encoding="utf-8").strip().splitlines()
            # Drop existing header
            for line in existing[1:]:
                if line.strip():
                    merged[line] = None
        for line in new_rows:
            merged[line] = None

        ordered = sorted(merged.keys(), key=_row_timestamp, reverse=True)
        path.write_text(header + "\n" + "\n".join(ordered) + "\n", encoding="utf-8")


# ---------- writers ----------

def write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


# ---------- main ----------

def main() -> int:
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    in_season = is_in_season()
    today = dt.date.today()
    season_label = "in" if in_season else "off"

    sample_count = 0
    cso_count = 0
    samples_status = "skipped"
    status_status = "skipped"
    cso_status = "skipped"
    errors: list[str] = []

    # --- Samples + status: in-season only (upstream stops publishing off-season) ---
    if in_season:
        try:
            samples_csv = fetch_samples_csv()
            samples = parse_samples_csv(samples_csv)
            sample_count = len(samples["rows"])
            write_json(DATA_DIR / "samples.json", samples)
            samples_status = "ok"
            try:
                archive_results_csv(samples_csv, BEACH_NAME)
            except Exception as e:  # noqa: BLE001
                errors.append(f"archive: {e}")
        except (urllib.error.URLError, urllib.error.HTTPError, ValueError) as e:
            errors.append(f"samples: {e}")
            samples_status = "error"

        try:
            status_csv = fetch_status_csv()
            status = parse_status_csv(status_csv)
            if status:
                write_json(DATA_DIR / "status.json", status)
                status_status = "ok"
            else:
                errors.append("status: empty or unparseable response")
                status_status = "error"
        except (urllib.error.URLError, urllib.error.HTTPError, ValueError) as e:
            errors.append(f"status: {e}")
            status_status = "error"
    else:
        # Off-season: surface an explicit "Closed for Season" status so the page
        # can render a consistent state if it does decide to read status.json.
        write_json(
            DATA_DIR / "status.json",
            {
                "name": BEACH_NAME,
                "status": "Closed for Season",
                "town": "Winchester",
            },
        )
        status_status = "off-season"

    # --- CSO: fetched year-round; incidents can occur outside swim season ---
    try:
        cso_payload, window_start = fetch_cso(window_days=14)
        results = cso_payload.get("results") or []
        cso_count = len(results)
        write_json(
            DATA_DIR / "cso.json",
            {
                "results": results,
                "rowCount": cso_count,
                "windowStart": window_start,
            },
        )
        cso_status = "ok"
    except (urllib.error.URLError, urllib.error.HTTPError, ValueError) as e:
        errors.append(f"cso: {e}")
        cso_status = "error"

    # --- Meta last so it reflects everything else that ran. ---
    meta = {
        "lastSynced": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "season": season_label,
        "today": today.isoformat(),
        "beach": BEACH_NAME,
        "mostRecentSeasonYear": most_recent_season_year(today),
        "samples": {"status": samples_status, "count": sample_count},
        "status": {"status": status_status},
        "cso": {"status": cso_status, "count": cso_count},
        "errors": errors,
        "schemaVersion": SCHEMA_VERSION,
    }
    write_json(DATA_DIR / "meta.json", meta)

    print(json.dumps(meta, indent=2))
    # Non-zero exit only if every fetcher failed; partial failures still let the
    # workflow commit whatever did succeed.
    if errors and samples_status != "ok" and status_status not in {"ok", "off-season"} and cso_status != "ok":
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
