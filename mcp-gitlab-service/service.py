from __future__ import annotations

import base64
import json
import logging
import uuid
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import quote

import requests
from fastapi import FastAPI, HTTPException, Header, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel

from config import get_bool, get_int, get_str

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

app = FastAPI(
    title="GitLab MCP Server",
    description="MCP server for GitLab operations",
    version="1.0.0",
)

# Session storage (in production, use Redis or similar)
sessions: Dict[str, Dict[str, Any]] = {}

# GitLab HTTP clients cache per session
gitlab_clients: Dict[str, requests.Session] = {}


# ---------------------------------------------------------------------------
# GitLab client helpers
# ---------------------------------------------------------------------------

def _gitlab_api_base() -> str:
    """Return the GitLab REST API v4 base URL."""
    base_url = (get_str("GITLAB_URL", "https://gitlab.com") or "https://gitlab.com").rstrip("/")
    return f"{base_url}/api/v4"


def _gitlab_verify() -> Any:
    """Return the `verify` argument for requests (CA bundle path or bool)."""
    ca_bundle = get_str("GITLAB_CA_BUNDLE")
    if ca_bundle:
        return ca_bundle
    return get_bool("GITLAB_VERIFY_SSL", True)


def _create_gitlab_client() -> requests.Session:
    """Create and return an authenticated GitLab HTTP session."""
    token = get_str("GITLAB_TOKEN")
    logger.info("Creating GitLab client for %s", _gitlab_api_base())

    client = requests.Session()
    client.headers.update({
        "Accept": "application/json",
        "User-Agent": "gitlab-mcp-server/1.0.0",
    })
    if token:
        client.headers["PRIVATE-TOKEN"] = token
    else:
        logger.warning("GITLAB_TOKEN is not set - only public resources will be accessible")
    client.verify = _gitlab_verify()
    return client


def _get_gitlab_client(session_id: str) -> requests.Session:
    """Get or create GitLab client for a session."""
    if session_id not in gitlab_clients:
        gitlab_clients[session_id] = _create_gitlab_client()
        logger.info(f"Created GitLab client for session {session_id}")
    return gitlab_clients[session_id]


def _enc(value: Any) -> str:
    """URL-encode a path segment (project path, file path, branch name, ...)."""
    return quote(str(value), safe="")


def _clean_params(params: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Drop empty query params and convert bools/lists to GitLab's format."""
    cleaned: Dict[str, Any] = {}
    for key, value in (params or {}).items():
        if value is None or value == "":
            continue
        if isinstance(value, bool):
            value = "true" if value else "false"
        elif isinstance(value, (list, tuple)):
            value = ",".join(str(v) for v in value)
        cleaned[key] = value
    return cleaned


def _clean_body(body: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Drop None values from a JSON request body."""
    return {k: v for k, v in (body or {}).items() if v is not None}


def _error_message(resp: requests.Response) -> str:
    """Extract a readable error message from a GitLab error response."""
    try:
        data = resp.json()
    except ValueError:
        return resp.text[:500] or resp.reason
    if isinstance(data, dict):
        message = data.get("message") or data.get("error_description") or data.get("error") or data
        return message if isinstance(message, str) else json.dumps(message)
    return str(data)


def _gitlab_request(
    session_id: str,
    method: str,
    path: str,
    params: Optional[Dict[str, Any]] = None,
    body: Optional[Dict[str, Any]] = None,
    raw: bool = False,
) -> Tuple[Any, Any]:
    """Call the GitLab API and return (data, response headers)."""
    client = _get_gitlab_client(session_id)
    url = f"{_gitlab_api_base()}{path}"
    try:
        resp = client.request(
            method,
            url,
            params=_clean_params(params),
            json=_clean_body(body) if body is not None else None,
            timeout=get_int("GITLAB_TIMEOUT", 30),
        )
    except requests.RequestException as e:
        logger.error(f"GitLab request failed: {method} {url}: {e}")
        raise HTTPException(status_code=502, detail=f"Failed to reach GitLab: {str(e)}")

    if resp.status_code >= 400:
        raise HTTPException(
            status_code=resp.status_code,
            detail=f"GitLab API error {resp.status_code}: {_error_message(resp)}",
        )
    if raw:
        return resp.text, resp.headers
    if resp.status_code == 204 or not resp.content:
        return None, resp.headers
    return resp.json(), resp.headers


def _page_params(arguments: Dict[str, Any]) -> Dict[str, int]:
    """Build pagination query params from tool arguments."""
    per_page = int(arguments.get("per_page") or 20)
    page = int(arguments.get("page") or 1)
    return {"per_page": max(1, min(per_page, 100)), "page": max(1, page)}


def _pagination(headers: Any) -> Dict[str, Optional[int]]:
    """Extract pagination info from GitLab response headers."""
    def _header_int(name: str) -> Optional[int]:
        value = headers.get(name)
        return int(value) if value and value.isdigit() else None

    return {
        "page": _header_int("X-Page"),
        "per_page": _header_int("X-Per-Page"),
        "total": _header_int("X-Total"),
        "total_pages": _header_int("X-Total-Pages"),
        "next_page": _header_int("X-Next-Page"),
    }


def _list_result(
    key: str,
    items: List[Dict[str, Any]],
    headers: Any,
    summarize: Optional[Callable[[Dict[str, Any]], Dict[str, Any]]] = None,
) -> str:
    """Format a paginated list response."""
    if summarize:
        items = [summarize(item) for item in items]
    return json.dumps({key: items, "count": len(items), "pagination": _pagination(headers)}, indent=2)


def _default_branch(session_id: str, project_id: str) -> str:
    """Look up the default branch of a project."""
    project, _ = _gitlab_request(session_id, "GET", f"/projects/{project_id}")
    return project.get("default_branch") or "main"


# ---------------------------------------------------------------------------
# Response summarizers (keep list output compact for LLM context)
# ---------------------------------------------------------------------------

def _pick(obj: Dict[str, Any], *keys: str) -> Dict[str, Any]:
    return {k: obj.get(k) for k in keys if k in obj}


def _username(user: Any) -> Optional[str]:
    return user.get("username") if isinstance(user, dict) else None


def _summarize_project(p: Dict[str, Any]) -> Dict[str, Any]:
    return _pick(p, "id", "name", "path_with_namespace", "description", "visibility",
                 "default_branch", "archived", "last_activity_at", "web_url")


def _summarize_group(g: Dict[str, Any]) -> Dict[str, Any]:
    return _pick(g, "id", "name", "full_path", "description", "visibility", "parent_id", "web_url")


def _summarize_user(u: Dict[str, Any]) -> Dict[str, Any]:
    return _pick(u, "id", "username", "name", "state", "access_level", "expires_at", "web_url")


def _summarize_branch(b: Dict[str, Any]) -> Dict[str, Any]:
    summary = _pick(b, "name", "merged", "protected", "default", "web_url")
    summary["commit"] = _pick(b.get("commit") or {}, "short_id", "title", "author_name", "committed_date")
    return summary


def _summarize_tag(t: Dict[str, Any]) -> Dict[str, Any]:
    summary = _pick(t, "name", "message", "protected")
    summary["commit"] = _pick(t.get("commit") or {}, "short_id", "title", "committed_date")
    return summary


def _summarize_commit(c: Dict[str, Any]) -> Dict[str, Any]:
    return _pick(c, "id", "short_id", "title", "author_name", "author_email", "authored_date", "web_url")


def _summarize_diff(d: Dict[str, Any]) -> Dict[str, Any]:
    return _pick(d, "old_path", "new_path", "new_file", "renamed_file", "deleted_file", "diff")


def _summarize_merge_request(mr: Dict[str, Any]) -> Dict[str, Any]:
    summary = _pick(mr, "id", "iid", "project_id", "title", "state", "draft", "source_branch",
                    "target_branch", "detailed_merge_status", "labels", "created_at",
                    "updated_at", "merged_at", "web_url")
    summary["author"] = _username(mr.get("author"))
    summary["assignees"] = [_username(a) for a in mr.get("assignees") or []]
    summary["reviewers"] = [_username(r) for r in mr.get("reviewers") or []]
    return summary


def _summarize_issue(i: Dict[str, Any]) -> Dict[str, Any]:
    summary = _pick(i, "id", "iid", "project_id", "title", "state", "labels", "due_date",
                    "created_at", "updated_at", "closed_at", "web_url")
    summary["author"] = _username(i.get("author"))
    summary["assignees"] = [_username(a) for a in i.get("assignees") or []]
    summary["milestone"] = (i.get("milestone") or {}).get("title")
    return summary


def _summarize_note(n: Dict[str, Any]) -> Dict[str, Any]:
    summary = _pick(n, "id", "body", "system", "resolvable", "resolved", "created_at", "updated_at")
    summary["author"] = _username(n.get("author"))
    return summary


def _summarize_pipeline(p: Dict[str, Any]) -> Dict[str, Any]:
    return _pick(p, "id", "iid", "project_id", "status", "source", "ref", "sha",
                 "created_at", "updated_at", "web_url")


def _summarize_job(j: Dict[str, Any]) -> Dict[str, Any]:
    summary = _pick(j, "id", "name", "stage", "status", "ref", "allow_failure", "failure_reason",
                    "created_at", "started_at", "finished_at", "duration", "web_url")
    summary["pipeline_id"] = (j.get("pipeline") or {}).get("id")
    return summary


def _summarize_label(label: Dict[str, Any]) -> Dict[str, Any]:
    return _pick(label, "id", "name", "color", "description", "open_issues_count",
                 "open_merge_requests_count")


def _summarize_milestone(m: Dict[str, Any]) -> Dict[str, Any]:
    return _pick(m, "id", "iid", "title", "state", "description", "start_date", "due_date", "web_url")


def _summarize_blob(b: Dict[str, Any]) -> Dict[str, Any]:
    return _pick(b, "project_id", "path", "filename", "ref", "startline", "data")


SEARCH_SUMMARIZERS: Dict[str, Callable[[Dict[str, Any]], Dict[str, Any]]] = {
    "projects": _summarize_project,
    "issues": _summarize_issue,
    "merge_requests": _summarize_merge_request,
    "milestones": _summarize_milestone,
    "users": _summarize_user,
    "commits": _summarize_commit,
    "blobs": _summarize_blob,
    "wiki_blobs": _summarize_blob,
    "notes": _summarize_note,
}


# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------

_PROJECT_ID = {"type": "string", "description": "Project ID or full path (e.g. 'my-group/my-project')"}
_GROUP_ID = {"type": "string", "description": "Group ID or full path (e.g. 'my-group/sub-group')"}
_MR_IID = {"type": "integer", "description": "Merge request IID (the !number shown in the UI)"}
_ISSUE_IID = {"type": "integer", "description": "Issue IID (the #number shown in the UI)"}
_PIPELINE_ID = {"type": "integer", "description": "Pipeline ID"}
_JOB_ID = {"type": "integer", "description": "Job ID"}
_LABELS = {"type": "string", "description": "Comma-separated label names"}
_USER_IDS = {"type": "array", "items": {"type": "integer"}, "description": "List of user IDs"}
_PAGINATION = {
    "per_page": {"type": "integer", "description": "Results per page (max 100)", "default": 20},
    "page": {"type": "integer", "description": "Page number", "default": 1},
}

TOOLS: List[Dict[str, Any]] = [
    # --- Users ---
    {
        "name": "get-current-user",
        "description": "Get the GitLab user that owns the configured access token",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "list-users",
        "description": "Search GitLab users by name, username or email",
        "inputSchema": {
            "type": "object",
            "properties": {
                "search": {"type": "string", "description": "Search term (name, username or email)"},
                "username": {"type": "string", "description": "Exact username"},
                "active": {"type": "boolean", "description": "Only active users"},
                **_PAGINATION,
            },
        },
    },
    # --- Projects ---
    {
        "name": "list-projects",
        "description": "List GitLab projects visible to the user, with optional search and filters",
        "inputSchema": {
            "type": "object",
            "properties": {
                "search": {"type": "string", "description": "Search projects by name"},
                "membership": {"type": "boolean", "description": "Only projects the user is a member of", "default": True},
                "owned": {"type": "boolean", "description": "Only projects owned by the user"},
                "starred": {"type": "boolean", "description": "Only projects starred by the user"},
                "archived": {"type": "boolean", "description": "Filter by archived status"},
                "visibility": {"type": "string", "enum": ["public", "internal", "private"], "description": "Filter by visibility"},
                "order_by": {"type": "string", "enum": ["id", "name", "path", "created_at", "updated_at", "last_activity_at"], "description": "Order by field", "default": "last_activity_at"},
                **_PAGINATION,
            },
        },
    },
    {
        "name": "get-project",
        "description": "Get detailed information about a project",
        "inputSchema": {
            "type": "object",
            "properties": {"project_id": _PROJECT_ID},
            "required": ["project_id"],
        },
    },
    {
        "name": "create-project",
        "description": "Create a new GitLab project",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Project name"},
                "path": {"type": "string", "description": "Project path/slug (defaults from name)"},
                "namespace_id": {"type": "integer", "description": "Group/namespace ID to create the project in (default: user namespace)"},
                "description": {"type": "string", "description": "Project description"},
                "visibility": {"type": "string", "enum": ["public", "internal", "private"], "description": "Visibility level", "default": "private"},
                "initialize_with_readme": {"type": "boolean", "description": "Create an initial README commit", "default": True},
                "default_branch": {"type": "string", "description": "Default branch name"},
            },
            "required": ["name"],
        },
    },
    {
        "name": "fork-project",
        "description": "Fork a project into a namespace",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_id": _PROJECT_ID,
                "namespace_path": {"type": "string", "description": "Target namespace path (default: user namespace)"},
                "name": {"type": "string", "description": "Name of the fork"},
                "path": {"type": "string", "description": "Path of the fork"},
            },
            "required": ["project_id"],
        },
    },
    {
        "name": "list-project-members",
        "description": "List members of a project",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_id": _PROJECT_ID,
                "query": {"type": "string", "description": "Filter by name or username"},
                "include_inherited": {"type": "boolean", "description": "Include members inherited from parent groups", "default": True},
                **_PAGINATION,
            },
            "required": ["project_id"],
        },
    },
    # --- Groups ---
    {
        "name": "list-groups",
        "description": "List GitLab groups visible to the user",
        "inputSchema": {
            "type": "object",
            "properties": {
                "search": {"type": "string", "description": "Search groups by name or path"},
                "owned": {"type": "boolean", "description": "Only groups owned by the user"},
                "top_level_only": {"type": "boolean", "description": "Only top-level groups"},
                **_PAGINATION,
            },
        },
    },
    {
        "name": "get-group",
        "description": "Get detailed information about a group",
        "inputSchema": {
            "type": "object",
            "properties": {"group_id": _GROUP_ID},
            "required": ["group_id"],
        },
    },
    {
        "name": "list-group-projects",
        "description": "List projects in a group",
        "inputSchema": {
            "type": "object",
            "properties": {
                "group_id": _GROUP_ID,
                "search": {"type": "string", "description": "Search projects by name"},
                "include_subgroups": {"type": "boolean", "description": "Include projects from subgroups", "default": False},
                "archived": {"type": "boolean", "description": "Filter by archived status"},
                **_PAGINATION,
            },
            "required": ["group_id"],
        },
    },
    # --- Repository: files & tree ---
    {
        "name": "get-repository-tree",
        "description": "List files and directories in a repository path",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_id": _PROJECT_ID,
                "path": {"type": "string", "description": "Directory path inside the repository (default: root)"},
                "ref": {"type": "string", "description": "Branch, tag or commit SHA (default: default branch)"},
                "recursive": {"type": "boolean", "description": "List recursively", "default": False},
                **_PAGINATION,
            },
            "required": ["project_id"],
        },
    },
    {
        "name": "get-file-contents",
        "description": "Read the contents of a file in a repository",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_id": _PROJECT_ID,
                "file_path": {"type": "string", "description": "Path of the file inside the repository (e.g. 'src/main.py')"},
                "ref": {"type": "string", "description": "Branch, tag or commit SHA (default: default branch)"},
            },
            "required": ["project_id", "file_path"],
        },
    },
    {
        "name": "create-or-update-file",
        "description": "Create a new file or update an existing file in a repository (single-file commit)",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_id": _PROJECT_ID,
                "file_path": {"type": "string", "description": "Path of the file inside the repository"},
                "branch": {"type": "string", "description": "Branch to commit to"},
                "content": {"type": "string", "description": "Full new file content"},
                "commit_message": {"type": "string", "description": "Commit message"},
                "start_branch": {"type": "string", "description": "Create 'branch' from this branch if it does not exist"},
                "encoding": {"type": "string", "enum": ["text", "base64"], "description": "Content encoding", "default": "text"},
                "last_commit_id": {"type": "string", "description": "Last known file commit ID (optimistic locking, update only)"},
            },
            "required": ["project_id", "file_path", "branch", "content", "commit_message"],
        },
    },
    {
        "name": "delete-file",
        "description": "Delete a file from a repository",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_id": _PROJECT_ID,
                "file_path": {"type": "string", "description": "Path of the file inside the repository"},
                "branch": {"type": "string", "description": "Branch to commit to"},
                "commit_message": {"type": "string", "description": "Commit message"},
            },
            "required": ["project_id", "file_path", "branch", "commit_message"],
        },
    },
    {
        "name": "create-commit",
        "description": "Create a commit with multiple file actions (create, update, delete, move, chmod)",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_id": _PROJECT_ID,
                "branch": {"type": "string", "description": "Branch to commit to"},
                "commit_message": {"type": "string", "description": "Commit message"},
                "start_branch": {"type": "string", "description": "Create 'branch' from this branch if it does not exist"},
                "actions": {
                    "type": "array",
                    "description": "File actions to include in the commit",
                    "items": {
                        "type": "object",
                        "properties": {
                            "action": {"type": "string", "enum": ["create", "update", "delete", "move", "chmod"]},
                            "file_path": {"type": "string", "description": "Path of the file"},
                            "previous_path": {"type": "string", "description": "Original path (move only)"},
                            "content": {"type": "string", "description": "File content (create/update/move)"},
                            "encoding": {"type": "string", "enum": ["text", "base64"]},
                            "execute_filemode": {"type": "boolean", "description": "Executable flag (chmod only)"},
                        },
                        "required": ["action", "file_path"],
                    },
                },
            },
            "required": ["project_id", "branch", "commit_message", "actions"],
        },
    },
    # --- Repository: commits ---
    {
        "name": "list-commits",
        "description": "List commits in a repository, optionally filtered by branch, path, author or date",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_id": _PROJECT_ID,
                "ref_name": {"type": "string", "description": "Branch, tag or revision range (default: default branch)"},
                "path": {"type": "string", "description": "Only commits touching this file path"},
                "author": {"type": "string", "description": "Filter by commit author"},
                "since": {"type": "string", "description": "Only commits after this date (ISO 8601)"},
                "until": {"type": "string", "description": "Only commits before this date (ISO 8601)"},
                **_PAGINATION,
            },
            "required": ["project_id"],
        },
    },
    {
        "name": "get-commit",
        "description": "Get details of a single commit",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_id": _PROJECT_ID,
                "sha": {"type": "string", "description": "Commit SHA, branch or tag name"},
            },
            "required": ["project_id", "sha"],
        },
    },
    {
        "name": "get-commit-diff",
        "description": "Get the diff of a single commit",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_id": _PROJECT_ID,
                "sha": {"type": "string", "description": "Commit SHA"},
                **_PAGINATION,
            },
            "required": ["project_id", "sha"],
        },
    },
    {
        "name": "compare-refs",
        "description": "Compare two branches, tags or commits and return commits and diffs between them",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_id": _PROJECT_ID,
                "from_ref": {"type": "string", "description": "Base branch, tag or commit SHA"},
                "to_ref": {"type": "string", "description": "Head branch, tag or commit SHA"},
                "straight": {"type": "boolean", "description": "Direct comparison (from..to) instead of merge-base (from...to)", "default": False},
            },
            "required": ["project_id", "from_ref", "to_ref"],
        },
    },
    # --- Repository: branches & tags ---
    {
        "name": "list-branches",
        "description": "List branches in a repository",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_id": _PROJECT_ID,
                "search": {"type": "string", "description": "Filter branches by name"},
                **_PAGINATION,
            },
            "required": ["project_id"],
        },
    },
    {
        "name": "get-branch",
        "description": "Get details of a single branch",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_id": _PROJECT_ID,
                "branch": {"type": "string", "description": "Branch name"},
            },
            "required": ["project_id", "branch"],
        },
    },
    {
        "name": "create-branch",
        "description": "Create a new branch from a ref",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_id": _PROJECT_ID,
                "branch": {"type": "string", "description": "New branch name"},
                "ref": {"type": "string", "description": "Branch, tag or commit SHA to branch from (default: default branch)"},
            },
            "required": ["project_id", "branch"],
        },
    },
    {
        "name": "delete-branch",
        "description": "Delete a branch",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_id": _PROJECT_ID,
                "branch": {"type": "string", "description": "Branch name"},
            },
            "required": ["project_id", "branch"],
        },
    },
    {
        "name": "list-tags",
        "description": "List tags in a repository",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_id": _PROJECT_ID,
                "search": {"type": "string", "description": "Filter tags by name"},
                **_PAGINATION,
            },
            "required": ["project_id"],
        },
    },
    {
        "name": "create-tag",
        "description": "Create a new tag",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_id": _PROJECT_ID,
                "tag_name": {"type": "string", "description": "Tag name"},
                "ref": {"type": "string", "description": "Branch, tag or commit SHA to tag"},
                "message": {"type": "string", "description": "Annotation message (creates an annotated tag)"},
            },
            "required": ["project_id", "tag_name", "ref"],
        },
    },
    # --- Merge requests ---
    {
        "name": "list-merge-requests",
        "description": "List merge requests in a project, a group, or across GitLab (when no project_id/group_id is given)",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_id": _PROJECT_ID,
                "group_id": _GROUP_ID,
                "state": {"type": "string", "enum": ["opened", "closed", "locked", "merged", "all"], "description": "Filter by state", "default": "opened"},
                "scope": {"type": "string", "enum": ["created_by_me", "assigned_to_me", "all"], "description": "Scope (global default: created_by_me)"},
                "source_branch": {"type": "string", "description": "Filter by source branch"},
                "target_branch": {"type": "string", "description": "Filter by target branch"},
                "author_username": {"type": "string", "description": "Filter by author username"},
                "reviewer_username": {"type": "string", "description": "Filter by reviewer username"},
                "labels": _LABELS,
                "search": {"type": "string", "description": "Search in title and description"},
                **_PAGINATION,
            },
        },
    },
    {
        "name": "get-merge-request",
        "description": "Get details of a merge request",
        "inputSchema": {
            "type": "object",
            "properties": {"project_id": _PROJECT_ID, "merge_request_iid": _MR_IID},
            "required": ["project_id", "merge_request_iid"],
        },
    },
    {
        "name": "get-merge-request-diffs",
        "description": "Get the file changes (diffs) of a merge request",
        "inputSchema": {
            "type": "object",
            "properties": {"project_id": _PROJECT_ID, "merge_request_iid": _MR_IID, **_PAGINATION},
            "required": ["project_id", "merge_request_iid"],
        },
    },
    {
        "name": "create-merge-request",
        "description": "Create a new merge request",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_id": _PROJECT_ID,
                "source_branch": {"type": "string", "description": "Source branch"},
                "target_branch": {"type": "string", "description": "Target branch (default: default branch)"},
                "title": {"type": "string", "description": "Merge request title"},
                "description": {"type": "string", "description": "Merge request description (Markdown)"},
                "assignee_ids": _USER_IDS,
                "reviewer_ids": _USER_IDS,
                "labels": _LABELS,
                "milestone_id": {"type": "integer", "description": "Milestone ID"},
                "remove_source_branch": {"type": "boolean", "description": "Delete source branch after merge"},
                "squash": {"type": "boolean", "description": "Squash commits on merge"},
                "draft": {"type": "boolean", "description": "Mark as draft", "default": False},
            },
            "required": ["project_id", "source_branch", "title"],
        },
    },
    {
        "name": "update-merge-request",
        "description": "Update a merge request (title, description, labels, assignees, close/reopen, ...)",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_id": _PROJECT_ID,
                "merge_request_iid": _MR_IID,
                "title": {"type": "string", "description": "New title"},
                "description": {"type": "string", "description": "New description"},
                "target_branch": {"type": "string", "description": "New target branch"},
                "state_event": {"type": "string", "enum": ["close", "reopen"], "description": "Close or reopen"},
                "labels": {"type": "string", "description": "Comma-separated labels (replaces all labels)"},
                "add_labels": {"type": "string", "description": "Comma-separated labels to add"},
                "remove_labels": {"type": "string", "description": "Comma-separated labels to remove"},
                "assignee_ids": _USER_IDS,
                "reviewer_ids": _USER_IDS,
                "milestone_id": {"type": "integer", "description": "Milestone ID (0 to unassign)"},
                "remove_source_branch": {"type": "boolean", "description": "Delete source branch after merge"},
                "squash": {"type": "boolean", "description": "Squash commits on merge"},
            },
            "required": ["project_id", "merge_request_iid"],
        },
    },
    {
        "name": "merge-merge-request",
        "description": "Merge (accept) a merge request",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_id": _PROJECT_ID,
                "merge_request_iid": _MR_IID,
                "merge_commit_message": {"type": "string", "description": "Custom merge commit message"},
                "squash_commit_message": {"type": "string", "description": "Custom squash commit message"},
                "squash": {"type": "boolean", "description": "Squash commits"},
                "should_remove_source_branch": {"type": "boolean", "description": "Delete source branch after merge"},
                "merge_when_pipeline_succeeds": {"type": "boolean", "description": "Merge automatically when the pipeline succeeds"},
                "sha": {"type": "string", "description": "Only merge if HEAD of source branch matches this SHA"},
            },
            "required": ["project_id", "merge_request_iid"],
        },
    },
    {
        "name": "approve-merge-request",
        "description": "Approve a merge request as the current user",
        "inputSchema": {
            "type": "object",
            "properties": {"project_id": _PROJECT_ID, "merge_request_iid": _MR_IID},
            "required": ["project_id", "merge_request_iid"],
        },
    },
    {
        "name": "list-merge-request-notes",
        "description": "List comments on a merge request",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_id": _PROJECT_ID,
                "merge_request_iid": _MR_IID,
                "sort": {"type": "string", "enum": ["asc", "desc"], "description": "Sort order by creation date", "default": "asc"},
                **_PAGINATION,
            },
            "required": ["project_id", "merge_request_iid"],
        },
    },
    {
        "name": "add-merge-request-note",
        "description": "Add a comment to a merge request",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_id": _PROJECT_ID,
                "merge_request_iid": _MR_IID,
                "body": {"type": "string", "description": "Comment text (Markdown)"},
            },
            "required": ["project_id", "merge_request_iid", "body"],
        },
    },
    # --- Issues ---
    {
        "name": "list-issues",
        "description": "List issues in a project, a group, or across GitLab (when no project_id/group_id is given)",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_id": _PROJECT_ID,
                "group_id": _GROUP_ID,
                "state": {"type": "string", "enum": ["opened", "closed", "all"], "description": "Filter by state", "default": "opened"},
                "scope": {"type": "string", "enum": ["created_by_me", "assigned_to_me", "all"], "description": "Scope (global default: created_by_me)"},
                "labels": _LABELS,
                "milestone": {"type": "string", "description": "Milestone title"},
                "assignee_username": {"type": "string", "description": "Filter by assignee username"},
                "author_username": {"type": "string", "description": "Filter by author username"},
                "search": {"type": "string", "description": "Search in title and description"},
                **_PAGINATION,
            },
        },
    },
    {
        "name": "get-issue",
        "description": "Get details of an issue",
        "inputSchema": {
            "type": "object",
            "properties": {"project_id": _PROJECT_ID, "issue_iid": _ISSUE_IID},
            "required": ["project_id", "issue_iid"],
        },
    },
    {
        "name": "create-issue",
        "description": "Create a new issue",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_id": _PROJECT_ID,
                "title": {"type": "string", "description": "Issue title"},
                "description": {"type": "string", "description": "Issue description (Markdown)"},
                "labels": _LABELS,
                "assignee_ids": _USER_IDS,
                "milestone_id": {"type": "integer", "description": "Milestone ID"},
                "due_date": {"type": "string", "description": "Due date (YYYY-MM-DD)"},
                "confidential": {"type": "boolean", "description": "Mark as confidential"},
            },
            "required": ["project_id", "title"],
        },
    },
    {
        "name": "update-issue",
        "description": "Update an issue (title, description, labels, assignees, close/reopen, ...)",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_id": _PROJECT_ID,
                "issue_iid": _ISSUE_IID,
                "title": {"type": "string", "description": "New title"},
                "description": {"type": "string", "description": "New description"},
                "state_event": {"type": "string", "enum": ["close", "reopen"], "description": "Close or reopen"},
                "labels": {"type": "string", "description": "Comma-separated labels (replaces all labels)"},
                "add_labels": {"type": "string", "description": "Comma-separated labels to add"},
                "remove_labels": {"type": "string", "description": "Comma-separated labels to remove"},
                "assignee_ids": _USER_IDS,
                "milestone_id": {"type": "integer", "description": "Milestone ID (0 to unassign)"},
                "due_date": {"type": "string", "description": "Due date (YYYY-MM-DD)"},
                "confidential": {"type": "boolean", "description": "Mark as confidential"},
            },
            "required": ["project_id", "issue_iid"],
        },
    },
    {
        "name": "list-issue-notes",
        "description": "List comments on an issue",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_id": _PROJECT_ID,
                "issue_iid": _ISSUE_IID,
                "sort": {"type": "string", "enum": ["asc", "desc"], "description": "Sort order by creation date", "default": "asc"},
                **_PAGINATION,
            },
            "required": ["project_id", "issue_iid"],
        },
    },
    {
        "name": "add-issue-note",
        "description": "Add a comment to an issue",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_id": _PROJECT_ID,
                "issue_iid": _ISSUE_IID,
                "body": {"type": "string", "description": "Comment text (Markdown)"},
            },
            "required": ["project_id", "issue_iid", "body"],
        },
    },
    # --- Labels & milestones ---
    {
        "name": "list-labels",
        "description": "List labels of a project",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_id": _PROJECT_ID,
                "search": {"type": "string", "description": "Filter labels by name"},
                **_PAGINATION,
            },
            "required": ["project_id"],
        },
    },
    {
        "name": "list-milestones",
        "description": "List milestones of a project",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_id": _PROJECT_ID,
                "state": {"type": "string", "enum": ["active", "closed"], "description": "Filter by state"},
                "search": {"type": "string", "description": "Filter milestones by title"},
                **_PAGINATION,
            },
            "required": ["project_id"],
        },
    },
    # --- CI/CD pipelines ---
    {
        "name": "list-pipelines",
        "description": "List CI/CD pipelines of a project",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_id": _PROJECT_ID,
                "status": {"type": "string", "enum": ["created", "waiting_for_resource", "preparing", "pending", "running", "success", "failed", "canceled", "skipped", "manual", "scheduled"], "description": "Filter by status"},
                "ref": {"type": "string", "description": "Filter by branch or tag"},
                "source": {"type": "string", "description": "Filter by source (push, web, trigger, schedule, api, merge_request_event, ...)"},
                "username": {"type": "string", "description": "Filter by triggering user"},
                **_PAGINATION,
            },
            "required": ["project_id"],
        },
    },
    {
        "name": "get-pipeline",
        "description": "Get details of a pipeline",
        "inputSchema": {
            "type": "object",
            "properties": {"project_id": _PROJECT_ID, "pipeline_id": _PIPELINE_ID},
            "required": ["project_id", "pipeline_id"],
        },
    },
    {
        "name": "create-pipeline",
        "description": "Trigger a new pipeline on a branch or tag",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_id": _PROJECT_ID,
                "ref": {"type": "string", "description": "Branch or tag to run the pipeline on"},
                "variables": {"type": "object", "description": "Pipeline variables as {\"KEY\": \"value\"}"},
            },
            "required": ["project_id", "ref"],
        },
    },
    {
        "name": "retry-pipeline",
        "description": "Retry failed or canceled jobs in a pipeline",
        "inputSchema": {
            "type": "object",
            "properties": {"project_id": _PROJECT_ID, "pipeline_id": _PIPELINE_ID},
            "required": ["project_id", "pipeline_id"],
        },
    },
    {
        "name": "cancel-pipeline",
        "description": "Cancel a running pipeline",
        "inputSchema": {
            "type": "object",
            "properties": {"project_id": _PROJECT_ID, "pipeline_id": _PIPELINE_ID},
            "required": ["project_id", "pipeline_id"],
        },
    },
    # --- CI/CD jobs ---
    {
        "name": "list-pipeline-jobs",
        "description": "List jobs of a pipeline",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_id": _PROJECT_ID,
                "pipeline_id": _PIPELINE_ID,
                "scope": {"type": "string", "enum": ["created", "pending", "running", "failed", "success", "canceled", "skipped", "manual"], "description": "Filter by job status"},
                "include_retried": {"type": "boolean", "description": "Include retried jobs", "default": False},
                **_PAGINATION,
            },
            "required": ["project_id", "pipeline_id"],
        },
    },
    {
        "name": "get-job",
        "description": "Get details of a CI/CD job",
        "inputSchema": {
            "type": "object",
            "properties": {"project_id": _PROJECT_ID, "job_id": _JOB_ID},
            "required": ["project_id", "job_id"],
        },
    },
    {
        "name": "get-job-log",
        "description": "Get the log (trace) output of a CI/CD job, useful for debugging failures",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_id": _PROJECT_ID,
                "job_id": _JOB_ID,
                "tail_lines": {"type": "integer", "description": "Return only the last N lines (0 = full log)", "default": 200},
            },
            "required": ["project_id", "job_id"],
        },
    },
    {
        "name": "retry-job",
        "description": "Retry a CI/CD job",
        "inputSchema": {
            "type": "object",
            "properties": {"project_id": _PROJECT_ID, "job_id": _JOB_ID},
            "required": ["project_id", "job_id"],
        },
    },
    {
        "name": "cancel-job",
        "description": "Cancel a CI/CD job",
        "inputSchema": {
            "type": "object",
            "properties": {"project_id": _PROJECT_ID, "job_id": _JOB_ID},
            "required": ["project_id", "job_id"],
        },
    },
    {
        "name": "play-job",
        "description": "Trigger a manual CI/CD job",
        "inputSchema": {
            "type": "object",
            "properties": {"project_id": _PROJECT_ID, "job_id": _JOB_ID},
            "required": ["project_id", "job_id"],
        },
    },
    # --- Search ---
    {
        "name": "search",
        "description": "Search GitLab globally, within a group, or within a project (code search uses scope 'blobs')",
        "inputSchema": {
            "type": "object",
            "properties": {
                "scope": {"type": "string", "enum": ["projects", "issues", "merge_requests", "milestones", "users", "commits", "blobs", "wiki_blobs", "notes"], "description": "What to search for ('blobs'/'commits' need a project or group, or advanced search)"},
                "search": {"type": "string", "description": "Search query"},
                "project_id": _PROJECT_ID,
                "group_id": _GROUP_ID,
                "ref": {"type": "string", "description": "Branch or tag to search (project blobs/commits only)"},
                **_PAGINATION,
            },
            "required": ["scope", "search"],
        },
    },
]

# Tools that modify GitLab state - disabled when GITLAB_READ_ONLY is enabled
WRITE_TOOLS = {
    "create-project",
    "fork-project",
    "create-or-update-file",
    "delete-file",
    "create-commit",
    "create-branch",
    "delete-branch",
    "create-tag",
    "create-merge-request",
    "update-merge-request",
    "merge-merge-request",
    "approve-merge-request",
    "add-merge-request-note",
    "create-issue",
    "update-issue",
    "add-issue-note",
    "create-pipeline",
    "retry-pipeline",
    "cancel-pipeline",
    "retry-job",
    "cancel-job",
    "play-job",
}


# ---------------------------------------------------------------------------
# MCP JSON-RPC endpoint
# ---------------------------------------------------------------------------

@app.post("/mcp")
async def mcp_endpoint(
    request: Request,
    mcp_session_id: Optional[str] = Header(None, alias="MCP-Session-Id"),
):
    """Main MCP endpoint handling JSON-RPC 2.0 requests."""
    body = None
    try:
        body = await request.json()
    except Exception as e:
        return JSONResponse(
            status_code=400,
            content={
                "jsonrpc": "2.0",
                "id": body.get("id") if body and isinstance(body, dict) else None,
                "error": {"code": -32700, "message": f"Parse error: {str(e)}"},
            },
        )

    method = body.get("method")
    request_id = body.get("id")
    params = body.get("params", {})

    # Handle initialize (no session required)
    if method == "initialize":
        return await handle_initialize(request_id, params)

    # Handle notifications/initialized (notification, no response needed)
    if method == "notifications/initialized":
        logger.info("Received initialized notification")
        return Response(status_code=200)

    # All other methods require a session
    if not mcp_session_id:
        return JSONResponse(
            status_code=400,
            content={
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32000, "message": "MCP-Session-Id header required"},
            },
        )

    if mcp_session_id not in sessions:
        return JSONResponse(
            status_code=400,
            content={
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32000, "message": "Invalid session ID"},
            },
        )

    # Route to appropriate handler
    if method == "tools/list":
        return await handle_tools_list(request_id)
    elif method == "tools/call":
        return await handle_tools_call(request_id, params, mcp_session_id)
    elif method == "ping":
        return JSONResponse(content={"jsonrpc": "2.0", "id": request_id, "result": {}})
    else:
        return JSONResponse(
            status_code=400,
            content={
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32601, "message": f"Method not found: {method}"},
            },
        )


async def handle_initialize(request_id: Any, params: Dict[str, Any]) -> Response:
    """Handle initialize method - creates a new session."""
    session_id = str(uuid.uuid4())
    sessions[session_id] = {
        "protocolVersion": params.get("protocolVersion", "2024-11-05"),
        "clientInfo": params.get("clientInfo", {}),
    }

    response = JSONResponse(
        content={
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {
                    "tools": {},
                },
                "serverInfo": {
                    "name": "gitlab-mcp-server",
                    "version": "1.0.0",
                },
            },
        }
    )
    response.headers["MCP-Session-Id"] = session_id
    return response


async def handle_tools_list(request_id: Any) -> JSONResponse:
    """Handle tools/list method - returns all available GitLab tools."""
    tools = TOOLS
    if get_bool("GITLAB_READ_ONLY", False):
        tools = [tool for tool in TOOLS if tool["name"] not in WRITE_TOOLS]

    return JSONResponse(
        content={
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {"tools": tools},
        }
    )


async def handle_tools_call(
    request_id: Any, params: Dict[str, Any], session_id: str
) -> JSONResponse:
    """Handle tools/call method - executes GitLab operations."""
    tool_name = params.get("name")
    arguments = params.get("arguments") or {}

    if not tool_name:
        return JSONResponse(
            status_code=400,
            content={
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32602, "message": "Tool name is required"},
            },
        )

    try:
        result = await execute_tool(tool_name, arguments, session_id)
        return JSONResponse(
            content={
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {"content": [{"type": "text", "text": result}]},
            }
        )
    except HTTPException as e:
        # GitLab/tool errors are returned as tool results so the agent can read and react to them
        logger.warning(f"Tool {tool_name} failed: {e.detail}")
        return JSONResponse(
            content={
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "content": [{"type": "text", "text": json.dumps({"error": e.detail, "status_code": e.status_code}, indent=2)}],
                    "isError": True,
                },
            }
        )
    except Exception as e:
        logger.error(f"Error executing tool {tool_name}: {e}", exc_info=True)
        return JSONResponse(
            status_code=500,
            content={
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32000, "message": f"Tool execution failed: {str(e)}"},
            },
        )


async def execute_tool(tool_name: str, arguments: Dict[str, Any], session_id: str) -> str:
    """Execute a GitLab tool operation."""
    if tool_name in WRITE_TOOLS and get_bool("GITLAB_READ_ONLY", False):
        raise HTTPException(status_code=403, detail=f"Tool {tool_name} is disabled: server is in read-only mode (GITLAB_READ_ONLY)")

    a = arguments
    try:
        # --- Users ---
        if tool_name == "get-current-user":
            user, _ = _gitlab_request(session_id, "GET", "/user")
            return json.dumps(_pick(user, "id", "username", "name", "email", "state", "is_admin", "web_url"), indent=2)

        elif tool_name == "list-users":
            users, headers = _gitlab_request(session_id, "GET", "/users", params={
                "search": a.get("search"),
                "username": a.get("username"),
                "active": a.get("active"),
                **_page_params(a),
            })
            return _list_result("users", users, headers, _summarize_user)

        # --- Projects ---
        elif tool_name == "list-projects":
            projects, headers = _gitlab_request(session_id, "GET", "/projects", params={
                "search": a.get("search"),
                "membership": a.get("membership", True),
                "owned": a.get("owned"),
                "starred": a.get("starred"),
                "archived": a.get("archived"),
                "visibility": a.get("visibility"),
                "order_by": a.get("order_by", "last_activity_at"),
                "simple": True,
                **_page_params(a),
            })
            return _list_result("projects", projects, headers, _summarize_project)

        elif tool_name == "get-project":
            project, _ = _gitlab_request(session_id, "GET", f"/projects/{_enc(a['project_id'])}")
            return json.dumps(project, indent=2)

        elif tool_name == "create-project":
            project, _ = _gitlab_request(session_id, "POST", "/projects", body={
                "name": a["name"],
                "path": a.get("path"),
                "namespace_id": a.get("namespace_id"),
                "description": a.get("description"),
                "visibility": a.get("visibility", "private"),
                "initialize_with_readme": a.get("initialize_with_readme", True),
                "default_branch": a.get("default_branch"),
            })
            return json.dumps({"message": f"Project {project.get('path_with_namespace')} created successfully",
                               "project": _summarize_project(project)}, indent=2)

        elif tool_name == "fork-project":
            project, _ = _gitlab_request(session_id, "POST", f"/projects/{_enc(a['project_id'])}/fork", body={
                "namespace_path": a.get("namespace_path"),
                "name": a.get("name"),
                "path": a.get("path"),
            })
            return json.dumps({"message": f"Fork {project.get('path_with_namespace')} created successfully",
                               "project": _summarize_project(project)}, indent=2)

        elif tool_name == "list-project-members":
            suffix = "/members/all" if a.get("include_inherited", True) else "/members"
            members, headers = _gitlab_request(session_id, "GET", f"/projects/{_enc(a['project_id'])}{suffix}", params={
                "query": a.get("query"),
                **_page_params(a),
            })
            return _list_result("members", members, headers, _summarize_user)

        # --- Groups ---
        elif tool_name == "list-groups":
            groups, headers = _gitlab_request(session_id, "GET", "/groups", params={
                "search": a.get("search"),
                "owned": a.get("owned"),
                "top_level_only": a.get("top_level_only"),
                **_page_params(a),
            })
            return _list_result("groups", groups, headers, _summarize_group)

        elif tool_name == "get-group":
            group, _ = _gitlab_request(session_id, "GET", f"/groups/{_enc(a['group_id'])}", params={"with_projects": False})
            return json.dumps(group, indent=2)

        elif tool_name == "list-group-projects":
            projects, headers = _gitlab_request(session_id, "GET", f"/groups/{_enc(a['group_id'])}/projects", params={
                "search": a.get("search"),
                "include_subgroups": a.get("include_subgroups"),
                "archived": a.get("archived"),
                "simple": True,
                **_page_params(a),
            })
            return _list_result("projects", projects, headers, _summarize_project)

        # --- Repository: files & tree ---
        elif tool_name == "get-repository-tree":
            tree, headers = _gitlab_request(session_id, "GET", f"/projects/{_enc(a['project_id'])}/repository/tree", params={
                "path": a.get("path"),
                "ref": a.get("ref"),
                "recursive": a.get("recursive"),
                **_page_params(a),
            })
            return _list_result("tree", tree, headers, lambda t: _pick(t, "name", "path", "type"))

        elif tool_name == "get-file-contents":
            project_id = _enc(a["project_id"])
            file_path = a["file_path"]
            ref = a.get("ref") or _default_branch(session_id, project_id)
            data, _ = _gitlab_request(session_id, "GET", f"/projects/{project_id}/repository/files/{_enc(file_path)}", params={"ref": ref})
            raw_content = base64.b64decode(data.get("content") or "")
            try:
                content: Optional[str] = raw_content.decode("utf-8")
                note = None
            except UnicodeDecodeError:
                content = None
                note = "Binary file - content not shown"
            result = {
                "file_path": data.get("file_path", file_path),
                "ref": ref,
                "size": data.get("size"),
                "last_commit_id": data.get("last_commit_id"),
                "blob_id": data.get("blob_id"),
                "content": content,
            }
            if note:
                result["note"] = note
            return json.dumps(result, indent=2)

        elif tool_name == "create-or-update-file":
            project_id = _enc(a["project_id"])
            file_path = a["file_path"]
            branch = a["branch"]
            file_url = f"/projects/{project_id}/repository/files/{_enc(file_path)}"

            # Check whether the file already exists to choose between create (POST) and update (PUT)
            exists = False
            for ref in [branch, a.get("start_branch")]:
                if not ref:
                    continue
                try:
                    _gitlab_request(session_id, "HEAD", file_url, params={"ref": ref})
                    exists = True
                    break
                except HTTPException as e:
                    if e.status_code != 404:
                        raise

            data, _ = _gitlab_request(session_id, "PUT" if exists else "POST", file_url, body={
                "branch": branch,
                "start_branch": a.get("start_branch"),
                "content": a["content"],
                "commit_message": a["commit_message"],
                "encoding": a.get("encoding"),
                "last_commit_id": a.get("last_commit_id") if exists else None,
            })
            return json.dumps({
                "message": f"File {file_path} {'updated' if exists else 'created'} successfully on branch {branch}",
                "file_path": (data or {}).get("file_path", file_path),
                "branch": (data or {}).get("branch", branch),
            }, indent=2)

        elif tool_name == "delete-file":
            file_path = a["file_path"]
            _gitlab_request(session_id, "DELETE", f"/projects/{_enc(a['project_id'])}/repository/files/{_enc(file_path)}", body={
                "branch": a["branch"],
                "commit_message": a["commit_message"],
            })
            return json.dumps({"message": f"File {file_path} deleted successfully from branch {a['branch']}"}, indent=2)

        elif tool_name == "create-commit":
            commit, _ = _gitlab_request(session_id, "POST", f"/projects/{_enc(a['project_id'])}/repository/commits", body={
                "branch": a["branch"],
                "commit_message": a["commit_message"],
                "start_branch": a.get("start_branch"),
                "actions": [_clean_body(action) for action in a["actions"]],
            })
            return json.dumps({"message": f"Commit created successfully on branch {a['branch']}",
                               "commit": _summarize_commit(commit)}, indent=2)

        # --- Repository: commits ---
        elif tool_name == "list-commits":
            commits, headers = _gitlab_request(session_id, "GET", f"/projects/{_enc(a['project_id'])}/repository/commits", params={
                "ref_name": a.get("ref_name"),
                "path": a.get("path"),
                "author": a.get("author"),
                "since": a.get("since"),
                "until": a.get("until"),
                **_page_params(a),
            })
            return _list_result("commits", commits, headers, _summarize_commit)

        elif tool_name == "get-commit":
            commit, _ = _gitlab_request(session_id, "GET", f"/projects/{_enc(a['project_id'])}/repository/commits/{_enc(a['sha'])}", params={"stats": True})
            return json.dumps(commit, indent=2)

        elif tool_name == "get-commit-diff":
            diffs, headers = _gitlab_request(session_id, "GET", f"/projects/{_enc(a['project_id'])}/repository/commits/{_enc(a['sha'])}/diff", params=_page_params(a))
            return _list_result("diffs", diffs, headers, _summarize_diff)

        elif tool_name == "compare-refs":
            comparison, _ = _gitlab_request(session_id, "GET", f"/projects/{_enc(a['project_id'])}/repository/compare", params={
                "from": a["from_ref"],
                "to": a["to_ref"],
                "straight": a.get("straight"),
            })
            commits = comparison.get("commits") or []
            diffs = comparison.get("diffs") or []
            return json.dumps({
                "from": a["from_ref"],
                "to": a["to_ref"],
                "commit_count": len(commits),
                "commits": [_summarize_commit(c) for c in commits],
                "diff_count": len(diffs),
                "diffs": [_summarize_diff(d) for d in diffs],
                "compare_timeout": comparison.get("compare_timeout"),
                "web_url": comparison.get("web_url"),
            }, indent=2)

        # --- Repository: branches & tags ---
        elif tool_name == "list-branches":
            branches, headers = _gitlab_request(session_id, "GET", f"/projects/{_enc(a['project_id'])}/repository/branches", params={
                "search": a.get("search"),
                **_page_params(a),
            })
            return _list_result("branches", branches, headers, _summarize_branch)

        elif tool_name == "get-branch":
            branch, _ = _gitlab_request(session_id, "GET", f"/projects/{_enc(a['project_id'])}/repository/branches/{_enc(a['branch'])}")
            return json.dumps(branch, indent=2)

        elif tool_name == "create-branch":
            project_id = _enc(a["project_id"])
            ref = a.get("ref") or _default_branch(session_id, project_id)
            branch, _ = _gitlab_request(session_id, "POST", f"/projects/{project_id}/repository/branches", params={
                "branch": a["branch"],
                "ref": ref,
            })
            return json.dumps({"message": f"Branch {a['branch']} created successfully from {ref}",
                               "branch": _summarize_branch(branch)}, indent=2)

        elif tool_name == "delete-branch":
            _gitlab_request(session_id, "DELETE", f"/projects/{_enc(a['project_id'])}/repository/branches/{_enc(a['branch'])}")
            return json.dumps({"message": f"Branch {a['branch']} deleted successfully"}, indent=2)

        elif tool_name == "list-tags":
            tags, headers = _gitlab_request(session_id, "GET", f"/projects/{_enc(a['project_id'])}/repository/tags", params={
                "search": a.get("search"),
                **_page_params(a),
            })
            return _list_result("tags", tags, headers, _summarize_tag)

        elif tool_name == "create-tag":
            tag, _ = _gitlab_request(session_id, "POST", f"/projects/{_enc(a['project_id'])}/repository/tags", params={
                "tag_name": a["tag_name"],
                "ref": a["ref"],
                "message": a.get("message"),
            })
            return json.dumps({"message": f"Tag {a['tag_name']} created successfully",
                               "tag": _summarize_tag(tag)}, indent=2)

        # --- Merge requests ---
        elif tool_name == "list-merge-requests":
            if a.get("project_id"):
                path = f"/projects/{_enc(a['project_id'])}/merge_requests"
            elif a.get("group_id"):
                path = f"/groups/{_enc(a['group_id'])}/merge_requests"
            else:
                path = "/merge_requests"
            merge_requests, headers = _gitlab_request(session_id, "GET", path, params={
                "state": a.get("state", "opened"),
                "scope": a.get("scope"),
                "source_branch": a.get("source_branch"),
                "target_branch": a.get("target_branch"),
                "author_username": a.get("author_username"),
                "reviewer_username": a.get("reviewer_username"),
                "labels": a.get("labels"),
                "search": a.get("search"),
                **_page_params(a),
            })
            return _list_result("merge_requests", merge_requests, headers, _summarize_merge_request)

        elif tool_name == "get-merge-request":
            merge_request, _ = _gitlab_request(session_id, "GET", f"/projects/{_enc(a['project_id'])}/merge_requests/{a['merge_request_iid']}")
            return json.dumps(merge_request, indent=2)

        elif tool_name == "get-merge-request-diffs":
            mr_path = f"/projects/{_enc(a['project_id'])}/merge_requests/{a['merge_request_iid']}"
            try:
                diffs, headers = _gitlab_request(session_id, "GET", f"{mr_path}/diffs", params=_page_params(a))
            except HTTPException as e:
                if e.status_code != 404:
                    raise
                # GitLab < 15.7 has no /diffs endpoint - fall back to /changes
                changes, headers = _gitlab_request(session_id, "GET", f"{mr_path}/changes")
                diffs = changes.get("changes") or []
            return _list_result("diffs", diffs, headers, _summarize_diff)

        elif tool_name == "create-merge-request":
            project_id = _enc(a["project_id"])
            title = a["title"]
            if a.get("draft") and not title.lower().startswith(("draft:", "[draft]", "(draft)")):
                title = f"Draft: {title}"
            merge_request, _ = _gitlab_request(session_id, "POST", f"/projects/{project_id}/merge_requests", body={
                "source_branch": a["source_branch"],
                "target_branch": a.get("target_branch") or _default_branch(session_id, project_id),
                "title": title,
                "description": a.get("description"),
                "assignee_ids": a.get("assignee_ids"),
                "reviewer_ids": a.get("reviewer_ids"),
                "labels": a.get("labels"),
                "milestone_id": a.get("milestone_id"),
                "remove_source_branch": a.get("remove_source_branch"),
                "squash": a.get("squash"),
            })
            return json.dumps({"message": f"Merge request !{merge_request.get('iid')} created successfully",
                               "merge_request": _summarize_merge_request(merge_request)}, indent=2)

        elif tool_name == "update-merge-request":
            merge_request, _ = _gitlab_request(session_id, "PUT", f"/projects/{_enc(a['project_id'])}/merge_requests/{a['merge_request_iid']}", body={
                "title": a.get("title"),
                "description": a.get("description"),
                "target_branch": a.get("target_branch"),
                "state_event": a.get("state_event"),
                "labels": a.get("labels"),
                "add_labels": a.get("add_labels"),
                "remove_labels": a.get("remove_labels"),
                "assignee_ids": a.get("assignee_ids"),
                "reviewer_ids": a.get("reviewer_ids"),
                "milestone_id": a.get("milestone_id"),
                "remove_source_branch": a.get("remove_source_branch"),
                "squash": a.get("squash"),
            })
            return json.dumps({"message": f"Merge request !{a['merge_request_iid']} updated successfully",
                               "merge_request": _summarize_merge_request(merge_request)}, indent=2)

        elif tool_name == "merge-merge-request":
            merge_request, _ = _gitlab_request(session_id, "PUT", f"/projects/{_enc(a['project_id'])}/merge_requests/{a['merge_request_iid']}/merge", body={
                "merge_commit_message": a.get("merge_commit_message"),
                "squash_commit_message": a.get("squash_commit_message"),
                "squash": a.get("squash"),
                "should_remove_source_branch": a.get("should_remove_source_branch"),
                "merge_when_pipeline_succeeds": a.get("merge_when_pipeline_succeeds"),
                "sha": a.get("sha"),
            })
            return json.dumps({"message": f"Merge request !{a['merge_request_iid']} merge requested",
                               "merge_request": _summarize_merge_request(merge_request)}, indent=2)

        elif tool_name == "approve-merge-request":
            approval, _ = _gitlab_request(session_id, "POST", f"/projects/{_enc(a['project_id'])}/merge_requests/{a['merge_request_iid']}/approve")
            return json.dumps({
                "message": f"Merge request !{a['merge_request_iid']} approved",
                "approved_by": [_username(ap.get("user")) for ap in (approval or {}).get("approved_by") or []],
            }, indent=2)

        elif tool_name == "list-merge-request-notes":
            notes, headers = _gitlab_request(session_id, "GET", f"/projects/{_enc(a['project_id'])}/merge_requests/{a['merge_request_iid']}/notes", params={
                "sort": a.get("sort", "asc"),
                "order_by": "created_at",
                **_page_params(a),
            })
            return _list_result("notes", notes, headers, _summarize_note)

        elif tool_name == "add-merge-request-note":
            note, _ = _gitlab_request(session_id, "POST", f"/projects/{_enc(a['project_id'])}/merge_requests/{a['merge_request_iid']}/notes", body={
                "body": a["body"],
            })
            return json.dumps({"message": f"Comment added to merge request !{a['merge_request_iid']}",
                               "note": _summarize_note(note)}, indent=2)

        # --- Issues ---
        elif tool_name == "list-issues":
            if a.get("project_id"):
                path = f"/projects/{_enc(a['project_id'])}/issues"
            elif a.get("group_id"):
                path = f"/groups/{_enc(a['group_id'])}/issues"
            else:
                path = "/issues"
            issues, headers = _gitlab_request(session_id, "GET", path, params={
                "state": a.get("state", "opened"),
                "scope": a.get("scope"),
                "labels": a.get("labels"),
                "milestone": a.get("milestone"),
                "assignee_username": a.get("assignee_username"),
                "author_username": a.get("author_username"),
                "search": a.get("search"),
                **_page_params(a),
            })
            return _list_result("issues", issues, headers, _summarize_issue)

        elif tool_name == "get-issue":
            issue, _ = _gitlab_request(session_id, "GET", f"/projects/{_enc(a['project_id'])}/issues/{a['issue_iid']}")
            return json.dumps(issue, indent=2)

        elif tool_name == "create-issue":
            issue, _ = _gitlab_request(session_id, "POST", f"/projects/{_enc(a['project_id'])}/issues", body={
                "title": a["title"],
                "description": a.get("description"),
                "labels": a.get("labels"),
                "assignee_ids": a.get("assignee_ids"),
                "milestone_id": a.get("milestone_id"),
                "due_date": a.get("due_date"),
                "confidential": a.get("confidential"),
            })
            return json.dumps({"message": f"Issue #{issue.get('iid')} created successfully",
                               "issue": _summarize_issue(issue)}, indent=2)

        elif tool_name == "update-issue":
            issue, _ = _gitlab_request(session_id, "PUT", f"/projects/{_enc(a['project_id'])}/issues/{a['issue_iid']}", body={
                "title": a.get("title"),
                "description": a.get("description"),
                "state_event": a.get("state_event"),
                "labels": a.get("labels"),
                "add_labels": a.get("add_labels"),
                "remove_labels": a.get("remove_labels"),
                "assignee_ids": a.get("assignee_ids"),
                "milestone_id": a.get("milestone_id"),
                "due_date": a.get("due_date"),
                "confidential": a.get("confidential"),
            })
            return json.dumps({"message": f"Issue #{a['issue_iid']} updated successfully",
                               "issue": _summarize_issue(issue)}, indent=2)

        elif tool_name == "list-issue-notes":
            notes, headers = _gitlab_request(session_id, "GET", f"/projects/{_enc(a['project_id'])}/issues/{a['issue_iid']}/notes", params={
                "sort": a.get("sort", "asc"),
                "order_by": "created_at",
                **_page_params(a),
            })
            return _list_result("notes", notes, headers, _summarize_note)

        elif tool_name == "add-issue-note":
            note, _ = _gitlab_request(session_id, "POST", f"/projects/{_enc(a['project_id'])}/issues/{a['issue_iid']}/notes", body={
                "body": a["body"],
            })
            return json.dumps({"message": f"Comment added to issue #{a['issue_iid']}",
                               "note": _summarize_note(note)}, indent=2)

        # --- Labels & milestones ---
        elif tool_name == "list-labels":
            labels, headers = _gitlab_request(session_id, "GET", f"/projects/{_enc(a['project_id'])}/labels", params={
                "search": a.get("search"),
                "with_counts": True,
                **_page_params(a),
            })
            return _list_result("labels", labels, headers, _summarize_label)

        elif tool_name == "list-milestones":
            milestones, headers = _gitlab_request(session_id, "GET", f"/projects/{_enc(a['project_id'])}/milestones", params={
                "state": a.get("state"),
                "search": a.get("search"),
                **_page_params(a),
            })
            return _list_result("milestones", milestones, headers, _summarize_milestone)

        # --- CI/CD pipelines ---
        elif tool_name == "list-pipelines":
            pipelines, headers = _gitlab_request(session_id, "GET", f"/projects/{_enc(a['project_id'])}/pipelines", params={
                "status": a.get("status"),
                "ref": a.get("ref"),
                "source": a.get("source"),
                "username": a.get("username"),
                **_page_params(a),
            })
            return _list_result("pipelines", pipelines, headers, _summarize_pipeline)

        elif tool_name == "get-pipeline":
            pipeline, _ = _gitlab_request(session_id, "GET", f"/projects/{_enc(a['project_id'])}/pipelines/{a['pipeline_id']}")
            return json.dumps(pipeline, indent=2)

        elif tool_name == "create-pipeline":
            variables = [
                {"key": key, "value": str(value), "variable_type": "env_var"}
                for key, value in (a.get("variables") or {}).items()
            ]
            pipeline, _ = _gitlab_request(session_id, "POST", f"/projects/{_enc(a['project_id'])}/pipeline", body={
                "ref": a["ref"],
                "variables": variables or None,
            })
            return json.dumps({"message": f"Pipeline {pipeline.get('id')} created on {a['ref']}",
                               "pipeline": _summarize_pipeline(pipeline)}, indent=2)

        elif tool_name == "retry-pipeline":
            pipeline, _ = _gitlab_request(session_id, "POST", f"/projects/{_enc(a['project_id'])}/pipelines/{a['pipeline_id']}/retry")
            return json.dumps({"message": f"Pipeline {a['pipeline_id']} retried",
                               "pipeline": _summarize_pipeline(pipeline)}, indent=2)

        elif tool_name == "cancel-pipeline":
            pipeline, _ = _gitlab_request(session_id, "POST", f"/projects/{_enc(a['project_id'])}/pipelines/{a['pipeline_id']}/cancel")
            return json.dumps({"message": f"Pipeline {a['pipeline_id']} canceled",
                               "pipeline": _summarize_pipeline(pipeline)}, indent=2)

        # --- CI/CD jobs ---
        elif tool_name == "list-pipeline-jobs":
            jobs, headers = _gitlab_request(session_id, "GET", f"/projects/{_enc(a['project_id'])}/pipelines/{a['pipeline_id']}/jobs", params={
                "scope": a.get("scope"),
                "include_retried": a.get("include_retried"),
                **_page_params(a),
            })
            return _list_result("jobs", jobs, headers, _summarize_job)

        elif tool_name == "get-job":
            job, _ = _gitlab_request(session_id, "GET", f"/projects/{_enc(a['project_id'])}/jobs/{a['job_id']}")
            return json.dumps(job, indent=2)

        elif tool_name == "get-job-log":
            log_text, _ = _gitlab_request(session_id, "GET", f"/projects/{_enc(a['project_id'])}/jobs/{a['job_id']}/trace", raw=True)
            lines = log_text.splitlines()
            tail_lines = int(a.get("tail_lines", 200) or 0)
            truncated = 0 < tail_lines < len(lines)
            if truncated:
                lines = lines[-tail_lines:]
            return json.dumps({
                "job_id": a["job_id"],
                "total_lines": len(log_text.splitlines()),
                "returned_lines": len(lines),
                "truncated": truncated,
                "log": "\n".join(lines),
            }, indent=2)

        elif tool_name == "retry-job":
            job, _ = _gitlab_request(session_id, "POST", f"/projects/{_enc(a['project_id'])}/jobs/{a['job_id']}/retry")
            return json.dumps({"message": f"Job {a['job_id']} retried as job {job.get('id')}",
                               "job": _summarize_job(job)}, indent=2)

        elif tool_name == "cancel-job":
            job, _ = _gitlab_request(session_id, "POST", f"/projects/{_enc(a['project_id'])}/jobs/{a['job_id']}/cancel")
            return json.dumps({"message": f"Job {a['job_id']} canceled",
                               "job": _summarize_job(job)}, indent=2)

        elif tool_name == "play-job":
            job, _ = _gitlab_request(session_id, "POST", f"/projects/{_enc(a['project_id'])}/jobs/{a['job_id']}/play")
            return json.dumps({"message": f"Manual job {a['job_id']} started",
                               "job": _summarize_job(job)}, indent=2)

        # --- Search ---
        elif tool_name == "search":
            scope = a["scope"]
            if a.get("project_id"):
                path = f"/projects/{_enc(a['project_id'])}/search"
            elif a.get("group_id"):
                path = f"/groups/{_enc(a['group_id'])}/search"
            else:
                path = "/search"
            results, headers = _gitlab_request(session_id, "GET", path, params={
                "scope": scope,
                "search": a["search"],
                "ref": a.get("ref") if a.get("project_id") else None,
                **_page_params(a),
            })
            return _list_result("results", results, headers, SEARCH_SUMMARIZERS.get(scope))

        else:
            raise HTTPException(status_code=400, detail=f"Unknown tool: {tool_name}")

    except HTTPException:
        raise
    except KeyError as e:
        raise HTTPException(status_code=400, detail=f"Missing required argument: {str(e)}")
    except Exception as e:
        logger.error(f"Error in execute_tool: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Tool execution error: {str(e)}")


# ---------------------------------------------------------------------------
# Pydantic models for Swagger documentation
# ---------------------------------------------------------------------------

class PaginationRequest(BaseModel):
    per_page: Optional[int] = None
    page: Optional[int] = None


class ProjectRequest(BaseModel):
    project_id: str


class GroupRequest(BaseModel):
    group_id: str


class ListUsersRequest(PaginationRequest):
    search: Optional[str] = None
    username: Optional[str] = None
    active: Optional[bool] = None


class ListProjectsRequest(PaginationRequest):
    search: Optional[str] = None
    membership: Optional[bool] = None
    owned: Optional[bool] = None
    starred: Optional[bool] = None
    archived: Optional[bool] = None
    visibility: Optional[str] = None
    order_by: Optional[str] = None


class CreateProjectRequest(BaseModel):
    name: str
    path: Optional[str] = None
    namespace_id: Optional[int] = None
    description: Optional[str] = None
    visibility: Optional[str] = None
    initialize_with_readme: Optional[bool] = None
    default_branch: Optional[str] = None


class ForkProjectRequest(ProjectRequest):
    namespace_path: Optional[str] = None
    name: Optional[str] = None
    path: Optional[str] = None


class ListProjectMembersRequest(ProjectRequest, PaginationRequest):
    query: Optional[str] = None
    include_inherited: Optional[bool] = None


class ListGroupsRequest(PaginationRequest):
    search: Optional[str] = None
    owned: Optional[bool] = None
    top_level_only: Optional[bool] = None


class ListGroupProjectsRequest(GroupRequest, PaginationRequest):
    search: Optional[str] = None
    include_subgroups: Optional[bool] = None
    archived: Optional[bool] = None


class RepositoryTreeRequest(ProjectRequest, PaginationRequest):
    path: Optional[str] = None
    ref: Optional[str] = None
    recursive: Optional[bool] = None


class GetFileContentsRequest(ProjectRequest):
    file_path: str
    ref: Optional[str] = None


class CreateOrUpdateFileRequest(ProjectRequest):
    file_path: str
    branch: str
    content: str
    commit_message: str
    start_branch: Optional[str] = None
    encoding: Optional[str] = None
    last_commit_id: Optional[str] = None


class DeleteFileRequest(ProjectRequest):
    file_path: str
    branch: str
    commit_message: str


class CommitAction(BaseModel):
    action: str
    file_path: str
    previous_path: Optional[str] = None
    content: Optional[str] = None
    encoding: Optional[str] = None
    execute_filemode: Optional[bool] = None


class CreateCommitRequest(ProjectRequest):
    branch: str
    commit_message: str
    start_branch: Optional[str] = None
    actions: List[CommitAction]


class ListCommitsRequest(ProjectRequest, PaginationRequest):
    ref_name: Optional[str] = None
    path: Optional[str] = None
    author: Optional[str] = None
    since: Optional[str] = None
    until: Optional[str] = None


class CommitRequest(ProjectRequest):
    sha: str


class CommitDiffRequest(CommitRequest, PaginationRequest):
    pass


class CompareRefsRequest(ProjectRequest):
    from_ref: str
    to_ref: str
    straight: Optional[bool] = None


class SearchProjectRequest(ProjectRequest, PaginationRequest):
    search: Optional[str] = None


class BranchRequest(ProjectRequest):
    branch: str


class CreateBranchRequest(BranchRequest):
    ref: Optional[str] = None


class CreateTagRequest(ProjectRequest):
    tag_name: str
    ref: str
    message: Optional[str] = None


class ListMergeRequestsRequest(PaginationRequest):
    project_id: Optional[str] = None
    group_id: Optional[str] = None
    state: Optional[str] = None
    scope: Optional[str] = None
    source_branch: Optional[str] = None
    target_branch: Optional[str] = None
    author_username: Optional[str] = None
    reviewer_username: Optional[str] = None
    labels: Optional[str] = None
    search: Optional[str] = None


class MergeRequestRequest(ProjectRequest):
    merge_request_iid: int


class MergeRequestPageRequest(MergeRequestRequest, PaginationRequest):
    pass


class CreateMergeRequestRequest(ProjectRequest):
    source_branch: str
    target_branch: Optional[str] = None
    title: str
    description: Optional[str] = None
    assignee_ids: Optional[List[int]] = None
    reviewer_ids: Optional[List[int]] = None
    labels: Optional[str] = None
    milestone_id: Optional[int] = None
    remove_source_branch: Optional[bool] = None
    squash: Optional[bool] = None
    draft: Optional[bool] = None


class UpdateMergeRequestRequest(MergeRequestRequest):
    title: Optional[str] = None
    description: Optional[str] = None
    target_branch: Optional[str] = None
    state_event: Optional[str] = None
    labels: Optional[str] = None
    add_labels: Optional[str] = None
    remove_labels: Optional[str] = None
    assignee_ids: Optional[List[int]] = None
    reviewer_ids: Optional[List[int]] = None
    milestone_id: Optional[int] = None
    remove_source_branch: Optional[bool] = None
    squash: Optional[bool] = None


class MergeMergeRequestRequest(MergeRequestRequest):
    merge_commit_message: Optional[str] = None
    squash_commit_message: Optional[str] = None
    squash: Optional[bool] = None
    should_remove_source_branch: Optional[bool] = None
    merge_when_pipeline_succeeds: Optional[bool] = None
    sha: Optional[str] = None


class ListMergeRequestNotesRequest(MergeRequestPageRequest):
    sort: Optional[str] = None


class AddMergeRequestNoteRequest(MergeRequestRequest):
    body: str


class ListIssuesRequest(PaginationRequest):
    project_id: Optional[str] = None
    group_id: Optional[str] = None
    state: Optional[str] = None
    scope: Optional[str] = None
    labels: Optional[str] = None
    milestone: Optional[str] = None
    assignee_username: Optional[str] = None
    author_username: Optional[str] = None
    search: Optional[str] = None


class IssueRequest(ProjectRequest):
    issue_iid: int


class CreateIssueRequest(ProjectRequest):
    title: str
    description: Optional[str] = None
    labels: Optional[str] = None
    assignee_ids: Optional[List[int]] = None
    milestone_id: Optional[int] = None
    due_date: Optional[str] = None
    confidential: Optional[bool] = None


class UpdateIssueRequest(IssueRequest):
    title: Optional[str] = None
    description: Optional[str] = None
    state_event: Optional[str] = None
    labels: Optional[str] = None
    add_labels: Optional[str] = None
    remove_labels: Optional[str] = None
    assignee_ids: Optional[List[int]] = None
    milestone_id: Optional[int] = None
    due_date: Optional[str] = None
    confidential: Optional[bool] = None


class ListIssueNotesRequest(IssueRequest, PaginationRequest):
    sort: Optional[str] = None


class AddIssueNoteRequest(IssueRequest):
    body: str


class ListMilestonesRequest(SearchProjectRequest):
    state: Optional[str] = None


class ListPipelinesRequest(ProjectRequest, PaginationRequest):
    status: Optional[str] = None
    ref: Optional[str] = None
    source: Optional[str] = None
    username: Optional[str] = None


class PipelineRequest(ProjectRequest):
    pipeline_id: int


class CreatePipelineRequest(ProjectRequest):
    ref: str
    variables: Optional[Dict[str, str]] = None


class ListPipelineJobsRequest(PipelineRequest, PaginationRequest):
    scope: Optional[str] = None
    include_retried: Optional[bool] = None


class JobRequest(ProjectRequest):
    job_id: int


class JobLogRequest(JobRequest):
    tail_lines: Optional[int] = None


class SearchRequest(PaginationRequest):
    scope: str
    search: str
    project_id: Optional[str] = None
    group_id: Optional[str] = None
    ref: Optional[str] = None


# Helper function to get or create a default session for Swagger testing
def _get_default_session() -> str:
    """Get or create a default session for Swagger testing."""
    default_session = "swagger-default"
    if default_session not in sessions:
        sessions[default_session] = {
            "protocolVersion": "2024-11-05",
            "clientInfo": {"name": "swagger", "version": "1.0.0"},
        }
    return default_session


async def _run_tool_route(tool_name: str, request: Optional[BaseModel] = None) -> JSONResponse:
    """Execute a tool from a Swagger route using the default session."""
    session_id = _get_default_session()
    arguments = request.model_dump(exclude_none=True) if request else {}
    result = await execute_tool(tool_name, arguments, session_id)
    return JSONResponse(content=json.loads(result))


# ---------------------------------------------------------------------------
# Individual Swagger routes for each tool
# ---------------------------------------------------------------------------

# --- Users ---
@app.post("/tools/get-current-user", tags=["Users"])
async def get_current_user_route():
    """Get the user that owns the configured access token."""
    return await _run_tool_route("get-current-user")


@app.post("/tools/list-users", tags=["Users"])
async def list_users_route(request: ListUsersRequest):
    """Search GitLab users."""
    return await _run_tool_route("list-users", request)


# --- Projects ---
@app.post("/tools/list-projects", tags=["Projects"])
async def list_projects_route(request: ListProjectsRequest):
    """List projects visible to the user."""
    return await _run_tool_route("list-projects", request)


@app.post("/tools/get-project", tags=["Projects"])
async def get_project_route(request: ProjectRequest):
    """Get project details."""
    return await _run_tool_route("get-project", request)


@app.post("/tools/create-project", tags=["Projects"])
async def create_project_route(request: CreateProjectRequest):
    """Create a new project."""
    return await _run_tool_route("create-project", request)


@app.post("/tools/fork-project", tags=["Projects"])
async def fork_project_route(request: ForkProjectRequest):
    """Fork a project."""
    return await _run_tool_route("fork-project", request)


@app.post("/tools/list-project-members", tags=["Projects"])
async def list_project_members_route(request: ListProjectMembersRequest):
    """List project members."""
    return await _run_tool_route("list-project-members", request)


# --- Groups ---
@app.post("/tools/list-groups", tags=["Groups"])
async def list_groups_route(request: ListGroupsRequest):
    """List groups visible to the user."""
    return await _run_tool_route("list-groups", request)


@app.post("/tools/get-group", tags=["Groups"])
async def get_group_route(request: GroupRequest):
    """Get group details."""
    return await _run_tool_route("get-group", request)


@app.post("/tools/list-group-projects", tags=["Groups"])
async def list_group_projects_route(request: ListGroupProjectsRequest):
    """List projects in a group."""
    return await _run_tool_route("list-group-projects", request)


# --- Repository ---
@app.post("/tools/get-repository-tree", tags=["Repository"])
async def get_repository_tree_route(request: RepositoryTreeRequest):
    """List files and directories in a repository."""
    return await _run_tool_route("get-repository-tree", request)


@app.post("/tools/get-file-contents", tags=["Repository"])
async def get_file_contents_route(request: GetFileContentsRequest):
    """Read a file from a repository."""
    return await _run_tool_route("get-file-contents", request)


@app.post("/tools/create-or-update-file", tags=["Repository"])
async def create_or_update_file_route(request: CreateOrUpdateFileRequest):
    """Create or update a file in a repository."""
    return await _run_tool_route("create-or-update-file", request)


@app.post("/tools/delete-file", tags=["Repository"])
async def delete_file_route(request: DeleteFileRequest):
    """Delete a file from a repository."""
    return await _run_tool_route("delete-file", request)


@app.post("/tools/create-commit", tags=["Repository"])
async def create_commit_route(request: CreateCommitRequest):
    """Create a multi-file commit."""
    return await _run_tool_route("create-commit", request)


@app.post("/tools/list-commits", tags=["Repository"])
async def list_commits_route(request: ListCommitsRequest):
    """List commits."""
    return await _run_tool_route("list-commits", request)


@app.post("/tools/get-commit", tags=["Repository"])
async def get_commit_route(request: CommitRequest):
    """Get commit details."""
    return await _run_tool_route("get-commit", request)


@app.post("/tools/get-commit-diff", tags=["Repository"])
async def get_commit_diff_route(request: CommitDiffRequest):
    """Get the diff of a commit."""
    return await _run_tool_route("get-commit-diff", request)


@app.post("/tools/compare-refs", tags=["Repository"])
async def compare_refs_route(request: CompareRefsRequest):
    """Compare two refs."""
    return await _run_tool_route("compare-refs", request)


@app.post("/tools/list-branches", tags=["Repository"])
async def list_branches_route(request: SearchProjectRequest):
    """List branches."""
    return await _run_tool_route("list-branches", request)


@app.post("/tools/get-branch", tags=["Repository"])
async def get_branch_route(request: BranchRequest):
    """Get branch details."""
    return await _run_tool_route("get-branch", request)


@app.post("/tools/create-branch", tags=["Repository"])
async def create_branch_route(request: CreateBranchRequest):
    """Create a branch."""
    return await _run_tool_route("create-branch", request)


@app.post("/tools/delete-branch", tags=["Repository"])
async def delete_branch_route(request: BranchRequest):
    """Delete a branch."""
    return await _run_tool_route("delete-branch", request)


@app.post("/tools/list-tags", tags=["Repository"])
async def list_tags_route(request: SearchProjectRequest):
    """List tags."""
    return await _run_tool_route("list-tags", request)


@app.post("/tools/create-tag", tags=["Repository"])
async def create_tag_route(request: CreateTagRequest):
    """Create a tag."""
    return await _run_tool_route("create-tag", request)


# --- Merge requests ---
@app.post("/tools/list-merge-requests", tags=["Merge Requests"])
async def list_merge_requests_route(request: ListMergeRequestsRequest):
    """List merge requests."""
    return await _run_tool_route("list-merge-requests", request)


@app.post("/tools/get-merge-request", tags=["Merge Requests"])
async def get_merge_request_route(request: MergeRequestRequest):
    """Get merge request details."""
    return await _run_tool_route("get-merge-request", request)


@app.post("/tools/get-merge-request-diffs", tags=["Merge Requests"])
async def get_merge_request_diffs_route(request: MergeRequestPageRequest):
    """Get merge request diffs."""
    return await _run_tool_route("get-merge-request-diffs", request)


@app.post("/tools/create-merge-request", tags=["Merge Requests"])
async def create_merge_request_route(request: CreateMergeRequestRequest):
    """Create a merge request."""
    return await _run_tool_route("create-merge-request", request)


@app.post("/tools/update-merge-request", tags=["Merge Requests"])
async def update_merge_request_route(request: UpdateMergeRequestRequest):
    """Update a merge request."""
    return await _run_tool_route("update-merge-request", request)


@app.post("/tools/merge-merge-request", tags=["Merge Requests"])
async def merge_merge_request_route(request: MergeMergeRequestRequest):
    """Merge a merge request."""
    return await _run_tool_route("merge-merge-request", request)


@app.post("/tools/approve-merge-request", tags=["Merge Requests"])
async def approve_merge_request_route(request: MergeRequestRequest):
    """Approve a merge request."""
    return await _run_tool_route("approve-merge-request", request)


@app.post("/tools/list-merge-request-notes", tags=["Merge Requests"])
async def list_merge_request_notes_route(request: ListMergeRequestNotesRequest):
    """List merge request comments."""
    return await _run_tool_route("list-merge-request-notes", request)


@app.post("/tools/add-merge-request-note", tags=["Merge Requests"])
async def add_merge_request_note_route(request: AddMergeRequestNoteRequest):
    """Comment on a merge request."""
    return await _run_tool_route("add-merge-request-note", request)


# --- Issues ---
@app.post("/tools/list-issues", tags=["Issues"])
async def list_issues_route(request: ListIssuesRequest):
    """List issues."""
    return await _run_tool_route("list-issues", request)


@app.post("/tools/get-issue", tags=["Issues"])
async def get_issue_route(request: IssueRequest):
    """Get issue details."""
    return await _run_tool_route("get-issue", request)


@app.post("/tools/create-issue", tags=["Issues"])
async def create_issue_route(request: CreateIssueRequest):
    """Create an issue."""
    return await _run_tool_route("create-issue", request)


@app.post("/tools/update-issue", tags=["Issues"])
async def update_issue_route(request: UpdateIssueRequest):
    """Update an issue."""
    return await _run_tool_route("update-issue", request)


@app.post("/tools/list-issue-notes", tags=["Issues"])
async def list_issue_notes_route(request: ListIssueNotesRequest):
    """List issue comments."""
    return await _run_tool_route("list-issue-notes", request)


@app.post("/tools/add-issue-note", tags=["Issues"])
async def add_issue_note_route(request: AddIssueNoteRequest):
    """Comment on an issue."""
    return await _run_tool_route("add-issue-note", request)


@app.post("/tools/list-labels", tags=["Issues"])
async def list_labels_route(request: SearchProjectRequest):
    """List project labels."""
    return await _run_tool_route("list-labels", request)


@app.post("/tools/list-milestones", tags=["Issues"])
async def list_milestones_route(request: ListMilestonesRequest):
    """List project milestones."""
    return await _run_tool_route("list-milestones", request)


# --- CI/CD ---
@app.post("/tools/list-pipelines", tags=["CI/CD"])
async def list_pipelines_route(request: ListPipelinesRequest):
    """List pipelines."""
    return await _run_tool_route("list-pipelines", request)


@app.post("/tools/get-pipeline", tags=["CI/CD"])
async def get_pipeline_route(request: PipelineRequest):
    """Get pipeline details."""
    return await _run_tool_route("get-pipeline", request)


@app.post("/tools/create-pipeline", tags=["CI/CD"])
async def create_pipeline_route(request: CreatePipelineRequest):
    """Trigger a pipeline."""
    return await _run_tool_route("create-pipeline", request)


@app.post("/tools/retry-pipeline", tags=["CI/CD"])
async def retry_pipeline_route(request: PipelineRequest):
    """Retry a pipeline."""
    return await _run_tool_route("retry-pipeline", request)


@app.post("/tools/cancel-pipeline", tags=["CI/CD"])
async def cancel_pipeline_route(request: PipelineRequest):
    """Cancel a pipeline."""
    return await _run_tool_route("cancel-pipeline", request)


@app.post("/tools/list-pipeline-jobs", tags=["CI/CD"])
async def list_pipeline_jobs_route(request: ListPipelineJobsRequest):
    """List jobs of a pipeline."""
    return await _run_tool_route("list-pipeline-jobs", request)


@app.post("/tools/get-job", tags=["CI/CD"])
async def get_job_route(request: JobRequest):
    """Get job details."""
    return await _run_tool_route("get-job", request)


@app.post("/tools/get-job-log", tags=["CI/CD"])
async def get_job_log_route(request: JobLogRequest):
    """Get job log output."""
    return await _run_tool_route("get-job-log", request)


@app.post("/tools/retry-job", tags=["CI/CD"])
async def retry_job_route(request: JobRequest):
    """Retry a job."""
    return await _run_tool_route("retry-job", request)


@app.post("/tools/cancel-job", tags=["CI/CD"])
async def cancel_job_route(request: JobRequest):
    """Cancel a job."""
    return await _run_tool_route("cancel-job", request)


@app.post("/tools/play-job", tags=["CI/CD"])
async def play_job_route(request: JobRequest):
    """Trigger a manual job."""
    return await _run_tool_route("play-job", request)


# --- Search ---
@app.post("/tools/search", tags=["Search"])
async def search_route(request: SearchRequest):
    """Search GitLab."""
    return await _run_tool_route("search", request)


@app.get("/")
async def root():
    """Root endpoint with API information."""
    return {
        "service": "GitLab MCP Server",
        "version": "1.0.0",
        "gitlab_url": get_str("GITLAB_URL", "https://gitlab.com"),
        "read_only": get_bool("GITLAB_READ_ONLY", False),
        "endpoints": {
            "/mcp": "POST - MCP JSON-RPC 2.0 endpoint",
            "/docs": "Swagger UI documentation",
            "/redoc": "ReDoc documentation",
        },
    }


@app.get("/health")
async def health_check():
    """
    Health check endpoint.

    Calls the GitLab /version endpoint with the configured token to verify both
    connectivity and authentication.
    """
    try:
        headers = {}
        token = get_str("GITLAB_TOKEN")
        if token:
            headers["PRIVATE-TOKEN"] = token
        resp = requests.get(
            f"{_gitlab_api_base()}/version",
            headers=headers,
            verify=_gitlab_verify(),
            timeout=5,
        )
        if resp.status_code >= 400:
            return JSONResponse(
                status_code=503,
                content={"status": "unhealthy", "reason": f"GitLab API error {resp.status_code}: {_error_message(resp)}"},
            )
        return JSONResponse(content={"status": "healthy", "gitlab_version": resp.json().get("version")})
    except Exception as e:
        logger.error(f"Health check failed: {e}")
        return JSONResponse(
            status_code=503, content={"status": "unhealthy", "reason": str(e)}
        )


def run() -> None:
    """Run the GitLab MCP Server."""
    host = get_str("MCP_HOST", "0.0.0.0") or "0.0.0.0"
    port = get_int("MCP_PORT", 8000)

    import uvicorn

    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    run()
