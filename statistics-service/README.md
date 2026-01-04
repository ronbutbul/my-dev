# Statistics Service

The Statistics Service provides a REST API to query win statistics from MongoDB.

## Purpose

- Reads win records from MongoDB
- Calculates statistics (wins per player)
- Exposes REST API with GET `/statistic` endpoint
- Includes Swagger UI for API documentation and testing

## Configuration

The service uses environment variables with fallback to defaults in `config.py`.

### Required Environment Variables

- `MONGO_URL`: MongoDB connection URL (default: `mongodb://localhost:27017`)

### Optional Environment Variables

- `MONGO_DB_NAME`: MongoDB database name (default: `tic-tac-toe`)
- `MONGO_USE_AUTH`: Enable MongoDB authentication (default: `false`)
- `MONGO_USERNAME`: MongoDB username (required if `MONGO_USE_AUTH=true`)
- `MONGO_PASSWORD`: MongoDB password (required if `MONGO_USE_AUTH=true`)
- `STATISTICS_HOST`: API server host (default: `0.0.0.0`)
- `STATISTICS_PORT`: API server port (default: `8000`)

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
   export MONGO_URL=mongodb://localhost:27017
   export MONGO_DB_NAME=tic-tac-toe
   export STATISTICS_PORT=8000
   ```

4. Run the service:
   ```bash
   python -m service
   ```

## API Endpoints

### GET `/statistic`

Get win statistics for all players.

**Response:**
```json
{
  "totalWins": 10,
  "players": [
    {"playerId": "ron", "wins": 6},
    {"playerId": "shimi", "wins": 4}
  ]
}
```

**Example:**
```bash
curl http://localhost:8000/statistic
```

### GET `/health`

Health check endpoint to verify service and MongoDB connection.

**Response:**
```json
{
  "status": "healthy"
}
```

### GET `/`

Root endpoint with API information.

### GET `/docs`

Swagger UI for interactive API documentation and testing.

### GET `/redoc`

ReDoc alternative API documentation.

## Swagger UI

Once the service is running, access Swagger UI at:
- http://localhost:8000/docs

You can:
- View all available endpoints
- Test endpoints directly from the browser
- See request/response schemas
- Try out the API without using curl

## Example Usage

```bash
# Get statistics
curl http://localhost:8000/statistic

# Health check
curl http://localhost:8000/health

# Access Swagger UI
# Open browser to http://localhost:8000/docs
```

