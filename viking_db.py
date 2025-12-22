# viking_db.py

"""
SQLite storage for Viking AI.

Public functions used by bot.py:
  - init_db()
  - store_event_from_tm(ev: dict, artist: str)
  - store_news_item(artist: str, title: str, url: str, source: str = "news")
  - get_artist_intel(artist: str, limit_events: int = 10, limit_news: int = 10) -> dict
  - get_artist_counts_time_aware(artist: str) -> dict

Compatibility:
  - get_db_connection()  (used by verified_fan_monitor)
"""

from __future__ import annotations

import os
import sqlite3
import time
import math
from typing import Dict, Any, List

DB_PATH = os.path.join(os.path.dirname(__file__), "viking_ai.db")


def _connect():
    return sqlite3.connect(DB_PATH)


def get_db_connection():
    """Compatibility: used by verified_fan_monitor."""
    return _connect()


def init_db() -> None:
    conn = _connect()
    cur = conn.cursor()

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            artist TEXT,
            tm_event_id TEXT,
            name TEXT,
            url TEXT,
            date TEXT,
            city TEXT,
            country TEXT,
            venue TEXT,
            source TEXT,
            created_at REAL
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS news (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            artist TEXT,
            title TEXT,
            url TEXT,
            source TEXT,
            created_at REAL
        )
        """
    )

    _ensure_column(cur, "events", "tm_event_id", "TEXT")
    _ensure_column(cur, "events", "source", "TEXT")
    _ensure_column(cur, "events", "created_at", "REAL")
    _ensure_column(cur, "news", "source", "TEXT")
    _ensure_column(cur, "news", "created_at", "REAL")

    cur.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_events_artist_event
        ON events(artist, tm_event_id)
        """
    )
    cur.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_news_artist_url
        ON news(artist, url)
        """
    )

    conn.commit()
    conn.close()


def _ensure_column(cur: sqlite3.Cursor, table: str, column: str, coltype: str) -> None:
    cur.execute(f"PRAGMA table_info({table})")
    cols = [row[1] for row in cur.fetchall()]
    if column not in cols:
        cur.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}")


def store_event_from_tm(ev: Dict[str, Any], artist: str) -> None:
    conn = _connect()
    cur = conn.cursor()

    eid = ev.get("id")
    name = ev.get("name")
    url = ev.get("url")
    dates = (ev.get("dates", {}) or {})
    start = (dates.get("start", {}) or {})
    date = start.get("localDate")

    ven = (ev.get("_embedded", {}).get("venues", [{}])[0] or {})
    venue_name = ven.get("name")
    city = (ven.get("city", {}) or {}).get("name")
    country = (ven.get("country", {}) or {}).get("name")

    created_at = time.time()

    cur.execute(
        """
        INSERT OR IGNORE INTO events
          (artist, tm_event_id, name, url, date, city, country, venue, source, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (artist, eid, name, url, date, city, country, venue_name, "ticketmaster", created_at),
    )

    conn.commit()
    conn.close()


def store_news_item(artist: str, title: str, url: str, source: str = "news") -> None:
    conn = _connect()
    cur = conn.cursor()
    created_at = time.time()

    cur.execute(
        """
        INSERT OR IGNORE INTO news
          (artist, title, url, source, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (artist, title, url, source, created_at),
    )

    conn.commit()
    conn.close()


def get_artist_intel(artist: str, limit_events: int = 10, limit_news: int = 10) -> Dict[str, Any]:
    conn = _connect()
    cur = conn.cursor()

    cur.execute(
        """
        SELECT tm_event_id, name, url, date, city, country, venue, created_at
        FROM events
        WHERE artist = ?
        ORDER BY
          CASE WHEN date IS NOT NULL THEN date ELSE '' END DESC,
          created_at DESC
        LIMIT ?
        """,
        (artist, limit_events),
    )
    ev_rows = cur.fetchall()

    events: List[Dict[str, Any]] = []
    for row in ev_rows:
        tm_event_id, name, url, date, city, country, venue, created_at = row
        events.append(
            {
                "tm_event_id": tm_event_id,
                "name": name,
                "url": url,
                "date": date,
                "city": city,
                "country": country,
                "venue": venue,
                "created_at": created_at,
            }
        )

    cur.execute(
        """
        SELECT title, url, source, created_at
        FROM news
        WHERE artist = ?
        ORDER BY created_at DESC
        LIMIT ?
        """,
        (artist, limit_news),
    )
    nw_rows = cur.fetchall()

    news: List[Dict[str, Any]] = []
    for row in nw_rows:
        title, url, source, created_at = row
        news.append({"title": title, "url": url, "source": source, "created_at": created_at})

    conn.close()
    return {"artist": artist, "events": events, "news": news}


def get_artist_counts_time_aware(artist: str) -> Dict[str, float]:
    """
    Basic time-aware “demand-ish” counters from DB history.
    """
    conn = _connect()
    cur = conn.cursor()

    cur.execute("SELECT created_at FROM events WHERE artist = ?", (artist,))
    event_times = [row[0] for row in cur.fetchall()]

    cur.execute("SELECT created_at FROM news WHERE artist = ?", (artist,))
    news_times = [row[0] for row in cur.fetchall()]

    conn.close()

    now = time.time()

    def decay_count(ts_list: List[float], half_life_days: float) -> float:
        if not ts_list:
            return 0.0
        hl = half_life_days * 86400.0
        total = 0.0
        for t in ts_list:
            age = max(0.0, now - float(t))
            total += 0.5 ** (age / hl) if hl > 0 else 1.0
        return float(total)

    return {
        "events_7d": decay_count(event_times, 7),
        "events_30d": decay_count(event_times, 30),
        "news_7d": decay_count(news_times, 7),
        "news_30d": decay_count(news_times, 30),
    }
import json
import sqlite3
import time

def ensure_price_monitor_tables() -> None:
    conn = get_db_connection()
    try:
        cur = conn.cursor()

        cur.execute("""
        CREATE TABLE IF NOT EXISTS price_watches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            artist TEXT NOT NULL,
            event TEXT NOT NULL,
            source_url TEXT,
            channel_id INTEGER NOT NULL,
            guild_id INTEGER,
            target_profit_margin_pct REAL NOT NULL DEFAULT 25,
            target_min_profit_usd REAL,
            baseline_face_price_usd REAL,
            resale_urls_json TEXT DEFAULT '[]',
            created_at INTEGER NOT NULL
        )
        """)

        cur.execute("""
        CREATE TABLE IF NOT EXISTS price_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            watch_id INTEGER NOT NULL,
            ts INTEGER NOT NULL,
            face_price_usd REAL,
            resale_min_usd REAL,
            FOREIGN KEY(watch_id) REFERENCES price_watches(id)
        )
        """)

        conn.commit()
    finally:
        conn.close()

def add_price_watch(
    artist: str,
    event: str,
    source_url: str,
    channel_id: int,
    guild_id: int | None,
    target_profit_margin_pct: float,
    target_min_profit_usd: float | None,
    baseline_face_price_usd: float | None,
    resale_urls: list[str] | None = None,
) -> int:
    ensure_price_monitor_tables()
    conn = get_db_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO price_watches (
              artist, event, source_url, channel_id, guild_id,
              target_profit_margin_pct, target_min_profit_usd,
              baseline_face_price_usd, resale_urls_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                artist,
                event,
                source_url,
                int(channel_id),
                int(guild_id) if guild_id is not None else None,
                float(target_profit_margin_pct),
                float(target_min_profit_usd) if target_min_profit_usd is not None else None,
                float(baseline_face_price_usd) if baseline_face_price_usd is not None else None,
                json.dumps(resale_urls or []),
                int(time.time()),
            ),
        )
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()

def list_price_watches() -> list[dict]:
    ensure_price_monitor_tables()
    conn = get_db_connection()
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.cursor()
        cur.execute("SELECT * FROM price_watches ORDER BY id DESC")
        return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()

def add_price_snapshot(
    watch_id: int,
    face_price_usd: float | None,
    resale_min_usd: float | None,
) -> None:
    ensure_price_monitor_tables()
    conn = get_db_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO price_snapshots (watch_id, ts, face_price_usd, resale_min_usd)
            VALUES (?, ?, ?, ?)
            """,
            (
                int(watch_id),
                int(time.time()),
                float(face_price_usd) if face_price_usd is not None else None,
                float(resale_min_usd) if resale_min_usd is not None else None,
            ),
        )
        conn.commit()
    finally:
        conn.close()

