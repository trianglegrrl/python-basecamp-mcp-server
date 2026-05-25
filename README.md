# Basecamp MCP Integration

> Fork of [georgeantonopoulos/Basecamp-MCP-Server](https://github.com/georgeantonopoulos/Basecamp-MCP-Server)
> maintained by [trianglegrrl](https://github.com/trianglegrrl). Adds a
> streamable-http transport for hosting, per-request bearer credentials,
> 18 additional Basecamp 3 tools (assignment-by-person, project setup,
> schedule, etc.), pagination on every list endpoint, and a live-test
> sandbox suite.

This project provides a **FastMCP-powered** integration for Basecamp 3, allowing AI clients to interact with Basecamp directly through the MCP protocol.

✅ **Migration Complete:** Successfully migrated to the official Anthropic FastMCP framework with **100% feature parity** with the upstream (75 tools at the migration milestone; expanded to **93 tools** in this fork's v1.1 surface).
🚀 **Ready for Production:** Full protocol compliance with MCP 2025-06-18

## What this fork adds (over the upstream)

| Area | Change |
|---|---|
| **Transport** | `--transport streamable-http` for hosting behind an HTTP proxy (per-request bearer auth). Upstream is stdio-only. |
| **Credentials** | `CredentialProvider` abstraction with `FileCredentialProvider` (stdio: reads `oauth_tokens.json`) and `HeaderCredentialProvider` (HTTP: reads `Authorization: Bearer …` + `X-Basecamp-Account-Id` per request). Hosting proxy handles OAuth refresh. |
| **Tool surface** | **75 → 93 tools (+18)** across 5 areas — see "v1.1 new tools" below. |
| **Pagination** | `Link`-header pagination on every list endpoint. Fixed `get_people` and `get_schedule_entries`, which previously silently truncated at 15 entries. |
| **`get_schedule` correctness** | The pre-existing `get_schedule` / `get_schedule_entries` client methods called BC3 endpoints that don't exist (`projects/{id}/schedule.json` and `buckets/{id}/schedules.json`); rewritten to use the correct dock-discovery + `/buckets/{p}/schedules/{id}/entries.json` shape. |
| **Live tests** | `tests/live/` sandbox suite gated by `pytest.mark.live` + `BASECAMP_TEST_REFRESH_TOKEN`. `make test-live`, `make test-live-cleanup` sweeper, GitHub Actions `workflow_dispatch` job. |
| **Token storage** | `BASECAMP_MCP_TOKEN_FILE` env-var override for ephemeral / containerized deploys (details in [Token Storage Location](#token-storage-location)). |
| **Tool signature** | Every `@mcp.tool()` now takes `ctx: Context` as its first parameter and resolves credentials via the active provider — no module-level singleton state (details in [Tool signature (post v1.1)](#tool-signature-post-v11)). |

## v1.1 new tools (the 18-tool port)

| Batch | Tools | Headline use case |
|---|---|---|
| **Project setup** | `create_project`, `update_project`, `trash_project`, `update_project_access` | "Spin up a new project for X with these tasks" |
| **Schedule** | `get_schedule`, `get_schedule_entries`, `get_schedule_entry`, `create_schedule_entry`, `update_schedule_entry` | Calendar event read + write (fetch-then-merge updates) |
| **People / profile** | `get_my_profile`, `get_people`, `get_project_people` | "Who's on this project?" / token-owner identification |
| **Assignment-by-person** | `get_my_assignments`, `get_my_due_assignments`, `get_my_completed_assignments`, `get_assignments_for_person` | "Show me Jill's tasks due this week" (the weekly-report workflow) |
| **Comment / message edit** | `update_comment`, `update_message` | Edit-after-publish workflow |

Date-scope vocabulary for `get_my_due_assignments` / `get_assignments_for_person`: `overdue`, `due_today`, `due_tomorrow`, `due_later_this_week`, `due_next_week`, `due_later` (Mon-start ISO weeks; disjoint). Full per-tool reference further down in [Available MCP Tools](#available-mcp-tools); the OAuth setup, per-client configuration, and troubleshooting from the upstream are preserved below.

## Quick Setup

This server works with **Cursor**, **Codex**, and **Claude Desktop**. Choose your preferred client:

### Prerequisites

- **Python 3.10+** (required for MCP SDK) — or use `uv` which auto-downloads the correct version
- A Basecamp 3 account
- A Basecamp OAuth application (create one at <https://launchpad.37signals.com/integrations>)

## For Cursor Users

### One-Command Setup

1. **Clone and set up with uv (recommended):**

   ```bash
   git clone <repository-url>
   cd Basecamp-MCP-Server

   # Using uv (recommended - auto-downloads Python 3.12)
   uv venv --python 3.12 venv
   source venv/bin/activate  # or venv\Scripts\activate on Windows
   uv pip install -r requirements.txt
   uv pip install mcp
   ```

   **Alternative: Using pip** (requires Python 3.10+ already installed):

   ```bash
   python setup.py
   ```

   The setup automatically:
   - ✅ Creates virtual environment
   - ✅ Installs all dependencies (FastMCP SDK, etc.)
   - ✅ Creates `.env` template file
   - ✅ Tests MCP server functionality

2. **Configure OAuth credentials:**
   Edit the generated `.env` file:

   ```bash
   BASECAMP_CLIENT_ID=your_client_id_here
   BASECAMP_CLIENT_SECRET=your_client_secret_here
   BASECAMP_ACCOUNT_ID=your_account_id_here
   USER_AGENT="Your App Name (your@email.com)"
   ```

3. **Authenticate with Basecamp:**

   ```bash
   python oauth_app.py
   ```

   Visit <http://localhost:8000> and complete the OAuth flow.

4. **Generate Cursor configuration:**

   ```bash
   python generate_cursor_config.py
   ```

5. **Restart Cursor completely** (quit and reopen, not just reload)

6. **Verify in Cursor:**
   - Go to Cursor Settings → MCP
   - You should see "basecamp" with a **green checkmark**
   - Available tools: **93 tools** for complete Basecamp control

### Test Your Setup

```bash
# Quick test the FastMCP server (works with both clients)
echo '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"test","version":"1.0"}}}
{"jsonrpc":"2.0","method":"notifications/initialized","params":{}}
{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}' | python basecamp_fastmcp.py

# Run automated tests  
python -m pytest tests/ -v
```

## For Codex Users

Codex integration is fully automated with a local path-agnostic script.
The script computes all paths from this repository root, so it works no matter where the repo is installed.

### Setup Steps

1. **Complete the basic setup** (same as Cursor steps 1-3 above):

   ```bash
   git clone <repository-url>
   cd Basecamp-MCP-Server
   python setup.py
   # Configure .env file with OAuth credentials
   python oauth_app.py
   ```

2. **Generate Codex configuration automatically:**

   ```bash
   python generate_codex_config.py
   ```

   Optional flags:

   ```bash
   # Preview commands only (no changes):
   python generate_codex_config.py --dry-run

   # Use legacy server instead of FastMCP:
   python generate_codex_config.py --legacy
   ```

3. **Verify in Codex:**

   ```bash
   codex mcp get basecamp
   codex mcp list
   ```

### Codex Configuration

The script writes to Codex global config:

- `~/.codex/config.toml`

It creates this MCP server entry shape:

```toml
[mcp_servers.basecamp]
command = "/path/to/your/project/venv/bin/python"
args = ["/path/to/your/project/basecamp_fastmcp.py"]

[mcp_servers.basecamp.env]
PYTHONPATH = "/path/to/your/project"
VIRTUAL_ENV = "/path/to/your/project/venv"
BASECAMP_ACCOUNT_ID = "your_account_id"
```

## For Claude Desktop Users

Based on the [official MCP quickstart guide](https://modelcontextprotocol.io/quickstart/server), Claude Desktop integration follows these steps:

### Setup Steps

1. **Complete the basic setup** (steps 1-3 from Cursor setup above):

   ```bash
   git clone <repository-url>
   cd Basecamp-MCP-Server
   python setup.py
   # Configure .env file with OAuth credentials
   python oauth_app.py
   ```

2. **Generate Claude Desktop configuration:**

   ```bash
   python generate_claude_desktop_config.py
   ```

3. **Restart Claude Desktop completely** (quit and reopen the application)

4. **Verify in Claude Desktop:**
   - Look for the "Search and tools" icon (🔍) in the chat interface
   - You should see "basecamp" listed with all 93 tools available
   - Toggle the tools on to enable Basecamp integration

### Claude Desktop Configuration

The configuration is automatically created at:

- **macOS**: `~/Library/Application Support/Claude/claude_desktop_config.json`
- **Windows**: `~/AppData/Roaming/Claude/claude_desktop_config.json`  
- **Linux**: `~/.config/claude-desktop/claude_desktop_config.json`

Example configuration generated:

```json
{
  "mcpServers": {
    "basecamp": {
      "command": "/path/to/your/project/venv/bin/python",
      "args": ["/path/to/your/project/basecamp_fastmcp.py"],
      "env": {
        "PYTHONPATH": "/path/to/your/project",
        "VIRTUAL_ENV": "/path/to/your/project/venv",
        "BASECAMP_ACCOUNT_ID": "your_account_id"
      }
    }
  }
}
```

### Usage in Claude Desktop

Ask Claude things like:

- "What are my current Basecamp projects?"
- "Show me the latest campfire messages from the Technology project"
- "Create a new card in the Development column with title 'Fix login bug'"
- "Get all todo items from the Marketing project"
- "Search for messages containing 'deadline'"

### Troubleshooting Claude Desktop

**Check Claude Desktop logs** (following [official debugging guide](https://modelcontextprotocol.io/quickstart/server#troubleshooting)):

```bash
# macOS/Linux - Monitor logs in real-time
tail -n 20 -f ~/Library/Logs/Claude/mcp*.log

# Check for specific errors
ls ~/Library/Logs/Claude/mcp-server-basecamp.log
```

**Common issues:**

- **Tools not appearing**: Verify configuration file syntax and restart Claude Desktop
- **Connection failures**: Check that Python path and script path are absolute paths
- **Authentication errors**: Ensure OAuth flow completed successfully (token file exists — see [Token Storage Location](#token-storage-location))

## Available MCP Tools

Once configured, you can use these tools in Cursor:

- `get_projects` - Get all Basecamp projects
- `get_project` - Get details for a specific project
- `create_project` - Create a new project (v1.1; free-plan accounts return 507)
- `update_project` - Update a project's name, description, admissions, or schedule_attributes (v1.1; fetch-then-merge — BC3 requires `name` even on partial updates; `None` means "preserve", cannot clear a field via this tool)
- `trash_project` - Soft-delete a project (v1.1; recoverable from BC3 UI for 30 days; warn the user before calling — blast radius is every project member)
- `update_project_access` - Grant/revoke project access by numeric person id, or create-and-grant by email (v1.1; **prerequisite** for assigning anyone to anything in a new project — BC3 silently no-ops assignments to non-members; numeric ids only — strings dropped)
- `get_todolists` - Get todo lists for a project
- `get_todolist` - Get a specific todo list by ID
- `create_todolist` - Create a new todo list in a project
- `update_todolist` - Update an existing todo list (name and/or description)
- `trash_todolist` - Move a todo list to the trash (recoverable within 30 days)
- `get_todos` - Get todos from a todo list (returns all pages; handles Basecamp pagination transparently)
- `get_todo` - Get a single todo item by its ID
- `create_todo` - Create a new todo item in a todo list (with assignees, due dates, descriptions)
- `update_todo` - Update an existing todo item (content, description, assignees, due date, etc.)
- `delete_todo` - Move a todo item to the trash (recoverable within 30 days)
- `complete_todo` - Mark a todo item as complete
- `uncomplete_todo` - Mark a todo item as incomplete
- `reposition_todo` - Reposition a todo within its list, or move it to another list or group
- `archive_todo` - Archive a todo item (hidden from active list, accessible via web UI)
- `search_basecamp` - Search across projects, todos, and messages
- `global_search` - Search projects, todos, and campfire messages across all projects
- `get_comments` - Get comments for a Basecamp item
- `create_comment` - Create a comment on a Basecamp item
- `update_comment` - Edit a comment's HTML content (v1.1; partial PUT — single-field on `content`, no fetch-then-merge needed for comments)
- `get_campfire_lines` - Get recent messages from a Basecamp campfire
- `get_message_board` - Get the message board for a project
- `get_messages` - Get all messages from a project's message board
- `get_message` - Get a specific message by ID
- `get_message_categories` - Get available message categories (types) for a project (e.g. Announcement, FYI, Heartbeat, Pitch, Question)
- `create_message` - Create a new message on a project's message board, with optional category
- `update_message` - Edit a message's subject, content, or category_id (v1.1; fetch-then-merge — BC3 requires `subject` on PUT; `None` means "preserve", cannot clear a field via this tool)
- `get_daily_check_ins` - Get project's daily check-in questions
- `get_question_answers` - Get answers to daily check-in questions
- `create_attachment` - Upload a file as an attachment
- `get_uploads` - List uploads in a project or vault
- `get_upload` - Get details for a specific upload
- `get_events` - Get events for a recording
- `get_webhooks` - List webhooks for a project
- `create_webhook` - Create a webhook
- `delete_webhook` - Delete a webhook
- `get_documents` - List documents in a vault
- `get_document` - Get a single document
- `create_document` - Create a document
- `update_document` - Update a document
- `trash_document` - Move a document to trash

### Todo List Group Tools

- `get_todolist_groups` - Get all groups in a todo list (named sections like "Phase 1", "Backlog")
- `create_todolist_group` - Create a new group inside a todo list (supports colors: white, red, orange, yellow, green, blue, aqua, purple, gray, pink, brown)
- `reposition_todolist_group` - Reposition a todo list group to a new location within its list

### Inbox Tools (Email Forwards)

- `get_inbox` - Get the inbox for a project (email forwards container)
- `get_forwards` - Get all forwarded emails from a project's inbox
- `get_forward` - Get a specific forwarded email by ID
- `get_inbox_replies` - Get all replies to a forwarded email
- `get_inbox_reply` - Get a specific reply to a forwarded email
- `trash_forward` - Move a forwarded email to trash

### Card Table Tools

- `get_card_tables` - Get all card tables for a project
- `get_card_table` - Get the card table details for a project
- `get_columns` - Get all columns in a card table
- `get_column` - Get details for a specific column
- `create_column` - Create a new column in a card table
- `update_column` - Update a column title
- `move_column` - Move a column to a new position
- `update_column_color` - Update a column color
- `put_column_on_hold` - Put a column on hold (freeze work)
- `remove_column_hold` - Remove hold from a column (unfreeze work)
- `watch_column` - Subscribe to notifications for changes in a column
- `unwatch_column` - Unsubscribe from notifications for a column
- `get_cards` - Get all cards in a column
- `get_card` - Get details for a specific card
- `create_card` - Create a new card in a column
- `update_card` - Update a card
- `move_card` - Move a card to a new column
- `complete_card` - Mark a card as complete
- `uncomplete_card` - Mark a card as incomplete
- `get_card_steps` - Get all steps (sub-tasks) for a card
- `create_card_step` - Create a new step (sub-task) for a card
- `get_card_step` - Get details for a specific card step
- `update_card_step` - Update a card step
- `delete_card_step` - Delete a card step
- `complete_card_step` - Mark a card step as complete
- `uncomplete_card_step` - Mark a card step as incomplete

### People + Profile Tools (v1.1)

- `get_my_profile` - Get the authenticated user's Person record (the OAuth token owner) — useful to confirm "the me in /my/* endpoints is who I think it is"
- `get_people` - List every person the authenticated user can see in the account (paginated via `Link` header)
- `get_project_people` - List every person with access to a specific project (paginated)

### Assignment-by-Person Tools (v1.1) — the weekly-report workflow

- `get_my_assignments` - Get the token owner's assignments (`{priorities, non_priorities}`)
- `get_my_due_assignments` - Get the token owner's assignments with a `due_on` date, optionally filtered by `scope`. BC3 handles scope server-side; the client validates scope before the HTTP call.
- `get_my_completed_assignments` - Get the token owner's completed assignments
- `get_assignments_for_person` - Find todos assigned to a specific person, by `person_name` (case-insensitive substring) or `person_id`, optionally filtered by `scope` and `bucket` (project id). Multi-step walk: resolves person via `get_people()` → falls back to scanning recording assignees → walks `/projects/recordings.json?type=Todo` (paginated, ~5-15s typical) → client-side filter by assignee → optional date-scope filter.

**Date-scope vocabulary** (for `get_my_due_assignments` and `get_assignments_for_person`):

| scope | matches `due_on` ... |
|---|---|
| `overdue` | before today |
| `due_today` | today |
| `due_tomorrow` | today + 1 |
| `due_later_this_week` | strictly after tomorrow, through Sunday of this week |
| `due_next_week` | Monday through Sunday of next week |
| `due_later` | strictly after next Sunday |

Mon-start ISO weeks. Scopes are disjoint. On a Sunday, `due_later_this_week` matches nothing — "this week" already ends today.

### Schedule (Calendar) Tools (v1.1)

- `get_schedule` - Get the project's schedule resource (dock-discovered)
- `get_schedule_entries` - Get all entries for a project's schedule (paginated)
- `get_schedule_entry` - Get a single entry by id
- `create_schedule_entry` - Create a schedule entry (`summary`, `starts_at`, `ends_at`, optional `description`, `participant_ids`, `all_day`, `notify`)
- `update_schedule_entry` - Update an entry (fetch-then-merge whitelist `[summary, description, starts_at, ends_at, participant_ids, all_day, notify]`; `None` means "preserve")

### Example Cursor Usage

Ask Cursor things like:

- "Show me all my Basecamp projects"
- "What todos are in project X?"
- "Create a new todo 'Review PR' in the Sprint Backlog list"
- "Mark the 'Deploy v2' todo as complete"
- "Show me the messages from the message board in project X"
- "What message categories are available in project X?"
- "Post a new Announcement to the message board in project X: 'We shipped v2.0!'"
- "Create a Heartbeat message in project X with a weekly progress update"
- "Search for messages containing 'deadline'"
- "Get details for the Technology project"
- "Show me the card table for project X"
- "Create a new card in the 'In Progress' column"
- "Move this card to the 'Done' column"
- "Update the color of the 'Urgent' column to red"
- "Mark card as complete"
- "Show me all steps for this card"
- "Create a sub-task for this card"
- "Mark this card step as complete"

## Architecture

The project uses the **official Anthropic FastMCP framework** for maximum reliability and compatibility:

1. **FastMCP Server** (`basecamp_fastmcp.py`) - Official MCP SDK with 93 tools, compatible with Cursor, Codex, Claude Desktop (stdio) and any MCP-aware HTTP proxy (streamable-http)
2. **OAuth App** (`oauth_app.py`) - Handles OAuth 2.0 flow with Basecamp  
3. **Token Storage** (`token_storage.py`) - Securely stores OAuth tokens
4. **Basecamp Client** (`basecamp_client.py`) - Basecamp API client library
5. **Search Utilities** (`search_utils.py`) - Search across Basecamp resources
6. **Setup Automation** (`setup.py`) - One-command installation
7. **Configuration Generators**:
   - `generate_cursor_config.py` - For Cursor IDE integration
   - `generate_codex_config.py` - For Codex CLI integration
   - `generate_claude_desktop_config.py` - For Claude Desktop integration

## Tool signature (post v1.1)

Every `@mcp.tool()` accepts a FastMCP `Context` as its first parameter and
resolves credentials via the active `CredentialProvider`:

```python
from mcp.server.fastmcp import Context

@mcp.tool()
async def get_projects(ctx: Context) -> Dict[str, Any]:
    client = _get_basecamp_client(ctx)
    if not client:
        return _get_auth_error_response(ctx)
    ...
```

The provider is selected at process startup by the `--transport` flag:

- `--transport stdio` (default, OSS path) → `FileCredentialProvider` reads
  `oauth_tokens.json` + env vars. Use `python oauth_app.py` for the one-time
  browser dance.
- `--transport streamable-http` (hosted path) → `HeaderCredentialProvider`
  reads `Authorization: Bearer <token>` and `X-Basecamp-Account-Id` from each
  incoming request. The hosting proxy handles OAuth refresh.

See `auth/README.md` for the provider abstraction; `scripts/smoke_streamable_http.py`
for the HTTP handshake.

## Troubleshooting

### Common Issues (Both Clients)

- 🔴 **Red/Yellow indicator:** Run `python setup.py` to create proper virtual environment
- 🔴 **"0 tools available":** Virtual environment missing MCP packages - run setup script
- 🔴 **"Tool not found" errors:** Restart your client (Cursor/Codex/Claude Desktop) completely
- ⚠️ **Missing BASECAMP_ACCOUNT_ID:** Add to `.env` file, then re-run the config generator

### Quick Fixes

**Problem: Server won't start**

```bash
# Test if FastMCP server works:
./venv/bin/python -c "import mcp; print('✅ MCP available')"
# If this fails, run: python setup.py
```

**Problem: Wrong Python version**

```bash
python --version  # Must be 3.10+
# If too old, use uv which auto-downloads the correct Python:
uv venv --python 3.12 venv && source venv/bin/activate && uv pip install -r requirements.txt && uv pip install mcp
```

**Problem: Authentication fails**

```bash  
# Check OAuth flow:
python oauth_app.py
# Visit http://localhost:8000 and complete login
```

### Manual Configuration (Last Resort)

**Cursor config location:** `~/.cursor/mcp.json` (macOS/Linux) or `%APPDATA%\Cursor\mcp.json` (Windows)  
**Codex config location:** `~/.codex/config.toml`  
**Claude Desktop config location:** `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS)

```json
{
    "mcpServers": {
        "basecamp": {
            "command": "/full/path/to/your/project/venv/bin/python",
            "args": ["/full/path/to/your/project/basecamp_fastmcp.py"],
            "cwd": "/full/path/to/your/project",
            "env": {
                "PYTHONPATH": "/full/path/to/your/project",
                "VIRTUAL_ENV": "/full/path/to/your/project/venv",
                "BASECAMP_ACCOUNT_ID": "your_account_id"
            }
        }
    }
}
```

Codex equivalent:

```toml
[mcp_servers.basecamp]
command = "/full/path/to/your/project/venv/bin/python"
args = ["/full/path/to/your/project/basecamp_fastmcp.py"]

[mcp_servers.basecamp.env]
PYTHONPATH = "/full/path/to/your/project"
VIRTUAL_ENV = "/full/path/to/your/project/venv"
BASECAMP_ACCOUNT_ID = "your_account_id"
```

## Finding Your Account ID

If you don't know your Basecamp account ID:

1. Log into Basecamp in your browser
2. Look at the URL - it will be like `https://3.basecamp.com/4389629/projects`
3. The number (4389629 in this example) is your account ID

## Security Notes

- Keep your `.env` file secure and never commit it to version control
- The OAuth tokens are stored locally in `oauth_tokens.json` (600 permissions, alongside `token_storage.py`)
- This setup is designed for local development use

## Token Storage Location

By default the OAuth token file lives next to `token_storage.py` (`<project>/oauth_tokens.json`). For containerized or server deployments where the project directory is read-only or ephemeral, set the `BASECAMP_MCP_TOKEN_FILE` environment variable to an absolute path:

```bash
export BASECAMP_MCP_TOKEN_FILE=/var/lib/basecamp-mcp/oauth_tokens.json
```

The path is resolved at import time; both the OAuth app and the MCP server honor the same variable, so they stay in sync without symlinks or file copies. When unset, behavior is unchanged.

`token_storage.py` also attempts to `chmod` the token file to `0o600` on write (best-effort; skipped on platforms that do not support it, such as Windows). If you point `BASECAMP_MCP_TOKEN_FILE` at a shared or mounted location, make sure the parent directory permissions are appropriate too, since only the token file itself is chmod'd.

## License

This project is licensed under the MIT License.

## Star History

<a href="https://www.star-history.com/#georgeantonopoulos/Basecamp-MCP-Server&Date">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/svg?repos=georgeantonopoulos/Basecamp-MCP-Server&type=Date&theme=dark" />
    <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/svg?repos=georgeantonopoulos/Basecamp-MCP-Server&type=Date" />
    <img alt="Star History Chart" src="https://api.star-history.com/svg?repos=georgeantonopoulos/Basecamp-MCP-Server&type=Date" />
  </picture>
</a>
