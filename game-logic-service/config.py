from __future__ import annotations

import os
from typing import Any, Dict, Optional

"""
Configuration for the Game Logic Service.

Services should read values from this module first, and only fall back to
process environment variables if a key is not present here.
"""


DEFAULT_CONFIG: Dict[str, Any] = {
    # Kafka connection and topics
    "KAFKA_BOOTSTRAP_SERVERS": "kafka:9092",
    "KAFKA_MOVES_TOPIC": "game-moves",
    "KAFKA_STATE_TOPIC": "game-state-updates",
    "KAFKA_CONSUMER_GROUP": "game-logic-service",
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


