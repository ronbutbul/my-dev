from __future__ import annotations

import os
from typing import Any, Dict, Optional

"""
Configuration for the Kafka MCP Service.

Services should read values from this module first, and only fall back to
process environment variables if a key is not present here.
"""


DEFAULT_CONFIG: Dict[str, Any] = {
    # Kafka connection
    # For consumer/producer operations (uses EXTERNAL listener)
    "KAFKA_BOOTSTRAP_SERVERS": "kafka.testmpr.aws2.rafael.co.il:9095",
    # For admin operations (should use same as bootstrap, not CONTROLLER listener)
    # Note: CONTROLLER listener (9093) uses KRaft protocol, not regular Kafka protocol
    # Admin clients should use EXTERNAL (9095) or CLIENT (9092) listeners
    "KAFKA_ADMIN_SERVERS": None,  # If not set, uses KAFKA_BOOTSTRAP_SERVERS
    # API Server
    "MCP_HOST": "0.0.0.0",
    "MCP_PORT": "8000",
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


def get_int(key: str, default: int = 0) -> int:
    """
    Return a configuration value as an integer.

    Resolution order:
    1. OS environment variables take precedence (if set).
    2. Otherwise, use DEFAULT_CONFIG if the key exists and is not None.
    3. Otherwise, return the provided default.
    """
    env_value = os.environ.get(key)
    if env_value is not None:
        try:
            return int(env_value)
        except ValueError:
            return default
    if key in DEFAULT_CONFIG and DEFAULT_CONFIG[key] is not None:
        try:
            return int(DEFAULT_CONFIG[key])
        except (ValueError, TypeError):
            return default
    return default

