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


import logging
import os

# Re-imported inline so existing module structure (token_storage / auth_manager
# at the repo root) keeps working. The provider boundary is the seam — these
# legacy modules stay untouched.
import token_storage
import auth_manager

logger = logging.getLogger(__name__)


class FileCredentialProvider(CredentialProvider):
    """Reads OAuth tokens from oauth_tokens.json + env, refreshes via basecamp_oauth.

    Used in stdio mode. The token file lives at $BASECAMP_MCP_TOKEN_FILE (or
    <script_dir>/oauth_tokens.json by default — see token_storage module).
    """

    def credentials_for(self, ctx: Context) -> Credentials | None:
        # ctx is intentionally unused: stdio mode has no per-request data.
        del ctx
        token_data = token_storage.get_token()
        if not token_data or not token_data.get('access_token'):
            logger.error("No OAuth token available")
            return None
        if not auth_manager.ensure_authenticated():
            logger.error("OAuth token expired and automatic refresh failed")
            return None
        token_data = token_storage.get_token()  # re-read after potential refresh
        if not token_data or not token_data.get('access_token'):
            # ensure_authenticated() reported success but the token is now
            # unreadable — return None cleanly instead of subscripting None.
            logger.error("Token unreadable after refresh reported success")
            return None
        account_id = token_data.get('account_id') or os.environ.get('BASECAMP_ACCOUNT_ID')
        if not account_id:
            logger.error("Missing account_id (not in token, not in env)")
            return None
        return Credentials(access_token=token_data['access_token'], account_id=str(account_id))
