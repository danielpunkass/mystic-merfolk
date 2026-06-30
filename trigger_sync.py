#!/usr/bin/env python3
"""
Trigger the water-data sync GitHub Action on demand (workflow_dispatch).

GitHub's `schedule` cron is best-effort and frequently drops/delays runs, so the
reliable way to keep data fresh is to fire the workflow from an always-on machine
you control. This script does exactly that with stdlib only (no `gh`, no deps).

Auth:
    Set GITHUB_TOKEN (or GH_TOKEN) to a token with Actions: write on the repo:
      - Fine-grained PAT: scope it to danielpunkass/mystic-merfolk,
        Repository permissions -> Actions: Read and write.
      - or a classic PAT with the `workflow` scope.

Usage:
    GITHUB_TOKEN=ghp_xxx ./trigger_sync.py
    GITHUB_TOKEN=ghp_xxx ./trigger_sync.py --ref main

A successful dispatch returns HTTP 204 (no body). Schedule it every ~15 min via
cron or launchd (see the comments at the bottom of this file).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

OWNER = os.environ.get("WATER_SYNC_OWNER", "danielpunkass")
REPO = os.environ.get("WATER_SYNC_REPO", "mystic-merfolk")
WORKFLOW = os.environ.get("WATER_SYNC_WORKFLOW", "sync.yml")  # workflow file name or id


def main() -> int:
    parser = argparse.ArgumentParser(description="Trigger the water-data sync workflow.")
    parser.add_argument("--ref", default="main", help="git ref to run the workflow on (default: main)")
    args = parser.parse_args()

    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if not token:
        print("error: set GITHUB_TOKEN (a PAT with Actions: write on the repo)", file=sys.stderr)
        return 2

    url = f"https://api.github.com/repos/{OWNER}/{REPO}/actions/workflows/{WORKFLOW}/dispatches"
    req = urllib.request.Request(
        url,
        data=json.dumps({"ref": args.ref}).encode(),
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "water-sync-trigger",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            print(f"ok: dispatched {WORKFLOW} on {args.ref} ({OWNER}/{REPO}) -> HTTP {resp.status}")
            return 0
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace").strip()
        print(f"error: HTTP {e.code} dispatching workflow: {detail[:400]}", file=sys.stderr)
        return 1
    except urllib.error.URLError as e:
        print(f"error: network failure: {e.reason}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

# --- Scheduling -------------------------------------------------------------
#
# cron (Linux or macOS) — every 15 minutes, token kept out of the crontab:
#
#   1. echo 'export GITHUB_TOKEN=ghp_xxx' > ~/.water-sync.env && chmod 600 ~/.water-sync.env
#   2. crontab -e, add:
#        */15 * * * * . $HOME/.water-sync.env; /usr/bin/python3 /path/to/trigger_sync.py >> $HOME/.water-sync.log 2>&1
#
# launchd (macOS, survives logout on an always-on Mac) — ~/Library/LaunchAgents/com.mysticmerfolks.watersync.plist:
#
#   <?xml version="1.0" encoding="UTF-8"?>
#   <!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
#   <plist version="1.0"><dict>
#     <key>Label</key><string>com.mysticmerfolks.watersync</string>
#     <key>ProgramArguments</key>
#       <array><string>/usr/bin/python3</string><string>/path/to/trigger_sync.py</string></array>
#     <key>EnvironmentVariables</key><dict><key>GITHUB_TOKEN</key><string>ghp_xxx</string></dict>
#     <key>StartInterval</key><integer>900</integer>   <!-- 900s = 15 min -->
#     <key>StandardOutPath</key><string>/tmp/water-sync.log</string>
#     <key>StandardErrorPath</key><string>/tmp/water-sync.err</string>
#   </dict></plist>
#
#   load with:  launchctl load ~/Library/LaunchAgents/com.mysticmerfolks.watersync.plist
#
# The workflow serializes overlapping runs (concurrency: pages-sync), so it's safe
# if a run is still finishing when the next trigger fires. You can keep GitHub's
# own schedule as a best-effort fallback or remove it from sync.yml.
