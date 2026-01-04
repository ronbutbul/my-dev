# Quick Start Guide - Full System

## Current Status
✅ Frontend is running at `http://localhost:3000`  
⏳ WebSocket is trying to connect to `ws://localhost:8080` (waiting for Gateway)

## To Get Everything Working

### Option 1: Quick Test with Docker Kafka (Easiest)

**Terminal 1: Start Kafka**
```bash
docker run -d --name kafka -p 9092:9092 apache/kafka:latest
```

**Terminal 2: Start Gateway Service**
```bash
cd /home/ronbut/games/tic-tac-toe/gateway-service
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
export KAFKA_BOOTSTRAP_SERVERS=localhost:9092
python app.py
```

**Terminal 3: Start Game Logic Service**
```bash
cd /home/ronbut/games/tic-tac-toe/game-logic-service
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
export KAFKA_BOOTSTRAP_SERVERS=localhost:9092
python -m service
```

**Terminal 4: Frontend (already running)**
- Should already be at `http://localhost:3000`
- WebSocket will connect once Gateway is running

### Option 2: Install Kafka Locally (More Setup)

If you prefer not to use Docker, you'll need to install Kafka manually. Docker is recommended for quick testing.

## Testing the Game

1. Open `http://localhost:3000` in your browser
2. Open a second browser window/tab (or use incognito)
3. In first window: Set Player ID to `player1`, Game ID to `game-1`
4. In second window: Set Player ID to `player2`, Game ID to `game-1`
5. Click a cell in the first window - game will auto-create when second player joins
6. Click a cell in the second window - game starts!
7. Take turns clicking cells

## Troubleshooting

**WebSocket stays "Pending":**
- Make sure Gateway Service is running on port 8080
- Check Terminal 2 for errors

**"Connection refused" errors:**
- Kafka might not be running
- Check `docker ps` to see if Kafka container is running

**No game state updates:**
- Make sure Game Logic Service is running
- Check that both services can connect to Kafka

