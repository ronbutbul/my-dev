from __future__ import annotations

import os
from typing import Any, Dict, Optional

"""
Configuration for the Statistics Service.

Services should read values from this module first, and only fall back to
process environment variables if a key is not present here.
"""


DEFAULT_CONFIG: Dict[str, Any] = {
    # MongoDB connection
    "MONGO_URL": "mongodb://localhost:27017",
    "MONGO_DB_NAME": "admin",
    "MONGO_USE_AUTH": "false",  # Set to "true" to enable authentication
    "MONGO_USERNAME": None,
    "MONGO_PASSWORD": None,
    # API Server
    "STATISTICS_HOST": "0.0.0.0",
    "STATISTICS_PORT": "8000",
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

