from __future__ import annotations

import json
import logging
import secrets
import uuid
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Header, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel
from pymongo import MongoClient
from pymongo.collection import Collection
from pymongo.database import Database
from pymongo.errors import ConnectionFailure, OperationFailure, PyMongoError

from config import get_str, get_int, get_bool

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

app = FastAPI(
    title="MongoDB MCP Server",
    description="MCP server for MongoDB operations",
    version="1.0.0",
)

# Session storage (in production, use Redis or similar)
sessions: Dict[str, Dict[str, Any]] = {}

# MongoDB client cache per session
mongo_clients: Dict[str, MongoClient] = {}


def _create_mongo_client() -> MongoClient:
    """Create and return a MongoDB client with optional authentication."""
    mongo_url = get_str("MDB_MCP_CONNECTION_STRING", "mongodb://localhost:44069")
    use_auth = get_bool("MONGO_USE_AUTH", False)
    
    if use_auth:
        username = get_str("MONGO_USERNAME")
        password = get_str("MONGO_PASSWORD")
        if not username or not password:
            raise RuntimeError(
                "MONGO_USERNAME and MONGO_PASSWORD are required when MONGO_USE_AUTH is true"
            )
        # Parse the URL and add credentials if not already present
        if "@" not in mongo_url:
            # Insert credentials into URL
            if mongo_url.startswith("mongodb://"):
                mongo_url = mongo_url.replace("mongodb://", f"mongodb://{username}:{password}@", 1)
            elif mongo_url.startswith("mongodb+srv://"):
                mongo_url = mongo_url.replace("mongodb+srv://", f"mongodb+srv://{username}:{password}@", 1)
        # Extract host for logging (hide credentials)
        log_url = mongo_url.split("@")[-1] if "@" in mongo_url else mongo_url
        logger.info("Connecting to MongoDB with authentication at %s", log_url)
    else:
        logger.info("Connecting to MongoDB without authentication at %s", mongo_url)
    
    # Add directConnection=true to bypass replica set discovery when connecting from outside cluster
    # This prevents MongoDB from trying to connect to internal Kubernetes service names
    if "?" in mongo_url:
        if "directConnection=" not in mongo_url:
            mongo_url += "&directConnection=true"
    else:
        mongo_url += "?directConnection=true"
    
    try:
        client = MongoClient(mongo_url)
        # Test the connection
        client.admin.command("ping")
        logger.info("Successfully connected to MongoDB")
    except Exception as e:
        logger.error("Failed to connect to MongoDB: %s", e)
        raise
    
    return client


def _get_mongo_client(session_id: str) -> MongoClient:
    """Get or create MongoDB client for a session."""
    if session_id not in mongo_clients:
        try:
            client = _create_mongo_client()
            mongo_clients[session_id] = client
            logger.info(f"Created MongoDB client for session {session_id}")
        except Exception as e:
            logger.error(f"Failed to connect to MongoDB: {e}")
            raise HTTPException(status_code=500, detail=f"Failed to connect to MongoDB: {str(e)}")
    return mongo_clients[session_id]


def _get_database(client: MongoClient, db_name: str) -> Database:
    """Get a database instance."""
    return client[db_name]


def _get_collection(client: MongoClient, db_name: str, collection_name: str) -> Collection:
    """Get a collection instance."""
    db = _get_database(client, db_name)
    return db[collection_name]


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
    # Notifications don't have an id, so we handle them before session checks
    if method == "notifications/initialized":
        # This is a notification (no id), so we just acknowledge it silently
        # According to JSON-RPC 2.0, notifications don't require a response
        # But HTTP requires a response, so we return 200 OK with empty body
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
                    "name": "mongodb-mcp-server",
                    "version": "1.0.0",
                },
            },
        }
    )
    response.headers["MCP-Session-Id"] = session_id
    return response


async def handle_tools_list(request_id: Any) -> JSONResponse:
    """Handle tools/list method - returns all available MongoDB tools."""
    tools = [
        {
            "name": "aggregate",
            "description": "Run an aggregation against a MongoDB collection",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "database": {"type": "string", "description": "Database name"},
                    "collection": {"type": "string", "description": "Collection name"},
                    "pipeline": {
                        "type": "array",
                        "description": "Aggregation pipeline stages",
                        "items": {"type": "object"},
                    },
                },
                "required": ["database", "collection", "pipeline"],
            },
        },
        {
            "name": "collection-indexes",
            "description": "Describe the indexes for a collection",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "database": {"type": "string", "description": "Database name"},
                    "collection": {"type": "string", "description": "Collection name"},
                },
                "required": ["database", "collection"],
            },
        },
        {
            "name": "collection-schema",
            "description": "Describe the schema for a collection",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "database": {"type": "string", "description": "Database name"},
                    "collection": {"type": "string", "description": "Collection name"},
                },
                "required": ["database", "collection"],
            },
        },
        {
            "name": "collection-storage-size",
            "description": "Gets the size of the collection",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "database": {"type": "string", "description": "Database name"},
                    "collection": {"type": "string", "description": "Collection name"},
                },
                "required": ["database", "collection"],
            },
        },
        {
            "name": "connect",
            "description": "Connect to a MongoDB instance",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "connectionString": {
                        "type": "string",
                        "description": "MongoDB connection string",
                    },
                },
                "required": ["connectionString"],
            },
        },
        {
            "name": "count",
            "description": "Gets the number of documents in a MongoDB collection using db.collection.count() and query as an optional filter parameter",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "database": {"type": "string", "description": "Database name"},
                    "collection": {"type": "string", "description": "Collection name"},
                    "query": {
                        "type": "object",
                        "description": "Optional query filter",
                    },
                },
                "required": ["database", "collection"],
            },
        },
        {
            "name": "create-collection",
            "description": "Creates a new collection in a database. If the database doesn't exist, it will be created automatically.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "database": {"type": "string", "description": "Database name"},
                    "collection": {"type": "string", "description": "Collection name"},
                },
                "required": ["database", "collection"],
            },
        },
        {
            "name": "create-index",
            "description": "Create an index for a collection",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "database": {"type": "string", "description": "Database name"},
                    "collection": {"type": "string", "description": "Collection name"},
                    "keys": {
                        "type": "object",
                        "description": "Index keys specification",
                    },
                    "options": {
                        "type": "object",
                        "description": "Index options (optional)",
                    },
                },
                "required": ["database", "collection", "keys"],
            },
        },
        {
            "name": "db-stats",
            "description": "Returns statistics that reflect the use state of a single database",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "database": {"type": "string", "description": "Database name"},
                },
                "required": ["database"],
            },
        },
        {
            "name": "delete-many",
            "description": "Removes all documents that match the filter from a MongoDB collection",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "database": {"type": "string", "description": "Database name"},
                    "collection": {"type": "string", "description": "Collection name"},
                    "filter": {
                        "type": "object",
                        "description": "Filter to match documents",
                    },
                },
                "required": ["database", "collection", "filter"],
            },
        },
        {
            "name": "drop-collection",
            "description": "Removes a collection or view from the database. The method also removes any indexes associated with the dropped collection.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "database": {"type": "string", "description": "Database name"},
                    "collection": {"type": "string", "description": "Collection name"},
                },
                "required": ["database", "collection"],
            },
        },
        {
            "name": "drop-database",
            "description": "Removes the specified database, deleting the associated data files",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "database": {"type": "string", "description": "Database name"},
                },
                "required": ["database"],
            },
        },
        {
            "name": "drop-index",
            "description": "Drop an index for the provided database and collection.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "database": {"type": "string", "description": "Database name"},
                    "collection": {"type": "string", "description": "Collection name"},
                    "indexName": {"type": "string", "description": "Index name"},
                },
                "required": ["database", "collection", "indexName"],
            },
        },
        {
            "name": "explain",
            "description": "Returns statistics describing the execution of the winning plan chosen by the query optimizer for the evaluated method",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "database": {"type": "string", "description": "Database name"},
                    "collection": {"type": "string", "description": "Collection name"},
                    "method": {
                        "type": "string",
                        "description": "Method to explain (find, aggregate, etc.)",
                    },
                    "query": {
                        "type": "object",
                        "description": "Query or pipeline to explain",
                    },
                },
                "required": ["database", "collection", "method", "query"],
            },
        },
        {
            "name": "export",
            "description": "Export a query or aggregation results in the specified EJSON format.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "database": {"type": "string", "description": "Database name"},
                    "collection": {"type": "string", "description": "Collection name"},
                    "query": {
                        "type": "object",
                        "description": "Query filter (optional)",
                    },
                    "format": {
                        "type": "string",
                        "description": "Export format (ejson, json)",
                        "default": "ejson",
                    },
                },
                "required": ["database", "collection"],
            },
        },
        {
            "name": "find",
            "description": "Run a find query against a MongoDB collection",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "database": {"type": "string", "description": "Database name"},
                    "collection": {"type": "string", "description": "Collection name"},
                    "query": {
                        "type": "object",
                        "description": "Query filter (optional)",
                    },
                    "projection": {
                        "type": "object",
                        "description": "Projection specification (optional)",
                    },
                    "limit": {"type": "integer", "description": "Limit results (optional)"},
                    "skip": {"type": "integer", "description": "Skip results (optional)"},
                    "sort": {
                        "type": "object",
                        "description": "Sort specification (optional)",
                    },
                },
                "required": ["database", "collection"],
            },
        },
        {
            "name": "insert-many",
            "description": "Insert an array of documents into a MongoDB collection. If the list of documents is above com.mongodb/maxRequestPayloadBytes, consider inserting them in batches.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "database": {"type": "string", "description": "Database name"},
                    "collection": {"type": "string", "description": "Collection name"},
                    "documents": {
                        "type": "array",
                        "description": "Array of documents to insert",
                        "items": {"type": "object"},
                    },
                },
                "required": ["database", "collection", "documents"],
            },
        },
        {
            "name": "list-collections",
            "description": "List all collections for a given database",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "database": {"type": "string", "description": "Database name"},
                },
                "required": ["database"],
            },
        },
        {
            "name": "list-databases",
            "description": "List all databases for a MongoDB connection",
            "inputSchema": {"type": "object", "properties": {}},
        },
        {
            "name": "mongodb-logs",
            "description": "Returns the most recent logged mongod events",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "description": "Number of log entries (optional)"},
                },
            },
        },
        {
            "name": "rename-collection",
            "description": "Renames a collection in a MongoDB database",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "database": {"type": "string", "description": "Database name"},
                    "collection": {"type": "string", "description": "Current collection name"},
                    "newName": {"type": "string", "description": "New collection name"},
                },
                "required": ["database", "collection", "newName"],
            },
        },
        {
            "name": "switch-connection",
            "description": "Switch to a different MongoDB connection",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "connectionString": {
                        "type": "string",
                        "description": "New MongoDB connection string",
                    },
                },
                "required": ["connectionString"],
            },
        },
        {
            "name": "update-many",
            "description": "Updates all documents that match the specified filter for a collection. If the list of documents is above com.mongodb/maxRequestPayloadBytes, consider updating them in batches.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "database": {"type": "string", "description": "Database name"},
                    "collection": {"type": "string", "description": "Collection name"},
                    "filter": {
                        "type": "object",
                        "description": "Filter to match documents",
                    },
                    "update": {
                        "type": "object",
                        "description": "Update operations",
                    },
                },
                "required": ["database", "collection", "filter", "update"],
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
    """Handle tools/call method - executes MongoDB operations."""
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
        client = _get_mongo_client(session_id)
        result = await execute_tool(tool_name, arguments, client, session_id)
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


async def execute_tool(tool_name: str, arguments: Dict[str, Any], client: MongoClient, session_id: str) -> str:
    """Execute a MongoDB tool operation."""
    try:
        if tool_name == "aggregate":
            db_name = arguments["database"]
            collection_name = arguments["collection"]
            pipeline = arguments["pipeline"]
            collection = _get_collection(client, db_name, collection_name)
            results = list(collection.aggregate(pipeline))
            return json.dumps(results, default=str, indent=2)

        elif tool_name == "collection-indexes":
            db_name = arguments["database"]
            collection_name = arguments["collection"]
            collection = _get_collection(client, db_name, collection_name)
            indexes = list(collection.list_indexes())
            return json.dumps([idx for idx in indexes], default=str, indent=2)

        elif tool_name == "collection-schema":
            db_name = arguments["database"]
            collection_name = arguments["collection"]
            collection = _get_collection(client, db_name, collection_name)
            # Sample documents to infer schema
            sample = list(collection.find().limit(100))
            if not sample:
                return json.dumps({"message": "Collection is empty"}, indent=2)
            # Simple schema inference
            schema = {}
            for doc in sample:
                for key, value in doc.items():
                    if key not in schema:
                        schema[key] = type(value).__name__
            return json.dumps({"schema": schema, "sampleCount": len(sample)}, indent=2)

        elif tool_name == "collection-storage-size":
            db_name = arguments["database"]
            collection_name = arguments["collection"]
            db = _get_database(client, db_name)
            stats = db.command("collStats", collection_name)
            return json.dumps(
                {
                    "size": stats.get("size", 0),
                    "storageSize": stats.get("storageSize", 0),
                    "totalIndexSize": stats.get("totalIndexSize", 0),
                },
                indent=2,
            )

        elif tool_name == "connect":
            connection_string = arguments["connectionString"]
            # Close old client and create new one
            if session_id in mongo_clients:
                mongo_clients[session_id].close()
            # Add directConnection=true to bypass replica set discovery
            if "?" in connection_string:
                if "directConnection=" not in connection_string:
                    connection_string += "&directConnection=true"
            else:
                connection_string += "?directConnection=true"
            try:
                new_client = MongoClient(connection_string)
                new_client.admin.command("ping")
                mongo_clients[session_id] = new_client
                logger.info(f"Connected to new MongoDB instance for session {session_id}")
                return json.dumps({"message": "Connected successfully"}, indent=2)
            except Exception as e:
                logger.error(f"Failed to connect to MongoDB: {e}")
                raise HTTPException(status_code=500, detail=f"Failed to connect: {str(e)}")

        elif tool_name == "count":
            db_name = arguments["database"]
            collection_name = arguments["collection"]
            query = arguments.get("query", {})
            collection = _get_collection(client, db_name, collection_name)
            count = collection.count_documents(query)
            return json.dumps({"count": count}, indent=2)

        elif tool_name == "create-collection":
            db_name = arguments["database"]
            collection_name = arguments["collection"]
            db = _get_database(client, db_name)
            db.create_collection(collection_name)
            return json.dumps({"message": f"Collection {collection_name} created"}, indent=2)

        elif tool_name == "create-index":
            db_name = arguments["database"]
            collection_name = arguments["collection"]
            keys = arguments["keys"]
            options = arguments.get("options", {})
            collection = _get_collection(client, db_name, collection_name)
            index_name = collection.create_index(list(keys.items()) if isinstance(keys, dict) else keys, **options)
            return json.dumps({"message": f"Index created: {index_name}"}, indent=2)

        elif tool_name == "db-stats":
            db_name = arguments["database"]
            db = _get_database(client, db_name)
            stats = db.command("dbStats")
            return json.dumps(stats, default=str, indent=2)

        elif tool_name == "delete-many":
            db_name = arguments["database"]
            collection_name = arguments["collection"]
            filter_query = arguments["filter"]
            collection = _get_collection(client, db_name, collection_name)
            result = collection.delete_many(filter_query)
            return json.dumps(
                {"deletedCount": result.deleted_count, "acknowledged": result.acknowledged},
                indent=2,
            )

        elif tool_name == "drop-collection":
            db_name = arguments["database"]
            collection_name = arguments["collection"]
            db = _get_database(client, db_name)
            db.drop_collection(collection_name)
            return json.dumps({"message": f"Collection {collection_name} dropped"}, indent=2)

        elif tool_name == "drop-database":
            db_name = arguments["database"]
            client.drop_database(db_name)
            return json.dumps({"message": f"Database {db_name} dropped"}, indent=2)

        elif tool_name == "drop-index":
            db_name = arguments["database"]
            collection_name = arguments["collection"]
            index_name = arguments["indexName"]
            collection = _get_collection(client, db_name, collection_name)
            collection.drop_index(index_name)
            return json.dumps({"message": f"Index {index_name} dropped"}, indent=2)

        elif tool_name == "explain":
            db_name = arguments["database"]
            collection_name = arguments["collection"]
            method = arguments["method"]
            query = arguments["query"]
            collection = _get_collection(client, db_name, collection_name)
            if method == "find":
                explanation = collection.find(query).explain()
            elif method == "aggregate":
                explanation = collection.aggregate(query).explain()
            else:
                raise ValueError(f"Unsupported method for explain: {method}")
            return json.dumps(explanation, default=str, indent=2)

        elif tool_name == "export":
            db_name = arguments["database"]
            collection_name = arguments["collection"]
            query = arguments.get("query", {})
            format_type = arguments.get("format", "ejson")
            collection = _get_collection(client, db_name, collection_name)
            results = list(collection.find(query))
            if format_type == "ejson":
                # Use MongoDB's extended JSON format
                from bson import json_util

                return json.dumps(results, default=json_util.default, indent=2)
            else:
                return json.dumps(results, default=str, indent=2)

        elif tool_name == "find":
            db_name = arguments["database"]
            collection_name = arguments["collection"]
            query = arguments.get("query", {})
            projection = arguments.get("projection")
            limit = arguments.get("limit")
            skip = arguments.get("skip")
            sort = arguments.get("sort")
            collection = _get_collection(client, db_name, collection_name)
            cursor = collection.find(query, projection)
            if sort:
                cursor = cursor.sort(list(sort.items()) if isinstance(sort, dict) else sort)
            if skip:
                cursor = cursor.skip(skip)
            if limit:
                cursor = cursor.limit(limit)
            results = list(cursor)
            return json.dumps(results, default=str, indent=2)

        elif tool_name == "insert-many":
            db_name = arguments["database"]
            collection_name = arguments["collection"]
            documents = arguments["documents"]
            collection = _get_collection(client, db_name, collection_name)
            result = collection.insert_many(documents)
            return json.dumps(
                {
                    "insertedIds": [str(id) for id in result.inserted_ids],
                    "insertedCount": len(result.inserted_ids),
                },
                indent=2,
            )

        elif tool_name == "list-collections":
            db_name = arguments["database"]
            db = _get_database(client, db_name)
            collections = db.list_collection_names()
            return json.dumps({"collections": collections}, indent=2)

        elif tool_name == "list-databases":
            databases = client.list_database_names()
            return json.dumps({"databases": databases}, indent=2)

        elif tool_name == "mongodb-logs":
            # MongoDB logs are typically accessed via serverStatus or getLog
            limit = arguments.get("limit", 100)
            try:
                logs = client.admin.command("getLog", "global")
                log_entries = logs.get("log", [])[-limit:]
                return json.dumps({"logs": log_entries}, indent=2)
            except Exception as e:
                return json.dumps({"message": f"Could not retrieve logs: {str(e)}"}, indent=2)

        elif tool_name == "rename-collection":
            db_name = arguments["database"]
            collection_name = arguments["collection"]
            new_name = arguments["newName"]
            db = _get_database(client, db_name)
            db[collection_name].rename(new_name)
            return json.dumps(
                {"message": f"Collection {collection_name} renamed to {new_name}"}, indent=2
            )

        elif tool_name == "switch-connection":
            connection_string = arguments["connectionString"]
            # Close old client and create new one
            if session_id in mongo_clients:
                mongo_clients[session_id].close()
            # Add directConnection=true to bypass replica set discovery
            if "?" in connection_string:
                if "directConnection=" not in connection_string:
                    connection_string += "&directConnection=true"
            else:
                connection_string += "?directConnection=true"
            try:
                new_client = MongoClient(connection_string)
                new_client.admin.command("ping")
                mongo_clients[session_id] = new_client
                logger.info(f"Switched MongoDB connection for session {session_id}")
                return json.dumps({"message": "Switched connection successfully"}, indent=2)
            except Exception as e:
                logger.error(f"Failed to switch MongoDB connection: {e}")
                raise HTTPException(status_code=500, detail=f"Failed to switch connection: {str(e)}")

        elif tool_name == "update-many":
            db_name = arguments["database"]
            collection_name = arguments["collection"]
            filter_query = arguments["filter"]
            update = arguments["update"]
            collection = _get_collection(client, db_name, collection_name)
            result = collection.update_many(filter_query, update)
            return json.dumps(
                {
                    "matchedCount": result.matched_count,
                    "modifiedCount": result.modified_count,
                    "acknowledged": result.acknowledged,
                },
                indent=2,
            )

        else:
            raise ValueError(f"Unknown tool: {tool_name}")

    except KeyError as e:
        raise HTTPException(status_code=400, detail=f"Missing required argument: {str(e)}")
    except (ConnectionFailure, OperationFailure, PyMongoError) as e:
        raise HTTPException(status_code=500, detail=f"MongoDB error: {str(e)}")
    except Exception as e:
        logger.error(f"Error in execute_tool: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Tool execution error: {str(e)}")


# Pydantic models for Swagger documentation
class DatabaseRequest(BaseModel):
    database: str


class CollectionRequest(BaseModel):
    database: str
    collection: str


class CountRequest(BaseModel):
    database: str
    collection: str
    query: Optional[Dict[str, Any]] = {}


class FindRequest(BaseModel):
    database: str
    collection: str
    query: Optional[Dict[str, Any]] = {}
    projection: Optional[Dict[str, Any]] = None
    limit: Optional[int] = None
    skip: Optional[int] = None
    sort: Optional[Dict[str, Any]] = None


class AggregateRequest(BaseModel):
    database: str
    collection: str
    pipeline: List[Dict[str, Any]]


class InsertManyRequest(BaseModel):
    database: str
    collection: str
    documents: List[Dict[str, Any]]


class UpdateManyRequest(BaseModel):
    database: str
    collection: str
    filter: Dict[str, Any]
    update: Dict[str, Any]


class DeleteManyRequest(BaseModel):
    database: str
    collection: str
    filter: Dict[str, Any]


class CreateIndexRequest(BaseModel):
    database: str
    collection: str
    keys: Dict[str, Any]
    options: Optional[Dict[str, Any]] = {}


class DropIndexRequest(BaseModel):
    database: str
    collection: str
    indexName: str


class RenameCollectionRequest(BaseModel):
    database: str
    collection: str
    newName: str


class ConnectRequest(BaseModel):
    connectionString: str


class ExportRequest(BaseModel):
    database: str
    collection: str
    query: Optional[Dict[str, Any]] = {}
    format: Optional[str] = "ejson"


class ExplainRequest(BaseModel):
    database: str
    collection: str
    method: str
    query: Dict[str, Any]


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


# Individual Swagger routes for each tool
@app.post("/tools/list-databases", tags=["MongoDB Tools"])
async def list_databases_route():
    """List all databases for a MongoDB connection."""
    session_id = _get_default_session()
    client = _get_mongo_client(session_id)
    result = await execute_tool("list-databases", {}, client, session_id)
    return JSONResponse(content=json.loads(result))


@app.post("/tools/list-collections", tags=["MongoDB Tools"])
async def list_collections_route(request: DatabaseRequest):
    """List all collections for a given database."""
    session_id = _get_default_session()
    client = _get_mongo_client(session_id)
    result = await execute_tool("list-collections", {"database": request.database}, client, session_id)
    return JSONResponse(content=json.loads(result))


@app.post("/tools/count", tags=["MongoDB Tools"])
async def count_route(request: CountRequest):
    """Gets the number of documents in a MongoDB collection."""
    session_id = _get_default_session()
    client = _get_mongo_client(session_id)
    result = await execute_tool(
        "count",
        {
            "database": request.database,
            "collection": request.collection,
            "query": request.query,
        },
        client,
        session_id,
    )
    return JSONResponse(content=json.loads(result))


@app.post("/tools/find", tags=["MongoDB Tools"])
async def find_route(request: FindRequest):
    """Run a find query against a MongoDB collection."""
    session_id = _get_default_session()
    client = _get_mongo_client(session_id)
    args = {
        "database": request.database,
        "collection": request.collection,
        "query": request.query,
    }
    if request.projection:
        args["projection"] = request.projection
    if request.limit:
        args["limit"] = request.limit
    if request.skip:
        args["skip"] = request.skip
    if request.sort:
        args["sort"] = request.sort
    result = await execute_tool("find", args, client, session_id)
    return JSONResponse(content=json.loads(result))


@app.post("/tools/aggregate", tags=["MongoDB Tools"])
async def aggregate_route(request: AggregateRequest):
    """Run an aggregation against a MongoDB collection."""
    session_id = _get_default_session()
    client = _get_mongo_client(session_id)
    result = await execute_tool(
        "aggregate",
        {
            "database": request.database,
            "collection": request.collection,
            "pipeline": request.pipeline,
        },
        client,
        session_id,
    )
    return JSONResponse(content=json.loads(result))


@app.post("/tools/insert-many", tags=["MongoDB Tools"])
async def insert_many_route(request: InsertManyRequest):
    """Insert an array of documents into a MongoDB collection."""
    session_id = _get_default_session()
    client = _get_mongo_client(session_id)
    result = await execute_tool(
        "insert-many",
        {
            "database": request.database,
            "collection": request.collection,
            "documents": request.documents,
        },
        client,
        session_id,
    )
    return JSONResponse(content=json.loads(result))


@app.post("/tools/update-many", tags=["MongoDB Tools"])
async def update_many_route(request: UpdateManyRequest):
    """Updates all documents that match the specified filter."""
    session_id = _get_default_session()
    client = _get_mongo_client(session_id)
    result = await execute_tool(
        "update-many",
        {
            "database": request.database,
            "collection": request.collection,
            "filter": request.filter,
            "update": request.update,
        },
        client,
        session_id,
    )
    return JSONResponse(content=json.loads(result))


@app.post("/tools/delete-many", tags=["MongoDB Tools"])
async def delete_many_route(request: DeleteManyRequest):
    """Removes all documents that match the filter from a MongoDB collection."""
    session_id = _get_default_session()
    client = _get_mongo_client(session_id)
    result = await execute_tool(
        "delete-many",
        {
            "database": request.database,
            "collection": request.collection,
            "filter": request.filter,
        },
        client,
        session_id,
    )
    return JSONResponse(content=json.loads(result))


@app.post("/tools/create-collection", tags=["MongoDB Tools"])
async def create_collection_route(request: CollectionRequest):
    """Creates a new collection in a database."""
    session_id = _get_default_session()
    client = _get_mongo_client(session_id)
    result = await execute_tool(
        "create-collection",
        {"database": request.database, "collection": request.collection},
        client,
        session_id,
    )
    return JSONResponse(content=json.loads(result))


@app.post("/tools/drop-collection", tags=["MongoDB Tools"])
async def drop_collection_route(request: CollectionRequest):
    """Removes a collection or view from the database."""
    session_id = _get_default_session()
    client = _get_mongo_client(session_id)
    result = await execute_tool(
        "drop-collection",
        {"database": request.database, "collection": request.collection},
        client,
        session_id,
    )
    return JSONResponse(content=json.loads(result))


@app.post("/tools/drop-database", tags=["MongoDB Tools"])
async def drop_database_route(request: DatabaseRequest):
    """Removes the specified database."""
    session_id = _get_default_session()
    client = _get_mongo_client(session_id)
    result = await execute_tool("drop-database", {"database": request.database}, client, session_id)
    return JSONResponse(content=json.loads(result))


@app.post("/tools/db-stats", tags=["MongoDB Tools"])
async def db_stats_route(request: DatabaseRequest):
    """Returns statistics that reflect the use state of a single database."""
    session_id = _get_default_session()
    client = _get_mongo_client(session_id)
    result = await execute_tool("db-stats", {"database": request.database}, client, session_id)
    return JSONResponse(content=json.loads(result))


@app.post("/tools/collection-indexes", tags=["MongoDB Tools"])
async def collection_indexes_route(request: CollectionRequest):
    """Describe the indexes for a collection."""
    session_id = _get_default_session()
    client = _get_mongo_client(session_id)
    result = await execute_tool(
        "collection-indexes",
        {"database": request.database, "collection": request.collection},
        client,
        session_id,
    )
    return JSONResponse(content=json.loads(result))


@app.post("/tools/collection-schema", tags=["MongoDB Tools"])
async def collection_schema_route(request: CollectionRequest):
    """Describe the schema for a collection."""
    session_id = _get_default_session()
    client = _get_mongo_client(session_id)
    result = await execute_tool(
        "collection-schema",
        {"database": request.database, "collection": request.collection},
        client,
        session_id,
    )
    return JSONResponse(content=json.loads(result))


@app.post("/tools/collection-storage-size", tags=["MongoDB Tools"])
async def collection_storage_size_route(request: CollectionRequest):
    """Gets the size of the collection."""
    session_id = _get_default_session()
    client = _get_mongo_client(session_id)
    result = await execute_tool(
        "collection-storage-size",
        {"database": request.database, "collection": request.collection},
        client,
        session_id,
    )
    return JSONResponse(content=json.loads(result))


@app.post("/tools/create-index", tags=["MongoDB Tools"])
async def create_index_route(request: CreateIndexRequest):
    """Create an index for a collection."""
    session_id = _get_default_session()
    client = _get_mongo_client(session_id)
    result = await execute_tool(
        "create-index",
        {
            "database": request.database,
            "collection": request.collection,
            "keys": request.keys,
            "options": request.options or {},
        },
        client,
        session_id,
    )
    return JSONResponse(content=json.loads(result))


@app.post("/tools/drop-index", tags=["MongoDB Tools"])
async def drop_index_route(request: DropIndexRequest):
    """Drop an index for the provided database and collection."""
    session_id = _get_default_session()
    client = _get_mongo_client(session_id)
    result = await execute_tool(
        "drop-index",
        {
            "database": request.database,
            "collection": request.collection,
            "indexName": request.indexName,
        },
        client,
        session_id,
    )
    return JSONResponse(content=json.loads(result))


@app.post("/tools/rename-collection", tags=["MongoDB Tools"])
async def rename_collection_route(request: RenameCollectionRequest):
    """Renames a collection in a MongoDB database."""
    session_id = _get_default_session()
    client = _get_mongo_client(session_id)
    result = await execute_tool(
        "rename-collection",
        {
            "database": request.database,
            "collection": request.collection,
            "newName": request.newName,
        },
        client,
        session_id,
    )
    return JSONResponse(content=json.loads(result))


@app.post("/tools/export", tags=["MongoDB Tools"])
async def export_route(request: ExportRequest):
    """Export a query or aggregation results in the specified format."""
    session_id = _get_default_session()
    client = _get_mongo_client(session_id)
    result = await execute_tool(
        "export",
        {
            "database": request.database,
            "collection": request.collection,
            "query": request.query,
            "format": request.format,
        },
        client,
        session_id,
    )
    return JSONResponse(content=json.loads(result))


@app.post("/tools/explain", tags=["MongoDB Tools"])
async def explain_route(request: ExplainRequest):
    """Returns statistics describing the execution of the winning plan."""
    session_id = _get_default_session()
    client = _get_mongo_client(session_id)
    result = await execute_tool(
        "explain",
        {
            "database": request.database,
            "collection": request.collection,
            "method": request.method,
            "query": request.query,
        },
        client,
        session_id,
    )
    return JSONResponse(content=json.loads(result))


@app.post("/tools/connect", tags=["MongoDB Tools"])
async def connect_route(request: ConnectRequest):
    """Connect to a MongoDB instance."""
    session_id = _get_default_session()
    # For connect, we get a dummy client first, then the tool will replace it
    client = _get_mongo_client(session_id)
    result = await execute_tool("connect", {"connectionString": request.connectionString}, client, session_id)
    return JSONResponse(content=json.loads(result))


@app.get("/")
async def root():
    """Root endpoint with API information."""
    return {
        "service": "MongoDB MCP Server",
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
        test_client = _create_mongo_client()
        test_client.close()
        return JSONResponse(content={"status": "healthy"})
    except Exception as e:
        logger.error(f"Health check failed: {e}")
        return JSONResponse(
            status_code=503, content={"status": "unhealthy", "reason": str(e)}
        )


def run() -> None:
    """Run the MongoDB MCP Server."""
    host = get_str("MCP_HOST", "0.0.0.0") or "0.0.0.0"
    port = get_int("MCP_PORT", 8000)

    import uvicorn

    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    run()

