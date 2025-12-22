# demand_model_v2.py
# ------------------------------------------------------------
# Advanced sell-out scoring engine for Viking AI
# ------------------------------------------------------------

from typing import Dict, Any


async def score_event(event: Dict[str, Any], agents: Dict[str, Any]) -> Dict[str, Any]:
    """
    event: Ticketmaster-style event dict with at least 'id' and 'name'
    agents: bundle of signals, keys may include:
        - spotify
        - trends
        - tiktok
        - socials
        - seats   (from seats_agent or tm_live_inventory)
        - heatmap (from tour_heatmap_agent)
    """

    spotify = agents.get("spotify", {})
    trends = agents.get("trends", {})
    tiktok = agents.get("tiktok", {})
    socials = agents.get("socials", {})
    seats = agents.get("seats", {})
    heatmap = agents.get("heatmap", {})

    score = 40.0  # base line

    # Spotify popularity: strong base signal
    score += (spotify.get("popularity", 0) or 0) * 0.35

    # Search trend score
    score += (trends.get("trend_score", 0) or 0) * 0.25

    # TikTok acceleration (if provided)
    if isinstance(tiktok, dict):
        accel = float(tiktok.get("weekly_growth", 0) or 0)
        score += min(accel / 1000.0, 20.0)  # keep bounded

    # Social heat
    score += (socials.get("heat_score", 0) or 0) * 0.15

    # Seatmap heat – use hottest zone if present
    if isinstance(seats, dict):
        zones = seats.get("zones", seats.get("sections", []))
        if zones:
            hottest = zones[0]
            zone_heat = float(hottest.get("heat", 0) or 0)
            score += min(zone_heat * 3, 20.0)

    # Market heat (average of top 3 cities for now)
    if isinstance(heatmap, dict):
        markets = heatmap.get("markets", [])
        if markets:
            top3 = markets[:3]
            avg_heat = sum(m["heat_score"] for m in top3) / len(top3)
            score += avg_heat * 0.1

    # Clamp & return
    final = max(0.0, min(score, 100.0))

    return {
        "event_id": event.get("id"),
        "event_name": event.get("name"),
        "sellout_probability": round(final, 2),
    }
