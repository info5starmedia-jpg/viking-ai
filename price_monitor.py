"""
price_monitor.py

Minimal, safe price monitor stub for Viking AI.

This module is intentionally designed to be import-safe:
- No scraping
- No network calls
- No external dependencies

The main entry point is poll_prices_once(), which returns
an empty list by default.
"""

import logging
import os
from dataclasses import dataclass
from typing import Any, Dict, List

logger = logging.getLogger("price_monitor")

__all__ = [
    "PRICE_MONITOR_ENABLED",
    "PriceAlert",
    "poll_prices_once",
]

# Toggle via environment variable (disabled if 0/false/empty)
PRICE_MONITOR_ENABLED = os.getenv("PRICE_MONITOR_ENABLED", "1").strip().lower() not in (
    "0",
    "false",
    "",
)


@dataclass(frozen=True)
class PriceAlert:
    """Simple data container for price alert messages."""
    title: str
    message: str
    metadata: Dict[str, Any] | None = None


def poll_prices_once() -> List[Dict[str, Any]]:
    """
    Poll prices one time and return alerts.

    This is a stub implementation that returns an empty list.
    """
    if not PRICE_MONITOR_ENABLED:
        logger.debug("Price monitor disabled; skipping poll.")
        return []

    logger.debug("Price monitor stub called; returning no alerts.")
    return []
