import logging
from agents.market_heat_agent import compute_market_heat
from agents.spotify_agent import get_spotify_profile

logger = logging.getLogger("tour_planner")


def plan_tour(artist: str, regions: str = "NA"):
    """
    Very rough tour planner.

    Returns dict:
    {
        "artist": str,
        "region": str,
        "recommended_venue": str,
        "market_heat": int,
        "risk": int,
        "routing_hint": str,
        "estimated_gross": float,
    }
    """

    sp = get_spotify_profile(artist)
    base_listeners = 0

    if isinstance(sp, dict) and "error" not in sp:
        base_listeners = sp.get("monthly_listeners", 0) or 0
    else:
        logger.warning("Spotify data unavailable in plan_tour for %s", artist)

    # Fallback if we truly have nothing
    if base_listeners <= 0:
        base_listeners = 100_000

    # Market heat
    market = compute_market_heat(artist)
    heat = int(market.get("market_heat", 0))

    # Venue tier
    if base_listeners > 5_000_000:
        venue = "Arenas (10k–20k)"
    elif base_listeners > 1_000_000:
        venue = "Theaters (3k–8k)"
    else:
        venue = "Clubs (500–2500)"

    risk = max(0, min(100, 100 - heat))

    estimated_gross = base_listeners * (heat / 100.0) * 0.015

    return {
        "artist": artist,
        "region": regions,
        "recommended_venue": venue,
        "market_heat": heat,
        "risk": risk,
        "routing_hint": "Focus on higher-heat markets first and avoid over-building in cold regions.",
        "estimated_gross": estimated_gross,
    }
