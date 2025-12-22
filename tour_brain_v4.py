import logging
from typing import Dict, Any, Optional

from agents.spotify_agent import get_spotify_profile
from agents.youtube_agent import get_youtube_profile
from agents.market_heat_agent import compute_market_heat
from agents.tm_live_inventory import get_live_seatmap, summarize_inventory

# Use the stable, synchronous demand model for now
from demand_model import score_event

from orchestrator.orchestrator_v2 import run_orchestrator

logger = logging.getLogger("tour_brain_v4")


async def get_event_intel(
    event: Dict[str, Any],
    artist: str,
    region: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Viking AI Touring Brain v4 (stable mode)

    Combines:
      - Spotify profile
      - YouTube momentum
      - Market heat (Spotify + YouTube + memory)
      - Live seatmap inventory (if configured)
      - Demand model scoring (synchronous demand_model.py)
      - LLM narrative (run_orchestrator)
    """

    # ----------------------------------
    # 1) Spotify profile
    # ----------------------------------
    try:
        spotify = get_spotify_profile(artist)
    except Exception as e:
        logger.exception("Spotify profile error for %s: %s", artist, e)
        spotify = {"error": str(e)}

    # ----------------------------------
    # 2) YouTube profile (now lightweight)
    # ----------------------------------
    try:
        youtube = get_youtube_profile(artist)
    except Exception as e:
        logger.exception("YouTube profile error for %s: %s", artist, e)
        youtube = {"error": str(e), "momentum": 0}

    # ----------------------------------
    # 3) Market heat
    # ----------------------------------
    try:
        market_heat = compute_market_heat(artist)
    except Exception as e:
        logger.exception("Market heat error for %s: %s", artist, e)
        market_heat = {"error": str(e), "market_heat": 0}

    # ----------------------------------
    # 4) Live seatmap (if available)
    # ----------------------------------
    seat_summary: Dict[str, Any] = {}
    try:
        live = await get_live_seatmap(event.get("id"))
        if isinstance(live, dict) and "seatmap" in live:
            seat_summary = await summarize_inventory(live["seatmap"])
        else:
            seat_summary = {}
    except Exception as e:
        logger.exception("Seatmap error for event %s: %s", event.get("id"), e)
        seat_summary = {}

    # ----------------------------------
    # 5) Demand model scoring (sync)
    # ----------------------------------
    features = {
        "event": event,
        "spotify": spotify,
        "youtube": youtube,
        "market_heat": market_heat,
        "seat_summary": seat_summary,
        "region": region,
    }

    demand: Dict[str, Any] = {}
    try:
        # demand_model.score_event is synchronous in this stable path
        score_res = score_event(event, features)

        if isinstance(score_res, dict):
            demand = score_res
        else:
            demand = {"raw_score": score_res}
    except Exception as e:
        logger.exception("Demand model error: %s", e)
        demand = {"error": str(e)}

    sellout_prob = demand.get("sellout_probability") or demand.get("raw_score")

    # ----------------------------------
    # 6) LLM narrative with orchestrator
    # ----------------------------------
    momentum = youtube.get("momentum", 0) if isinstance(youtube, dict) else 0
    heat_score = market_heat.get("market_heat", 0) if isinstance(market_heat, dict) else 0
    ml = spotify.get("monthly_listeners") if isinstance(spotify, dict) else None
    pop = spotify.get("popularity") if isinstance(spotify, dict) else None

    llm_prompt = f"""
You are VikingAI, a pro touring and ticketing analyst.

Artist: {artist}
Region focus: {region or "global"}

Event:
{event}

Spotify snapshot:
monthly_listeners={ml}, popularity={pop}

YouTube snapshot:
momentum_score={momentum}

Market Heat:
{market_heat}

Seat Summary (if any):
{seat_summary}

Demand model output:
{demand}

Explain in a short, practical way:
1) How strong demand is and why.
2) Which markets or fan segments are driving demand.
3) How aggressive we can be on pricing and number of shows.
4) Whether this looks like a flip / arbitrage opportunity.
"""

    try:
        llm = await run_orchestrator(llm_prompt, use_web_search=False)
        llm_output = llm.get("output", "")
    except Exception as e:
        logger.exception("LLM analysis error: %s", e)
        llm_output = f"(Error: no LLM available – {e})"

    # ----------------------------------
    # Final structured payload
    # ----------------------------------
    return {
        "event": event,
        "spotify": spotify,
        "youtube": youtube,
        "market_heat": market_heat,
        "seat_summary": seat_summary,
        "demand": demand,
        "llm_output": llm_output,
        "sellout_probability": sellout_prob,
    }
