#!/usr/bin/env python3
"""Sweep the sandbox project — trash every recording listed in any
.test-live-ids-*.json sidecar file in the repo root.

Sidecar files are written by `tests/live/conftest.py`'s `id_store`
fixture. One file per test-run uuid; the cleanup deletes the file
after every entry trashes successfully.

Usage:
  python scripts/test_live_cleanup.py
  python scripts/test_live_cleanup.py --max-age-hours 1   # only sweep old runs

Requires the same env as the live tests:
  BASECAMP_TEST_PROJECT_ID
  BASECAMP_TEST_REFRESH_TOKEN
  BASECAMP_CLIENT_ID / BASECAMP_CLIENT_SECRET
  BASECAMP_ACCOUNT_ID
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

# Ensure the repo root is on sys.path so `auth` and `basecamp_client` are importable
# when the script is run directly (e.g. `python scripts/test_live_cleanup.py`).
sys.path.insert(0, str(Path(__file__).parent.parent))

import requests

from auth.live_sandbox_guard import assert_sandbox
from basecamp_client import BasecampClient


REPO_ROOT = Path(__file__).parent.parent


def _refresh() -> str:
    r = requests.post(
        'https://launchpad.37signals.com/authorization/token',
        params={
            'type': 'refresh',
            'refresh_token': os.environ['BASECAMP_TEST_REFRESH_TOKEN'],
            'client_id': os.environ['BASECAMP_CLIENT_ID'],
            'client_secret': os.environ['BASECAMP_CLIENT_SECRET'],
        },
        timeout=10,
    )
    r.raise_for_status()
    return r.json()['access_token']


def run_cleanup(client: BasecampClient, dir: Path, max_age_hours: float | None) -> tuple[int, int]:
    trashed = 0
    failed = 0
    cutoff = (time.time() - max_age_hours * 3600) if max_age_hours else None
    for path in sorted(dir.glob('.test-live-ids-*.json')):
        entries = json.loads(path.read_text())
        if cutoff is not None:
            entries = [e for e in entries if e.get('recorded_at_unix', 0) < cutoff]
            if not entries:
                continue
        all_ok = True
        for entry in entries:
            try:
                # 'Todo' is the only kind that uses delete_todo (BC's todo
                # endpoint is separate from the generic recording trash).
                # Everything else (Todolist, Column, CardTableCard, CardStep,
                # Message::Post, Comment, etc.) routes through trash_recording.
                if entry['kind'] == 'Todo':
                    client.delete_todo(entry['project_id'], entry['recording_id'])
                else:
                    client.trash_recording(entry['project_id'], entry['recording_id'])
                trashed += 1
            except Exception as e:
                print(f"  ! trash failed for {entry['kind']} {entry['recording_id']}: {e}", file=sys.stderr)
                failed += 1
                all_ok = False
        if all_ok and cutoff is None:
            path.unlink()
    return trashed, failed


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--max-age-hours', type=float, default=None,
                    help='only sweep sidecar entries older than N hours (default: all)')
    args = ap.parse_args()

    access = _refresh()
    client = BasecampClient(
        access_token=access,
        account_id=os.environ['BASECAMP_ACCOUNT_ID'],
        user_agent='Basecamp MCP cleanup',
        auth_mode='oauth',
    )
    assert_sandbox(client=client)  # guard the sweep itself

    trashed, failed = run_cleanup(client, REPO_ROOT, args.max_age_hours)
    print(f"Cleanup complete. Trashed: {trashed}. Failed: {failed}.")
    if failed > 0:
        sys.exit(2)


if __name__ == '__main__':
    main()
