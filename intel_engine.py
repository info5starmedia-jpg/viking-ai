import math
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

# ----------------------------
# Safe import helpers
# ----------------------------

def _try_import(path: str):
    try:
        module = __import__(path, fromlist=["*"])
        return module
    except Exception:
        return None

spotify_agent = _try_import("spotify_agent")
youtube_agent = _try_import("youtube_agent")
tiktok_agent = _try_import("tiktok_agent")
ticketmaster_agent = _try_import("ticketmaster_agent")

tour_news_agent = _try_import("agents.tour_news_agent_v3")
tour_intel_agent = _try_import("tour_intel_agent")  # optional legacy


# ----------------------------
# Core scoring helpers
# ----------------------------

def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))

def _safe_int(x: Any, default: int = 0) -> int:
    try:
        return int(float(x))
    except Exception:
        return default

def _safe_float(x: Any, default: float = 0.0) -> float:
    try:
        return float(x)
    except Exception:
        return default

def _log1p_scale(x: float, max_x: float) -> float:
    """0..1 scale using log1p to handle large ranges."""
    x = _clamp(x, 0.0, max_x)
    return math.log1p(x) / math.log1p(max_x)

def _star_rating(score01: float) -> int:
    """Convert 0..1 into 1..5 stars."""
    score01 = _clamp(score01, 0.0, 1.0)
    if score01 >= 0.85:
        return 5
    if score01 >= 0.65:
        return 4
    if score01 >= 0.45:
        return 3
    if score01 >= 0.25:
        return 2
    return 1

def _sellout_probability(
    artist_score01: float,
    venue_capacity: int,
    days_until: Optional[int] = None,
    market_boost01: float = 0.0,
) -> int:
    """
    Heuristic:
    - Higher artist_score increases demand
    - Smaller venues sell out easier
    - Near-term dates (within ~45 days) slightly higher conversion
    """
    venue_capacity = max(0, venue_capacity)
    # venue factor: small venues ~1, huge venues ~0
    # 2k => ~0.92, 10k => ~0.73, 20k => ~0.63, 50k => ~0.46
    venue_factor = 1.0 / (1.0 + math.log10(1.0 + venue_capacity / 1500.0))

    time_factor = 0.0
    if days_until is not None:
        # within 45 days => +, far out => slightly lower
        time_factor = _clamp((45.0 - float(days_until)) / 90.0, -0.15, 0.15)

    raw = (
        0.55 * artist_score01 +
        0.30 * venue_factor +
        0.15 * _clamp(market_boost01, 0.0, 1.0) +
        time_factor
    )
    return int(round(_clamp(raw, 0.0, 1.0) * 100.0))


# ----------------------------
# Data pulling (best-effort)
# ----------------------------

def fetch_spotify_signals(artist: str) -> Dict[str, Any]:
    """
    Expected output:
      monthly_listeners, popularity, followers, artist_url
    Works with your spotify_agent if it provides anything similar.
    """
    out: Dict[str, Any] = {"ok": False}
    if not spotify_agent:
        return out
    # Try common function names
    for fn_name in ("get_artist_stats", "fetch_artist_stats", "spotify_artist_stats", "get_spotify_artist"):
        fn = getattr(spotify_agent, fn_name, None)
        if callable(fn):
            try:
                data = fn(artist)
                if isinstance(data, dict):
                    data["ok"] = True
                    return data
                return {"ok": True, "raw": data}
            except Exception as e:
                out["error"] = str(e)
                return out
    return out

def fetch_youtube_signals(artist: str) -> Dict[str, Any]:
    out: Dict[str, Any] = {"ok": False}
    if not youtube_agent:
        return out
    for fn_name in ("get_youtube_stats", "fetch_youtube_stats", "youtube_artist_stats"):
        fn = getattr(youtube_agent, fn_name, None)
        if callable(fn):
            try:
                data = fn(artist)
                if isinstance(data, dict):
                    data["ok"] = True
                    return data
                return {"ok": True, "raw": data}
            except Exception as e:
                out["error"] = str(e)
                return out
    return out

def fetch_tiktok_signals(artist: str) -> Dict[str, Any]:
    out: Dict[str, Any] = {"ok": False}
    if not tiktok_agent:
        return out
    for fn_name in ("get_tiktok_stats", "fetch_tiktok_stats", "tiktok_artist_stats"):
        fn = getattr(tiktok_agent, fn_name, None)
        if callable(fn):
            try:
                data = fn(artist)
                if isinstance(data, dict):
                    data["ok"] = True
                    return data
                return {"ok": True, "raw": data}
            except Exception as e:
                out["error"] = str(e)
                return out
    return out

def fetch_ticketmaster_events(artist: str, limit: int = 12) -> List[Dict[str, Any]]:
    """
    Expected to work with your existing ticketmaster_agent functions.
    Falls back safely if names differ.
    """
    if not ticketmaster_agent:
        return []
    for fn_name in ("search_events_for_artist", "search_events", "tm_search_events", "find_events"):
        fn = getattr(ticketmaster_agent, fn_name, None)
        if callable(fn):
            try:
                data = fn(artist)
                if isinstance(data, list):
                    return data[:limit]
                if isinstance(data, dict) and "events" in data and isinstance(data["events"], list):
                    return data["events"][:limit]
                return []
            except Exception:
                return []
    return []

def fetch_tour_news(artist: str) -> Dict[str, Any]:
    if not tour_news_agent:
        return {"ok": False}
    fn = getattr(tour_news_agent, "get_tour_news", None)
    if callable(fn):
        try:
            data = fn(artist)
            if isinstance(data, dict):
                data["ok"] = True
                return data
            return {"ok": True, "raw": data}
        except Exception as e:
            return {"ok": False, "error": str(e)}
    return {"ok": False}


# ----------------------------
# Normalization & city logic
# ----------------------------

def _extract_city(event: Dict[str, Any]) -> str:
    # Try common Ticketmaster-like shapes
    city = (
        event.get("city")
        or event.get("venue_city")
        or (event.get("venue", {}) or {}).get("city")
        or ((event.get("_embedded", {}) or {}).get("venues", [{}])[0].get("city", {}) or {}).get("name")
    )
    if isinstance(city, dict):
        city = city.get("name")
    return (city or "").strip() or "Unknown"

def _extract_state(event: Dict[str, Any]) -> str:
    state = (
        event.get("state")
        or event.get("venue_state")
        or (event.get("venue", {}) or {}).get("state")
        or ((event.get("_embedded", {}) or {}).get("venues", [{}])[0].get("state", {}) or {}).get("stateCode")
    )
    if isinstance(state, dict):
        state = state.get("stateCode") or state.get("name")
    return (state or "").strip()

def _extract_venue(event: Dict[str, Any]) -> str:
    venue = (
        event.get("venue")
        or event.get("venue_name")
        or ((event.get("_embedded", {}) or {}).get("venues", [{}])[0].get("name"))
    )
    if isinstance(venue, dict):
        venue = venue.get("name")
    return (venue or "").strip() or "Unknown Venue"

def _extract_capacity(event: Dict[str, Any]) -> int:
    # Best-effort: you may store this or not; fallback to 0.
    cap = event.get("capacity") or event.get("venue_capacity")
    return _safe_int(cap, 0)

def _extract_date_unix(event: Dict[str, Any]) -> Optional[int]:
    # If you already store unix
    if "date_unix" in event:
        return _safe_int(event.get("date_unix"), 0) or None
    # Ticketmaster-like: dates.start.dateTime (ISO)
    dt = (((event.get("dates", {}) or {}).get("start", {}) or {}).get("dateTime"))
    if not dt:
        return None
    # parse ISO quickly
    try:
        # "2026-05-01T00:00:00Z"
        import datetime
        dt2 = datetime.datetime.fromisoformat(dt.replace("Z", "+00:00"))
        return int(dt2.timestamp())
    except Exception:
        return None

def _event_link(event: Dict[str, Any]) -> str:
    return (event.get("url") or event.get("ticket_url") or "").strip()

def _presale_link(event: Dict[str, Any]) -> str:
    # If your scraper adds it
    return (event.get("presale_url") or event.get("signup_url") or "").strip()

def compute_artist_score01(spotify: Dict[str, Any], yt: Dict[str, Any], tt: Dict[str, Any]) -> Tuple[float, Dict[str, float]]:
    """
    Produces overall 0..1 score + breakdown.
    Uses whatever fields exist; ignores missing.
    """
    # Spotify
    monthly = _safe_float(spotify.get("monthly_listeners"), 0.0)
    popularity = _safe_float(spotify.get("popularity"), 0.0)  # 0..100 typical
    followers = _safe_float(spotify.get("followers"), 0.0)

    spotify_score = (
        0.55 * _log1p_scale(monthly, 20_000_000) +
        0.30 * _clamp(popularity / 100.0, 0.0, 1.0) +
        0.15 * _log1p_scale(followers, 10_000_000)
    )

    # YouTube
    views = _safe_float(yt.get("total_views") or yt.get("views"), 0.0)
    subs = _safe_float(yt.get("subscribers") or yt.get("subs"), 0.0)
    yt_score = 0.65 * _log1p_scale(views, 2_000_000_000) + 0.35 * _log1p_scale(subs, 20_000_000)

    # TikTok
    followers_tt = _safe_float(tt.get("followers"), 0.0)
    likes_tt = _safe_float(tt.get("likes"), 0.0)
    velocity = _safe_float(tt.get("velocity") or tt.get("trend_velocity") or tt.get("growth"), 0.0)  # 0..?? optional
    tt_score = 0.55 * _log1p_scale(followers_tt, 20_000_000) + 0.35 * _log1p_scale(likes_tt, 2_000_000_000) + 0.10 * _clamp(velocity, 0.0, 1.0)

    # Weight based on availability
    parts: List[Tuple[str, float]] = []
    if spotify.get("ok"):
        parts.append(("spotify", spotify_score))
    if yt.get("ok"):
        parts.append(("youtube", yt_score))
    if tt.get("ok"):
        parts.append(("tiktok", tt_score))

    if not parts:
        return 0.35, {"spotify": 0.0, "youtube": 0.0, "tiktok": 0.0}  # default baseline

    # Normalize weights (spotify tends to be most reliable)
    weights = {"spotify": 0.45, "youtube": 0.30, "tiktok": 0.25}
    wsum = sum(weights.get(k, 0.0) for k, _ in parts) or 1.0
    score = sum(weights.get(k, 0.0) * v for k, v in parts) / wsum

    breakdown = {"spotify": spotify_score, "youtube": yt_score, "tiktok": tt_score}
    return _clamp(score, 0.0, 1.0), breakdown

def rank_cities(events: List[Dict[str, Any]], artist_score01: float) -> List[Tuple[str, float]]:
    """
    Simple market heat: cities with more events + nearer dates get slight bump.
    """
    now = int(time.time())
    city_scores: Dict[str, float] = {}
    for ev in events:
        city = _extract_city(ev)
        ts = _extract_date_unix(ev)
        days_until = None
        if ts:
            days_until = max(0, int((ts - now) / 86400))
        near_boost = 0.0 if days_until is None else _clamp((60 - days_until) / 120.0, -0.10, 0.15)

        base = 0.60 * artist_score01 + 0.40 * (0.50 + near_boost)
        city_scores[city] = city_scores.get(city, 0.0) + base

    ranked = sorted(city_scores.items(), key=lambda x: x[1], reverse=True)
    return ranked[:8]


# ----------------------------
# Public API: run intel
# ----------------------------

def run_artist_intel(artist: str) -> Dict[str, Any]:
    artist = (artist or "").strip()
    if not artist:
        return {"ok": False, "error": "Missing artist name."}

    spotify = fetch_spotify_signals(artist)
    yt = fetch_youtube_signals(artist)
    tt = fetch_tiktok_signals(artist)

    artist_score01, breakdown = compute_artist_score01(spotify, yt, tt)
    stars = _star_rating(artist_score01)

    events = fetch_ticketmaster_events(artist, limit=12)
    news = fetch_tour_news(artist)

    now = int(time.time())
    enriched_events: List[Dict[str, Any]] = []
    for ev in events:
        cap = _extract_capacity(ev)
        ts = _extract_date_unix(ev)
        days_until = None
        if ts:
            days_until = max(0, int((ts - now) / 86400))

        # market boost: if many events in same city => slightly higher demand
        city = _extract_city(ev)
        market_boost01 = 0.0
        # later filled by city rank density
        p = _sellout_probability(artist_score01, cap, days_until=days_until, market_boost01=market_boost01)

        enriched_events.append({
            "name": ev.get("name") or f"{artist} Live",
            "date_unix": ts,
            "city": city,
            "state": _extract_state(ev),
            "venue": _extract_venue(ev),
            "capacity": cap,
            "ticket_url": _event_link(ev),
            "presale_url": _presale_link(ev),
            "sellout_probability": p,
            "raw": ev,  # keep for debugging
        })

    # Now compute market boost from city density & rerun probabilities
    city_rank = rank_cities(events, artist_score01)
    city_to_boost: Dict[str, float] = {}
    if city_rank:
        top = city_rank[0][1]
        for city, score in city_rank:
            # normalize relative to top
            city_to_boost[city] = _clamp(score / (top or 1.0), 0.0, 1.0)

    for ev in enriched_events:
        cap = _safe_int(ev.get("capacity"), 0)
        ts = ev.get("date_unix")
        days_until = None
        if ts:
            days_until = max(0, int((int(ts) - now) / 86400))
        boost = city_to_boost.get(ev.get("city") or "Unknown", 0.0)
        ev["sellout_probability"] = _sellout_probability(artist_score01, cap, days_until=days_until, market_boost01=boost)

    enriched_events.sort(key=lambda e: (e.get("sellout_probability", 0), e.get("date_unix") or 10**18), reverse=True)

    return {
        "ok": True,
        "artist": artist,
        "stars": stars,
        "artist_score01": round(artist_score01, 4),
        "score_breakdown": {k: round(v, 4) for k, v in breakdown.items()},
        "spotify": spotify,
        "youtube": yt,
        "tiktok": tt,
        "top_cities": [{"city": c, "score": round(s, 4)} for c, s in city_rank],
        "events": enriched_events[:12],
        "tour_news": news,
        "generated_unix": now,
    }
