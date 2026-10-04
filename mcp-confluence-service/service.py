from __future__ import annotations

import html
import json
import logging
import re
import uuid
from html.parser import HTMLParser
from typing import Any, Callable, Dict, List, Optional, Tuple

import requests
from fastapi import FastAPI, HTTPException, Header, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel

from config import get_bool, get_int, get_str

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

app = FastAPI(
    title="Confluence MCP Server",
    description="MCP server for Confluence operations",
    version="1.0.0",
)

# Session storage (in production, use Redis or similar)
sessions: Dict[str, Dict[str, Any]] = {}

# Confluence HTTP clients cache per session
confluence_clients: Dict[str, requests.Session] = {}


# ---------------------------------------------------------------------------
# Confluence client helpers
# ---------------------------------------------------------------------------

def _confluence_base_url() -> str:
    """Return the Confluence base URL (e.g. https://confluence.example.com or https://x.atlassian.net/wiki)."""
    base_url = get_str("CONFLUENCE_URL")
    if not base_url:
        raise HTTPException(status_code=500, detail="CONFLUENCE_URL is not configured")
    return base_url.rstrip("/")


def _confluence_api_base() -> str:
    """Return the Confluence REST API v1 base URL."""
    return f"{_confluence_base_url()}/rest/api"


def _confluence_verify() -> Any:
    """Return the `verify` argument for requests (CA bundle path or bool)."""
    ca_bundle = get_str("CONFLUENCE_CA_BUNDLE")
    if ca_bundle:
        return ca_bundle
    return get_bool("CONFLUENCE_VERIFY_SSL", True)


def _auth_mode() -> str:
    """Describe which authentication method is configured."""
    if get_str("CONFLUENCE_USERNAME") and get_str("CONFLUENCE_TOKEN"):
        return f"basic ({get_str('CONFLUENCE_USERNAME')} + token)"
    if get_str("CONFLUENCE_TOKEN"):
        return "bearer (personal access token)"
    return "none (anonymous)"


def _auth_hint() -> str:
    """Hint for authentication failures, based on the configured instance type."""
    if ".atlassian.net" in (get_str("CONFLUENCE_URL") or ""):
        if not get_str("CONFLUENCE_USERNAME"):
            return "Confluence Cloud requires CONFLUENCE_USERNAME (your Atlassian email) together with an API token"
        return ("Confluence Cloud rejected the credentials - check that CONFLUENCE_USERNAME is the email of the account "
                "that can open this site and CONFLUENCE_TOKEN is a classic (non-scoped) API token of that same account")
    return "Check CONFLUENCE_TOKEN (Server/Data Center expects a Personal Access Token without CONFLUENCE_USERNAME)"


def _create_confluence_client() -> requests.Session:
    """Create and return an authenticated Confluence HTTP session."""
    username = get_str("CONFLUENCE_USERNAME")
    token = get_str("CONFLUENCE_TOKEN")
    logger.info("Creating Confluence client for %s", _confluence_api_base())

    client = requests.Session()
    client.headers.update({
        "Accept": "application/json",
        "User-Agent": "confluence-mcp-server/1.0.0",
    })
    if username and token:
        # Cloud: email + API token (also works for Server/DC basic auth with a password)
        client.auth = (username, token)
    elif token:
        # Server/Data Center: Personal Access Token
        client.headers["Authorization"] = f"Bearer {token}"
    else:
        logger.warning("CONFLUENCE_TOKEN is not set - only anonymously accessible content will be available")
    client.verify = _confluence_verify()
    return client


def _get_confluence_client(session_id: str) -> requests.Session:
    """Get or create Confluence client for a session."""
    if session_id not in confluence_clients:
        confluence_clients[session_id] = _create_confluence_client()
        logger.info(f"Created Confluence client for session {session_id}")
    return confluence_clients[session_id]


def _clean_params(params: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Drop empty query params and convert bools to Confluence's format."""
    cleaned: Dict[str, Any] = {}
    for key, value in (params or {}).items():
        if value is None or value == "":
            continue
        if isinstance(value, bool):
            value = "true" if value else "false"
        cleaned[key] = value
    return cleaned


def _error_message(resp: requests.Response) -> str:
    """Extract a readable error message from a Confluence error response."""
    try:
        data = resp.json()
    except ValueError:
        return resp.text[:500] or resp.reason
    if isinstance(data, dict):
        message = data.get("message") or data.get("errorMessage") or data.get("reason") or data
        return message if isinstance(message, str) else json.dumps(message)
    return str(data)


def _confluence_request(
    session_id: str,
    method: str,
    path: str = "",
    params: Optional[Dict[str, Any]] = None,
    body: Any = None,
    files: Optional[Dict[str, Any]] = None,
    headers: Optional[Dict[str, str]] = None,
    url: Optional[str] = None,
    raw: bool = False,
) -> Tuple[Any, Any]:
    """Call the Confluence API and return (data, response headers)."""
    client = _get_confluence_client(session_id)
    url = url or f"{_confluence_api_base()}{path}"
    try:
        resp = client.request(
            method,
            url,
            params=_clean_params(params),
            json=body,
            files=files,
            headers=headers,
            timeout=get_int("CONFLUENCE_TIMEOUT", 30),
        )
    except requests.RequestException as e:
        logger.error(f"Confluence request failed: {method} {url}: {e}")
        raise HTTPException(status_code=502, detail=f"Failed to reach Confluence: {str(e)}")

    if resp.status_code >= 400:
        raise HTTPException(
            status_code=resp.status_code,
            detail=f"Confluence API error {resp.status_code}: {_error_message(resp)}",
        )
    if raw:
        return resp.content, resp.headers
    if resp.status_code == 204 or not resp.content:
        return None, resp.headers
    try:
        return resp.json(), resp.headers
    except ValueError:
        # Usually means CONFLUENCE_URL points at an HTML page (login/SSO redirect, wrong context path)
        raise HTTPException(
            status_code=502,
            detail=f"Confluence returned non-JSON response from {url} - check CONFLUENCE_URL and authentication",
        )


def _page_params(arguments: Dict[str, Any], default_limit: int = 25) -> Dict[str, int]:
    """Build pagination query params (start/limit) from tool arguments."""
    limit = int(arguments.get("limit") or default_limit)
    start = int(arguments.get("start") or 0)
    return {"limit": max(1, min(limit, 100)), "start": max(0, start)}


def _pagination(data: Dict[str, Any]) -> Dict[str, Optional[int]]:
    """Extract pagination info from a Confluence paged response."""
    start = data.get("start", 0)
    size = data.get("size", len(data.get("results") or []))
    has_next = bool((data.get("_links") or {}).get("next"))
    return {
        "start": start,
        "limit": data.get("limit"),
        "size": size,
        "total": data.get("totalSize"),
        "next_start": start + size if has_next else None,
    }


def _list_result(
    key: str,
    data: Dict[str, Any],
    summarize: Optional[Callable[[Dict[str, Any]], Dict[str, Any]]] = None,
) -> str:
    """Format a paginated list response."""
    items = data.get("results") or []
    if summarize:
        items = [summarize(item) for item in items]
    return json.dumps({key: items, "count": len(items), "pagination": _pagination(data)}, indent=2)


def _cql_quote(value: str) -> str:
    """Quote a value for use inside a CQL expression."""
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'


def _split_labels(labels: Any) -> List[str]:
    """Accept labels as a list or a comma-separated string."""
    if isinstance(labels, str):
        labels = labels.split(",")
    return [str(label).strip() for label in labels or [] if str(label).strip()]


def _body_payload(content: str, representation: str) -> Dict[str, Any]:
    """Build a Confluence body payload from content in the given representation."""
    if representation == "plain":
        paragraphs = content.split("\n")
        value = "".join(f"<p>{html.escape(p)}</p>" if p.strip() else "<p><br /></p>" for p in paragraphs)
        return {"storage": {"value": value, "representation": "storage"}}
    if representation not in ("storage", "wiki"):
        raise HTTPException(status_code=400, detail=f"Unsupported representation: {representation} (use storage, wiki or plain)")
    return {representation: {"value": content, "representation": representation}}


# ---------------------------------------------------------------------------
# Confluence storage/HTML -> plain text conversion (compact output for LLMs)
# ---------------------------------------------------------------------------

class _HTMLToText(HTMLParser):
    BLOCK_TAGS = {"p", "div", "pre", "blockquote", "table", "ul", "ol", "tr", "section",
                  "ac:layout-section", "ac:layout-cell", "ac:rich-text-body", "ac:task"}
    # Blocks followed by a blank line
    SPACED_TAGS = {"p", "pre", "blockquote", "table", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6"}
    HEADING_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6"}
    SKIP_TAGS = {"script", "style", "ac:parameter"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._out: List[str] = []
        self._skip = 0
        self._pre = 0
        self._list_item = 0  # inside <li>: nested blocks don't add blank lines
        self._cell = 0  # inside <td>/<th>: nested blocks stay on the row
        self._link_start: Optional[int] = None  # output position at <ac:link>
        self._link_target: Optional[str] = None  # linked page/attachment/url title

    def _emit(self, text: str, raw: bool = False) -> None:
        if not raw:
            text = re.sub(r"\s+", " ", text)
            if not self._out or self._out[-1].endswith(("\n", " ")):
                text = text.lstrip()
            if not text:
                return
        self._out.append(text)

    def _newline(self, count: int = 1) -> None:
        """Ensure the output ends with at least `count` newlines."""
        if not self._out or self._out[-1] == "- ":
            return
        if self._cell:
            if not self._out[-1].endswith(" "):
                self._emit(" ", raw=True)
            return
        self._out[-1] = self._out[-1].rstrip(" ")
        tail = "".join(self._out[-3:])
        existing = len(tail) - len(tail.rstrip("\n"))
        if existing < count:
            self._out.append("\n" * (count - existing))

    def handle_starttag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        if tag in self.SKIP_TAGS:
            self._skip += 1
        elif tag == "br":
            self._newline()
        elif tag == "hr":
            self._newline()
            self._emit("---", raw=True)
            self._newline()
        elif tag in self.HEADING_TAGS:
            self._newline(2)
            self._emit("#" * int(tag[1]) + " ", raw=True)
        elif tag == "li":
            self._newline()
            self._emit("- ", raw=True)
            self._list_item += 1
        elif tag in ("td", "th"):
            self._emit("| ", raw=True)
            self._cell += 1
        elif tag == "ac:link":
            self._link_start = len(self._out)
            self._link_target = None
        elif tag in ("ri:page", "ri:blog-post", "ri:attachment", "ri:url", "ri:space"):
            attr = dict(attrs)
            self._link_target = (attr.get("ri:content-title") or attr.get("ri:filename")
                                 or attr.get("ri:value") or attr.get("ri:space-key"))
        elif tag in self.BLOCK_TAGS:
            if tag == "pre":
                self._pre += 1
            self._newline()

    def handle_endtag(self, tag: str) -> None:
        if tag in self.SKIP_TAGS:
            self._skip = max(0, self._skip - 1)
        elif tag in ("td", "th"):
            self._cell = max(0, self._cell - 1)
            self._emit(" ", raw=True)
        elif tag == "li":
            self._list_item = max(0, self._list_item - 1)
        elif tag == "ac:link":
            # Links without their own text show the target title
            if self._link_start is not None and self._link_start == len(self._out) and self._link_target:
                self._emit(self._link_target)
            self._link_start = None
            self._link_target = None
        elif tag == "tr":
            self._emit("|", raw=True)
            self._newline()
        elif tag in self.HEADING_TAGS or tag in self.BLOCK_TAGS:
            if tag == "pre":
                self._pre = max(0, self._pre - 1)
            self._newline(2 if tag in self.SPACED_TAGS and not self._list_item else 1)

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self._emit(data, raw=bool(self._pre))

    def unknown_decl(self, data: str) -> None:
        # CDATA sections hold code macro bodies in storage format
        if data.startswith("CDATA[") and not self._skip:
            if self._link_start is not None:
                # Link body text stays inline
                self._emit(data[len("CDATA["):])
                return
            self._newline()
            self._emit(data[len("CDATA["):], raw=True)
            self._newline(2)

    def text(self) -> str:
        return re.sub(r"\n{3,}", "\n\n", "".join(self._out)).strip()


def _html_to_text(value: str) -> str:
    parser = _HTMLToText()
    parser.feed(value or "")
    parser.close()
    return parser.text()


def _truncate(text: Optional[str], max_chars: Any) -> Tuple[Optional[str], bool]:
    """Truncate text to max_chars (0/None = no limit)."""
    max_chars = int(max_chars or 0)
    if text is not None and max_chars > 0 and len(text) > max_chars:
        return text[:max_chars], True
    return text, False


# ---------------------------------------------------------------------------
# Response summarizers (keep list output compact for LLM context)
# ---------------------------------------------------------------------------

def _web_url(obj: Dict[str, Any]) -> Optional[str]:
    webui = (obj.get("_links") or {}).get("webui")
    return f"{_confluence_base_url()}{webui}" if webui else None


def _user_name(user: Any) -> Optional[str]:
    if not isinstance(user, dict):
        return None
    return user.get("displayName") or user.get("username") or user.get("publicName")


def _summarize_space(s: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": s.get("id"),
        "key": s.get("key"),
        "name": s.get("name"),
        "type": s.get("type"),
        "status": s.get("status"),
        "description": ((s.get("description") or {}).get("plain") or {}).get("value"),
        "homepage_id": (s.get("homepage") or {}).get("id"),
        "web_url": _web_url(s),
    }


def _summarize_content(c: Dict[str, Any]) -> Dict[str, Any]:
    version = c.get("version") or {}
    return {
        "id": c.get("id"),
        "type": c.get("type"),
        "status": c.get("status"),
        "title": c.get("title"),
        "space_key": (c.get("space") or {}).get("key"),
        "version": version.get("number"),
        "last_updated": version.get("when"),
        "updated_by": _user_name(version.get("by")),
        "web_url": _web_url(c),
    }


def _summarize_comment(c: Dict[str, Any]) -> Dict[str, Any]:
    version = c.get("version") or {}
    storage = ((c.get("body") or {}).get("storage") or {}).get("value")
    return {
        "id": c.get("id"),
        "author": _user_name(version.get("by")) or _user_name((c.get("history") or {}).get("createdBy")),
        "created": (c.get("history") or {}).get("createdDate") or version.get("when"),
        "text": _html_to_text(storage) if storage else None,
        "web_url": _web_url(c),
    }


def _summarize_attachment(a: Dict[str, Any]) -> Dict[str, Any]:
    extensions = a.get("extensions") or {}
    metadata = a.get("metadata") or {}
    download = (a.get("_links") or {}).get("download")
    return {
        "id": a.get("id"),
        "title": a.get("title"),
        "media_type": metadata.get("mediaType") or extensions.get("mediaType"),
        "file_size": extensions.get("fileSize"),
        "comment": metadata.get("comment") or extensions.get("comment"),
        "version": (a.get("version") or {}).get("number"),
        "download_url": f"{_confluence_base_url()}{download}" if download else None,
    }


def _summarize_label(label: Dict[str, Any]) -> Dict[str, Any]:
    return {"name": label.get("name"), "prefix": label.get("prefix"), "id": label.get("id")}


_CONTENT_EXPAND = "space,version"


# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------

_PAGE_ID = {"type": "string", "description": "Page (content) ID - the number in the page URL, e.g. .../pages/123456/..."}
_SPACE_KEY = {"type": "string", "description": "Space key (e.g. 'DEV', '~username' for personal spaces)"}
_PAGINATION = {
    "limit": {"type": "integer", "description": "Results per page (max 100)", "default": 25},
    "start": {"type": "integer", "description": "Offset of the first result (use pagination.next_start)", "default": 0},
}
_REPRESENTATION = {
    "type": "string",
    "enum": ["storage", "wiki", "plain"],
    "description": "Format of 'body': storage (Confluence XHTML), wiki (wiki markup) or plain (plain text, converted to paragraphs)",
    "default": "storage",
}
_FORMAT = {
    "type": "string",
    "enum": ["text", "storage", "view"],
    "description": "Body format to return: text (clean plain text, best for reading/memory), storage (raw XHTML, needed before editing), view (rendered HTML)",
    "default": "text",
}
_MAX_CHARS = {"type": "integer", "description": "Truncate the body to this many characters (0 = no limit)", "default": 0}

TOOLS: List[Dict[str, Any]] = [
    # --- Users ---
    {
        "name": "get-current-user",
        "description": "Get the Confluence user that the configured credentials belong to",
        "inputSchema": {"type": "object", "properties": {}},
    },
    # --- Spaces ---
    {
        "name": "list-spaces",
        "description": "List Confluence spaces visible to the user",
        "inputSchema": {
            "type": "object",
            "properties": {
                "type": {"type": "string", "enum": ["global", "personal"], "description": "Filter by space type"},
                "status": {"type": "string", "enum": ["current", "archived"], "description": "Filter by space status"},
                **_PAGINATION,
            },
        },
    },
    {
        "name": "get-space",
        "description": "Get details of a space, including its description and homepage ID",
        "inputSchema": {
            "type": "object",
            "properties": {"space_key": _SPACE_KEY},
            "required": ["space_key"],
        },
    },
    # --- Search ---
    {
        "name": "search-content",
        "description": "Search Confluence pages/blog posts by text, optionally within a space and/or by label",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Text to search for"},
                "space_key": _SPACE_KEY,
                "content_type": {"type": "string", "enum": ["page", "blogpost", "comment", "attachment"], "description": "Content type to search", "default": "page"},
                "label": {"type": "string", "description": "Only content with this label"},
                "title_only": {"type": "boolean", "description": "Match the title only instead of the full text", "default": False},
                **_PAGINATION,
            },
            "required": ["query"],
        },
    },
    {
        "name": "cql-search",
        "description": "Search Confluence with a raw CQL query (e.g. 'space = DEV and type = page and lastmodified > now(\"-7d\")')",
        "inputSchema": {
            "type": "object",
            "properties": {
                "cql": {"type": "string", "description": "CQL query"},
                **_PAGINATION,
            },
            "required": ["cql"],
        },
    },
    # --- Pages ---
    {
        "name": "list-pages",
        "description": "List pages (or blog posts) in a space, optionally filtered by exact title",
        "inputSchema": {
            "type": "object",
            "properties": {
                "space_key": _SPACE_KEY,
                "title": {"type": "string", "description": "Exact page title"},
                "content_type": {"type": "string", "enum": ["page", "blogpost"], "description": "Content type", "default": "page"},
                **_PAGINATION,
            },
            "required": ["space_key"],
        },
    },
    {
        "name": "get-page",
        "description": "Read a page by ID, including its body content (as clean text by default), space, version and ancestors",
        "inputSchema": {
            "type": "object",
            "properties": {
                "page_id": _PAGE_ID,
                "format": _FORMAT,
                "version": {"type": "integer", "description": "Read a specific historical version (default: latest)"},
                "max_chars": _MAX_CHARS,
            },
            "required": ["page_id"],
        },
    },
    {
        "name": "get-page-by-title",
        "description": "Read a page by its exact title within a space",
        "inputSchema": {
            "type": "object",
            "properties": {
                "space_key": _SPACE_KEY,
                "title": {"type": "string", "description": "Exact page title"},
                "format": _FORMAT,
                "max_chars": _MAX_CHARS,
            },
            "required": ["space_key", "title"],
        },
    },
    {
        "name": "get-page-children",
        "description": "List the direct child pages of a page",
        "inputSchema": {
            "type": "object",
            "properties": {"page_id": _PAGE_ID, **_PAGINATION},
            "required": ["page_id"],
        },
    },
    {
        "name": "get-page-descendants",
        "description": "List all pages below a page (children, grandchildren, ...), useful to load a whole page tree",
        "inputSchema": {
            "type": "object",
            "properties": {"page_id": _PAGE_ID, **_PAGINATION},
            "required": ["page_id"],
        },
    },
    {
        "name": "get-page-history",
        "description": "Get the history of a page: creator, creation date and last update",
        "inputSchema": {
            "type": "object",
            "properties": {"page_id": _PAGE_ID},
            "required": ["page_id"],
        },
    },
    {
        "name": "create-page",
        "description": "Create a new page (or blog post) in a space, optionally under a parent page",
        "inputSchema": {
            "type": "object",
            "properties": {
                "space_key": _SPACE_KEY,
                "title": {"type": "string", "description": "Page title"},
                "body": {"type": "string", "description": "Page content"},
                "representation": _REPRESENTATION,
                "parent_id": {"type": "string", "description": "Parent page ID (default: top level of the space)"},
                "content_type": {"type": "string", "enum": ["page", "blogpost"], "description": "Content type", "default": "page"},
            },
            "required": ["space_key", "title", "body"],
        },
    },
    {
        "name": "update-page",
        "description": "Update a page's title and/or body, or move it under another parent. The version number is incremented automatically. Read the page with format 'storage' first to preserve formatting.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "page_id": _PAGE_ID,
                "title": {"type": "string", "description": "New title (default: keep current)"},
                "body": {"type": "string", "description": "New full page content (default: keep current)"},
                "representation": _REPRESENTATION,
                "parent_id": {"type": "string", "description": "Move the page under this parent page ID"},
                "version_message": {"type": "string", "description": "Version comment"},
                "minor_edit": {"type": "boolean", "description": "Minor edit (no notifications)", "default": False},
            },
            "required": ["page_id"],
        },
    },
    {
        "name": "delete-page",
        "description": "Delete a page (moves it to the space trash)",
        "inputSchema": {
            "type": "object",
            "properties": {"page_id": _PAGE_ID},
            "required": ["page_id"],
        },
    },
    # --- Comments ---
    {
        "name": "list-page-comments",
        "description": "List comments on a page (as plain text)",
        "inputSchema": {
            "type": "object",
            "properties": {"page_id": _PAGE_ID, **_PAGINATION},
            "required": ["page_id"],
        },
    },
    {
        "name": "add-page-comment",
        "description": "Add a comment to a page",
        "inputSchema": {
            "type": "object",
            "properties": {
                "page_id": _PAGE_ID,
                "body": {"type": "string", "description": "Comment content"},
                "representation": _REPRESENTATION,
            },
            "required": ["page_id", "body"],
        },
    },
    # --- Labels ---
    {
        "name": "list-page-labels",
        "description": "List labels on a page",
        "inputSchema": {
            "type": "object",
            "properties": {"page_id": _PAGE_ID, **_PAGINATION},
            "required": ["page_id"],
        },
    },
    {
        "name": "add-page-labels",
        "description": "Add one or more labels to a page",
        "inputSchema": {
            "type": "object",
            "properties": {
                "page_id": _PAGE_ID,
                "labels": {"type": "string", "description": "Comma-separated label names (lowercase, no spaces)"},
            },
            "required": ["page_id", "labels"],
        },
    },
    {
        "name": "remove-page-label",
        "description": "Remove a label from a page",
        "inputSchema": {
            "type": "object",
            "properties": {
                "page_id": _PAGE_ID,
                "label": {"type": "string", "description": "Label name"},
            },
            "required": ["page_id", "label"],
        },
    },
    # --- Attachments ---
    {
        "name": "list-attachments",
        "description": "List files attached to a page",
        "inputSchema": {
            "type": "object",
            "properties": {
                "page_id": _PAGE_ID,
                "file_name": {"type": "string", "description": "Filter by exact file name"},
                **_PAGINATION,
            },
            "required": ["page_id"],
        },
    },
    {
        "name": "get-attachment-content",
        "description": "Download a text attachment (txt, csv, json, yaml, xml, md, ...) of a page and return its content",
        "inputSchema": {
            "type": "object",
            "properties": {
                "page_id": _PAGE_ID,
                "file_name": {"type": "string", "description": "Attachment file name"},
                "attachment_id": {"type": "string", "description": "Attachment ID (alternative to file_name)"},
                "max_chars": {"type": "integer", "description": "Truncate content to this many characters (0 = no limit)", "default": 50000},
            },
            "required": ["page_id"],
        },
    },
    {
        "name": "upload-attachment",
        "description": "Upload a text file as an attachment to a page (creates a new version if the file already exists)",
        "inputSchema": {
            "type": "object",
            "properties": {
                "page_id": _PAGE_ID,
                "file_name": {"type": "string", "description": "File name (e.g. 'report.csv')"},
                "content": {"type": "string", "description": "File content (text)"},
                "media_type": {"type": "string", "description": "MIME type", "default": "text/plain"},
                "comment": {"type": "string", "description": "Attachment comment"},
            },
            "required": ["page_id", "file_name", "content"],
        },
    },
]

# Tools that modify Confluence state - disabled when CONFLUENCE_READ_ONLY is enabled
WRITE_TOOLS = {
    "create-page",
    "update-page",
    "delete-page",
    "add-page-comment",
    "add-page-labels",
    "remove-page-label",
    "upload-attachment",
}


# ---------------------------------------------------------------------------
# Tool helpers
# ---------------------------------------------------------------------------

def _format_page(page: Dict[str, Any], body_format: str, max_chars: Any) -> str:
    """Format a page (fetched with body expanded) for tool output."""
    body = page.get("body") or {}
    if body_format == "view":
        content = (body.get("view") or {}).get("value")
    else:
        content = (body.get("storage") or {}).get("value")
        if body_format == "text" and content is not None:
            content = _html_to_text(content)
    content, truncated = _truncate(content, max_chars)

    result = _summarize_content(page)
    result["ancestors"] = [{"id": a.get("id"), "title": a.get("title")} for a in page.get("ancestors") or []]
    result["labels"] = [label.get("name") for label in ((page.get("metadata") or {}).get("labels") or {}).get("results") or []]
    result["format"] = body_format
    result["content"] = content
    if truncated:
        result["truncated"] = True
    return json.dumps(result, indent=2)


def _page_expand(body_format: str) -> str:
    body_expand = "body.view" if body_format == "view" else "body.storage"
    return f"{_CONTENT_EXPAND},ancestors,metadata.labels,{body_expand}"


def _find_attachment(session_id: str, page_id: str, file_name: str) -> Optional[Dict[str, Any]]:
    """Find an attachment on a page by file name."""
    data, _ = _confluence_request(session_id, "GET", f"/content/{page_id}/child/attachment", params={
        "filename": file_name,
        "expand": "version,metadata",
    })
    for attachment in (data or {}).get("results") or []:
        if attachment.get("title") == file_name:
            return attachment
    return None


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
                    "name": "confluence-mcp-server",
                    "version": "1.0.0",
                },
            },
        }
    )
    response.headers["MCP-Session-Id"] = session_id
    return response


async def handle_tools_list(request_id: Any) -> JSONResponse:
    """Handle tools/list method - returns all available Confluence tools."""
    tools = TOOLS
    if get_bool("CONFLUENCE_READ_ONLY", False):
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
    """Handle tools/call method - executes Confluence operations."""
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
        # Confluence/tool errors are returned as tool results so the agent can read and react to them
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
    """Execute a Confluence tool operation."""
    if tool_name in WRITE_TOOLS and get_bool("CONFLUENCE_READ_ONLY", False):
        raise HTTPException(status_code=403, detail=f"Tool {tool_name} is disabled: server is in read-only mode (CONFLUENCE_READ_ONLY)")

    a = arguments
    try:
        # --- Users ---
        if tool_name == "get-current-user":
            user, _ = _confluence_request(session_id, "GET", "/user/current")
            return json.dumps({
                "type": user.get("type"),
                "username": user.get("username"),
                "account_id": user.get("accountId"),
                "display_name": user.get("displayName"),
                "email": user.get("email"),
            }, indent=2)

        # --- Spaces ---
        elif tool_name == "list-spaces":
            data, _ = _confluence_request(session_id, "GET", "/space", params={
                "type": a.get("type"),
                "status": a.get("status"),
                "expand": "description.plain,homepage",
                **_page_params(a),
            })
            return _list_result("spaces", data, _summarize_space)

        elif tool_name == "get-space":
            space, _ = _confluence_request(session_id, "GET", f"/space/{a['space_key']}", params={
                "expand": "description.plain,homepage",
            })
            return json.dumps(_summarize_space(space), indent=2)

        # --- Search ---
        elif tool_name == "search-content":
            field = "title" if a.get("title_only") else "text"
            clauses = [f"{field} ~ {_cql_quote(a['query'])}", f"type = {a.get('content_type') or 'page'}"]
            if a.get("space_key"):
                clauses.append(f"space = {_cql_quote(a['space_key'])}")
            if a.get("label"):
                clauses.append(f"label = {_cql_quote(a['label'])}")
            cql = " and ".join(clauses) + " order by lastmodified desc"
            data, _ = _confluence_request(session_id, "GET", "/content/search", params={
                "cql": cql,
                "expand": _CONTENT_EXPAND,
                **_page_params(a),
            })
            result = json.loads(_list_result("results", data, _summarize_content))
            result["cql"] = cql
            return json.dumps(result, indent=2)

        elif tool_name == "cql-search":
            data, _ = _confluence_request(session_id, "GET", "/content/search", params={
                "cql": a["cql"],
                "expand": _CONTENT_EXPAND,
                **_page_params(a),
            })
            return _list_result("results", data, _summarize_content)

        # --- Pages ---
        elif tool_name == "list-pages":
            data, _ = _confluence_request(session_id, "GET", "/content", params={
                "spaceKey": a["space_key"],
                "type": a.get("content_type") or "page",
                "title": a.get("title"),
                "expand": _CONTENT_EXPAND,
                **_page_params(a),
            })
            return _list_result("pages", data, _summarize_content)

        elif tool_name == "get-page":
            body_format = a.get("format") or "text"
            params: Dict[str, Any] = {"expand": _page_expand(body_format)}
            if a.get("version"):
                params.update({"version": a["version"], "status": "historical"})
            page, _ = _confluence_request(session_id, "GET", f"/content/{a['page_id']}", params=params)
            return _format_page(page, body_format, a.get("max_chars"))

        elif tool_name == "get-page-by-title":
            body_format = a.get("format") or "text"
            data, _ = _confluence_request(session_id, "GET", "/content", params={
                "spaceKey": a["space_key"],
                "title": a["title"],
                "type": "page",
                "expand": _page_expand(body_format),
            })
            pages = (data or {}).get("results") or []
            if not pages:
                raise HTTPException(status_code=404, detail=f"Page '{a['title']}' not found in space {a['space_key']}")
            return _format_page(pages[0], body_format, a.get("max_chars"))

        elif tool_name == "get-page-children":
            data, _ = _confluence_request(session_id, "GET", f"/content/{a['page_id']}/child/page", params={
                "expand": _CONTENT_EXPAND,
                **_page_params(a),
            })
            return _list_result("pages", data, _summarize_content)

        elif tool_name == "get-page-descendants":
            # CQL 'ancestor' works on both Cloud and Server/Data Center
            data, _ = _confluence_request(session_id, "GET", "/content/search", params={
                "cql": f"ancestor = {_cql_quote(a['page_id'])} and type = page",
                "expand": f"{_CONTENT_EXPAND},ancestors",
                **_page_params(a),
            })
            pages = []
            for page in (data or {}).get("results") or []:
                summary = _summarize_content(page)
                summary["parent_id"] = ((page.get("ancestors") or [{}])[-1]).get("id")
                summary["depth"] = len(page.get("ancestors") or [])
                pages.append(summary)
            return json.dumps({"pages": pages, "count": len(pages), "pagination": _pagination(data)}, indent=2)

        elif tool_name == "get-page-history":
            history, _ = _confluence_request(session_id, "GET", f"/content/{a['page_id']}/history", params={
                "expand": "lastUpdated,previousVersion,contributors.publishers.users",
            })
            last_updated = history.get("lastUpdated") or {}
            publishers = ((history.get("contributors") or {}).get("publishers") or {}).get("users") or []
            return json.dumps({
                "page_id": a["page_id"],
                "latest": history.get("latest"),
                "created_by": _user_name(history.get("createdBy")),
                "created_date": history.get("createdDate"),
                "last_updated": {
                    "version": last_updated.get("number"),
                    "when": last_updated.get("when"),
                    "by": _user_name(last_updated.get("by")),
                    "message": last_updated.get("message"),
                },
                "previous_version": (history.get("previousVersion") or {}).get("number"),
                "contributors": [_user_name(u) for u in publishers],
            }, indent=2)

        elif tool_name == "create-page":
            payload: Dict[str, Any] = {
                "type": a.get("content_type") or "page",
                "title": a["title"],
                "space": {"key": a["space_key"]},
                "body": _body_payload(a["body"], a.get("representation") or "storage"),
            }
            if a.get("parent_id"):
                payload["ancestors"] = [{"id": str(a["parent_id"])}]
            page, _ = _confluence_request(session_id, "POST", "/content", body=payload)
            return json.dumps({"message": f"Page '{page.get('title')}' created successfully",
                               "page": _summarize_content(page)}, indent=2)

        elif tool_name == "update-page":
            page_id = a["page_id"]
            current, _ = _confluence_request(session_id, "GET", f"/content/{page_id}", params={
                "expand": "version,space,body.storage",
            })
            if a.get("body") is not None:
                body = _body_payload(a["body"], a.get("representation") or "storage")
            else:
                body = {"storage": {"value": ((current.get("body") or {}).get("storage") or {}).get("value", ""),
                                    "representation": "storage"}}
            payload = {
                "id": page_id,
                "type": current.get("type", "page"),
                "title": a.get("title") or current.get("title"),
                "space": {"key": (current.get("space") or {}).get("key")},
                "body": body,
                "version": {
                    "number": ((current.get("version") or {}).get("number") or 0) + 1,
                    "minorEdit": bool(a.get("minor_edit", False)),
                },
            }
            if a.get("version_message"):
                payload["version"]["message"] = a["version_message"]
            if a.get("parent_id"):
                payload["ancestors"] = [{"id": str(a["parent_id"])}]
            page, _ = _confluence_request(session_id, "PUT", f"/content/{page_id}", body=payload)
            return json.dumps({"message": f"Page '{page.get('title')}' updated to version {(page.get('version') or {}).get('number')}",
                               "page": _summarize_content(page)}, indent=2)

        elif tool_name == "delete-page":
            _confluence_request(session_id, "DELETE", f"/content/{a['page_id']}")
            return json.dumps({"message": f"Page {a['page_id']} deleted (moved to trash)"}, indent=2)

        # --- Comments ---
        elif tool_name == "list-page-comments":
            data, _ = _confluence_request(session_id, "GET", f"/content/{a['page_id']}/child/comment", params={
                "expand": "body.storage,version,history",
                "depth": "all",
                **_page_params(a),
            })
            return _list_result("comments", data, _summarize_comment)

        elif tool_name == "add-page-comment":
            comment, _ = _confluence_request(session_id, "POST", "/content", body={
                "type": "comment",
                "container": {"id": str(a["page_id"]), "type": "page"},
                "body": _body_payload(a["body"], a.get("representation") or "storage"),
            })
            return json.dumps({"message": f"Comment added to page {a['page_id']}",
                               "comment_id": comment.get("id"),
                               "web_url": _web_url(comment)}, indent=2)

        # --- Labels ---
        elif tool_name == "list-page-labels":
            data, _ = _confluence_request(session_id, "GET", f"/content/{a['page_id']}/label", params=_page_params(a))
            return _list_result("labels", data, _summarize_label)

        elif tool_name == "add-page-labels":
            labels = _split_labels(a["labels"])
            if not labels:
                raise HTTPException(status_code=400, detail="No labels given")
            data, _ = _confluence_request(session_id, "POST", f"/content/{a['page_id']}/label",
                                          body=[{"prefix": "global", "name": label} for label in labels])
            return json.dumps({"message": f"Labels added to page {a['page_id']}",
                               "labels": [label.get("name") for label in (data or {}).get("results") or []]}, indent=2)

        elif tool_name == "remove-page-label":
            _confluence_request(session_id, "DELETE", f"/content/{a['page_id']}/label", params={"name": a["label"]})
            return json.dumps({"message": f"Label '{a['label']}' removed from page {a['page_id']}"}, indent=2)

        # --- Attachments ---
        elif tool_name == "list-attachments":
            data, _ = _confluence_request(session_id, "GET", f"/content/{a['page_id']}/child/attachment", params={
                "filename": a.get("file_name"),
                "expand": "version,metadata",
                **_page_params(a),
            })
            return _list_result("attachments", data, _summarize_attachment)

        elif tool_name == "get-attachment-content":
            page_id = a["page_id"]
            attachment = None
            if a.get("attachment_id"):
                data, _ = _confluence_request(session_id, "GET", f"/content/{page_id}/child/attachment", params={
                    "expand": "version,metadata", "limit": 100,
                })
                attachment = next((att for att in (data or {}).get("results") or []
                                   if str(att.get("id")) == str(a["attachment_id"])), None)
            elif a.get("file_name"):
                attachment = _find_attachment(session_id, page_id, a["file_name"])
            else:
                raise HTTPException(status_code=400, detail="Either file_name or attachment_id is required")
            if not attachment:
                raise HTTPException(status_code=404, detail=f"Attachment not found on page {page_id}")

            summary = _summarize_attachment(attachment)
            if not summary["download_url"]:
                raise HTTPException(status_code=502, detail="Attachment has no download link")
            raw_content, headers = _confluence_request(session_id, "GET", url=summary["download_url"], raw=True)
            try:
                content: Optional[str] = raw_content.decode("utf-8")
                note = None
            except UnicodeDecodeError:
                content = None
                note = f"Binary file ({headers.get('Content-Type', 'unknown type')}) - content not shown"
            content, truncated = _truncate(content, a.get("max_chars", 50000))
            result = {**summary, "size_bytes": len(raw_content), "content": content, "truncated": truncated}
            if note:
                result["note"] = note
            return json.dumps(result, indent=2)

        elif tool_name == "upload-attachment":
            page_id = a["page_id"]
            file_name = a["file_name"]
            files = {"file": (file_name, a["content"].encode("utf-8"), a.get("media_type") or "text/plain")}
            form_headers = {"X-Atlassian-Token": "no-check"}
            existing = _find_attachment(session_id, page_id, file_name)
            if existing:
                path = f"/content/{page_id}/child/attachment/{existing['id']}/data"
            else:
                path = f"/content/{page_id}/child/attachment"
            if a.get("comment"):
                files["comment"] = (None, a["comment"])
            data, _ = _confluence_request(session_id, "POST", path, files=files, headers=form_headers)
            uploaded = (data.get("results") or [data])[0] if isinstance(data, dict) else {}
            return json.dumps({"message": f"Attachment {file_name} {'updated' if existing else 'uploaded'} on page {page_id}",
                               "attachment": _summarize_attachment(uploaded)}, indent=2)

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
    limit: Optional[int] = None
    start: Optional[int] = None


class PageRequest(BaseModel):
    page_id: str


class PagePaginationRequest(PageRequest, PaginationRequest):
    pass


class ListSpacesRequest(PaginationRequest):
    type: Optional[str] = None
    status: Optional[str] = None


class SpaceRequest(BaseModel):
    space_key: str


class SearchContentRequest(PaginationRequest):
    query: str
    space_key: Optional[str] = None
    content_type: Optional[str] = None
    label: Optional[str] = None
    title_only: Optional[bool] = None


class CqlSearchRequest(PaginationRequest):
    cql: str


class ListPagesRequest(SpaceRequest, PaginationRequest):
    title: Optional[str] = None
    content_type: Optional[str] = None


class GetPageRequest(PageRequest):
    format: Optional[str] = None
    version: Optional[int] = None
    max_chars: Optional[int] = None


class GetPageByTitleRequest(SpaceRequest):
    title: str
    format: Optional[str] = None
    max_chars: Optional[int] = None


class CreatePageRequest(SpaceRequest):
    title: str
    body: str
    representation: Optional[str] = None
    parent_id: Optional[str] = None
    content_type: Optional[str] = None


class UpdatePageRequest(PageRequest):
    title: Optional[str] = None
    body: Optional[str] = None
    representation: Optional[str] = None
    parent_id: Optional[str] = None
    version_message: Optional[str] = None
    minor_edit: Optional[bool] = None


class AddPageCommentRequest(PageRequest):
    body: str
    representation: Optional[str] = None


class AddPageLabelsRequest(PageRequest):
    labels: str


class RemovePageLabelRequest(PageRequest):
    label: str


class ListAttachmentsRequest(PagePaginationRequest):
    file_name: Optional[str] = None


class GetAttachmentContentRequest(PageRequest):
    file_name: Optional[str] = None
    attachment_id: Optional[str] = None
    max_chars: Optional[int] = None


class UploadAttachmentRequest(PageRequest):
    file_name: str
    content: str
    media_type: Optional[str] = None
    comment: Optional[str] = None


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
    """Get the user the configured credentials belong to."""
    return await _run_tool_route("get-current-user")


# --- Spaces ---
@app.post("/tools/list-spaces", tags=["Spaces"])
async def list_spaces_route(request: ListSpacesRequest):
    """List spaces."""
    return await _run_tool_route("list-spaces", request)


@app.post("/tools/get-space", tags=["Spaces"])
async def get_space_route(request: SpaceRequest):
    """Get space details."""
    return await _run_tool_route("get-space", request)


# --- Search ---
@app.post("/tools/search-content", tags=["Search"])
async def search_content_route(request: SearchContentRequest):
    """Search content by text."""
    return await _run_tool_route("search-content", request)


@app.post("/tools/cql-search", tags=["Search"])
async def cql_search_route(request: CqlSearchRequest):
    """Search content with CQL."""
    return await _run_tool_route("cql-search", request)


# --- Pages ---
@app.post("/tools/list-pages", tags=["Pages"])
async def list_pages_route(request: ListPagesRequest):
    """List pages in a space."""
    return await _run_tool_route("list-pages", request)


@app.post("/tools/get-page", tags=["Pages"])
async def get_page_route(request: GetPageRequest):
    """Read a page by ID."""
    return await _run_tool_route("get-page", request)


@app.post("/tools/get-page-by-title", tags=["Pages"])
async def get_page_by_title_route(request: GetPageByTitleRequest):
    """Read a page by title."""
    return await _run_tool_route("get-page-by-title", request)


@app.post("/tools/get-page-children", tags=["Pages"])
async def get_page_children_route(request: PagePaginationRequest):
    """List child pages."""
    return await _run_tool_route("get-page-children", request)


@app.post("/tools/get-page-descendants", tags=["Pages"])
async def get_page_descendants_route(request: PagePaginationRequest):
    """List all descendant pages."""
    return await _run_tool_route("get-page-descendants", request)


@app.post("/tools/get-page-history", tags=["Pages"])
async def get_page_history_route(request: PageRequest):
    """Get page history."""
    return await _run_tool_route("get-page-history", request)


@app.post("/tools/create-page", tags=["Pages"])
async def create_page_route(request: CreatePageRequest):
    """Create a page."""
    return await _run_tool_route("create-page", request)


@app.post("/tools/update-page", tags=["Pages"])
async def update_page_route(request: UpdatePageRequest):
    """Update a page."""
    return await _run_tool_route("update-page", request)


@app.post("/tools/delete-page", tags=["Pages"])
async def delete_page_route(request: PageRequest):
    """Delete a page."""
    return await _run_tool_route("delete-page", request)


# --- Comments ---
@app.post("/tools/list-page-comments", tags=["Comments"])
async def list_page_comments_route(request: PagePaginationRequest):
    """List page comments."""
    return await _run_tool_route("list-page-comments", request)


@app.post("/tools/add-page-comment", tags=["Comments"])
async def add_page_comment_route(request: AddPageCommentRequest):
    """Comment on a page."""
    return await _run_tool_route("add-page-comment", request)


# --- Labels ---
@app.post("/tools/list-page-labels", tags=["Labels"])
async def list_page_labels_route(request: PagePaginationRequest):
    """List page labels."""
    return await _run_tool_route("list-page-labels", request)


@app.post("/tools/add-page-labels", tags=["Labels"])
async def add_page_labels_route(request: AddPageLabelsRequest):
    """Add labels to a page."""
    return await _run_tool_route("add-page-labels", request)


@app.post("/tools/remove-page-label", tags=["Labels"])
async def remove_page_label_route(request: RemovePageLabelRequest):
    """Remove a label from a page."""
    return await _run_tool_route("remove-page-label", request)


# --- Attachments ---
@app.post("/tools/list-attachments", tags=["Attachments"])
async def list_attachments_route(request: ListAttachmentsRequest):
    """List page attachments."""
    return await _run_tool_route("list-attachments", request)


@app.post("/tools/get-attachment-content", tags=["Attachments"])
async def get_attachment_content_route(request: GetAttachmentContentRequest):
    """Download a text attachment."""
    return await _run_tool_route("get-attachment-content", request)


@app.post("/tools/upload-attachment", tags=["Attachments"])
async def upload_attachment_route(request: UploadAttachmentRequest):
    """Upload a text attachment."""
    return await _run_tool_route("upload-attachment", request)


@app.get("/")
async def root():
    """Root endpoint with API information."""
    return {
        "service": "Confluence MCP Server",
        "version": "1.0.0",
        "confluence_url": get_str("CONFLUENCE_URL"),
        "auth": _auth_mode(),
        "read_only": get_bool("CONFLUENCE_READ_ONLY", False),
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

    Calls the Confluence /user/current endpoint with the configured credentials to
    verify both connectivity and authentication.
    """
    try:
        client = _create_confluence_client()
        resp = client.get(f"{_confluence_api_base()}/user/current", timeout=5)
        if resp.status_code >= 400:
            return JSONResponse(
                status_code=503,
                content={
                    "status": "unhealthy",
                    "reason": f"Confluence API error {resp.status_code}: {_error_message(resp)}",
                    "auth": _auth_mode(),
                    **({"hint": _auth_hint()} if resp.status_code in (401, 403) else {}),
                },
            )
        user = resp.json()
        if get_str("CONFLUENCE_TOKEN") and user.get("type") == "anonymous":
            return JSONResponse(
                status_code=503,
                content={
                    "status": "unhealthy",
                    "reason": "Authentication failed - credentials were not accepted (anonymous user)",
                    "auth": _auth_mode(),
                    "hint": _auth_hint(),
                },
            )
        return JSONResponse(content={"status": "healthy", "user": _user_name(user)})
    except HTTPException as e:
        return JSONResponse(status_code=503, content={"status": "unhealthy", "reason": e.detail})
    except Exception as e:
        logger.error(f"Health check failed: {e}")
        return JSONResponse(
            status_code=503, content={"status": "unhealthy", "reason": str(e)}
        )


def run() -> None:
    """Run the Confluence MCP Server."""
    host = get_str("MCP_HOST", "0.0.0.0") or "0.0.0.0"
    port = get_int("MCP_PORT", 8000)

    import uvicorn

    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    run()
