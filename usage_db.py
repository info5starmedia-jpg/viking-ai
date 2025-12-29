"""Usage logging + tier overrides for Viking AI."""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
from typing import Any, Dict, Iterable, List, Optional

logger = logging.getLogger("viking_ai.usage_db")

DB_PATH = os.getenv("VIKING_USAGE_DB_PATH", "/opt/viking-ai/viking_ai.sqlite")


def _connect() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    return sqlite3.connect(DB_PATH)


def init_db() -> None:
    conn = _connect()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS usage_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts INTEGER,
                guild_id TEXT,
                channel_id TEXT,
                user_id TEXT,
                command TEXT,
                ok INTEGER,
                latency_ms INTEGER,
                extra_json TEXT
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS guild_tiers (
                guild_id TEXT PRIMARY KEY,
                tier TEXT,
                updated_ts INTEGER
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


def record_usage(
    command: str,
    guild_id: Optional[str],
    channel_id: Optional[str],
    user_id: Optional[str],
    ok: bool,
    latency_ms: int,
    extra: Optional[Dict[str, Any]] = None,
) -> None:
    try:
        init_db()
        payload = json.dumps(extra or {}, ensure_ascii=False)
        conn = _connect()
        try:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO usage_events (
                    ts, guild_id, channel_id, user_id, command, ok, latency_ms, extra_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    int(time.time()),
                    str(guild_id) if guild_id is not None else "",
                    str(channel_id) if channel_id is not None else "",
                    str(user_id) if user_id is not None else "",
                    command,
                    1 if ok else 0,
                    int(latency_ms),
                    payload,
                ),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception as exc:
        logger.warning("Usage record failed: %s", exc)


def _iter_rows(cur: sqlite3.Cursor) -> Iterable[sqlite3.Row]:
    for row in cur.fetchall():
        yield row


def list_recent_artist_keys(days: int = 7, limit: int = 30) -> List[str]:
    cutoff = int(time.time() - max(days, 1) * 86400)
    init_db()
    conn = _connect()
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT ts, extra_json
            FROM usage_events
            WHERE ts >= ?
              AND command IN ('intel', 'news_now', 'events')
            ORDER BY ts DESC
            LIMIT ?
            """,
            (cutoff, int(limit) * 5),
        )
        seen = set()
        ordered: List[str] = []
        for row in _iter_rows(cur):
            extra_json = row["extra_json"]
            if not extra_json:
                continue
            try:
                payload = json.loads(extra_json)
            except Exception:
                continue
            artist_key = (payload.get("artist_key") or "").strip()
            if not artist_key:
                artist_key = (payload.get("artist") or "").strip().lower()
            if not artist_key or artist_key in seen:
                continue
            seen.add(artist_key)
            ordered.append(artist_key)
            if len(ordered) >= int(limit):
                break
        return ordered
    finally:
        conn.close()


def get_guild_tier_override(guild_id: str) -> Optional[str]:
    if not guild_id:
        return None
    init_db()
    conn = _connect()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT tier FROM guild_tiers WHERE guild_id = ?",
            (str(guild_id),),
        )
        row = cur.fetchone()
        if row and row[0]:
            return str(row[0]).strip().upper()
        return None
    finally:
        conn.close()
