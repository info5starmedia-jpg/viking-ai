"""agents/demand_heatmap.py

Viking AI — Demand Heatmap
-------------------------
Produces a ranked list of cities/metros where an artist is likely to have the
highest demand.

We do *not* have Spotify monthly listeners/top cities from the Spotify Web API
client-credentials flow, so we model demand using:
  • Ticketmaster upcoming event density by city (signal of touring footprint)
  • A fallback "major market" prior when no TM data is available

The result is useful for:
  - /intel "Best Cities" section
  - Event sellout scoring (market_heat per city)
"""

from __future__ import annotations

import os
import time
import logging
from collections import Counter
from typing import Any, Dict, List, Tuple, Optional

import requests

logger = logging.getLogger("demand_heatmap")

_CACHE: Dict[str, Dict[str, Any]] = {}
_CACHE_TTL_SEC = 60 * 30  # 30 min


MAJOR_MARKETS_US = [
    "New York", "Los Angeles", "Chicago", "Dallas", "Houston", "Atlanta",
    "Washington", "Philadelphia", "Boston", "San Francisco", "Seattle",
    "Denver", "Phoenix", "Detroit", "Minneapolis", "Tampa", "Miami",
    "San Diego", "Portland", "Austin",
]


def _cache_get(key: str) -> Optional[Dict[str, Any]]:
    obj = _CACHE.get(key)
    if not obj:
        return None
    if time.time() - obj["ts"] > _CACHE_TTL_SEC:
        return None
    return obj["value"]


def _cache_set(key: str, value: Dict[str, Any]) -> None:
    _CACHE[key] = {"ts": time.time(), "value": value}


def _tm_search_events(artist: str, size: int = 200) -> List[Dict[str, Any]]:
    key = (os.getenv("TICKETMASTER_API_KEY") or "").strip()
    if not key:
        return []
    try:
        r = requests.get(
            "https://app.ticketmaster.com/discovery/v2/events.json",
            params={
                "apikey": key,
                "keyword": artist,
                "segmentName": "Music",
                "size": min(200, max(10, size)),
                "sort": "date,asc",
            },
            timeout=18,
        )
        if r.status_code != 200:
            return []
        data = r.json() or {}
        return (data.get("_embedded") or {}).get("events") or []
    except Exception as e:
        logger.warning("TM search error: %s", e)
        return []


def top_cities_for_artist(artist: str, top_n: int = 10) -> List[Tuple[str, int]]:
    """Return [(city, weight)] ranked by demand signal."""
    artist = (artist or "").strip()
    if not artist:
        return []

    ck = f"heatmap:{artist.lower()}:{top_n}"
    cached = _cache_get(ck)
    if cached:
        return cached["cities"]

    events = _tm_search_events(artist)
    cities: List[str] = []
    for e in events:
        venues = ((e.get("_embedded") or {}).get("venues") or [])
        if not venues:
            continue
        v0 = venues[0] or {}
        city = (v0.get("city") or {}).get("name")
        if city:
            cities.append(city)

    if not cities:
        # fallback prior: major markets
        ranked = [(c, max(1, (top_n * 2) - i)) for i, c in enumerate(MAJOR_MARKETS_US[:top_n])]
        _cache_set(ck, {"cities": ranked})
        return ranked

    counts = Counter(cities)

    # Weight: upcoming shows in that city; clamp so one residency doesn't dominate.
    ranked = [(city, min(10, cnt) * 10) for city, cnt in counts.most_common(top_n)]
    _cache_set(ck, {"cities": ranked})
    return ranked

# --- Back-compat export ---
def compute_best_cities(*args, **kwargs):
    """
    Compatibility wrapper for older imports.
    Tries to call the primary city-ranking function in this module.
    """
    for fn_name in ("best_cities", "get_best_cities", "rank_best_cities", "compute_city_rankings"):
        fn = globals().get(fn_name)
        if callable(fn):
            return fn(*args, **kwargs)
    raise ImportError("No underlying city ranking function found to back compute_best_cities()")

# --- Back-compat export (final) ---
def compute_best_cities(artist: str, top_n: int = 10, *args, **kwargs):
    """
    Backwards-compatible name used by intel command.
    Returns a list[(city, count)] from Ticketmaster city density.
    """
    return top_cities_for_artist(artist, top_n=top_n)
