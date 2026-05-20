"""Live-test bootstrap. Imported by every test under tests/live/*.

Provides three fixtures:
  - sandbox_project_id: str — the validated project id (from assert_sandbox).
  - live_client: BasecampClient bound to the sandbox project, authed via
    BASECAMP_TEST_REFRESH_TOKEN (one-off refresh against the dev OAuth app
    on first use; thereafter the access_token rides in-process).
  - id_store: callable to record (recording_id, project_id) tuples; the
    cleanup script reads the resulting JSON sidecar after the run.

Layer 1+2 sandbox guard runs in a module-level fixture so a failure aborts
the suite BEFORE any test starts hitting BC. The default suite (`pytest`
without `-m live`) never imports this conftest — pytest only loads conftest.py
files in the dir tree it's collecting from, and `tests/live/` is excluded
by default per pytest.ini (Task 5.2).
"""
from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path

import pytest
import requests

from auth.live_sandbox_guard import assert_sandbox
from basecamp_client import BasecampClient


REPO_ROOT = Path(__file__).parent.parent.parent


def _refresh_for_live() -> str:
    """Exchange BASECAMP_TEST_REFRESH_TOKEN for an access_token via the dev
    OAuth app. Used at the top of each suite run — we don't persist the
    access_token; the test refresh token is the credential of record."""
    rt = os.environ['BASECAMP_TEST_REFRESH_TOKEN']
    client_id = os.environ['BASECAMP_CLIENT_ID']
    client_secret = os.environ['BASECAMP_CLIENT_SECRET']
    r = requests.post(
        'https://launchpad.37signals.com/authorization/token',
        params={
            'type': 'refresh',
            'refresh_token': rt,
            'client_id': client_id,
            'client_secret': client_secret,
        },
        timeout=10,
    )
    r.raise_for_status()
    return r.json()['access_token']


@pytest.fixture(scope='session')
def live_client() -> BasecampClient:
    access_token = _refresh_for_live()
    account_id = os.environ['BASECAMP_ACCOUNT_ID']
    return BasecampClient(
        access_token=access_token,
        account_id=account_id,
        user_agent='Basecamp MCP Server live tests (mcp@basecamp-server.dev)',
        auth_mode='oauth',
    )


@pytest.fixture(scope='session')
def sandbox_project_id(live_client) -> str:
    return assert_sandbox(client=live_client)


@pytest.fixture(scope='session')
def run_id() -> str:
    return uuid.uuid4().hex[:12]


@pytest.fixture(scope='session')
def prefix(run_id) -> str:
    return f'[mcp-test-{run_id}]'


@pytest.fixture(scope='session')
def id_store(run_id):
    """Per-run recorder — appends to .test-live-ids-<run_id>.json in the
    repo root. The cleanup script (scripts/test_live_cleanup.py) reads
    every .test-live-ids-*.json file it finds and trashes the IDs.

    Session-scoped: module-scoped fixtures (todolist_id, column_id) depend
    on it, and a fixture may only use same-or-broader-scoped fixtures."""
    path = REPO_ROOT / f'.test-live-ids-{run_id}.json'

    def record(recording_id: str, project_id: str, kind: str):
        existing = json.loads(path.read_text()) if path.exists() else []
        existing.append({
            'recording_id': str(recording_id),
            'project_id': str(project_id),
            'kind': kind,
            'recorded_at_unix': int(time.time()),
        })
        path.write_text(json.dumps(existing, indent=2))

    return record
