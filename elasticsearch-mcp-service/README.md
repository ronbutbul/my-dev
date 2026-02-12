# Elasticsearch MCP Server

A Multi-Cluster Proxy (MCP) server for Elasticsearch operations, implementing the JSON-RPC 2.0 protocol for use with kagent.

## Features

This MCP server exposes the following Elasticsearch operations as tools:

- **search** - Run a search query against an Elasticsearch index
- **count** - Return the number of documents matching a query in an index
- **get-document** - Get a single document by ID
- **index-document** - Index (create or replace) a single document
- **delete-document** - Delete a single document by ID
- **update-document** - Partially update a document by ID
- **list-indices** - List all indices in the cluster
- **index-mappings** - Get mappings for an index
- **index-stats** - Get statistics for an index
- **cluster-health** - Get cluster health information
- **cluster-stats** - Get cluster-wide statistics
- **switch-connection** - Switch to a different Elasticsearch connection URL

## Setup

### Prerequisites

- Python 3.10+
- Elasticsearch cluster (local or remote)

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

The service can be configured via environment variables (or via `config.py` defaults):

- `ES_MCP_CONNECTION_STRING` - Elasticsearch URL (default: `http://localhost:9200`)
- `ES_USE_AUTH` - Enable basic authentication (`true`/`false`, default: `false`)
- `ES_USERNAME` - Username for basic auth (required if `ES_USE_AUTH=true`)
- `ES_PASSWORD` - Password for basic auth (required if `ES_USE_AUTH=true`)
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

## Usage with kagent / direct MCP calls

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

### Call a Tool (Example: list-indices)

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
      "name":"list-indices",
      "arguments":{}
    }
  }'
```

### Call a Tool (Example: search)

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
      "name":"search",
      "arguments":{
        "index":"k8s-2026.02.12",
        "query":{
          "match_all":{}
        },
        "size":10
      }
    }
  }'
```

You can replace the `query` body with any valid Elasticsearch query DSL.

## Docker

Build the Docker image:

```bash
docker build -t elasticsearch-mcp-service .
```

Run the container:

```bash
docker run -p 8000:8000 \
  -e ES_MCP_CONNECTION_STRING="http://elasticsearch:9200" \
  elasticsearch-mcp-service
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

- Each session maintains its own Elasticsearch client connection
- Sessions are stored in memory (for production, consider using Redis)
- The service implements the MCP protocol version 2024-11-05
- All Elasticsearch operations return JSON-formatted results (wrapped as text in MCP responses)

