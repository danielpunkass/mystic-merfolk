# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is a beach water quality monitoring dashboard that displays data from Massachusetts Department of Public Health. The project consists of:

- **HTML dashboards** (`mystic.html`, `simple.html`) for displaying beach water quality data
- **PHP proxy** (`beachdata.php`) to handle CORS and fetch CSV data from Mass.gov APIs  
- **Apache configuration** (`.htaccess`) for CORS headers

## Architecture

### Data Flow
1. HTML pages make requests to `beachdata.php` 
2. PHP proxy fetches CSV data from `https://datavisualization.dph.mass.gov/views/BeachesDashboard-CloudVersion-2025/Results.csv`
3. Data is parsed client-side using custom CSV parser and displayed in sortable tables
4. Auto-refresh occurs every 5 minutes

### Key Components
- **mystic.html**: Main dashboard for Shannon Beach @ Upper Mystic (DCR)
- **beachdata.php**: CORS proxy that accepts `?b=` (beach name) and `?u=` (base URL) parameters
- **simple.html**: Test page for CORS functionality
- Custom CSV parser in JavaScript handles quoted fields and skips redundant date columns

## Data Sources

Primary API endpoint: `https://datavisualization.dph.mass.gov/views/BeachesDashboard-CloudVersion-2025/Results.csv`

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
- Table sorting by date (newest first)
- Early fetch initiation to minimize loading time