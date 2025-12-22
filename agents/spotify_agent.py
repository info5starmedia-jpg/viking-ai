# agents/spotify_agent.py — stable + cached + light mode
from __future__ import annotations

import os
import time
import json
import logging
from typing import Dict, Any, Optional

import requests

logger = logging.getLogger("spotify_agent")

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


def _get_access_token() -> Optional[str]:
    cid = os.getenv("SPOTIFY_CLIENT_ID", "").strip()
    secret = os.getenv("SPOTIFY_CLIENT_SECRET", "").strip()
    if not cid or not secret:
        logger.warning("Spotify client id/secret missing; Spotify limited.")
        return None

    try:
        resp = requests.post(
            "https://accounts.spotify.com/api/token",
            data={"grant_type": "client_credentials"},
            auth=(cid, secret),
            timeout=12,
        )
        if resp.status_code != 200:
            logger.warning("Spotify token request failed %s: %s", resp.status_code, resp.text[:200])
            return None
        return resp.json().get("access_token")
    except Exception as e:
        logger.warning("Spotify token error: %s", e)
        return None


def get_spotify_profile(artist_name: str, light_mode: bool = True) -> Dict[str, Any]:
    """
    Returns dict:
      { name, followers, popularity, url }
    Never raises.
    """
    artist_name = (artist_name or "").strip()
    if not artist_name:
        return {}

    ck = f"spotify:{artist_name.lower()}"
    cached = _cache_get(ck)
    if cached:
        return cached

    token = _get_access_token()
    if not token:
        out = {"name": artist_name, "followers": 0, "popularity": 50, "url": ""}
        _cache_set(ck, out)
        return out

    try:
        q = requests.utils.quote(artist_name)
        url = f"https://api.spotify.com/v1/search?q={q}&type=artist&limit=1"
        resp = requests.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=12)
        if resp.status_code != 200:
            logger.warning("Spotify API Error %s: %s", resp.status_code, resp.text[:200])
            out = {"name": artist_name, "followers": 0, "popularity": 50, "url": ""}
            _cache_set(ck, out)
            return out

        data = resp.json()
        items = (((data.get("artists") or {}).get("items")) or [])
        if not items:
            out = {"name": artist_name, "followers": 0, "popularity": 50, "url": ""}
            _cache_set(ck, out)
            return out

        a = items[0]
        out = {
            "name": a.get("name", artist_name),
            "followers": (a.get("followers") or {}).get("total", 0) or 0,
            "popularity": a.get("popularity", 50) or 50,
            "url": (a.get("external_urls") or {}).get("spotify", ""),
        }
        _cache_set(ck, out)
        return out
    except Exception as e:
        logger.warning("Spotify error: %s", e)
        out = {"name": artist_name, "followers": 0, "popularity": 50, "url": ""}
        _cache_set(ck, out)
        return out
