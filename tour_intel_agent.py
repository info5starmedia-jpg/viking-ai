"""
tour_intel_agent.py

Builds a structured "tour demand intel" report for an artist.

Used by: /intel command in bot.py

Features:
- Uses Ticketmaster Discovery API (segmentName=Music) to gauge upcoming concerts.
- Uses Tavily / tour_news_agent for news & buzz signals.
- Optionally uses streaming metrics (if you later wire up streaming_metrics.py).
- Computes a 1–5 star rating with labels like:
    ⭐⭐⭐⭐⭐ + 🔥 "S-tier touring cycle"
    ⭐⭐     "Emerging / low current cycle"
- Returns a nicely formatted Markdown report with sections.
"""

import os
import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, Any, List, Optional

import requests

from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger("tour_intel_agent")

# ---------------------------------------------------------------------------
# Optional imports
# ---------------------------------------------------------------------------

# Orchestrator for nice natural-language summary (Gemini / OpenRouter stack)
try:
    from orchestrator_agent import orchestrate_query  # type: ignore
except ImportError:
    orchestrate_query = None  # type: ignore

# Tavily for fallback news if tour_news_agent not available
try:
    from tavily_agent import tavily_search  # type: ignore
except ImportError:
    tavily_search = None  # type: ignore

# Tour news agent (if present)
try:
    from tour_news_agent import search_tour_news  # type: ignore
except ImportError:
    search_tour_news = None  # type: ignore

# Optional streaming metrics module (you can implement later)
try:
    import streaming_metrics  # type: ignore
except ImportError:
    streaming_metrics = None  # type: ignore

# ---------------------------------------------------------------------------
# Ticketmaster basics
# ---------------------------------------------------------------------------

TM_API_KEY = os.getenv("TICKETMASTER_API_KEY")
TM_BASE = "https://app.ticketmaster.com/discovery/v2/events.json"
SEGMENT_NAME = "Music"  # Ticketmaster's "concerts" segment


def _tm_get(params: Dict[str, Any]) -> Dict[str, Any]:
    if not TM_API_KEY:
        raise RuntimeError("Missing TICKETMASTER_API_KEY in environment / .env")
    q = dict(params)
    q["apikey"] = TM_API_KEY
    resp = requests.get(TM_BASE, params=q, timeout=20)
    resp.raise_for_status()
    return resp.json()


def _tm_search_concerts(
    artist: str, country_codes: Optional[List[str]] = None, size: int = 100
) -> List[Dict[str, Any]]:
    """
    Search Ticketmaster for concert events for this artist.

    Filters:
      - segmentName=Music
      - countryCode = comma-separated list if provided
    """
    params: Dict[str, Any] = {
        "keyword": artist,
        "segmentName": SEGMENT_NAME,
        "size": min(size, 200),
        "sort": "date,asc",
    }
    if country_codes:
        params["countryCode"] = ",".join(country_codes)

    try:
        data = _tm_get(params)
    except Exception as e:
        logger.warning("Ticketmaster search failed for %s: %s", artist, e)
        return []

    events = (data.get("_embedded") or {}).get("events", []) or []
    return events


def _normalize_tm_event(e: Dict[str, Any]) -> Dict[str, Any]:
    """Flatten an event into a simple dict for intel."""
    dates = e.get("dates", {}) or {}
    start = dates.get("start", {}) or {}
    local_date = start.get("localDate")
    local_time = start.get("localTime")

    venues = (e.get("_embedded", {}) or {}).get("venues", []) or [{}]
    v0 = venues[0] or {}
    city = (v0.get("city") or {}).get("name")
    country = (v0.get("country") or {}).get("name")
    venue_name = v0.get("name")

    cl = (e.get("classifications") or [{}])[0] or {}
    segment = (cl.get("segment") or {}).get("name")
    genre = (cl.get("genre") or {}).get("name")

    atts = (e.get("_embedded", {}) or {}).get("attractions", []) or []
    artist_name = atts[0].get("name") if atts else None

    return {
        "id": e.get("id"),
        "name": e.get("name"),
        "artist": artist_name,
        "local_date": local_date,
        "local_time": local_time,
        "url": e.get("url"),
        "venue": venue_name,
        "city": city,
        "country": country,
        "segment": segment,
        "genre": genre,
    }


# ---------------------------------------------------------------------------
# Region mapping helper
# ---------------------------------------------------------------------------

def _region_to_country_codes(region: str) -> Optional[List[str]]:
    """
    Map human region labels to Ticketmaster country codes.
    If None is returned, we don't filter by country at all.
    """
    r = (region or "").strip().upper()
    if r in ("NA", "NORTH AMERICA"):
        return ["US", "CA"]
    if r in ("US", "USA", "UNITED STATES"):
        return ["US"]
    if r in ("CA", "CANADA"):
        return ["CA"]
    if r in ("UK", "GB", "UNITED KINGDOM"):
        return ["GB"]
    if r in ("IE", "IRELAND"):
        return ["IE"]
    if r in ("UKIE", "UK+IE"):
        return ["GB", "IE"]
    if r in ("EU", "EUROPE"):
        # Ticketmaster doesn't cover all of EU equally; keep it small
        return ["GB", "IE", "DE", "FR", "NL", "SE"]
    if r in ("GLOBAL", "WORLD", "WW"):
        return None
    # Fallback: NA
    return ["US", "CA"]


def _region_label(region: str) -> str:
    r = (region or "").strip().upper()
    mapping = {
        "NA": "North America (US/CA)",
        "NORTH AMERICA": "North America (US/CA)",
        "US": "United States",
        "USA": "United States",
        "UNITED STATES": "United States",
        "CA": "Canada",
        "CANADA": "Canada",
        "UK": "United Kingdom",
        "GB": "United Kingdom",
        "UNITED KINGDOM": "United Kingdom",
        "IE": "Ireland",
        "IRELAND": "Ireland",
        "UKIE": "UK & Ireland",
        "UK+IE": "UK & Ireland",
        "EU": "Europe (Ticketmaster markets)",
        "EUROPE": "Europe (Ticketmaster markets)",
        "GLOBAL": "Global",
        "WORLD": "Global",
        "WW": "Global",
    }
    return mapping.get(r, "North America (US/CA)")


# ---------------------------------------------------------------------------
# News & streaming helpers
# ---------------------------------------------------------------------------

def _fetch_news_hits(artist: str, region: str) -> List[Dict[str, Any]]:
    """
    Get news hits about tour announcements / tickets.

    Prefers tour_news_agent.search_tour_news, falls back to Tavily search.
    """
    if search_tour_news:
        try:
            return search_tour_news(artist, regions=region)
        except Exception as e:
            logger.warning("tour_news_agent.search_tour_news failed: %s", e)

    if tavily_search:
        try:
            q = f'"{artist}" tour OR tickets OR presale OR on sale'
            results = tavily_search(q, max_results=8)
            return results
        except Exception as e:
            logger.warning("tavily_search failed in news fallback: %s", e)

    return []


def _fetch_streaming_metrics(artist: str) -> Dict[str, Any]:
    """
    Optional hook: if you implement streaming_metrics.py with a function like:

        def get_artist_metrics(name: str) -> dict:
            return {
                "spotify_monthly_listeners": int,
                "youtube_monthly_listeners": int,
            }

    Then we'll use that here. Otherwise we gracefully return {}.
    """
    if not streaming_metrics:
        return {}

    get_metrics = getattr(streaming_metrics, "get_artist_metrics", None)
    if not callable(get_metrics):
        return {}

    try:
        return get_metrics(artist)
    except Exception as e:
        logger.warning("streaming_metrics.get_artist_metrics failed: %s", e)
        return {}


# ---------------------------------------------------------------------------
# Rating logic
# ---------------------------------------------------------------------------

def _score_from_streaming(metrics: Dict[str, Any]) -> int:
    """
    Simple 0–10 score from streaming footprint.
    Tuned for Spotify monthly listeners, with optional YouTube.
    """
    if not metrics:
        return 0

    spotify_ml = metrics.get("spotify_monthly_listeners") or 0
    youtube_ml = metrics.get("youtube_monthly_listeners") or 0

    # Use the max as a rough proxy
    reach = max(spotify_ml, youtube_ml)

    if reach >= 20_000_000:
        return 10
    if reach >= 5_000_000:
        return 8
    if reach >= 1_000_000:
        return 6
    if reach >= 250_000:
        return 4
    if reach > 0:
        return 2
    return 0


def _compute_rating(
    upcoming_total: int,
    upcoming_30d: int,
    news_hits_count: int,
    streaming_metrics: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Combine signals into a 0–100 score, then map to 1–5 stars.

    Rough weighting:
      - Upcoming concerts total: up to 40 pts
      - Next 30 days shows: up to 30 pts
      - News hits: up to 20 pts
      - Streaming: up to 10 pts
    """
    # Upcoming total: saturate at 40
    score_upcoming = min(upcoming_total * 2, 40)  # each show ~2 pts

    # Next 30 days: saturate at 30
    score_next30 = min(upcoming_30d * 3, 30)

    # News: saturate at 20 (each hit ~3 pts)
    score_news = min(news_hits_count * 3, 20)

    # Streaming: 0–10 from helper
    score_streaming = _score_from_streaming(streaming_metrics)

    total_score = score_upcoming + score_next30 + score_news + score_streaming

    # Map to stars & labels
    if total_score >= 80:
        stars = 5
        label = "S-tier touring cycle 🔥"
    elif total_score >= 65:
        stars = 4
        label = "Strong / A-tier cycle"
    elif total_score >= 50:
        stars = 3
        label = "Solid / B-tier cycle"
    elif total_score >= 30:
        stars = 2
        label = "Emerging / low current cycle"
    else:
        stars = 1
        label = "Very early / niche cycle"

    return {
        "score": total_score,
        "stars": stars,
        "label": label,
        "score_breakdown": {
            "upcoming_total": score_upcoming,
            "upcoming_30d": score_next30,
            "news": score_news,
            "streaming": score_streaming,
        },
    }


def _stars_emoji(stars: int) -> str:
    full = "⭐" * max(0, min(stars, 5))
    empty = "☆" * (5 - len(full))
    return full + empty


# ---------------------------------------------------------------------------
# Public entrypoint: build_tour_intel
# ---------------------------------------------------------------------------

def build_tour_intel(artist: str, region: str = "NA") -> str:
    """
    Main entrypoint used by /intel.

    Returns a Markdown string with:
      - Header + stars
      - Ticketmaster section
      - News section
      - Streaming section
      - AI-written summary (if orchestrator available)
    """
    artist = artist.strip()
    region_codes = _region_to_country_codes(region)
    region_desc = _region_label(region)

    # 1) Ticketmaster concerts
    tm_events_raw = _tm_search_concerts(artist, country_codes=region_codes, size=100)
    tm_events = [_normalize_tm_event(e) for e in tm_events_raw]

    now = datetime.now(timezone.utc).date()
    cutoff_30 = now + timedelta(days=30)

    upcoming_total = len(tm_events)
    upcoming_30d = 0
    city_counter: Dict[str, int] = {}
    example_events: List[Dict[str, Any]] = []

    for ev in tm_events:
        ld = ev.get("local_date")
        if ld:
            try:
                d = datetime.strptime(ld, "%Y-%m-%d").date()
            except Exception:
                d = None
        else:
            d = None

        if d and d >= now:
            # upcoming
            if not example_events:
                example_events.append(ev)
            if d <= cutoff_30:
                upcoming_30d += 1

        city = ev.get("city")
        if city:
            city_counter[city] = city_counter.get(city, 0) + 1

    top_cities = sorted(city_counter.items(), key=lambda x: x[1], reverse=True)[:5]

    # 2) News & buzz
    news_hits = _fetch_news_hits(artist, region)
    news_hits_count = len(news_hits)

    # 3) Streaming metrics (optional)
    stream_metrics = _fetch_streaming_metrics(artist)

    # 4) Score & rating
    rating = _compute_rating(
        upcoming_total=upcoming_total,
        upcoming_30d=upcoming_30d,
        news_hits_count=news_hits_count,
        streaming_metrics=stream_metrics,
    )

    stars_text = _stars_emoji(rating["stars"])
    label = rating["label"]
    score = rating["score"]

    # 5) AI summary (optional)
    summary_block = ""
    if orchestrate_query:
        try:
            tm_points = [
                f"Upcoming concerts in {region_desc}: {upcoming_total}",
                f"Concerts in next 30 days: {upcoming_30d}",
                f"Top cities: {', '.join([c for c, _ in top_cities]) or 'N/A'}",
            ]
            news_points = [
                f"News hits found: {news_hits_count}",
            ]
            stream_points = []
            if stream_metrics:
                sm = stream_metrics
                if sm.get("spotify_monthly_listeners"):
                    stream_points.append(
                        f"Spotify monthly listeners: {sm['spotify_monthly_listeners']:,}"
                    )
                if sm.get("youtube_monthly_listeners"):
                    stream_points.append(
                        f"YouTube monthly listeners: {sm['youtube_monthly_listeners']:,}"
                    )

            prompt = (
                f"You are a concert and touring demand analyst. "
                f"Given these signals, write 3–5 bullet points describing "
                f"current touring demand for the artist '{artist}' in {region_desc}. "
                f"Focus on touring momentum, fan demand, and likely ticket sell-through.\n\n"
                f"Ticketmaster signals:\n- " + "\n- ".join(tm_points) +
                "\n\nNews & buzz:\n- " + "\n- ".join(news_points) +
                ("\n\nStreaming reach:\n- " + "\n- ".join(stream_points) if stream_points else "") +
                f"\n\nOverall numeric score: {score} (0–100). "
                f"Star rating: {rating['stars']} (1–5) labeled '{label}'."
            )

            summary = orchestrate_query(prompt, mode="auto")
            summary_block = summary.strip()
        except Exception as e:
            logger.warning("orchestrate_query failed in build_tour_intel: %s", e)
            summary_block = ""
    # If no LLM summary, we can fall back to a simple text
    if not summary_block:
        summary_lines = [
            f"- Upcoming concerts in {region_desc}: {upcoming_total}",
            f"- Shows in next 30 days: {upcoming_30d}",
            f"- News hits: {news_hits_count}",
        ]
        if stream_metrics:
            sm = stream_metrics
            if sm.get("spotify_monthly_listeners"):
                summary_lines.append(
                    f"- Spotify monthly listeners: {sm['spotify_monthly_listeners']:,}"
                )
            if sm.get("youtube_monthly_listeners"):
                summary_lines.append(
                    f"- YouTube monthly listeners: {sm['youtube_monthly_listeners']:,}"
                )
        summary_block = "\n".join(summary_lines)

    # 6) Build Markdown report
    lines: List[str] = []

    lines.append(f"🎯 **Tour Demand Intel – {artist}**")
    lines.append(f"Region focus: `{region_desc}`")
    lines.append("")
    lines.append(f"**Overall rating:** {stars_text} – {label} (score: `{score}` / 100)")
    lines.append("")

    # Section 1 – Ticketmaster / on-sale
    lines.append("### 1. Ticketmaster / On-sale signals")
    lines.append(f"- Upcoming concert events in {region_desc}: **{upcoming_total}**")
    lines.append(f"- Concerts in next 30 days: **{upcoming_30d}**")

    if top_cities:
        city_str = ", ".join([f"{c} (x{n})" for c, n in top_cities])
        lines.append(f"- Top markets by number of shows: {city_str}")
    else:
        lines.append("- Top markets: N/A")

    if example_events:
        ev = example_events[0]
        ev_line = f"- Example upcoming show: **{ev.get('name','Unknown')}** – {ev.get('local_date','TBA')} – {ev.get('city','')} – {ev.get('venue','')}"
        if ev.get("url"):
            ev_line += f"\n  {ev['url']}"
        lines.append(ev_line)

    lines.append("")

    # Section 2 – News & buzz
    lines.append("### 2. News & Buzz")
    lines.append(f"- Recent tour/ticket-related hits: **{news_hits_count}**")
    if news_hits:
        max_show = 5
        for h in news_hits[:max_show]:
            title = h.get("title", "Untitled")
            url = h.get("url", "")
            src = h.get("source", "")
            when = h.get("published", "")
            row = f"  • **{title}**"
            if src:
                row += f" ({src})"
            if when:
                row += f" – {when}"
            if url:
                row += f"\n    {url}"
            lines.append(row)
        if len(news_hits) > max_show:
            lines.append(f"  • …and {len(news_hits) - max_show} more not shown.")
    else:
        lines.append("  • No strong recent tour/ticket coverage detected (or news agent unavailable).")

    lines.append("")

    # Section 3 – Streaming reach
    lines.append("### 3. Streaming Reach (Spotify / YouTube)")
    if stream_metrics:
        sm = stream_metrics
        if sm.get("spotify_monthly_listeners"):
            lines.append(
                f"- Spotify monthly listeners: **{sm['spotify_monthly_listeners']:,}**"
            )
        else:
            lines.append("- Spotify monthly listeners: not available")

        if sm.get("youtube_monthly_listeners"):
            lines.append(
                f"- YouTube monthly listeners: **{sm['youtube_monthly_listeners']:,}**"
            )
        else:
            lines.append("- YouTube monthly listeners: not available")
    else:
        lines.append(
            "- Streaming metrics not configured yet. "
            "You can later wire up streaming_metrics.py to add Spotify/YouTube stats."
        )

    lines.append("")

    # Section 4 – Summary
    lines.append("### 4. Summary")
    lines.append(summary_block)

    return "\n".join(lines)
