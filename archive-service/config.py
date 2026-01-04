from __future__ import annotations

import os
from typing import Any, Dict, Optional

"""
Configuration for the Archive Service.

Services should read values from this module first, and only fall back to
process environment variables if a key is not present here.
"""


DEFAULT_CONFIG: Dict[str, Any] = {
    # Kafka connection and topics
    "KAFKA_BOOTSTRAP_SERVERS": "kafka:9092",
    "KAFKA_STATE_TOPIC": "game-state-updates",
    "KAFKA_CONSUMER_GROUP": "archive-service",
    # MongoDB connection
    "MONGO_URL": "mongodb://localhost:27017",
    "MONGO_DB_NAME": "tic-tac-toe",
    "MONGO_USE_AUTH": "false",  # Set to "true" to enable authentication
    "MONGO_USERNAME": None,
    "MONGO_PASSWORD": None,
}


def get_str(key: str, default: Optional[str] = None) -> Optional[str]:
    """
    Return a configuration value as a string.

    Resolution order:
    1. OS environment variables take precedence (if set).
    2. Otherwise, use DEFAULT_CONFIG if the key exists and is not None.
    3. Otherwise, return the provided default.
    """
    # OS environment variables take precedence over config file defaults
    env_value = os.environ.get(key)
    if env_value is not None:
        return env_value
    # Fall back to config file default if no OS env var is set
    if key in DEFAULT_CONFIG and DEFAULT_CONFIG[key] is not None:
        return str(DEFAULT_CONFIG[key])
    return default


def get_bool(key: str, default: bool = False) -> bool:
    """
    Return a configuration value as a boolean.

    Resolution order:
    1. OS environment variables take precedence (if set).
    2. Otherwise, use DEFAULT_CONFIG if the key exists and is not None.
    3. Otherwise, return the provided default.
    """
    env_value = os.environ.get(key)
    if env_value is not None:
        return env_value.lower() in ("true", "1", "yes", "on")
    if key in DEFAULT_CONFIG and DEFAULT_CONFIG[key] is not None:
        value = DEFAULT_CONFIG[key]
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.lower() in ("true", "1", "yes", "on")
    return default

