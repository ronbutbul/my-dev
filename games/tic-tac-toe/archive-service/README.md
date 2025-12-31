# Archive Service

The Archive Service consumes game state updates from Kafka and stores win records in MongoDB.

## Purpose

- Consumes from `game-state-updates` topic
- Filters for `GAME_OVER` status messages
- Extracts the winner's player ID from the game state
- Stores win records in MongoDB with:
  - Player ID
  - Game ID
  - Timestamp (UTC)

## Configuration

The service uses environment variables with fallback to defaults in `config.py`.

### Required Environment Variables

- `KAFKA_BOOTSTRAP_SERVERS`: Kafka broker address (default: `kafka:9092`)
- `MONGO_URL`: MongoDB connection URL (default: `mongodb://localhost:27017`)

### Optional Environment Variables

- `KAFKA_STATE_TOPIC`: Kafka topic to consume from (default: `game-state-updates`)
- `KAFKA_CONSUMER_GROUP`: Kafka consumer group ID (default: `archive-service`)
- `MONGO_DB_NAME`: MongoDB database name (default: `tic-tac-toe`)
- `MONGO_USE_AUTH`: Enable MongoDB authentication (default: `false`)
- `MONGO_USERNAME`: MongoDB username (required if `MONGO_USE_AUTH=true`)
- `MONGO_PASSWORD`: MongoDB password (required if `MONGO_USE_AUTH=true`)

## Setup

1. Create a virtual environment:
   ```bash
   python3 -m venv venv
   source venv/bin/activate
   ```

2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

3. Set environment variables (optional):
   ```bash
   export KAFKA_BOOTSTRAP_SERVERS=localhost:9092
   export MONGO_URL=mongodb://localhost:27017
   export MONGO_DB_NAME=tic-tac-toe
   export MONGO_USE_AUTH=false
   ```

4. Run the service:
   ```bash
   python -m service
   ```

## MongoDB Authentication

To use MongoDB with authentication:

```bash
export MONGO_USE_AUTH=true
export MONGO_USERNAME=your_username
export MONGO_PASSWORD=your_password
export MONGO_URL=mongodb://localhost:27017
```

The service will automatically add credentials to the MongoDB URL.

## Data Structure

Win records are stored in the `wins` collection with the following structure:

```json
{
  "_id": ObjectId("..."),
  "playerId": "ron",
  "gameId": "game-1",
  "timestamp": ISODate("2024-01-15T10:30:00.000Z")
}
```

## Example Queries

Query all wins for a player:
```javascript
db.wins.find({ playerId: "ron" })
```

Query wins in a date range:
```javascript
db.wins.find({
  timestamp: {
    $gte: ISODate("2024-01-01"),
    $lt: ISODate("2024-02-01")
  }
})
```

Count wins per player:
```javascript
db.wins.aggregate([
  { $group: { _id: "$playerId", wins: { $sum: 1 } } },
  { $sort: { wins: -1 } }
])
```

