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
GitHub Action (hourly cron)
  └─► sync_water_data.py
        ├─► fetch Results.csv  (Mass DPH)  ─► data/samples.json
        ├─► fetch BeachList.csv (Mass DPH) ─► data/status.json
        ├─► fetch CSO incidents (Mass DEP) ─► data/cso.json
        ├─► merge into archive/<beach>/<year>.csv
        └─► write data/meta.json (lastSynced, season, ...)
  └─► commit refreshed data + archive
  └─► stage site/ and deploy to GitHub Pages
```

At page-load time, `mystic.html` fetches `data/samples.json`, `data/status.json`,
`data/cso.json`, and `data/meta.json` directly. Off-season it reads
`archive/Shannon_Beach_Upper_Mystic_DCR/<year>.csv` instead of `data/samples.json`.

### Key Files

- **`mystic.html`** — Main dashboard. Vanilla JS, no frameworks. Loads static JSON
  from `data/`, renders current status, CSO incidents card, samples table, and a
  "Data last synced" line driven by `meta.json`.
- **`simple.html`** — Tiny page kept for diagnostics.
- **`sw.js`** — Service worker (currently registration is disabled in `mystic.html`).
  Reads the same `data/*.json` and posts a desktop notification on status change.
- **`sync_water_data.py`** — Python 3 stdlib script that does all upstream fetching.
  Stdlib only (urllib + csv + json) — no external deps, no requirements.txt.
- **`.github/workflows/sync.yml`** — Hourly cron that runs the sync script, commits
  data changes, stages `site/`, and deploys via `actions/deploy-pages`.
- **`CNAME`** — `water.jalkut.com`.

### Upstream Endpoints (consumed by sync script only)

- Sample data: `https://datavisualization.dph.mass.gov/views/BeachesDashboard-CloudVersion-2025/Results.csv?refresh=y&Name=<beach>`
  — needs `Referer: https://datavisualization.dph.mass.gov`
- Beach status: `https://datavisualization.dph.mass.gov/views/BeachesDashboard-CloudVersion-2025/BeachList.csv?:refresh=y&Name=<beach>`
  — same referer
- CSO incidents: `https://eeaonline.eea.state.ma.us/dep/CSOAPI/api/Incident/GetIncidentsBySearchFields/?municipality=WINCHESTER&pageNumber=1&incidentFromDate=<DD/MM/YYYY>`
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

Then open `http://localhost:8000/mystic.html`. The page reads from `data/*.json`
and `archive/...csv` — both committed to the repo — so it works fully offline
once the repo is cloned.

To regenerate the static data locally:

```bash
python3 sync_water_data.py
```

This hits the live Mass DPH / Mass DEP endpoints and rewrites `data/*.json` plus
appends to `archive/<beach>/<year>.csv`. No external deps required.

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

1. Runs hourly on cron (`17 * * * *`)
2. Executes `sync_water_data.py`
3. Commits any changed `data/` or `archive/` files back to `main`
   (uses `[skip ci]` so the resulting push doesn't loop into another deploy)
4. Stages a `site/` directory containing only the public files
   (`mystic.html`, `simple.html`, `index.html`, `sw.js`, `data/`, `archive/`,
   `test-data/`, `CNAME`) — `sync_water_data.py`, `beachdata.php`, `.htaccess`,
   `.claude/`, and shell scripts are not deployed
5. Uploads as a Pages artifact and deploys via `actions/deploy-pages@v4`

DNS: `water.jalkut.com` CNAME → `<github-pages-host>`. Custom domain configured
via the `CNAME` file at the repo root and the Pages settings UI.

## Desktop Notifications

The application can provide desktop notifications when swimming status changes
(open ↔ closed). Currently disabled in production (`registerServiceWorker()`
call is commented out in `mystic.html`).

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
