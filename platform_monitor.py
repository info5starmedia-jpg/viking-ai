"""
platform_monitor.py
Viking AI Drop Catcher — Multi-platform price monitor.

Polls client monitor URLs on SeatGeek, StubHub, and VividSeats.
Fires structured alert dicts (same schema as drop_catcher) on:
  - price_drop  : lowest available price fell since last poll
  - price_spike : lowest available price rose above a threshold
  - new_event   : event/listing newly detected

Auto-detects platform from URL. Skips gracefully if API keys or
the requests library are not available.

Env vars (all optional):
  SEATGEEK_CLIENT_ID        — SeatGeek API client ID
  SEATGEEK_CLIENT_SECRET    — SeatGeek API client secret
  PLATFORM_POLL_SECONDS     — poll interval in seconds (default 300)
  PLATFORM_PRICE_DROP_PCT   — minimum % drop to fire price_drop alert (default 5)
  PLATFORM_PRICE_SPIKE_PCT  — minimum % rise to fire price_spike alert (default 20)

Usage (from bot.py):
    from platform_monitor import platform_watch_loop
    task = asyncio.create_task(
        platform_watch_loop(discord_post=callback, stop_event=stop)
    )
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import time
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger("platform_monitor")

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

POLL_SECONDS      = int(os.getenv("PLATFORM_POLL_SECONDS", "300") or "300")
PRICE_DROP_PCT    = float(os.getenv("PLATFORM_PRICE_DROP_PCT", "5") or "5")
PRICE_SPIKE_PCT   = float(os.getenv("PLATFORM_PRICE_SPIKE_PCT", "20") or "20")
SG_CLIENT_ID      = os.getenv("SEATGEEK_CLIENT_ID", "")
SG_CLIENT_SECRET  = os.getenv("SEATGEEK_CLIENT_SECRET", "")
SG_API_BASE       = "https://api.seatgeek.com/2"
SH_API_BASE       = "https://api.stubhub.com"

# ---------------------------------------------------------------------------
# Optional deps
# ---------------------------------------------------------------------------

# Prefer curl_cffi (mimics real browser TLS fingerprint / JA3, bypasses TLS fingerprinting)
# Fall back to requests if unavailable.
try:
    from curl_cffi import requests as _curl_requests  # type: ignore
    _CURL_OK = True
except Exception:
    _curl_requests = None  # type: ignore
    _CURL_OK = False

try:
    import requests as _requests  # type: ignore
    _REQUESTS_OK = True
except Exception:
    _requests = None  # type: ignore
    _REQUESTS_OK = False

_HTTP_OK = _CURL_OK or _REQUESTS_OK

# Rotate through common browser User-Agent strings to reduce fingerprinting
_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4_1) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4.1 Safari/605.1.15",
]

def _browser_headers(idx: int = 0) -> dict:
    """Return a realistic browser header set for stealth scraping."""
    ua = _USER_AGENTS[idx % len(_USER_AGENTS)]
    is_firefox = "Firefox" in ua
    return {
        "User-Agent": ua,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
        **({"sec-ch-ua": '"Chromium";v="124","Google Chrome";v="124"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"'} if not is_firefox else {}),
    }

try:
    from dashboard.db import (  # type: ignore
        get_monitors_by_platform,
        update_monitor_price,
        increment_monitor_alert_count,
    )
    _DB_OK = True
except Exception:
    try:
        import sys, os as _os
        sys.path.insert(0, _os.path.join(_os.path.dirname(__file__), "dashboard"))
        from db import (  # type: ignore
            get_monitors_by_platform,
            update_monitor_price,
            increment_monitor_alert_count,
        )
        _DB_OK = True
    except Exception as _e:
        logger.warning("platform_monitor: dashboard db import failed: %s", _e)
        _DB_OK = False


# ---------------------------------------------------------------------------
# Platform detection
# ---------------------------------------------------------------------------

def detect_platform(url: str) -> str:
    """Return platform slug from a monitor URL."""
    url_lower = url.lower()
    if "seatgeek.com" in url_lower:
        return "seatgeek"
    if "stubhub.com" in url_lower:
        return "stubhub"
    if "vividseats.com" in url_lower:
        return "vividseats"
    return "ticketmaster"


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def _build_proxy_dict(proxies: Optional[List[str]]) -> Optional[Dict]:
    """Pick one proxy from the list by time-rotation and return a proxy dict."""
    if not proxies:
        return None
    raw = proxies[int(time.time()) % len(proxies)]
    parts = raw.split(":")
    if len(parts) >= 4:
        proxy_url = f"http://{parts[2]}:{parts[3]}@{parts[0]}:{parts[1]}"
    else:
        proxy_url = f"http://{raw}" if not raw.startswith("http") else raw
    return {"http": proxy_url, "https": proxy_url}


def _get(url: str, params: Optional[Dict] = None, headers: Optional[Dict] = None,
         proxies: Optional[List[str]] = None, timeout: int = 15,
         ua_idx: int = 0) -> Optional[Dict]:
    """GET with optional proxy rotation. Returns parsed JSON or None.

    Uses curl_cffi (Chrome TLS/JA3 impersonation) when available, falls back
    to requests. Browser-realistic headers are injected automatically; callers
    may override with explicit headers= (e.g. for API auth headers).
    """
    if not _HTTP_OK:
        return None
    proxy_dict = _build_proxy_dict(proxies)
    req_headers = {**_browser_headers(ua_idx), **(headers or {})}
    try:
        if _CURL_OK and _curl_requests is not None:
            r = _curl_requests.get(
                url,
                params=params,
                headers=req_headers,
                proxies=proxy_dict,
                timeout=timeout,
                impersonate="chrome124",
            )
        else:
            r = _requests.get(url, params=params, headers=req_headers,
                              proxies=proxy_dict, timeout=timeout)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        logger.debug("platform_monitor: GET %s failed: %s", url, e)
        return None


def _get_html(url: str, proxies: Optional[List[str]] = None, timeout: int = 15,
              ua_idx: int = 0) -> Optional[str]:
    """GET HTML page with full browser-like headers and TLS impersonation."""
    if not _HTTP_OK:
        return None
    proxy_dict = _build_proxy_dict(proxies)
    req_headers = _browser_headers(ua_idx)
    try:
        if _CURL_OK and _curl_requests is not None:
            r = _curl_requests.get(
                url,
                headers=req_headers,
                proxies=proxy_dict,
                timeout=timeout,
                impersonate="chrome124",
            )
        else:
            r = _requests.get(url, headers=req_headers, proxies=proxy_dict, timeout=timeout)
        r.raise_for_status()
        return r.text
    except Exception as e:
        logger.debug("platform_monitor: HTML GET %s failed: %s", url, e)
        return None


# ---------------------------------------------------------------------------
# JSON-LD / script extraction helpers
# ---------------------------------------------------------------------------

def _extract_jsonld(html: str) -> List[Dict]:
    """Extract all JSON-LD blocks from an HTML page."""
    results = []
    for m in re.finditer(r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html, re.DOTALL | re.IGNORECASE):
        try:
            results.append(json.loads(m.group(1)))
        except Exception:
            pass
    return results


def _extract_next_data(html: str) -> Optional[Dict]:
    """Extract __NEXT_DATA__ JSON embedded in a Next.js page."""
    m = re.search(r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>', html, re.DOTALL | re.IGNORECASE)
    if m:
        try:
            return json.loads(m.group(1))
        except Exception:
            pass
    return None


def _find_price_in_obj(obj: Any, keys: tuple = ("minListPrice", "listingPrice", "min_price",
                                                  "lowestPrice", "lowest_price", "price",
                                                  "minPrice", "startingFrom")) -> Optional[float]:
    """Recursively search a nested dict/list for a known price key."""
    if isinstance(obj, dict):
        for k in keys:
            if k in obj and obj[k] is not None:
                try:
                    return float(obj[k])
                except Exception:
                    pass
        for v in obj.values():
            result = _find_price_in_obj(v, keys)
            if result is not None:
                return result
    elif isinstance(obj, list):
        for item in obj[:10]:
            result = _find_price_in_obj(item, keys)
            if result is not None:
                return result
    return None


# ---------------------------------------------------------------------------
# SeatGeek poller
# ---------------------------------------------------------------------------

def _sg_event_id_from_url(url: str) -> Optional[str]:
    """Extract SeatGeek numeric event ID from URL if present."""
    m = re.search(r"/events?/(\d+)", url, re.IGNORECASE)
    return m.group(1) if m else None


def _sg_performer_slug_from_url(url: str) -> Optional[str]:
    """Extract performer slug from SeatGeek URL, e.g. 'taylor-swift'."""
    m = re.search(r"seatgeek\.com/([a-z0-9-]+)-tickets", url, re.IGNORECASE)
    return m.group(1) if m else None


def poll_seatgeek_price(monitor: Dict[str, Any]) -> Optional[Tuple[Optional[float], Optional[float], str, str]]:
    """
    Poll a SeatGeek monitor URL for lowest listing price.
    Returns (price_min, price_max, event_name, event_url) or None on failure.
    """
    url = monitor.get("url", "")
    proxies = monitor.get("proxies") or []

    # Try API first if client ID is set
    if SG_CLIENT_ID:
        event_id = _sg_event_id_from_url(url)
        if event_id:
            sg_params: Dict = {"client_id": SG_CLIENT_ID}
            if SG_CLIENT_SECRET:
                sg_params["client_secret"] = SG_CLIENT_SECRET
            data = _get(
                f"{SG_API_BASE}/events/{event_id}",
                params=sg_params,
                proxies=proxies,
            )
            if data:
                stats = data.get("stats") or {}
                price_min = stats.get("lowest_price")
                price_max = stats.get("highest_price") or price_min
                name = data.get("title") or data.get("short_title") or "Event"
                ev_url = data.get("url") or url
                if price_min is not None:
                    return float(price_min), float(price_max or price_min), name, ev_url

        slug = _sg_performer_slug_from_url(url)
        if slug:
            sg_params2: Dict = {
                "client_id": SG_CLIENT_ID,
                "performers.slug": slug,
                "per_page": 1,
                "sort": "lowest_price.asc",
            }
            if SG_CLIENT_SECRET:
                sg_params2["client_secret"] = SG_CLIENT_SECRET
            data = _get(
                f"{SG_API_BASE}/events",
                params=sg_params2,
                proxies=proxies,
            )
            if data:
                events = data.get("events") or []
                if events:
                    ev = events[0]
                    stats = ev.get("stats") or {}
                    price_min = stats.get("lowest_price")
                    price_max = stats.get("highest_price") or price_min
                    name = ev.get("title") or "Event"
                    ev_url = ev.get("url") or url
                    if price_min is not None:
                        return float(price_min), float(price_max or price_min), name, ev_url

    # Fallback: scrape the page
    html = _get_html(url, proxies=proxies)
    if not html:
        return None

    # Try JSON-LD
    for jld in _extract_jsonld(html):
        price = _find_price_in_obj(jld)
        if price and price > 0:
            # Extract event name from JSON-LD
            name = ""
            if isinstance(jld, dict):
                name = jld.get("name") or jld.get("title") or ""
            return price, price, name or "Event", url

    # Try __NEXT_DATA__
    nd = _extract_next_data(html)
    if nd:
        price = _find_price_in_obj(nd)
        if price and price > 0:
            return price, price, "Event", url

    # Try plain text extraction for "From $XX"
    m = re.search(r"From\s+\$(\d+(?:\.\d+)?)", html)
    if m:
        price = float(m.group(1))
        return price, price, "Event", url

    return None


# ---------------------------------------------------------------------------
# StubHub poller
# ---------------------------------------------------------------------------

def _sh_event_id_from_url(url: str) -> Optional[str]:
    m = re.search(r"/event/(\d+)", url, re.IGNORECASE)
    return m.group(1) if m else None


def poll_stubhub_price(monitor: Dict[str, Any]) -> Optional[Tuple[Optional[float], Optional[float], str, str]]:
    """
    Poll a StubHub monitor URL for lowest listing price via page scrape.
    Returns (price_min, price_max, event_name, event_url) or None.
    """
    url = monitor.get("url", "")
    proxies = monitor.get("proxies") or []

    html = _get_html(url, proxies=proxies)
    if not html:
        return None

    # Try JSON-LD first
    for jld in _extract_jsonld(html):
        if isinstance(jld, dict) and jld.get("@type") in ("Event", "MusicEvent"):
            offers = jld.get("offers") or {}
            if isinstance(offers, dict):
                price_min = offers.get("lowPrice") or offers.get("price")
                price_max = offers.get("highPrice") or price_min
                if price_min:
                    name = jld.get("name") or "Event"
                    return float(price_min), float(price_max or price_min), name, url

    # Try __NEXT_DATA__
    nd = _extract_next_data(html)
    if nd:
        price = _find_price_in_obj(nd)
        if price and price > 0:
            # Try to find name
            name_m = re.search(r'"name"\s*:\s*"([^"]{5,80})"', html)
            name = name_m.group(1) if name_m else "Event"
            return price, price, name, url

    # Plain text "From $XX" or "Starting at $XX"
    m = re.search(r"(?:From|Starting at|As low as)\s+\$(\d+(?:\.\d+)?)", html, re.IGNORECASE)
    if m:
        price = float(m.group(1))
        name_m = re.search(r'<h1[^>]*>(.*?)</h1>', html, re.DOTALL)
        name = re.sub(r"<[^>]+>", "", name_m.group(1)).strip() if name_m else "Event"
        return price, price, name[:100], url

    return None


# ---------------------------------------------------------------------------
# VividSeats poller
# ---------------------------------------------------------------------------

def poll_vividseats_price(monitor: Dict[str, Any]) -> Optional[Tuple[Optional[float], Optional[float], str, str]]:
    """
    Poll a VividSeats monitor URL for lowest listing price via page scrape.
    Returns (price_min, price_max, event_name, event_url) or None.
    """
    url = monitor.get("url", "")
    proxies = monitor.get("proxies") or []

    html = _get_html(url, proxies=proxies)
    if not html:
        return None

    # JSON-LD
    for jld in _extract_jsonld(html):
        if isinstance(jld, dict) and jld.get("@type") in ("Event", "MusicEvent", "SportsEvent"):
            offers = jld.get("offers") or {}
            if isinstance(offers, dict):
                price_min = offers.get("lowPrice") or offers.get("price")
                if price_min:
                    name = jld.get("name") or "Event"
                    price_max = offers.get("highPrice") or price_min
                    return float(price_min), float(price_max or price_min), name, url

    # __NEXT_DATA__ / __INITIAL_STATE__
    nd = _extract_next_data(html)
    if nd:
        price = _find_price_in_obj(nd)
        if price and price > 0:
            return price, price, "Event", url

    # Search for embedded JSON with "minListPrice"
    m = re.search(r'"minListPrice"\s*:\s*([\d.]+)', html)
    if m:
        price = float(m.group(1))
        name_m = re.search(r'"name"\s*:\s*"([^"]{5,80})"', html)
        name = name_m.group(1) if name_m else "Event"
        return price, price, name, url

    return None


# ---------------------------------------------------------------------------
# Per-monitor poll dispatcher
# ---------------------------------------------------------------------------

_POLL_FN = {
    "seatgeek":   poll_seatgeek_price,
    "stubhub":    poll_stubhub_price,
    "vividseats": poll_vividseats_price,
}


async def _poll_one_monitor(
    monitor: Dict[str, Any],
    discord_post: Callable[[Any], Awaitable[None]],
) -> None:
    platform = monitor.get("platform", "ticketmaster")
    fn = _POLL_FN.get(platform)
    if fn is None:
        return  # ticketmaster handled by drop_catcher

    monitor_id  = monitor.get("id")
    last_min    = monitor.get("last_price_min")
    last_max    = monitor.get("last_price_max")
    webhook_url = monitor.get("discord_webhook", "")

    try:
        result = await asyncio.to_thread(fn, monitor)
    except Exception as e:
        logger.warning("platform_monitor: poll failed for monitor %s: %s", monitor_id, e)
        return

    if result is None:
        return

    price_min, price_max, event_name, event_url = result

    # Update stored price
    if _DB_OK:
        try:
            update_monitor_price(monitor_id, price_min, price_max)
        except Exception:
            pass

    if price_min is None:
        return

    # Determine alert type
    alert_type = None
    if last_min is None:
        # First time seeing a price — fire a new_event alert
        alert_type = "new_event"
    elif price_min < last_min * (1 - PRICE_DROP_PCT / 100):
        alert_type = "price_drop"
    elif price_min > last_min * (1 + PRICE_SPIKE_PCT / 100):
        alert_type = "price_spike"

    if alert_type is None:
        return

    logger.info(
        "platform_monitor: %s alert on %s monitor %s (%.0f → %.0f)",
        alert_type, platform, monitor_id, last_min or 0, price_min,
    )

    alert_data: Dict[str, Any] = {
        "type":     alert_type,
        "name":     event_name,
        "url":      event_url,
        "source":   platform.title(),
        "price_min": price_min,
        "price_max": price_max,
    }
    if alert_type == "price_drop":
        alert_data["old_price"] = last_min
        alert_data["new_price"] = price_min
    elif alert_type == "price_spike":
        alert_data["old_price"] = last_min
        alert_data["new_price"] = price_min

    if _DB_OK:
        try:
            increment_monitor_alert_count(monitor_id)
        except Exception:
            pass

    try:
        await discord_post(alert_data)
    except Exception as e:
        logger.warning("platform_monitor: discord_post failed for monitor %s: %s", monitor_id, e)


# ---------------------------------------------------------------------------
# Main background loop
# ---------------------------------------------------------------------------

PLATFORMS = ("seatgeek", "stubhub", "vividseats")


def is_available() -> bool:
    return _HTTP_OK and _DB_OK


async def platform_watch_loop(
    discord_post: Callable[[Any], Awaitable[None]],
    stop_event: Optional[asyncio.Event] = None,
) -> None:
    """
    Background loop: polls SeatGeek, StubHub, and VividSeats monitors
    at PLATFORM_POLL_SECONDS interval. Fires structured alert dicts via discord_post.
    """
    if not is_available():
        logger.info("platform_monitor: not available (missing requests or db); loop exiting.")
        return

    logger.info("platform_monitor: background loop started (%ss).", POLL_SECONDS)

    while True:
        if stop_event and stop_event.is_set():
            logger.info("platform_monitor: stop_event set; exiting.")
            return

        for platform in PLATFORMS:
            try:
                monitors = await asyncio.to_thread(get_monitors_by_platform, platform)
            except Exception as e:
                logger.warning("platform_monitor: db fetch failed for %s: %s", platform, e)
                continue

            for monitor in monitors:
                await _poll_one_monitor(monitor, discord_post)
                await asyncio.sleep(1)  # rate limit between monitors

        # Sleep in chunks for responsive shutdown
        slept = 0
        while slept < POLL_SECONDS:
            if stop_event and stop_event.is_set():
                return
            chunk = min(15, POLL_SECONDS - slept)
            await asyncio.sleep(chunk)
            slept += chunk
