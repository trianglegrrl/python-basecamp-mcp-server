#!/usr/bin/env python3
"""
FastMCP server for Basecamp integration.

This server implements the MCP (Model Context Protocol) using the official
Anthropic FastMCP framework, replacing the custom JSON-RPC implementation.
"""

import logging
import os
import sys
from typing import Any, Dict, List, Optional
import anyio
import httpx
from mcp.server.fastmcp import Context, FastMCP

# Import existing business logic
from basecamp_client import BasecampClient
from search_utils import BasecampSearch
import token_storage
import auth_manager
from dotenv import load_dotenv

# Determine project root (directory containing this script)
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
DOTENV_PATH = os.path.join(PROJECT_ROOT, '.env')
load_dotenv(DOTENV_PATH)

# Set up logging to file AND stderr (following MCP best practices)
LOG_FILE_PATH = os.path.join(PROJECT_ROOT, 'basecamp_fastmcp.log')
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE_PATH),
        logging.StreamHandler(sys.stderr)  # Critical: log to stderr, not stdout
    ]
)
logger = logging.getLogger('basecamp_fastmcp')

import argparse
from contextlib import asynccontextmanager
from typing import AsyncIterator

from auth.provider import (
    CredentialProvider,
    FileCredentialProvider,
    HeaderCredentialProvider,
)

# Transport is chosen at process start by the __main__ block and read once
# by the lifespan startup callback. It is NOT per-request state — the lifespan
# runs exactly once per server lifetime, after __main__ sets it.
# Spike outcome branch-a (mcp SDK 1.27.1): the provider lives in the lifespan
# context, reached by tools via ctx.request_context.lifespan_context["provider"].
_transport_mode: str = "stdio"


def make_lifespan(transport: str):
    """Build the FastMCP lifespan for the given transport.

    The returned asynccontextmanager yields a dict that FastMCP exposes as
    ctx.request_context.lifespan_context. Tools read the provider via
    lifespan_context["provider"]. One provider object per server lifetime.
    """
    @asynccontextmanager
    async def _lifespan(_app: FastMCP) -> AsyncIterator[dict]:
        provider: CredentialProvider = (
            HeaderCredentialProvider()
            if transport == "streamable-http"
            else FileCredentialProvider()
        )
        logger.info("Lifespan startup: provider=%s", type(provider).__name__)
        yield {"provider": provider}

    return _lifespan


@asynccontextmanager
async def _module_lifespan(app: FastMCP) -> AsyncIterator[dict]:
    """Lifespan bound onto `mcp` at construction. FastMCP has no post-construction
    lifespan setter (mcp SDK 1.27.1), but the transport is only known in
    __main__ — so this defers the choice by reading _transport_mode when the
    `async with` body runs (startup), strictly after __main__ assigns it."""
    async with make_lifespan(_transport_mode)(app) as ctx_dict:
        yield ctx_dict


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Basecamp 3 MCP server (FastMCP)")
    p.add_argument(
        '--transport',
        choices=['stdio', 'streamable-http'],
        default='stdio',
        help='Transport mode. stdio (default) is the legacy single-user path; '
             'streamable-http binds an HTTP server for hosted multi-user use.',
    )
    p.add_argument('--host', default='127.0.0.1',
        help='Bind host for streamable-http transport. Ignored for stdio.')
    p.add_argument('--port', type=int, default=8084,
        help='Bind port for streamable-http transport. Ignored for stdio.')
    return p.parse_args(argv)


# Initialize FastMCP server
mcp = FastMCP("basecamp", lifespan=_module_lifespan)

# Auth helper functions (reused from original server)
def _get_basecamp_client(ctx: Context) -> Optional[BasecampClient]:
    """Construct a per-request BasecampClient via the active CredentialProvider.

    The provider lives in the FastMCP lifespan context — read it via
    _provider_from_ctx(ctx). The caller MUST pass a Context; the legacy no-args
    path was removed in PR T3 once all 75 tools took ctx.
    """
    try:
        provider = _provider_from_ctx(ctx)
        if provider is None:
            logger.error("_get_basecamp_client: no provider in lifespan_context "
                         "— server lifespan misconfigured")
            return None
        creds = provider.credentials_for(ctx)
        if creds is None:
            return None
        user_agent = os.getenv('USER_AGENT') or "Basecamp MCP Server (mcp@basecamp-server.dev)"
        return BasecampClient(
            access_token=creds.access_token,
            account_id=creds.account_id,
            user_agent=user_agent,
            auth_mode='oauth',
        )
    except Exception as e:
        # Name the exception type so an operator can tell a misconfigured
        # lifespan from a network fault from a programming bug at a glance.
        logger.error("Error creating Basecamp client: %s: %s", type(e).__name__, e)
        return None


def _provider_from_ctx(ctx: Context) -> Optional[CredentialProvider]:
    """Pull the CredentialProvider out of the FastMCP lifespan context.

    The lifespan (see make_lifespan) yields {"provider": <CredentialProvider>};
    FastMCP exposes it at ctx.request_context.lifespan_context. The single
    AttributeError catch deliberately covers BOTH attribute hops — ctx missing
    request_context, or request_context missing lifespan_context — since either
    means a misconfigured lifespan or an unpopulated test ctx. A non-dict
    lifespan_context likewise yields None. The caller logs the None case."""
    try:
        lifespan_ctx = ctx.request_context.lifespan_context
    except AttributeError:
        return None
    if isinstance(lifespan_ctx, dict):
        return lifespan_ctx.get("provider")
    return None


def _get_auth_error_response(ctx: Context) -> Dict[str, Any]:
    """Return a consistent auth-error response dict.

    Accepts `ctx` because every ctx-migrated tool calls this as
    `_get_auth_error_response(ctx)`. The message is currently transport-agnostic
    so `ctx` is not read — it keeps the 75 call sites uniform and is the seam
    for a future transport-aware error message.
    """
    del ctx  # accepted for call-site uniformity; not used by the current message
    if token_storage.is_token_expired():
        return {
            "error": "OAuth token expired",
            "message": "Your Basecamp OAuth token has expired. Please re-authenticate by visiting http://localhost:8000 and completing the OAuth flow again."
        }
    else:
        return {
            "error": "Authentication required", 
            "message": "Please authenticate with Basecamp first. Visit http://localhost:8000 to log in."
        }

async def _run_sync(func, *args, **kwargs):
    """Wrapper to run synchronous functions in thread pool."""
    return await anyio.to_thread.run_sync(func, *args, **kwargs)

# Core MCP Tools - Starting with essential ones from original server

# Tool batches for the Chunk 3 migration (PR T2). See plan §Chunk 3.
# Each batch is one PR. Tool names per batch are the canonical list — line
# numbers are NOT recorded here (they would rot the moment Batch A's edits
# land; the tool list is stable, the lines are not).
#
#   Batch A — projects + search                                       (4 tools)
#   Batch B — todos + todolists                                       (17 tools)
#   Batch C — messages + campfire + comments + inbox/forwards         (14 tools)
#   Batch D — cards / columns / check-ins                             (21 tools)
#   Batch E — card steps + attachments                                (8 tools)
#   Batch F — documents / uploads / webhooks / events                 (11 tools)

@mcp.tool()
async def get_projects(ctx: Context) -> Dict[str, Any]:
    """Get all Basecamp projects."""
    client = _get_basecamp_client(ctx)
    if not client:
        return _get_auth_error_response(ctx)
    
    try:
        projects = await _run_sync(client.get_projects)
        return {
            "status": "success",
            "projects": projects,
            "count": len(projects)
        }
    except Exception as e:
        logger.error(f"Error getting projects: {e}")
        if "401" in str(e) and "expired" in str(e).lower():
            return {
                "error": "OAuth token expired",
                "message": "Your Basecamp OAuth token expired during the API call. Please re-authenticate by visiting http://localhost:8000 and completing the OAuth flow again."
            }
        return {
            "error": "Execution error",
            "message": str(e)
        }

@mcp.tool()
async def get_project(ctx: Context, project_id: str) -> Dict[str, Any]:
    """Get details for a specific project.

    Args:
        project_id: The project ID
    """
    client = _get_basecamp_client(ctx)
    if not client:
        return _get_auth_error_response(ctx)
    
    try:
        project = await _run_sync(client.get_project, project_id)
        return {
            "status": "success",
            "project": project
        }
    except Exception as e:
        logger.error(f"Error getting project {project_id}: {e}")
        if "401" in str(e) and "expired" in str(e).lower():
            return {
                "error": "OAuth token expired",
                "message": "Your Basecamp OAuth token expired during the API call. Please re-authenticate by visiting http://localhost:8000 and completing the OAuth flow again."
            }
        return {
            "error": "Execution error",
            "message": str(e)
        }

@mcp.tool()
async def create_project(ctx: Context, name: str,
                         description: Optional[str] = None) -> Dict[str, Any]:
    """Create a new Basecamp project.

    Args:
        name: Project name (required by BC3)
        description: Optional project description (free-form text)

    Note:
        Free-plan accounts return 507 Insufficient Storage when the project
        cap is hit; the message is surfaced verbatim in the error response.
    """
    client = _get_basecamp_client(ctx)
    if not client:
        return _get_auth_error_response(ctx)

    try:
        project = await _run_sync(
            lambda: client.create_project(name=name, description=description)
        )
        return {
            "status": "success",
            "project": project,
            "message": f"Project '{name}' created successfully",
        }
    except Exception as e:
        logger.error(f"Error creating project: {e}")
        if "401" in str(e) and "expired" in str(e).lower():
            return {
                "error": "OAuth token expired",
                "message": "Your Basecamp OAuth token expired during the API call. Please re-authenticate by visiting http://localhost:8000 and completing the OAuth flow again."
            }
        return {
            "error": "Execution error",
            "message": str(e)
        }


@mcp.tool()
async def update_project(ctx: Context, project_id: str,
                         name: Optional[str] = None,
                         description: Optional[str] = None,
                         admissions: Optional[str] = None,
                         schedule_attributes: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Update a Basecamp project. BC3 requires `name` in the PUT body even
    when changing only the description, so this fetch-then-merges: the client
    GETs the current project and supplies the current `name` if the patch
    omits it. Only the whitelisted fields (name, description, admissions,
    schedule_attributes) are forwarded.

    Args:
        project_id: Project ID to update
        name: New project name. Omit to keep current.
        description: New project description.
        admissions: Project admissions setting.
        schedule_attributes: Project schedule attributes dict.

    Note:
        None / omitted = "leave current value". This tool cannot CLEAR a field
        back to empty — that requires the BC3 UI. Same limitation as update_todo.
    """
    client = _get_basecamp_client(ctx)
    if not client:
        return _get_auth_error_response(ctx)

    try:
        project = await _run_sync(
            lambda: client.update_project(
                project_id, name=name, description=description,
                admissions=admissions, schedule_attributes=schedule_attributes,
            )
        )
        return {
            "status": "success",
            "project": project,
            "message": f"Project {project_id} updated successfully",
        }
    except Exception as e:
        logger.error(f"Error updating project {project_id}: {e}")
        if "401" in str(e) and "expired" in str(e).lower():
            return {
                "error": "OAuth token expired",
                "message": "Your Basecamp OAuth token expired during the API call. Please re-authenticate by visiting http://localhost:8000 and completing the OAuth flow again."
            }
        return {
            "error": "Execution error",
            "message": str(e)
        }


@mcp.tool()
async def trash_project(ctx: Context, project_id: str) -> Dict[str, Any]:
    """Trash (soft-delete) a Basecamp project. Recoverable from the BC3 UI
    for 30 days, after which BC3 hard-deletes it.

    Args:
        project_id: Project ID to trash
    """
    client = _get_basecamp_client(ctx)
    if not client:
        return _get_auth_error_response(ctx)

    try:
        await _run_sync(lambda: client.trash_project(project_id))
        return {
            "status": "success",
            "message": f"Project {project_id} trashed successfully (recoverable from the BC3 UI for 30 days)",
        }
    except Exception as e:
        logger.error(f"Error trashing project {project_id}: {e}")
        if "401" in str(e) and "expired" in str(e).lower():
            return {
                "error": "OAuth token expired",
                "message": "Your Basecamp OAuth token expired during the API call. Please re-authenticate by visiting http://localhost:8000 and completing the OAuth flow again."
            }
        return {
            "error": "Execution error",
            "message": str(e)
        }


@mcp.tool()
async def update_project_access(ctx: Context, project_id: str,
                                grant: Optional[List[int]] = None,
                                revoke: Optional[List[int]] = None,
                                create: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """Grant / revoke project access, or invite new people.

    BC3 silently drops string IDs in `grant` / `revoke` — callers MUST pass
    numeric person IDs (use `get_people` to look them up).

    Args:
        project_id: Project ID
        grant: Numeric person IDs to add to the project.
        revoke: Numeric person IDs to remove from the project.
        create: New people to invite. Each dict needs `name` and
                `email_address`; `title` and `company_name` are optional.
    """
    client = _get_basecamp_client(ctx)
    if not client:
        return _get_auth_error_response(ctx)

    try:
        access = await _run_sync(
            lambda: client.update_project_access(
                project_id, grant=grant, revoke=revoke, create=create,
            )
        )
        return {
            "status": "success",
            "access": access,
            "message": f"Project {project_id} access updated successfully",
        }
    except Exception as e:
        logger.error(f"Error updating project access {project_id}: {e}")
        if "401" in str(e) and "expired" in str(e).lower():
            return {
                "error": "OAuth token expired",
                "message": "Your Basecamp OAuth token expired during the API call. Please re-authenticate by visiting http://localhost:8000 and completing the OAuth flow again."
            }
        return {
            "error": "Execution error",
            "message": str(e)
        }


@mcp.tool()
async def search_basecamp(ctx: Context, query: str, project_id: Optional[str] = None) -> Dict[str, Any]:
    """Search across Basecamp projects, todos, and messages.

    Args:
        query: Search query
        project_id: Optional project ID to limit search scope
    """
    client = _get_basecamp_client(ctx)
    if not client:
        return _get_auth_error_response(ctx)
    
    try:
        search = BasecampSearch(client=client)
        results = {}

        if project_id:
            # Search within specific project
            results["todolists"] = await _run_sync(search.search_todolists, query, project_id)
            results["todos"] = await _run_sync(search.search_todos, query, project_id)
        else:
            # Search across all projects
            results["projects"] = await _run_sync(search.search_projects, query)
            results["todos"] = await _run_sync(search.search_todos, query)
            results["messages"] = await _run_sync(search.search_messages, query)

        return {
            "status": "success",
            "query": query,
            "results": results
        }
    except Exception as e:
        logger.error(f"Error searching Basecamp: {e}")
        if "401" in str(e) and "expired" in str(e).lower():
            return {
                "error": "OAuth token expired",
                "message": "Your Basecamp OAuth token expired during the API call. Please re-authenticate by visiting http://localhost:8000 and completing the OAuth flow again."
            }
        return {
            "error": "Execution error",
            "message": str(e)
        }

@mcp.tool()
async def get_todolists(ctx: Context, project_id: str) -> Dict[str, Any]:
    """Get todo lists for a project.

    Args:
        project_id: The project ID
    """
    client = _get_basecamp_client(ctx)
    if not client:
        return _get_auth_error_response(ctx)
    
    try:
        todolists = await _run_sync(client.get_todolists, project_id)
        return {
            "status": "success",
            "todolists": todolists,
            "count": len(todolists)
        }
    except Exception as e:
        logger.error(f"Error getting todolists: {e}")
        if "401" in str(e) and "expired" in str(e).lower():
            return {
                "error": "OAuth token expired",
                "message": "Your Basecamp OAuth token expired during the API call. Please re-authenticate by visiting http://localhost:8000 and completing the OAuth flow again."
            }
        return {
            "error": "Execution error",
            "message": str(e)
        }

@mcp.tool()
async def get_todos(ctx: Context, project_id: str, todolist_id: str) -> Dict[str, Any]:
    """Get todos from a todo list.

    Args:
        project_id: Project ID
        todolist_id: The todo list ID
    """
    client = _get_basecamp_client(ctx)
    if not client:
        return _get_auth_error_response(ctx)
    
    try:
        todos = await _run_sync(client.get_todos, project_id, todolist_id)
        return {
            "status": "success",
            "todos": todos,
            "count": len(todos)
        }
    except Exception as e:
        logger.error(f"Error getting todos: {e}")
        if "401" in str(e) and "expired" in str(e).lower():
            return {
                "error": "OAuth token expired",
                "message": "Your Basecamp OAuth token expired during the API call. Please re-authenticate by visiting http://localhost:8000 and completing the OAuth flow again."
            }
        return {
            "error": "Execution error",
            "message": str(e)
        }

@mcp.tool()
async def get_todo(ctx: Context, project_id: str, todo_id: str) -> Dict[str, Any]:
    """Get a single todo item by its ID.

    Args:
        project_id: Project ID
        todo_id: The todo ID
    """
    client = _get_basecamp_client(ctx)
    if not client:
        return _get_auth_error_response(ctx)

    try:
        todo = await _run_sync(client.get_todo, project_id, todo_id)
        return {
            "status": "success",
            "todo": todo
        }
    except Exception as e:
        logger.error(f"Error getting todo {todo_id}: {e}")
        if "401" in str(e) and "expired" in str(e).lower():
            return {
                "error": "OAuth token expired",
                "message": "Your Basecamp OAuth token expired during the API call. Please re-authenticate by visiting http://localhost:8000 and completing the OAuth flow again."
            }
        return {
            "error": "Execution error",
            "message": str(e)
        }

@mcp.tool()
async def create_todo(ctx: Context, project_id: str, todolist_id: str, content: str,
                     description: Optional[str] = None,
                     assignee_ids: Optional[List[str]] = None,
                     completion_subscriber_ids: Optional[List[str]] = None,
                     notify: bool = False,
                     due_on: Optional[str] = None,
                     starts_on: Optional[str] = None) -> Dict[str, Any]:
    """Create a new todo item in a todo list.

    Args:
        project_id: Project ID
        todolist_id: The todo list ID
        content: The todo item's text (required)
        description: HTML description of the todo
        assignee_ids: List of person IDs to assign
        completion_subscriber_ids: List of person IDs to notify on completion
        notify: Whether to notify assignees
        due_on: Due date in YYYY-MM-DD format
        starts_on: Start date in YYYY-MM-DD format
    """
    client = _get_basecamp_client(ctx)
    if not client:
        return _get_auth_error_response(ctx)
    
    try:
        # Use lambda to properly handle keyword arguments
        todo = await _run_sync(
            lambda: client.create_todo(
                project_id, todolist_id, content,
                description=description,
                assignee_ids=assignee_ids,
                completion_subscriber_ids=completion_subscriber_ids,
                notify=notify,
                due_on=due_on,
                starts_on=starts_on
            )
        )
        return {
            "status": "success",
            "todo": todo,
            "message": f"Todo '{content}' created successfully"
        }
    except Exception as e:
        logger.error(f"Error creating todo: {e}")
        if "401" in str(e) and "expired" in str(e).lower():
            return {
                "error": "OAuth token expired",
                "message": "Your Basecamp OAuth token expired during the API call. Please re-authenticate by visiting http://localhost:8000 and completing the OAuth flow again."
            }
        return {
            "error": "Execution error",
            "message": str(e)
        }

@mcp.tool()
async def update_todo(ctx: Context, project_id: str, todo_id: str,
                     content: Optional[str] = None,
                     description: Optional[str] = None,
                     assignee_ids: Optional[List[str]] = None,
                     completion_subscriber_ids: Optional[List[str]] = None,
                     notify: Optional[bool] = None,
                     due_on: Optional[str] = None,
                     starts_on: Optional[str] = None) -> Dict[str, Any]:
    """Update an existing todo item.

    Args:
        project_id: Project ID
        todo_id: The todo ID
        content: The todo item's text
        description: HTML description of the todo
        assignee_ids: List of person IDs to assign
        completion_subscriber_ids: List of person IDs to notify on completion
        due_on: Due date in YYYY-MM-DD format
        starts_on: Start date in YYYY-MM-DD format
    """
    client = _get_basecamp_client(ctx)
    if not client:
        return _get_auth_error_response(ctx)
    
    try:
        # Guard against no-op updates
        if all(v is None for v in [content, description, assignee_ids,
                                   completion_subscriber_ids, notify,
                                   due_on, starts_on]):
            return {
                "error": "Invalid input",
                "message": "At least one field to update must be provided"
            }
        # Use lambda to properly handle keyword arguments
        todo = await _run_sync(
            lambda: client.update_todo(
                project_id, todo_id,
                content=content,
                description=description,
                assignee_ids=assignee_ids,
                completion_subscriber_ids=completion_subscriber_ids,
                notify=notify,
                due_on=due_on,
                starts_on=starts_on
            )
        )
        return {
            "status": "success",
            "todo": todo,
            "message": "Todo updated successfully"
        }
    except Exception as e:
        logger.error(f"Error updating todo: {e}")
        if "401" in str(e) and "expired" in str(e).lower():
            return {
                "error": "OAuth token expired",
                "message": "Your Basecamp OAuth token expired during the API call. Please re-authenticate by visiting http://localhost:8000 and completing the OAuth flow again."
            }
        return {
            "error": "Execution error",
            "message": str(e)
        }

@mcp.tool()
async def delete_todo(ctx: Context, project_id: str, todo_id: str) -> Dict[str, Any]:
    """Move a todo item to the trash.

    Trashed todos can be recovered from the Basecamp web UI within 30 days.

    Args:
        project_id: Project ID
        todo_id: The todo ID
    """
    client = _get_basecamp_client(ctx)
    if not client:
        return _get_auth_error_response(ctx)

    try:
        await _run_sync(client.delete_todo, project_id, todo_id)
        return {
            "status": "success",
            "message": "Todo moved to trash"
        }
    except Exception as e:
        logger.error(f"Error trashing todo: {e}")
        if "401" in str(e) and "expired" in str(e).lower():
            return {
                "error": "OAuth token expired",
                "message": "Your Basecamp OAuth token expired during the API call. Please re-authenticate by visiting http://localhost:8000 and completing the OAuth flow again."
            }
        return {
            "error": "Execution error",
            "message": str(e)
        }

@mcp.tool()
async def complete_todo(ctx: Context, project_id: str, todo_id: str) -> Dict[str, Any]:
    """Mark a todo item as complete.

    Args:
        project_id: Project ID
        todo_id: The todo ID
    """
    client = _get_basecamp_client(ctx)
    if not client:
        return _get_auth_error_response(ctx)
    
    try:
        completion = await _run_sync(client.complete_todo, project_id, todo_id)
        return {
            "status": "success",
            "completion": completion,
            "message": "Todo marked as complete"
        }
    except Exception as e:
        logger.error(f"Error completing todo: {e}")
        if "401" in str(e) and "expired" in str(e).lower():
            return {
                "error": "OAuth token expired",
                "message": "Your Basecamp OAuth token expired during the API call. Please re-authenticate by visiting http://localhost:8000 and completing the OAuth flow again."
            }
        return {
            "error": "Execution error",
            "message": str(e)
        }

@mcp.tool()
async def uncomplete_todo(ctx: Context, project_id: str, todo_id: str) -> Dict[str, Any]:
    """Mark a todo item as incomplete.

    Args:
        project_id: Project ID
        todo_id: The todo ID
    """
    client = _get_basecamp_client(ctx)
    if not client:
        return _get_auth_error_response(ctx)
    
    try:
        await _run_sync(client.uncomplete_todo, project_id, todo_id)
        return {
            "status": "success",
            "message": "Todo marked as incomplete"
        }
    except Exception as e:
        logger.error(f"Error uncompleting todo: {e}")
        if "401" in str(e) and "expired" in str(e).lower():
            return {
                "error": "OAuth token expired",
                "message": "Your Basecamp OAuth token expired during the API call. Please re-authenticate by visiting http://localhost:8000 and completing the OAuth flow again."
            }
        return {
            "error": "Execution error",
            "message": str(e)
        }

@mcp.tool()
async def archive_todo(ctx: Context, project_id: str, todo_id: str) -> Dict[str, Any]:
    """Archive a todo item.

    Archived todos are hidden from the active list but remain accessible
    via the Basecamp web UI.

    Args:
        project_id: Project ID
        todo_id: The todo ID
    """
    client = _get_basecamp_client(ctx)
    if not client:
        return _get_auth_error_response(ctx)

    try:
        await _run_sync(client.archive_todo, project_id, todo_id)
        return {"status": "success", "message": f"Todo {todo_id} archived"}
    except Exception as e:
        logger.error(f"Error archiving todo {todo_id}: {e}")
        if "401" in str(e) and "expired" in str(e).lower():
            return {"error": "OAuth token expired", "message": "Your Basecamp OAuth token expired during the API call. Please re-authenticate by visiting http://localhost:8000 and completing the OAuth flow again."}
        return {"error": "Execution error", "message": str(e)}


@mcp.tool()
async def reposition_todo(
    ctx: Context,
    project_id: str,
    todo_id: str,
    position: int,
    parent_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Reposition a todo within its list, or move it to another list or group.

    Args:
        project_id: The project ID
        todo_id: The todo ID
        position: New 1-based position within the target list
        parent_id: ID of the target todolist or group to move the todo into.
                   Omit to keep the todo in its current list and only change position.
    """
    client = _get_basecamp_client(ctx)
    if not client:
        return _get_auth_error_response(ctx)

    if position < 1:
        return {"error": "Invalid input", "message": "position must be >= 1"}

    try:
        await _run_sync(
            lambda: client.reposition_todo(project_id, todo_id, position, parent_id)
        )
        return {"status": "success", "message": f"Todo {todo_id} moved to position {position}"}
    except Exception as e:
        logger.error(f"Error repositioning todo {todo_id}: {e}")
        if "401" in str(e) and "expired" in str(e).lower():
            return {"error": "OAuth token expired", "message": "Your Basecamp OAuth token expired during the API call. Please re-authenticate by visiting http://localhost:8000 and completing the OAuth flow again."}
        return {"error": "Execution error", "message": str(e)}


@mcp.tool()
async def global_search(ctx: Context, query: str) -> Dict[str, Any]:
    """Search projects, todos and campfire messages across all projects.

    Args:
        query: Search query
    """
    client = _get_basecamp_client(ctx)
    if not client:
        return _get_auth_error_response(ctx)
    
    try:
        search = BasecampSearch(client=client)
        results = await _run_sync(search.global_search, query)
        return {
            "status": "success",
            "query": query,
            "results": results
        }
    except Exception as e:
        logger.error(f"Error in global search: {e}")
        if "401" in str(e) and "expired" in str(e).lower():
            return {
                "error": "OAuth token expired",
                "message": "Your Basecamp OAuth token expired during the API call. Please re-authenticate by visiting http://localhost:8000 and completing the OAuth flow again."
            }
        return {
            "error": "Execution error",
            "message": str(e)
        }

@mcp.tool()
async def get_comments(ctx: Context, recording_id: str, project_id: str, page: int = 1) -> Dict[str, Any]:
    """Get comments for a Basecamp item.

    Args:
        recording_id: The item ID
        project_id: The project ID
        page: Page number for pagination (default: 1). Basecamp uses geared pagination:
              page 1 has 15 results, page 2 has 30, page 3 has 50, page 4+ has 100.
    """
    client = _get_basecamp_client(ctx)
    if not client:
        return _get_auth_error_response(ctx)

    try:
        result = await _run_sync(client.get_comments, recording_id, project_id, page)
        return {
            "status": "success",
            "comments": result["comments"],
            "count": len(result["comments"]),
            "page": page,
            "total_count": result["total_count"],
            "next_page": result["next_page"]
        }
    except Exception as e:
        logger.error(f"Error getting comments: {e}")
        if "401" in str(e) and "expired" in str(e).lower():
            return {
                "error": "OAuth token expired",
                "message": "Your Basecamp OAuth token expired during the API call. Please re-authenticate by visiting http://localhost:8000 and completing the OAuth flow again."
            }
        return {
            "error": "Execution error",
            "message": str(e)
        }

@mcp.tool()
async def create_comment(ctx: Context, recording_id: str, project_id: str, content: str) -> Dict[str, Any]:
    """Create a comment on a Basecamp item.

    Args:
        recording_id: The item ID
        project_id: The project ID
        content: The comment content in HTML format
    """
    client = _get_basecamp_client(ctx)
    if not client:
        return _get_auth_error_response(ctx)

    try:
        comment = await _run_sync(client.create_comment, recording_id, project_id, content)
        return {
            "status": "success",
            "comment": comment,
            "message": "Comment created successfully"
        }
    except Exception as e:
        logger.error(f"Error creating comment: {e}")
        if "401" in str(e) and "expired" in str(e).lower():
            return {
                "error": "OAuth token expired",
                "message": "Your Basecamp OAuth token expired during the API call. Please re-authenticate by visiting http://localhost:8000 and completing the OAuth flow again.",
            }
        return {
            "error": "Execution error",
            "message": str(e)
        }

@mcp.tool()
async def get_campfire_lines(ctx: Context, project_id: str, campfire_id: str) -> Dict[str, Any]:
    """Get recent messages from a Basecamp campfire (chat room).

    Args:
        project_id: The project ID
        campfire_id: The campfire/chat room ID
    """
    client = _get_basecamp_client(ctx)
    if not client:
        return _get_auth_error_response(ctx)
    
    try:
        lines = await _run_sync(client.get_campfire_lines, project_id, campfire_id)
        return {
            "status": "success",
            "campfire_lines": lines,
            "count": len(lines)
        }
    except Exception as e:
        logger.error(f"Error getting campfire lines: {e}")
        if "401" in str(e) and "expired" in str(e).lower():
            return {
                "error": "OAuth token expired",
                "message": "Your Basecamp OAuth token expired during the API call. Please re-authenticate by visiting http://localhost:8000 and completing the OAuth flow again."
            }
        return {
            "error": "Execution error",
            "message": str(e)
        }

@mcp.tool()
async def get_message_board(ctx: Context, project_id: str) -> Dict[str, Any]:
    """Get the message board for a project.

    Args:
        project_id: The project ID
    """
    client = _get_basecamp_client(ctx)
    if not client:
        return _get_auth_error_response(ctx)

    try:
        message_board = await _run_sync(client.get_message_board, project_id)
        return {
            "status": "success",
            "message_board": message_board
        }
    except Exception as e:
        logger.error(f"Error getting message board: {e}")
        if "401" in str(e) and "expired" in str(e).lower():
            return {
                "error": "OAuth token expired",
                "message": "Your Basecamp OAuth token expired during the API call. Please re-authenticate by visiting http://localhost:8000 and completing the OAuth flow again."
            }
        return {
            "error": "Execution error",
            "message": str(e)
        }

@mcp.tool()
async def get_messages(ctx: Context, project_id: str, message_board_id: Optional[str] = None) -> Dict[str, Any]:
    """Get all messages from a project's message board.

    Args:
        project_id: The project ID
        message_board_id: Optional message board ID. If not provided, will be auto-discovered from the project.
    """
    client = _get_basecamp_client(ctx)
    if not client:
        return _get_auth_error_response(ctx)

    try:
        messages = await _run_sync(client.get_messages, project_id, message_board_id)
        return {
            "status": "success",
            "messages": messages,
            "count": len(messages)
        }
    except Exception as e:
        logger.error(f"Error getting messages: {e}")
        if "401" in str(e) and "expired" in str(e).lower():
            return {
                "error": "OAuth token expired",
                "message": "Your Basecamp OAuth token expired during the API call. Please re-authenticate by visiting http://localhost:8000 and completing the OAuth flow again."
            }
        return {
            "error": "Execution error",
            "message": str(e)
        }

@mcp.tool()
async def get_message(ctx: Context, project_id: str, message_id: str) -> Dict[str, Any]:
    """Get a specific message by ID.

    Args:
        project_id: The project ID
        message_id: The message ID
    """
    client = _get_basecamp_client(ctx)
    if not client:
        return _get_auth_error_response(ctx)

    try:
        message = await _run_sync(client.get_message, project_id, message_id)
        return {
            "status": "success",
            "message": message
        }
    except Exception as e:
        logger.error(f"Error getting message: {e}")
        if "401" in str(e) and "expired" in str(e).lower():
            return {
                "error": "OAuth token expired",
                "message": "Your Basecamp OAuth token expired during the API call. Please re-authenticate by visiting http://localhost:8000 and completing the OAuth flow again."
            }
        return {
            "error": "Execution error",
            "message": str(e)
        }


@mcp.tool()
async def get_message_categories(ctx: Context, project_id: str) -> Dict[str, Any]:
    """Get message categories (types) for a project.

    Args:
        project_id: The project ID
    """
    client = _get_basecamp_client(ctx)
    if not client:
        return _get_auth_error_response(ctx)

    try:
        categories = await _run_sync(client.get_message_categories, project_id)
        return {
            "status": "success",
            "categories": categories,
            "count": len(categories)
        }
    except Exception as e:
        logger.error(f"Error getting message categories: {e}")
        if "401" in str(e) and "expired" in str(e).lower():
            return {
                "error": "OAuth token expired",
                "message": "Your Basecamp OAuth token expired during the API call. Please re-authenticate by visiting http://localhost:8000 and completing the OAuth flow again."
            }
        return {
            "error": "Execution error",
            "message": str(e)
        }


@mcp.tool()
async def create_message(ctx: Context, project_id: str, subject: str, content: str,
                         message_board_id: Optional[str] = None,
                         category_id: Optional[str] = None) -> Dict[str, Any]:
    """Create a new message on a project's message board.

    Args:
        project_id: The project ID
        subject: Message title/subject
        content: Message body in HTML format
        message_board_id: Optional message board ID. If not provided, will be auto-discovered from the project.
        category_id: Optional message type/category ID
    """
    client = _get_basecamp_client(ctx)
    if not client:
        return _get_auth_error_response(ctx)

    try:
        message = await _run_sync(
            lambda: client.create_message(
                project_id, subject, content,
                message_board_id=message_board_id,
                category_id=category_id
            )
        )
        return {
            "status": "success",
            "message": message,
            "result": f"Message '{subject}' created successfully"
        }
    except Exception as e:
        logger.error(f"Error creating message: {e}")
        if "401" in str(e) and "expired" in str(e).lower():
            return {
                "error": "OAuth token expired",
                "message": "Your Basecamp OAuth token expired during the API call. Please re-authenticate by visiting http://localhost:8000 and completing the OAuth flow again."
            }
        return {
            "error": "Execution error",
            "message": str(e)
        }


# Schedule Tools (Calendar Entries)
@mcp.tool()
async def get_schedule(ctx: Context, project_id: str) -> Dict[str, Any]:
    """Get a project's schedule (calendar). The schedule ID is auto-discovered
    from the project dock — call this before get_schedule_entries if you need
    the schedule's id or entries_url.

    Args:
        project_id: The project ID
    """
    client = _get_basecamp_client(ctx)
    if not client:
        return _get_auth_error_response(ctx)

    try:
        schedule = await _run_sync(client.get_schedule, project_id)
        return {
            "status": "success",
            "schedule": schedule
        }
    except Exception as e:
        logger.error(f"Error getting schedule for project {project_id}: {e}")
        if "401" in str(e) and "expired" in str(e).lower():
            return {
                "error": "OAuth token expired",
                "message": "Your Basecamp OAuth token expired during the API call. Please re-authenticate by visiting http://localhost:8000 and completing the OAuth flow again."
            }
        return {
            "error": "Execution error",
            "message": str(e)
        }


@mcp.tool()
async def get_schedule_entries(ctx: Context, project_id: str,
                               schedule_id: Optional[str] = None) -> Dict[str, Any]:
    """List all entries on a project's schedule.

    Args:
        project_id: The project ID
        schedule_id: Optional schedule ID. If not provided, will be auto-discovered from the project dock.
    """
    client = _get_basecamp_client(ctx)
    if not client:
        return _get_auth_error_response(ctx)

    try:
        entries = await _run_sync(
            lambda: client.get_schedule_entries(project_id, schedule_id=schedule_id)
        )
        return {
            "status": "success",
            "entries": entries,
            "count": len(entries) if entries else 0,
        }
    except Exception as e:
        logger.error(f"Error getting schedule entries for project {project_id}: {e}")
        if "401" in str(e) and "expired" in str(e).lower():
            return {
                "error": "OAuth token expired",
                "message": "Your Basecamp OAuth token expired during the API call. Please re-authenticate by visiting http://localhost:8000 and completing the OAuth flow again."
            }
        return {
            "error": "Execution error",
            "message": str(e)
        }


@mcp.tool()
async def get_schedule_entry(ctx: Context, project_id: str,
                             entry_id: str) -> Dict[str, Any]:
    """Get a single schedule entry by ID.

    Args:
        project_id: The project ID
        entry_id: The schedule entry ID
    """
    client = _get_basecamp_client(ctx)
    if not client:
        return _get_auth_error_response(ctx)

    try:
        entry = await _run_sync(client.get_schedule_entry, project_id, entry_id)
        return {
            "status": "success",
            "entry": entry,
        }
    except Exception as e:
        logger.error(f"Error getting schedule entry {entry_id}: {e}")
        if "401" in str(e) and "expired" in str(e).lower():
            return {
                "error": "OAuth token expired",
                "message": "Your Basecamp OAuth token expired during the API call. Please re-authenticate by visiting http://localhost:8000 and completing the OAuth flow again."
            }
        return {
            "error": "Execution error",
            "message": str(e)
        }


@mcp.tool()
async def create_schedule_entry(ctx: Context, project_id: str,
                                summary: str, starts_at: str, ends_at: str,
                                description: Optional[str] = None,
                                participant_ids: Optional[List[int]] = None,
                                all_day: Optional[bool] = None,
                                notify: Optional[bool] = None) -> Dict[str, Any]:
    """Create a schedule entry on a project's schedule. The schedule ID is
    auto-discovered from the project dock.

    Args:
        project_id: The project ID
        summary: Entry title (required by BC3)
        starts_at: ISO-8601 start timestamp (required by BC3)
        ends_at: ISO-8601 end timestamp (required by BC3)
        description: Optional HTML body
        participant_ids: Optional list of person IDs to add as participants
            (BC3 requires numeric IDs — pass ints, not name strings)
        all_day: Optional. If True, BC3 ignores times in starts_at/ends_at
        notify: Optional. If True, BC3 notifies participants
    """
    client = _get_basecamp_client(ctx)
    if not client:
        return _get_auth_error_response(ctx)

    try:
        entry = await _run_sync(
            lambda: client.create_schedule_entry(
                project_id,
                summary=summary,
                starts_at=starts_at,
                ends_at=ends_at,
                description=description,
                participant_ids=participant_ids,
                all_day=all_day,
                notify=notify,
            )
        )
        return {
            "status": "success",
            "entry": entry,
            "message": f"Schedule entry '{summary}' created successfully",
        }
    except Exception as e:
        logger.error(f"Error creating schedule entry: {e}")
        if "401" in str(e) and "expired" in str(e).lower():
            return {
                "error": "OAuth token expired",
                "message": "Your Basecamp OAuth token expired during the API call. Please re-authenticate by visiting http://localhost:8000 and completing the OAuth flow again."
            }
        return {
            "error": "Execution error",
            "message": str(e)
        }


@mcp.tool()
async def update_schedule_entry(ctx: Context, project_id: str, entry_id: str,
                                summary: Optional[str] = None,
                                description: Optional[str] = None,
                                starts_at: Optional[str] = None,
                                ends_at: Optional[str] = None,
                                participant_ids: Optional[List[int]] = None,
                                all_day: Optional[bool] = None,
                                notify: Optional[bool] = None) -> Dict[str, Any]:
    """Update a schedule entry. BC3's PUT replaces the full representation, so
    this fetch-then-merges: the client GETs the current entry and supplies the
    current value for every whitelisted field the patch omits. Only the
    whitelisted fields (summary, description, starts_at, ends_at,
    participant_ids, all_day, notify) are forwarded.

    Args:
        project_id: The project ID
        entry_id: The schedule entry ID
        summary: New title. Omit/None keeps current.
        description: New HTML body. Omit/None keeps current.
        starts_at: New ISO start timestamp.
        ends_at: New ISO end timestamp.
        participant_ids: New participant list (BC3 replaces, not merges —
            pass the full desired set; numeric IDs).
        all_day: All-day flag.
        notify: Notify-participants flag.

    Note:
        None / omitted = "leave current value". This tool cannot CLEAR a field
        back to empty — that requires the BC3 UI. Same limitation as
        update_todo / update_project.
    """
    client = _get_basecamp_client(ctx)
    if not client:
        return _get_auth_error_response(ctx)

    try:
        entry = await _run_sync(
            lambda: client.update_schedule_entry(
                project_id, entry_id,
                summary=summary, description=description,
                starts_at=starts_at, ends_at=ends_at,
                participant_ids=participant_ids,
                all_day=all_day, notify=notify,
            )
        )
        return {
            "status": "success",
            "entry": entry,
            "message": f"Schedule entry {entry_id} updated successfully",
        }
    except Exception as e:
        logger.error(f"Error updating schedule entry {entry_id}: {e}")
        if "401" in str(e) and "expired" in str(e).lower():
            return {
                "error": "OAuth token expired",
                "message": "Your Basecamp OAuth token expired during the API call. Please re-authenticate by visiting http://localhost:8000 and completing the OAuth flow again."
            }
        return {
            "error": "Execution error",
            "message": str(e)
        }


# Inbox Tools (Email Forwards)
@mcp.tool()
async def get_inbox(ctx: Context, project_id: str) -> Dict[str, Any]:
    """Get the inbox for a project (for email forwards).

    Args:
        project_id: The project ID
    """
    client = _get_basecamp_client(ctx)
    if not client:
        return _get_auth_error_response(ctx)

    try:
        inbox = await _run_sync(client.get_inbox, project_id)
        return {
            "status": "success",
            "inbox": inbox
        }
    except Exception as e:
        logger.error(f"Error getting inbox: {e}")
        if "401" in str(e) and "expired" in str(e).lower():
            return {
                "error": "OAuth token expired",
                "message": "Your Basecamp OAuth token expired during the API call. Please re-authenticate by visiting http://localhost:8000 and completing the OAuth flow again."
            }
        return {
            "error": "Execution error",
            "message": str(e)
        }


@mcp.tool()
async def get_forwards(ctx: Context, project_id: str, inbox_id: Optional[str] = None) -> Dict[str, Any]:
    """Get all forwarded emails from a project's inbox.

    Args:
        project_id: The project ID
        inbox_id: Optional inbox ID. If not provided, will be auto-discovered from the project.
    """
    client = _get_basecamp_client(ctx)
    if not client:
        return _get_auth_error_response(ctx)

    try:
        forwards = await _run_sync(client.get_forwards, project_id, inbox_id)
        return {
            "status": "success",
            "forwards": forwards,
            "count": len(forwards)
        }
    except Exception as e:
        logger.error(f"Error getting forwards: {e}")
        if "401" in str(e) and "expired" in str(e).lower():
            return {
                "error": "OAuth token expired",
                "message": "Your Basecamp OAuth token expired during the API call. Please re-authenticate by visiting http://localhost:8000 and completing the OAuth flow again."
            }
        return {
            "error": "Execution error",
            "message": str(e)
        }


@mcp.tool()
async def get_forward(ctx: Context, project_id: str, forward_id: str) -> Dict[str, Any]:
    """Get a specific forwarded email by ID.

    Args:
        project_id: The project ID
        forward_id: The forward ID
    """
    client = _get_basecamp_client(ctx)
    if not client:
        return _get_auth_error_response(ctx)

    try:
        forward = await _run_sync(client.get_forward, project_id, forward_id)
        return {
            "status": "success",
            "forward": forward
        }
    except Exception as e:
        logger.error(f"Error getting forward: {e}")
        if "401" in str(e) and "expired" in str(e).lower():
            return {
                "error": "OAuth token expired",
                "message": "Your Basecamp OAuth token expired during the API call. Please re-authenticate by visiting http://localhost:8000 and completing the OAuth flow again."
            }
        return {
            "error": "Execution error",
            "message": str(e)
        }


@mcp.tool()
async def get_inbox_replies(ctx: Context, project_id: str, forward_id: str) -> Dict[str, Any]:
    """Get all replies to a forwarded email.

    Args:
        project_id: The project ID
        forward_id: The forward ID
    """
    client = _get_basecamp_client(ctx)
    if not client:
        return _get_auth_error_response(ctx)

    try:
        replies = await _run_sync(client.get_inbox_replies, project_id, forward_id)
        return {
            "status": "success",
            "replies": replies,
            "count": len(replies)
        }
    except Exception as e:
        logger.error(f"Error getting inbox replies: {e}")
        if "401" in str(e) and "expired" in str(e).lower():
            return {
                "error": "OAuth token expired",
                "message": "Your Basecamp OAuth token expired during the API call. Please re-authenticate by visiting http://localhost:8000 and completing the OAuth flow again."
            }
        return {
            "error": "Execution error",
            "message": str(e)
        }


@mcp.tool()
async def get_inbox_reply(ctx: Context, project_id: str, forward_id: str, reply_id: str) -> Dict[str, Any]:
    """Get a specific reply to a forwarded email.

    Args:
        project_id: The project ID
        forward_id: The forward ID
        reply_id: The reply ID
    """
    client = _get_basecamp_client(ctx)
    if not client:
        return _get_auth_error_response(ctx)

    try:
        reply = await _run_sync(client.get_inbox_reply, project_id, forward_id, reply_id)
        return {
            "status": "success",
            "reply": reply
        }
    except Exception as e:
        logger.error(f"Error getting inbox reply: {e}")
        if "401" in str(e) and "expired" in str(e).lower():
            return {
                "error": "OAuth token expired",
                "message": "Your Basecamp OAuth token expired during the API call. Please re-authenticate by visiting http://localhost:8000 and completing the OAuth flow again."
            }
        return {
            "error": "Execution error",
            "message": str(e)
        }


@mcp.tool()
async def trash_forward(ctx: Context, project_id: str, forward_id: str) -> Dict[str, Any]:
    """Move a forwarded email to trash.

    Args:
        project_id: The project ID
        forward_id: The forward ID
    """
    client = _get_basecamp_client(ctx)
    if not client:
        return _get_auth_error_response(ctx)

    try:
        await _run_sync(client.trash_forward, project_id, forward_id)
        return {
            "status": "success",
            "message": "Forward trashed"
        }
    except Exception as e:
        logger.error(f"Error trashing forward: {e}")
        if "401" in str(e) and "expired" in str(e).lower():
            return {
                "error": "OAuth token expired",
                "message": "Your Basecamp OAuth token expired during the API call. Please re-authenticate by visiting http://localhost:8000 and completing the OAuth flow again."
            }
        return {
            "error": "Execution error",
            "message": str(e)
        }


@mcp.tool()
async def get_card_tables(ctx: Context, project_id: str) -> Dict[str, Any]:
    """Get all card tables for a project.

    Args:
        project_id: The project ID
    """
    client = _get_basecamp_client(ctx)
    if not client:
        return _get_auth_error_response(ctx)
    
    try:
        card_tables = await _run_sync(client.get_card_tables, project_id)
        return {
            "status": "success",
            "card_tables": card_tables,
            "count": len(card_tables)
        }
    except Exception as e:
        logger.error(f"Error getting card tables: {e}")
        if "401" in str(e) and "expired" in str(e).lower():
            return {
                "error": "OAuth token expired",
                "message": "Your Basecamp OAuth token expired during the API call. Please re-authenticate by visiting http://localhost:8000 and completing the OAuth flow again."
            }
        return {
            "error": "Execution error",
            "message": str(e)
        }

@mcp.tool()
async def get_card_table(ctx: Context, project_id: str) -> Dict[str, Any]:
    """Get the card table details for a project.

    Args:
        project_id: The project ID
    """
    client = _get_basecamp_client(ctx)
    if not client:
        return _get_auth_error_response(ctx)
    
    try:
        card_table = await _run_sync(client.get_card_table, project_id)
        card_table_details = await _run_sync(client.get_card_table_details, project_id, card_table['id'])
        return {
            "status": "success",
            "card_table": card_table_details
        }
    except Exception as e:
        logger.error(f"Error getting card table: {e}")
        error_msg = str(e)
        if "401" in str(e) and "expired" in str(e).lower():
            return {
                "error": "OAuth token expired",
                "message": "Your Basecamp OAuth token expired during the API call. Please re-authenticate by visiting http://localhost:8000 and completing the OAuth flow again."
            }
        return {
            "status": "error",
            "message": f"Error getting card table: {error_msg}",
            "debug": error_msg
        }

@mcp.tool()
async def get_columns(ctx: Context, project_id: str, card_table_id: str) -> Dict[str, Any]:
    """Get all columns in a card table.

    Args:
        project_id: The project ID
        card_table_id: The card table ID
    """
    client = _get_basecamp_client(ctx)
    if not client:
        return _get_auth_error_response(ctx)
    
    try:
        columns = await _run_sync(client.get_columns, project_id, card_table_id)
        return {
            "status": "success",
            "columns": columns,
            "count": len(columns)
        }
    except Exception as e:
        logger.error(f"Error getting columns: {e}")
        if "401" in str(e) and "expired" in str(e).lower():
            return {
                "error": "OAuth token expired",
                "message": "Your Basecamp OAuth token expired during the API call. Please re-authenticate by visiting http://localhost:8000 and completing the OAuth flow again."
            }
        return {
            "error": "Execution error",
            "message": str(e)
        }

@mcp.tool()
async def get_cards(ctx: Context, project_id: str, column_id: str) -> Dict[str, Any]:
    """Get all cards in a column.

    Args:
        project_id: The project ID
        column_id: The column ID
    """
    client = _get_basecamp_client(ctx)
    if not client:
        return _get_auth_error_response(ctx)
    
    try:
        cards = await _run_sync(client.get_cards, project_id, column_id)
        return {
            "status": "success",
            "cards": cards,
            "count": len(cards)
        }
    except Exception as e:
        logger.error(f"Error getting cards: {e}")
        if "401" in str(e) and "expired" in str(e).lower():
            return {
                "error": "OAuth token expired",
                "message": "Your Basecamp OAuth token expired during the API call. Please re-authenticate by visiting http://localhost:8000 and completing the OAuth flow again."
            }
        return {
            "error": "Execution error",
            "message": str(e)
        }

@mcp.tool()
async def create_card(ctx: Context, project_id: str, column_id: str, title: str, content: Optional[str] = None, due_on: Optional[str] = None, notify: bool = False) -> Dict[str, Any]:
    """Create a new card in a column.

    Args:
        project_id: The project ID
        column_id: The column ID
        title: The card title
        content: Optional card content/description
        due_on: Optional due date (ISO 8601 format)
        notify: Whether to notify assignees (default: false)
    """
    client = _get_basecamp_client(ctx)
    if not client:
        return _get_auth_error_response(ctx)
    
    try:
        card = await _run_sync(client.create_card, project_id, column_id, title, content, due_on, notify)
        return {
            "status": "success",
            "card": card,
            "message": f"Card '{title}' created successfully"
        }
    except Exception as e:
        logger.error(f"Error creating card: {e}")
        if "401" in str(e) and "expired" in str(e).lower():
            return {
                "error": "OAuth token expired",
                "message": "Your Basecamp OAuth token expired during the API call. Please re-authenticate by visiting http://localhost:8000 and completing the OAuth flow again."
            }
        return {
            "error": "Execution error",
            "message": str(e)
        }

@mcp.tool()
async def get_column(ctx: Context, project_id: str, column_id: str) -> Dict[str, Any]:
    """Get details for a specific column.

    Args:
        project_id: The project ID
        column_id: The column ID
    """
    client = _get_basecamp_client(ctx)
    if not client:
        return _get_auth_error_response(ctx)
    
    try:
        column = await _run_sync(client.get_column, project_id, column_id)
        return {
            "status": "success",
            "column": column
        }
    except Exception as e:
        logger.error(f"Error getting column: {e}")
        if "401" in str(e) and "expired" in str(e).lower():
            return {
                "error": "OAuth token expired",
                "message": "Your Basecamp OAuth token expired during the API call. Please re-authenticate by visiting http://localhost:8000 and completing the OAuth flow again."
            }
        return {
            "error": "Execution error",
            "message": str(e)
        }

@mcp.tool()
async def create_column(ctx: Context, project_id: str, card_table_id: str, title: str) -> Dict[str, Any]:
    """Create a new column in a card table.

    Args:
        project_id: The project ID
        card_table_id: The card table ID
        title: The column title
    """
    client = _get_basecamp_client(ctx)
    if not client:
        return _get_auth_error_response(ctx)
    
    try:
        column = await _run_sync(client.create_column, project_id, card_table_id, title)
        return {
            "status": "success",
            "column": column,
            "message": f"Column '{title}' created successfully"
        }
    except Exception as e:
        logger.error(f"Error creating column: {e}")
        if "401" in str(e) and "expired" in str(e).lower():
            return {
                "error": "OAuth token expired",
                "message": "Your Basecamp OAuth token expired during the API call. Please re-authenticate by visiting http://localhost:8000 and completing the OAuth flow again."
            }
        return {
            "error": "Execution error",
            "message": str(e)
        }

@mcp.tool()
async def move_card(ctx: Context, project_id: str, card_id: str, column_id: str) -> Dict[str, Any]:
    """Move a card to a new column.

    Args:
        project_id: The project ID
        card_id: The card ID
        column_id: The destination column ID
    """
    client = _get_basecamp_client(ctx)
    if not client:
        return _get_auth_error_response(ctx)
    
    try:
        await _run_sync(client.move_card, project_id, card_id, column_id)
        return {
            "status": "success",
            "message": f"Card moved to column {column_id}"
        }
    except Exception as e:
        logger.error(f"Error moving card: {e}")
        if "401" in str(e) and "expired" in str(e).lower():
            return {
                "error": "OAuth token expired",
                "message": "Your Basecamp OAuth token expired during the API call. Please re-authenticate by visiting http://localhost:8000 and completing the OAuth flow again."
            }
        return {
            "error": "Execution error",
            "message": str(e)
        }

@mcp.tool()
async def complete_card(ctx: Context, project_id: str, card_id: str) -> Dict[str, Any]:
    """Mark a card as complete.

    Args:
        project_id: The project ID
        card_id: The card ID
    """
    client = _get_basecamp_client(ctx)
    if not client:
        return _get_auth_error_response(ctx)
    
    try:
        await _run_sync(client.complete_card, project_id, card_id)
        return {
            "status": "success",
            "message": "Card marked as complete"
        }
    except Exception as e:
        logger.error(f"Error completing card: {e}")
        if "401" in str(e) and "expired" in str(e).lower():
            return {
                "error": "OAuth token expired",
                "message": "Your Basecamp OAuth token expired during the API call. Please re-authenticate by visiting http://localhost:8000 and completing the OAuth flow again."
            }
        return {
            "error": "Execution error",
            "message": str(e)
        }

@mcp.tool()
async def get_card(ctx: Context, project_id: str, card_id: str) -> Dict[str, Any]:
    """Get details for a specific card.

    Args:
        project_id: The project ID
        card_id: The card ID
    """
    client = _get_basecamp_client(ctx)
    if not client:
        return _get_auth_error_response(ctx)
    
    try:
        card = await _run_sync(client.get_card, project_id, card_id)
        return {
            "status": "success",
            "card": card
        }
    except Exception as e:
        logger.error(f"Error getting card: {e}")
        if "401" in str(e) and "expired" in str(e).lower():
            return {
                "error": "OAuth token expired", 
                "message": "Your Basecamp OAuth token expired during the API call. Please re-authenticate by visiting http://localhost:8000 and completing the OAuth flow again."
            }
        return {
            "error": "Execution error",
            "message": str(e)
        }

@mcp.tool()
async def update_card(ctx: Context, project_id: str, card_id: str, title: Optional[str] = None, content: Optional[str] = None, due_on: Optional[str] = None, assignee_ids: Optional[List[str]] = None) -> Dict[str, Any]:
    """Update a card.

    Args:
        project_id: The project ID
        card_id: The card ID
        title: The new card title
        content: The new card content/description
        due_on: Due date (ISO 8601 format)
        assignee_ids: Array of person IDs to assign to the card
    """
    client = _get_basecamp_client(ctx)
    if not client:
        return _get_auth_error_response(ctx)
    
    try:
        card = await _run_sync(client.update_card, project_id, card_id, title, content, due_on, assignee_ids)
        return {
            "status": "success",
            "card": card,
            "message": "Card updated successfully"
        }
    except Exception as e:
        logger.error(f"Error updating card: {e}")
        if "401" in str(e) and "expired" in str(e).lower():
            return {
                "error": "OAuth token expired",
                "message": "Your Basecamp OAuth token expired during the API call. Please re-authenticate by visiting http://localhost:8000 and completing the OAuth flow again."
            }
        return {
            "error": "Execution error",
            "message": str(e)
        }

@mcp.tool()
async def get_daily_check_ins(ctx: Context, project_id: str, page: Optional[int] = None) -> Dict[str, Any]:
    """Get project's daily checking questionnaire.

    Args:
        project_id: The project ID
        page: Page number paginated response
    """
    client = _get_basecamp_client(ctx)
    if not client:
        return _get_auth_error_response(ctx)
    
    try:
        if page is not None and not isinstance(page, int):
            page = 1
        answers = await _run_sync(client.get_daily_check_ins, project_id, page=page or 1)
        return {
            "status": "success",
            "campfire_lines": answers,
            "count": len(answers)
        }
    except Exception as e:
        logger.error(f"Error getting daily check ins: {e}")
        if "401" in str(e) and "expired" in str(e).lower():
            return {
                "error": "OAuth token expired",
                "message": "Your Basecamp OAuth token expired during the API call. Please re-authenticate by visiting http://localhost:8000 and completing the OAuth flow again."
            }
        return {
            "error": "Execution error",
            "message": str(e)
        }

@mcp.tool()
async def get_question_answers(ctx: Context, project_id: str, question_id: str, page: Optional[int] = None) -> Dict[str, Any]:
    """Get answers on daily check-in question.

    Args:
        project_id: The project ID
        question_id: The question ID
        page: Page number paginated response
    """
    client = _get_basecamp_client(ctx)
    if not client:
        return _get_auth_error_response(ctx)
    
    try:
        if page is not None and not isinstance(page, int):
            page = 1
        answers = await _run_sync(client.get_question_answers, project_id, question_id, page=page or 1)
        return {
            "status": "success",
            "campfire_lines": answers,
            "count": len(answers)
        }
    except Exception as e:
        logger.error(f"Error getting question answers: {e}")
        if "401" in str(e) and "expired" in str(e).lower():
            return {
                "error": "OAuth token expired",
                "message": "Your Basecamp OAuth token expired during the API call. Please re-authenticate by visiting http://localhost:8000 and completing the OAuth flow again."
            }
        return {
            "error": "Execution error",
            "message": str(e)
        }

# Column Management Tools
@mcp.tool()
async def update_column(ctx: Context, project_id: str, column_id: str, title: str) -> Dict[str, Any]:
    """Update a column title.

    Args:
        project_id: The project ID
        column_id: The column ID
        title: The new column title
    """
    client = _get_basecamp_client(ctx)
    if not client:
        return _get_auth_error_response(ctx)
    
    try:
        column = await _run_sync(client.update_column, project_id, column_id, title)
        return {
            "status": "success",
            "column": column,
            "message": "Column updated successfully"
        }
    except Exception as e:
        logger.error(f"Error updating column: {e}")
        if "401" in str(e) and "expired" in str(e).lower():
            return {
                "error": "OAuth token expired",
                "message": "Your Basecamp OAuth token expired during the API call. Please re-authenticate by visiting http://localhost:8000 and completing the OAuth flow again."
            }
        return {
            "error": "Execution error",
            "message": str(e)
        }

@mcp.tool()
async def move_column(ctx: Context, project_id: str, card_table_id: str, column_id: str, position: int) -> Dict[str, Any]:
    """Move a column to a new position.

    Args:
        project_id: The project ID
        card_table_id: The card table ID
        column_id: The column ID
        position: The new 1-based position
    """
    client = _get_basecamp_client(ctx)
    if not client:
        return _get_auth_error_response(ctx)
    
    try:
        await _run_sync(client.move_column, project_id, column_id, position, card_table_id)
        return {
            "status": "success",
            "message": f"Column moved to position {position}"
        }
    except Exception as e:
        logger.error(f"Error moving column: {e}")
        if "401" in str(e) and "expired" in str(e).lower():
            return {
                "error": "OAuth token expired",
                "message": "Your Basecamp OAuth token expired during the API call. Please re-authenticate by visiting http://localhost:8000 and completing the OAuth flow again."
            }
        return {
            "error": "Execution error",
            "message": str(e)
        }

@mcp.tool()
async def update_column_color(ctx: Context, project_id: str, column_id: str, color: str) -> Dict[str, Any]:
    """Update a column color.

    Args:
        project_id: The project ID
        column_id: The column ID
        color: The hex color code (e.g., #FF0000)
    """
    client = _get_basecamp_client(ctx)
    if not client:
        return _get_auth_error_response(ctx)
    
    try:
        column = await _run_sync(client.update_column_color, project_id, column_id, color)
        return {
            "status": "success",
            "column": column,
            "message": f"Column color updated to {color}"
        }
    except Exception as e:
        logger.error(f"Error updating column color: {e}")
        if "401" in str(e) and "expired" in str(e).lower():
            return {
                "error": "OAuth token expired",
                "message": "Your Basecamp OAuth token expired during the API call. Please re-authenticate by visiting http://localhost:8000 and completing the OAuth flow again."
            }
        return {
            "error": "Execution error",
            "message": str(e)
        }

@mcp.tool()
async def put_column_on_hold(ctx: Context, project_id: str, column_id: str) -> Dict[str, Any]:
    """Put a column on hold (freeze work).

    Args:
        project_id: The project ID
        column_id: The column ID
    """
    client = _get_basecamp_client(ctx)
    if not client:
        return _get_auth_error_response(ctx)
    
    try:
        await _run_sync(client.put_column_on_hold, project_id, column_id)
        return {
            "status": "success",
            "message": "Column put on hold"
        }
    except Exception as e:
        logger.error(f"Error putting column on hold: {e}")
        if "401" in str(e) and "expired" in str(e).lower():
            return {
                "error": "OAuth token expired",
                "message": "Your Basecamp OAuth token expired during the API call. Please re-authenticate by visiting http://localhost:8000 and completing the OAuth flow again."
            }
        return {
            "error": "Execution error",
            "message": str(e)
        }

@mcp.tool()
async def remove_column_hold(ctx: Context, project_id: str, column_id: str) -> Dict[str, Any]:
    """Remove hold from a column (unfreeze work).

    Args:
        project_id: The project ID
        column_id: The column ID
    """
    client = _get_basecamp_client(ctx)
    if not client:
        return _get_auth_error_response(ctx)
    
    try:
        await _run_sync(client.remove_column_hold, project_id, column_id)
        return {
            "status": "success",
            "message": "Column hold removed"
        }
    except Exception as e:
        logger.error(f"Error removing column hold: {e}")
        if "401" in str(e) and "expired" in str(e).lower():
            return {
                "error": "OAuth token expired",
                "message": "Your Basecamp OAuth token expired during the API call. Please re-authenticate by visiting http://localhost:8000 and completing the OAuth flow again."
            }
        return {
            "error": "Execution error",
            "message": str(e)
        }

@mcp.tool()
async def watch_column(ctx: Context, project_id: str, column_id: str) -> Dict[str, Any]:
    """Subscribe to notifications for changes in a column.

    Args:
        project_id: The project ID
        column_id: The column ID
    """
    client = _get_basecamp_client(ctx)
    if not client:
        return _get_auth_error_response(ctx)
    
    try:
        await _run_sync(client.watch_column, project_id, column_id)
        return {
            "status": "success",
            "message": "Column notifications enabled"
        }
    except Exception as e:
        logger.error(f"Error watching column: {e}")
        if "401" in str(e) and "expired" in str(e).lower():
            return {
                "error": "OAuth token expired",
                "message": "Your Basecamp OAuth token expired during the API call. Please re-authenticate by visiting http://localhost:8000 and completing the OAuth flow again."
            }
        return {
            "error": "Execution error",
            "message": str(e)
        }

@mcp.tool()
async def unwatch_column(ctx: Context, project_id: str, column_id: str) -> Dict[str, Any]:
    """Unsubscribe from notifications for a column.

    Args:
        project_id: The project ID
        column_id: The column ID
    """
    client = _get_basecamp_client(ctx)
    if not client:
        return _get_auth_error_response(ctx)
    
    try:
        await _run_sync(client.unwatch_column, project_id, column_id)
        return {
            "status": "success",
            "message": "Column notifications disabled"
        }
    except Exception as e:
        logger.error(f"Error unwatching column: {e}")
        if "401" in str(e) and "expired" in str(e).lower():
            return {
                "error": "OAuth token expired",
                "message": "Your Basecamp OAuth token expired during the API call. Please re-authenticate by visiting http://localhost:8000 and completing the OAuth flow again."
            }
        return {
            "error": "Execution error",
            "message": str(e)
        }

# More Card Management Tools  
@mcp.tool()
async def uncomplete_card(ctx: Context, project_id: str, card_id: str) -> Dict[str, Any]:
    """Mark a card as incomplete.

    Args:
        project_id: The project ID
        card_id: The card ID
    """
    client = _get_basecamp_client(ctx)
    if not client:
        return _get_auth_error_response(ctx)
    
    try:
        await _run_sync(client.uncomplete_card, project_id, card_id)
        return {
            "status": "success",
            "message": "Card marked as incomplete"
        }
    except Exception as e:
        logger.error(f"Error uncompleting card: {e}")
        if "401" in str(e) and "expired" in str(e).lower():
            return {
                "error": "OAuth token expired",
                "message": "Your Basecamp OAuth token expired during the API call. Please re-authenticate by visiting http://localhost:8000 and completing the OAuth flow again."
            }
        return {
            "error": "Execution error",
            "message": str(e)
        }

# Card Steps (Sub-tasks) Management
@mcp.tool()
async def get_card_steps(ctx: Context, project_id: str, card_id: str) -> Dict[str, Any]:
    """Get all steps (sub-tasks) for a card.

    Args:
        project_id: The project ID
        card_id: The card ID
    """
    client = _get_basecamp_client(ctx)
    if not client:
        return _get_auth_error_response(ctx)
    
    try:
        steps = await _run_sync(client.get_card_steps, project_id, card_id)
        return {
            "status": "success",
            "steps": steps,
            "count": len(steps)
        }
    except Exception as e:
        logger.error(f"Error getting card steps: {e}")
        if "401" in str(e) and "expired" in str(e).lower():
            return {
                "error": "OAuth token expired",
                "message": "Your Basecamp OAuth token expired during the API call. Please re-authenticate by visiting http://localhost:8000 and completing the OAuth flow again."
            }
        return {
            "error": "Execution error",
            "message": str(e)
        }

@mcp.tool()
async def create_card_step(ctx: Context, project_id: str, card_id: str, title: str, due_on: Optional[str] = None, assignee_ids: Optional[List[str]] = None) -> Dict[str, Any]:
    """Create a new step (sub-task) for a card.

    Args:
        project_id: The project ID
        card_id: The card ID
        title: The step title
        due_on: Optional due date (ISO 8601 format)
        assignee_ids: Array of person IDs to assign to the step
    """
    client = _get_basecamp_client(ctx)
    if not client:
        return _get_auth_error_response(ctx)
    
    try:
        step = await _run_sync(client.create_card_step, project_id, card_id, title, due_on, assignee_ids)
        return {
            "status": "success",
            "step": step,
            "message": f"Step '{title}' created successfully"
        }
    except Exception as e:
        logger.error(f"Error creating card step: {e}")
        if "401" in str(e) and "expired" in str(e).lower():
            return {
                "error": "OAuth token expired",
                "message": "Your Basecamp OAuth token expired during the API call. Please re-authenticate by visiting http://localhost:8000 and completing the OAuth flow again."
            }
        return {
            "error": "Execution error",
            "message": str(e)
        }

@mcp.tool()
async def get_card_step(ctx: Context, project_id: str, step_id: str) -> Dict[str, Any]:
    """Get details for a specific card step.

    Args:
        project_id: The project ID
        step_id: The step ID
    """
    client = _get_basecamp_client(ctx)
    if not client:
        return _get_auth_error_response(ctx)
    
    try:
        step = await _run_sync(client.get_card_step, project_id, step_id)
        return {
            "status": "success",
            "step": step
        }
    except Exception as e:
        logger.error(f"Error getting card step: {e}")
        if "401" in str(e) and "expired" in str(e).lower():
            return {
                "error": "OAuth token expired",
                "message": "Your Basecamp OAuth token expired during the API call. Please re-authenticate by visiting http://localhost:8000 and completing the OAuth flow again."
            }
        return {
            "error": "Execution error",
            "message": str(e)
        }

@mcp.tool()
async def update_card_step(ctx: Context, project_id: str, step_id: str, title: Optional[str] = None, due_on: Optional[str] = None, assignee_ids: Optional[List[str]] = None) -> Dict[str, Any]:
    """Update a card step.

    Args:
        project_id: The project ID
        step_id: The step ID
        title: The step title
        due_on: Due date (ISO 8601 format)
        assignee_ids: Array of person IDs to assign to the step
    """
    client = _get_basecamp_client(ctx)
    if not client:
        return _get_auth_error_response(ctx)
    
    try:
        step = await _run_sync(client.update_card_step, project_id, step_id, title, due_on, assignee_ids)
        return {
            "status": "success",
            "step": step,
            "message": f"Step updated successfully"
        }
    except Exception as e:
        logger.error(f"Error updating card step: {e}")
        if "401" in str(e) and "expired" in str(e).lower():
            return {
                "error": "OAuth token expired",
                "message": "Your Basecamp OAuth token expired during the API call. Please re-authenticate by visiting http://localhost:8000 and completing the OAuth flow again."
            }
        return {
            "error": "Execution error",
            "message": str(e)
        }

@mcp.tool()
async def delete_card_step(ctx: Context, project_id: str, step_id: str) -> Dict[str, Any]:
    """Delete a card step.

    Args:
        project_id: The project ID
        step_id: The step ID
    """
    client = _get_basecamp_client(ctx)
    if not client:
        return _get_auth_error_response(ctx)
    
    try:
        await _run_sync(client.delete_card_step, project_id, step_id)
        return {
            "status": "success",
            "message": "Step deleted successfully"
        }
    except Exception as e:
        logger.error(f"Error deleting card step: {e}")
        if "401" in str(e) and "expired" in str(e).lower():
            return {
                "error": "OAuth token expired",
                "message": "Your Basecamp OAuth token expired during the API call. Please re-authenticate by visiting http://localhost:8000 and completing the OAuth flow again."
            }
        return {
            "error": "Execution error",
            "message": str(e)
        }

@mcp.tool()
async def complete_card_step(ctx: Context, project_id: str, step_id: str) -> Dict[str, Any]:
    """Mark a card step as complete.

    Args:
        project_id: The project ID
        step_id: The step ID
    """
    client = _get_basecamp_client(ctx)
    if not client:
        return _get_auth_error_response(ctx)
    
    try:
        await _run_sync(client.complete_card_step, project_id, step_id)
        return {
            "status": "success",
            "message": "Step marked as complete"
        }
    except Exception as e:
        logger.error(f"Error completing card step: {e}")
        if "401" in str(e) and "expired" in str(e).lower():
            return {
                "error": "OAuth token expired",
                "message": "Your Basecamp OAuth token expired during the API call. Please re-authenticate by visiting http://localhost:8000 and completing the OAuth flow again."
            }
        return {
            "error": "Execution error",
            "message": str(e)
        }

@mcp.tool()
async def uncomplete_card_step(ctx: Context, project_id: str, step_id: str) -> Dict[str, Any]:
    """Mark a card step as incomplete.

    Args:
        project_id: The project ID
        step_id: The step ID
    """
    client = _get_basecamp_client(ctx)
    if not client:
        return _get_auth_error_response(ctx)
    
    try:
        await _run_sync(client.uncomplete_card_step, project_id, step_id)
        return {
            "status": "success",
            "message": "Step marked as incomplete"
        }
    except Exception as e:
        logger.error(f"Error uncompleting card step: {e}")
        if "401" in str(e) and "expired" in str(e).lower():
            return {
                "error": "OAuth token expired",
                "message": "Your Basecamp OAuth token expired during the API call. Please re-authenticate by visiting http://localhost:8000 and completing the OAuth flow again."
            }
        return {
            "error": "Execution error",
            "message": str(e)
        }

# Attachments, Events, and Webhooks
@mcp.tool()
async def create_attachment(ctx: Context, file_path: str, name: str, content_type: Optional[str] = None) -> Dict[str, Any]:
    """Upload a file as an attachment.

    Args:
        file_path: Local path to file
        name: Filename for Basecamp
        content_type: MIME type
    """
    client = _get_basecamp_client(ctx)
    if not client:
        return _get_auth_error_response(ctx)
    
    try:
        result = await _run_sync(client.create_attachment, file_path, name, content_type or "application/octet-stream")
        return {
            "status": "success",
            "attachment": result
        }
    except Exception as e:
        logger.error(f"Error creating attachment: {e}")
        if "401" in str(e) and "expired" in str(e).lower():
            return {
                "error": "OAuth token expired",
                "message": "Your Basecamp OAuth token expired during the API call. Please re-authenticate by visiting http://localhost:8000 and completing the OAuth flow again."
            }
        return {
            "error": "Execution error",
            "message": str(e)
        }

@mcp.tool()
async def get_events(ctx: Context, project_id: str, recording_id: str) -> Dict[str, Any]:
    """Get events for a recording.

    Args:
        project_id: Project ID
        recording_id: Recording ID
    """
    client = _get_basecamp_client(ctx)
    if not client:
        return _get_auth_error_response(ctx)
    
    try:
        events = await _run_sync(client.get_events, project_id, recording_id)
        return {
            "status": "success",
            "events": events,
            "count": len(events)
        }
    except Exception as e:
        logger.error(f"Error getting events: {e}")
        if "401" in str(e) and "expired" in str(e).lower():
            return {
                "error": "OAuth token expired",
                "message": "Your Basecamp OAuth token expired during the API call. Please re-authenticate by visiting http://localhost:8000 and completing the OAuth flow again."
            }
        return {
            "error": "Execution error",
            "message": str(e)
        }

@mcp.tool()
async def get_webhooks(ctx: Context, project_id: str) -> Dict[str, Any]:
    """List webhooks for a project.

    Args:
        project_id: Project ID
    """
    client = _get_basecamp_client(ctx)
    if not client:
        return _get_auth_error_response(ctx)
    
    try:
        hooks = await _run_sync(client.get_webhooks, project_id)
        return {
            "status": "success",
            "webhooks": hooks,
            "count": len(hooks)
        }
    except Exception as e:
        logger.error(f"Error getting webhooks: {e}")
        if "401" in str(e) and "expired" in str(e).lower():
            return {
                "error": "OAuth token expired",
                "message": "Your Basecamp OAuth token expired during the API call. Please re-authenticate by visiting http://localhost:8000 and completing the OAuth flow again."
            }
        return {
            "error": "Execution error",
            "message": str(e)
        }

@mcp.tool()
async def create_webhook(ctx: Context, project_id: str, payload_url: str, types: Optional[List[str]] = None) -> Dict[str, Any]:
    """Create a webhook.

    Args:
        project_id: Project ID
        payload_url: Payload URL
        types: Event types
    """
    client = _get_basecamp_client(ctx)
    if not client:
        return _get_auth_error_response(ctx)
    
    try:
        hook = await _run_sync(client.create_webhook, project_id, payload_url, types)
        return {
            "status": "success",
            "webhook": hook
        }
    except Exception as e:
        logger.error(f"Error creating webhook: {e}")
        if "401" in str(e) and "expired" in str(e).lower():
            return {
                "error": "OAuth token expired",
                "message": "Your Basecamp OAuth token expired during the API call. Please re-authenticate by visiting http://localhost:8000 and completing the OAuth flow again."
            }
        return {
            "error": "Execution error",
            "message": str(e)
        }

@mcp.tool()
async def delete_webhook(ctx: Context, project_id: str, webhook_id: str) -> Dict[str, Any]:
    """Delete a webhook.

    Args:
        project_id: Project ID
        webhook_id: Webhook ID
    """
    client = _get_basecamp_client(ctx)
    if not client:
        return _get_auth_error_response(ctx)
    
    try:
        await _run_sync(client.delete_webhook, project_id, webhook_id)
        return {
            "status": "success",
            "message": "Webhook deleted"
        }
    except Exception as e:
        logger.error(f"Error deleting webhook: {e}")
        if "401" in str(e) and "expired" in str(e).lower():
            return {
                "error": "OAuth token expired",
                "message": "Your Basecamp OAuth token expired during the API call. Please re-authenticate by visiting http://localhost:8000 and completing the OAuth flow again."
            }
        return {
            "error": "Execution error",
            "message": str(e)
        }

# Document Management
@mcp.tool()
async def get_documents(ctx: Context, project_id: str, vault_id: str) -> Dict[str, Any]:
    """List documents in a vault.

    Args:
        project_id: Project ID
        vault_id: Vault ID
    """
    client = _get_basecamp_client(ctx)
    if not client:
        return _get_auth_error_response(ctx)
    
    try:
        docs = await _run_sync(client.get_documents, project_id, vault_id)
        return {
            "status": "success",
            "documents": docs,
            "count": len(docs)
        }
    except Exception as e:
        logger.error(f"Error getting documents: {e}")
        if "401" in str(e) and "expired" in str(e).lower():
            return {
                "error": "OAuth token expired",
                "message": "Your Basecamp OAuth token expired during the API call. Please re-authenticate by visiting http://localhost:8000 and completing the OAuth flow again."
            }
        return {
            "error": "Execution error",
            "message": str(e)
        }

@mcp.tool()
async def get_document(ctx: Context, project_id: str, document_id: str) -> Dict[str, Any]:
    """Get a single document.

    Args:
        project_id: Project ID
        document_id: Document ID
    """
    client = _get_basecamp_client(ctx)
    if not client:
        return _get_auth_error_response(ctx)
    
    try:
        doc = await _run_sync(client.get_document, project_id, document_id)
        return {
            "status": "success",
            "document": doc
        }
    except Exception as e:
        logger.error(f"Error getting document: {e}")
        if "401" in str(e) and "expired" in str(e).lower():
            return {
                "error": "OAuth token expired",
                "message": "Your Basecamp OAuth token expired during the API call. Please re-authenticate by visiting http://localhost:8000 and completing the OAuth flow again."
            }
        return {
            "error": "Execution error",
            "message": str(e)
        }

@mcp.tool()
async def create_document(ctx: Context, project_id: str, vault_id: str, title: str, content: str) -> Dict[str, Any]:
    """Create a document in a vault.

    Args:
        project_id: Project ID
        vault_id: Vault ID
        title: Document title
        content: Document HTML content
    """
    client = _get_basecamp_client(ctx)
    if not client:
        return _get_auth_error_response(ctx)
    
    try:
        doc = await _run_sync(client.create_document, project_id, vault_id, title, content)
        return {
            "status": "success",
            "document": doc
        }
    except Exception as e:
        logger.error(f"Error creating document: {e}")
        if "401" in str(e) and "expired" in str(e).lower():
            return {
                "error": "OAuth token expired",
                "message": "Your Basecamp OAuth token expired during the API call. Please re-authenticate by visiting http://localhost:8000 and completing the OAuth flow again."
            }
        return {
            "error": "Execution error",
            "message": str(e)
        }

@mcp.tool()
async def update_document(ctx: Context, project_id: str, document_id: str, title: Optional[str] = None, content: Optional[str] = None) -> Dict[str, Any]:
    """Update a document.

    Args:
        project_id: Project ID
        document_id: Document ID
        title: New title
        content: New HTML content
    """
    client = _get_basecamp_client(ctx)
    if not client:
        return _get_auth_error_response(ctx)
    
    try:
        doc = await _run_sync(client.update_document, project_id, document_id, title, content)
        return {
            "status": "success",
            "document": doc
        }
    except Exception as e:
        logger.error(f"Error updating document: {e}")
        if "401" in str(e) and "expired" in str(e).lower():
            return {
                "error": "OAuth token expired",
                "message": "Your Basecamp OAuth token expired during the API call. Please re-authenticate by visiting http://localhost:8000 and completing the OAuth flow again."
            }
        return {
            "error": "Execution error",
            "message": str(e)
        }

@mcp.tool()
async def trash_document(ctx: Context, project_id: str, document_id: str) -> Dict[str, Any]:
    """Move a document to trash.

    Args:
        project_id: Project ID
        document_id: Document ID
    """
    client = _get_basecamp_client(ctx)
    if not client:
        return _get_auth_error_response(ctx)
    
    try:
        await _run_sync(client.trash_document, project_id, document_id)
        return {
            "status": "success",
            "message": "Document trashed"
        }
    except Exception as e:
        logger.error(f"Error trashing document: {e}")
        if "401" in str(e) and "expired" in str(e).lower():
            return {
                "error": "OAuth token expired",
                "message": "Your Basecamp OAuth token expired during the API call. Please re-authenticate by visiting http://localhost:8000 and completing the OAuth flow again."
            }
        return {
            "error": "Execution error",
            "message": str(e)
        }

# Upload Management
@mcp.tool()
async def get_uploads(ctx: Context, project_id: str, vault_id: Optional[str] = None) -> Dict[str, Any]:
    """List uploads in a project or vault.

    Args:
        project_id: Project ID
        vault_id: Optional vault ID to limit to specific vault
    """
    client = _get_basecamp_client(ctx)
    if not client:
        return _get_auth_error_response(ctx)
    
    try:
        uploads = await _run_sync(client.get_uploads, project_id, vault_id)
        return {
            "status": "success",
            "uploads": uploads,
            "count": len(uploads)
        }
    except Exception as e:
        logger.error(f"Error getting uploads: {e}")
        if "401" in str(e) and "expired" in str(e).lower():
            return {
                "error": "OAuth token expired",
                "message": "Your Basecamp OAuth token expired during the API call. Please re-authenticate by visiting http://localhost:8000 and completing the OAuth flow again."
            }
        return {
            "error": "Execution error",
            "message": str(e)
        }

@mcp.tool()
async def get_upload(ctx: Context, project_id: str, upload_id: str) -> Dict[str, Any]:
    """Get details for a specific upload.

    Args:
        project_id: Project ID
        upload_id: Upload ID
    """
    client = _get_basecamp_client(ctx)
    if not client:
        return _get_auth_error_response(ctx)
    
    try:
        upload = await _run_sync(client.get_upload, project_id, upload_id)
        return {
            "status": "success",
            "upload": upload
        }
    except Exception as e:
        logger.error(f"Error getting upload: {e}")
        if "401" in str(e) and "expired" in str(e).lower():
            return {
                "error": "OAuth token expired",
                "message": "Your Basecamp OAuth token expired during the API call. Please re-authenticate by visiting http://localhost:8000 and completing the OAuth flow again."
            }
        return {
            "error": "Execution error",
            "message": str(e)
        }

@mcp.tool()
async def get_todolist(ctx: Context, project_id: str, todolist_id: str) -> Dict[str, Any]:
    """Get a specific todo list by ID.

    Args:
        project_id: The project ID
        todolist_id: The todo list ID
    """
    client = _get_basecamp_client(ctx)
    if not client:
        return _get_auth_error_response(ctx)

    try:
        todolist = await _run_sync(client.get_todolist, project_id, todolist_id)
        return {"status": "success", "todolist": todolist}
    except Exception as e:
        logger.error(f"Error getting todolist {todolist_id}: {e}")
        if "401" in str(e) and "expired" in str(e).lower():
            return {"error": "OAuth token expired", "message": "Your Basecamp OAuth token expired during the API call. Please re-authenticate by visiting http://localhost:8000 and completing the OAuth flow again."}
        return {"error": "Execution error", "message": str(e)}


@mcp.tool()
async def create_todolist(
    ctx: Context,
    project_id: str,
    name: str,
    description: Optional[str] = None,
) -> Dict[str, Any]:
    """Create a new todo list in a project.

    Args:
        project_id: The project ID
        name: Todo list name
        description: Optional HTML description
    """
    client = _get_basecamp_client(ctx)
    if not client:
        return _get_auth_error_response(ctx)

    try:
        todolist = await _run_sync(
            lambda: client.create_todolist(project_id, name, description)
        )
        return {"status": "success", "todolist": todolist}
    except Exception as e:
        logger.error(f"Error creating todolist: {e}")
        if "401" in str(e) and "expired" in str(e).lower():
            return {"error": "OAuth token expired", "message": "Your Basecamp OAuth token expired during the API call. Please re-authenticate by visiting http://localhost:8000 and completing the OAuth flow again."}
        return {"error": "Execution error", "message": str(e)}


@mcp.tool()
async def update_todolist(
    ctx: Context,
    project_id: str,
    todolist_id: str,
    name: str,
    description: Optional[str] = None,
) -> Dict[str, Any]:
    """Update an existing todo list.

    The Basecamp API requires the name even when only updating the description.

    Args:
        project_id: The project ID
        todolist_id: The todo list ID
        name: Todo list name (required)
        description: Optional HTML description
    """
    client = _get_basecamp_client(ctx)
    if not client:
        return _get_auth_error_response(ctx)

    try:
        todolist = await _run_sync(
            lambda: client.update_todolist(project_id, todolist_id, name, description)
        )
        return {"status": "success", "todolist": todolist}
    except Exception as e:
        logger.error(f"Error updating todolist {todolist_id}: {e}")
        if "401" in str(e) and "expired" in str(e).lower():
            return {"error": "OAuth token expired", "message": "Your Basecamp OAuth token expired during the API call. Please re-authenticate by visiting http://localhost:8000 and completing the OAuth flow again."}
        return {"error": "Execution error", "message": str(e)}


@mcp.tool()
async def trash_todolist(ctx: Context, project_id: str, todolist_id: str) -> Dict[str, Any]:
    """Move a todo list to the trash.

    Trashed lists can be recovered from the Basecamp web UI within 30 days.

    Args:
        project_id: The project ID
        todolist_id: The todo list ID
    """
    client = _get_basecamp_client(ctx)
    if not client:
        return _get_auth_error_response(ctx)

    try:
        await _run_sync(client.trash_todolist, project_id, todolist_id)
        return {"status": "success", "message": f"Todolist {todolist_id} moved to trash"}
    except Exception as e:
        logger.error(f"Error trashing todolist {todolist_id}: {e}")
        if "401" in str(e) and "expired" in str(e).lower():
            return {"error": "OAuth token expired", "message": "Your Basecamp OAuth token expired during the API call. Please re-authenticate by visiting http://localhost:8000 and completing the OAuth flow again."}
        return {"error": "Execution error", "message": str(e)}


@mcp.tool()
async def get_todolist_groups(ctx: Context, project_id: str, todolist_id: str) -> Dict[str, Any]:
    """Get all groups in a todo list.

    Groups are named sections within a todo list (e.g. "Phase 1", "Backlog").

    Args:
        project_id: The project ID
        todolist_id: The todo list ID
    """
    client = _get_basecamp_client(ctx)
    if not client:
        return _get_auth_error_response(ctx)

    try:
        groups = await _run_sync(client.get_todolist_groups, project_id, todolist_id)
        return {"status": "success", "groups": groups, "count": len(groups)}
    except Exception as e:
        logger.error(f"Error getting todolist groups: {e}")
        if "401" in str(e) and "expired" in str(e).lower():
            return {"error": "OAuth token expired", "message": "Your Basecamp OAuth token expired during the API call. Please re-authenticate by visiting http://localhost:8000 and completing the OAuth flow again."}
        return {"error": "Execution error", "message": str(e)}


@mcp.tool()
async def create_todolist_group(
    ctx: Context,
    project_id: str,
    todolist_id: str,
    name: str,
    color: Optional[str] = None,
) -> Dict[str, Any]:
    """Create a new group inside a todo list.

    Groups act as named sections to organise todos within a list.

    Args:
        project_id: The project ID
        todolist_id: The todo list ID
        name: Group name
        color: Optional color – one of: white, red, orange, yellow, green,
               blue, aqua, purple, gray, pink, brown
    """
    client = _get_basecamp_client(ctx)
    if not client:
        return _get_auth_error_response(ctx)

    try:
        group = await _run_sync(
            lambda: client.create_todolist_group(project_id, todolist_id, name, color)
        )
        return {"status": "success", "group": group}
    except Exception as e:
        logger.error(f"Error creating todolist group: {e}")
        if "401" in str(e) and "expired" in str(e).lower():
            return {"error": "OAuth token expired", "message": "Your Basecamp OAuth token expired during the API call. Please re-authenticate by visiting http://localhost:8000 and completing the OAuth flow again."}
        return {"error": "Execution error", "message": str(e)}


@mcp.tool()
async def reposition_todolist_group(
    ctx: Context, project_id: str, group_id: str, position: int
) -> Dict[str, Any]:
    """Reposition a todo list group to a new location within its list.

    Args:
        project_id: The project ID
        group_id: The group ID
        position: New 1-based position
    """
    client = _get_basecamp_client(ctx)
    if not client:
        return _get_auth_error_response(ctx)

    if position < 1:
        return {"error": "Invalid input", "message": "position must be >= 1"}

    try:
        await _run_sync(
            lambda: client.reposition_todolist_group(project_id, group_id, position)
        )
        return {"status": "success", "message": f"Group {group_id} repositioned to position {position}"}
    except Exception as e:
        logger.error(f"Error repositioning todolist group {group_id}: {e}")
        if "401" in str(e) and "expired" in str(e).lower():
            return {"error": "OAuth token expired", "message": "Your Basecamp OAuth token expired during the API call. Please re-authenticate by visiting http://localhost:8000 and completing the OAuth flow again."}
        return {"error": "Execution error", "message": str(e)}


# 🎉 COMPLETE FastMCP server with ALL tools migrated!

if __name__ == "__main__":
    args = parse_args()
    # Module-scope assignment: rebinds the module-level _transport_mode (this
    # block runs at module scope, not a function). _module_lifespan reads it at
    # startup. If this block is ever extracted into a main() function, add
    # `global _transport_mode` so the rebind still reaches the module global.
    _transport_mode = args.transport
    logger.info("Starting Basecamp FastMCP server (transport=%s)", args.transport)
    if args.transport == "streamable-http":
        mcp.settings.host = args.host
        mcp.settings.port = args.port
        mcp.run(transport="streamable-http")
    else:
        mcp.run(transport="stdio")
