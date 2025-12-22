"""agents/artist_resolver.py

Viking AI — Artist Resolver
--------------------------
Turns a user-provided artist string into a canonical identity payload.

Design goals:
  • Stable: never raises; always returns a dict.
  • Fast: lightweight requests; cached where possible.
  • Practical: enough IDs/links to power rating + demand + event intel.

Outputs (best-effort):
  {
    "query": "...",
    "name": "Canonical Artist Name",
    "spotify": { ... },
    "youtube": { ... },
    "tiktok": { ... },
    "ticketmaster": {
        "attraction_id": "...",
        "attraction_url": "...",
        "official_site": "..."   # from Ticketmaster externalLinks.homepage if available
    }
  }
"""

from __future__ import annotations

import os
import time
import logging
from typing import Any, Dict, Optional

import requests

from agents.spotify_agent import get_spotify_profile
from agents.youtube_agent import get_youtube_profile

logger = logging.getLogger("artist_resolver")


# Simple in-memory cache (1 hour)
_CACHE: Dict[str, Dict[str, Any]] = {}
_CACHE_TTL_SEC = 60 * 60


def _cache_get(key: str) -> Optional[Dict[str, Any]]:
    obj = _CACHE.get(key)
    if not obj:
        return None
    if time.time() - obj["ts"] > _CACHE_TTL_SEC:
        return None
    return obj["value"]


def _cache_set(key: str, value: Dict[str, Any]) -> None:
    _CACHE[key] = {"ts": time.time(), "value": value}


def _tm_get_json(path: str, params: Dict[str, Any]) -> Dict[str, Any]:
    """Ticketmaster Discovery API helper (best-effort)."""
    key = (os.getenv("TICKETMASTER_API_KEY") or "").strip()
    if not key:
        return {}
    base = "https://app.ticketmaster.com/discovery/v2"
    q = dict(params)
    q["apikey"] = key
    try:
        r = requests.get(f"{base}/{path}", params=q, timeout=15)
        if r.status_code != 200:
            return {}
        return r.json() or {}
    except Exception:
        return {}


def _resolve_ticketmaster_attraction(artist: str) -> Dict[str, Any]:
    """Find a matching attraction and try to extract an official site."""
    data = _tm_get_json(
        "attractions.json",
        {
            "keyword": artist,
            "classificationName": "music",
            "size": 1,
            "sort": "relevance,desc",
        },
    )
    items = (data.get("_embedded") or {}).get("attractions") or []
    if not items:
        return {}
    a = items[0] or {}
    attraction_id = a.get("id")
    attraction_url = a.get("url")

    official_site = ""
    external = a.get("externalLinks") or {}
    # Ticketmaster commonly uses: externalLinks.homepage = [{"url": "..."}]
    homepage = external.get("homepage")
    if isinstance(homepage, list) and homepage:
        official_site = (homepage[0] or {}).get("url") or ""

    # Some attractions may also include "twitter", "facebook", etc; keep for future.
    return {
        "attraction_id": attraction_id,
        "attraction_url": attraction_url,
        "official_site": official_site,
    }


def resolve_artist(artist_query: str) -> Dict[str, Any]:
    """Resolve artist identity. Never raises."""
    artist_query = (artist_query or "").strip()
    if not artist_query:
        return {"query": "", "name": "", "spotify": {}, "youtube": {}, "tiktok": {}, "ticketmaster": {}}

    ck = f"resolve:{artist_query.lower()}"
    cached = _cache_get(ck)
    if cached:
        return cached

    spotify = {}
    youtube = {}
    try:
        spotify = get_spotify_profile(artist_query)
    except Exception as e:
        logger.warning("spotify resolve failed: %s", e)

    try:
        youtube = get_youtube_profile(artist_query)
    except Exception as e:
        logger.warning("youtube resolve failed: %s", e)

    # TikTok is async in your codebase; we store a placeholder here.
    tiktok: Dict[str, Any] = {"status": "available_via_async_agent"}

    tm = {}
    try:
        tm = _resolve_ticketmaster_attraction(spotify.get("name") or artist_query)
    except Exception as e:
        logger.warning("ticketmaster attraction resolve failed: %s", e)

    canonical_name = spotify.get("name") or youtube.get("channel_title") or artist_query
    out = {
        "query": artist_query,
        "name": canonical_name,
        "spotify": spotify,
        "youtube": youtube,
        "tiktok": tiktok,
        "ticketmaster": tm,
    }

    _cache_set(ck, out)
    return out
