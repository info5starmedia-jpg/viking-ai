"""
sellout_estimator.py

Heuristic “sell-out probability” scorer for Viking AI.

Takes in rough streaming + event context and outputs:
- probability 0–100 (int)
- label like "Ultra-hot", "Strong", "Moderate", etc.
- short human explanation.

Designed to be *best-effort* and safe even with partial data.
"""

from __future__ import annotations
from typing import Dict, Any, Tuple, Optional
import math


def _safe_float(value: Any) -> float:
    try:
        if value is None:
            return 0.0
        if isinstance(value, (int, float)):
            return float(value)
        # Strings like "12,345,678"
        s = str(value).replace(",", "").strip()
        return float(s) if s else 0.0
    except Exception:
        return 0.0


def _logish(x: float, base: float = 10.0) -> float:
    if x <= 0:
        return 0.0
    return math.log(x, base)


def estimate_sellout_probability(
    streams: Optional[Dict[str, Any]] = None,
    *,
    tm_events_count: int = 0,
    region: str = "NA",
) -> Tuple[int, str, str]:
    """
    Main entrypoint.

    Parameters
    ----------
    streams : dict or None
        Whatever your streaming_metrics helper returns. This function is
        defensive and only looks for a few common keys.
        Expected keys (best-effort):
          - "spotify_monthly"
          - "spotify_followers"
          - "youtube_monthly"
          - "youtube_28d_views" or "youtube_views_30d"
    tm_events_count : int
        How many *upcoming* Ticketmaster events we see in this region.
    region : str
        Short region label like "NA", "EU", "UK", "Global", etc.

    Returns
    -------
    (probability, label, reason)
      probability : int 0–100
      label       : str (e.g. "Ultra-hot", "Strong", "Moderate")
      reason      : short explanation string
    """
    streams = streams or {}

    # Pull numbers out in a very forgiving way
    spotify_monthly = _safe_float(
        streams.get("spotify_monthly")
        or streams.get("spotify_listeners")
        or streams.get("spotify_monthly_listeners")
    )
    spotify_followers = _safe_float(
        streams.get("spotify_followers")
        or streams.get("spotify_followers_count")
    )
    yt_monthly = _safe_float(
        streams.get("youtube_monthly")
        or streams.get("youtube_monthly_listeners")
    )
    yt_recent_views = _safe_float(
        streams.get("youtube_28d_views")
        or streams.get("youtube_views_30d")
        or streams.get("youtube_recent_views")
    )

    # 1) Stream-based score (0–100)
    # Rough tiers so it doesn't swing wildly:
    # - 1M Spotify monthly is "solid touring"
    # - 10M+ is "arena" level
    # - YouTube views used as confirmation
    spotify_component = min(_logish(spotify_monthly, base=10.0) * 15.0, 60.0)
    followers_component = min(_logish(spotify_followers, base=10.0) * 7.0, 20.0)
    yt_component = min(_logish(yt_recent_views + yt_monthly, base=10.0) * 8.0, 25.0)

    raw_stream_score = spotify_component + followers_component + yt_component
    # Cap raw score to something sane
    raw_stream_score = max(0.0, min(raw_stream_score, 95.0))

    # 2) Event pressure bonus: more upcoming dates → more opportunities to sell,
    #   but also more supply. Give a *small* positive nudge only.
    if tm_events_count <= 0:
        events_bonus = -5.0  # no dates yet → lower near-term sell-out odds
    elif tm_events_count < 5:
        events_bonus = 5.0
    elif tm_events_count < 15:
        events_bonus = 8.0
    else:
        events_bonus = 10.0

    base_prob = raw_stream_score + events_bonus

    # 3) Region tweak (very light, just flavour)
    region = (region or "").upper()
    if region in ("NA", "US", "CA", "NORTH AMERICA"):
        base_prob += 2.0
    elif region in ("UK", "EU", "IRELAND", "EUROPE"):
        base_prob += 1.0

    prob = int(round(max(0.0, min(base_prob, 99.0))))

    # Label & reason
    if prob >= 85:
        label = "Ultra-hot"
        reason = "Likely to sell out quickly in major markets."
    elif prob >= 70:
        label = "Strong"
        reason = "High demand with good odds of sell-outs in key cities."
    elif prob >= 55:
        label = "Moderate"
        reason = "Decent demand; some markets may sell out, others stay open."
    elif prob >= 35:
        label = "Niche / mixed"
        reason = "Demand appears localized or niche; only some dates likely to sell out."
    else:
        label = "Low"
        reason = "Limited demand signals; sell-outs are unlikely except in special cases."

    # If we really had *no* streaming data, force a softer message
    if spotify_monthly == 0 and yt_monthly == 0 and yt_recent_views == 0:
        prob = max(prob, 10)  # don't go to 0 just because of data gaps
        label = "Unknown / data-light"
        reason = "Very limited streaming data; treat this probability as highly uncertain."

    return prob, label, reason
