"""
Viking AI – Ticketmaster Discovery API helper.

Provides:
  - search_events(keyword: str, size: int = 10) -> dict
  - get_event_details(event_id: str) -> dict
  - search_venues(keyword: str, size: int = 10) -> dict
  - search_attractions(keyword: str, size: int = 10) -> dict

Plus Viking-AI specific helpers expected by bot.py:
  - search_events_for_artist(artist: str, size: int = 10) -> list[dict]
  - summarize_tomorrow_onsales() -> str
  - import_tomorrow_onsales_csv(csv_text: str) -> str

Extra:
  - fetch_verified_fan_programs() -> list[dict]
    (Uses Tavily search to detect Verified Fan / presale registration links)

All functions expect:
  - TICKETMASTER_API_KEY (for Discovery API)
  - TAVILY_API_KEY (for Verified Fan scanning)
"""

from __future__ import annotations

import os
import csv
import io
import re
import hashlib
from typing import Dict, Any, List

import requests
from dotenv import load_dotenv

load_dotenv()

TM_API_KEY = os.getenv("TICKETMASTER_API_KEY")
TM_BASE = "https://app.ticketmaster.com/discovery/v2"

TAVILY_API_KEY = os.getenv("TAVILY_API_KEY")


class TicketmasterError(RuntimeError):
    pass


def _check_api_key():
    if not TM_API_KEY:
        raise TicketmasterError("Missing TICKETMASTER_API_KEY in environment / .env")


def _get(path: str, params: Dict[str, Any]) -> Dict[str, Any]:
    _check_api_key()
    full_params = dict(params or {})
    full_params["apikey"] = TM_API_KEY

    url = f"{TM_BASE}{path}"
    resp = requests.get(url, params=full_params, timeout=20)
    resp.raise_for_status()
    return resp.json()


# -------------------------------------------------------------------
# Core events helpers
# -------------------------------------------------------------------

def search_events(keyword: str, size: int = 10) -> Dict[str, Any]:
    params: Dict[str, Any] = {"size": size, "sort": "date,asc", "keyword": keyword}
    data = _get("/events.json", params)
    return (data.get("_embedded", {}) or {})


def get_event_details(event_id: str) -> Dict[str, Any]:
    data = _get(f"/events/{event_id}.json", {})
    ven = (data.get("_embedded", {}).get("venues", [{}])[0] or {})

    dates = (data.get("dates", {}) or {})
    start = (dates.get("start", {}) or {})

    segment = ""
    genre = ""
    if data.get("classifications"):
        c0 = (data["classifications"][0] or {})
        segment = (c0.get("segment") or {}).get("name", "") or ""
        genre = (c0.get("genre") or {}).get("name", "") or ""

    return {
        "id": data.get("id"),
        "name": data.get("name"),
        "url": data.get("url"),
        "date": start.get("localDate"),
        "time": start.get("localTime"),
        "venue": ven.get("name"),
        "address": (ven.get("address") or {}).get("line1"),
        "city": (ven.get("city") or {}).get("name"),
        "country": (ven.get("country") or {}).get("name"),
        "segment": segment,
        "genre": genre,
        "price_ranges": data.get("priceRanges", []),
        "promoter": (data.get("promoter") or {}).get("name"),
        "pleaseNote": data.get("pleaseNote"),
    }


def search_venues(keyword: str, size: int = 10) -> Dict[str, Any]:
    data = _get("/venues.json", {"keyword": keyword, "size": size})
    return (data.get("_embedded", {}) or {})


def search_attractions(keyword: str, size: int = 10) -> Dict[str, Any]:
    data = _get("/attractions.json", {"keyword": keyword, "size": size})
    return (data.get("_embedded", {}) or {})


# -------------------------------------------------------------------
# Viking AI-specific helpers expected by bot.py
# -------------------------------------------------------------------

def search_events_for_artist(artist: str, size: int = 10) -> List[Dict[str, Any]]:
    embedded = search_events(artist, size=size)
    events = embedded.get("events", []) or []

    normalized: List[Dict[str, Any]] = []
    for ev in events:
        venues = (ev.get("_embedded", {}) or {}).get("venues", []) or [{}]
        ven = venues[0] or {}
        dates = (ev.get("dates", {}) or {})
        start = (dates.get("start", {}) or {})

        normalized.append(
            {
                "id": ev.get("id"),
                "name": ev.get("name"),
                "url": ev.get("url"),
                "date": start.get("localDate"),
                "time": start.get("localTime"),
                "venue": ven.get("name"),
                "city": (ven.get("city") or {}).get("name"),
            }
        )
    return normalized


def summarize_tomorrow_onsales() -> str:
    return (
        "📅 Tomorrow on-sale summary is not fully implemented in this Ticketmaster agent.\n"
        "You can extend summarize_tomorrow_onsales() to fetch/summarize real on-sale data."
    )


def import_tomorrow_onsales_csv(csv_text: str) -> str:
    if not csv_text or not csv_text.strip():
        return "❌ Empty CSV."

    reader = csv.DictReader(io.StringIO(csv_text))
    rows = list(reader)
    if not rows:
        return "❌ No rows detected."

    return f"✅ Imported {len(rows)} rows (placeholder)."


# -------------------------------------------------------------------
# Verified Fan / Presale scanning (Tavily-based)
# -------------------------------------------------------------------

def fetch_verified_fan_programs(max_results: int = 12) -> List[Dict[str, Any]]:
    """
    Returns list of dicts:
      { id, artist, event, url }
    This is a best-effort signal scanner (Ticketmaster does not expose
    a simple Discovery endpoint for Verified Fan programs).
    """
    if not TAVILY_API_KEY:
        return []

    query = (
        "site:ticketmaster.com (\"Verified Fan\" OR \"verified fan\" OR presale OR registration) "
        "(tickets OR tour OR concert) -jobs -careers"
    )

    payload = {
        "api_key": TAVILY_API_KEY,
        "query": query,
        "search_depth": "advanced",
        "max_results": max_results,
    }

    try:
        r = requests.post("https://api.tavily.com/search", json=payload, timeout=25)
        r.raise_for_status()
        data = r.json()
    except Exception:
        return []

    raw = data.get("results", []) or []
    programs: List[Dict[str, Any]] = []

    for item in raw:
        title = (item.get("title") or "").strip()
        url = (item.get("url") or "").strip()
        content = (item.get("content") or "").strip()

        if not url:
            continue

        # Heuristic filters: keep only likely presale/verified fan/registration pages
        text = (title + " " + content).lower()
        if not any(k in text for k in ["verified fan", "presale", "registration", "register"]):
            continue

        # Guess artist/event from title
        # Example: "Jack White Verified Fan Presale FAQ"
        artist_guess = title.split(" Verified Fan")[0].strip() if "Verified Fan" in title else title.split(" presale")[0].strip()
        artist_guess = re.sub(r"\s*-\s*Ticketmaster.*$", "", artist_guess).strip()

        program_id = hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]

        programs.append(
            {
                "id": program_id,
                "artist": artist_guess or "—",
                "event": title or "—",
                "url": url,
            }
        )

    # De-dupe by url/id
    seen = set()
    deduped: List[Dict[str, Any]] = []
    for p in programs:
        if p["id"] in seen:
            continue
        seen.add(p["id"])
        deduped.append(p)

    return deduped
