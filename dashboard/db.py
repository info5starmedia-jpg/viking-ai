"""
dashboard/db.py
SQLite helpers for the Viking AI Drop Catcher web dashboard.
Extends the existing VIKING_DB_PATH database with dashboard-specific tables.
"""
from __future__ import annotations

import os
import sqlite3
import time
from typing import Any, Dict, List, Optional

DB_PATH = os.getenv("VIKING_DB_PATH", "/opt/viking-ai/viking_ai.sqlite")


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    with connect() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS dc_clients (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                discord_id   TEXT UNIQUE,
                google_id    TEXT UNIQUE,
                email        TEXT,
                display_name TEXT,
                avatar_url   TEXT,
                created_at   REAL,
                last_login   REAL,
                active       INTEGER DEFAULT 1
            );

            CREATE TABLE IF NOT EXISTS dc_proxies (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                client_id  INTEGER NOT NULL REFERENCES dc_clients(id) ON DELETE CASCADE,
                proxy      TEXT NOT NULL,
                created_at REAL
            );

            CREATE TABLE IF NOT EXISTS dc_monitors (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                client_id       INTEGER NOT NULL REFERENCES dc_clients(id) ON DELETE CASCADE,
                url             TEXT NOT NULL,
                label           TEXT DEFAULT '',
                discord_webhook TEXT DEFAULT '',
                last_checked    REAL,
                last_status     TEXT DEFAULT 'pending',
                alert_count     INTEGER DEFAULT 0,
                active          INTEGER DEFAULT 1,
                created_at      REAL
            );
        """)
        conn.commit()


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------

def upsert_client(
    discord_id: Optional[str] = None,
    google_id: Optional[str] = None,
    email: str = "",
    display_name: str = "",
    avatar_url: str = "",
) -> sqlite3.Row:
    now = time.time()
    with connect() as conn:
        if discord_id:
            row = conn.execute(
                "SELECT id FROM dc_clients WHERE discord_id=?", (discord_id,)
            ).fetchone()
            if row:
                conn.execute(
                    "UPDATE dc_clients SET email=?, display_name=?, avatar_url=?, last_login=? WHERE discord_id=?",
                    (email, display_name, avatar_url, now, discord_id),
                )
            else:
                conn.execute(
                    "INSERT INTO dc_clients (discord_id, email, display_name, avatar_url, created_at, last_login) VALUES (?,?,?,?,?,?)",
                    (discord_id, email, display_name, avatar_url, now, now),
                )
            conn.commit()
            return conn.execute(
                "SELECT * FROM dc_clients WHERE discord_id=?", (discord_id,)
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT id FROM dc_clients WHERE google_id=?", (google_id,)
            ).fetchone()
            if row:
                conn.execute(
                    "UPDATE dc_clients SET email=?, display_name=?, avatar_url=?, last_login=? WHERE google_id=?",
                    (email, display_name, avatar_url, now, google_id),
                )
            else:
                conn.execute(
                    "INSERT INTO dc_clients (google_id, email, display_name, avatar_url, created_at, last_login) VALUES (?,?,?,?,?,?)",
                    (google_id, email, display_name, avatar_url, now, now),
                )
            conn.commit()
            return conn.execute(
                "SELECT * FROM dc_clients WHERE google_id=?", (google_id,)
            ).fetchone()


def get_client(client_id: int) -> Optional[sqlite3.Row]:
    with connect() as conn:
        return conn.execute(
            "SELECT * FROM dc_clients WHERE id=?", (client_id,)
        ).fetchone()


# ---------------------------------------------------------------------------
# Proxies
# ---------------------------------------------------------------------------

def get_proxies(client_id: int) -> List[sqlite3.Row]:
    with connect() as conn:
        return conn.execute(
            "SELECT * FROM dc_proxies WHERE client_id=? ORDER BY created_at ASC",
            (client_id,),
        ).fetchall()


def add_proxy(client_id: int, proxy: str) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO dc_proxies (client_id, proxy, created_at) VALUES (?,?,?)",
            (client_id, proxy, time.time()),
        )
        conn.commit()


def delete_proxy(proxy_id: int, client_id: int) -> None:
    with connect() as conn:
        conn.execute(
            "DELETE FROM dc_proxies WHERE id=? AND client_id=?",
            (proxy_id, client_id),
        )
        conn.commit()


# ---------------------------------------------------------------------------
# Monitors
# ---------------------------------------------------------------------------

def get_monitors(client_id: int) -> List[sqlite3.Row]:
    with connect() as conn:
        return conn.execute(
            "SELECT * FROM dc_monitors WHERE client_id=? ORDER BY created_at ASC",
            (client_id,),
        ).fetchall()


def add_monitor(client_id: int, url: str, label: str, discord_webhook: str) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO dc_monitors (client_id, url, label, discord_webhook, created_at) VALUES (?,?,?,?,?)",
            (client_id, url, label or "", discord_webhook or "", time.time()),
        )
        conn.commit()


def delete_monitor(monitor_id: int, client_id: int) -> None:
    with connect() as conn:
        conn.execute(
            "DELETE FROM dc_monitors WHERE id=? AND client_id=?",
            (monitor_id, client_id),
        )
        conn.commit()


def set_global_webhook(client_id: int, discord_webhook: str) -> None:
    """Apply one webhook URL to every monitor belonging to this client."""
    with connect() as conn:
        conn.execute(
            "UPDATE dc_monitors SET discord_webhook=? WHERE client_id=?",
            (discord_webhook, client_id),
        )
        conn.commit()


def get_active_monitors_for_bot() -> List[Dict[str, Any]]:
    """
    Return all active monitors with their proxy list — consumed by drop_catcher loop.
    """
    with connect() as conn:
        monitors = conn.execute(
            "SELECT m.*, c.active as client_active FROM dc_monitors m "
            "JOIN dc_clients c ON c.id = m.client_id "
            "WHERE m.active=1 AND c.active=1"
        ).fetchall()
        result = []
        for m in monitors:
            proxies = conn.execute(
                "SELECT proxy FROM dc_proxies WHERE client_id=?", (m["client_id"],)
            ).fetchall()
            result.append({
                **dict(m),
                "proxies": [p["proxy"] for p in proxies],
            })
    return result


# ---------------------------------------------------------------------------
# Admin stats
# ---------------------------------------------------------------------------

def get_admin_stats() -> Dict[str, Any]:
    with connect() as conn:
        total_clients = conn.execute(
            "SELECT COUNT(*) FROM dc_clients WHERE active=1"
        ).fetchone()[0]
        total_monitors = conn.execute(
            "SELECT COUNT(*) FROM dc_monitors WHERE active=1"
        ).fetchone()[0]
        top_urls = conn.execute(
            """
            SELECT url, COUNT(DISTINCT client_id) as client_count
            FROM dc_monitors
            WHERE active=1
            GROUP BY url
            ORDER BY client_count DESC
            LIMIT 5
            """
        ).fetchall()
        all_clients = conn.execute(
            """
            SELECT
                c.id, c.display_name, c.email, c.created_at, c.last_login, c.active,
                (SELECT COUNT(*) FROM dc_monitors WHERE client_id=c.id) as monitor_count,
                (SELECT COUNT(*) FROM dc_proxies  WHERE client_id=c.id) as proxy_count
            FROM dc_clients c
            ORDER BY c.created_at DESC
            """
        ).fetchall()
    return {
        "total_clients": total_clients,
        "total_monitors": total_monitors,
        "top_urls": [dict(r) for r in top_urls],
        "all_clients": [dict(r) for r in all_clients],
    }


def toggle_client(client_id: int, active: int) -> None:
    with connect() as conn:
        conn.execute(
            "UPDATE dc_clients SET active=? WHERE id=?", (active, client_id)
        )
        conn.commit()
