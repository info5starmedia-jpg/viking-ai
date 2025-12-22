# agents/routing_agent.py
# ------------------------------------------------------------
# Routing Intelligence Agent (tour routing suggestions)
# ------------------------------------------------------------

from typing import Dict, Any, List


async def get_route_suggestions(artist_name: str) -> Dict[str, Any]:
    """
    Returns imaginary high-demand city suggestions.
    This can later be replaced with real heatmaps.
    """

    top_cities = [
        "New York", "Los Angeles", "Chicago",
        "Toronto", "Houston", "London",
        "Berlin", "Sydney"
    ]

    return {
        "artist": artist_name,
        "recommended_cities": top_cities[:5],
        "logic": "placeholder heuristic – upgrade soon with real metrics",
    }
