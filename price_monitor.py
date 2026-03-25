"""
price_monitor.py

Real price-drop monitor for Viking AI.

Reads watched events from drop_catcher.list_drop_watches(), polls
Ticketmaster for current price ranges, and returns alerts when prices
drop by more than PRICE_DROP_THRESHOLD_PCT (default 10%).

Price history is persisted in price_state.json so previous prices
survive restarts without touching drop_catcher's DB state.

Env vars:
  PRICE_MONITOR_ENABLED      – set to 0 to disable (default 1)
  PRICE_DROP_THRESHOLD_PCT   – minimum % drop to alert (default 10)
  PRICE_STATE_FILE           – path to price state JSON (default alongside this file)
  PRICE_PREFIX               – prefix string for alert titles (default [PRICE])
"""

from __future__ import annotations

import json
import logging
import os
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
# Optional deps
# ---------------------------------------------------------------------------

try:
    import ticketmaster_agent as _ta  # type: ignore
except Exception as _e:
    _ta = None  # type: ignore
    logger.warning("price_monitor: ticketmaster_agent not available: %s", _e)

try:
    import drop_catcher as _dc  # type: ignore
except Exception as _e:
    _dc = None  # type: ignore
    logger.warning("price_monitor: drop_catcher not available: %s", _e)


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
    try:
        with open(PRICE_STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f) or {}
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    except Exception:
        return {}


def _save_price_state(state: Dict[str, Any]) -> None:
    try:
        with open(PRICE_STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2, sort_keys=True)
    except Exception as e:
        logger.warning("price_monitor: failed saving price state: %s", e)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

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

    if _dc is None or not hasattr(_dc, "list_drop_watches"):
        logger.debug("price_monitor: drop_catcher unavailable; skipping.")
        return []

    watched = _dc.list_drop_watches()
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

        # Use drop_catcher's normalizer for consistent price extraction
        norm = _dc._normalize_event(event)
        new_min = norm.get("price_min")
        new_max = norm.get("price_max")

        if new_min is None or new_min <= 0:
            time.sleep(0.5)
            continue

        name = norm.get("name") or row.get("name") or f"Event {event_id}"
        url = norm.get("url") or row.get("url") or ""
        artist = norm.get("artist") or row.get("artist") or ""

        prev = state.get(event_id) or {}
        prev_min = prev.get("price_min")

        updated_state[event_id] = {
            "price_min": new_min,
            "price_max": new_max,
            "last_checked": time.time(),
        }

        if prev_min is None:
            logger.debug("price_monitor: first price snapshot for %s: $%.0f", event_id, new_min)
            time.sleep(0.5)
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

        time.sleep(0.5)

    _save_price_state(updated_state)
    return alerts
