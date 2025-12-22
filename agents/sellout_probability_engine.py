"""agents/sellout_probability_engine.py

Viking AI — Sellout Probability (0–100)
--------------------------------------
Computes per-event sellout probability using the stable demand model
(`demand_model.DemandSignals` + `demand_model.score_event`).

We deliberately keep this best-effort:
  • No hard dependency on seatmap scraping or venue capacity datasets
  • Works with raw Ticketmaster event dicts OR flattened events

Inputs:
  event: dict with at least `city` and optionally venue info
  spotify: dict from agents.spotify_agent.get_spotify_profile
  youtube: dict from agents.youtube_agent.get_youtube_profile
  city_weight: optional 0..100 adjustment from demand_heatmap

Output:
  {
    "sellout_probability": int 0..100,
    "reasons": [...],
    "market_heat": int,
  }
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from agents.market_heat_agent import compute_market_heat
from demand_model import DemandSignals, score_event


def _extract_city(event: Dict[str, Any]) -> str:
    # Accept either flattened event or raw TM event
    if event.get("city"):
        return str(event.get("city") or "")
    venues = ((event.get("_embedded") or {}).get("venues") or [])
    if venues:
        v0 = venues[0] or {}
        return str(((v0.get("city") or {}).get("name")) or "")
    return ""


def _extract_venue(event: Dict[str, Any]) -> str:
    if event.get("venue"):
        return str(event.get("venue") or "")
    venues = ((event.get("_embedded") or {}).get("venues") or [])
    if venues:
        v0 = venues[0] or {}
        return str(v0.get("name") or "")
    return ""


def score_sellout_probability(
    event: Dict[str, Any],
    spotify: Optional[Dict[str, Any]] = None,
    youtube: Optional[Dict[str, Any]] = None,
    city_weight: Optional[int] = None,
    venue_capacity: Optional[int] = None,
    inventory_pressure: Optional[int] = None,
) -> Dict[str, Any]:
    spotify = spotify or {}
    youtube = youtube or {}

    city = _extract_city(event)
    venue = _extract_venue(event)

    # compute_market_heat returns (score, reason)
    heat, heat_reason = compute_market_heat(
        artist_name=str(event.get("artist") or event.get("name") or ""),
        city=city or None,
        venue=venue or None,
        spotify_stats=spotify,
        youtube_profile=youtube,
    )

    # Blend an optional city_weight from heatmap (0..100) into market heat
    if isinstance(city_weight, int):
        heat = int(round((0.70 * heat) + (0.30 * max(0, min(100, city_weight)))))

    signals = DemandSignals(
        market_heat=int(max(0, min(100, heat))),
        spotify_popularity=int(spotify.get("popularity", 50) or 50),
        spotify_followers=int(spotify.get("followers", 0) or 0),
        youtube_momentum=int(youtube.get("momentum", 50) or 50),
        venue_capacity=venue_capacity,
        inventory_pressure=inventory_pressure,
    )

    out = score_event(event, signals)
    out["market_heat"] = signals.market_heat
    out["market_heat_reason"] = heat_reason
    return out

# --- Back-compat export ---
def score_events_sellout(*args, **kwargs):
    """
    Compatibility wrapper for older imports.
    Tries to call the primary sellout scoring function in this module.
    """
    for fn_name in ("score_sellout", "score_events", "score_event_sellout", "score_events_probabilities"):
        fn = globals().get(fn_name)
        if callable(fn):
            return fn(*args, **kwargs)
    raise ImportError("No underlying scoring function found to back score_events_sellout()")

# --- Back-compat export (final) ---
def score_events_sellout(event: dict, *args, **kwargs):
    """
    Backwards-compatible name used by intel command.
    Scores one event dict -> {"probability": int, "label": str, "reasons": [...]}
    """
    return score_sellout_probability(event, *args, **kwargs)
