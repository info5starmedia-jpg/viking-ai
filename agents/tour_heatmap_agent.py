# agents/tour_heatmap_agent.py
# ------------------------------------------------------------
# Touring Heatmap Agent for Viking AI
# ------------------------------------------------------------

from typing import Dict, Any, List

# This module expects that other agents are available and can be passed in:
# - spotify_agent.get_artist_stats
# - trends_agent.get_google_trends
# - socials_agent.get_socials_heat


async def get_market_heatmap(
    artist_name: str,
    spotify_stats: Dict[str, Any],
    trends: Dict[str, Any],
    socials: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Combines signals into a synthetic market heatmap.
    For now we generate a simple global list of cities with weighted scores.
    """

    base_cities = [
        "New York",
        "Los Angeles",
        "Chicago",
        "Miami",
        "Toronto",
        "Mexico City",
        "London",
        "Paris",
        "Berlin",
        "Sydney",
    ]

    popularity = spotify_stats.get("popularity", 50) or 50
    trend_score = trends.get("trend_score", 50) or 50
    social_heat = socials.get("heat_score", 50) or 50

    heatmap: List[Dict[str, Any]] = []

    for idx, city in enumerate(base_cities):
        city_factor = 1.0 - (idx * 0.04)  # slight drop by index
        score = (
            popularity * 0.5
            + trend_score * 0.3
            + social_heat * 0.2
        ) * city_factor

        heatmap.append({
            "city": city,
            "heat_score": round(min(score, 100), 2),
        })

    heatmap_sorted = sorted(heatmap, key=lambda c: c["heat_score"], reverse=True)

    return {
        "artist": artist_name,
        "markets": heatmap_sorted,
    }
