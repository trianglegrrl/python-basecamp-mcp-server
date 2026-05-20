# auth/

Credential resolution for the Basecamp MCP server.

`CredentialProvider` is selected by `basecamp_fastmcp.py` at process startup,
based on the `--transport` CLI flag:

- `--transport stdio` → `FileCredentialProvider` reads `oauth_tokens.json` + env
  variables. Refreshes tokens via `basecamp_oauth` when needed. This is the
  legacy path for OSS users.
- `--transport streamable-http` → `HeaderCredentialProvider` reads
  `Authorization: Bearer <token>` and `X-Basecamp-Account-Id: <id>` from each
  incoming HTTP request. Refresh is the hosting proxy's job, not ours.

See `docs/superpowers/specs/2026-05-19-basecamp-mcp-as-portal-upstream-design.md`
in the `pn-ai-portal` repo for the full design (§5).
