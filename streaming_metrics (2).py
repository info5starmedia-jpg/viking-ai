import os
import math
import logging
from typing import Dict, Any, Optional

import requests

logger = logging.getLogger("streaming_metrics")

SPOTIFY_CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID")
SPOTIFY_CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET")
YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY")


# ---------------------------------------------------------------------------
# Spotify helpers
# ---------------------------------------------------------------------------

def _get_spotify_token() -> Optional[str]:
    if not SPOTIFY_CLIENT_ID or not SPOTIFY_CLIENT_SECRET:
        return None
    try:
        r = requests.post(
            "https://accounts.spotify.com/api/token",
            data={"grant_type": "client_credentials"},
            auth=(SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET),
            timeout=10,
        )
        r.raise_for_status()
        return r.json().get("access_token")
    except Exception as e:
        logger.warning("Spotify token fetch failed: %s", e)
        return None


def _spotify_search_artist(artist: str, token: str) -> Dict[str, Any]:
    try:
        r = requests.get(
            "https://api.spotify.com/v1/search",
            headers={"Authorization": f"Bearer {token}"},
            params={"q": artist, "type": "artist", "limit": 1},
            timeout=10,
        )
        r.raise_for_status()
        data = r.json()
        items = data.get("artists", {}).get("items", [])
        if not items:
            return {}
        return items[0]
    except Exception as e:
        logger.warning("Spotify artist search failed: %s", e)
        return {}


def fetch_spotify_metrics(artist: str) -> Dict[str, Any]:
    """
    Return Spotify metrics for an artist.

    NOTE: Official Spotify API does NOT expose monthly listeners.
    We use:
      - followers.total
      - popularity (0-100)
    """
    token = _get_spotify_token()
    if not token:
        return {"available": False}

    info = _spotify_search_artist(artist, token)
    if not info:
        return {"available": False}

    followers = info.get("followers", {}).get("total")
    popularity = info.get("popularity")
    name = info.get("name") or artist

    return {
        "available": True,
        "artist_name": name,
        "followers": followers,
        "popularity": popularity,
        "raw": info,
    }


# ---------------------------------------------------------------------------
# YouTube helpers
# ---------------------------------------------------------------------------

def _youtube_search_channel(artist: str) -> Optional[str]:
    if not YOUTUBE_API_KEY:
        return None
    try:
        r = requests.get(
            "https://youtube.googleapis.com/youtube/v3/search",
            params={
                "part": "snippet",
                "q": artist,
                "type": "channel",
                "maxResults": 1,
                "key": YOUTUBE_API_KEY,
            },
            timeout=10,
        )
        r.raise_for_status()
        data = r.json()
        items = data.get("items", [])
        if not items:
            return None
        return items[0]["id"]["channelId"]
    except Exception as e:
        logger.warning("YouTube search failed: %s", e)
        return None


def _youtube_channel_stats(channel_id: str) -> Dict[str, Any]:
    if not YOUTUBE_API_KEY:
        return {}
    try:
        r = requests.get(
            "https://youtube.googleapis.com/youtube/v3/channels",
            params={
                "part": "statistics",
                "id": channel_id,
                "key": YOUTUBE_API_KEY,
            },
            timeout=10,
        )
        r.raise_for_status()
        data = r.json()
        items = data.get("items", [])
        if not items:
            return {}
        stats = items[0].get("statistics", {})
        return stats
    except Exception as e:
        logger.warning("YouTube channel stats failed: %s", e)
        return {}


def fetch_youtube_metrics(artist: str) -> Dict[str, Any]:
    """
    Return YouTube metrics for an artist's main channel:
      - subscriberCount
      - viewCount
    """
    if not YOUTUBE_API_KEY:
        return {"available": False}

    channel_id = _youtube_search_channel(artist)
    if not channel_id:
        return {"available": False}

    stats = _youtube_channel_stats(channel_id)
    if not stats:
        return {"available": False}

    def _to_int(x):
        try:
            return int(x)
        except Exception:
            return None

    subs = _to_int(stats.get("subscriberCount"))
    views = _to_int(stats.get("viewCount"))

    return {
        "available": True,
        "channel_id": channel_id,
        "subscribers": subs,
        "views": views,
        "raw": stats,
    }


# ---------------------------------------------------------------------------
# Demand scoring & formatting
# ---------------------------------------------------------------------------

def _normalize_log(value: Optional[int], high: float) -> float:
    """
    Normalize log10(value) against a 'high' like 7 (10M), 8 (100M), etc.
    Returns 0.0–1.0
    """
    if value is None or value <= 0:
        return 0.0
    v = math.log10(float(value))
    return max(0.0, min(1.0, v / high))


def compute_demand_score(
    spotify_popularity: Optional[int],
    spotify_followers: Optional[int],
    yt_subs: Optional[int],
    yt_views: Optional[int],
) -> Dict[str, Any]:
    """
    Combine Spotify + YouTube into a 0–100 score + 1–5 stars + label.
    """

    pop_score = 0.0
    if spotify_popularity is not None:
        pop_score = max(0.0, min(1.0, spotify_popularity / 100.0))

    followers_score = _normalize_log(spotify_followers, high=7.0)  # 10^7 ~ 10M
    subs_score = _normalize_log(yt_subs, high=7.0)                 # 10^7 ~ 10M
    views_score = _normalize_log(yt_views, high=9.0)               # 10^9 ~ 1B

    # Weighted blend
    # Popularity: 40%, Followers: 30%, Subs: 15%, Views: 15%
    blended = (
        0.40 * pop_score +
        0.30 * followers_score +
        0.15 * subs_score +
        0.15 * views_score
    )

    score = int(round(blended * 100))

    # Stars
    if score >= 85:
        stars = 5
        label = "S-tier touring cycle 🔥"
    elif score >= 70:
        stars = 4
        label = "A-tier strong cycle"
    elif score >= 55:
        stars = 3
        label = "B-tier solid demand"
    elif score >= 40:
        stars = 2
        label = "Emerging / regional"
    else:
        stars = 1
        label = "Early / niche signal"

    return {
        "score": score,
        "stars": stars,
        "label": label,
        "components": {
            "pop_score": pop_score,
            "followers_score": followers_score,
            "subs_score": subs_score,
            "views_score": views_score,
        },
    }


def get_streaming_snapshot(artist: str) -> Dict[str, Any]:
    """
    High-level convenience:
      - Try Spotify + YouTube
      - Compute demand score
    """
    spotify = fetch_spotify_metrics(artist)
    youtube = fetch_youtube_metrics(artist)

    sp_followers = spotify.get("followers") if spotify.get("available") else None
    sp_pop = spotify.get("popularity") if spotify.get("available") else None
    yt_subs = youtube.get("subscribers") if youtube.get("available") else None
    yt_views = youtube.get("views") if youtube.get("available") else None

    if any(x is not None for x in [sp_followers, sp_pop, yt_subs, yt_views]):
        demand = compute_demand_score(sp_pop, sp_followers, yt_subs, yt_views)
    else:
        demand = {
            "score": None,
            "stars": None,
            "label": "Not enough streaming data.",
            "components": {},
        }

    return {
        "artist": artist,
        "spotify": spotify,
        "youtube": youtube,
        "demand": demand,
    }


def _starbar(stars: Optional[int]) -> str:
    if stars is None:
        return "N/A"
    s = max(0, min(5, int(stars)))
    full = "⭐" * s
    empty = "☆" * (5 - s)
    return full + empty


def _fmt_int(x: Optional[int]) -> str:
    if x is None:
        return "N/A"
    if x >= 1_000_000_000:
        return f"{x/1_000_000_000:.1f}B"
    if x >= 1_000_000:
        return f"{x/1_000_000:.1f}M"
    if x >= 1_000:
        return f"{x/1_000:.1f}K"
    return str(x)


def format_streaming_block(snapshot: Dict[str, Any]) -> str:
    artist = snapshot.get("artist") or "Unknown artist"
    spotify = snapshot.get("spotify", {})
    youtube = snapshot.get("youtube", {})
    demand = snapshot.get("demand", {})

    score = demand.get("score")
    stars = demand.get("stars")
    label = demand.get("label") or "Streaming demand not available."

    lines = []
    lines.append(f"### Streaming demand snapshot – {artist}")
    if score is not None and stars is not None:
        lines.append(f"Overall demand: {_starbar(stars)}  (Score: {score}/100 – {label})")
    else:
        lines.append(f"Overall demand: {label}")
    lines.append("")

    # Spotify
    if spotify.get("available"):
        sp_name = spotify.get("artist_name") or artist
        sp_followers = spotify.get("followers")
        sp_pop = spotify.get("popularity")
        lines.append("**Spotify**")
        lines.append(f"• Artist: {sp_name}")
        lines.append(f"• Popularity: {sp_pop if sp_pop is not None else 'N/A'} / 100")
        lines.append(f"• Followers: {_fmt_int(sp_followers)}")
        lines.append("")
    else:
        lines.append("**Spotify**")
        lines.append("• Not available (check SPOTIFY_CLIENT_ID / SPOTIFY_CLIENT_SECRET).")
        lines.append("")

    # YouTube
    if youtube.get("available"):
        yt_subs = youtube.get("subscribers")
        yt_views = youtube.get("views")
        lines.append("**YouTube**")
        lines.append(f"• Subscribers: {_fmt_int(yt_subs)}")
        lines.append(f"• Total views: {_fmt_int(yt_views)}")
        lines.append("")
    else:
        lines.append("**YouTube**")
        lines.append("• Not available (check YOUTUBE_API_KEY).")
        lines.append("")

    return "\n".join(lines)
