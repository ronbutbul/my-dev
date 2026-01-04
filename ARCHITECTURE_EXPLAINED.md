# Tic-Tac-Toe Architecture - Simple Explanation

## The Services (Simple Version)

### 1. **Frontend (React)**
**Purpose:** The user interface - what players see and click.

**What it does:**
- Shows a 3×3 board on the screen
- When you click a cell, it sends: `{gameId, playerId, row, col}` to the Gateway
- Receives game state updates and displays them
- **Does NOT decide** if moves are valid, whose turn it is, or who won
- Just displays whatever the server tells it

**Think of it as:** A dumb display that shows what the server says.

---

### 2. **Gateway Service (Python + WebSocket on port 8080)**
**Purpose:** The entry point - all clients connect here.

**What it does:**
- Listens for WebSocket connections from the frontend
- When it receives a move from a client → forwards it to Kafka topic `game-moves`
- Subscribes to Kafka topic `game-state-updates`
- When it gets a state update from Kafka → broadcasts it to all connected clients
- **Does NOT validate moves or apply rules** - just forwards messages

**Think of it as:** A messenger that takes messages from clients and puts them in Kafka, and takes messages from Kafka and gives them to clients.

---

### 3. **Game Logic Service (Python)**
**Purpose:** The brain - the ONLY place where game rules exist.

**What it does:**
- Listens to Kafka topic `game-moves` for move requests
- **Auto-creates games** when the second player joins (first two players become X and O)
- For each move, it:
  - Checks if it's valid (correct player, correct turn, empty cell, game still running)
  - Updates the board
  - Checks for win or draw
  - Switches turns
- Publishes the updated game state to Kafka topic `game-state-updates`
- **This is the ONLY service that knows the rules**

**Think of it as:** The referee who decides if moves are legal and who wins.

---


---

### 5. **Kafka (Event Bus)**
**Purpose:** The message queue that connects services.

**What it does:**
- Stores messages in topics (like a mailbox)
- `game-moves` topic: stores move requests from clients
- `game-state-updates` topic: stores authoritative game state snapshots
- Services publish (write) and consume (read) from these topics
- **Services never talk directly to each other** - they only talk through Kafka

**Think of it as:** A post office where services leave messages for each other.

---

## The Flow (Step by Step)

### When a Player Makes a Move:

1. **Player clicks a cell** in the Frontend
2. **Frontend sends** `{gameId, playerId, row, col}` to Gateway via WebSocket
3. **Gateway receives** the move and publishes it to Kafka topic `game-moves`
4. **Game Logic Service** consumes the move from `game-moves`
5. **Game Logic Service** validates and applies the move:
   - Checks if it's valid
   - Updates the board
   - Checks for win/draw
   - Switches turn
6. **Game Logic Service** publishes the new state to Kafka topic `game-state-updates`
7. **Gateway** (and **Broadcaster**) consume the state update from `game-state-updates`
8. **Gateway** (and **Broadcaster**) broadcast the state to all connected clients via WebSocket
9. **Frontend** receives the state and updates the display

---

## Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────┐
│                         PLAYERS (Browser)                        │
│                                                                   │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │              Frontend (React)                            │   │
│  │  - Shows 3×3 board                                       │   │
│  │  - Sends moves: {gameId, playerId, row, col}            │   │
│  │  - Displays state received from server                   │   │
│  └──────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
                              │
                              │ WebSocket (port 8080)
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                    Gateway Service (Python)                      │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │  WebSocket Server (port 8080)                           │   │
│  │  - Receives moves from clients                          │   │
│  │  - Forwards moves → Kafka                               │   │
│  │  - Consumes state updates from Kafka                    │   │
│  │  - Broadcasts state updates → clients                    │   │
│  └──────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
                              │
                              │ Kafka Topics
                              │
        ┌─────────────────────┴─────────────────────┐
        │                                           │
        ▼                                           ▼
┌───────────────────────┐              ┌───────────────────────┐
│   Kafka (Event Bus)   │              │  Game Logic Service   │
│                       │              │      (Python)          │
│  Topics:              │              │                       │
│  • game-moves         │◄─────────────│  - Consumes moves     │
│  • game-state-updates │              │  - Validates moves    │
│                       │              │  - Applies moves      │
│                       │              │  - Checks win/draw    │
│                       │              │  - Publishes state    │
│                       │──────────────►│                       │
└───────────────────────┘              └───────────────────────┘
```

---

## Key Principles

1. **Server is Authoritative:** Only the Game Logic Service knows the rules. Frontend is dumb.

2. **No Direct Service Calls:** Services never call each other directly. They only communicate through Kafka.

3. **Event-Driven:** Everything happens through events (messages in Kafka topics).

4. **Separation of Concerns:**
   - Frontend = Display
   - Gateway = Message forwarding
   - Game Logic = Rules and validation
   - Broadcaster = State broadcasting (redundant currently)
   - Kafka = Message queue

---

## Game Creation

Games are **auto-created** when the first move arrives:
- When a move arrives for a new `gameId`, the Game Logic Service tracks the player
- When a second player joins the same `gameId`, the game is created
- First player becomes X, second player becomes O
- The game starts and the initial state is published

