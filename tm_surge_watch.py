from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import sqlite3
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple, Callable, Awaitable

try:
    from dotenv import load_dotenv
except Exception:
    load_dotenv = None

if load_dotenv:
    load_dotenv("/opt/viking-ai/.env", override=False)

logger = logging.getLogger("tm_surge_watch")

try:
    import ticketmaster_agent
except Exception as e:
    ticketmaster_agent = None
    logger.warning("ticketmaster_agent import failed: %s", e)

try:
    import requests
except Exception:
    requests = None

DB_PATH = os.getenv("VIKING_DB_PATH", "/opt/viking-ai/viking_ai.sqlite")
TOUR_SCAN_WEBHOOK_URL = (os.getenv("TOUR_SCAN_WEBHOOK_URL") or "").strip()
TM_SURGE_POLL_SECONDS = int(os.getenv("TM_SURGE_POLL_SECONDS", "1800") or "1800")
MAX_SURGE_ARTISTS = 10

_last_request_ts = 0.0
_backoff_seconds = 0.0
_backoff_until = 0.0


def is_available() -> bool:
    return ticketmaster_agent is not None and hasattr(ticketmaster_agent, "search_events_for_artist")


def _connect() -> sqlite3.Connection:
    return sqlite3.connect(DB_PATH, timeout=30)


def _init_db() -> None:
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS tm_surge_artists (
                artist TEXT PRIMARY KEY,
                expires_at_unix REAL,
                created_unix REAL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS tm_seen_events (
                artist TEXT,
                event_id TEXT UNIQUE,
                first_seen_unix REAL,
                url TEXT,
                event_time TEXT
            )
            """
        )
        conn.commit()


def _normalize_artist(artist: str) -> str:
    return (artist or "").strip()


def _now() -> float:
    return time.time()


def add_surge_artist(artist: str, days: int = 5) -> Tuple[bool, str]:
    artist = _normalize_artist(artist)
    if not artist:
        return False, "Artist name is required."
    days = max(1, int(days or 5))
    expires_at = _now() + days * 86400

    _init_db()
    with _connect() as conn:
        active = conn.execute(
            "SELECT COUNT(1) FROM tm_surge_artists WHERE expires_at_unix > ?",
            (_now(),),
        ).fetchone()
        active_count = int(active[0]) if active else 0
        exists = conn.execute(
            "SELECT 1 FROM tm_surge_artists WHERE artist = ?",
            (artist,),
        ).fetchone()
        if not exists and active_count >= MAX_SURGE_ARTISTS:
            return False, f"Max surge artists reached ({MAX_SURGE_ARTISTS})."
        conn.execute(
            """
            INSERT INTO tm_surge_artists (artist, expires_at_unix, created_unix)
            VALUES (?, ?, ?)
            ON CONFLICT(artist) DO UPDATE SET expires_at_unix=excluded.expires_at_unix
            """,
            (artist, expires_at, _now()),
        )
        conn.commit()
    return True, f"✅ Surge watch enabled for {artist} ({days} days)."


def remove_surge_artist(artist: str) -> Tuple[bool, str]:
    artist = _normalize_artist(artist)
    if not artist:
        return False, "Artist name is required."
    _init_db()
    with _connect() as conn:
        cur = conn.execute("DELETE FROM tm_surge_artists WHERE artist = ?", (artist,))
        conn.commit()
    if cur.rowcount:
        return True, f"✅ Surge watch removed for {artist}."
    return False, f"No surge watch found for {artist}."


def list_surge_artists() -> List[Dict[str, Any]]:
    _init_db()
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT artist, expires_at_unix, created_unix
            FROM tm_surge_artists
            WHERE expires_at_unix > ?
            ORDER BY expires_at_unix ASC
            """,
            (_now(),),
        ).fetchall()
    return [
        {
            "artist": row[0],
            "expires_at_unix": row[1],
            "created_unix": row[2],
        }
        for row in rows
    ]


def _format_event_time(event: Dict[str, Any]) -> Tuple[Optional[str], Optional[str]]:
    date = (event.get("date") or "").strip()
    tm = (event.get("time") or "").strip()
    if not date:
        return None, None
    if tm:
        local = f"{date} {tm}"
    else:
        local = date
    utc = None
    if date and tm:
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
            try:
                dt = datetime.strptime(local, fmt)
                utc = dt.replace(tzinfo=timezone.utc).isoformat()
                break
            except ValueError:
                continue
    return local, utc


def _record_event_if_new(
    artist: str,
    event_id: str,
    url: Optional[str],
    event_time: Optional[str],
) -> bool:
    if not event_id:
        return False
    _init_db()
    with _connect() as conn:
        try:
            conn.execute(
                """
                INSERT INTO tm_seen_events (artist, event_id, first_seen_unix, url, event_time)
                VALUES (?, ?, ?, ?, ?)
                """,
                (artist, event_id, _now(), url, event_time),
            )
            conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False


def _is_rate_limited(exc: Exception) -> bool:
    response = getattr(exc, "response", None)
    if response is not None and getattr(response, "status_code", None) == 429:
        return True
    return False


def _apply_backoff() -> None:
    global _backoff_seconds, _backoff_until
    if _backoff_seconds <= 0:
        _backoff_seconds = 5
    else:
        _backoff_seconds = min(_backoff_seconds * 2, 300)
    _backoff_until = _now() + _backoff_seconds


def _reset_backoff() -> None:
    global _backoff_seconds, _backoff_until
    _backoff_seconds = 0
    _backoff_until = 0


def _surge_message(artist: str, event: Dict[str, Any], event_time_local: Optional[str]) -> str:
    name = event.get("name") or "New event"
    url = event.get("url") or ""
    venue = event.get("venue") or ""
    city = event.get("city") or ""
    lines = [f"🎫 New Ticketmaster event for **{artist}**", name]
    if event_time_local:
        lines.append(f"🗓️ {event_time_local}")
    if venue or city:
        location = ", ".join([p for p in [venue, city] if p])
        lines.append(f"📍 {location}")
    if url:
        lines.append(url)
    return "\n".join([l for l in lines if l]).strip()


def _post_webhook(payload: Dict[str, Any]) -> None:
    if not TOUR_SCAN_WEBHOOK_URL:
        return
    if not requests:
        return

    # Discord webhooks require "content" or "embeds".
    # If we're given an internal payload, convert to a readable content message.
    try:
        if "content" not in payload:
            artist = payload.get("artist") or "Artist"
            url = payload.get("url") or ""
            event_time = payload.get("event_time_local") or ""
            msg = f"🎫 New Ticketmaster event for **{artist}**"
            if event_time:
                msg += f"\n🗓️ {event_time}"
            if url:
                msg += f"\n{url}"
            payload = {"content": msg}

        requests.post(TOUR_SCAN_WEBHOOK_URL, json=payload, timeout=15).raise_for_status()
    except Exception as exc:
        logger.warning("Surge webhook failed: %s", exc)

async def _throttle() -> None:
    global _last_request_ts
    now = time.monotonic()
    elapsed = now - _last_request_ts
    if elapsed < 1.0:
        await asyncio.sleep(1.0 - elapsed)
    _last_request_ts = time.monotonic()


async def _wait_for_backoff() -> None:
    now = _now()
    if _backoff_until > now:
        await asyncio.sleep(_backoff_until - now)


async def _fetch_events(artist: str) -> List[Dict[str, Any]]:
    if not is_available():
        return []
    await _wait_for_backoff()
    await _throttle()
    try:
        events = await asyncio.to_thread(ticketmaster_agent.search_events_for_artist, artist, 25)
        _reset_backoff()
        return events or []
    except Exception as exc:
        if _is_rate_limited(exc):
            _apply_backoff()
            logger.warning("Ticketmaster rate limited, backing off for %.1fs", _backoff_seconds)
        else:
            logger.warning("Ticketmaster fetch failed for %s: %s", artist, exc)
        return []


async def surge_watch_loop(
    discord_post: Optional[Callable[[str], Awaitable[None]]] = None,
    stop_event: Optional[asyncio.Event] = None,
) -> None:
    poll_seconds = max(60, TM_SURGE_POLL_SECONDS)
    logger.info("TM surge watch loop started (%ss interval).", poll_seconds)
    while True:
        if stop_event and stop_event.is_set():
            logger.info("TM surge watch stop event received.")
            return
        artists = list_surge_artists()
        if not artists:
            await asyncio.sleep(poll_seconds)
            continue
        for entry in artists:
            if stop_event and stop_event.is_set():
                return
            artist = entry["artist"]
            await asyncio.sleep(random.uniform(0, 3))
            events = await _fetch_events(artist)
            for event in events:
                event_id = (event.get("id") or "").strip()
                url = (event.get("url") or "").strip()
                event_time_local, event_time_utc = _format_event_time(event)
                if not _record_event_if_new(artist, event_id, url, event_time_local):
                    continue
                payload = {
                    "type": "tm_added_event",
                    "artist": artist,
                    "event_id": event_id,
                    "url": url,
                    "event_time_local": event_time_local,
                    "event_time_utc": event_time_utc,
                    "venue": event.get("venue"),
                    "city": event.get("city"),
                    "detected_at": datetime.now(tz=timezone.utc).isoformat(),
                }
                _post_webhook(payload)
                if discord_post:
                    try:
                        await discord_post(_surge_message(artist, event, event_time_local))
                    except Exception as exc:
                        logger.warning("Discord surge post failed: %s", exc)
        await asyncio.sleep(poll_seconds)
