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
        │              Tableau Cloud ─► data/samples.json (all beaches, keyed) +
        │              data/beaches.json (selector index + status) + data/status.json
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
`publicdashboardtoken.mass.gov`. **Before each read it must force a data-source
refresh** (`getDataSourcesAsync()` → `refreshAsync()`) — otherwise
`getSummaryDataAsync()` returns a day-stale cached extract (see "Data freshness"
below). `meta.json.samples.source` records which path ran (`browser` | `none`).

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

**Data freshness — a successful fetch is NOT automatically a fresh one.** Distinct
from the failure modes above (which announce themselves as errors), there is a
*silent* mode where the fetch succeeds — `samples.source` = `browser`,
`samples.status` = `ok`, `errors` empty — yet the committed readings are ~a day
stale. Cause: `getSummaryDataAsync()` reads whatever DPH's shared "Beaches
DataSource" has **cached**, and that extract lags the live data by roughly a day.
Loading the view fresh each run does not re-query it, and the `:refresh=yes`
view-URL parameter only busts the *render* cache (verified: it returns byte-identical
stale rows). The workbook's own toolbar **Download → Crosstab** implicitly forces a
live re-query, which is why a hand-downloaded CSV can contain readings the scrape
lacks. The fix (shipped 2026-07-08): `_embed_html` calls `getDataSourcesAsync()`
then `refreshAsync()` on each data source before `getSummaryDataAsync()`
(best-effort — it logs each refresh in `window.__r.refreshLog` and still reads on
failure). This runs on all three worksheet reads and adds seconds each, so
`BROWSER_FETCH_TIMEOUT` is 300s and `_read_worksheet` catches/retries `page.goto`
timeouts instead of aborting. **Symptom to watch for:** readings frozen at an old
date while syncs keep reporting `source: browser` with no errors ⇒ suspect the
data-source cache, not the fetch. (This is a *fourth* mode the export can be in,
beyond `403`, `init` flake, and the "unavailable" fallback.)

### Key Files

- **`index.html`** — Main dashboard. Vanilla JS, no frameworks. Loads static JSON
  from `data/`, renders current status, CSO incidents card, samples table, and a
  "Data last synced" line driven by `meta.json`.
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
- **`.github/workflows/probe-export.yml`** — Watchdog for a *sustained* export
  outage (see "Export reliability" above). Runs every 6h; reads the `samples.source`
  history from `data/meta.json` commits (not a live fetch — no Playwright) and opens
  a GitHub issue only when in-season syncs have failed continuously for ≥4h, then
  auto-closes it on recovery. Single transient flakes stay quiet. Does not deploy.
- **`CNAME`** — `water.jalkut.com`.

### Upstream Endpoints

Primary (in-season samples + status), consumed by `fetch_tableau_cloud.py` via the
Tableau JS Embedding API in a headless browser:

- Public access token: `https://publicdashboardtoken.mass.gov/tokens/requestpublicaccess?connectedapp=DPH-BCEH-BDD-BD`
  → `{ "token": "<JWT>" }`
- Workbook: `https://prod-useast-b.online.tableau.com/t/eohhspublic/views/BeachWaterQualityDashboard/<view>`
  — `TestResultsTable` worksheet → all-beach readings (Town, Name, Date, Indicator,
  GeoMean, Results); `Map` worksheet → per-beach status (Beach Name, Beach Status, …);
  `Closures` view's `ClosureTable` worksheet → stated closure reason per closed beach
  (Town, Beach, Closure Reason). All read with
  `getSummaryDataAsync({maxRows:0, ignoreSelection:true})` (the summary-data API only
  — underlying/full-column data is `403 PermissionDenied` for the public token, so a
  reason must be a field placed on a worksheet, which is why `ClosureTable` is used),
  each preceded by a `refreshAsync()` on the worksheet's data source so the summary
  reflects live data rather than the ~day-stale cached extract (see "Data freshness").

Legacy fallback only (frozen at 2025 season end), consumed by `sync_water_data.py`:

- Sample data: `https://datavisualization.dph.mass.gov/views/BeachesDashboard-CloudVersion-2025/Results.csv?refresh=y&Name=<beach>`
  — needs `Referer: https://datavisualization.dph.mass.gov`
- Beach status: `https://datavisualization.dph.mass.gov/views/BeachesDashboard-CloudVersion-2025/BeachList.csv?:refresh=y&Name=<beach>`
  — same referer

CSO incidents (year-round, stdlib):

- `https://eeaonline.eea.state.ma.us/dep/CSOAPI/api/Incident/GetIncidentsBySearchFields/?municipality=WINCHESTER&pageNumber=1&incidentFromDate=<DD/MM/YYYY>`
  — needs `Referer: https://eeaonline.eea.state.ma.us/portal/dep/cso-data-portal/`

### Static Data Shapes

- `data/samples.json` → all-beaches, keyed by beach name:
  `{ "headers": [...], "beaches": { "<beach name>": { "town", "rows": [[date, indicator, threshold, results], ...], "geoMean"?: { "date", "indicator", "threshold", "value" } } } }`
  The page filters to the selected beach client-side. `geoMean` (optional) is the
  most recent non-null cumulative geometric mean for the beach — it drives the
  "Geometric Mean Test Results" card and uses freshwater geomean thresholds
  (Enterococci 33, E. Coli 126). (The old single-beach `{ headers, rows }` shape is
  still read for back-compat.)
- `data/beaches.json` → `{ "beaches": [ { "name", "town", "status", "reason"? }, ... ] }`
  — the index that drives the Town/Beach selector and carries per-beach status.
  Only beaches with readings are listed: DPH's `Map` worksheet names some sites at
  the water-body level (e.g. `Lake Dennison State Park (DCR)`) while readings come
  in at the sampling-point level (`... @Day Use Beach`), so those status-only
  aggregate entries are excluded (`build_beach_index`) to keep the selector to
  beaches that actually have data.
  Off-season every `status` is `"Closed for Season"`; on an in-season fetch
  failure they are blanked (`""`) while the beach list is preserved. `reason`
  (optional) is the state's stated closure reason (e.g. `"Bacterial Exceedance"`,
  `"Harmful Cyanobacteria Bloom"`, `"CSO/SSO event"`), present only on
  currently-`Closed` beaches. It comes from the `Closures` dashboard's
  `ClosureTable` worksheet (Town, Beach, Closure Reason), joined onto the Map
  status by full beach name (`build_closure_reasons`), and drives the closure-reason
  subheading in the red "Closed for Swimming" status card.
- `data/status.json` → `{ "name", "status", "town", "reason"? }` — the **default
  beach only** (Shannon), kept for the currently-disabled service worker. The page
  reads per-beach status from `beaches.json`, not this file.
- `data/cso.json` → `{ "results": [...], "rowCount": N, "windowStart": "YYYY-MM-DD" }`
  — Front-end filters `results` to Mystic Lake by `waterBodyDescription` and only
  shows the CSO card when the selected beach is a Mystic beach (`/mystic/i`).
- `data/meta.json` → `{ "lastSynced", "season", "today", "beach", "samples", "status", "cso", "errors", "schemaVersion" }`

### Town/Beach Selector

The header carries cascading **Town → Beach** dropdowns (collapsed behind a
"Looking for another beach?" link) built from `beaches.json`. The selection
defaults to Winchester / Shannon Beach @ Upper Mystic and is persisted in a
`selectedBeach` cookie (1-year max-age) so returning visitors land on their
last-viewed beach. In-season the page loads the whole all-beaches `samples.json`
once and re-filters on selection (no refetch); off-season it loads
`archive/<beach key>/<year>.csv` per beach — archives are currently committed
only for Shannon, so other beaches show a "no archived readings" note
off-season. The "view historical readings" links appear only when the selected
beach actually has an archived season.

### Beach Permalinks & Slug Overrides

Each beach has a shareable path permalink `/beach/<slug>/`. Precedence on load is
**path slug → cookie → default**; selecting a beach rewrites the address bar via
`history.replaceState` and persists the choice. These are **real HTTP 200s**, not
an SPA 404 trick: the deploy (`sync.yml`) pre-renders a copy of the app at
`site/beach/<slug>/index.html` for every beach in `beaches.json`, and
`<base href="/">` in `index.html` keeps each copy's `data/`/`archive/` fetches
rooted. A beach that first appears between deploys has no page until the next sync
(accepted — it has no data yet).

Slugs are derived from the beach name (`lowercase`, non-alphanumeric → `-`), but
**`slug-overrides.json`** (`{ "<exact beach name>": "<custom slug>" }`, committed
at the repo root and staged to the site) lets the project publish a friendlier
permalink — e.g. `"Shannon Beach @ Upper Mystic (DCR)"` → `upper-mystic`. The
front-end fetches this table; the deploy reads it to name the directories. For an
overridden beach the **derived slug is also pre-rendered as an alias** and still
resolves (then normalizes to the override), so links shared before an override was
added keep working. Test override: `?slug-overrides-url=`.

### Season Logic

Memorial Day through Labor Day = "in-season". The Python sync script and the JS
both implement the same calendar math. Off-season, upstream stops publishing
samples, so the script writes a stub `status.json` (`"Closed for Season"`) and
the page reads from `archive/Shannon_Beach_Upper_Mystic_DCR/<year>.csv` for the
most recent completed season.

## Local Development

The site is fully static. Serve it with the dev server:

```bash
python3 serve.py            # default port 8000
```

Then open `http://localhost:8000/`. `serve.py` is a thin stdlib wrapper around
`http.server` that additionally answers `/beach/<slug>/` permalink URLs with
`index.html` — in production those pages are pre-rendered at deploy time and
don't exist in the repo, so under a plain `python3 -m http.server` they 404
(the plain server still works for everything else). The page reads from `data/*.json`
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
- `?data-url=test-data/results-typical.csv` — single-beach samples (bypasses the
  all-beaches selector model)
- `?status-url=test-data/status-open.csv`
- `?cso-url=test-data/cso-mystic-incident.json`
- `?beaches-url=test-data/beaches-multi.json` — the Town/Beach selector index
- `?samples-url=test-data/samples-multi.json` — the all-beaches keyed readings
- `?season=open|closed` — override the date-based season check

To exercise the multi-beach selector locally, combine the last two, e.g.
`?season=open&beaches-url=test-data/beaches-multi.json&samples-url=test-data/samples-multi.json`.

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
   (`index.html`, `sw.js`, `faq/`, `data/`, `archive/`,
   `test-data/`, `CNAME`) — `sync_water_data.py`, `beachdata.php`, `.htaccess`,
   `.claude/`, and shell scripts are not deployed
5. Uploads as a Pages artifact and deploys via `actions/deploy-pages@v4`

DNS: `water.jalkut.com` CNAME → `<github-pages-host>`. Custom domain configured
via the `CNAME` file at the repo root and the Pages settings UI.

### Sync scheduling (external trigger)

GitHub's `schedule:` cron is best-effort and frequently drops/delays runs (the
every-15-min cron in practice fired only every few hours), so data freshness is
driven by an **external trigger** instead. `trigger_sync.py` (stdlib Python 3)
fires the workflow via `workflow_dispatch` against the GitHub API, and runs on a
**cron on `Cielo.local`** (an always-on machine, not part of this repo) every 15
minutes. The token comes from `GITHUB_TOKEN`/`GH_TOKEN` in the environment (a PAT
with Actions: write), kept on that machine — not in the repo or GitHub Secrets
(Secrets are only readable inside Actions runs, not from an external client). The
`schedule:` block in `sync.yml` is retained as a best-effort fallback. Note: only
`schedule`/`workflow_dispatch` runs actually sync — `push` events skip the sync
and just redeploy the committed `data/`.

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
