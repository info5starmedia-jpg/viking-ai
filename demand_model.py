# demand_model.py — Viking AI (stable, non-async)
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Any, Optional


def clamp(n: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, n))


@dataclass
class DemandSignals:
    market_heat: int = 50              # 0..100
    spotify_popularity: int = 50       # 0..100
    spotify_followers: int = 0
    youtube_momentum: int = 50         # 0..100
    venue_capacity: Optional[int] = None
    inventory_pressure: Optional[int] = None  # 0..100 (higher = more sold)


def score_event(event: Dict[str, Any], signals: DemandSignals) -> Dict[str, Any]:
    """
    IMPORTANT: This is intentionally NON-async so callers never accidentally print a coroutine.
    Returns a dict with probability 0..100 and some reasoning.
    """
    # Baseline demand from market + artist
    base = 0.45 * signals.market_heat + 0.35 * signals.spotify_popularity + 0.20 * signals.youtube_momentum

    # Venue size adjustment (bigger venue => slightly lower sellout probability at same demand)
    cap = signals.venue_capacity or 0
    if cap <= 0:
        venue_adj = 0.0
    elif cap <= 3000:
        venue_adj = +6.0
    elif cap <= 7000:
        venue_adj = +2.0
    elif cap <= 12000:
        venue_adj = -2.0
    elif cap <= 20000:
        venue_adj = -6.0
    else:
        venue_adj = -10.0

    # Inventory pressure adjustment (if known)
    inv_adj = 0.0
    if isinstance(signals.inventory_pressure, int):
        # If inventory pressure says it's already selling hard, bump probability
        inv_adj = (signals.inventory_pressure - 50) * 0.18  # +/-9 max

    score = clamp(base + venue_adj + inv_adj, 0, 100)

    reasons = []
    reasons.append(f"Market heat {signals.market_heat}/100")
    reasons.append(f"Spotify popularity {signals.spotify_popularity}/100")
    reasons.append(f"YouTube momentum {signals.youtube_momentum}/100")
    if cap:
        reasons.append(f"Venue capacity {cap}")
    if isinstance(signals.inventory_pressure, int):
        reasons.append(f"Inventory pressure {signals.inventory_pressure}/100")

    return {
        "sellout_probability": int(round(score)),
        "reasons": reasons,
    }
