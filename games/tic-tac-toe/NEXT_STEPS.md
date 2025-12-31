# Next Steps - Complete System Setup

## Current Status
✅ Frontend running at `http://localhost:3000`  
✅ Gateway Service running on port 8080  
✅ KafkaUI running at `http://localhost:8090`  
✅ Kafka accessible at `localhost:9092`  
⏳ Game Logic Service - **NEXT TO START**

## Step 1: Start Game Logic Service

Open a **new terminal** and run:

```bash
cd /home/ronbut/games/tic-tac-toe/game-logic-service

# Create virtual environment (if not already done)
python3 -m venv venv
source venv/bin/activate

# Install dependencies (if not already done)
pip install -r requirements.txt

# Set environment variable and start
export KAFKA_BOOTSTRAP_SERVERS=localhost:9092
python -m service
```

You should see:
```
INFO:__main__:Game Logic Service consuming moves from topic game-moves
INFO:__main__:Game Logic Service will publish state updates
INFO:__main__:Game Logic Service started
```

## Step 2: Verify All Services Are Running

You should now have:
1. **Frontend** - `http://localhost:3000` (browser)
2. **Gateway Service** - Terminal 1 (port 8080)
3. **Game Logic Service** - Terminal 2 (consuming from Kafka)
4. **Kafka** - Running (port 9092)
5. **KafkaUI** - `http://localhost:8090` (browser)

## Step 3: Test the Game

### Test with Two Browser Windows:

1. **Open first browser window:**
   - Go to `http://localhost:3000`
   - Set Player ID: `player1`
   - Set Game ID: `game-1`
   - Wait for connection

2. **Open second browser window (or incognito):**
   - Go to `http://localhost:3000`
   - Set Player ID: `player2`
   - Set Game ID: `game-1` (same game!)
   - Wait for connection

3. **Play the game:**
   - Click a cell in the first window (player1)
   - Game will auto-create when player2 joins
   - Click a cell in the second window (player2)
   - Take turns clicking cells
   - Game will detect wins and draws automatically

### What to Watch:

- **Browser Console (F12)**: Check for WebSocket messages
- **Gateway Service Terminal**: Should show client connections and message forwarding
- **Game Logic Service Terminal**: Should show move processing, validation, and state updates
- **KafkaUI**: Go to `http://localhost:8090` and check:
  - `game-moves` topic - should show move events
  - `game-state-updates` topic - should show state updates

## Troubleshooting

### Game Logic Service can't connect to Kafka
- Make sure Kafka is running: `sudo docker ps | grep kafka`
- Verify `KAFKA_BOOTSTRAP_SERVERS=localhost:9092` is set
- Check that `/etc/hosts` has `127.0.0.1 kafka` entry

### No game state updates
- Check Game Logic Service terminal for errors
- Verify it's consuming from `game-moves` topic
- Check KafkaUI to see if messages are in topics

### WebSocket not connecting
- Make sure Gateway Service is running
- Check browser console for connection errors
- Verify Gateway is listening on port 8080

### Game doesn't start
- Make sure both players use the **same Game ID**
- First player's move will wait for second player
- Game auto-creates when second player joins

## Expected Flow

1. Player1 clicks cell → Frontend sends move → Gateway → Kafka `game-moves`
2. Game Logic consumes move → Creates game (waits for player2)
3. Player2 clicks cell → Frontend sends move → Gateway → Kafka `game-moves`
4. Game Logic consumes move → Creates game → Publishes initial state → Kafka `game-state-updates`
5. Gateway consumes state → Broadcasts to both clients via WebSocket
6. Both browsers update with game state
7. Players take turns, Game Logic validates, updates state, publishes to Kafka
8. Gateway broadcasts all state updates to all connected clients

## Success Indicators

✅ WebSocket connects (no "Pending" in Network tab)  
✅ Game state appears after both players join  
✅ Board updates when players click  
✅ Turn alternates between X and O  
✅ Win/draw detection works  
✅ Messages visible in KafkaUI topics  

