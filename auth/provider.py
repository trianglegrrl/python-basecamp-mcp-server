"""
Credential resolution for the Basecamp MCP server.

Two implementations, one ABC. Selected by transport mode at process startup.
See docs at github.com/trianglegrrl/python-basecamp-mcp-server (the streamable-http
PR thread) and the consuming portal spec at
docs/superpowers/specs/2026-05-19-basecamp-mcp-as-portal-upstream-design.md §5.1.

FastMCP SDK pin: mcp>=1.10,<2. Per-request HTTP headers are read via
`ctx.request_context.request.headers`. If the SDK is bumped to 2.x and that
path changes, update HeaderCredentialProvider in one place.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass

from mcp.server.fastmcp import Context


@dataclass(frozen=True)
class Credentials:
    access_token: str
    account_id: str


class CredentialProvider(ABC):
    """Resolves per-request Basecamp credentials.

    The concrete implementation depends on transport mode:
      - stdio mode: FileCredentialProvider (reads oauth_tokens.json + env)
      - streamable-http mode: HeaderCredentialProvider (reads HTTP headers from ctx)
    """

    @abstractmethod
    def credentials_for(self, ctx: Context) -> Credentials | None:
        """Return Credentials for this request, or None if unavailable.

        None signals to the calling tool that the request can't be authorized.
        The tool returns the standard auth-error response.
        """
        raise NotImplementedError
