# MongoDB MCP Server

A Multi-Cluster Proxy (MCP) server for MongoDB operations, implementing the JSON-RPC 2.0 protocol for use with kagent.

## Features

This MCP server exposes the following MongoDB operations as tools:

- **aggregate** - Run an aggregation against a MongoDB collection
- **collection-indexes** - Describe the indexes for a collection
- **collection-schema** - Describe the schema for a collection
- **collection-storage-size** - Gets the size of the collection
- **connect** - Connect to a MongoDB instance
- **count** - Gets the number of documents in a MongoDB collection
- **create-collection** - Creates a new collection in a database
- **create-index** - Create an index for a collection
- **db-stats** - Returns statistics that reflect the use state of a single database
- **delete-many** - Removes all documents that match the filter from a MongoDB collection
- **drop-collection** - Removes a collection or view from the database
- **drop-database** - Removes the specified database
- **drop-index** - Drop an index for the provided database and collection
- **explain** - Returns statistics describing the execution of the winning plan
- **export** - Export a query or aggregation results in EJSON format
- **find** - Run a find query against a MongoDB collection
- **insert-many** - Insert an array of documents into a MongoDB collection
- **list-collections** - List all collections for a given database
- **list-databases** - List all databases for a MongoDB connection
- **mongodb-logs** - Returns the most recent logged mongod events
- **rename-collection** - Renames a collection in a MongoDB database
- **switch-connection** - Switch to a different MongoDB connection
- **update-many** - Updates all documents that match the specified filter

## Setup

### Prerequisites

- Python 3.10+
- MongoDB instance (local or remote)

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

The service can be configured via environment variables:

- `MDB_MCP_CONNECTION_STRING` - MongoDB connection string (default: `mongodb://localhost:27017`)
- `MCP_HOST` - Host to bind to (default: `0.0.0.0`)
- `MCP_PORT` - Port to listen on (default: `8000`)

## Running Locally

```bash
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

### Call a Tool (Example: list-databases)

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
      "name":"list-databases",
      "arguments":{}
    }
  }'
```

### Call a Tool (Example: find)

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
      "name":"find",
      "arguments":{
        "database":"mydb",
        "collection":"mycollection",
        "query":{},
        "limit":10
      }
    }
  }'
```

## Docker

Build the Docker image:
```bash
docker build -t mongodb-mcp-service .
```

Run the container:
```bash
docker run -p 8000:8000 \
  -e MDB_MCP_CONNECTION_STRING="mongodb://mongodb:27017" \
  mongodb-mcp-service
```

## API Documentation

Once the service is running, visit:
- Swagger UI: `http://localhost:8000/docs`
- ReDoc: `http://localhost:8000/redoc`

## Health Check

```bash
curl http://localhost:8000/health
```

## Notes

- Each session maintains its own MongoDB client connection
- Sessions are stored in memory (for production, consider using Redis)
- The service implements the MCP protocol version 2024-11-05
- All MongoDB operations return JSON-formatted results

