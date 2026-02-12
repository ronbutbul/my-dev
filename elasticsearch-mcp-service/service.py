from __future__ import annotations

import json
import logging
import uuid
from typing import Any, Dict, List, Optional

from elasticsearch import Elasticsearch, NotFoundError
from fastapi import FastAPI, HTTPException, Header, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel

from config import get_str, get_int

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

app = FastAPI(
    title="Elasticsearch MCP Server",
    description="MCP server for Elasticsearch operations",
    version="1.0.0",
)

# Session storage (in production, use Redis or similar)
sessions: Dict[str, Dict[str, Any]] = {}

# Elasticsearch client cache per session
es_clients: Dict[str, Elasticsearch] = {}


def _create_es_client() -> Elasticsearch:
    """Create and return an Elasticsearch client with optional authentication."""
    es_url = get_str("ES_MCP_CONNECTION_STRING", "http://localhost:9200")
    use_auth = get_str("ES_USE_AUTH", "false")
    username = get_str("ES_USERNAME")
    password = get_str("ES_PASSWORD")

    kwargs: Dict[str, Any] = {"hosts": [es_url]}

    if use_auth and use_auth.lower() in ("true", "1", "yes", "on"):
        if not username or not password:
            raise RuntimeError(
                "ES_USERNAME and ES_PASSWORD are required when ES_USE_AUTH is true"
            )
        kwargs["basic_auth"] = (username, password)

    try:
        client = Elasticsearch(**kwargs)
        # Test the connection
        client.info()
        logger.info("Successfully connected to Elasticsearch at %s", es_url)
    except Exception as e:
        logger.error("Failed to connect to Elasticsearch: %s", e)
        raise

    return client


def _get_es_client(session_id: str) -> Elasticsearch:
    """Get or create Elasticsearch client for a session."""
    if session_id not in es_clients:
        try:
            client = _create_es_client()
            es_clients[session_id] = client
            logger.info(f"Created Elasticsearch client for session {session_id}")
        except Exception as e:
            logger.error(f"Failed to connect to Elasticsearch: {e}")
            raise HTTPException(
                status_code=500,
                detail=f"Failed to connect to Elasticsearch: {str(e)}",
            )
    return es_clients[session_id]


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
                    "name": "elasticsearch-mcp-server",
                    "version": "1.0.0",
                },
            },
        }
    )
    response.headers["MCP-Session-Id"] = session_id
    return response


async def handle_tools_list(request_id: Any) -> JSONResponse:
    """Handle tools/list method - returns all available Elasticsearch tools."""
    tools = [
        {
            "name": "search",
            "description": "Run a search query against an Elasticsearch index",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "index": {"type": "string", "description": "Index name"},
                    "query": {
                        "type": "object",
                        "description": "Elasticsearch query DSL body (optional)",
                    },
                    "from": {
                        "type": "integer",
                        "description": "Offset from the first result you want to fetch (optional)",
                    },
                    "size": {
                        "type": "integer",
                        "description": "Number of hits to return (optional)",
                    },
                    "sort": {
                        "type": "array",
                        "description": "Sort specification, e.g. [{\"@timestamp\": {\"order\": \"desc\"}}]",
                        "items": {"type": "object"},
                    },
                    "source": {
                        "description": "Source filtering configuration. Either an array of field names to include or an object with includes/excludes, e.g. [\"field1\", \"field2\"] or {\"includes\": [\"field1\"], \"excludes\": [\"field2\"]}.",
                        "anyOf": [
                            {
                                "type": "array",
                                "items": {"type": "string"}
                            },
                            {
                                "type": "object"
                            }
                        ]
                    },
                },
                "required": ["index"],
            },
        },
        {
            "name": "count",
            "description": "Return the number of documents matching a query in an index",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "index": {"type": "string", "description": "Index name"},
                    "query": {
                        "type": "object",
                        "description": "Elasticsearch query DSL body (optional)",
                    },
                },
                "required": ["index"],
            },
        },
        {
            "name": "get-document",
            "description": "Get a single document by ID",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "index": {"type": "string", "description": "Index name"},
                    "id": {"type": "string", "description": "Document ID"},
                },
                "required": ["index", "id"],
            },
        },
        {
            "name": "index-document",
            "description": "Index (create or replace) a single document",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "index": {"type": "string", "description": "Index name"},
                    "id": {
                        "type": "string",
                        "description": "Document ID (optional, autogenerated if omitted)",
                    },
                    "document": {
                        "type": "object",
                        "description": "Document body",
                    },
                },
                "required": ["index", "document"],
            },
        },
        {
            "name": "delete-document",
            "description": "Delete a single document by ID",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "index": {"type": "string", "description": "Index name"},
                    "id": {"type": "string", "description": "Document ID"},
                },
                "required": ["index", "id"],
            },
        },
        {
            "name": "update-document",
            "description": "Partially update a document by ID",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "index": {"type": "string", "description": "Index name"},
                    "id": {"type": "string", "description": "Document ID"},
                    "doc": {
                        "type": "object",
                        "description": "Partial document with fields to update",
                    },
                },
                "required": ["index", "id", "doc"],
            },
        },
        {
            "name": "list-indices",
            "description": "List all indices in the cluster",
            "inputSchema": {"type": "object", "properties": {}},
        },
        {
            "name": "index-mappings",
            "description": "Get mappings for an index",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "index": {"type": "string", "description": "Index name"},
                },
                "required": ["index"],
            },
        },
        {
            "name": "index-stats",
            "description": "Get statistics for an index",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "index": {"type": "string", "description": "Index name"},
                },
                "required": ["index"],
            },
        },
        {
            "name": "cluster-health",
            "description": "Get cluster health information",
            "inputSchema": {"type": "object", "properties": {}},
        },
        {
            "name": "cluster-stats",
            "description": "Get cluster statistics",
            "inputSchema": {"type": "object", "properties": {}},
        },
        {
            "name": "switch-connection",
            "description": "Switch to a different Elasticsearch connection",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "connectionString": {
                        "type": "string",
                        "description": "New Elasticsearch URL, e.g., http://localhost:9200",
                    },
                },
                "required": ["connectionString"],
            },
        },
    ]

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
    """Handle tools/call method - executes Elasticsearch operations."""
    tool_name = params.get("name")
    arguments = params.get("arguments", {})

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
        client = _get_es_client(session_id)
        result = await execute_tool(tool_name, arguments, client, session_id)
        # kagent expects content type "text" with field "text" (not "json"); keep as string
        return JSONResponse(
            content={
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {"content": [{"type": "text", "text": result}]},
            }
        )
    except HTTPException:
        raise
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


async def execute_tool(
    tool_name: str, arguments: Dict[str, Any], client: Elasticsearch, session_id: str
) -> str:
    """Execute an Elasticsearch tool operation."""
    try:
        if tool_name == "search":
            index = arguments["index"]
            query = arguments.get("query", {"match_all": {}})
            from_ = arguments.get("from")
            size = arguments.get("size")
            sort = arguments.get("sort")
            source = arguments.get("source")

            search_args: Dict[str, Any] = {"index": index, "body": {"query": query}}
            if from_ is not None:
                search_args["from_"] = from_
            if size is not None:
                search_args["size"] = size
            if sort is not None:
                search_args["body"]["sort"] = sort
            if source is not None:
                search_args["body"]["_source"] = source

            resp = client.search(**search_args)
            return json.dumps(resp, default=str, indent=2)

        elif tool_name == "count":
            index = arguments["index"]
            query = arguments.get("query", {"match_all": {}})
            resp = client.count(index=index, body={"query": query})
            return json.dumps(resp, default=str, indent=2)

        elif tool_name == "get-document":
            index = arguments["index"]
            doc_id = arguments["id"]
            try:
                resp = client.get(index=index, id=doc_id)
                return json.dumps(resp, default=str, indent=2)
            except NotFoundError:
                return json.dumps(
                    {"found": False, "message": "Document not found"}, indent=2
                )

        elif tool_name == "index-document":
            index = arguments["index"]
            document = arguments["document"]
            doc_id = arguments.get("id")
            if doc_id:
                resp = client.index(index=index, id=doc_id, document=document)
            else:
                resp = client.index(index=index, document=document)
            return json.dumps(resp, default=str, indent=2)

        elif tool_name == "delete-document":
            index = arguments["index"]
            doc_id = arguments["id"]
            try:
                resp = client.delete(index=index, id=doc_id)
                return json.dumps(resp, default=str, indent=2)
            except NotFoundError:
                return json.dumps(
                    {"found": False, "message": "Document not found"}, indent=2
                )

        elif tool_name == "update-document":
            index = arguments["index"]
            doc_id = arguments["id"]
            doc = arguments["doc"]
            body = {"doc": doc}
            resp = client.update(index=index, id=doc_id, body=body)
            return json.dumps(resp, default=str, indent=2)

        elif tool_name == "list-indices":
            resp = client.indices.get_alias(index="*")
            indices = sorted(resp.keys())
            return json.dumps({"indices": indices}, indent=2)

        elif tool_name == "index-mappings":
            index = arguments["index"]
            resp = client.indices.get_mapping(index=index)
            return json.dumps(resp, default=str, indent=2)

        elif tool_name == "index-stats":
            index = arguments["index"]
            resp = client.indices.stats(index=index)
            return json.dumps(resp, default=str, indent=2)

        elif tool_name == "cluster-health":
            resp = client.cluster.health()
            return json.dumps(resp, default=str, indent=2)

        elif tool_name == "cluster-stats":
            resp = client.cluster.stats()
            return json.dumps(resp, default=str, indent=2)

        elif tool_name == "switch-connection":
            connection_string = arguments["connectionString"]
            # Close old client and create new one
            if session_id in es_clients:
                es_clients[session_id].close()
            try:
                new_client = Elasticsearch(hosts=[connection_string])
                new_client.info()
                es_clients[session_id] = new_client
                logger.info(
                    f"Switched Elasticsearch connection for session {session_id}"
                )
                return json.dumps(
                    {"message": "Switched connection successfully"}, indent=2
                )
            except Exception as e:
                logger.error(f"Failed to switch Elasticsearch connection: {e}")
                raise HTTPException(
                    status_code=500,
                    detail=f"Failed to switch connection: {str(e)}",
                )

        else:
            raise ValueError(f"Unknown tool: {tool_name}")

    except KeyError as e:
        raise HTTPException(status_code=400, detail=f"Missing required argument: {str(e)}")
    except Exception as e:
        logger.error(f"Error in execute_tool: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Tool execution error: {str(e)}")


# Pydantic models for Swagger documentation
class SearchRequest(BaseModel):
    index: str
    query: Optional[Dict[str, Any]] = None
    from_: Optional[int] = None
    size: Optional[int] = None
    sort: Optional[List[Dict[str, Any]]] = None
    source: Optional[Any] = None


class CountRequest(BaseModel):
    index: str
    query: Optional[Dict[str, Any]] = None


class GetDocumentRequest(BaseModel):
    index: str
    id: str


class IndexDocumentRequest(BaseModel):
    index: str
    id: Optional[str] = None
    document: Dict[str, Any]


class DeleteDocumentRequest(BaseModel):
    index: str
    id: str


class UpdateDocumentRequest(BaseModel):
    index: str
    id: str
    doc: Dict[str, Any]


class IndexOnlyRequest(BaseModel):
    index: str


class SwitchConnectionRequest(BaseModel):
    connectionString: str


def _get_default_session() -> str:
    """Get or create a default session for Swagger testing."""
    default_session = "swagger-default"
    if default_session not in sessions:
        sessions[default_session] = {
            "protocolVersion": "2024-11-05",
            "clientInfo": {"name": "swagger", "version": "1.0.0"},
        }
    return default_session


@app.post("/tools/search", tags=["Elasticsearch Tools"])
async def search_route(request: SearchRequest):
    """Run a search query against an Elasticsearch index."""
    session_id = _get_default_session()
    client = _get_es_client(session_id)
    args: Dict[str, Any] = {
        "index": request.index,
    }
    if request.query is not None:
        args["query"] = request.query
    if request.from_ is not None:
        args["from"] = request.from_
    if request.size is not None:
        args["size"] = request.size
    if request.sort is not None:
        args["sort"] = request.sort
    if request.source is not None:
        args["source"] = request.source
    result = await execute_tool("search", args, client, session_id)
    return JSONResponse(content=json.loads(result))


@app.post("/tools/count", tags=["Elasticsearch Tools"])
async def count_route(request: CountRequest):
    """Return the number of documents matching a query in an index."""
    session_id = _get_default_session()
    client = _get_es_client(session_id)
    result = await execute_tool(
        "count",
        {
            "index": request.index,
            "query": request.query,
        },
        client,
        session_id,
    )
    return JSONResponse(content=json.loads(result))


@app.post("/tools/get-document", tags=["Elasticsearch Tools"])
async def get_document_route(request: GetDocumentRequest):
    """Get a single document by ID."""
    session_id = _get_default_session()
    client = _get_es_client(session_id)
    result = await execute_tool(
        "get-document",
        {
            "index": request.index,
            "id": request.id,
        },
        client,
        session_id,
    )
    return JSONResponse(content=json.loads(result))


@app.post("/tools/index-document", tags=["Elasticsearch Tools"])
async def index_document_route(request: IndexDocumentRequest):
    """Index (create or replace) a single document."""
    session_id = _get_default_session()
    client = _get_es_client(session_id)
    args: Dict[str, Any] = {
        "index": request.index,
        "document": request.document,
    }
    if request.id is not None:
        args["id"] = request.id
    result = await execute_tool(
        "index-document",
        args,
        client,
        session_id,
    )
    return JSONResponse(content=json.loads(result))


@app.post("/tools/delete-document", tags=["Elasticsearch Tools"])
async def delete_document_route(request: DeleteDocumentRequest):
    """Delete a single document by ID."""
    session_id = _get_default_session()
    client = _get_es_client(session_id)
    result = await execute_tool(
        "delete-document",
        {
            "index": request.index,
            "id": request.id,
        },
        client,
        session_id,
    )
    return JSONResponse(content=json.loads(result))


@app.post("/tools/update-document", tags=["Elasticsearch Tools"])
async def update_document_route(request: UpdateDocumentRequest):
    """Partially update a document by ID."""
    session_id = _get_default_session()
    client = _get_es_client(session_id)
    result = await execute_tool(
        "update-document",
        {
            "index": request.index,
            "id": request.id,
            "doc": request.doc,
        },
        client,
        session_id,
    )
    return JSONResponse(content=json.loads(result))


@app.post("/tools/list-indices", tags=["Elasticsearch Tools"])
async def list_indices_route():
    """List all indices in the cluster."""
    session_id = _get_default_session()
    client = _get_es_client(session_id)
    result = await execute_tool("list-indices", {}, client, session_id)
    return JSONResponse(content=json.loads(result))


@app.post("/tools/index-mappings", tags=["Elasticsearch Tools"])
async def index_mappings_route(request: IndexOnlyRequest):
    """Get mappings for an index."""
    session_id = _get_default_session()
    client = _get_es_client(session_id)
    result = await execute_tool(
        "index-mappings",
        {"index": request.index},
        client,
        session_id,
    )
    return JSONResponse(content=json.loads(result))


@app.post("/tools/index-stats", tags=["Elasticsearch Tools"])
async def index_stats_route(request: IndexOnlyRequest):
    """Get statistics for an index."""
    session_id = _get_default_session()
    client = _get_es_client(session_id)
    result = await execute_tool(
        "index-stats",
        {"index": request.index},
        client,
        session_id,
    )
    return JSONResponse(content=json.loads(result))


@app.post("/tools/cluster-health", tags=["Elasticsearch Tools"])
async def cluster_health_route():
    """Get cluster health information."""
    session_id = _get_default_session()
    client = _get_es_client(session_id)
    result = await execute_tool("cluster-health", {}, client, session_id)
    return JSONResponse(content=json.loads(result))


@app.post("/tools/cluster-stats", tags=["Elasticsearch Tools"])
async def cluster_stats_route():
    """Get cluster statistics."""
    session_id = _get_default_session()
    client = _get_es_client(session_id)
    result = await execute_tool("cluster-stats", {}, client, session_id)
    return JSONResponse(content=json.loads(result))


@app.post("/tools/switch-connection", tags=["Elasticsearch Tools"])
async def switch_connection_route(request: SwitchConnectionRequest):
    """Switch to a different Elasticsearch connection."""
    session_id = _get_default_session()
    # For switch-connection, we get a dummy client first, then the tool will replace it
    client = _get_es_client(session_id)
    result = await execute_tool(
        "switch-connection",
        {"connectionString": request.connectionString},
        client,
        session_id,
    )
    return JSONResponse(content=json.loads(result))


@app.get("/")
async def root():
    """Root endpoint with API information."""
    return {
        "service": "Elasticsearch MCP Server",
        "version": "1.0.0",
        "endpoints": {
            "/mcp": "POST - MCP JSON-RPC 2.0 endpoint",
            "/docs": "Swagger UI documentation",
            "/redoc": "ReDoc documentation",
        },
    }


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    try:
        test_client = _create_es_client()
        # Perform a lightweight check
        test_client.ping()
        return JSONResponse(content={"status": "healthy"})
    except Exception as e:
        logger.error(f"Health check failed: {e}")
        return JSONResponse(
            status_code=503, content={"status": "unhealthy", "reason": str(e)}
        )


def run() -> None:
    """Run the Elasticsearch MCP Server."""
    host = get_str("MCP_HOST", "0.0.0.0") or "0.0.0.0"
    port = get_int("MCP_PORT", 8000)

    import uvicorn

    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    run()

