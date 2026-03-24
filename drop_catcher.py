"""
drop_catcher.py

Monitors tracked Ticketmaster event IDs for inventory availability changes
("drops"). When a previously offsale/unavailable event goes on-sale, a
Discord alert is fired.

Env vars (all optional, have safe defaults):
  VIKING_DB_PATH         – SQLite path (default /opt/viking-ai/viking_ai.sqlite)
  DROP_CATCHER_POLL_SECONDS – poll interval in seconds (default 300 = 5 min)
  DROP_CATCH_MAX_WATCHES – max simultaneous watches (default 20)
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sqlite3
import time
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

try:
    from dotenv import load_dotenv
except Exception:
    load_dotenv = None  # type: ignore

if load_dotenv:
    load_dotenv("/opt/viking-ai/.env", override=False)

logger = logging.getLogger("drop_catcher")

try:
    import ticketmaster_agent as _ta  # type: ignore
except Exception as _e:
    _ta = None  # type: ignore
    logger.warning("ticketmaster_agent import failed in drop_catcher: %s", _e)

DB_PATH = os.getenv("VIKING_DB_PATH", "/opt/viking-ai/viking_ai.sqlite")
DROP_POLL_SECONDS = int(os.getenv("DROP_CATCHER_POLL_SECONDS", "300") or "300")
MAX_WATCHES = int(os.getenv("DROP_CATCH_MAX_WATCHES", "20") or "20")

# Status codes that mean "no tickets available"
_OFFSALE_CODES = {"offsale", "cancelled", "postponed", "rescheduled"}

# ---------------------------------------------------------------------------
# DB helpers
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
                last_check_unix REAL
            )
            """
        )
        conn.commit()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def is_available() -> bool:
    """True if the TM agent is loaded and can query events."""
    return _ta is not None and hasattr(_ta, "get_event_details")


def add_drop_watch(event_id: str, artist: str = "", days: int = 7) -> Tuple[bool, str]:
    """
    Register an event ID to watch for ticket availability drops.

    Returns (ok, message).
    """
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
                (event_id, artist, name, url, added_unix, expires_at_unix, last_status, last_check_unix)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (event_id, artist.strip(), "", "", time.time(), expires, "unknown", 0),
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
            SELECT event_id, artist, name, url, added_unix, expires_at_unix, last_status, last_check_unix
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
        }
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Status detection helpers
# ---------------------------------------------------------------------------

def _extract_status(event: Dict[str, Any]) -> str:
    """
    Extract a normalized availability status from a TM event dict.

    Returns one of: "onsale" | "offsale" | "cancelled" | "postponed" | "unknown"
    """
    # TM Discovery v2 uses dates.status.code
    try:
        code = (
            event.get("dates", {})
                 .get("status", {})
                 .get("code", "")
                 or ""
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
        now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
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
    """
    Returns True when the transition looks like tickets just became available.
    E.g. offsale → onsale, unknown → onsale, presale → onsale.
    """
    if new_status == "onsale" and old_status != "onsale":
        return True
    return False


def _format_drop_alert(row: Dict[str, Any], new_status: str) -> str:
    event_id = row["event_id"]
    name = row.get("name") or f"Event {event_id}"
    artist = row.get("artist") or ""
    url = row.get("url") or ""
    old_status = row.get("last_status") or "unknown"

    parts = [f"🎟️ **TICKET DROP** — {name}"]
    if artist:
        parts.append(f"Artist: **{artist}**")
    parts.append(f"Status: `{old_status}` → `{new_status}`")
    parts.append(f"Event ID: `{event_id}`")
    if url:
        parts.append(url)
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Background poll loop
# ---------------------------------------------------------------------------

async def drop_watch_loop(
    discord_post: Callable[[str], Awaitable[None]],
    stop_event: Optional[asyncio.Event] = None,
) -> None:
    """
    Async background loop. Checks each watched event for availability changes.

    Args:
        discord_post: async callable that sends a string to Discord.
        stop_event:   optional asyncio.Event to request graceful shutdown.
    """
    if not is_available():
        logger.info("drop_catcher: ticketmaster_agent not available; loop exiting.")
        return

    _init_db()
    logger.info("drop_catcher: background loop started (%ss interval).", DROP_POLL_SECONDS)

    while True:
        if stop_event and stop_event.is_set():
            logger.info("drop_catcher: stop_event set; exiting loop.")
            return

        try:
            await _poll_once(discord_post)
        except Exception as e:
            logger.warning("drop_catcher: poll error: %s", e)

        # Sleep in small increments so stop_event is checked responsively
        slept = 0
        while slept < DROP_POLL_SECONDS:
            if stop_event and stop_event.is_set():
                return
            chunk = min(15, DROP_POLL_SECONDS - slept)
            await asyncio.sleep(chunk)
            slept += chunk


async def _poll_once(discord_post: Callable[[str], Awaitable[None]]) -> None:
    """Single poll iteration: check every non-expired watched event."""
    now = time.time()
    watches = list_drop_watches()
    if not watches:
        return

    # Expire stale watches
    with _connect() as conn:
        conn.execute("DELETE FROM drop_watch_events WHERE expires_at_unix <= ?", (now,))
        conn.commit()

    for row in watches:
        if row["expires_at_unix"] <= now:
            continue  # just expired

        event_id = row["event_id"]
        try:
            event = await asyncio.to_thread(_ta.get_event_details, event_id)
        except Exception as e:
            logger.warning("drop_catcher: failed to fetch event %s: %s", event_id, e)
            continue

        if not isinstance(event, dict) or not event:
            logger.debug("drop_catcher: empty response for event %s", event_id)
            continue

        new_status = _extract_status(event)

        # Enrich name/url from live data if we don't have them yet
        name = row.get("name") or event.get("name") or ""
        url = row.get("url") or event.get("url") or ""

        old_status = row.get("last_status") or "unknown"

        with _connect() as conn:
            conn.execute(
                """
                UPDATE drop_watch_events
                SET last_status = ?, last_check_unix = ?, name = ?, url = ?
                WHERE event_id = ?
                """,
                (new_status, now, name, url, event_id),
            )
            conn.commit()

        if _is_dropped(old_status, new_status):
            row["name"] = name
            row["url"] = url
            alert = _format_drop_alert(row, new_status)
            logger.info("drop_catcher: DROP detected for %s (%s → %s)", event_id, old_status, new_status)
            try:
                await discord_post(alert)
            except Exception as e:
                logger.warning("drop_catcher: discord_post failed: %s", e)

        await asyncio.sleep(1)  # small delay between TM requests
