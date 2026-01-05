# city_boosts.py
from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, List, Tuple, Optional

DEFAULT_CITY_LIST = [
    # US / Canada (major touring markets)
    "New York, NY", "Brooklyn, NY", "Queens, NY", "Newark, NJ", "Jersey City, NJ",
    "Los Angeles, CA", "Inglewood, CA", "Anaheim, CA", "San Diego, CA",
    "San Francisco, CA", "Oakland, CA", "San Jose, CA", "Sacramento, CA",
    "Las Vegas, NV", "Phoenix, AZ", "Denver, CO", "Salt Lake City, UT",
    "Seattle, WA", "Tacoma, WA", "Portland, OR", "Vancouver, BC",
    "Calgary, AB", "Edmonton, AB", "Winnipeg, MB",
    "Toronto, ON", "Ottawa, ON", "Montreal, QC", "Quebec City, QC", "Halifax, NS",
    "Chicago, IL", "Rosemont, IL", "Detroit, MI", "Cleveland, OH", "Columbus, OH",
    "Pittsburgh, PA", "Philadelphia, PA", "Boston, MA", "Washington, DC", "Baltimore, MD",
    "Atlanta, GA", "Miami, FL", "Fort Lauderdale, FL", "Orlando, FL", "Tampa, FL",
    "Charlotte, NC", "Raleigh, NC", "Nashville, TN", "Memphis, TN",
    "Dallas, TX", "Fort Worth, TX", "Houston, TX", "Austin, TX", "San Antonio, TX",
    "New Orleans, LA", "St. Louis, MO", "Kansas City, MO", "Minneapolis, MN",
]

CITY_HISTORY_PATH = os.getenv("CITY_HISTORY_JSON", "/opt/viking-ai/city_history.json")


def _safe_float(x: Any, default: float = 0.0) -> float:
    try:
        if x is None:
            return default
        return float(x)
    except Exception:
        return default


def _load_city_history(path: str = CITY_HISTORY_PATH) -> Dict[str, Any]:
    """
    Optional history file you can write from tour_scan_monitor.py.
    Expected shape (flexible):
    {
      "cities": {
        "Toronto, ON": {"count": 12, "last_seen_unix": 123...},
        ...
      }
    }
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f) or {}
    except FileNotFoundError:
        return {}
    except Exception:
        return {}


def _tm_event_density(city: str) -> float:
    """
    Placeholder density score. If you later wire Ticketmaster search counts per city,
    return something like normalized_count (0..1).
    For now: 0.0 (meaning 'no boost').
    """
    return 0.0


def compute_city_weights(
    artist: str,
    socials_heat: Optional[Dict[str, Any]] = None,
    now_unix: Optional[float] = None,
    history_path: str = CITY_HISTORY_PATH,
) -> Dict[str, float]:
    """
    Compute a weight per city from:
    - Ticketmaster event density (future hook)
    - Your RSS/tour scan history (optional file)
    - Socials heat (optional)
    """
    now = now_unix or time.time()
    history = _load_city_history(history_path)
    cities_hist = (history.get("cities") or {}) if isinstance(history, dict) else {}

    # Social heat: normalize a few values if present
    heat_score = None
    if isinstance(socials_heat, dict):
        heat_score = socials_heat.get("heat_score")
    heat = _safe_float(heat_score, default=0.0)

    weights: Dict[str, float] = {}

    for city in DEFAULT_CITY_LIST:
        w = 1.0

        # 1) Ticketmaster density (0..1)
        tm = _tm_event_density(city)
        w += 0.35 * _safe_float(tm, 0.0)

        # 2) City history count (log-ish)
        h = cities_hist.get(city) or {}
        if isinstance(h, dict):
            count = _safe_float(h.get("count"), 0.0)
            last_seen = _safe_float(h.get("last_seen_unix"), 0.0)
            recency_days = (now - last_seen) / 86400.0 if last_seen else None

            # more sightings => more boost
            w += min(0.50, 0.08 * count)

            # if recently seen in your RSS history, small extra boost
            if recency_days is not None and recency_days <= 60:
                w += 0.15

        # 3) Social heat: small global boost (artist-level)
        # (Later you can do geo-aware boosts.)
        w += min(0.30, 0.02 * heat)

        weights[city] = max(0.25, w)

    return weights


def score_city(
    city: str,
    base_score: float,
    weights: Dict[str, float],
) -> float:
    return base_score * _safe_float(weights.get(city), 1.0)


def get_city_scores(
    artist: str,
    base_city_scores: Optional[Dict[str, float]] = None,
    socials_heat: Optional[Dict[str, Any]] = None,
) -> Dict[str, float]:
    """
    Return final city scores (float) after weights.
    base_city_scores can be your own seed scores per city (e.g. from genre heuristics).
    If omitted, all base scores default to 1.0 (what you're seeing now).
    """
    base = base_city_scores or {c: 1.0 for c in DEFAULT_CITY_LIST}
    weights = compute_city_weights(artist, socials_heat=socials_heat)
    return {c: score_city(c, base.get(c, 1.0), weights) for c in base.keys()}


def rank_cities(
    artist: str,
    top_n: int = 20,
    base_city_scores: Optional[Dict[str, float]] = None,
    socials_heat: Optional[Dict[str, Any]] = None,
) -> List[Tuple[str, float]]:
    scores = get_city_scores(artist, base_city_scores=base_city_scores, socials_heat=socials_heat)
    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    return ranked[:max(1, int(top_n))]


# Aliases (so your earlier code + your introspection script work)
def rank_cities_weighted(*args, **kwargs):
    return rank_cities(*args, **kwargs)


def city_debug(
    artist: str,
    top_n: int = 20,
    socials_heat: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    weights = compute_city_weights(artist, socials_heat=socials_heat)
    ranked = rank_cities(artist, top_n=top_n, socials_heat=socials_heat)

    # show a few cities with components
    history = _load_city_history(CITY_HISTORY_PATH).get("cities", {})
    details = []
    for city, score in ranked:
        h = history.get(city, {}) if isinstance(history, dict) else {}
        details.append({
            "city": city,
            "score": score,
            "weight": weights.get(city, 1.0),
            "history": h,
            "tm_density": _tm_event_density(city),
        })

    return {
        "artist": artist,
        "history_path": CITY_HISTORY_PATH,
        "note": "If all scores are 1.0, you likely have no city_history.json yet and tm_density is still 0.0.",
        "top": details,
    }
