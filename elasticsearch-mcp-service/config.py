from __future__ import annotations

import os
from typing import Any, Dict, Optional

"""
Configuration for the Elasticsearch MCP Service.

Services should read values from this module first, and only fall back to
process environment variables if a key is not present here.
"""


DEFAULT_CONFIG: Dict[str, Any] = {
    # Elasticsearch connection
    "ES_MCP_CONNECTION_STRING": "http://localhost:9200",
    "ES_USE_AUTH": "false",  # Set to "true" to enable basic authentication
    "ES_USERNAME": None,
    "ES_PASSWORD": None,
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
    env_value = os.environ.get(key)
    if env_value is not None:
        return env_value
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

