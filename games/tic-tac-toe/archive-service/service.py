from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Dict, Optional

from kafka import KafkaConsumer
from pymongo import MongoClient
from pymongo.collection import Collection
from pymongo.database import Database

from config import get_str, get_bool


logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)


def _create_consumer() -> KafkaConsumer:
    """Create and return a Kafka consumer for game state updates."""
    bootstrap_servers = get_str("KAFKA_BOOTSTRAP_SERVERS")
    if not bootstrap_servers:
        raise RuntimeError("KAFKA_BOOTSTRAP_SERVERS environment variable is required")

    topic = get_str("KAFKA_STATE_TOPIC", "game-state-updates")

    consumer = KafkaConsumer(
        topic,
        bootstrap_servers=bootstrap_servers,
        value_deserializer=lambda m: json.loads(m.decode("utf-8")),
        enable_auto_commit=True,
        auto_offset_reset="earliest",
        group_id=get_str("KAFKA_CONSUMER_GROUP", "archive-service"),
    )
    logger.info("Archive Service consuming state updates from topic %s", topic)
    return consumer


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
    except Exception as e:
        logger.error("Failed to connect to MongoDB: %s", e)
        raise
    
    return client


def _get_winner_player_id(state: Dict) -> Optional[str]:
    """
    Extract the winner's player ID from a game state.
    
    Args:
        state: Game state dictionary with 'winner' and 'players' fields
        
    Returns:
        The player ID of the winner, or None if no winner found
    """
    winner_symbol = state.get("winner")
    if not winner_symbol:
        return None
    
    players = state.get("players", {})
    # Find the player ID that has the winner symbol
    for player_id, symbol in players.items():
        if symbol == winner_symbol:
            return player_id
    
    return None


def _store_win(db: Database, player_id: str, game_id: str, event_timestamp: datetime) -> None:
    """
    Store a win record in MongoDB.
    
    Args:
        db: MongoDB database instance
        player_id: ID of the winning player
        game_id: ID of the game
        event_timestamp: Timestamp from the Kafka message event
    """
    collection_name = "wins"
    collection: Collection = db[collection_name]
    
    win_record = {
        "playerId": player_id,
        "gameId": game_id,
        "timestamp": event_timestamp,
    }
    
    result = collection.insert_one(win_record)
    logger.info(
        "Stored win record: player=%s, game=%s, timestamp=%s, record_id=%s",
        player_id,
        game_id,
        event_timestamp.isoformat(),
        result.inserted_id
    )


def run() -> None:
    """
    Main loop for the Archive Service.
    
    - Consumes game state updates from `game-state-updates` topic
    - Filters for GAME_OVER status
    - Extracts winner player ID and stores win records in MongoDB
    """
    consumer = _create_consumer()
    mongo_client = _create_mongo_client()
    
    db_name = get_str("MONGO_DB_NAME", "tic-tac-toe")
    db = mongo_client[db_name]
    
    logger.info("Archive Service started, connected to MongoDB database: %s", db_name)
    
    try:
        for message in consumer:
            state = message.value
            
            # Only process GAME_OVER states
            status = state.get("status")
            if status != "GAME_OVER":
                continue
            
            game_id = state.get("gameId")
            winner_player_id = _get_winner_player_id(state)
            
            if not winner_player_id:
                logger.warning(
                    "GAME_OVER state for game %s has no winner player ID. State: %s",
                    game_id,
                    state
                )
                continue
            
            # Get timestamp from Kafka message (in milliseconds since epoch)
            # Convert to datetime object in local timezone
            message_timestamp_ms = message.timestamp
            if message_timestamp_ms:
                # Kafka timestamp is in milliseconds, convert to seconds for datetime (naive datetime, local timezone)
                event_timestamp = datetime.fromtimestamp(message_timestamp_ms / 1000.0)
            else:
                # Fallback to current time if timestamp is not available
                logger.warning("Message timestamp not available, using current time")
                event_timestamp = datetime.now()
            
            # Store the win record with the event timestamp
            _store_win(db, winner_player_id, game_id, event_timestamp)
            
    except KeyboardInterrupt:
        logger.info("Archive Service shutting down...")
    except Exception as e:
        logger.error("Archive Service error: %s", e, exc_info=True)
        raise
    finally:
        consumer.close()
        mongo_client.close()
        logger.info("Archive Service closed connections")


if __name__ == "__main__":
    run()

