# agents/youtube_agent.py — stable + cached + light mode (NO heavy comment scraping by default)
from __future__ import annotations

import os
import time
import logging
from typing import Dict, Any, Optional

import requests

logger = logging.getLogger("youtube_agent")

_CACHE: Dict[str, Any] = {}
_CACHE_TTL_SEC = 60 * 60  # 1 hour


def _cache_get(key: str) -> Optional[Dict[str, Any]]:
    obj = _CACHE.get(key)
    if not obj:
        return None
    if time.time() - obj["ts"] > _CACHE_TTL_SEC:
        return None
    return obj["value"]


def _cache_set(key: str, value: Dict[str, Any]) -> None:
    _CACHE[key] = {"ts": time.time(), "value": value}


def get_youtube_profile(artist: str, light_mode: bool = True) -> Dict[str, Any]:
    """
    Returns dict:
      { channel_title, subs_estimate, momentum }
    - light_mode=True means: do NOT fetch comments (avoids long blocking flows)
    """
    artist = (artist or "").strip()
    if not artist:
        return {}

    ck = f"yt:{artist.lower()}:{'light' if light_mode else 'heavy'}"
    cached = _cache_get(ck)
    if cached:
        return cached

    api_key = os.getenv("YOUTUBE_API_KEY", "").strip()
    if not api_key:
        logger.warning("YOUTUBE_API_KEY missing; YouTube features limited.")
        out = {"channel_title": artist, "subs_estimate": 0, "momentum": 50}
        _cache_set(ck, out)
        return out

    try:
        # Search for channel
        q = requests.utils.quote(artist)
        search_url = "https://www.googleapis.com/youtube/v3/search"
        resp = requests.get(
            search_url,
            params={
                "part": "snippet",
                "q": artist,
                "type": "channel",
                "maxResults": 1,
                "key": api_key,
            },
            timeout=12,
        )
        if resp.status_code != 200:
            logger.warning("YouTube search error %s: %s", resp.status_code, resp.text[:200])
            out = {"channel_title": artist, "subs_estimate": 0, "momentum": 50}
            _cache_set(ck, out)
            return out

        items = resp.json().get("items", []) or []
        if not items:
            out = {"channel_title": artist, "subs_estimate": 0, "momentum": 50}
            _cache_set(ck, out)
            return out

        channel_id = items[0]["snippet"].get("channelId")
        title = items[0]["snippet"].get("channelTitle", artist)

        # Fetch channel stats
        chan_url = "https://www.googleapis.com/youtube/v3/channels"
        resp2 = requests.get(
            chan_url,
            params={"part": "statistics,snippet", "id": channel_id, "key": api_key},
            timeout=12,
        )
        subs = 0
        if resp2.status_code == 200:
            citems = resp2.json().get("items", []) or []
            if citems:
                stats = citems[0].get("statistics", {}) or {}
                subs = int(stats.get("subscriberCount", 0) or 0)

        # Momentum heuristic (safe + fast)
        momentum = 50
        if subs >= 10_000_000:
            momentum = 70
        elif subs >= 3_000_000:
            momentum = 62
        elif subs >= 1_000_000:
            momentum = 56

        out = {"channel_title": title, "subs_estimate": subs, "momentum": momentum}

        # heavy mode placeholder (future): comments/video scans
        # keep it off for stability unless you explicitly turn it on
        _cache_set(ck, out)
        return out

    except Exception as e:
        logger.warning("YouTube error: %s", e)
        out = {"channel_title": artist, "subs_estimate": 0, "momentum": 50}
        _cache_set(ck, out)
        return out
