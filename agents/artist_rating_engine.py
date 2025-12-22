"""agents/artist_rating_engine.py

Viking AI — Artist Rating Engine (1–5 stars)
------------------------------------------------
Computes a simple, explainable artist rating from signals we already collect.

Inputs are *best-effort* dicts (spotify/youtube/tiktok). Missing values are OK.

Output:
  {
    "stars": 1..5,
    "score": 0..100,
    "label": "Emerging"|"Growing"|"Hot"|"Headliner"|"Rockstar",
    "reasons": ["..."]
  }
"""

from __future__ import annotations

from typing import Any, Dict, List


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def _safe_int(v: Any, default: int = 0) -> int:
    try:
        return int(v or 0)
    except Exception:
        return default


def _norm_log(value: int, max_value: int) -> float:
    """0..1-ish with diminishing returns. Avoids over-weighting huge channels."""
    value = max(0, value)
    max_value = max(1, max_value)
    # piecewise log-ish without importing math (keeps it simple)
    # Scale: value/max_value then sqrt twice
    r = value / max_value
    r = _clamp(r, 0.0, 1.0)
    return (r ** 0.25)  # strong diminishing returns


def rate_artist(
    spotify: Dict[str, Any] | None = None,
    youtube: Dict[str, Any] | None = None,
    tiktok: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    spotify = spotify or {}
    youtube = youtube or {}
    tiktok = tiktok or {}

    reasons: List[str] = []

    # --- Spotify (50%) ---
    sp_pop = _safe_int(spotify.get("popularity"), 50)  # 0..100
    sp_followers = _safe_int(spotify.get("followers"), 0)
    sp_followers_norm = _norm_log(sp_followers, 10_000_000) * 100

    spotify_score = 0.70 * sp_pop + 0.30 * sp_followers_norm
    reasons.append(f"Spotify popularity {sp_pop}/100")
    if sp_followers:
        reasons.append(f"Spotify followers {sp_followers:,}")

    # --- YouTube (25%) ---
    yt_subs = _safe_int(youtube.get("subs_estimate"), 0)
    yt_momentum = _safe_int(youtube.get("momentum"), 50)
    yt_subs_norm = _norm_log(yt_subs, 10_000_000) * 100
    youtube_score = 0.65 * yt_subs_norm + 0.35 * yt_momentum
    if yt_subs:
        reasons.append(f"YouTube subs {yt_subs:,}")
    reasons.append(f"YouTube momentum {yt_momentum}/100")

    # --- TikTok (25%) ---
    # Your current TikTok agent returns hashtag views + weekly_growth.
    tt_views = _safe_int(tiktok.get("views"), 0)
    tt_growth = _safe_int(tiktok.get("weekly_growth"), 0)
    tt_views_norm = _norm_log(tt_views, 5_000_000_000) * 100
    tt_growth_norm = _clamp(tt_growth / 100.0, 0.0, 1.0) * 100
    tiktok_score = 0.75 * tt_views_norm + 0.25 * tt_growth_norm
    if tt_views:
        reasons.append(f"TikTok hashtag views {tt_views:,}")
    if tt_growth:
        reasons.append(f"TikTok weekly growth {tt_growth}")

    # --- Weighted final ---
    total = (
        0.50 * spotify_score
        + 0.25 * youtube_score
        + 0.25 * tiktok_score
    )
    total = _clamp(total, 0.0, 100.0)

    # Map to stars
    if total < 25:
        stars, label = 1, "Emerging"
    elif total < 45:
        stars, label = 2, "Growing"
    elif total < 65:
        stars, label = 3, "Hot"
    elif total < 85:
        stars, label = 4, "Headliner"
    else:
        stars, label = 5, "Rockstar"

    return {
        "stars": stars,
        "score": int(round(total)),
        "label": label,
        "reasons": reasons,
    }


def stars_to_emoji(stars: int) -> str:
    stars = max(1, min(5, int(stars or 1)))
    return "⭐" * stars
