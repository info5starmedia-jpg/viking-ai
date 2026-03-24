"""
price_monitor.py

Real price-drop monitor for Viking AI.

Reads watched events from the drop_watch_events SQLite table, polls
Ticketmaster for current price ranges, and returns alerts when prices
drop by more than PRICE_DROP_THRESHOLD_PCT (default 10%).

Price history is persisted in price_state.json so previous prices
survive restarts without touching drop_catcher's DB state.

Env vars:
  VIKING_DB_PATH             – SQLite path (default /opt/viking-ai/viking_ai.sqlite)
  PRICE_MONITOR_ENABLED      – set to 0 to disable (default 1)
  PRICE_DROP_THRESHOLD_PCT   – minimum % drop to alert (default 10)
  PRICE_STATE_FILE           – path to price state JSON (default alongside this file)
  PRICE_PREFIX               – prefix string for alert titles (default [PRICE])
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

try:
    from dotenv import load_dotenv
except Exception:
    load_dotenv = None  # type: ignore

if load_dotenv:
    load_dotenv("/opt/viking-ai/.env", override=False)

logger = logging.getLogger("price_monitor")

PRICE_PREFIX = (os.getenv("PRICE_PREFIX") or "[PRICE]").strip()
PRICE_MONITOR_ENABLED = os.getenv("PRICE_MONITOR_ENABLED", "1").strip().lower() not in ("0", "false", "")
PRICE_DROP_THRESHOLD_PCT = float(os.getenv("PRICE_DROP_THRESHOLD_PCT", "10") or "10")
DB_PATH = os.getenv("VIKING_DB_PATH", "/opt/viking-ai/viking_ai.sqlite")
PRICE_STATE_FILE = os.getenv(
    "PRICE_STATE_FILE",
    os.path.join(os.path.dirname(__file__), "price_state.json"),
)

__all__ = [
    "PRICE_MONITOR_ENABLED",
    "PriceAlert",
    "poll_prices_once",
]

# ---------------------------------------------------------------------------
# Optional TM import
# ---------------------------------------------------------------------------

try:
    import ticketmaster_agent as _ta  # type: ignore
except Exception as _e:
    _ta = None  # type: ignore
    logger.warning("price_monitor: ticketmaster_agent not available: %s", _e)


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PriceAlert:
    title: str
    message: str
    metadata: Optional[Dict[str, Any]] = None


# ---------------------------------------------------------------------------
# Price state (persisted JSON so history survives restarts)
# ---------------------------------------------------------------------------

def _load_price_state() -> Dict[str, Any]:
    if not os.path.exists(PRICE_STATE_FILE):
        return {}
    try:
        with open(PRICE_STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception:
        return {}


def _save_price_state(state: Dict[str, Any]) -> None:
    try:
        with open(PRICE_STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2, sort_keys=True)
    except Exception as e:
        logger.warning("price_monitor: failed saving price state: %s", e)


# ---------------------------------------------------------------------------
# Watched events source (reads drop_watch_events table)
# ---------------------------------------------------------------------------

def _get_watched_events() -> List[Dict[str, Any]]:
    """Return non-expired watched events from drop_watch_events."""
    try:
        conn = sqlite3.connect(DB_PATH, timeout=10)
        rows = conn.execute(
            """
            SELECT event_id, artist, name, url
            FROM drop_watch_events
            WHERE expires_at_unix > ?
            """,
            (time.time(),),
        ).fetchall()
        conn.close()
        return [{"event_id": r[0], "artist": r[1], "name": r[2], "url": r[3]} for r in rows]
    except Exception as e:
        logger.debug("price_monitor: could not read drop_watch_events: %s", e)
        return []


# ---------------------------------------------------------------------------
# Price extraction
# ---------------------------------------------------------------------------

def _extract_prices(event: Dict[str, Any]) -> Optional[Dict[str, float]]:
    """Return {min, max} price dict from a TM event, or None."""
    price_ranges = event.get("priceRanges") or []
    if not price_ranges:
        return None
    try:
        pmin = min(p.get("min", 0) for p in price_ranges if p.get("min") is not None)
        pmax = max(p.get("max", 0) for p in price_ranges if p.get("max") is not None)
        if pmin > 0:
            return {"min": float(pmin), "max": float(pmax)}
    except Exception:
        pass
    return None


def _pct_change(old: float, new: float) -> float:
    if old <= 0:
        return 0.0
    return ((new - old) / old) * 100.0


# ---------------------------------------------------------------------------
# Main poll function
# ---------------------------------------------------------------------------

def poll_prices_once() -> List[Dict[str, Any]]:
    """
    Poll prices for all watched events. Returns a list of alert dicts:
      [{title, name, url, current_price, previous_price, pct_change}, ...]

    Each dict maps to what bot.py's _format_price_alert() expects.
    """
    if not PRICE_MONITOR_ENABLED:
        logger.debug("price_monitor: disabled; skipping.")
        return []

    if _ta is None or not hasattr(_ta, "get_event_details"):
        logger.debug("price_monitor: ticketmaster_agent unavailable; skipping.")
        return []

    watched = _get_watched_events()
    if not watched:
        logger.debug("price_monitor: no watched events; skipping.")
        return []

    state = _load_price_state()
    alerts: List[Dict[str, Any]] = []
    updated_state = dict(state)

    for row in watched:
        event_id = row.get("event_id") or ""
        if not event_id:
            continue

        try:
            event = _ta.get_event_details(event_id)
        except Exception as e:
            logger.warning("price_monitor: TM fetch failed for %s: %s", event_id, e)
            continue

        if not isinstance(event, dict) or not event:
            continue

        prices = _extract_prices(event)
        if not prices:
            continue

        new_min = prices["min"]
        new_max = prices["max"]

        name = event.get("name") or row.get("name") or f"Event {event_id}"
        url = event.get("url") or row.get("url") or ""
        artist = row.get("artist") or ""

        prev = state.get(event_id) or {}
        prev_min = prev.get("price_min")

        updated_state[event_id] = {
            "price_min": new_min,
            "price_max": new_max,
            "last_checked": time.time(),
        }

        if prev_min is None:
            # First time seeing this event — store but don't alert
            logger.debug("price_monitor: first price snapshot for %s: $%.0f", event_id, new_min)
            continue

        pct = _pct_change(prev_min, new_min)

        if pct <= -PRICE_DROP_THRESHOLD_PCT:
            title_parts = [f"{PRICE_PREFIX} Price drop"]
            if artist:
                title_parts.append(artist)
            title_parts.append(name)

            alerts.append({
                "title": " — ".join(title_parts),
                "name": name,
                "url": url,
                "current_price": f"${new_min:.0f}–${new_max:.0f}",
                "previous_price": f"${prev_min:.0f}",
                "pct_change": round(pct, 1),
                "event_id": event_id,
            })
            logger.info(
                "price_monitor: price drop for %s: $%.0f → $%.0f (%.1f%%)",
                event_id, prev_min, new_min, pct,
            )

        # Small sleep between TM requests (called synchronously in thread)
        try:
            import time as _t
            _t.sleep(0.5)
        except Exception:
            pass

    _save_price_state(updated_state)
    return alerts
