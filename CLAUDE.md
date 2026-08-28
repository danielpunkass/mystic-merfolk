# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is a beach water quality monitoring dashboard for Shannon Beach @ Upper Mystic
(DCR), displaying data from the Massachusetts Department of Public Health.

The site is **fully static** and served from GitHub Pages at `water.mysticmerfolk.org`. A
scheduled GitHub Action runs `sync_water_data.py` periodically to fetch upstream
data and commit refreshed `data/*.json` and `archive/<beach>/<year>.csv` files
back to the repo. The page loads only same-origin static files at runtime — no
PHP proxy, no CORS workaround, no live API call from the browser.

## Architecture

### Data Flow

```
GitHub Action (dispatched every 15 min from Cielo.local)
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
- **`.github/workflows/sync.yml`** — Installs Playwright + Chromium, runs the sync
  script, commits data changes, stages `site/`, deploys via `actions/deploy-pages`.
  `fetch_tableau_cloud.py` is NOT deployed to the site. It has **no `schedule:`
  cron** — it is triggered externally (see "Sync scheduling" below).
- **`.github/workflows/probe-export.yml`** — Watchdog, running every 2h. Reads the
  `lastSynced` / `samples.source` history from `data/meta.json` commits (not a live
  fetch — no Playwright) and opens a GitHub issue for either of two failures, each
  auto-closing on recovery:
  - **syncs stopped entirely** — no new `meta.json` commit in ≥4h, i.e. the
    external dispatcher is down *or* the deploy queue is wedged (below). This is
    the backstop for sync.yml having no cron of its own; without it a dead
    dispatcher is silent, since nothing errors and the page just keeps serving
    the last good data.
  - **sustained export outage** — in-season syncs running but failing continuously
    for ≥4h (see "Export reliability" above). Single transient flakes stay quiet.

  It also **self-heals a wedged deploy queue** before evaluating staleness (see
  "Wedged runs" below), and names that cause in the stale-sync issue when it fires
  — otherwise the issue text sends you to Cielo.local, which will look healthy.
  Needs `actions: write` for the cancel. Does not deploy.
- **`CNAME`** — `water.mysticmerfolk.org`.

### Upstream Endpoints

Primary (in-season samples + status), consumed by `fetch_tableau_cloud.py` via the
Tableau JS Embedding API in a headless browser:

- Public access token: `https://publicdashboardtoken.mass.gov/tokens/requestpublicaccess?connectedapp=DPH-BCEH-BDD-BD`
  → `{ "token": "<JWT>" }`
- Workbook: `https://prod-useast-b.online.tableau.com/t/eohhspublic/views/BeachWaterQualityDashboard/<view>`
  — `TestResultsTable` worksheet → all-beach readings (Town, Name, Date, Indicator,
  GeoMean, Results); `Map` worksheet → per-beach status and water type (Beach Name,
  Beach Status, Marine or Freshwater, …), the latter selecting each beach's
  threshold standard;
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
  "Geometric Mean Test Results" card. Sample-row and geomean thresholds are
  stamped per beach by water type (the Map worksheet's "Marine or Freshwater"
  field; 105 CMR 445): freshwater Enterococci 61/33, E. Coli 235/126; marine
  Enterococci 104/35. A beach with no Map match falls back to freshwater values.
  (The old single-beach `{ headers, rows }` shape is still read for back-compat.)
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
  plus `commit` / `commitDate` (short SHA + ISO-8601 committer date), which exist
  **only in the deployed copy** — `sync.yml` patches them into `site/data/meta.json`
  at stage time, since a commit can't contain its own SHA. The page renders them
  as the footer's "Commit:" line, with the date in the visitor's locale.

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

### Localization

The site ships in **thirteen locales**: English, Spanish (Spain), Spanish
(Latin America / `es-419`), Spanish (US), Portuguese (Brazil), Russian, Arabic,
French, Italian, Chinese (Simplified), Haitian Creole, Vietnamese, and Khmer.

The set tracks **Massachusetts demographics**, not "major European languages" —
Spanish, Portuguese, Chinese, and Haitian Creole are the state's four most-spoken
non-English languages, Vietnamese is fifth, and Russian and Arabic are sixth and
seventh. Khmer is there despite a smaller count because Massachusetts holds ~10%
of all US Khmer speakers (Lowell). The
three Spanish variants and Brazilian Portuguese exist because those populations
are well represented here and a single "es" would serve peninsular wording to
readers who don't use it. German was dropped for the same reason it was never
justified: it isn't in the state's top ten.

Because everything is static, localization is entirely client-side: each page
carries its own string catalog and applies it before first paint. There is no
build step, no per-language URL, and nothing for `sync_water_data.py` or the
deploy to do — the pre-rendered `/beach/<slug>/` pages are byte-copies of
`index.html` and inherit the whole mechanism for free.

**Language resolution** (`resolveLang()` → `normalizeLang()`, same on both
pages): `?lang=xx` → the `lang` cookie (1-year, shared across the dashboard and
the FAQ) → `navigator.languages` → English. `normalizeLang()` maps any BCP-47
tag onto a catalog, in this order:

1. **Exact regional match** — `es-US` → `es-US`, `pt-BR` → `pt-BR`.
2. **`ES_419_REGIONS`** — any Latin American Spanish region (`es-MX`, `es-CO`,
   `es-AR`, …) → `es-419`.
3. **Base language** — `de-AT` → `de`, `es-ES` → `es`.
4. **`BASE_FALLBACK`** — a base with no catalog of its own; `pt` / `pt-PT` →
   `pt-BR`, which is far closer than dropping to English.

Anything unmatched (`ja`) falls back to English rather than half-translating.

**Two separate notions of "language."** `langPreference` is what the visitor
chose — a concrete code, or the sentinel `SYSTEM_LANG` (`"system"`) meaning
"follow the browser." `currentLang` is the code actually in use. Everything that
renders reads `currentLang`; the cookie and the `<select>` carry
`langPreference`. Collapsing the two would make "Match System" unrepresentable:
a visitor on a French browser being shown French gives no way to tell whether
they *chose* French or are following the system, so they could never get back to
following it. **`SYSTEM_LANG` is the default** — with no `?lang=` and no cookie,
`resolvePreference()` returns it, so the page tracks the browser until something
is pinned explicitly.

The switcher's first entry is that option, labelled with the language it
currently resolves to — `Match System (Français)` — followed by an `<hr>`
divider and then the fixed list. The label names the **system** language, not the
active one, so it says what picking it would do: on a French browser with
Vietnamese pinned it still reads `Dùng ngôn ngữ hệ thống (Français)`. Variant
labels use a middle dot (`Español · España`) rather than parentheses, so nesting
them inside that string doesn't produce `Match System (Español (España))`.

The `<select>` lives at the **foot of the page**, as quiet chrome. The page now
ends with, in order: the language `<select>`, the translation-feedback link, then
the sync metadata (`#last-updated`, which was moved out of `#results-card` so the
whole block reads as one footer) —
language is a set-once preference that persists in a cookie, not something
visitors toggle, so it doesn't earn header space above the status card.
Switching re-renders in place — no reload, so the visitor keeps their beach,
scroll position, and any revealed historical card — writes the cookie, and
rewrites `?lang=` **only if the URL already had one** (otherwise a reload of a
shared link would snap back to the sender's language, and clean URLs stay
clean).

**Where the strings live:**

- `index.html` — `STRINGS` (79 keys per complete catalog) plus `t(key, params)`
  for `{placeholder}` interpolation and `tData(namespace, value)` for *upstream*
  vocabulary. Static markup carries `data-i18n` (textContent), `data-i18n-html`
  (innerHTML), and `data-i18n-attr="aria-label:some.key"` hooks that
  `applyStaticTranslations()` fills in; everything rendered from JS calls `t()`.
- `faq/index.html` — the same pattern with its own 45-key catalog (the prose is
  page-specific, so the two catalogs are deliberately separate).
- `sw.js` — `NOTIFICATION_STRINGS` for the status-change notification. A service
  worker can't read `document.cookie`, so the page writes the active language
  into the shared `BeachStatusDB` `config` record (`lang`) and the SW reads it
  back. (Moot while SW registration stays commented out, but kept in step.)

**Regional variants inherit.** `LANG_PARENT` chains `es-US` → `es-419` → `es`
→ `en`, and `lookup()` walks it. A variant catalog therefore holds **only what
actually differs** — `es-419` overrides 18 keys on the dashboard and 13 in the
FAQ ("monitoreo" for "control", "agregar" for "añadir", "descarga" for
"vertido", `"…"` for `«…»`); `es-US` adds 4 and 1 more on top ("Ciudad" for the
town selector, and the Memorial Day / Labor Day glosses dropped, since a US
audience doesn't need to be told when those fall). Keep it that way: a variant
key whose value equals its parent's is dead weight, and both catalogs are
checked for exactly that. `en` and every base language stay complete.

**Translating DPH's own vocabulary.** Beach and town names are proper nouns and
stay as-is, but four upstream vocabularies are mapped through `tData()`, which
falls back to the **raw upstream value** when a term isn't in the catalog — DPH
can introduce wording we haven't seen, and showing it untranslated beats showing
a key:

- `table.*` — column headers (`Date and Time`, `Indicator`, `Threshold`, `Results`)
- `indicator.*` — `Enterococci` → `Enterokokken` / `Entérocoques` / …
  (`E. Coli` normalizes to `E. coli` everywhere)
- `reason.*` — closure reasons (`Bacterial Exceedance`,
  `Harmful Cyanobacteria Bloom`, `CSO/SSO event`, `Other`)
- statuses (`Open` / `Closed`) render through `status.open` / `status.closed`

**Haitian Creole and Khmer have no CLDR data, and the failure is silent.**
`Intl.DateTimeFormat("ht")` does not throw — it resolves to the runtime's default
locale (Chrome picked `es-ES` on this machine), so those pages would quietly show
**Spanish** month names. Both are therefore formatted by hand from
`MANUAL_DATE_LOCALES` (month names plus a `clock24` flag; Kreyòl uses 12-hour,
Khmer 24-hour). Their `locale` fields are fallbacks for the number formatting Intl
still does: `fr-HT` for Haitian Creole (French is co-official there) and `en-US`
for Khmer (Cambodia uses the same `,` grouping and `.` decimal).

**Before adding a language, check
`Intl.DateTimeFormat.supportedLocalesOf(["xx"])` in a browser** — Node's ICU and
Chrome's disagree (Node has `km`, Chrome doesn't), and the browser is what ships.
An empty result means the language needs a `MANUAL_DATE_LOCALES` entry. **All**
date rendering goes through `formatDateTime(value, time)` so the special case
lives in exactly one place — never call `toLocaleString` directly.

**Sample dates** arrive as US-format strings (`8/5/2026 8:00:00 AM`).
`formatSampleDate()` re-renders them in the active locale so `8/5` isn't misread
as 5 August — but returns English **verbatim**, so the en display stays
byte-identical to upstream. It uses `dateStyle: "medium"` (`4 ago 2026, 8:35`)
rather than the default numeric form, because numeric only moves the ambiguity
around: **CLDR's `es-US` is day-first** (`4/8/2026`), so a US Spanish reader
would hit precisely the misreading this exists to prevent. A month name can't be
misread in any locale. Only the *display* is localized — `row` itself stays raw,
so date sorting and the threshold comparison are untouched.

**Gotcha — the status card's pre-data placeholders.** `#status-heading` and
`#status-description` ship with "Current Status: Loading…" copy that
`applyStaticTranslations()` is allowed to localize *only while*
`data-i18n-init` is still set. The first real render calls `clearInitFlags()` to
take ownership of those nodes, so a later language switch can't clobber live
status with the loading placeholder. Any new branch that writes those two
elements must call `clearInitFlags()` first.

**Right-to-left.** Arabic carries `rtl: true` in `LANGS`; `isRTL()` reads it and
`applyStaticTranslations()` sets `documentElement.dir` alongside `lang`. Three
things make that nearly free here:

1. **The CSS is logical, not physical.** Every direction-sensitive declaration
   (`text-align`, `padding-left/right`, `margin-left`, `border-left/right`, the
   refresh indicator's `right`) uses `start`/`end`/`inline` forms, so RTL mirrors
   with no per-direction overrides. Verified: the English render is
   byte-identical before and after the conversion. **Keep it that way** — a new
   `padding-left` is a silent RTL bug.
2. **`t()` isolates its parameters.** Interpolated values are proper nouns, dates
   and counts — Latin-script runs inside an RTL sentence. `bidiIsolate()` wraps
   them in `U+2068`/`U+2069` (FSI…PDI) so the bidi algorithm can't drag adjacent
   punctuation to the wrong end. It's a no-op in LTR, so English stays
   byte-identical. `updatePageHeading()` isolates the beach name too, since it
   builds its string outside `t()`.
3. **Table cells get `unicode-bidi: isolate` under `[dir="rtl"]`.** Sample rows
   mix Arabic headers with Latin values (`E. coli`, `2,419.60`, DPH dates), and
   each cell needs its own bidi context.

Arabic's locale is plain `ar` deliberately: CLDR's `ar` uses Western digits,
while `ar-EG`/`ar-SA` use Arabic-Indic ones — and the Results column carries raw
Latin numerals from DPH, so those would render one table in two numbering
systems. The `→` in `link.viewHistorical` is a per-language string, so Arabic
simply uses `←`.

**Translation feedback.** Below the switcher, `#translation-report` links to a
prefilled GitHub issue (`Translation problem: <label> (<code>)` — the reporter is
the one person who knows which catalog is wrong and the least likely to think to
say). It is shown only when `currentLang !== DEFAULT_LANG`: English is the source
text, so there is nothing to report it against.

**Deliberately not translated:** the test-mode debug panel (developer tool),
`mystic.html` (a zero-delay meta-refresh redirect stub nobody reads), and beach
/ town names.

**Not yet covered:** Cape Verdean Creole is the main remaining gap — it matters
more than its census count suggests (Brockton, New Bedford, both coastal), since
the census usually folds it into "Portuguese". After that the tail drops off
fast: Korean, Greek, Polish, Hindi and Gujarati are each well under 1% of the
state's limited-English-proficient population.

Adding a language is now: a catalog, a `LANGS` entry, an `sw.js` entry, and —
if `Intl.DateTimeFormat.supportedLocalesOf()` comes back empty **in a browser** —
a `MANUAL_DATE_LOCALES` table. An RTL language additionally needs `rtl: true`
and nothing else.

**Translation provenance.** The Romance-language, Russian and Chinese catalogs
are model-written and reviewed against the English source. **Arabic, Haitian
Creole, Vietnamese, and Khmer have had no native-speaker review** — Khmer least
confident. Worth arranging before leaning on them for public-health messaging;
the in-page feedback link exists partly for this.

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
- `?lang=en|es|es-419|es-US|pt-BR|ru|ar|fr|it|zh|ht|vi|km` — force a locale (outranks the
  cookie and the browser). Region tags resolve through `normalizeLang()`, so
  `?lang=es-MX` also works and lands on `es-419`, and every `zh-*` lands on `zh`.
  `?lang=system` selects "Match System" (the default when nothing is pinned).

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

1. Is dispatched every 15 min from outside GitHub (see "Sync scheduling" below)
2. Executes `sync_water_data.py`
3. Commits any changed `data/` or `archive/` files back to `main`
   (uses `[skip ci]` so the resulting push doesn't loop into another deploy).
   Concurrent runs are handled by re-applying the freshly generated files on top
   of `origin/main` and retrying — see "Concurrent runs" below
4. Stages a `site/` directory containing only the public files
   (`index.html`, `sw.js`, `faq/`, `data/`, `archive/`,
   `test-data/`, `CNAME`) — `sync_water_data.py`, `beachdata.php`, `.htaccess`,
   `.claude/`, and shell scripts are not deployed
5. Uploads as a Pages artifact and deploys via `actions/deploy-pages@v4`

DNS: `water.mysticmerfolk.org` CNAME → `<github-pages-host>`. Custom domain configured
via the `CNAME` file at the repo root and the Pages settings UI.

### Sync scheduling (external trigger)

GitHub's `schedule:` cron is best-effort and frequently drops/delays runs (the
every-15-min cron in practice fired only every few hours), so data freshness is
driven by an **external trigger** instead. `trigger_sync.py` (stdlib Python 3)
fires the workflow via `workflow_dispatch` against the GitHub API, and runs on a
**cron on `Cielo.local`** (an always-on machine, not part of this repo) every 15
minutes. The token comes from `GITHUB_TOKEN`/`GH_TOKEN` in the environment (a PAT
with Actions: write), kept on that machine — not in the repo or GitHub Secrets
(Secrets are only readable inside Actions runs, not from an external client).

`sync.yml` has **no `schedule:` block at all** — it was dropped once the external
dispatcher proved reliable. Keeping both meant ~2× the runs, and the two triggers
firing seconds apart (e.g. `:59:43` schedule + `:00:01` dispatch) were the main
source of the concurrent-run push failures described below. **Cielo.local is
therefore the sole trigger, and a single point of failure** — that is what
`probe-export.yml`'s stale-sync check exists to catch: if no `meta.json` commit
lands for ≥4h it opens an issue pointing at the machine, its cron, and its PAT.
A manual **Actions → Run workflow** is the stopgap.

Note: only `workflow_dispatch` (and, if one is ever re-added, `schedule`) runs
actually sync — `push` events skip the sync and just redeploy the committed
`data/`.

### Concurrent runs

Two sync runs can still overlap (a manual dispatch during a scheduled one, a
retry, a slow Playwright fetch). Two things keep that from failing the job:

- **`actions/checkout` uses `ref: main`, not the default `github.sha`.**
  `github.sha` is pinned when a run is *queued*, and the `pages-sync` concurrency
  group routinely holds a run pending for a minute or more while the run ahead of
  it pushes — so the default would check out an already-stale base and guarantee a
  non-fast-forward push. This was the actual cause of the "sporadic merge issue"
  failures.
- **The commit step never rebases.** `data/` is a wholesale-regenerated snapshot
  and every run rewrites `meta.json`'s `lastSynced` line, so a rebase is a
  *guaranteed* conflict, and a conflict is fatal under the step's `bash -e`. On a
  rejected push it instead saves the generated `data/` + `archive/`, resets hard
  to `origin/main`, restores them, re-commits, and retries (5×, then fails
  loudly). Ours-wins is safe: `archive/` is a union merge of the full upstream
  season, so a row only the other run saw is re-added by the next sync.

### Wedged runs

A distinct failure of the same concurrency group, seen 2026-08-06 → 08-08: a sync
run parked in GitHub's **`waiting`** state on the `github-pages` environment for a
day and a half. Its `pending_deployments` entry had `wait_timer: 0` and an **empty
`reviewers` array** — the environment's only protection rule is a branch policy,
so there was no gate to satisfy and no approval that could ever release it. With
`cancel-in-progress: false`, that zombie held `pages-sync` indefinitely: every
15-min dispatch queued behind it and was killed by the *next* dispatch, giving a
wall of runs all `cancelled` at exactly ~15m with

> Canceling since a higher priority waiting request for pages-sync exists

**The symptom points the wrong way.** No job ever starts, so there are no logs;
nothing fails, so nothing is reported except by the staleness watchdog, whose
message blames the external dispatcher. Cielo.local was dispatching perfectly
throughout. Diagnose it with

```bash
gh run list --limit 200 --json databaseId,status,workflowName \
  --jq '.[] | select(.status != "completed")'
```

and look for a `waiting`/`pending` run hours or days old. `gh run cancel <id>`
drains the entire queue within seconds.

`probe-export.yml` now does this automatically every 2h, but only for runs that
are **provably unreleasable** — `waiting` for >30 min with no reviewers and no
running wait timer. A genuine approval gate is left alone, so adding a required
reviewer to `github-pages` later won't have its deploys cancelled out from under
it.

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

These can be deleted once `water.mysticmerfolk.org` is confirmed serving correctly.
