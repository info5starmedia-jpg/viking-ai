import logging
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)


def _safe_num(value: Any, default: float = 0.0) -> float:
    """Convert a value to float safely."""
    try:
        return float(value or 0)
    except Exception:  # noqa: BLE001
        return default


def compute_market_heat(
    artist_name: str,
    city: Optional[str] = None,
    country: Optional[str] = None,
    venue: Optional[str] = None,
    spotify_stats: Optional[Dict[str, Any]] = None,
    youtube_profile: Optional[Dict[str, Any]] = None,
    **kwargs: Any,
) -> Tuple[int, str]:
    """
    Return a simple 0–100 'market heat' score + human-readable reason.

    Extra kwargs are accepted and ignored so callers can safely pass more
    context (region, market name, etc.) without breaking this function.
    """
    reasons = []

    # 1) Spotify contribution
    base = 0.0
    if spotify_stats:
        pop = _safe_num(spotify_stats.get("popularity"), 0.0)  # 0–100
        listeners = _safe_num(spotify_stats.get("monthly_listeners"), 0.0)

        # Popularity has the largest weight
        base += pop * 0.5

        if listeners > 5_000_000:
            base += 15
            reasons.append("Strong global monthly listeners on Spotify")
        elif listeners > 1_000_000:
            base += 8
            reasons.append("Solid monthly listeners on Spotify")

        # Check if this city appears as a top market (if provided)
        top_cities = spotify_stats.get("top_cities")
        if city and isinstance(top_cities, list):
            if city.lower() in [c.lower() for c in top_cities]:
                base += 10
                reasons.append("City appears in Spotify top-city data")

    # 2) YouTube contribution
    if youtube_profile:
        momentum = _safe_num(
            youtube_profile.get("momentum_score")
            or youtube_profile.get("momentum")
            or youtube_profile.get("growth_index"),
            0.0,
        )
        base += momentum * 0.3

        if momentum >= 8:
            reasons.append("High recent YouTube momentum")
        elif momentum >= 5:
            reasons.append("Moderate YouTube momentum")

    # 3) Venue / location flavor text
    if venue:
        reasons.append(f"Venue: {venue}")

    if country:
        reasons.append(f"Country: {country}")

    # Clamp score to 0–100
    score = int(max(0.0, min(100.0, base)))

    if not reasons:
        reasons.append("Limited data; baseline heat applied")

    reason_text = "; ".join(reasons)

    logger.debug(
        "Market heat for %s (%s, %s, %s): %s – %s",
        artist_name,
        city,
        country,
        venue,
        score,
        reason_text,
    )

    return score, reason_text
