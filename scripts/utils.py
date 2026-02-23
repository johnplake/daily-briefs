"""Utility helpers for daily-briefs scripts."""

import json
from typing import Any, Callable


def safe_json_load(value: str | None, default: Any = None, warn_fn: Callable[[str], None] | None = None):
    """Safely parse JSON with fallback.

    Args:
        value: JSON string (or None)
        default: value to return if missing or malformed (defaults to [])
        warn_fn: optional function to call with warning message
    """
    if default is None:
        default = []
    if not value:
        return default
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        msg = f"Malformed JSON: {value[:100]}"
        if warn_fn:
            warn_fn(msg)
        else:
            print(msg)
        return default
