# Confluence MCP Server

An MCP (Model Context Protocol) server for Confluence operations, implementing the JSON-RPC 2.0 protocol for use with kagent.
It uses the Confluence REST API v1 and works with both **Confluence Server / Data Center** and **Confluence Cloud**.

## Features

This MCP server exposes the following Confluence operations as tools (✏️ = write tool, disabled in read-only mode):

### Users & Spaces
- **get-current-user** - Get the user the configured credentials belong to
- **list-spaces** - List spaces (filter by type/status)
- **get-space** - Get space details, including its homepage ID

### Search
- **search-content** - Search pages/blog posts by text, optionally within a space, by label, or by title only
- **cql-search** - Search with a raw CQL query (e.g. `space = DEV and lastmodified > now("-7d")`)

### Pages
- **list-pages** - List pages (or blog posts) in a space
- **get-page** - Read a page by ID - body returned as clean **text** (default), `storage` XHTML or rendered `view` HTML
- **get-page-by-title** - Read a page by exact title within a space
- **get-page-children** - List direct child pages
- **get-page-descendants** - List the whole page tree below a page
- **get-page-history** - Creator, creation date, last update and contributors
- **create-page** ✏️ - Create a page or blog post (optionally under a parent page)
- **update-page** ✏️ - Update title/body or move a page (version is incremented automatically)
- **delete-page** ✏️ - Delete a page (moves it to the trash)

### Comments & Labels
- **list-page-comments** - List page comments (as text)
- **add-page-comment** ✏️ - Comment on a page
- **list-page-labels** - List page labels
- **add-page-labels** ✏️ / **remove-page-label** ✏️ - Add / remove labels

### Attachments
- **list-attachments** - List files attached to a page
- **get-attachment-content** - Download a text attachment (txt, csv, json, yaml, xml, md, ...) and return its content
- **upload-attachment** ✏️ - Upload a text file (creates a new version if it already exists)

## Setup

### Prerequisites

- Python 3.10+
- A Confluence instance (Server/Data Center or Cloud)
- Credentials:
  - **Server/Data Center**: a Personal Access Token (Profile → Settings → Personal Access Tokens)
  - **Cloud**: your account email + an API token (https://id.atlassian.com/manage-profile/security/api-tokens)

### Installation

1. Create a virtual environment:
```bash
python3 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

2. Install dependencies:
```bash
pip install -r requirements.txt
```

## Configuration

The service can be configured via environment variables (they take precedence over `config.py`):

- `CONFLUENCE_URL` - **Required.** Base URL of Confluence, without `/rest/api`
  - Server/Data Center: `https://confluence.example.com` (include the context path if there is one, e.g. `https://example.com/confluence`)
  - Cloud: `https://your-domain.atlassian.net/wiki`
- `CONFLUENCE_TOKEN` - Personal Access Token (Server/DC) or API token (Cloud)
- `CONFLUENCE_USERNAME` - Only for Cloud (your email) or Server/DC basic auth. When set, `username:token` basic auth is used; otherwise the token is sent as a Bearer token
- `CONFLUENCE_VERIFY_SSL` - Verify TLS certificates (default: `true`)
- `CONFLUENCE_CA_BUNDLE` - Path to a CA bundle file for self-signed / internal CAs (optional)
- `CONFLUENCE_TIMEOUT` - HTTP request timeout in seconds (default: `30`)
- `CONFLUENCE_READ_ONLY` - When `true`, all write tools are hidden from `tools/list` and rejected on call (default: `false`)
- `MCP_HOST` - Host to bind to (default: `0.0.0.0`)
- `MCP_PORT` - Port to listen on (default: `8000`)

## Running Locally

```bash
# Server / Data Center
export CONFLUENCE_URL="https://confluence.example.com"
export CONFLUENCE_TOKEN="<personal access token>"

# Cloud
# export CONFLUENCE_URL="https://your-domain.atlassian.net/wiki"
# export CONFLUENCE_USERNAME="you@example.com"
# export CONFLUENCE_TOKEN="<api token>"

export CONFLUENCE_READ_ONLY=true
python -m service
```

Or using uvicorn directly:
```bash
uvicorn service:app --host 0.0.0.0 --port 8000
```

The service will be available at:
- API: `http://localhost:8000/mcp`
- Swagger UI: `http://localhost:8000/docs`
- ReDoc: `http://localhost:8000/redoc`

## Usage with kagent

### Initialize Session

```bash
MCP_URL="http://localhost:8000/mcp"

SID=$(curl -sS -D - "$MCP_URL" \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -o /dev/null \
  -d '{
    "jsonrpc":"2.0",
    "id":1,
    "method":"initialize",
    "params":{
      "protocolVersion":"2024-11-05",
      "clientInfo":{"name":"curl-test","version":"0.0.1"},
      "capabilities":{}
    }
  }' | tr -d '\r' | awk -F': ' 'tolower($1)=="mcp-session-id"{print $2}')

echo "Session ID: $SID"
```

### List Available Tools

```bash
curl -sS "$MCP_URL" \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -H "MCP-Session-Id: $SID" \
  -d '{
    "jsonrpc":"2.0",
    "id":2,
    "method":"tools/list"
  }'
```

### Call a Tool (Example: search-content)

```bash
curl -sS "$MCP_URL" \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -H "MCP-Session-Id: $SID" \
  -d '{
    "jsonrpc":"2.0",
    "id":3,
    "method":"tools/call",
    "params":{
      "name":"search-content",
      "arguments":{
        "query":"deployment guide",
        "space_key":"DEV"
      }
    }
  }'
```

### Call a Tool (Example: get-page)

```bash
curl -sS "$MCP_URL" \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -H "MCP-Session-Id: $SID" \
  -d '{
    "jsonrpc":"2.0",
    "id":4,
    "method":"tools/call",
    "params":{
      "name":"get-page",
      "arguments":{
        "page_id":"123456"
      }
    }
  }'
```

## Docker

Build the Docker image:
```bash
docker build -t mcp-confluence-service .
```

Run the container:
```bash
docker run -p 8000:8000 \
  -e CONFLUENCE_URL="https://confluence.example.com" \
  -e CONFLUENCE_TOKEN="<personal access token>" \
  mcp-confluence-service
```

## API Documentation

Once the service is running, visit:
- Swagger UI: `http://localhost:8000/docs` (every tool has its own route under `/tools/<tool-name>`, grouped by area)
- ReDoc: `http://localhost:8000/redoc`

## Health Check

```bash
curl http://localhost:8000/health
```

The health check calls `/rest/api/user/current` with the configured credentials. It reports unhealthy if Confluence
is unreachable or if a token is configured but Confluence treats the request as anonymous (credentials rejected).

## Notes

- **Page ID**: the number in the page URL (`.../pages/123456/Title` or `?pageId=123456`). If you only know the title, use `get-page-by-title` or `search-content`
- `get-page` returns clean text by default (headings as `#`, lists as `-`, tables as `| a | b |`, code blocks preserved) - ideal for LLM context and memory. Use `format: "storage"` before editing a page to keep its formatting
- `update-page` without `body` keeps the current content (e.g. rename or move only)
- `create-page`/`update-page`/`add-page-comment` accept `representation`: `storage` (Confluence XHTML), `wiki` (wiki markup) or `plain` (plain text)
- List tools return compact summaries plus `pagination` (`start`, `limit`, `next_start`); pass `next_start` as `start` for the next page
- Confluence API errors (404, 403, ...) are returned as tool results with `isError: true` so the agent can read and react to them
- Each session maintains its own Confluence HTTP client
- Sessions are stored in memory (for production, consider using Redis)
- The service implements the MCP protocol version 2024-11-05
- Store `CONFLUENCE_TOKEN` in a Kubernetes secret rather than in `config.py`
