# Tic-Tac-Toe Multiplayer Game

A fully working multiplayer Tic-Tac-Toe game with microservices architecture, using React frontend, Python backend services, Kafka for event streaming, and MongoDB for data persistence.

## Architecture Overview

- **Frontend**: React application (WebSocket client)
- **Gateway Service**: WebSocket server that forwards moves to Kafka and broadcasts state updates
- **Game Logic Service**: Authoritative game logic, consumes moves from Kafka, publishes state updates
- **Archive Service**: Consumes game state updates, stores win records in MongoDB
- **Statistics Service**: REST API to query win statistics from MongoDB
- **Kafka**: Event bus for service-to-service communication
- **MongoDB**: Database for storing win records

## Service URLs and Ports

| Service | URL/Port | Description |
|---------|----------|-------------|
| **Frontend** | http://localhost:3000 | React web application |
| **Gateway Service** | ws://localhost:9000 | WebSocket server |
| **Statistics Service** | http://localhost:8000 | REST API |
| **Statistics Swagger UI** | http://localhost:8000/docs | Interactive API documentation |
| **Kafka** | localhost:9092 | Kafka broker |
| **MongoDB** | localhost:27017 | MongoDB database |

## Prerequisites

- Python 3.10+
- Node.js and npm
- Kafka (running on localhost:9092)
- MongoDB (running on localhost:27017)

## Quick Start

### 1. Set Environment Variables

Source the environment variables file:

```bash
cd /home/ronbut/games/tic-tac-toe
source infra/env-exports.sh
```

Or manually set the required variables:

```bash
export KAFKA_BOOTSTRAP_SERVERS=localhost:9092
export MONGO_URL=mongodb://localhost:27017
export MONGO_DB_NAME=admin
export GATEWAY_PORT=9000
export STATISTICS_PORT=8000
```

### 2. Start Kafka

Ensure Kafka is running on `localhost:9092`. If using Docker:

```bash
# Check if Kafka is running
docker ps | grep kafka

# Or start Kafka using docker-compose (if available)
cd infra
docker-compose up -d kafka
```

### 3. Start MongoDB

Ensure MongoDB is running on `localhost:27017`. If using Docker:

```bash
# Check if MongoDB is running
docker ps | grep mongo

# Or start MongoDB
docker run -d -p 27017:27017 --name mongodb mongo:latest
```

### 4. Start Game Logic Service

The Game Logic Service is the core service that processes game moves and maintains authoritative game state.

```bash
cd game-logic-service

# Create virtual environment (first time only)
python3 -m venv venv
source venv/bin/activate

# Install dependencies (first time only)
pip install -r requirements.txt

# Run the service
python -m service
```

**Expected output:**
```
INFO:__main__:Game Logic Service started
INFO:__main__:Game Logic Service consuming moves from topic game-moves
```

### 5. Start Gateway Service

The Gateway Service handles WebSocket connections from clients and forwards messages to/from Kafka.

```bash
cd gateway-service

# Create virtual environment (first time only)
python3 -m venv venv
source venv/bin/activate

# Install dependencies (first time only)
pip install -r requirements.txt

# Run the service
python -m app
```

**Expected output:**
```
INFO:__main__:Gateway WebSocket server listening on 0.0.0.0:9000
```

### 6. Start Archive Service (Optional)

The Archive Service consumes game state updates and stores win records in MongoDB.

```bash
cd archive-service

# Create virtual environment (first time only)
python3 -m venv venv
source venv/bin/activate

# Install dependencies (first time only)
pip install -r requirements.txt

# Run the service
python -m service
```

**Expected output:**
```
INFO:__main__:Archive Service consuming state updates from topic game-state-updates
INFO:__main__:Archive Service started, connected to MongoDB database: admin
```

### 7. Start Statistics Service (Optional)

The Statistics Service provides a REST API to query win statistics.

```bash
cd statistics-service

# Create virtual environment (first time only)
python3 -m venv venv
source venv/bin/activate

# Install dependencies (first time only)
pip install -r requirements.txt

# Run the service
python -m service
```

**Expected output:**
```
INFO:__main__:Statistics Service started successfully
INFO:     Started server process
INFO:     Uvicorn running on http://0.0.0.0:8000
```

### 8. Start Frontend

The React frontend application.

```bash
cd frontend

# Install dependencies (first time only)
npm install

# Start the development server
npm start
```

**Expected output:**
```
webpack compiled successfully
```

The frontend will be available at http://localhost:3000

## Complete Startup Order

For a complete setup, start services in this order:

1. **Kafka** (if not already running)
2. **MongoDB** (if not already running)
3. **Game Logic Service** (required)
4. **Gateway Service** (required)
5. **Archive Service** (optional - for win tracking)
6. **Statistics Service** (optional - for statistics API)
7. **Frontend** (required for playing)

## Testing the Application

### 1. Play the Game

1. Open http://localhost:3000 in your browser
2. Enter a Player ID (e.g., "player1")
3. Enter a Game ID (e.g., "game-1")
4. Open another browser window/tab
5. Enter a different Player ID (e.g., "player2") and the same Game ID
6. Start playing!

### 2. Check Statistics

```bash
# Get win statistics
curl http://localhost:8000/statistic

# Expected response:
# {
#   "totalWins": 5,
#   "players": [
#     {"playerId": "ron", "wins": 3},
#     {"playerId": "shimi", "wins": 2}
#   ]
# }
```

### 3. Use Swagger UI

1. Open http://localhost:8000/docs in your browser
2. Explore available endpoints
3. Test the `/statistic` endpoint directly from the browser

### 4. Check MongoDB

```bash
# Connect to MongoDB
mongosh mongodb://localhost:27017

# Switch to database
use admin

# Query wins collection
db.wins.find().pretty()

# Count wins per player
db.wins.aggregate([
  { $group: { _id: "$playerId", wins: { $sum: 1 } } },
  { $sort: { wins: -1 } }
])
```

## Service Dependencies

```
Frontend
  └─> Gateway Service (WebSocket)
        └─> Kafka
              └─> Game Logic Service
                    └─> Kafka (state updates)
                          └─> Gateway Service (broadcast to clients)
                          └─> Archive Service (store wins)
                                └─> MongoDB
                                      └─> Statistics Service (read stats)
```

## Environment Variables Reference

All services support environment variables. See `infra/env-exports.sh` for all available variables.

### Common Variables

- `KAFKA_BOOTSTRAP_SERVERS`: Kafka broker address (default: `localhost:9092`)
- `MONGO_URL`: MongoDB connection URL (default: `mongodb://localhost:27017`)
- `MONGO_DB_NAME`: MongoDB database name (default: `admin`)
- `MONGO_USE_AUTH`: Enable MongoDB authentication (default: `false`)

### Service-Specific Variables

**Game Logic Service:**
- `KAFKA_MOVES_TOPIC`: Topic for game moves (default: `game-moves`)
- `KAFKA_STATE_TOPIC`: Topic for state updates (default: `game-state-updates`)
- `KAFKA_CONSUMER_GROUP`: Consumer group ID (default: `game-logic-service`)

**Gateway Service:**
- `GATEWAY_HOST`: WebSocket server host (default: `0.0.0.0`)
- `GATEWAY_PORT`: WebSocket server port (default: `9000`)

**Statistics Service:**
- `STATISTICS_HOST`: API server host (default: `0.0.0.0`)
- `STATISTICS_PORT`: API server port (default: `8000`)

## Troubleshooting

### Gateway Service: "address already in use"
- Change `GATEWAY_PORT` to a different port (e.g., `9001`)
- Or stop the process using port 9000: `lsof -ti:9000 | xargs kill`

### Kafka Connection Issues
- Verify Kafka is running: `docker ps | grep kafka`
- Check Kafka logs: `docker logs <kafka-container-id>`
- Verify `KAFKA_BOOTSTRAP_SERVERS` environment variable

### MongoDB Connection Issues
- Verify MongoDB is running: `docker ps | grep mongo`
- Test connection: `mongosh mongodb://localhost:27017`
- Check `MONGO_URL` environment variable

### Frontend Not Connecting
- Verify Gateway Service is running on port 9000
- Check browser console for WebSocket connection errors
- Verify `GATEWAY_PORT` matches frontend WebSocket URL

## Development

### Running Tests

```bash
# Game Logic Service tests
cd game-logic-service
source venv/bin/activate
python -m pytest test_game.py
```

### Code Structure

```
tic-tac-toe/
├── frontend/              # React frontend
├── gateway-service/       # WebSocket gateway
├── game-logic-service/    # Core game logic
├── archive-service/       # Win record archiving
├── statistics-service/     # Statistics API
├── infra/                 # Infrastructure configs
│   └── env-exports.sh    # Environment variables
└── k8s/                   # Kubernetes manifests
```

## License

This project is part of a microservices architecture demonstration.

