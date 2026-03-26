"""
drop_catcher.py

Two-track ticket drop detection engine for Viking AI:

TRACK 1 — Watched events (specific TM event IDs)
  Poll each registered event ID; fire when status transitions to "onsale".
  Commands: /drop_add, /drop_list, /drop_remove

TRACK 2 — Global scan (all US/CA music events)
  Uses TM's onsaleStartDateTime filter to catch any concert that just went
  on-sale in the last N hours. Hash-dedup prevents double-alerting.
  Commands: /drop_changes, /drop_tomorrow

Env vars (all optional):
  VIKING_DB_PATH              – SQLite path  (default /opt/viking-ai/viking_ai.sqlite)
  DROP_CATCHER_POLL_SECONDS   – per-event poll interval seconds (default 300)
  DROP_GLOBAL_SCAN_HOURS      – global scan look-back window hours (default 1)
  DROP_GLOBAL_SCAN_INTERVAL   – global scan interval seconds (default 900)
  DROP_CATCH_MAX_WATCHES      – max simultaneous event-ID watches (default 20)
  DROP_GLOBAL_SCAN_ENABLED    – set to 0 to disable global scan (default 1)
"""

from __future__ import annotations

import asyncio
import csv
import json
import logging
import os
import re
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

try:
    from dotenv import load_dotenv
except Exception:
    load_dotenv = None  # type: ignore

if load_dotenv:
    load_dotenv("/opt/viking-ai/.env", override=False)

logger = logging.getLogger("drop_catcher")

# ---------------------------------------------------------------------------
# Optional deps — graceful fallbacks on every import
# ---------------------------------------------------------------------------

try:
    import requests as _requests  # type: ignore
except Exception:
    _requests = None  # type: ignore

try:
    import ticketmaster_agent as _ta  # type: ignore
except Exception as _e:
    _ta = None  # type: ignore
    logger.warning("ticketmaster_agent import failed in drop_catcher: %s", _e)

try:
    from seatmap_intel import assess_event_seatmap as _assess_seatmap  # type: ignore
except Exception:
    _assess_seatmap = None  # type: ignore

try:
    from agents.tm_live_inventory import get_live_seatmap as _get_live_seatmap  # type: ignore
    from agents.tm_live_inventory import summarize_inventory as _summarize_inventory  # type: ignore
except Exception:
    _get_live_seatmap = None  # type: ignore
    _summarize_inventory = None  # type: ignore

try:
    from arbitrage_agent import analyze_arbitrage as _analyze_arbitrage  # type: ignore
except Exception:
    _analyze_arbitrage = None  # type: ignore

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

DB_PATH = os.getenv("VIKING_DB_PATH", "/opt/viking-ai/viking_ai.sqlite")
DROP_POLL_SECONDS = int(os.getenv("DROP_CATCHER_POLL_SECONDS", "300") or "300")
MAX_WATCHES = int(os.getenv("DROP_CATCH_MAX_WATCHES", "20") or "20")
GLOBAL_SCAN_HOURS = float(os.getenv("DROP_GLOBAL_SCAN_HOURS", "1") or "1")
GLOBAL_SCAN_INTERVAL = int(os.getenv("DROP_GLOBAL_SCAN_INTERVAL", "900") or "900")
GLOBAL_SCAN_ENABLED = os.getenv("DROP_GLOBAL_SCAN_ENABLED", "1").strip().lower() not in ("0", "false", "")

TM_BASE = "https://app.ticketmaster.com/discovery/v2/events.json"
COUNTRY_CODES = ["US", "CA"]
GLOBAL_STATE_FILE = os.path.join(os.path.dirname(__file__), "drop_global_state.json")

# Status codes meaning "no tickets available right now"
_OFFSALE_CODES = {"offsale", "cancelled", "postponed", "rescheduled"}

# Aggressive mode config
AGGRESSIVE_ENABLED       = os.getenv("DROP_AGGRESSIVE_ENABLED", "1").strip().lower() not in ("0", "false")
AGGRESSIVE_POLL_SECONDS  = int(os.getenv("DROP_AGGRESSIVE_POLL_SECONDS", "3") or "3")
AGGRESSIVE_WINDOW_SECS   = int(os.getenv("DROP_AGGRESSIVE_WINDOW_SECS", "300") or "300")  # T-5min
AGGRESSIVE_PREWARM_SECS  = int(os.getenv("DROP_AGGRESSIVE_PREWARM_SECS", "60") or "60")   # T-60s pre-warm
AGGRESSIVE_EXPIRE_SECS   = int(os.getenv("DROP_AGGRESSIVE_EXPIRE_SECS", "600") or "600")  # T+10min expire

# In-memory: event_id -> {onsale_unix, url, warmed, added_unix}
_aggressive_targets: Dict[str, Dict[str, Any]] = {}


# ---------------------------------------------------------------------------
# Availability check
# ---------------------------------------------------------------------------

def is_available() -> bool:
    """True if TM agent can query events."""
    return _ta is not None and hasattr(_ta, "get_event_details")


def global_scan_is_available() -> bool:
    """True if global scan (requires requests + TM key) is ready."""
    return bool(
        _requests is not None
        and os.getenv("TICKETMASTER_API_KEY")
        and GLOBAL_SCAN_ENABLED
    )


# ---------------------------------------------------------------------------
# DB helpers — Track 1 (watched event IDs)
# ---------------------------------------------------------------------------

def _connect() -> sqlite3.Connection:
    return sqlite3.connect(DB_PATH, timeout=30)


def _init_db() -> None:
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS drop_watch_events (
                event_id        TEXT PRIMARY KEY,
                artist          TEXT,
                name            TEXT,
                url             TEXT,
                added_unix      REAL,
                expires_at_unix REAL,
                last_status     TEXT,
                last_check_unix REAL,
                last_price_min  REAL,
                last_price_max  REAL
            )
            """
        )
        conn.commit()


# ---------------------------------------------------------------------------
# TM helpers (pulled from tm_scraper_change_tracking pattern)
# ---------------------------------------------------------------------------

def _fmt_utc(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _tm_get(params: Dict[str, Any]) -> Dict[str, Any]:
    """Raw TM Discovery API GET. Raises on HTTP errors."""
    key = os.getenv("TICKETMASTER_API_KEY") or os.getenv("TM_API_KEY")
    if not key:
        raise RuntimeError("Missing TICKETMASTER_API_KEY.")
    if _requests is None:
        raise RuntimeError("requests library not available.")
    q = dict(params)
    q["apikey"] = key
    resp = _requests.get(TM_BASE, params=q, timeout=20)
    resp.raise_for_status()
    return resp.json()


def _paginate_events(params: Dict[str, Any], max_results: int = 200) -> List[Dict[str, Any]]:
    """
    Paginate TM Discovery events. Respects TM's (page * size) < 1000 limit.
    Pulled from tm_scraper_change_tracking._paginate_events pattern.
    """
    all_events: List[Dict[str, Any]] = []
    size = int(params.get("size", 200))
    max_pages = min(1000 // max(size, 1), 5)  # cap at 5 pages for safety

    for page in range(max_pages):
        if len(all_events) >= max_results:
            break
        p = dict(params)
        p["page"] = page
        try:
            data = _tm_get(p)
        except Exception as e:
            logger.warning("drop_catcher: TM page %d error: %s", page, e)
            break

        events = (data.get("_embedded") or {}).get("events") or []
        if not events:
            break

        all_events.extend(events)

        page_info = data.get("page") or {}
        number = page_info.get("number")
        total_pages = page_info.get("totalPages")
        if total_pages is not None and number is not None and number >= total_pages - 1:
            break

    return all_events[:max_results]


def _normalize_event(e: Dict[str, Any]) -> Dict[str, Any]:
    """
    Flatten a raw TM event dict into clean fields.
    Mirrors tm_scraper_change_tracking._normalize_event with added price info.
    """
    dates = e.get("dates") or {}
    start = dates.get("start") or {}
    status_code = (dates.get("status") or {}).get("code") or "unknown"

    venues = ((e.get("_embedded") or {}).get("venues") or [{}])
    v0 = venues[0] if venues else {}
    city = (v0.get("city") or {}).get("name") or ""
    country = (v0.get("country") or {}).get("name") or ""
    venue_name = v0.get("name") or ""

    atts = (e.get("_embedded") or {}).get("attractions") or []
    artist_name = atts[0].get("name") if atts else ""

    price_ranges = e.get("priceRanges") or []
    price_min = price_max = None
    if price_ranges:
        try:
            price_min = min(p.get("min", 0) for p in price_ranges if p.get("min") is not None)
            price_max = max(p.get("max", 0) for p in price_ranges if p.get("max") is not None)
        except Exception:
            pass

    # Extract public on-sale start time for aggressive mode
    sales = e.get("sales") or {}
    public_sales = sales.get("public") or {}
    onsale_start_str = public_sales.get("startDateTime") or ""
    onsale_start_unix = None
    if onsale_start_str:
        try:
            from datetime import datetime, timezone as _tz
            dt = datetime.strptime(onsale_start_str, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=_tz.utc)
            onsale_start_unix = dt.timestamp()
        except Exception:
            pass

    return {
        "id": e.get("id") or "",
        "name": e.get("name") or "",
        "artist": artist_name,
        "url": e.get("url") or "",
        "status": status_code,
        "event_local_date": start.get("localDate") or "",
        "event_local_time": start.get("localTime") or "",
        "venue": venue_name,
        "city": city,
        "country": country,
        "price_min": price_min,
        "price_max": price_max,
        "onsale_start_unix": onsale_start_unix,
    }


def _event_hash(norm: Dict[str, Any]) -> str:
    """Fingerprint of key event fields for change detection."""
    parts = [
        norm.get("name") or "",
        norm.get("status") or "",
        norm.get("event_local_date") or "",
        norm.get("city") or "",
        str(norm.get("price_min") or ""),
        str(norm.get("price_max") or ""),
    ]
    return "|".join(parts)


# ---------------------------------------------------------------------------
# Global scan state (file-based, mirrors tm_scraper_change_tracking pattern)
# ---------------------------------------------------------------------------

def _load_global_state() -> Dict[str, Any]:
    try:
        with open(GLOBAL_STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f) or {}
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    except Exception:
        return {}


def _save_global_state(state: Dict[str, Any]) -> None:
    try:
        with open(GLOBAL_STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2, sort_keys=True)
    except Exception as e:
        logger.warning("drop_catcher: failed saving global state: %s", e)


# ---------------------------------------------------------------------------
# Seatmap enrichment (from seatmap_intel + agents.tm_live_inventory)
# ---------------------------------------------------------------------------

async def _enrich_with_seatmap(event_id: str) -> Optional[str]:
    """
    Try to fetch live seatmap and return a short enrichment string, or None.
    Non-fatal — if anything fails we just skip it.
    """
    if not _get_live_seatmap or not _summarize_inventory or not _assess_seatmap:
        return None
    try:
        seatmap_data = await _get_live_seatmap(event_id)
        if seatmap_data.get("error"):
            return None
        raw_seatmap = seatmap_data.get("seatmap") or {}
        summary = await asyncio.to_thread(_summarize_inventory, raw_seatmap)
        total = summary.get("total_seats", 0)
        available = summary.get("available_seats", 0)
        if total > 0:
            sections = summary.get("sections") or []
            seat_list = []
            for sec in sections:
                for _ in range(int(sec.get("available", 0))):
                    seat_list.append({"status": "available", "price": sec.get("price", 0)})
                for _ in range(int(sec.get("total", 0)) - int(sec.get("available", 0))):
                    seat_list.append({"status": "sold", "price": sec.get("price", 0)})
            if seat_list:
                intel = _assess_seatmap(seat_list)
                sell_pct = intel.get("sell_through_pct", 0.0)
                signals = intel.get("signals") or []
                lines = [f"Seats: {available}/{total} available ({100 - sell_pct:.0f}% open)"]
                if signals:
                    lines.append(" • ".join(signals[:2]))
                return "\n".join(lines)
    except Exception as e:
        logger.debug("drop_catcher: seatmap enrichment failed for %s: %s", event_id, e)
    return None


# ---------------------------------------------------------------------------
# Arbitrage estimation
# ---------------------------------------------------------------------------

def _estimate_resale_floor(face_min: float, sell_through_pct: float = 0.0) -> float:
    """
    Heuristic resale floor estimate based on face price and demand signals.
    Uses sell-through % from seatmap intel when available.
    """
    if sell_through_pct >= 90:
        multiplier = 2.0   # very hot show
    elif sell_through_pct >= 75:
        multiplier = 1.6
    elif sell_through_pct >= 60:
        multiplier = 1.4
    else:
        multiplier = 1.3   # baseline average
    return face_min * multiplier


def _arbitrage_line(face_min: float, sell_through_pct: float = 0.0) -> Optional[str]:
    """
    Return a one-line arbitrage summary string, or None if unavailable.
    Uses arbitrage_agent.analyze_arbitrage with an estimated resale floor.
    """
    if _analyze_arbitrage is None or face_min <= 0:
        return None
    try:
        resale_floor = _estimate_resale_floor(face_min, sell_through_pct)
        result = _analyze_arbitrage(
            face_floor=face_min,
            resale_floor=resale_floor,
            primary_fees_pct=0.15,   # ~15% TM fees
            resale_fees_pct=0.10,    # ~10% resale platform fees
        )
        return (
            f"Arbitrage: {result.rating} "
            f"(face~${result.face_floor:.0f} → resale~${result.resale_floor:.0f}, "
            f"x{result.factor:.2f}, +${result.spread:.0f})"
        )
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Alert formatters
# ---------------------------------------------------------------------------

def _format_drop_alert(
    row: Dict[str, Any],
    new_status: str,
    seatmap_txt: Optional[str] = None,
    sell_through_pct: float = 0.0,
) -> str:
    event_id = row.get("event_id") or row.get("id") or "?"
    name = row.get("name") or f"Event {event_id}"
    artist = row.get("artist") or row.get("artist_label") or ""
    url = row.get("url") or ""
    old_status = row.get("last_status") or row.get("prev_status") or "unknown"
    price_min = row.get("price_min") or row.get("last_price_min")
    price_max = row.get("price_max") or row.get("last_price_max")
    city = row.get("city") or ""
    date_str = row.get("event_local_date") or ""

    parts = [f"🎟️ **TICKET DROP** — {name}"]
    if artist:
        parts.append(f"Artist: **{artist}**")
    if city or date_str:
        loc = f"{city} {date_str}".strip()
        parts.append(f"When/Where: {loc}")
    if price_min is not None and price_max is not None:
        parts.append(f"Price: `${price_min:.0f}` – `${price_max:.0f}`")
        arb = _arbitrage_line(float(price_min), sell_through_pct)
        if arb:
            parts.append(arb)
    parts.append(f"Status: `{old_status}` → `{new_status}`")
    parts.append(f"Event ID: `{event_id}`")
    if seatmap_txt:
        parts.append(seatmap_txt)
    if url:
        parts.append(url)
    return "\n".join(parts)


def _format_global_drop_alert(norm: Dict[str, Any], change_type: str) -> str:
    name = norm.get("name") or "Unknown Event"
    artist = norm.get("artist") or ""
    city = norm.get("city") or ""
    country = norm.get("country") or ""
    date_str = norm.get("event_local_date") or ""
    url = norm.get("url") or ""
    price_min = norm.get("price_min")
    price_max = norm.get("price_max")

    emoji = "🆕" if change_type == "NEW" else "🔄"
    parts = [f"{emoji} **ON-SALE {change_type}** — {name}"]
    if artist:
        parts.append(f"Artist: **{artist}**")
    loc_parts = [p for p in [city, country] if p]
    if loc_parts:
        parts.append(f"Location: {', '.join(loc_parts)}")
    if date_str:
        parts.append(f"Show date: {date_str}")
    if price_min is not None and price_max is not None:
        parts.append(f"Price: `${price_min:.0f}` – `${price_max:.0f}`")
    if url:
        parts.append(url)
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Track 1 public API — specific watched event IDs
# ---------------------------------------------------------------------------

def add_drop_watch(event_id: str, artist: str = "", days: int = 7) -> Tuple[bool, str]:
    """Register a TM event ID to watch for ticket drops."""
    event_id = (event_id or "").strip()
    if not event_id:
        return False, "event_id is required."
    if not is_available():
        return False, "Ticketmaster agent not available."

    _init_db()
    with _connect() as conn:
        count = conn.execute("SELECT COUNT(*) FROM drop_watch_events").fetchone()[0]
        if count >= MAX_WATCHES:
            return False, f"Max watches ({MAX_WATCHES}) reached. Remove one first."
        existing = conn.execute(
            "SELECT event_id FROM drop_watch_events WHERE event_id = ?", (event_id,)
        ).fetchone()
        if existing:
            return False, f"Event `{event_id}` is already being watched."

        expires = time.time() + days * 86400
        conn.execute(
            """
            INSERT INTO drop_watch_events
                (event_id, artist, name, url, added_unix, expires_at_unix,
                 last_status, last_check_unix, last_price_min, last_price_max)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (event_id, artist.strip(), "", "", time.time(), expires, "unknown", 0, None, None),
        )
        conn.commit()

    return True, f"Now watching event `{event_id}` for drops (expires in {days}d)."


def remove_drop_watch(event_id: str) -> Tuple[bool, str]:
    """Remove an event from the drop watch list."""
    event_id = (event_id or "").strip()
    if not event_id:
        return False, "event_id is required."

    _init_db()
    with _connect() as conn:
        cur = conn.execute(
            "DELETE FROM drop_watch_events WHERE event_id = ?", (event_id,)
        )
        conn.commit()
        if cur.rowcount:
            return True, f"Removed event `{event_id}` from drop watch."
        return False, f"Event `{event_id}` was not in the drop watch list."


def list_drop_watches() -> List[Dict[str, Any]]:
    """Return all active (non-expired) drop watches."""
    _init_db()
    now = time.time()
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT event_id, artist, name, url, added_unix, expires_at_unix,
                   last_status, last_check_unix, last_price_min, last_price_max
            FROM drop_watch_events
            WHERE expires_at_unix > ?
            ORDER BY added_unix ASC
            """,
            (now,),
        ).fetchall()
    return [
        {
            "event_id": r[0],
            "artist": r[1],
            "name": r[2],
            "url": r[3],
            "added_unix": r[4],
            "expires_at_unix": r[5],
            "last_status": r[6],
            "last_check_unix": r[7],
            "last_price_min": r[8],
            "last_price_max": r[9],
        }
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Track 2 public API — global scan helpers (sync, for slash commands)
# ---------------------------------------------------------------------------

def scan_new_onsales(hours: float = 1.0) -> Dict[str, Any]:
    """
    Return music events whose on-sale window started in the last `hours`.
    Deduplicates via drop_global_state.json so each event alerts only once.

    Returns {"since": str, "until": str, "new": [...], "updated": [...]}
    """
    if not global_scan_is_available():
        raise RuntimeError("Global scan unavailable (missing requests or TICKETMASTER_API_KEY).")

    now_utc = datetime.now(timezone.utc)
    since_dt = now_utc - timedelta(hours=hours)
    since_str = _fmt_utc(since_dt)
    until_str = _fmt_utc(now_utc)

    params: Dict[str, Any] = {
        "countryCode": ",".join(COUNTRY_CODES),
        "segmentName": "Music",
        "onsaleStartDateTime": since_str,
        "onsaleEndDateTime": until_str,
        "size": 200,
        "sort": "date,asc",
    }

    raw_events = _paginate_events(params)
    state = _load_global_state()
    new_state: Dict[str, Any] = dict(state)
    new_events: List[Dict[str, Any]] = []
    updated_events: List[Dict[str, Any]] = []

    for e in raw_events:
        norm = _normalize_event(e)
        eid = norm.get("id")
        if not eid:
            continue

        h = _event_hash(norm)
        old = state.get(eid)

        if not old:
            norm["change_type"] = "NEW"
            new_events.append(norm)
        elif old.get("hash") != h:
            norm["change_type"] = "UPDATED"
            norm["prev_status"] = old.get("status") or "unknown"
            updated_events.append(norm)

        new_state[eid] = {
            "hash": h,
            "status": norm.get("status") or "unknown",
            "last_seen": until_str,
        }

    # Prune state entries older than 30 days to keep file small
    cutoff = _fmt_utc(now_utc - timedelta(days=30))
    new_state = {k: v for k, v in new_state.items() if v.get("last_seen", "") >= cutoff}

    _save_global_state(new_state)

    return {
        "since": since_str,
        "until": until_str,
        "new": new_events,
        "updated": updated_events,
    }


def scan_tomorrow_onsales() -> Dict[str, Any]:
    """
    Return music events whose on-sale window starts tomorrow (UTC).
    Does NOT update dedup state — this is a preview only.

    Returns {"date": str, "events": [...]}
    """
    if not global_scan_is_available():
        raise RuntimeError("Global scan unavailable (missing requests or TICKETMASTER_API_KEY).")

    now_utc = datetime.now(timezone.utc).date()
    tomorrow = now_utc + timedelta(days=1)
    start_dt = datetime(tomorrow.year, tomorrow.month, tomorrow.day, 0, 0, 0, tzinfo=timezone.utc)
    end_dt = start_dt + timedelta(days=1)

    params: Dict[str, Any] = {
        "countryCode": ",".join(COUNTRY_CODES),
        "segmentName": "Music",
        "onsaleStartDateTime": _fmt_utc(start_dt),
        "onsaleEndDateTime": _fmt_utc(end_dt),
        "size": 200,
        "sort": "date,asc",
    }

    raw_events = _paginate_events(params, max_results=50)
    events = [_normalize_event(e) for e in raw_events]

    return {"date": tomorrow.isoformat(), "events": events}


# ---------------------------------------------------------------------------
# Status detection (Track 1 helper)
# ---------------------------------------------------------------------------

def _extract_status(event: Dict[str, Any]) -> str:
    """Extract normalized status from a TM event dict."""
    try:
        code = (
            event.get("dates", {})
                 .get("status", {})
                 .get("code") or ""
        ).lower().strip()
        if code:
            return code
    except Exception:
        pass

    # Fallback: check sales window
    try:
        public = event.get("sales", {}).get("public", {})
        start = public.get("startDateTime", "")
        end = public.get("endDateTime", "")
        now_iso = _fmt_utc(datetime.now(timezone.utc))
        if start and end:
            if now_iso >= start and now_iso <= end:
                return "onsale"
            elif now_iso < start:
                return "presale"
            else:
                return "offsale"
    except Exception:
        pass

    return "unknown"


def _is_dropped(old_status: str, new_status: str) -> bool:
    """True when the transition looks like tickets just became available."""
    return new_status == "onsale" and old_status != "onsale"


def _extract_prices(event: Dict[str, Any]) -> Tuple[Optional[float], Optional[float]]:
    """Return (min_price, max_price) from a TM event dict."""
    price_ranges = event.get("priceRanges") or []
    if not price_ranges:
        return None, None
    try:
        pmin = min(p.get("min", 0) for p in price_ranges if p.get("min") is not None)
        pmax = max(p.get("max", 0) for p in price_ranges if p.get("max") is not None)
        return float(pmin), float(pmax)
    except Exception:
        return None, None


# ---------------------------------------------------------------------------
# Background loop
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Aggressive mode — public API
# ---------------------------------------------------------------------------

def register_aggressive_target(event_id: str, onsale_unix: float, url: str = "") -> None:
    """
    Register an event for aggressive polling.
    Called from bot slash commands or automatically from _poll_watched_events
    when an on-sale time is detected within AGGRESSIVE_WINDOW_SECS.
    """
    if not AGGRESSIVE_ENABLED:
        return
    if event_id not in _aggressive_targets:
        logger.info("drop_catcher: aggressive target registered — %s (onsale=%s)", event_id, onsale_unix)
    _aggressive_targets[event_id] = {
        "onsale_unix": onsale_unix,
        "url": url,
        "warmed": False,
        "added_unix": time.time(),
    }


def list_aggressive_targets() -> List[Dict[str, Any]]:
    return [{"event_id": k, **v} for k, v in _aggressive_targets.items()]


def _prune_aggressive_targets() -> None:
    """Remove targets that are more than AGGRESSIVE_EXPIRE_SECS past their on-sale time."""
    now = time.time()
    expired = [
        eid for eid, t in _aggressive_targets.items()
        if now > t["onsale_unix"] + AGGRESSIVE_EXPIRE_SECS
    ]
    for eid in expired:
        logger.info("drop_catcher: aggressive target expired — %s", eid)
        del _aggressive_targets[eid]


def _prewarm_url(url: str) -> None:
    """Non-blocking HTTP HEAD to warm the TCP connection to TM."""
    if not url:
        return
    try:
        import requests as _req
        _req.head(url, timeout=5, allow_redirects=False)
        logger.debug("drop_catcher: pre-warmed %s", url)
    except Exception:
        pass


async def _aggressive_poll_cycle(
    discord_post: Callable[[Any], Awaitable[None]],
) -> None:
    """
    Single cycle of aggressive polling for all active targets.
    Runs every AGGRESSIVE_POLL_SECONDS seconds when targets exist.
    """
    _prune_aggressive_targets()
    now = time.time()

    for event_id, target in list(_aggressive_targets.items()):
        onsale_unix = target["onsale_unix"]
        url = target["url"]

        # Pre-warm at T-PREWARM_SECS
        if not target["warmed"] and 0 < (onsale_unix - now) <= AGGRESSIVE_PREWARM_SECS:
            logger.info("drop_catcher: pre-warming %s (T-%ds)", event_id, int(onsale_unix - now))
            await asyncio.to_thread(_prewarm_url, url)
            _aggressive_targets[event_id]["warmed"] = True

        # Fast-poll the event
        if not is_available():
            continue
        try:
            event = await asyncio.to_thread(_ta.get_event_details, event_id)
        except Exception as e:
            logger.warning("drop_catcher: aggressive poll failed for %s: %s", event_id, e)
            continue

        if not isinstance(event, dict) or not event:
            continue

        norm = _normalize_event(event)
        new_status = norm["status"] or _extract_status(event)
        old_status = "unknown"  # We don't track state here — drop_watch_loop handles persistence

        if _is_dropped(old_status, new_status):
            # Update DB via the normal watch path (avoid double-alert by checking drop_watch_events)
            logger.info("drop_catcher: AGGRESSIVE DROP %s (%s)", event_id, new_status)
            alert_data: Dict[str, Any] = {
                "type": "sale_start",
                "event_id": event_id,
                "name": norm.get("name") or f"Event {event_id}",
                "artist": norm.get("artist") or "",
                "url": norm.get("url") or url,
                "city": norm.get("city") or "",
                "event_local_date": norm.get("event_local_date") or "",
                "price_min": norm.get("price_min"),
                "price_max": norm.get("price_max"),
                "source": "Ticketmaster",
                "text": f"🚀 AGGRESSIVE DROP — {norm.get('name') or event_id} is now ON SALE",
            }
            try:
                await discord_post(alert_data)
            except Exception as e:
                logger.warning("drop_catcher: aggressive discord_post failed: %s", e)
            # Remove target after firing so it doesn't spam
            _aggressive_targets.pop(event_id, None)

        await asyncio.sleep(0.5)


async def drop_watch_loop(
    discord_post: Callable[[Any], Awaitable[None]],
    stop_event: Optional[asyncio.Event] = None,
) -> None:
    """
    Async background loop running both tracks:
      - Track 1: poll each registered event ID for availability changes
      - Track 2: periodic global scan for any new US/CA music on-sales
    """
    if not is_available() and not global_scan_is_available():
        logger.info("drop_catcher: neither track available; loop exiting.")
        return

    _init_db()
    logger.info(
        "drop_catcher: background loop started (track1=%ss, track2=%ss, global_scan=%s).",
        DROP_POLL_SECONDS,
        GLOBAL_SCAN_INTERVAL,
        GLOBAL_SCAN_ENABLED,
    )

    last_global_scan = 0.0
    last_normal_poll = 0.0

    while True:
        if stop_event and stop_event.is_set():
            logger.info("drop_catcher: stop_event set; exiting loop.")
            return

        now = time.time()

        # --- Aggressive mode: fast poll active targets ---
        if AGGRESSIVE_ENABLED and _aggressive_targets:
            try:
                await _aggressive_poll_cycle(discord_post)
            except Exception as e:
                logger.warning("drop_catcher: aggressive poll error: %s", e)

        # --- Track 1: watched event IDs (normal cadence) ---
        if is_available() and (now - last_normal_poll) >= DROP_POLL_SECONDS:
            try:
                await _poll_watched_events(discord_post)
                last_normal_poll = time.time()
            except Exception as e:
                logger.warning("drop_catcher: track-1 poll error: %s", e)

        # --- Track 2: global new on-sale scan ---
        if global_scan_is_available() and (now - last_global_scan) >= GLOBAL_SCAN_INTERVAL:
            try:
                await _poll_global_scan(discord_post)
                last_global_scan = time.time()
            except Exception as e:
                logger.warning("drop_catcher: track-2 global scan error: %s", e)

        # Sleep: short if aggressive targets active, normal otherwise
        if AGGRESSIVE_ENABLED and _aggressive_targets:
            await asyncio.sleep(AGGRESSIVE_POLL_SECONDS)
        else:
            slept = 0
            sleep_target = min(DROP_POLL_SECONDS, GLOBAL_SCAN_INTERVAL)
            while slept < sleep_target:
                if stop_event and stop_event.is_set():
                    return
                # Wake up early if aggressive targets appear
                if AGGRESSIVE_ENABLED and _aggressive_targets:
                    break
                chunk = min(15, sleep_target - slept)
                await asyncio.sleep(chunk)
                slept += chunk


def _parse_sell_through(seatmap_txt: Optional[str]) -> float:
    """Extract sell-through % from seatmap enrichment text, or return 0."""
    if not seatmap_txt:
        return 0.0
    try:
        m = re.search(r"(\d+(?:\.\d+)?)%\s+open", seatmap_txt)
        if m:
            return max(0.0, 100.0 - float(m.group(1)))
    except Exception:
        pass
    return 0.0


async def _poll_watched_events(discord_post: Callable[[Any], Awaitable[None]]) -> None:
    """Track 1: check each individually-registered event ID."""
    now = time.time()

    # Purge expired watches
    with _connect() as conn:
        conn.execute("DELETE FROM drop_watch_events WHERE expires_at_unix <= ?", (now,))
        conn.commit()

    watches = list_drop_watches()
    for row in watches:
        event_id = row["event_id"]
        try:
            event = await asyncio.to_thread(_ta.get_event_details, event_id)
        except Exception as e:
            logger.warning("drop_catcher: failed to fetch event %s: %s", event_id, e)
            await asyncio.sleep(1)
            continue

        if not isinstance(event, dict) or not event:
            await asyncio.sleep(1)
            continue

        # Use rich normalization for enriched fields
        norm = _normalize_event(event)
        new_status = norm["status"] or _extract_status(event)
        new_name = norm["name"] or row.get("name") or ""
        new_url = norm["url"] or row.get("url") or ""
        new_price_min = norm["price_min"]
        new_price_max = norm["price_max"]

        old_status = row.get("last_status") or "unknown"

        with _connect() as conn:
            conn.execute(
                """
                UPDATE drop_watch_events
                SET last_status = ?, last_check_unix = ?, name = ?, url = ?,
                    last_price_min = ?, last_price_max = ?
                WHERE event_id = ?
                """,
                (new_status, now, new_name, new_url, new_price_min, new_price_max, event_id),
            )
            conn.commit()

        # Auto-register aggressive target when on-sale is within AGGRESSIVE_WINDOW_SECS
        if AGGRESSIVE_ENABLED and new_status not in ("onsale",):
            onsale_unix = norm.get("onsale_start_unix")
            if onsale_unix:
                secs_until = onsale_unix - time.time()
                if 0 < secs_until <= AGGRESSIVE_WINDOW_SECS:
                    register_aggressive_target(event_id, onsale_unix, url=new_url)
                    logger.info(
                        "drop_catcher: auto-registered aggressive target %s (T-%ds)",
                        event_id, int(secs_until),
                    )

        if _is_dropped(old_status, new_status):
            # Try seatmap enrichment — also extracts sell_through for arbitrage
            seatmap_txt = await _enrich_with_seatmap(event_id)
            sell_through = _parse_sell_through(seatmap_txt)

            enriched_row = {
                **row,
                "name": new_name,
                "url": new_url,
                "artist": norm.get("artist") or row.get("artist") or "",
                "city": norm.get("city") or "",
                "event_local_date": norm.get("event_local_date") or "",
                "price_min": new_price_min,
                "price_max": new_price_max,
            }
            # Build structured alert dict — bot uses this for rich embeds.
            # "text" fallback is used by plain-text consumers.
            alert_data = {
                "type": "sale_start",
                "event_id": event_id,
                "name": new_name,
                "artist": norm.get("artist") or row.get("artist") or "",
                "url": new_url,
                "city": norm.get("city") or "",
                "country": norm.get("country") or "",
                "event_local_date": norm.get("event_local_date") or "",
                "price_min": new_price_min,
                "price_max": new_price_max,
                "old_status": old_status,
                "new_status": new_status,
                "seatmap_txt": seatmap_txt,
                "sell_through_pct": sell_through,
                "source": "Ticketmaster",
                "text": _format_drop_alert(enriched_row, new_status, seatmap_txt, sell_through),
            }
            logger.info(
                "drop_catcher: DROP event_id=%s (%s → %s)", event_id, old_status, new_status
            )
            try:
                await discord_post(alert_data)
            except Exception as e:
                logger.warning("drop_catcher: discord_post failed: %s", e)

        await asyncio.sleep(1)  # rate-limit TM requests


async def _poll_global_scan(discord_post: Callable[[Any], Awaitable[None]]) -> None:
    """Track 2: fire alerts for any music event that just went on-sale globally."""
    result = await asyncio.to_thread(scan_new_onsales, GLOBAL_SCAN_HOURS)
    new_items = result.get("new") or []
    updated_items = result.get("updated") or []

    for norm in new_items:
        alert_data = {
            "type": "new_event",
            "event_id": norm.get("id") or "",
            "name": norm.get("name") or "Unknown Event",
            "artist": norm.get("artist") or "",
            "url": norm.get("url") or "",
            "city": norm.get("city") or "",
            "country": norm.get("country") or "",
            "event_local_date": norm.get("event_local_date") or "",
            "price_min": norm.get("price_min"),
            "price_max": norm.get("price_max"),
            "source": "Ticketmaster",
            "text": _format_global_drop_alert(norm, "NEW"),
        }
        logger.info("drop_catcher: global NEW onsale — %s (%s)", norm.get("id"), norm.get("name"))
        try:
            await discord_post(alert_data)
        except Exception as e:
            logger.warning("drop_catcher: discord_post (global new) failed: %s", e)
        await asyncio.sleep(0.5)

    for norm in updated_items:
        # Only alert on status-change updates (not just price drift)
        if norm.get("prev_status") != norm.get("status"):
            alert_data = {
                "type": "restock",
                "event_id": norm.get("id") or "",
                "name": norm.get("name") or "Unknown Event",
                "artist": norm.get("artist") or "",
                "url": norm.get("url") or "",
                "city": norm.get("city") or "",
                "country": norm.get("country") or "",
                "event_local_date": norm.get("event_local_date") or "",
                "price_min": norm.get("price_min"),
                "price_max": norm.get("price_max"),
                "old_status": norm.get("prev_status") or "",
                "new_status": norm.get("status") or "",
                "source": "Ticketmaster",
                "text": _format_global_drop_alert(norm, "UPDATED"),
            }
            logger.info(
                "drop_catcher: global UPDATED — %s (%s → %s)",
                norm.get("id"), norm.get("prev_status"), norm.get("status"),
            )
            try:
                await discord_post(alert_data)
            except Exception as e:
                logger.warning("drop_catcher: discord_post (global update) failed: %s", e)
            await asyncio.sleep(0.5)
