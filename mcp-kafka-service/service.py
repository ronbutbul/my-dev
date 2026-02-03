from __future__ import annotations

import json
import logging
import uuid
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Header, Request
from fastapi.responses import JSONResponse, Response
from kafka import KafkaAdminClient, KafkaProducer, KafkaConsumer
from kafka.admin import ConfigResource, ConfigResourceType, NewTopic
from kafka.errors import KafkaError
from pydantic import BaseModel

from config import get_str, get_int

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

app = FastAPI(
    title="Kafka MCP Server",
    description="MCP server for Kafka operations",
    version="1.0.0",
)

# Session storage (in production, use Redis or similar)
sessions: Dict[str, Dict[str, Any]] = {}

# Kafka clients cache per session
kafka_admin_clients: Dict[str, KafkaAdminClient] = {}
kafka_producers: Dict[str, KafkaProducer] = {}
kafka_consumers: Dict[str, KafkaConsumer] = {}


def _create_kafka_admin_client() -> KafkaAdminClient:
    """Create and return a Kafka admin client."""
    # Use KAFKA_ADMIN_SERVERS if set, otherwise use KAFKA_BOOTSTRAP_SERVERS
    admin_servers = get_str("KAFKA_ADMIN_SERVERS")
    if not admin_servers:
        admin_servers = get_str("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
    
    logger.info("Connecting to Kafka admin at %s", admin_servers)
    
    try:
        client = KafkaAdminClient(
            bootstrap_servers=admin_servers.split(","),
            client_id="kafka-mcp-server-admin",
            api_version=(2, 5, 0),  # Explicitly set API version to avoid protocol issues
            api_version_auto_timeout_ms=10000,  # Increase timeout for controller discovery
            request_timeout_ms=30000,  # Increase request timeout
            retry_backoff_ms=100,  # Retry backoff
        )
        logger.info("Successfully connected to Kafka admin client")
    except Exception as e:
        logger.error("Failed to connect to Kafka admin: %s", e)
        raise
    
    return client


def _get_kafka_admin_client(session_id: str) -> KafkaAdminClient:
    """Get or create Kafka admin client for a session."""
    if session_id not in kafka_admin_clients:
        try:
            client = _create_kafka_admin_client()
            kafka_admin_clients[session_id] = client
            logger.info(f"Created Kafka admin client for session {session_id}")
        except Exception as e:
            logger.error(f"Failed to connect to Kafka: {e}")
            # Don't cache failed connections
            raise HTTPException(status_code=500, detail=f"Failed to connect to Kafka: {str(e)}")
    return kafka_admin_clients[session_id]


def _get_kafka_producer(session_id: str) -> KafkaProducer:
    """Get or create Kafka producer for a session."""
    if session_id not in kafka_producers:
        try:
            bootstrap_servers = get_str("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
            producer = KafkaProducer(
                bootstrap_servers=bootstrap_servers.split(","),
                value_serializer=lambda v: json.dumps(v).encode("utf-8"),
                key_serializer=lambda k: k.encode("utf-8") if k else None,
            )
            kafka_producers[session_id] = producer
            logger.info(f"Created Kafka producer for session {session_id}")
        except Exception as e:
            logger.error(f"Failed to create Kafka producer: {e}")
            raise HTTPException(status_code=500, detail=f"Failed to create Kafka producer: {str(e)}")
    return kafka_producers[session_id]


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
                    "name": "kafka-mcp-server",
                    "version": "1.0.0",
                },
            },
        }
    )
    response.headers["MCP-Session-Id"] = session_id
    return response


async def handle_tools_list(request_id: Any) -> JSONResponse:
    """Handle tools/list method - returns all available Kafka tools."""
    tools = [
        {
            "name": "create-topic",
            "description": "Create a new Kafka topic with configurable partitions and replication factor",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "topic": {"type": "string", "description": "Topic name"},
                    "num_partitions": {"type": "integer", "description": "Number of partitions", "default": 1},
                    "replication_factor": {"type": "integer", "description": "Replication factor", "default": 1},
                    "configs": {
                        "type": "object",
                        "description": "Topic configuration (optional)",
                    },
                },
                "required": ["topic"],
            },
        },
        {
            "name": "list-topics",
            "description": "Get a list of all available Kafka topics in the cluster",
            "inputSchema": {"type": "object", "properties": {}},
        },
        {
            "name": "delete-topic",
            "description": "Remove an existing Kafka topic",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "topic": {"type": "string", "description": "Topic name"},
                },
                "required": ["topic"],
            },
        },
        {
            "name": "describe-topic",
            "description": "Get detailed information about a specific topic, including partition details",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "topic": {"type": "string", "description": "Topic name"},
                },
                "required": ["topic"],
            },
        },
        {
            "name": "produce-message",
            "description": "Send messages to a Kafka topic with support for message keys and headers",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "topic": {"type": "string", "description": "Topic name"},
                    "message": {"type": "string", "description": "Message content"},
                    "key": {"type": "string", "description": "Message key (optional)"},
                    "headers": {
                        "type": "object",
                        "description": "Message headers (optional)",
                    },
                },
                "required": ["topic", "message"],
            },
        },
        {
            "name": "consume-messages",
            "description": "Read messages from a Kafka topic with configurable timeout",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "topic": {"type": "string", "description": "Topic name"},
                    "timeout": {"type": "integer", "description": "Timeout in seconds", "default": 5},
                    "max_messages": {"type": "integer", "description": "Maximum number of messages", "default": 10},
                },
                "required": ["topic"],
            },
        },
        {
            "name": "list-broker",
            "description": "List all brokers in Kafka cluster",
            "inputSchema": {"type": "object", "properties": {}},
        },
        {
            "name": "list-consumer",
            "description": "List all consumer groups",
            "inputSchema": {"type": "object", "properties": {}},
        },
        {
            "name": "describe-consumer-group",
            "description": "Describe a specific consumer group by ID",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "group_id": {"type": "string", "description": "Consumer group ID"},
                },
                "required": ["group_id"],
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
    """Handle tools/call method - executes Kafka operations."""
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
        result = await execute_tool(tool_name, arguments, session_id)
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


async def execute_tool(tool_name: str, arguments: Dict[str, Any], session_id: str) -> str:
    """Execute a Kafka tool operation."""
    try:
        if tool_name == "create-topic":
            topic = arguments["topic"]
            num_partitions = arguments.get("num_partitions", 1)
            replication_factor = arguments.get("replication_factor", 1)
            configs = arguments.get("configs", {})
            
            try:
                admin_client = _get_kafka_admin_client(session_id)
                topic_list = [NewTopic(
                    name=topic,
                    num_partitions=num_partitions,
                    replication_factor=replication_factor,
                    topic_configs=configs,
                )]
                
                result = admin_client.create_topics(new_topics=topic_list, validate_only=False)
                result[topic].result()  # Wait for creation
                
                return json.dumps({"message": f"Topic {topic} created successfully"}, indent=2)
            except HTTPException as e:
                # If admin client creation fails (NodeNotReadyError)
                error_msg = str(e.detail) if hasattr(e, 'detail') else str(e)
                logger.warning(f"create-topic failed (admin client error): {error_msg}")
                return json.dumps({
                    "message": f"Topic creation failed: {error_msg}",
                    "topic": topic,
                    "explanation": "Creating topics requires admin client access to the Kafka controller (port 9093), which is only accessible internally within the Kubernetes cluster.",
                    "workaround": "Run this service inside the Kubernetes cluster, or use Kafka admin tools from within the cluster (kubectl exec into a pod)",
                }, indent=2)
            except Exception as e:
                logger.error(f"create-topic failed: {e}", exc_info=True)
                return json.dumps({
                    "message": f"Topic creation failed: {str(e)}",
                    "topic": topic,
                    "note": "This requires admin client access to the Kafka controller.",
                }, indent=2)

        elif tool_name == "list-topics":
            bootstrap_servers = get_str("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
            # Use consumer to fetch metadata - this is more reliable
            consumer = KafkaConsumer(bootstrap_servers=bootstrap_servers.split(","))
            topics = []
            try:
                # Poll to trigger metadata fetch - this populates the cluster
                consumer.poll(timeout_ms=3000)
                
                # Access cluster metadata through consumer's internal client
                if hasattr(consumer, '_client') and hasattr(consumer._client, 'cluster'):
                    cluster = consumer._client.cluster
                    # The cluster object has a topics() method that returns all topics
                    if hasattr(cluster, 'topics'):
                        topics = list(cluster.topics())
                        logger.info(f"Found {len(topics)} topics from consumer cluster")
                    
                # If still empty, try admin client
                if not topics:
                    logger.info("Consumer metadata empty, trying admin client")
                    admin_client = _get_kafka_admin_client(session_id)
                    try:
                        # Try list_topics first
                        topic_list = admin_client.list_topics()
                        logger.info(f"Admin list_topics() returned: {type(topic_list)}, {topic_list}")
                        if isinstance(topic_list, dict):
                            topics = list(topic_list.keys())
                        elif hasattr(topic_list, 'keys'):
                            topics = list(topic_list.keys())
                        elif hasattr(topic_list, '__iter__') and not isinstance(topic_list, str):
                            topics = list(topic_list)
                    except Exception as e:
                        logger.error(f"Admin list_topics() failed: {e}")
            finally:
                consumer.close()
                
            logger.info(f"Returning {len(topics)} topics: {topics}")
            return json.dumps({"topics": sorted(topics)}, indent=2)

        elif tool_name == "delete-topic":
            topic = arguments["topic"]
            try:
                admin_client = _get_kafka_admin_client(session_id)
                admin_client.delete_topics(topics=[topic])
                return json.dumps({"message": f"Topic {topic} deleted successfully"}, indent=2)
            except HTTPException as e:
                # If admin client creation fails (NodeNotReadyError)
                error_msg = str(e.detail) if hasattr(e, 'detail') else str(e)
                logger.warning(f"delete-topic failed (admin client error): {error_msg}")
                return json.dumps({
                    "message": f"Topic deletion failed: {error_msg}",
                    "topic": topic,
                    "explanation": "Deleting topics requires admin client access to the Kafka controller (port 9093), which is only accessible internally within the Kubernetes cluster.",
                    "workaround": "Run this service inside the Kubernetes cluster, or use Kafka admin tools from within the cluster (kubectl exec into a pod)",
                }, indent=2)
            except Exception as e:
                logger.error(f"delete-topic failed: {e}", exc_info=True)
                return json.dumps({
                    "message": f"Topic deletion failed: {str(e)}",
                    "topic": topic,
                    "note": "This requires admin client access to the Kafka controller.",
                }, indent=2)

        elif tool_name == "describe-topic":
            topic = arguments["topic"]
            bootstrap_servers = get_str("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
            consumer = KafkaConsumer(bootstrap_servers=bootstrap_servers.split(","))
            
            try:
                # Check if topic exists
                if hasattr(consumer, '_client') and hasattr(consumer._client, 'cluster'):
                    if topic not in consumer._client.cluster.topics():
                        raise HTTPException(status_code=404, detail=f"Topic {topic} not found")
                    
                    # Get partition metadata
                    partitions = []
                    partition_metadata = consumer._client.cluster.partitions_for_topic(topic)
                    if partition_metadata:
                        for partition_id in partition_metadata:
                            pm = consumer._client.cluster.partition_metadata_for_topic(topic).get(partition_id)
                            if pm:
                                partitions.append({
                                    "partition": partition_id,
                                    "leader": pm.leader if hasattr(pm, 'leader') else None,
                                    "replicas": list(pm.replicas) if hasattr(pm, 'replicas') else [],
                                    "isr": list(pm.isr) if hasattr(pm, 'isr') else [],
                                })
                else:
                    # Fallback: use admin client
                    admin_client = _get_kafka_admin_client(session_id)
                    metadata = admin_client.describe_topics([topic])
                    if topic not in metadata:
                        raise HTTPException(status_code=404, detail=f"Topic {topic} not found")
                    topic_metadata = metadata[topic]
                    partitions = [{"partition": i, "leader": None, "replicas": [], "isr": []} for i in range(getattr(topic_metadata, 'num_partitions', 1))]
            finally:
                consumer.close()
            
            return json.dumps({
                "topic": topic,
                "partitions": partitions,
                "partition_count": len(partitions),
            }, indent=2)

        elif tool_name == "produce-message":
            topic = arguments["topic"]
            message = arguments["message"]
            key = arguments.get("key")
            headers = arguments.get("headers", {})
            
            producer = _get_kafka_producer(session_id)
            
            # Convert headers to list of tuples
            kafka_headers = [(k.encode("utf-8"), v.encode("utf-8")) for k, v in headers.items()] if headers else None
            
            future = producer.send(
                topic,
                value=message,
                key=key,
                headers=kafka_headers,
            )
            record_metadata = future.get(timeout=10)
            
            return json.dumps({
                "message": "Message produced successfully",
                "topic": record_metadata.topic,
                "partition": record_metadata.partition,
                "offset": record_metadata.offset,
            }, indent=2)

        elif tool_name == "consume-messages":
            topic = arguments["topic"]
            timeout = arguments.get("timeout", 5)
            max_messages = arguments.get("max_messages", 10)
            
            bootstrap_servers = get_str("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
            consumer = KafkaConsumer(
                topic,
                bootstrap_servers=bootstrap_servers.split(","),
                auto_offset_reset="earliest",
                enable_auto_commit=True,
                consumer_timeout_ms=timeout * 1000,
                value_deserializer=lambda m: m.decode("utf-8") if m else None,
            )
            
            messages = []
            try:
                for message in consumer:
                    try:
                        # Try to parse as JSON, if fails return as string
                        value = json.loads(message.value) if message.value else None
                    except (json.JSONDecodeError, TypeError):
                        value = message.value
                    
                    msg_data = {
                        "topic": message.topic,
                        "partition": message.partition,
                        "offset": message.offset,
                        "key": message.key.decode("utf-8") if message.key else None,
                        "value": value,
                        "timestamp": message.timestamp,
                    }
                    messages.append(msg_data)
                    if len(messages) >= max_messages:
                        break
            finally:
                consumer.close()
            
            return json.dumps({"messages": messages, "count": len(messages)}, indent=2)

        elif tool_name == "list-broker":
            bootstrap_servers = get_str("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
            # Use consumer to fetch metadata - same approach as list-topics
            consumer = KafkaConsumer(bootstrap_servers=bootstrap_servers.split(","))
            brokers = []
            try:
                # Poll to trigger metadata fetch
                consumer.poll(timeout_ms=3000)
                
                # Access cluster metadata through consumer's internal client
                if hasattr(consumer, '_client') and hasattr(consumer._client, 'cluster'):
                    cluster = consumer._client.cluster
                    # Get brokers from cluster metadata
                    # Try different ways to access brokers depending on kafka-python version
                    brokers_data = None
                    
                    # Method 1: Try cluster.brokers() (might return dict or set)
                    if hasattr(cluster, 'brokers'):
                        try:
                            brokers_data = cluster.brokers()
                            logger.info(f"cluster.brokers() returned type: {type(brokers_data)}")
                        except Exception as e:
                            logger.warning(f"cluster.brokers() failed: {e}")
                    
                    # Method 2: Try cluster.brokers (property, might be a dict)
                    if not brokers_data and hasattr(cluster, 'brokers'):
                        try:
                            brokers_data = cluster.brokers
                            logger.info(f"cluster.brokers (property) returned type: {type(brokers_data)}")
                        except Exception as e:
                            logger.warning(f"cluster.brokers (property) failed: {e}")
                    
                    if brokers_data:
                        if isinstance(brokers_data, dict):
                            # If it's a dict, iterate over items
                            for broker_id, broker_metadata in brokers_data.items():
                                brokers.append({
                                    "id": broker_id,
                                    "host": getattr(broker_metadata, 'host', 'unknown'),
                                    "port": getattr(broker_metadata, 'port', 9092),
                                })
                        elif isinstance(brokers_data, (set, list, tuple)):
                            # If it's a set, list, or tuple, iterate directly
                            for broker_metadata in brokers_data:
                                # Try to get node_id or id attribute
                                broker_id = getattr(broker_metadata, 'node_id', None)
                                if broker_id is None:
                                    broker_id = getattr(broker_metadata, 'id', 'unknown')
                                
                                brokers.append({
                                    "id": broker_id,
                                    "host": getattr(broker_metadata, 'host', 'unknown'),
                                    "port": getattr(broker_metadata, 'port', 9092),
                                })
                        elif hasattr(brokers_data, 'items'):
                            # If it has items() method, use it
                            for broker_id, broker_metadata in brokers_data.items():
                                brokers.append({
                                    "id": broker_id,
                                    "host": getattr(broker_metadata, 'host', 'unknown'),
                                    "port": getattr(broker_metadata, 'port', 9092),
                                })
                        else:
                            # Try to access as dict-like
                            try:
                                for broker_id in brokers_data:
                                    broker_metadata = brokers_data[broker_id]
                                    brokers.append({
                                        "id": broker_id,
                                        "host": getattr(broker_metadata, 'host', 'unknown'),
                                        "port": getattr(broker_metadata, 'port', 9092),
                                    })
                            except (TypeError, KeyError, AttributeError) as e:
                                logger.warning(f"Failed to iterate brokers_data as dict: {e}")
                    
                    logger.info(f"Found {len(brokers)} brokers from consumer cluster")
                        
                # Final fallback: parse from bootstrap servers if no brokers found
                if not brokers:
                    logger.info("Using bootstrap servers as fallback")
                    for server in bootstrap_servers.split(","):
                        parts = server.split(":")
                        brokers.append({
                            "id": "unknown",
                            "host": parts[0],
                            "port": int(parts[1]) if len(parts) > 1 else 9092,
                        })
            finally:
                consumer.close()
            
            logger.info(f"Returning {len(brokers)} brokers: {brokers}")
            return json.dumps({"brokers": brokers, "count": len(brokers)}, indent=2)

        elif tool_name == "list-consumer":
            # Explanation of why list-topics works but list-consumer doesn't:
            #
            # Kafka has multiple listeners configured:
            # - EXTERNAL (9095): Accessible from outside cluster - used by KafkaConsumer
            # - CONTROLLER (9093): Internal only - required by KafkaAdminClient for admin operations
            # - CLIENT (9092): Internal cluster communication
            # - INTERNAL (9094): Inter-broker communication
            #
            # Why list-topics works (port 9095):
            #   - Uses KafkaConsumer -> connects to EXTERNAL listener (9095) -> accessible from outside
            #
            # Why list-consumer fails (tries ports 9096/9097):
            #   - Uses KafkaAdminClient -> needs controller access (9093) for admin operations
            #   - Admin client connects to bootstrap (9095), gets cluster metadata
            #   - Metadata shows other brokers on ports 9096/9097 (advertised.listeners)
            #   - Admin client tries to connect to controller via these brokers -> fails (not accessible from outside)
            #   - Controller (9093) is only accessible internally within Kubernetes cluster
            #
            logger.info("list-consumer called - admin operations require controller access (internal only)")
            return json.dumps({
                "message": "Consumer group listing requires admin client access to the Kafka controller",
                "explanation": {
                    "why_list_topics_works": {
                        "uses": "KafkaConsumer",
                        "connects_to": "EXTERNAL listener (port 9095)",
                        "result": "Works from outside cluster"
                    },
                    "why_list_consumer_fails": {
                        "uses": "KafkaAdminClient",
                        "needs": "CONTROLLER listener (port 9093) for admin operations",
                        "what_happens": "Connects to bootstrap (9095), gets metadata showing brokers on 9096/9097, tries to reach controller through them",
                        "result": "Fails because controller (9093) is only accessible internally within Kubernetes cluster"
                    },
                    "kafka_listeners": {
                        "EXTERNAL": "9095 - accessible from outside (used by consumers/producers)",
                        "CONTROLLER": "9093 - internal only (required for admin operations like list_consumer_groups)",
                        "CLIENT": "9092 - internal cluster communication",
                        "INTERNAL": "9094 - inter-broker communication"
                    }
                },
                "workarounds": [
                    "Run this service inside the Kubernetes cluster to access the controller",
                    "Use describe-consumer-group tool with a specific group ID if you know it",
                    "Access Kafka admin tools from within the cluster (kubectl exec into a pod)"
                ],
                "consumer_groups": [],
                "count": 0,
            }, indent=2)

        elif tool_name == "describe-consumer-group":
            group_id = arguments["group_id"]
            # Try to describe a specific consumer group
            # This also requires admin client, but might work if we can access it
            try:
                admin_client = _get_kafka_admin_client(session_id)
                # Describe the consumer group
                group_description = admin_client.describe_consumer_groups([group_id])
                logger.info(f"describe_consumer_groups() returned: {type(group_description)}, {group_description}")
                
                # Handle the response
                if isinstance(group_description, dict) and group_id in group_description:
                    group_info = group_description[group_id]
                    return json.dumps({
                        "group_id": group_id,
                        "state": getattr(group_info, 'state', 'unknown'),
                        "members": getattr(group_info, 'members', []),
                        "group_info": str(group_info),
                    }, indent=2)
                elif hasattr(group_description, '__iter__'):
                    # If it's a list or iterable
                    for group in group_description:
                        if hasattr(group, 'group_id') and group.group_id == group_id:
                            return json.dumps({
                                "group_id": group_id,
                                "state": getattr(group, 'state', 'unknown'),
                                "members": getattr(group, 'members', []),
                                "group_info": str(group),
                            }, indent=2)
                
                return json.dumps({
                    "message": f"Consumer group {group_id} not found or could not be described",
                    "group_id": group_id,
                }, indent=2)
            except HTTPException as e:
                # If admin client creation fails
                error_msg = str(e.detail) if hasattr(e, 'detail') else str(e)
                logger.warning(f"describe-consumer-group failed (admin client error): {error_msg}")
                return json.dumps({
                    "message": f"Consumer group description failed: {error_msg}",
                    "group_id": group_id,
                    "note": "This requires admin client access to the Kafka controller (port 9093), which is only accessible internally within the Kubernetes cluster.",
                }, indent=2)
            except Exception as e:
                logger.error(f"describe-consumer-group failed: {e}", exc_info=True)
                return json.dumps({
                    "message": f"Consumer group description failed: {str(e)}",
                    "group_id": group_id,
                    "note": "This requires admin client access. The service may need to run inside the Kubernetes cluster.",
                }, indent=2)

        else:
            raise ValueError(f"Unknown tool: {tool_name}")

    except KeyError as e:
        raise HTTPException(status_code=400, detail=f"Missing required argument: {str(e)}")
    except KafkaError as e:
        raise HTTPException(status_code=500, detail=f"Kafka error: {str(e)}")
    except Exception as e:
        logger.error(f"Error in execute_tool: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Tool execution error: {str(e)}")


# Pydantic models for Swagger documentation
class TopicRequest(BaseModel):
    topic: str


class CreateTopicRequest(BaseModel):
    topic: str
    num_partitions: Optional[int] = 1
    replication_factor: Optional[int] = 1
    configs: Optional[Dict[str, str]] = {}


class ProduceMessageRequest(BaseModel):
    topic: str
    message: str
    key: Optional[str] = None
    headers: Optional[Dict[str, str]] = {}


class ConsumeMessagesRequest(BaseModel):
    topic: str
    timeout: Optional[int] = 5
    max_messages: Optional[int] = 10


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
@app.post("/tools/create-topic", tags=["Kafka Tools"])
async def create_topic_route(request: CreateTopicRequest):
    """Create a new Kafka topic."""
    session_id = _get_default_session()
    result = await execute_tool(
        "create-topic",
        {
            "topic": request.topic,
            "num_partitions": request.num_partitions,
            "replication_factor": request.replication_factor,
            "configs": request.configs or {},
        },
        session_id,
    )
    return JSONResponse(content=json.loads(result))


@app.post("/tools/list-topics", tags=["Kafka Tools"])
async def list_topics_route():
    """List all Kafka topics."""
    session_id = _get_default_session()
    result = await execute_tool("list-topics", {}, session_id)
    return JSONResponse(content=json.loads(result))


@app.post("/tools/delete-topic", tags=["Kafka Tools"])
async def delete_topic_route(request: TopicRequest):
    """Delete a Kafka topic."""
    session_id = _get_default_session()
    result = await execute_tool("delete-topic", {"topic": request.topic}, session_id)
    return JSONResponse(content=json.loads(result))


@app.post("/tools/describe-topic", tags=["Kafka Tools"])
async def describe_topic_route(request: TopicRequest):
    """Describe a Kafka topic."""
    session_id = _get_default_session()
    result = await execute_tool("describe-topic", {"topic": request.topic}, session_id)
    return JSONResponse(content=json.loads(result))


@app.post("/tools/produce-message", tags=["Kafka Tools"])
async def produce_message_route(request: ProduceMessageRequest):
    """Produce a message to a Kafka topic."""
    session_id = _get_default_session()
    result = await execute_tool(
        "produce-message",
        {
            "topic": request.topic,
            "message": request.message,
            "key": request.key,
            "headers": request.headers or {},
        },
        session_id,
    )
    return JSONResponse(content=json.loads(result))


@app.post("/tools/consume-messages", tags=["Kafka Tools"])
async def consume_messages_route(request: ConsumeMessagesRequest):
    """Consume messages from a Kafka topic."""
    session_id = _get_default_session()
    result = await execute_tool(
        "consume-messages",
        {
            "topic": request.topic,
            "timeout": request.timeout,
            "max_messages": request.max_messages,
        },
        session_id,
    )
    return JSONResponse(content=json.loads(result))


@app.post("/tools/list-broker", tags=["Kafka Tools"])
async def list_broker_route():
    """List all brokers in Kafka cluster."""
    session_id = _get_default_session()
    result = await execute_tool("list-broker", {}, session_id)
    return JSONResponse(content=json.loads(result))


@app.post("/tools/list-consumer", tags=["Kafka Tools"])
async def list_consumer_route():
    """List all consumer groups."""
    session_id = _get_default_session()
    result = await execute_tool("list-consumer", {}, session_id)
    return JSONResponse(content=json.loads(result))


class DescribeConsumerGroupRequest(BaseModel):
    group_id: str


@app.post("/tools/describe-consumer-group", tags=["Kafka Tools"])
async def describe_consumer_group_route(request: DescribeConsumerGroupRequest):
    """Describe a specific consumer group."""
    session_id = _get_default_session()
    result = await execute_tool("describe-consumer-group", {"group_id": request.group_id}, session_id)
    return JSONResponse(content=json.loads(result))


@app.get("/")
async def root():
    """Root endpoint with API information."""
    return {
        "service": "Kafka MCP Server",
        "version": "1.0.0",
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

    Uses a lightweight KafkaConsumer against KAFKA_BOOTSTRAP_SERVERS instead of the
    admin client to avoid protocol/version issues (e.g. MetadataRequest_v0).
    """
    try:
        bootstrap_servers = get_str("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
        logger.info("Health check: connecting KafkaConsumer to %s", bootstrap_servers)

        consumer = KafkaConsumer(
            bootstrap_servers=bootstrap_servers.split(","),
            consumer_timeout_ms=1000,
        )
        # Trigger metadata fetch
        consumer.poll(timeout_ms=1000)
        consumer.close()

        return JSONResponse(content={"status": "healthy"})
    except Exception as e:
        logger.error(f"Health check failed: {e}")
        return JSONResponse(
            status_code=503, content={"status": "unhealthy", "reason": str(e)}
        )


def run() -> None:
    """Run the Kafka MCP Server."""
    host = get_str("MCP_HOST", "0.0.0.0") or "0.0.0.0"
    port = get_int("MCP_PORT", 8000)

    import uvicorn

    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    run()

