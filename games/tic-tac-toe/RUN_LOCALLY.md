# Running All Services Locally

This guide provides commands to run all Tic-Tac-Toe services locally.

## Prerequisites

- Python 3.10+
- Node.js and npm
- Kafka running on `localhost:9092`
- MongoDB running on `localhost:27017`

## 1. Set Environment Variables

```bash
cd /home/ronbut/games/git/my-dev/games/tic-tac-toe
source infra/env-exports.sh
```

Or set them manually:
```bash
export KAFKA_BOOTSTRAP_SERVERS=localhost:9092
export KAFKA_STATE_TOPIC=game-state-updates
export KAFKA_MOVES_TOPIC=game-moves
export MONGO_URL=mongodb://localhost:27017
export MONGO_DB_NAME=admin
export MONGO_USE_AUTH=false
export GATEWAY_HOST=0.0.0.0
export GATEWAY_PORT=9000
export STATISTICS_PORT=8000
```

## 2. Game Logic Service

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

## 3. Gateway Service

Open a **new terminal**:

```bash
cd /home/ronbut/games/git/my-dev/games/tic-tac-toe
source infra/env-exports.sh

cd gateway-service

# Create virtual environment (first time only)
python3 -m venv venv
source venv/bin/activate

# Install dependencies (first time only)
pip install -r requirements.txt

# Run the service
python -m app
```

## 4. Archive Service (Optional)

Open a **new terminal**:

```bash
cd /home/ronbut/games/git/my-dev/games/tic-tac-toe
source infra/env-exports.sh

cd archive-service

# Create virtual environment (first time only)
python3 -m venv venv
source venv/bin/activate

# Install dependencies (first time only)
pip install -r requirements.txt

# Run the service
python -m service
```

## 5. Statistics Service (Optional)

Open a **new terminal**:

```bash
cd /home/ronbut/games/git/my-dev/games/tic-tac-toe
source infra/env-exports.sh

cd statistics-service

# Create virtual environment (first time only)
python3 -m venv venv
source venv/bin/activate

# Install dependencies (first time only)
pip install -r requirements.txt

# Run the service
python -m service
```

## 6. Frontend

Open a **new terminal**:

```bash
cd /home/ronbut/games/git/my-dev/games/tic-tac-toe/frontend

# Install dependencies (first time only)
npm install

# Start the development server
npm start
```

## Complete Startup Script

You can also create a script to start all services. Save this as `start-all.sh`:

```bash
#!/bin/bash

# Set environment variables
cd /home/ronbut/games/git/my-dev/games/tic-tac-toe
source infra/env-exports.sh

# Function to start a service in a new terminal
start_service() {
    local service_name=$1
    local service_dir=$2
    local command=$3
    
    gnome-terminal --tab --title="$service_name" -- bash -c "cd $service_dir && source venv/bin/activate && $command; exec bash"
}

# Start Game Logic Service
cd game-logic-service
if [ ! -d "venv" ]; then
    python3 -m venv venv
    source venv/bin/activate
    pip install -r requirements.txt
else
    source venv/bin/activate
fi
start_service "Game Logic" "$(pwd)" "python -m service"

# Start Gateway Service
cd ../gateway-service
if [ ! -d "venv" ]; then
    python3 -m venv venv
    source venv/bin/activate
    pip install -r requirements.txt
else
    source venv/bin/activate
fi
start_service "Gateway" "$(pwd)" "python -m app"

# Start Archive Service
cd ../archive-service
if [ ! -d "venv" ]; then
    python3 -m venv venv
    source venv/bin/activate
    pip install -r requirements.txt
else
    source venv/bin/activate
fi
start_service "Archive" "$(pwd)" "python -m service"

# Start Statistics Service
cd ../statistics-service
if [ ! -d "venv" ]; then
    python3 -m venv venv
    source venv/bin/activate
    pip install -r requirements.txt
else
    source venv/bin/activate
fi
start_service "Statistics" "$(pwd)" "python -m service"

# Start Frontend
cd ../frontend
if [ ! -d "node_modules" ]; then
    npm install
fi
start_service "Frontend" "$(pwd)" "npm start"

echo "All services started in separate terminal tabs"
```

Make it executable:
```bash
chmod +x start-all.sh
./start-all.sh
```

## Service URLs

Once all services are running:

- **Frontend**: http://localhost:3000
- **Gateway WebSocket**: ws://localhost:9000
- **Statistics API**: http://localhost:8000
- **Statistics Swagger**: http://localhost:8000/docs

## Verification

Check if services are running:
```bash
# Check Python services
ps aux | grep -E "python.*service|python.*app" | grep -v grep

# Check frontend
ps aux | grep "webpack\|node" | grep -v grep

# Test WebSocket connection
wscat -c ws://localhost:9000

# Test Statistics API
curl http://localhost:8000/statistic
```

## Stopping Services

Press `Ctrl+C` in each terminal, or:
```bash
# Kill all Python services
pkill -f "python.*service"
pkill -f "python.*app"

# Kill frontend
pkill -f "webpack"
```

