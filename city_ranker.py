import json
import os
import math
import time
from typing import Any, Dict, List, Optional, Tuple

STATE_PATH = "/opt/viking-ai/viking_state.json"


def _load_state() -> Dict[str, Any]:
    try:
        with open(STATE_PATH, "r", encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception:
        return {}


def _save_state(state: Dict[str, Any]) -> None:
    tmp = STATE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, sort_keys=True, default=str)
    os.replace(tmp, STATE_PATH)


def _norm_city(city: str) -> str:
    return (city or "").strip()


def record_tour_scan_city(artist: str, city: str) -> None:
    """Called by tour_scan_monitor when a tour announcement is detected."""
    artist = (artist or "").strip().lower()
    city = _norm_city(city)
    if not artist or not city:
        return

    st = _load_state()
    ts = int(time.time())
    st.setdefault("tour_scan_city_counts", {})
    st.setdefault("tour_scan_city_last_seen_unix", {})
    st["tour_scan_city_counts"].setdefault(artist, {})
    st["tour_scan_city_counts"][artist][city] = int(st["tour_scan_city_counts"][artist].get(city, 0)) + 1
    st["tour_scan_city_last_seen_unix"].setdefault(artist, {})
    st["tour_scan_city_last_seen_unix"][artist][city] = ts
    _save_state(st)


def get_tour_scan_city_counts(artist: str) -> Dict[str, int]:
    artist = (artist or "").strip().lower()
    st = _load_state()
    counts = (st.get("tour_scan_city_counts") or {}).get(artist) or {}
    out: Dict[str, int] = {}
    for k, v in counts.items():
        try:
            out[str(k)] = int(v)
        except Exception:
            continue
    return out


def _safe_int(x: Any) -> Optional[int]:
    try:
        if x is None:
            return None
        return int(float(x))
    except Exception:
        return None


def _log_scale(n: Optional[int], cap: float) -> float:
    """0..cap based on log scale of n"""
    if not n or n <= 0:
        return 0.0
    return min(cap, math.log10(max(10, n)) * (cap / 6.0))


def _count_scale(n: int, cap: float) -> float:
    """0..cap based on count saturation"""
    if n <= 0:
        return 0.0
    return min(cap, math.sqrt(n) * (cap / 3.0))


def rank_cities(
    artist: str,
    base_cities: List[str],
    tm_city_counts: Dict[str, int],
    tour_scan_city_counts: Dict[str, int],
    social_heat: Optional[float],
    spotify_followers: Optional[int],
    monthly_listeners: Optional[int],
    yt_subs: Optional[int],
    extra_city_boosts: Optional[Dict[str, float]] = None,
    limit: int = 40,
) -> List[Dict[str, Any]]:
    """
    Returns list of dicts: {city, score, breakdown:{...}}
    """
    social_heat = float(social_heat) if social_heat is not None else None
    if social_heat is not None:
        social_heat = max(0.0, min(1.0, social_heat))

    # Global demand factor (artist strength)
    strength = (
        0.35 * _log_scale(spotify_followers, 10.0) +
        0.35 * _log_scale(monthly_listeners, 10.0) +
        0.30 * _log_scale(yt_subs, 10.0)
    )  # ~0..10
    heat_bonus = 0.0 if social_heat is None else (social_heat * 4.0)  # 0..4

    # candidates = union of everything
    candidates = []
    seen = set()
    for c in (base_cities or []):
        c2 = _norm_city(c)
        if c2 and c2.lower() not in seen:
            seen.add(c2.lower())
            candidates.append(c2)
    for c in list(tm_city_counts.keys()) + list(tour_scan_city_counts.keys()):
        c2 = _norm_city(c)
        if c2 and c2.lower() not in seen:
            seen.add(c2.lower())
            candidates.append(c2)

    boosts = extra_city_boosts or {}

    ranked: List[Dict[str, Any]] = []
    for city in candidates:
        tm_n = int(tm_city_counts.get(city, 0) or 0)
        ts_n = int(tour_scan_city_counts.get(city, 0) or 0)

        # density signals
        tm_score = _count_scale(tm_n, 12.0)          # 0..12
        tour_score = _count_scale(ts_n, 10.0)        # 0..10

        # combine into city score
        score = (
            0.45 * tm_score +
            0.35 * tour_score +
            0.20 * strength +
            heat_bonus
        )

        # optional external boosts (tavily/google/llm summaries)
        score += float(boosts.get(city, 0.0) or 0.0)

        ranked.append({
            "city": city,
            "score": round(score, 2),
            "breakdown": {
                "tm_count": tm_n,
                "tour_scan_count": ts_n,
                "tm_score": round(tm_score, 2),
                "tour_score": round(tour_score, 2),
                "artist_strength": round(strength, 2),
                "social_heat_bonus": round(heat_bonus, 2),
                "external_boost": round(float(boosts.get(city, 0.0) or 0.0), 2),
            }
        })

    ranked.sort(key=lambda x: x["score"], reverse=True)
    return ranked[: max(1, int(limit))]
