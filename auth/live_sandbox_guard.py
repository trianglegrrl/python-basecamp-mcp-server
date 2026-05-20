"""Two-layer sandbox guard for Basecamp live tests.

Layer 1: BASECAMP_TEST_PROJECT_ID must be set in the environment.
Layer 2: The named project's `name` must contain a sentinel
         (default `MCP_TEST_SANDBOX`; overrideable via
         BASECAMP_TEST_PROJECT_NAME_GUARD).

If either layer fails, this module refuses to proceed. The live suite
and the cleanup script both use it; do NOT bypass it.
"""
from __future__ import annotations

import os
from typing import Protocol


DEFAULT_SENTINEL = 'MCP_TEST_SANDBOX'


class SandboxGuardError(RuntimeError):
    """Raised when either sandbox-guard layer refuses to proceed."""


class _ProjectFetcher(Protocol):
    def get_project(self, project_id: str) -> dict: ...


def assert_sandbox(client: _ProjectFetcher) -> str:
    """Return the validated sandbox project id.

    Raises SandboxGuardError if either layer refuses.
    """
    project_id = os.environ.get('BASECAMP_TEST_PROJECT_ID')
    if not project_id:
        raise SandboxGuardError(
            'BASECAMP_TEST_PROJECT_ID is not set. Refusing to run live tests against an unspecified project.'
        )
    sentinel = os.environ.get('BASECAMP_TEST_PROJECT_NAME_GUARD') or DEFAULT_SENTINEL
    project = client.get_project(project_id)
    name = project.get('name') or ''
    if sentinel not in name:
        raise SandboxGuardError(
            f"Project {project_id} ('{name}') does not contain the sandbox sentinel '{sentinel}' in its name. "
            'Refusing to run live tests.'
        )
    return project_id
