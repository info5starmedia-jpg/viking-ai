import os
import time
import json
import threading
import logging
from typing import Optional, Any, Dict, List, Set, Callable

logger = logging.getLogger("tour_scan")

# ========= ENV =========
TOUR_SCAN_RSS_URLS = (os.getenv("TOUR_SCAN_RSS_URLS") or "").strip()
TOUR_SCAN_SEEN_PATH = os.getenv("TOUR_SCAN_SEEN_PATH", "/opt/viking-ai/data/tour_scan_seen.json")
TOUR_SCAN_PREFIX = (os.getenv("TOUR_SCAN_PREFIX") or "[TOUR]").strip()
TOUR_SCAN_WEBHOOK_URL = (os.getenv("TOUR_SCAN_WEBHOOK_URL") or "").strip()

# ========= STATE =========
_THREAD: Optional[threading.Thread] = None
_STOP_EVENT = threading.Event()

# Wake trigger for "scan now" without waiting for the next interval tick
_WAKE_EVENT = threading.Event()

# Optional: last-run stats (useful for /status)
_LAST_RUN: Dict[str, Any] = {
    "last_run_utc": None,
    "feeds": 0,
    "fetched_items": 0,
    "new_items": 0,
    "errors": 0,
}


def clamp_interval(value: Any, default: int = 3600) -> int:
    """
    Convert value to int seconds and clamp to >= 60s.
    """
    try:
        iv = int(float(value))
    except Exception:
        iv = default
    return max(60, iv)  # never poll faster than 60s


def load_seen() -> Set[str]:
    try:
        with open(TOUR_SCAN_SEEN_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, dict) and isinstance(data.get("seen"), list):
                return set(str(x) for x in data["seen"])
    except FileNotFoundError:
        return set()
    except Exception as e:
        logger.warning("tour_scan: failed loading seen file: %s", e)
    return set()


def save_seen(seen: Set[str]) -> None:
    """
    Persist seen keys with stable pruning (keep newest-ish by sort).
    """
    try:
        os.makedirs(os.path.dirname(TOUR_SCAN_SEEN_PATH), exist_ok=True)
        seen_list = sorted(seen)
        if len(seen_list) > 5000:
            seen_list = seen_list[-5000:]
        with open(TOUR_SCAN_SEEN_PATH, "w", encoding="utf-8") as f:
            json.dump({"seen": seen_list}, f)
    except Exception as e:
        logger.warning("tour_scan: failed saving seen file: %s", e)


def post_webhook(msg: str) -> None:
    if not TOUR_SCAN_WEBHOOK_URL:
        return
    try:
        import requests

        r = requests.post(
            TOUR_SCAN_WEBHOOK_URL,
            json={"content": f"{TOUR_SCAN_PREFIX} {msg}"},
            timeout=15,
        )
        if r.status_code >= 300:
            logger.warning(
                "tour_scan: webhook non-2xx status=%s body=%s",
                r.status_code,
                (r.text or "")[:200],
            )
        else:
            logger.info("tour_scan: webhook posted")
    except Exception as e:
        logger.warning("tour_scan: webhook failed: %s", e)


def fetch_rss(url: str) -> List[Dict[str, str]]:
    """
    Returns list of items with keys: guid, title, link, pubDate, summary
    """
    try:
        import feedparser

        feed = feedparser.parse(url)
        items: List[Dict[str, str]] = []
        for e in (feed.entries or []):
            guid = getattr(e, "id", "") or getattr(e, "guid", "") or ""
            title = getattr(e, "title", "") or ""
            link = getattr(e, "link", "") or ""
            pub = getattr(e, "published", "") or getattr(e, "pubDate", "") or ""
            summary = getattr(e, "summary", "") or getattr(e, "description", "") or ""
            items.append(
                {
                    "guid": str(guid or ""),
                    "title": str(title or ""),
                    "link": str(link or ""),
                    "pubDate": str(pub or ""),
                    "summary": str(summary or ""),
                }
            )
        return items
    except Exception as e:
        logger.warning("tour_scan: RSS fetch failed %s: %s", url, e)
        return []


def _current_feeds() -> List[str]:
    # Read env each time so a restart picks up changes cleanly.
    rss_urls = (os.getenv("TOUR_SCAN_RSS_URLS") or TOUR_SCAN_RSS_URLS or "").strip()
    return [u.strip() for u in rss_urls.split(",") if u.strip()]


def _effective_interval_seconds(passed_interval: int) -> int:
    # If TOUR_SCAN_INTERVAL_SECONDS is set, it wins; otherwise use passed value.
    env_val = (os.getenv("TOUR_SCAN_INTERVAL_SECONDS") or "").strip()
    if env_val:
        return clamp_interval(env_val, default=passed_interval)
    return clamp_interval(passed_interval)


def _scan_once(
    *,
    seen: Set[str],
    feeds: List[str],
    post_callback: Optional[Callable[[Dict[str, str]], Any]] = None,
    post_to_webhook: bool = True,
) -> Dict[str, Any]:
    """
    One scan pass over all feeds.
    Returns summary: fetched_items, new_items, new_titles, errors
    """
    fetched_total = 0
    new_total = 0
    errors = 0
    new_titles: List[str] = []

    for url in feeds:
        if _STOP_EVENT.is_set():
            break

        logger.info("tour_scan: fetching %s", url)
        items = fetch_rss(url)
        fetched_total += len(items)
        logger.info("tour_scan: fetched %d items", len(items))

        for item in items:
            guid = (item.get("guid") or "").strip()
            title = (item.get("title") or "").strip()
            link = (item.get("link") or "").strip()

            # Stable unique key: prefer guid; else title|link; else skip
            key = guid or (f"{title}|{link}" if (title or link) else "")
            if not key or key in seen:
                continue

            seen.add(key)
            save_seen(seen)

            new_total += 1
            new_titles.append(title or key)

            msg = f"NEW tour item:\n{title or 'New tour item'}\n{link}".strip()
            logger.info("tour_scan: NEW item %s", title or key)

            # Optional per-item enrichment (used by your bot sometimes)
            if callable(post_callback):
                try:
                    enriched = post_callback(item)
                    if isinstance(enriched, str) and enriched.strip():
                        msg = enriched.strip()
                except Exception:
                    errors += 1
                    logger.exception("tour_scan: post_callback failed")

            if post_to_webhook:
                post_webhook(msg)

    return {
        "fetched_items": fetched_total,
        "new_items": new_total,
        "new_titles": new_titles,
        "errors": errors,
    }


def run_once(post_callback=None, post_to_webhook: bool = True) -> Dict[str, Any]:
    """
    Synchronous "scan now" helper for a /tour_scan_now command.
    - Runs a single scan immediately (does NOT start/stop the background thread).
    - Returns a summary dict you can display in Discord.
    """
    seen = load_seen()
    feeds = _current_feeds()
    if not feeds:
        return {"ok": False, "error": "no TOUR_SCAN_RSS_URLS configured", "feeds": 0}

    logger.info("tour_scan: run_once invoked feeds=%d seen=%d", len(feeds), len(seen))
    out = _scan_once(seen=seen, feeds=feeds, post_callback=post_callback, post_to_webhook=post_to_webhook)
    _LAST_RUN.update(
        {
            "last_run_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "feeds": len(feeds),
            "fetched_items": out.get("fetched_items", 0),
            "new_items": out.get("new_items", 0),
            "errors": out.get("errors", 0),
        }
    )
    out["ok"] = True
    out["feeds"] = len(feeds)
    return out


def trigger_now() -> None:
    """
    Tiny local trigger: wake the background loop immediately.
    Your /tour_scan_now slash command can call this to force the loop to run
    right away (instead of waiting up to TOUR_SCAN_INTERVAL_SECONDS).
    """
    _WAKE_EVENT.set()


def get_status() -> Dict[str, Any]:
    """
    Optional: small status payload (nice for /status or /health dumps).
    """
    return {
        "thread_alive": bool(_THREAD and _THREAD.is_alive()),
        "stop_set": _STOP_EVENT.is_set(),
        "wake_set": _WAKE_EVENT.is_set(),
        "feeds": len(_current_feeds()),
        "seen_path": TOUR_SCAN_SEEN_PATH,
        **_LAST_RUN,
    }


def poll_loop(interval_seconds: int, post_callback=None) -> None:
    interval_seconds = _effective_interval_seconds(interval_seconds)
    seen = load_seen()
    feeds = _current_feeds()

    logger.info("tour_scan: loop started interval=%ss feeds=%d seen=%d", interval_seconds, len(feeds), len(seen))

    while not _STOP_EVENT.is_set():
        feeds = _current_feeds()

        if not feeds:
            logger.warning("tour_scan: no TOUR_SCAN_RSS_URLS configured; sleeping")
            _LAST_RUN.update(
                {
                    "last_run_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "feeds": 0,
                    "fetched_items": 0,
                    "new_items": 0,
                }
            )
        else:
            out = _scan_once(seen=seen, feeds=feeds, post_callback=post_callback, post_to_webhook=True)
            _LAST_RUN.update(
                {
                    "last_run_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "feeds": len(feeds),
                    "fetched_items": out.get("fetched_items", 0),
                    "new_items": out.get("new_items", 0),
                    "errors": out.get("errors", 0),
                }
            )

        # Sleep, but allow immediate wake via trigger_now()
        # Check stop frequently so shutdown is responsive.
        waited = 0.0
        while not _STOP_EVENT.is_set() and waited < float(interval_seconds):
            # If someone triggered "scan now", clear and break immediately
            if _WAKE_EVENT.is_set():
                _WAKE_EVENT.clear()
                logger.info("tour_scan: wake trigger received; running scan now")
                break

            # Wait up to 5s at a time
            step = min(5.0, float(interval_seconds) - waited)
            _WAKE_EVENT.wait(timeout=step)
            waited += step


def start_background_thread(interval_seconds: int = 3600, post_callback=None) -> None:
    """
    Starts the tour scan background thread.
    - Compatible with "legacy signature" style used by your bot.
    """
    global _THREAD
    if _THREAD and _THREAD.is_alive():
        return
    _STOP_EVENT.clear()
    _WAKE_EVENT.clear()
    _THREAD = threading.Thread(
        target=poll_loop,
        kwargs={"interval_seconds": interval_seconds, "post_callback": post_callback},
        daemon=True,
    )
    _THREAD.start()
    logger.info("tour_scan: background thread started")


def stop_background_thread() -> None:
    global _THREAD
    _STOP_EVENT.set()
    _WAKE_EVENT.set()  # wake any sleep so it can exit promptly
    try:
        if _THREAD and _THREAD.is_alive():
            _THREAD.join(timeout=10)
    except Exception:
        pass
