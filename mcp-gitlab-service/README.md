# GitLab MCP Server

An MCP (Model Contextt Protocol) server for GitLab operations, implementing the JSON-RPC 2.0 protocol for use with kagent.
It talks to the GitLab REST API v4 and works with both gitlab.com and self-managed GitLab instances.

## Features

This MCP server exposes the following GitLab operations as tools (✏️ = write tool, disabled in read-only mode):

### Users
- **get-current-user** - Get the user that owns the configured access token
- **list-users** - Search users by name, username or email

### Projects & Groups
- **list-projects** - List projects visible to the user (search, owned, starred, visibility, ...)
- **get-project** - Get detailed information about a project
- **create-project** ✏️ - Create a new project
- **fork-project** ✏️ - Fork a project into a namespace
- **list-project-members** - List members of a project (optionally including inherited members)
- **list-groups** - List groups visible to the user
- **get-group** - Get detailed information about a group
- **list-group-projects** - List projects in a group (optionally including subgroups)

### Repository
- **get-repository-tree** - List files and directories in a repository path
- **get-file-contents** - Read a file from a repository
- **create-or-update-file** ✏️ - Create or update a single file (one commit)
- **delete-file** ✏️ - Delete a file
- **create-commit** ✏️ - Create a commit with multiple file actions (create/update/delete/move/chmod)
- **list-commits** - List commits (filter by branch, path, author, date)
- **get-commit** - Get details of a commit
- **get-commit-diff** - Get the diff of a commit
- **compare-refs** - Compare two branches/tags/commits
- **list-branches** / **get-branch** - List branches / get a branch
- **create-branch** ✏️ / **delete-branch** ✏️ - Create / delete a branch
- **list-tags** - List tags
- **create-tag** ✏️ - Create a tag

### Merge Requests
- **list-merge-requests** - List merge requests in a project, group, or globally
- **get-merge-request** - Get merge request details
- **get-merge-request-diffs** - Get the file changes of a merge request
- **create-merge-request** ✏️ - Create a merge request (supports draft, reviewers, labels)
- **update-merge-request** ✏️ - Update title/description/labels/assignees, close or reopen
- **merge-merge-request** ✏️ - Merge a merge request (supports squash, merge when pipeline succeeds)
- **approve-merge-request** ✏️ - Approve a merge request
- **list-merge-request-notes** - List comments on a merge request
- **add-merge-request-note** ✏️ - Comment on a merge request

### Issues
- **list-issues** - List issues in a project, group, or globally
- **get-issue** - Get issue details
- **create-issue** ✏️ - Create an issue
- **update-issue** ✏️ - Update title/description/labels/assignees, close or reopen
- **list-issue-notes** - List comments on an issue
- **add-issue-note** ✏️ - Comment on an issue
- **list-labels** - List project labels
- **list-milestones** - List project milestones

### CI/CD
- **list-pipelines** - List pipelines (filter by status, ref, source, user)
- **get-pipeline** - Get pipeline details
- **create-pipeline** ✏️ - Trigger a pipeline on a branch or tag (with variables)
- **retry-pipeline** ✏️ / **cancel-pipeline** ✏️ - Retry / cancel a pipeline
- **list-pipeline-jobs** - List jobs of a pipeline
- **get-job** - Get job details
- **get-job-log** - Get a job's log output (tail N lines) - useful for debugging failures
- **retry-job** ✏️ / **cancel-job** ✏️ / **play-job** ✏️ - Retry / cancel / trigger a manual job

### Search
- **search** - Search globally, in a group, or in a project (projects, issues, merge_requests, commits, blobs/code, ...)

## Setup

### Prerequisites

- Python 3.10+
- A GitLab instance (gitlab.com or self-managed)
- A GitLab access token (personal, group or project access token)
  - Scope `api` for full access, or `read_api` for read-only usage

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

- `GITLAB_URL` - Base URL of the GitLab instance, without `/api/v4` (default: `https://gitlab.com`)
- `GITLAB_TOKEN` - GitLab access token, sent as `PRIVATE-TOKEN` header
  - Without a token only public resources are accessible
- `GITLAB_VERIFY_SSL` - Verify TLS certificates (default: `true`)
- `GITLAB_CA_BUNDLE` - Path to a CA bundle file for self-signed / internal CAs (optional)
- `GITLAB_TIMEOUT` - HTTP request timeout in seconds (default: `30`)
- `GITLAB_READ_ONLY` - When `true`, all write tools are hidden from `tools/list` and rejected on call (default: `false`)
- `MCP_HOST` - Host to bind to (default: `0.0.0.0`)
- `MCP_PORT` - Port to listen on (default: `8000`)

## Running Locally

```bash
export GITLAB_URL="https://gitlab.example.com"
export GITLAB_TOKEN="glpat-xxxxxxxxxxxxxxxxxxxx"
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

### Call a Tool (Example: list-merge-requests)

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
      "name":"list-merge-requests",
      "arguments":{
        "project_id":"my-group/my-project",
        "state":"opened"
      }
    }
  }'
```

### Call a Tool (Example: get-job-log)

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
      "name":"get-job-log",
      "arguments":{
        "project_id":"my-group/my-project",
        "job_id":123456,
        "tail_lines":100
      }
    }
  }'
```

## Docker

Build the Docker image:
```bash
docker build -t mcp-gitlab-service .
```

Run the container:
```bash
docker run -p 8000:8000 \
  -e GITLAB_URL="https://gitlab.example.com" \
  -e GITLAB_TOKEN="glpat-xxxxxxxxxxxxxxxxxxxx" \
  mcp-gitlab-service
```

## API Documentation

Once the service is running, visit:
- Swagger UI: `http://localhost:8000/docs` (every tool has its own route under `/tools/<tool-name>`, grouped by area)
- ReDoc: `http://localhost:8000/redoc`

## Health Check

```bash
curl http://localhost:8000/health
```

The health check calls GitLab's `/api/v4/version` with the configured token, so it verifies both connectivity and authentication.

## Notes

- `project_id` / `group_id` accept either a numeric ID or a full path (e.g. `my-group/my-project`); paths are URL-encoded automatically
- Merge requests and issues are addressed by their **IID** (the `!12` / `#34` number shown in the GitLab UI)
- List tools return compact summaries plus `pagination` info (`page`, `per_page`, `total`, `next_page`); use the `get-*` tools for full details
- GitLab API errors (404, 403, ...) are returned as tool results with `isError: true` so the agent can read and react to them
- Each session maintains its own GitLab HTTP client
- Sessions are stored in memory (for production, consider using Redis)
- The service implements the MCP protocol version 2024-11-05
- Store `GITLAB_TOKEN` in a Kubernetes secret rather than in `config.py`
