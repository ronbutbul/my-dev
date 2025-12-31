# KafkaUI Setup Guide

KafkaUI is a web-based UI for managing and monitoring Apache Kafka clusters.

## Quick Start

### Option 1: Run KafkaUI with Docker (Recommended)

**If your Kafka is running on localhost:9092:**
```bash
docker run -d \
  --name kafka-ui \
  -p 8080:8080 \
  -e KAFKA_CLUSTERS_0_NAME=local \
  -e KAFKA_CLUSTERS_0_BOOTSTRAPSERVERS=host.docker.internal:9092 \
  provectuslabs/kafka-ui:latest
```

**If your Kafka is running in Docker with network:**
```bash
docker run -d \
  --name kafka-ui \
  -p 8080:8080 \
  --network host \
  -e KAFKA_CLUSTERS_0_NAME=local \
  -e KAFKA_CLUSTERS_0_BOOTSTRAPSERVERS=localhost:9092 \
  provectuslabs/kafka-ui:latest
```

**Access KafkaUI:**
Open your browser to: `http://localhost:8080`

### Option 2: Run KafkaUI with Docker Compose (Easier Management)

Create a `docker-compose-kafkaui.yml` file:

```yaml
version: '3.8'
services:
  kafka-ui:
    image: provectuslabs/kafka-ui:latest
    container_name: kafka-ui
    ports:
      - "8080:8080"
    environment:
      KAFKA_CLUSTERS_0_NAME: local
      KAFKA_CLUSTERS_0_BOOTSTRAPSERVERS: host.docker.internal:9092
    restart: unless-stopped
```

Then run:
```bash
docker-compose -f docker-compose-kafkaui.yml up -d
```

## Troubleshooting

### Docker daemon not running
```bash
# Start Docker daemon (Ubuntu)
sudo systemctl start docker

# Or if using Docker Desktop, start it from the application menu
```

### Can't connect to Kafka from KafkaUI

**If Kafka is running on localhost:9092:**
- Use `host.docker.internal:9092` (works on Linux/Mac/Windows)
- Or use `--network host` flag (Linux only)

**If Kafka is in Docker:**
- Use the Docker network name (e.g., `kafka:9092`)
- Or connect both containers to the same network

### Port 8080 already in use

If port 8080 is used by your Gateway Service, use a different port:
```bash
docker run -d \
  --name kafka-ui \
  -p 8090:8080 \
  -e KAFKA_CLUSTERS_0_NAME=local \
  -e KAFKA_CLUSTERS_0_BOOTSTRAPSERVERS=host.docker.internal:9092 \
  provectuslabs/kafka-ui:latest
```

Then access at: `http://localhost:8090`

## What You Can Do in KafkaUI

- View all Kafka topics
- Browse messages in topics
- See consumer groups
- Monitor cluster health
- View topic configurations
- Send test messages
- View message schemas

## Stop KafkaUI

```bash
docker stop kafka-ui
docker rm kafka-ui
```

Or if using docker-compose:
```bash
docker-compose -f docker-compose-kafkaui.yml down
```

