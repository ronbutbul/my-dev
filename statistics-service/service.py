from __future__ import annotations

import logging
from typing import Dict, List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pymongo import MongoClient
from pymongo.collection import Collection
from pymongo.database import Database
from pymongo.errors import ConnectionFailure, OperationFailure

from config import get_str, get_bool, get_int


logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

app = FastAPI(
    title="Tic-Tac-Toe Statistics API",
    description="API for retrieving game statistics from MongoDB",
    version="1.0.0",
)


def _create_mongo_client() -> MongoClient:
    """Create and return a MongoDB client with optional authentication."""
    mongo_url = get_str("MONGO_URL", "mongodb://localhost:27017")
    use_auth = get_bool("MONGO_USE_AUTH", False)
    
    if use_auth:
        username = get_str("MONGO_USERNAME")
        password = get_str("MONGO_PASSWORD")
        if not username or not password:
            raise RuntimeError(
                "MONGO_USERNAME and MONGO_PASSWORD are required when MONGO_USE_AUTH is true"
            )
        # Parse the URL and add credentials if not already present
        if "@" not in mongo_url:
            # Insert credentials into URL
            if mongo_url.startswith("mongodb://"):
                mongo_url = mongo_url.replace("mongodb://", f"mongodb://{username}:{password}@", 1)
            elif mongo_url.startswith("mongodb+srv://"):
                mongo_url = mongo_url.replace("mongodb+srv://", f"mongodb+srv://{username}:{password}@", 1)
        # Extract host for logging (hide credentials)
        log_url = mongo_url.split("@")[-1] if "@" in mongo_url else mongo_url
        logger.info("Connecting to MongoDB with authentication at %s", log_url)
    else:
        logger.info("Connecting to MongoDB without authentication at %s", mongo_url)
    
    try:
        client = MongoClient(mongo_url)
        # Test the connection
        client.admin.command("ping")
        logger.info("Successfully connected to MongoDB")
    except ConnectionFailure as e:
        logger.error("Failed to connect to MongoDB: %s", e)
        raise
    except Exception as e:
        logger.error("Error connecting to MongoDB: %s", e)
        raise
    
    return client


def _get_database() -> Database:
    """Get the MongoDB database instance."""
    db_name = get_str("MONGO_DB_NAME", "tic-tac-toe")
    return mongo_client[db_name]


def _get_wins_collection() -> Collection:
    """Get the wins collection from MongoDB."""
    db = _get_database()
    return db["wins"]


def _calculate_statistics() -> Dict[str, any]:
    """
    Calculate win statistics from MongoDB.
    
    Returns:
        Dictionary with statistics including wins per player
    """
    try:
        collection = _get_wins_collection()
        
        # Aggregate wins per player
        pipeline = [
            {
                "$group": {
                    "_id": "$playerId",
                    "wins": {"$sum": 1}
                }
            },
            {
                "$sort": {"wins": -1}  # Sort by wins descending
            },
            {
                "$project": {
                    "_id": 0,
                    "playerId": "$_id",
                    "wins": 1
                }
            }
        ]
        
        results = list(collection.aggregate(pipeline))
        
        # Calculate total wins
        total_wins = sum(player["wins"] for player in results)
        
        # Format response
        statistics = {
            "totalWins": total_wins,
            "players": results
        }
        
        return statistics
        
    except OperationFailure as e:
        logger.error("MongoDB operation failed: %s", e)
        raise HTTPException(status_code=500, detail=f"MongoDB operation failed: {str(e)}")
    except Exception as e:
        logger.error("Error calculating statistics: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error calculating statistics: {str(e)}")


# Global MongoDB client (initialized on startup)
mongo_client: Optional[MongoClient] = None


@app.on_event("startup")
async def startup_event():
    """Initialize MongoDB connection on startup."""
    global mongo_client
    try:
        mongo_client = _create_mongo_client()
        logger.info("Statistics Service started successfully")
    except Exception as e:
        logger.error("Failed to start Statistics Service: %s", e)
        raise


@app.on_event("shutdown")
async def shutdown_event():
    """Close MongoDB connection on shutdown."""
    global mongo_client
    if mongo_client:
        mongo_client.close()
        logger.info("Statistics Service closed MongoDB connection")


@app.get("/")
async def root():
    """Root endpoint with API information."""
    return {
        "service": "Tic-Tac-Toe Statistics API",
        "version": "1.0.0",
        "endpoints": {
            "/statistic": "GET - Get win statistics per player",
            "/docs": "Swagger UI documentation",
            "/redoc": "ReDoc documentation"
        }
    }


@app.get("/statistic", tags=["Statistics"])
async def get_statistics():
    """
    Get win statistics for all players.
    
    Returns:
        JSON response with:
        - totalWins: Total number of wins across all players
        - players: List of players with their win counts, sorted by wins (descending)
    
    Example response:
    ```json
    {
        "totalWins": 10,
        "players": [
            {"playerId": "ron", "wins": 6},
            {"playerId": "shimi", "wins": 4}
        ]
    }
    ```
    """
    try:
        statistics = _calculate_statistics()
        return JSONResponse(content=statistics)
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Unexpected error in get_statistics: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    try:
        if mongo_client is None:
            return JSONResponse(
                status_code=503,
                content={"status": "unhealthy", "reason": "MongoDB not connected"}
            )
        
        # Test MongoDB connection
        mongo_client.admin.command("ping")
        return JSONResponse(content={"status": "healthy"})
    except Exception as e:
        logger.error("Health check failed: %s", e)
        return JSONResponse(
            status_code=503,
            content={"status": "unhealthy", "reason": str(e)}
        )


def run() -> None:
    """Run the Statistics Service API server."""
    host = get_str("STATISTICS_HOST", "0.0.0.0") or "0.0.0.0"
    port = get_int("STATISTICS_PORT", 8000)
    
    import uvicorn
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    run()

