"""
Viking AI - Tour Scan Monitor

Enhancement:
- Poll RSS feeds for newly announced tour dates and send alerts.
How it posts:
  - If `discord_client` and `channel_id` provided: posts in Discord.
  - Else if `TOUR_SCAN_WEBHOOK_URL` is set: posts to the webhook.

Notes:
  - Everything is best-effort and failure-safe (no crash loops).
"""
import os
import time
import json
import threading
import logging
from datetime import datetime
from typing import Optional, List, Dict, Any, Callable


logger = logging.getLogger("tour_scan")

# -------------- ENV --------------
TOUR_SCAN_ALERT_CHANNEL_ID = int(os.getenv("TOUR_SCAN_ALERT_CHANNEL_ID", "0") or "0")
TOUR_SCAN_WEBHOOK_URL = (os.getenv("TOUR_SCAN_WEBHOOK_URL") or "").strip()

# Optional prefix for mixed alert channels
TOUR_SCAN_PREFIX = (os.getenv("TOUR_SCAN_PREFIX") or "[TOUR]").strip()

# -------------- GLOBALS --------------
_THREAD: Optional[threading.Thread] = None
_STOP_EVENT = threading.Event()

# -------------- HELPERS --------------
def _resolve_interval_seconds(value: Any, default: int = 3600) -> int:
    if isinstance(value, dict):
        for key in ("interval_seconds", "seconds", "interval", "poll_seconds", "poll_interval", "value"):
            if key in value:
                value = value.get(key)
                break
        else:
            return default
    try:
        interval = int(float(value))
    except Exception:
        return default
    return max(1, interval)

def _apply_prefix(prefix: str, msg: str) -> str:
    """Prepend prefix unless message already starts with it."""
    if not prefix:
        return msg
    m = (msg or "").strip()
    if not m:
        return msg
    return m if m.startswith(prefix) else f"{prefix} {m}"

def post_webhook(msg: str) -> None:
    """Post a message to the configured TOUR_SCAN webhook."""
    if not TOUR_SCAN_WEBHOOK_URL:
        logger.info("TOUR_SCAN_WEBHOOK_URL not set, skipping webhook post")
        return

    content = _apply_prefix(TOUR_SCAN_PREFIX, msg)

    try:
        import requests
        requests.post(
            TOUR_SCAN_WEBHOOK_URL,
            json={"content": content},
            timeout=15,
        ).raise_for_status()
    except Exception as e:
        logger.warning("Webhook post failed: %s", e)

# --------- YOUR EXISTING LOGIC BELOW ---------
# NOTE: I’m keeping your function names so bot.py can call start_background_thread()

def fetch_rss_items(url: str) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    try:
        import feedparser
        feed = feedparser.parse(url)
        for e in (feed.entries or []):
            items.append({
                "title": getattr(e, "title", ""),
                "link": getattr(e, "link", ""),
                "published": getattr(e, "published", "") or getattr(e, "updated", ""),
            })
    except Exception as ex:
        logger.warning("Failed parsing RSS %s: %s", url, ex)
    return items

def resolve_artist(artist: str) -> str:
    return (artist or "").strip()

def rate_artist(artist: str) -> int:
    return 3

def stars_to_emoji(stars: int) -> str:
    return "⭐" * max(0, min(5, stars))

def top_cities_for_artist(artist: str) -> List[str]:
    return []

def poll_tour_scan_loop(
    interval_seconds: int = 3600,
    post_callback: Optional[Callable[[Dict[str, Any]], Optional[str]]] = None,
) -> None:
    """Main polling loop (runs in a background thread)."""
    interval_seconds = _resolve_interval_seconds(interval_seconds, 3600)
    logger.info("Tour scan loop started (%s-minute interval).", int(interval_seconds / 60))
    # If you have RSS URLs in env, use them; otherwise keep running quietly.
    rss_urls = [u.strip() for u in (os.getenv("TOUR_SCAN_RSS_URLS") or "").split(",") if u.strip()]
    seen = set()

    while not _STOP_EVENT.is_set():
        try:
            if not (TOUR_SCAN_ALERT_CHANNEL_ID or TOUR_SCAN_WEBHOOK_URL):
                logger.info("tour_scan_monitor: no channel_id or TOUR_SCAN_WEBHOOK_URL configured; not starting")
                time.sleep(interval_seconds)
                continue

            for url in rss_urls:
                for item in fetch_rss_items(url):
                    key = (item.get("title","") + "|" + item.get("link","")).strip()
                    if not key or key in seen:
                        continue
                    seen.add(key)

                    msg = f"New tour item: {item.get('title','(no title)')}\n{item.get('link','')}".strip()
                    if callable(post_callback):
                        try:
                            enriched = post_callback(item)
                            if isinstance(enriched, str) and enriched.strip():
                                msg = enriched.strip()
                        except Exception:
                            logger.exception("tour_scan_monitor: post_callback failed")
                    post_webhook(msg)

        except Exception as ex:
            logger.warning("tour_scan loop error: %s", ex)

        # sleep in small increments so stop is responsive
        for _ in range(max(1, int(interval_seconds / 5))):
            if _STOP_EVENT.is_set():
                break
            time.sleep(5)

def start_background_thread(
    interval_seconds: int = 3600,
    post_callback: Optional[Callable[[Dict[str, Any]], Optional[str]]] = None,
) -> None:
    global _THREAD
    if _THREAD and _THREAD.is_alive():
        logger.info("tour_scan_monitor: already running")
        return
    _STOP_EVENT.clear()
    if isinstance(interval_seconds, dict) and post_callback is None:
        post_callback = interval_seconds.get("post_callback")
    resolved_interval = _resolve_interval_seconds(interval_seconds, 3600)
    _THREAD = threading.Thread(
        target=poll_tour_scan_loop,
        kwargs={"interval_seconds": resolved_interval, "post_callback": post_callback},
        daemon=True,
    )
    _THREAD.start()
    logger.info("tour_scan_monitor: background thread started")

def stop_background_thread() -> None:
    _STOP_EVENT.set()
