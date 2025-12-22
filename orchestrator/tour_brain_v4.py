# orchestrator/tour_brain_v4.py — Touring Brain (stable + defensive)
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from orchestrator.orchestrator_v2 import run_llm_analysis
from agents.spotify_agent import get_spotify_profile
from agents.youtube_agent import get_youtube_profile
from demand_model import DemandSignals, score_event

logger = logging.getLogger("tour_brain_v4")


def _as_dict_venue(venue_obj: Any) -> Dict[str, Any]:
    """
    Ticketmaster sometimes gives venue as dict, but your pipeline sometimes passes a string.
    Normalize to dict so .get never crashes.
    """
    if isinstance(venue_obj, dict):
        return venue_obj
    if isinstance(venue_obj, str):
        return {"name": venue_obj}
    return {}


def compute_market_heat(
    *,
    artist: str,
    city: Optional[str] = None,
    state: Optional[str] = None,
    country: Optional[str] = None,
    genre: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Always returns a dict (NOT a tuple).
    Accepts city kwarg (fix for your 'unexpected keyword argument city' error).
    """
    score = 50
    signals = {}

    if country:
        signals["country"] = country
    if city:
        signals["city"] = city
    if state:
        signals["state"] = state
    if genre:
        signals["genre"] = genre

    # Simple heuristics (you can improve later)
    # Big markets + slight bump
    big_cities = {"new york", "los angeles", "london", "toronto", "chicago", "miami", "paris", "berlin"}
    if city and city.strip().lower() in big_cities:
        score += 8

    # US/UK/CA/EU baseline stability
    if country:
        c = country.strip().lower()
        if c in {"us", "usa", "united states"}:
            score += 4
        if c in {"uk", "united kingdom"}:
            score += 4
        if c in {"ca", "canada"}:
            score += 3

    score = max(0, min(100, score))
    return {"market_heat": score, "signals": signals}


async def get_event_intel(event: Dict[str, Any], artist: str) -> Dict[str, Any]:
    """
    Main brain call used by /intel.
    Returns dict with:
      - market_heat (dict)
      - spotify (dict)
      - youtube (dict)
      - sellout (dict)
      - analysis (str)
    """
    artist = (artist or "").strip()
    if not artist:
        return {"error": "No artist provided."}

    venue_obj = _as_dict_venue(event.get("venue") or event.get("_embedded", {}).get("venues", [{}])[0])
    city = (
        event.get("city")
        or event.get("_embedded", {}).get("venues", [{}])[0].get("city", {}).get("name")
        or venue_obj.get("city")
    )
    state = (
        event.get("state")
        or event.get("_embedded", {}).get("venues", [{}])[0].get("state", {}).get("stateCode")
        or venue_obj.get("state")
    )
    country = (
        event.get("country")
        or event.get("_embedded", {}).get("venues", [{}])[0].get("country", {}).get("countryCode")
        or venue_obj.get("country")
    )

    market_heat = compute_market_heat(artist=artist, city=city, state=state, country=country)

    spotify = get_spotify_profile(artist, light_mode=True)  # keep stable + fast
    youtube = get_youtube_profile(artist, light_mode=True)  # keep stable + fast

    sig = DemandSignals(
        market_heat=int(market_heat.get("market_heat", 50)),
        spotify_popularity=int(spotify.get("popularity", 50) or 50),
        spotify_followers=int(spotify.get("followers", 0) or 0),
        youtube_momentum=int(youtube.get("momentum", 50) or 50),
        venue_capacity=int(event.get("venue_capacity") or 0) or None,
        inventory_pressure=int(event.get("inventory_pressure") or 0) or None,
    )

    sellout = score_event(event, sig)

    analysis_prompt = (
        f"Artist: {artist}\n"
        f"City: {city} State: {state} Country: {country}\n"
        f"Market heat: {sig.market_heat}/100\n"
        f"Spotify: followers={sig.spotify_followers}, popularity={sig.spotify_popularity}/100\n"
        f"YouTube momentum: {sig.youtube_momentum}/100\n"
        f"Estimated sellout probability: {sellout.get('sellout_probability')}%\n"
        f"Give 4-6 bullet points: why, risks, and 1 actionable strategy for ticket flipping."
    )
    analysis = run_llm_analysis(analysis_prompt, mode="short")

    return {
        "market_heat": market_heat,   # dict
        "spotify": spotify,           # dict
        "youtube": youtube,           # dict
        "sellout": sellout,           # dict
        "analysis": analysis,         # string
        "venue": venue_obj,           # dict
    }
