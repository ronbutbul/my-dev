# Kafka MCP Server original f

A Multi-Cluster Proxy (MCP) server for Kafka operations, implementing the JSON-RPC 2.0 protocol for use with kagent.

## Features

This MCP server exposes the following Kafka operations as tools:

- **create-topic** - Create a new Kafka topic with configurable partitions and replication factor
- **list-topics** - Get a list of all available Kafka topics in the cluster
- **delete-topic** - Remove an existing Kafka topic
- **describe-topic** - Get detailed information about a specific topic, including partition details
- **produce-message** - Send messages to a Kafka topic with support for message keys and headers
- **consume-messages** - Read messages from a Kafka topic with configurable timeout
- **list-broker** - List all brokers in Kafka cluster
- **list-consumer** - List all consumer groups

## Setup

### Prerequisites

- Python 3.10+
- Kafka cluster (local or remote)

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

- `KAFKA_BOOTSTRAP_SERVERS` - Kafka bootstrap servers for consumer/producer operations (default: `localhost:9092`)
  - Should use EXTERNAL listener (e.g., port 9095) for external access
- `KAFKA_ADMIN_SERVERS` - Kafka bootstrap servers for admin operations (optional)
  - If not set, uses `KAFKA_BOOTSTRAP_SERVERS`
  - **Important**: Should use EXTERNAL (9095) or CLIENT (9092) listener, NOT CONTROLLER (9093)
  - CONTROLLER listener uses KRaft protocol and doesn't support regular Kafka admin operations
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

### Call a Tool (Example: list-topics)

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
      "name":"list-topics",
      "arguments":{}
    }
  }'
```

### Call a Tool (Example: produce-message)

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
      "name":"produce-message",
      "arguments":{
        "topic":"my-topic",
        "message":"Hello Kafka!",
        "key":"my-key"
      }
    }
  }'
```

## Docker

Build the Docker image:
```bash
docker build -t mcp-kafka-service .
```

Run the container:
```bash
docker run -p 8000:8000 \
  -e KAFKA_BOOTSTRAP_SERVERS="kafka:9092" \
  mcp-kafka-service
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

- Each session maintains its own Kafka client connections
- Sessions are stored in memory (for production, consider using Redis)
- The service implements the MCP protocol version 2024-11-05
- All Kafka operations return JSON-formatted results
- Consumer group listing requires admin permissions

