# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is a beach water quality monitoring dashboard that displays data from Massachusetts Department of Public Health. The project consists of:

- **HTML dashboards** (`mystic.html`, `simple.html`) for displaying beach water quality data
- **PHP proxy** (`beachdata.php`) to handle CORS and fetch CSV data from Mass.gov APIs  
- **Apache configuration** (`.htaccess`) for CORS headers

## Architecture

### Data Flow
1. HTML pages make requests to `beachdata.php` for both sample data and beach status
2. PHP proxy fetches CSV data from two Mass.gov endpoints:
   - Sample data: `https://datavisualization.dph.mass.gov/views/BeachesDashboard-CloudVersion-2025/Results.csv`
   - Beach status: `https://datavisualization.dph.mass.gov/views/BeachesDashboard-CloudVersion-2025/BeachList.csv`
3. Data is parsed client-side using custom CSV parser and displayed in:
   - Current Status section showing open/closed beach status
   - Recent Samples table with sortable data and threshold indicators
4. Both data sources auto-refresh every 5 minutes

### Key Components
- **mystic.html**: Main dashboard for Shannon Beach @ Upper Mystic (DCR)
- **beachdata.php**: CORS proxy that accepts `?b=` (beach name) and `?u=` (base URL) parameters
- **simple.html**: Test page for CORS functionality
- **sw.js**: Service worker providing background notifications when swimming status changes
- Custom CSV parser in JavaScript handles quoted fields and skips redundant date columns

## Data Sources

Active API endpoints:
- **Sample data**: `https://datavisualization.dph.mass.gov/views/BeachesDashboard-CloudVersion-2025/Results.csv`
- **Beach status**: `https://datavisualization.dph.mass.gov/views/BeachesDashboard-CloudVersion-2025/BeachList.csv`

The TODO file mentions additional endpoints:
- Closure data: `BeachesDashboardMockup_test/ClosureTable.csv`
- Geometric mean data: `BeachesDashboardMockup_test/Geomean.csv`

## Development Notes

### Server Requirements
- PHP with cURL support
- Apache with mod_headers for CORS configuration
- Web server accessible at `jalkut.com/water/` domain

### CORS Configuration
- `.htaccess` allows requests from `https://jalkut.com`
- `beachdata.php` sets wildcard CORS headers for broader access
- Both approaches are used for different access patterns

### JavaScript Architecture
- Vanilla JavaScript, no frameworks
- Custom CSV parsing logic handles Massachusetts DPH CSV format
- Parallel data fetching for both sample data and beach status
- Two-section UI:
  - **Current Status**: Shows open/closed beach status with location info
  - **Recent Test Results**: Sortable table with threshold indicators
- Table sorting by date (newest first)
- Early fetch initiation to minimize loading time
- Test mode support with sample data for both endpoints

## Local Development

### Running a Local Web Server
To test the application locally, you need to run a web server that supports PHP:

**PHP Built-in Server** (recommended for development):
```bash
php -S localhost:8000
```

**Python HTTP Server** (for static content only):
```bash
# Python 3
python -m http.server 8000

# Python 2
python -m SimpleHTTPServer 8000
```

**Node.js/npm servers**:
```bash
# Using http-server
npx http-server -p 8000

# Using live-server
npx live-server --port=8000
```

**Apache/Nginx**: Configure virtual host pointing to project directory

After starting the server, access the dashboard at:
- Main dashboard: `http://localhost:8000/mystic.html`
- Test page: `http://localhost:8000/simple.html`

**Note**: The PHP built-in server is recommended since the application uses `beachdata.php` for CORS proxying. Static servers won't execute PHP code.

## Desktop Notifications

The application provides desktop notifications when swimming status changes from open to closed (or vice versa).

### Notification Features
- **Background operation**: Works even when browser tab is closed (but Safari must remain open)
- **Service worker powered**: Uses `sw.js` for reliable background monitoring
- **User permission required**: Shows banner prompting user to enable notifications
- **Status change detection**: Only notifies when status actually changes between open/closed
- **Persistent notifications**: Require user interaction to dismiss

### Notification Architecture
- **Main page**: Displays status and stores configuration in IndexedDB
- **Service worker**: Monitors status changes and sends notifications in background
- **Shared database**: `BeachStatusDB` IndexedDB stores status history and configuration
- **Configurable frequency**: Sync frequency stored in database (5 minutes default, 30 seconds in test mode)

### IndexedDB Storage
The application stores data in `BeachStatusDB` with these records:
- **`current`**: Latest swimming status (open/closed) 
- **`config`**: Configuration including test mode, test data, and sync frequency

### Test Mode Configuration
When using `?test=1` URL parameter:
- Uses local test data instead of live API
- Sets sync frequency to 30 seconds for faster testing
- Test data and settings stored in IndexedDB for service worker access
- Manual test data editing supported (won't be overwritten)

### Notification Testing
1. Visit `?test=1` to enable test mode with fast 30-second sync
2. Enable notifications via banner button
3. Close tab but keep Safari running
4. Edit test data in IndexedDB or modify `testStatusData` in code
5. Notifications appear when status changes are detected