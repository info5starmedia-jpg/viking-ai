# streaming_metrics.py

"""
Viking AI – Streaming & social metrics helper.

Provides:
  - get_spotify_metrics(artist_name: str) -> dict
  - get_youtube_metrics(artist_name: str) -> dict

Both return a dict with:
  {
    "ok": bool,
    "error": Optional[str],
    ...provider-specific fields...
  }

Environment variables (in .env):

  SPOTIFY_CLIENT_ID
  SPOTIFY_CLIENT_SECRET

  YOUTUBE_API_KEY
"""

import os
import time
from typing import Dict, Any, Optional

import requests
from dotenv import load_dotenv

load_dotenv()

SPOTIFY_CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID")
SPOTIFY_CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET")

YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY")

# In-memory cache for Spotify token
_SPOTIFY_TOKEN: Optional[str] = None
_SPOTIFY_TOKEN_EXPIRES_AT: float = 0.0


# -------------------------------------------------------------------
# Spotify helpers
# -------------------------------------------------------------------

def _get_spotify_token() -> str:
    """
    Get (and cache) a Spotify app token via Client Credentials flow.
    """
    global _SPOTIFY_TOKEN, _SPOTIFY_TOKEN_EXPIRES_AT

    now = time.time()
    if _SPOTIFY_TOKEN and now < _SPOTIFY_TOKEN_EXPIRES_AT - 60:
        return _SPOTIFY_TOKEN

    if not SPOTIFY_CLIENT_ID or not SPOTIFY_CLIENT_SECRET:
        raise RuntimeError("SPOTIFY_CLIENT_ID / SPOTIFY_CLIENT_SECRET not configured.")

    resp = requests.post(
        "https://accounts.spotify.com/api/token",
        data={"grant_type": "client_credentials"},
        auth=(SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET),
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    token = data.get("access_token")
    expires_in = data.get("expires_in", 3600)

    if not token:
        raise RuntimeError("No access_token in Spotify auth response.")

    _SPOTIFY_TOKEN = token
    _SPOTIFY_TOKEN_EXPIRES_AT = now + float(expires_in)
    return token


def get_spotify_metrics(artist_name: str) -> Dict[str, Any]:
    """
    Look up an artist on Spotify and return basic metrics.

    Returns on success:
      {
        "ok": True,
        "id": str,
        "name": str,
        "followers": int,
        "popularity": int,
        "url": str
      }

    On failure:
      {
        "ok": False,
        "error": "..."
      }
    """
    artist_name = (artist_name or "").strip()
    if not artist_name:
        return {"ok": False, "error": "Empty artist name."}

    try:
        token = _get_spotify_token()
    except Exception as e:
        return {"ok": False, "error": f"Spotify auth failed: {e}"}

    headers = {"Authorization": f"Bearer {token}"}
    params = {
        "q": artist_name,
        "type": "artist",
        "limit": 1,
    }

    try:
        resp = requests.get(
            "https://api.spotify.com/v1/search",
            headers=headers,
            params=params,
            timeout=20,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        return {"ok": False, "error": f"Spotify search failed: {e}"}

    artists = (data.get("artists") or {}).get("items") or []
    if not artists:
        return {"ok": False, "error": "No matching Spotify artist found."}

    a = artists[0]
    followers = (a.get("followers") or {}).get("total", 0)
    popularity = a.get("popularity", 0)
    sp_id = a.get("id")
    name = a.get("name") or artist_name
    url = (a.get("external_urls") or {}).get("spotify")

    return {
        "ok": True,
        "id": sp_id,
        "name": name,
        "followers": int(followers),
        "popularity": int(popularity),
        "url": url,
    }


# -------------------------------------------------------------------
# YouTube helpers
# -------------------------------------------------------------------

def get_youtube_metrics(artist_name: str) -> Dict[str, Any]:
    """
    Use YouTube Data API to approximate subscribers for the artist.

    Strategy:
      1. Search for channels matching the artist name
      2. Take the top result and fetch statistics (subscriberCount)

    Returns on success:
      {
        "ok": True,
        "channel_id": str,
        "title": str,
        "subscribers": int,
        "url": str
      }

    On failure:
      {
        "ok": False,
        "error": "..."
      }
    """
    artist_name = (artist_name or "").strip()
    if not artist_name:
        return {"ok": False, "error": "Empty artist name."}

    if not YOUTUBE_API_KEY:
        return {"ok": False, "error": "YOUTUBE_API_KEY not configured."}

    # Step 1: search for channel
    search_params = {
        "part": "snippet",
        "q": artist_name,
        "type": "channel",
        "maxResults": 1,
        "key": YOUTUBE_API_KEY,
    }

    try:
        s_resp = requests.get(
            "https://www.googleapis.com/youtube/v3/search",
            params=search_params,
            timeout=20,
        )
        s_resp.raise_for_status()
        s_data = s_resp.json()
    except Exception as e:
        return {"ok": False, "error": f"YouTube search failed: {e}"}

    items = s_data.get("items") or []
    if not items:
        return {"ok": False, "error": "No matching YouTube channel found."}

    ch = items[0]
    channel_id = (ch.get("id") or {}).get("channelId")
    if not channel_id:
        return {"ok": False, "error": "No channelId in YouTube search result."}

    # Step 2: channel statistics
    stats_params = {
        "part": "statistics,snippet",
        "id": channel_id,
        "key": YOUTUBE_API_KEY,
    }

    try:
        c_resp = requests.get(
            "https://www.googleapis.com/youtube/v3/channels",
            params=stats_params,
            timeout=20,
        )
        c_resp.raise_for_status()
        c_data = c_resp.json()
    except Exception as e:
        return {"ok": False, "error": f"YouTube channel stats failed: {e}"}

    c_items = c_data.get("items") or []
    if not c_items:
        return {"ok": False, "error": "No channel stats returned."}

    info = c_items[0]
    stats = info.get("statistics") or {}
    snippet = info.get("snippet") or {}

    subs_str = stats.get("subscriberCount")
    try:
        subscribers = int(subs_str) if subs_str is not None else 0
    except ValueError:
        subscribers = 0

    title = snippet.get("title") or artist_name
    url = f"https://www.youtube.com/channel/{channel_id}"

    return {
        "ok": True,
        "channel_id": channel_id,
        "title": title,
        "subscribers": subscribers,
        "url": url,
    }


if __name__ == "__main__":
    # Simple manual test helpers
    test_artist = "Taylor Swift"
    print("Testing Spotify metrics...")
    print(get_spotify_metrics(test_artist))

    print("Testing YouTube metrics...")
    print(get_youtube_metrics(test_artist))
