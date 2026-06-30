# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is a beach water quality monitoring dashboard for Shannon Beach @ Upper Mystic
(DCR), displaying data from the Massachusetts Department of Public Health.

The site is **fully static** and served from GitHub Pages at `water.jalkut.com`. A
scheduled GitHub Action runs `sync_water_data.py` periodically to fetch upstream
data and commit refreshed `data/*.json` and `archive/<beach>/<year>.csv` files
back to the repo. The page loads only same-origin static files at runtime — no
PHP proxy, no CORS workaround, no live API call from the browser.

## Architecture

### Data Flow

```
GitHub Action (15-min cron)
  └─► sync_water_data.py
        ├─► in-season samples + status:
        │     PRIMARY  fetch_tableau_cloud.py (headless Chromium / Playwright)
        │              reads the live "Beach Water Quality Dashboard" on
        │              Tableau Cloud ─► data/samples.json + data/status.json
        │     ON FAIL  publish an "unavailable" state (empty status.json +
        │              current-year-only samples.json) — NOT the frozen legacy
        │              endpoints, which would clobber good data with 2025 values
        ├─► fetch CSO incidents (Mass DEP) ─► data/cso.json
        ├─► merge samples into archive/<beach>/<year>.csv
        └─► write data/meta.json (lastSynced, season, samples.source, ...)
  └─► commit refreshed data + archive
  └─► stage site/ and deploy to GitHub Pages
```

At page-load time, `index.html` fetches `data/samples.json`, `data/status.json`,
`data/cso.json`, and `data/meta.json` directly. Off-season it reads
`archive/Shannon_Beach_Upper_Mystic_DCR/<year>.csv` instead of `data/samples.json`.

**Why the headless browser?** In 2026 DPH moved the live dashboard from the old
Tableau Server (`datavisualization.dph.mass.gov`, workbook
`BeachesDashboard-CloudVersion-2025`) to Tableau Cloud
(`prod-useast-b.online.tableau.com`, site `eohhspublic`, workbook
`BeachWaterQualityDashboard`). The old per-beach CSV endpoints are frozen at the
2025 season's end. The current readings live only in the new workbook's
`TestResultsTable` worksheet, which has no static CSV URL (the data loads lazily
into the live viz). The official "Download Full Dataset"
button is a Tableau extension that reads it via `getSummaryDataAsync()`;
`fetch_tableau_cloud.py` does the same with the Embedding API in a headless
browser, authenticating with the public connected-app JWT from
`publicdashboardtoken.mass.gov`. `meta.json.samples.source` records which path
ran (`browser` | `none`).

**Export reliability.** The export normally works: in late June 2026 nearly every
sync succeeded with `samples.source` = `browser`. (There was an earlier stretch
around May 2026 where DPH had revoked the public "Guest" group's summary-data
download permission and `getSummaryDataAsync()` returned `403
PermissionDeniedException`; that has since been restored.) The failures seen now
are usually **transient load flakes**, not access blocks: the headless viz
occasionally never reaches `firstinteractive` within the poll window (the embed's
`window.__r` stays `{status:"init"}`), which surfaces as a `fatal: ...: {"status":
"init"}` error — distinct from a `403`. To absorb these, `fetch_tableau_cloud.py`
reloads and retries (`1 + FETCH_RETRIES`, currently 3 attempts total, 3s backoff)
before giving up. On a genuine/persistent failure the in-season sync still
publishes an explicit **"unavailable"** state (`samples.source` = `none`, empty
`status.json`) rather than falling back to the frozen 2025 legacy endpoints; the
page then shows "Information Unavailable" / "Latest readings temporarily
unavailable" instead of a misleading stale value, and the next 15-min run
typically self-heals. `.github/workflows/probe-export.yml` runs the real fetch
daily as a watchdog and opens a GitHub issue if the export breaks for an extended
period. The legacy `datavisualization.dph.mass.gov` CSV fetchers remain in
`sync_water_data.py` but are no longer wired into the in-season path.

### Key Files

- **`index.html`** — Main dashboard. Vanilla JS, no frameworks. Loads static JSON
  from `data/`, renders current status, CSO incidents card, samples table, and a
  "Data last synced" line driven by `meta.json`.
- **`simple.html`** — Tiny page kept for diagnostics.
- **`sw.js`** — Service worker (currently registration is disabled in `index.html`).
  Reads the same `data/*.json` and posts a desktop notification on status change.
- **`sync_water_data.py`** — Python 3 stdlib orchestrator. Stdlib only (urllib +
  csv + json). Shells out to `fetch_tableau_cloud.py` for the in-season primary
  fetch; everything else (CSO, archiving, meta, the "unavailable" fallback) is
  stdlib. `preserve_current_year_samples()` keeps the page honest when the live
  fetch fails (drops stale prior-year rows rather than showing them as current).
- **`fetch_tableau_cloud.py`** — Playwright/headless-Chromium fetcher for the live
  Tableau Cloud workbook. **Only file with a third-party dep.** Standalone CLI that
  prints JSON (`samples`, `samplesCsv`, `status`, …); run via subprocess so the
  orchestrator stays importable without Playwright. Run it directly to debug:
  `python3 fetch_tableau_cloud.py`.
- **`.github/workflows/sync.yml`** — Every-15-min cron. Installs Playwright + Chromium,
  runs the sync script, commits data changes, stages `site/`, deploys via
  `actions/deploy-pages`. `fetch_tableau_cloud.py` is NOT deployed to the site.
- **`.github/workflows/probe-export.yml`** — Daily watchdog for the Tableau export
  (see "Export reliability" above). Runs `fetch_tableau_cloud.py` and opens a GitHub
  issue if the summary-data export breaks for an extended period; silent while it
  works. Does not deploy.
- **`CNAME`** — `water.jalkut.com`.

### Upstream Endpoints

Primary (in-season samples + status), consumed by `fetch_tableau_cloud.py` via the
Tableau JS Embedding API in a headless browser:

- Public access token: `https://publicdashboardtoken.mass.gov/tokens/requestpublicaccess?connectedapp=DPH-BCEH-BDD-BD`
  → `{ "token": "<JWT>" }`
- Workbook: `https://prod-useast-b.online.tableau.com/t/eohhspublic/views/BeachWaterQualityDashboard/<view>`
  — `TestResultsTable` worksheet → all-beach readings (Town, Name, Date, Indicator,
  GeoMean, Results); `Map` worksheet → per-beach status (Beach Name, Beach Status, …).
  Both read with `getSummaryDataAsync({maxRows:0, ignoreSelection:true})`.

Legacy fallback only (frozen at 2025 season end), consumed by `sync_water_data.py`:

- Sample data: `https://datavisualization.dph.mass.gov/views/BeachesDashboard-CloudVersion-2025/Results.csv?refresh=y&Name=<beach>`
  — needs `Referer: https://datavisualization.dph.mass.gov`
- Beach status: `https://datavisualization.dph.mass.gov/views/BeachesDashboard-CloudVersion-2025/BeachList.csv?:refresh=y&Name=<beach>`
  — same referer

CSO incidents (year-round, stdlib):

- `https://eeaonline.eea.state.ma.us/dep/CSOAPI/api/Incident/GetIncidentsBySearchFields/?municipality=WINCHESTER&pageNumber=1&incidentFromDate=<DD/MM/YYYY>`
  — needs `Referer: https://eeaonline.eea.state.ma.us/portal/dep/cso-data-portal/`

### Static Data Shapes

- `data/samples.json` → `{ "headers": [...], "rows": [[date, indicator, threshold, results], ...] }`
- `data/status.json` → `{ "name", "status", "town" }`
- `data/cso.json` → `{ "results": [...], "rowCount": N, "windowStart": "YYYY-MM-DD" }`
  — Front-end still filters `results` to Mystic Lake by `waterBodyDescription`.
- `data/meta.json` → `{ "lastSynced", "season", "today", "beach", "samples", "status", "cso", "errors", "schemaVersion" }`

### Season Logic

Memorial Day through Labor Day = "in-season". The Python sync script and the JS
both implement the same calendar math. Off-season, upstream stops publishing
samples, so the script writes a stub `status.json` (`"Closed for Season"`) and
the page reads from `archive/Shannon_Beach_Upper_Mystic_DCR/<year>.csv` for the
most recent completed season.

## Local Development

The site is fully static. Any static file server works:

```bash
python3 -m http.server 8000
```

Then open `http://localhost:8000/`. The page reads from `data/*.json`
and `archive/...csv` — both committed to the repo — so it works fully offline
once the repo is cloned.

To regenerate the static data locally:

```bash
# One-time: the in-season primary fetch needs Playwright + Chromium.
python3 -m venv .venv && . .venv/bin/activate
pip install playwright && playwright install chromium

python sync_water_data.py
```

This rewrites `data/*.json` and appends to `archive/<beach>/<year>.csv`. In-season
it drives the live Tableau Cloud dashboard via headless Chromium
(`fetch_tableau_cloud.py`); CSO data uses stdlib only. If Playwright isn't
installed (or the Tableau fetch fails after its retries — see "Export reliability"
above), the sync still runs: it logs the browser error and publishes the
"unavailable" state, with `meta.json.samples.source` = `none`.

### Test Mode

Visit `?test=1` for test mode. Use URL overrides to load specific fixtures:
- `?data-url=test-data/results-typical.csv`
- `?status-url=test-data/status-open.csv`
- `?cso-url=test-data/cso-mystic-incident.json`

The page's `fetchSamples` / `fetchStatus` helpers accept either JSON
(`*.json`) or the upstream CSV format, so existing CSV fixtures continue to work.

### Test Mode Configuration

When using `?test=1`:
- 30-second sync frequency (vs. 5-minute prod default)
- Test config persisted in `BeachStatusDB` IndexedDB so the service worker sees it
- Debug panel exposes sync frequency, status override, SW controls, DB clear

## Deployment

GitHub Pages source = **GitHub Actions** (not branch). The workflow at
`.github/workflows/sync.yml`:

1. Runs every 15 min on cron (`2,17,32,47 * * * *`)
2. Executes `sync_water_data.py`
3. Commits any changed `data/` or `archive/` files back to `main`
   (uses `[skip ci]` so the resulting push doesn't loop into another deploy)
4. Stages a `site/` directory containing only the public files
   (`index.html`, `simple.html`, `sw.js`, `data/`, `archive/`,
   `test-data/`, `CNAME`) — `sync_water_data.py`, `beachdata.php`, `.htaccess`,
   `.claude/`, and shell scripts are not deployed
5. Uploads as a Pages artifact and deploys via `actions/deploy-pages@v4`

DNS: `water.jalkut.com` CNAME → `<github-pages-host>`. Custom domain configured
via the `CNAME` file at the repo root and the Pages settings UI.

## Desktop Notifications

The application can provide desktop notifications when swimming status changes
(open ↔ closed). Currently disabled in production (`registerServiceWorker()`
call is commented out in `index.html`).

### Architecture

- **Main page**: Renders current state and writes config (test mode flag, sync
  frequency) to `BeachStatusDB` IndexedDB.
- **Service worker (`sw.js`)**: On `sync` / `periodicsync` events, fetches
  `data/status.json` and `data/cso.json` and compares against the last value in
  IndexedDB. If status flipped, posts a notification.
- **Shared `BeachStatusDB`** records:
  - `current` — `{ id: 'current', status: 'open'|'closed', timestamp }`
  - `config` — `{ id: 'config', isTestMode, syncFrequencyMinutes, statusOverride? }`

Because the SW now reads same-origin static JSON, it no longer needs the PHP
proxy or any CORS workaround.

## CSO Incident Monitoring

CSO incidents are pre-fetched by the sync script for the last 2 weeks of
Winchester events and written to `data/cso.json`. The front-end filters down
to Mystic Lake-related incidents (`waterBodyDescription` contains "mystic")
and shows them in a warning-styled card. The card auto-hides when there are
no relevant incidents.

## Legacy Files

Until the GitHub Pages migration is fully verified, the following legacy files
remain in the repo but are excluded from the deploy artifact:

- `beachdata.php` — old PHP CORS proxy (no longer referenced by the page)
- `.htaccess` — old Apache CORS config (irrelevant on Pages)
- `database.js` — currently unused; retain in case SW notifications get
  re-enabled and want to share schema with the page

These can be deleted once `water.jalkut.com` is confirmed serving correctly.
