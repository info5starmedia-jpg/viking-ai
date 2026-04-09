"""
apify_client.py
Viking AI — Apify REST API wrapper for ticket/event scraping.

Used as a secondary data source / fallback when primary APIs
(Ticketmaster, SeatGeek, StubHub) are rate-limited or blocked.

Relevant Apify actors:
  misceres/ticketmaster-scraper   — TM event search & listing pages
  dtrungtin/stubhub-scraper       — StubHub event price scraping
  epctex/seatgeek-scraper         — SeatGeek event price scraping
  epctex/vivid-seats-scraper      — VividSeats event price scraping

Apify run model:
  Synchronous run (< 5 min):
    POST /v2/acts/{actorId}/run-sync-get-dataset-items?token=...
    Body: actor input JSON
    Returns: dataset items array directly

  Asynchronous run (long jobs):
    POST /v2/acts/{actorId}/runs?token=...
    GET  /v2/actor-runs/{runId}?token=...  (poll until finished)
    GET  /v2/datasets/{datasetId}/items?token=...

Env vars:
  APIFY_API_TOKEN   — required for any Apify calls
  APIFY_TIMEOUT_S   — synchronous run timeout in seconds (default 120)
"""
from __future__ import annotations

import logging
import os
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger("apify_client")

APIFY_BASE        = "https://api.apify.com/v2"
APIFY_API_TOKEN   = os.getenv("APIFY_API_TOKEN", "")
APIFY_TIMEOUT_S   = int(os.getenv("APIFY_TIMEOUT_S", "120") or "120")

# Actor IDs for each platform
ACTOR_TM        = "misceres/ticketmaster-scraper"
ACTOR_STUBHUB   = "dtrungtin/stubhub-scraper"
ACTOR_SEATGEEK  = "epctex/seatgeek-scraper"
ACTOR_VIVIDSEATS= "epctex/vivid-seats-scraper"

# ---------------------------------------------------------------------------
# Optional HTTP dep
# ---------------------------------------------------------------------------

try:
    from curl_cffi import requests as _http  # type: ignore
    _HTTP_IMPL = "curl_cffi"
except Exception:
    try:
        import requests as _http  # type: ignore
        _HTTP_IMPL = "requests"
    except Exception:
        _http = None  # type: ignore
        _HTTP_IMPL = "none"


def is_available() -> bool:
    """True if APIFY_API_TOKEN is set and an HTTP lib is available."""
    return bool(APIFY_API_TOKEN and _http is not None)


# ---------------------------------------------------------------------------
# Core HTTP helpers
# ---------------------------------------------------------------------------

def _headers() -> Dict[str, str]:
    return {
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def _run_sync(actor_id: str, actor_input: Dict[str, Any],
              max_items: int = 10, timeout: int = APIFY_TIMEOUT_S) -> List[Dict[str, Any]]:
    """
    Run an Apify actor synchronously and return dataset items.
    Blocks until the run completes (or timeout_secs is reached).
    Returns list of result dicts, or [] on any failure.
    """
    if not is_available():
        logger.debug("apify_client: not available (no token or no http lib)")
        return []

    url = (
        f"{APIFY_BASE}/acts/{actor_id}/run-sync-get-dataset-items"
        f"?token={APIFY_API_TOKEN}&maxItems={max_items}&timeout={timeout}"
    )
    try:
        kwargs: Dict[str, Any] = dict(
            json=actor_input,
            headers=_headers(),
            timeout=timeout + 10,
        )
        if _HTTP_IMPL == "curl_cffi":
            kwargs["impersonate"] = "chrome124"
        resp = _http.post(url, **kwargs)  # type: ignore[union-attr]
        if resp.status_code == 402:
            logger.warning("apify_client: account limit reached (402) for actor %s", actor_id)
            return []
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, list):
            return data
        # Some actors wrap in {"items": [...]}
        if isinstance(data, dict):
            return data.get("items") or []
        return []
    except Exception as e:
        logger.warning("apify_client: run_sync failed actor=%s: %s", actor_id, e)
        return []


# ---------------------------------------------------------------------------
# Ticketmaster scraper
# ---------------------------------------------------------------------------

def search_tm_events(
    keyword: str = "",
    url: str = "",
    max_items: int = 20,
) -> List[Dict[str, Any]]:
    """
    Search Ticketmaster via Apify scraper.

    Pass either a keyword (artist name / event) or a full TM URL.
    Returns a list of raw event dicts normalizable by drop_catcher._normalize_event.
    """
    if not keyword and not url:
        return []

    actor_input: Dict[str, Any] = {"maxItems": max_items}
    if url:
        actor_input["startUrls"] = [{"url": url}]
    else:
        # Build a TM search URL from keyword
        import urllib.parse
        q = urllib.parse.quote_plus(keyword)
        actor_input["startUrls"] = [
            {"url": f"https://www.ticketmaster.com/search?q={q}&type=event"}
        ]

    items = _run_sync(ACTOR_TM, actor_input, max_items=max_items)
    logger.info("apify_client: TM scrape returned %d items for %r", len(items), keyword or url)
    return items


def get_tm_event(event_id: str) -> Optional[Dict[str, Any]]:
    """
    Fetch a single TM event by ID via Apify scraper.
    Returns raw event dict or None.
    """
    url = f"https://www.ticketmaster.com/event/{event_id}"
    items = search_tm_events(url=url, max_items=1)
    return items[0] if items else None


# ---------------------------------------------------------------------------
# StubHub scraper
# ---------------------------------------------------------------------------

def get_stubhub_price(url: str, max_items: int = 5) -> Optional[Dict[str, Any]]:
    """
    Scrape a StubHub event page via Apify.
    Returns first result dict with price fields, or None.
    """
    actor_input = {
        "startUrls": [{"url": url}],
        "maxItems": max_items,
    }
    items = _run_sync(ACTOR_STUBHUB, actor_input, max_items=max_items)
    return items[0] if items else None


# ---------------------------------------------------------------------------
# SeatGeek scraper
# ---------------------------------------------------------------------------

def get_seatgeek_price(url: str, max_items: int = 5) -> Optional[Dict[str, Any]]:
    """
    Scrape a SeatGeek event page via Apify.
    Returns first result dict with price fields, or None.
    """
    actor_input = {
        "startUrls": [{"url": url}],
        "maxItems": max_items,
    }
    items = _run_sync(ACTOR_SEATGEEK, actor_input, max_items=max_items)
    return items[0] if items else None


# ---------------------------------------------------------------------------
# VividSeats scraper
# ---------------------------------------------------------------------------

def get_vividseats_price(url: str, max_items: int = 5) -> Optional[Dict[str, Any]]:
    """
    Scrape a VividSeats event page via Apify.
    Returns first result dict with price fields, or None.
    """
    actor_input = {
        "startUrls": [{"url": url}],
        "maxItems": max_items,
    }
    items = _run_sync(ACTOR_VIVIDSEATS, actor_input, max_items=max_items)
    return items[0] if items else None


# ---------------------------------------------------------------------------
# Price extraction helper (normalizes across actor output schemas)
# ---------------------------------------------------------------------------

def extract_price_from_result(item: Dict[str, Any]) -> Optional[float]:
    """
    Pull the lowest price out of an Apify scraper result regardless of
    which actor produced it. Tries common key names in order.
    """
    for key in (
        "lowestPrice", "lowest_price", "minPrice", "min_price",
        "priceMin", "price_min", "startingFrom", "starting_from",
        "price", "listPrice", "ticketPrice",
    ):
        val = item.get(key)
        if val is not None:
            try:
                f = float(str(val).replace("$", "").replace(",", "").strip())
                if f > 0:
                    return f
            except Exception:
                pass

    # Nested: {"priceRange": {"min": 50}}
    for nested_key in ("priceRange", "price_range", "prices"):
        nested = item.get(nested_key)
        if isinstance(nested, dict):
            for k in ("min", "low", "lowest", "from"):
                val = nested.get(k)
                if val is not None:
                    try:
                        f = float(str(val).replace("$", "").replace(",", "").strip())
                        if f > 0:
                            return f
                    except Exception:
                        pass
    return None


def extract_name_from_result(item: Dict[str, Any]) -> str:
    """Pull event name from an Apify scraper result."""
    for key in ("name", "title", "eventName", "event_name", "eventTitle"):
        val = item.get(key)
        if val and isinstance(val, str):
            return val.strip()[:120]
    return "Event"
