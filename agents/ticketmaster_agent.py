"""
Real Ticketmaster implementation (canonical).

Root `ticketmaster_agent.py` imports from here.
Keep these function names stable:
  - search_events_for_artist
  - get_event_details
  - fetch_verified_fan_programs
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple
import os
import requests

TM_API_KEY = os.getenv("TICKETMASTER_API_KEY") or os.getenv("TM_API_KEY")
TM_DISCOVERY_BASE = os.getenv("TM_DISCOVERY_BASE", "https://app.ticketmaster.com/discovery/v2").rstrip("/")


def _require_key() -> str:
    if not TM_API_KEY:
        raise RuntimeError("Missing Ticketmaster API key. Set TICKETMASTER_API_KEY (or TM_API_KEY).")
    return TM_API_KEY


def _normalize_search_args(
    args: Tuple[Any, ...],
    country_code: str,
    size: int,
    kwargs: Dict[str, Any],
) -> Tuple[str, int, Dict[str, Any]]:
    """
    Backwards-compatible normalization.

    Supports legacy calls:
      - search_events_for_artist("bts", 10)        # size
      - search_events_for_artist("bts", "US")      # country
      - search_events_for_artist("bts", "US", 25)  # country + size
    """
    cc = country_code
    sz = size

    if len(args) >= 1:
        a0 = args[0]
        if isinstance(a0, int):
            sz = a0
        elif isinstance(a0, str):
            cc = a0

    if len(args) >= 2:
        a1 = args[1]
        if isinstance(a1, int):
            sz = a1
        elif isinstance(a1, str):
            cc = a1

    if "country_code" in kwargs and isinstance(kwargs["country_code"], str):
        cc = kwargs.pop("country_code")
    if "size" in kwargs and isinstance(kwargs["size"], int):
        sz = kwargs.pop("size")

    cc = (cc or "US")
    if not isinstance(cc, str):
        cc = "US"
    cc = cc.upper().strip()
    if len(cc) != 2:
        cc = "US"

    try:
        sz = int(sz)
    except Exception:
        sz = 10
    if sz < 1:
        sz = 1
    if sz > 200:
        sz = 200

    return cc, sz, kwargs


def search_events_for_artist(
    artist: str,
    *args: Any,
    country_code: str = "US",
    size: int = 10,
    **kwargs: Any,
) -> List[Dict[str, Any]]:
    """
    Search Ticketmaster Discovery API for events for an artist keyword.
    Returns a list of TM event objects (dicts).
    """
    cc, sz, kwargs = _normalize_search_args(args, country_code, size, dict(kwargs))

    key = _require_key()
    url = f"{TM_DISCOVERY_BASE}/events.json"
    params: Dict[str, Any] = {
        "apikey": key,
        "keyword": artist,
        "countryCode": cc,
        "size": sz,
    }

    for k in (
        "classificationName",
        "segmentId",
        "genreId",
        "startDateTime",
        "endDateTime",
        "page",
        "sort",
        "radius",
        "unit",
        "locale",
    ):
        if k in kwargs:
            params[k] = kwargs[k]

    r = requests.get(url, params=params, timeout=15)
    r.raise_for_status()
    data = r.json() or {}

    embedded = data.get("_embedded") or {}
    events = embedded.get("events") or []
    if not isinstance(events, list):
        return []
    return events


def get_event_details(event_id: str, **kwargs: Any) -> Dict[str, Any]:
    """Fetch details for a single event by ID."""
    key = _require_key()
    url = f"{TM_DISCOVERY_BASE}/events/{event_id}.json"
    params: Dict[str, Any] = {"apikey": key}
    if "locale" in kwargs:
        params["locale"] = kwargs["locale"]

    r = requests.get(url, params=params, timeout=15)
    r.raise_for_status()
    data = r.json() or {}
    if not isinstance(data, dict):
        return {}
    return data


def fetch_verified_fan_programs(*args: Any, **kwargs: Any) -> List[Dict[str, Any]]:
    """
    Placeholder: keep Viking AI stable by returning [] for now.
    Later implement curated Verified Fan URLs + polling.
    """
    return []
