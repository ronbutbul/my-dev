from __future__ import annotations

import os
from typing import Any, Dict, Optional

"""
Configuration for the Gateway Service.

Values are taken from this file first; if a key is not present here, the
process environment is used as a fallback.
"""


DEFAULT_CONFIG: Dict[str, Any] = {
    "KAFKA_BOOTSTRAP_SERVERS": "kafka:9092",
    "KAFKA_MOVES_TOPIC": "game-moves",
    "KAFKA_STATE_TOPIC": "game-state-updates",
    "GATEWAY_CONSUMER_GROUP": "gateway-service-state-consumer",
    "GATEWAY_HOST": "0.0.0.0",
    "GATEWAY_PORT": "9000",
}


def get_str(key: str, default: Optional[str] = None) -> Optional[str]:
    # OS environment variables take precedence over config file defaults
    env_value = os.environ.get(key)
    if env_value is not None:
        return env_value
    # Fall back to config file default if no OS env var is set
    if key in DEFAULT_CONFIG and DEFAULT_CONFIG[key] is not None:
        return str(DEFAULT_CONFIG[key])
    return default


