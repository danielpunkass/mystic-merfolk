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