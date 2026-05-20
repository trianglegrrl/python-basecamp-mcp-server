#!/usr/bin/env python3
"""Smoke test: start the streamable-http server as a subprocess, hit it with
an initialize → tools/list MCP handshake, verify it returns 75 tools, exit 0.

Used by deploy.sh and operations runbook to confirm the upstream is healthy
post-restart without needing real Basecamp credentials.

Usage: python scripts/smoke_streamable_http.py [--port 8090]
"""
import argparse
import json
import subprocess
import sys
import time
import urllib.request
import urllib.error
from contextlib import contextmanager


@contextmanager
def server_on(port: int):
    """Spawn basecamp_fastmcp.py --transport streamable-http on the given port.
    Caller iterates until ready, then makes requests. Killed on exit."""
    proc = subprocess.Popen(
        [sys.executable, 'basecamp_fastmcp.py',
         '--transport', 'streamable-http', '--host', '127.0.0.1', '--port', str(port)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        # Wait up to 10s for the port to accept connections.
        deadline = time.time() + 10
        while time.time() < deadline:
            try:
                urllib.request.urlopen(f'http://127.0.0.1:{port}/mcp', timeout=0.5)
            except urllib.error.HTTPError:
                # We expect 4xx — the server's running.
                break
            except (urllib.error.URLError, ConnectionRefusedError):
                time.sleep(0.2)
        else:
            raise RuntimeError("server did not bind within 10s")
        yield proc
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--port', type=int, default=8090)
    args = ap.parse_args()

    # Initialize + tools/list via two JSON-RPC requests over Streamable HTTP.
    init_req = {
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "smoke", "version": "1.0"},
        },
    }
    list_req = {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}

    with server_on(args.port):
        # Initialize
        req = urllib.request.Request(
            f'http://127.0.0.1:{args.port}/mcp',
            data=json.dumps(init_req).encode('utf-8'),
            headers={
                'Content-Type': 'application/json',
                'Accept': 'application/json, text/event-stream',
            },
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            session_id = resp.headers.get('Mcp-Session-Id')
            assert session_id, "no Mcp-Session-Id returned"

        # tools/list with the session id
        req = urllib.request.Request(
            f'http://127.0.0.1:{args.port}/mcp',
            data=json.dumps(list_req).encode('utf-8'),
            headers={
                'Content-Type': 'application/json',
                'Accept': 'application/json, text/event-stream',
                'Mcp-Session-Id': session_id,
            },
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            body = resp.read().decode('utf-8')
            # SSE wrapper: extract the data: <json> line
            for line in body.splitlines():
                if line.startswith('data: '):
                    payload = json.loads(line[6:])
                    tools = payload.get('result', {}).get('tools', [])
                    print(f"tools/list returned {len(tools)} tools")
                    if len(tools) < 70:
                        print(f"ERROR: expected ≥70 tools, got {len(tools)}", file=sys.stderr)
                        sys.exit(1)
                    sys.exit(0)
            print("ERROR: no data: line in response", file=sys.stderr)
            sys.exit(1)


if __name__ == '__main__':
    main()
