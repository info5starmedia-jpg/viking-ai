# agents/tm_live_inventory.py
# ------------------------------------------------------------
# Live Ticketmaster inventory scanner for Viking AI
# ------------------------------------------------------------

import os
import httpx
from typing import Dict, Any, List, Optional

TM_KEY = os.getenv("TICKETMASTER_API_KEY")
BROWSERLESS_KEY = os.getenv("BROWSERLESS_API_KEY")


async def _fetch_tm_event(event_id: str) -> Dict[str, Any]:
    """Fetch raw Ticketmaster event JSON."""
    if not TM_KEY:
        raise RuntimeError("TICKETMASTER_API_KEY missing in environment.")

    url = f"https://app.ticketmaster.com/discovery/v2/events/{event_id}.json"
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.get(url, params={"apikey": TM_KEY})
        r.raise_for_status()
        return r.json()


async def _scrape_seatmap_via_browserless(event_url: str) -> Optional[Dict[str, Any]]:
    """
    Uses Browserless to extract the __NEXT_DATA__ seatmap JSON (if configured).
    Returns raw seatmap dict or None.

    If BROWSERLESS_API_KEY is not set, this will just return None and the
    rest of the Touring OS will still function (it will fall back to no seatmap).
    """
    if not BROWSERLESS_KEY:
        # No Browserless configured – skip seatmap scraping
        return None

    payload = {
        "url": event_url,
        "elements": [
            {
                "selector": "script#__NEXT_DATA__",
                "attribute": "innerText",
            }
        ],
    }

    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(
            f"https://chrome.browserless.io/content?token={BROWSERLESS_KEY}",
            json=payload,
        )
        r.raise_for_status()
        data = r.json()

    try:
        text = data[0]["results"][0]["value"]
        import json
        root = json.loads(text)
        seatmap = root["props"]["pageProps"].get("seatMap", {})
        return seatmap
    except Exception:
        # If any parsing error, just return None so the caller can degrade gracefully
        return None


async def get_live_seatmap(event_id: str) -> Dict[str, Any]:
    """
    High-level helper:
      - Fetch TM event
      - Extract URL
      - Scrape seatmap if possible (Browserless)
    Returns:
      { "event": <event_json>, "seatmap": <seatmap_json> } or an error dict.
    """
    try:
        event_data = await _fetch_tm_event(event_id)
    except Exception as e:
        return {"error": f"tm_fetch_failed: {e}"}

    event_url = event_data.get("url")
    if not event_url:
        return {"error": "no_event_url", "event": event_data}

    seatmap = await _scrape_seatmap_via_browserless(event_url)
    if seatmap is None:
        return {"error": "seatmap_scrape_failed", "event": event_data, "event_url": event_url}

    return {"event": event_data, "seatmap": seatmap}


async def summarize_inventory(seatmap: Dict[str, Any]) -> Dict[str, Any]:
    """
    Takes a raw seatmap JSON and returns a simple summary:
      - total_seats
      - available_seats
      - sections: [{ section, price, available, total, heat }, ...]

    This is a generic heuristic because TM seatmap structure can vary.
    It assumes seatmap["sections"] is present if scraping succeeded.
    """
    sections_raw: List[Dict[str, Any]] = seatmap.get("sections", [])
    sections: List[Dict[str, Any]] = []
    total = 0
    available = 0

    for section in sections_raw:
        name = section.get("name", "Unknown")
        price = section.get("price", 0)
        avail = section.get("available", 0)
        total_seats = section.get("total", avail)

        total += total_seats
        available += avail

        # Very simple heat: more sold at higher price = higher heat
        heat = 0.0
        if total_seats > 0:
            sold = total_seats - avail
            sell_rate = sold / total_seats
            heat = round(sell_rate * (price or 1) / 10, 3)

        sections.append({
            "section": name,
            "price": price,
            "available": avail,
            "total": total_seats,
            "heat": heat,
        })

    sections_sorted = sorted(sections, key=lambda s: s["heat"], reverse=True)

    return {
        "total_seats": total,
        "available_seats": available,
        "sections": sections_sorted,
    }
