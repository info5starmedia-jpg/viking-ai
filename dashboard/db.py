"""
dashboard/db.py
SQLite helpers for the Viking AI Drop Catcher web dashboard.
Extends the existing VIKING_DB_PATH database with dashboard-specific tables.
"""
from __future__ import annotations

import os
import secrets
import sqlite3
import time
from typing import Any, Dict, List, Optional

DB_PATH = os.getenv("VIKING_DB_PATH", "/opt/viking-ai/viking_ai.sqlite")

# Tier monitor limits (None = unlimited)
TIER_LIMITS: Dict[str, Optional[int]] = {
    "tester":    None,   # unlimited monitors, checkout blocked in app layer
    "starter":   7,
    "pro":       15,
    "unlimited": None,
}

WATCHLIST_LIMITS: Dict[str, Optional[int]] = {
    "tester":    5,
    "starter":   20,
    "pro":       50,
    "unlimited": None,
}

TIER_PRICES = {
    "tester":    0,
    "starter":   80,
    "pro":       100,
    "unlimited": 300,
}


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

            CREATE TABLE IF NOT EXISTS dc_subscriptions (
                id                     INTEGER PRIMARY KEY AUTOINCREMENT,
                client_id              INTEGER NOT NULL REFERENCES dc_clients(id) ON DELETE CASCADE,
                tier                   TEXT NOT NULL DEFAULT 'tester',
                status                 TEXT NOT NULL DEFAULT 'active',
                stripe_customer_id     TEXT,
                stripe_subscription_id TEXT,
                current_period_end     REAL,
                extra_days             INTEGER DEFAULT 0,
                created_at             REAL,
                updated_at             REAL
            );

            CREATE TABLE IF NOT EXISTS dc_invite_codes (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                code         TEXT NOT NULL UNIQUE,
                tier         TEXT NOT NULL DEFAULT 'tester',
                max_uses     INTEGER NOT NULL DEFAULT 1,
                uses_count   INTEGER NOT NULL DEFAULT 0,
                expires_at   REAL,
                note         TEXT DEFAULT '',
                active       INTEGER DEFAULT 1,
                created_at   REAL
            );

            CREATE TABLE IF NOT EXISTS dc_invite_redemptions (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                code        TEXT NOT NULL,
                client_id   INTEGER NOT NULL REFERENCES dc_clients(id) ON DELETE CASCADE,
                redeemed_at REAL
            );

            CREATE TABLE IF NOT EXISTS dc_audit_log (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                admin_name TEXT,
                action     TEXT NOT NULL,
                target     TEXT,
                detail     TEXT,
                created_at REAL
            );

            CREATE TABLE IF NOT EXISTS dc_watchlist_global (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                artist     TEXT NOT NULL,
                note       TEXT DEFAULT '',
                image_url  TEXT DEFAULT '',
                active     INTEGER DEFAULT 1,
                created_at REAL
            );

            CREATE TABLE IF NOT EXISTS dc_watchlist_personal (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                client_id  INTEGER NOT NULL REFERENCES dc_clients(id) ON DELETE CASCADE,
                artist     TEXT NOT NULL,
                added_at   REAL,
                UNIQUE(client_id, artist)
            );

            CREATE TABLE IF NOT EXISTS dc_hot_picks (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                artist     TEXT NOT NULL,
                note       TEXT DEFAULT '',
                image_url  TEXT DEFAULT '',
                active     INTEGER DEFAULT 1,
                created_at REAL
            );
        """)
        # Migrate dc_monitors with new columns (ALTER TABLE doesn't support IF NOT EXISTS)
        for col, definition in [
            ("platform",       "TEXT NOT NULL DEFAULT 'ticketmaster'"),
            ("last_price_min", "REAL"),
            ("last_price_max", "REAL"),
        ]:
            try:
                conn.execute(f"ALTER TABLE dc_monitors ADD COLUMN {col} {definition}")
            except Exception:
                pass  # column already exists
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
# Subscriptions
# ---------------------------------------------------------------------------

def get_subscription(client_id: int) -> Optional[Dict[str, Any]]:
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM dc_subscriptions WHERE client_id=? ORDER BY id DESC LIMIT 1",
            (client_id,),
        ).fetchone()
    return dict(row) if row else None


def ensure_subscription(client_id: int) -> Dict[str, Any]:
    """Return subscription for client, creating a tester one if none exists."""
    sub = get_subscription(client_id)
    if not sub:
        now = time.time()
        with connect() as conn:
            conn.execute(
                "INSERT INTO dc_subscriptions (client_id, tier, status, created_at, updated_at) VALUES (?,?,?,?,?)",
                (client_id, "tester", "active", now, now),
            )
            conn.commit()
        sub = get_subscription(client_id)
    return sub


def is_subscription_active(client_id: int) -> bool:
    """True if client has an active subscription (not expired/past_due/canceled)."""
    sub = get_subscription(client_id)
    if not sub:
        return False
    if sub["status"] not in ("active", "trialing"):
        return False
    # Check period end — if set and past, expired
    period_end = sub.get("current_period_end")
    if period_end:
        extra = sub.get("extra_days", 0) or 0
        effective_end = period_end + (extra * 86400)
        if time.time() > effective_end:
            return False
    return True


def get_tier(client_id: int) -> str:
    sub = get_subscription(client_id)
    return sub["tier"] if sub else "tester"


def get_monitor_limit(client_id: int) -> Optional[int]:
    """Return max monitors allowed for this client's tier. None = unlimited."""
    tier = get_tier(client_id)
    return TIER_LIMITS.get(tier)


def upsert_subscription_from_stripe(
    stripe_customer_id: str,
    stripe_subscription_id: str,
    tier: str,
    status: str,
    current_period_end: float,
) -> None:
    now = time.time()
    with connect() as conn:
        row = conn.execute(
            "SELECT id, client_id FROM dc_subscriptions WHERE stripe_subscription_id=?",
            (stripe_subscription_id,),
        ).fetchone()
        if row:
            conn.execute(
                """UPDATE dc_subscriptions
                   SET tier=?, status=?, current_period_end=?, updated_at=?
                   WHERE id=?""",
                (tier, status, current_period_end, now, row["id"]),
            )
        else:
            # Find client by stripe customer ID
            sub_row = conn.execute(
                "SELECT client_id FROM dc_subscriptions WHERE stripe_customer_id=? LIMIT 1",
                (stripe_customer_id,),
            ).fetchone()
            if sub_row:
                conn.execute(
                    """INSERT INTO dc_subscriptions
                       (client_id, tier, status, stripe_customer_id, stripe_subscription_id,
                        current_period_end, created_at, updated_at)
                       VALUES (?,?,?,?,?,?,?,?)""",
                    (sub_row["client_id"], tier, status, stripe_customer_id,
                     stripe_subscription_id, current_period_end, now, now),
                )
        conn.commit()


def set_subscription_status(client_id: int, status: str) -> None:
    now = time.time()
    with connect() as conn:
        conn.execute(
            "UPDATE dc_subscriptions SET status=?, updated_at=? WHERE client_id=?",
            (status, now, client_id),
        )
        conn.commit()


def admin_set_tier(client_id: int, tier: str, admin_name: str = "admin") -> None:
    now = time.time()
    with connect() as conn:
        conn.execute(
            "UPDATE dc_subscriptions SET tier=?, updated_at=? WHERE client_id=?",
            (tier, now, client_id),
        )
        conn.execute(
            "INSERT INTO dc_audit_log (admin_name, action, target, detail, created_at) VALUES (?,?,?,?,?)",
            (admin_name, "set_tier", str(client_id), f"tier={tier}", now),
        )
        conn.commit()


def admin_add_days(client_id: int, days: int, admin_name: str = "admin") -> None:
    now = time.time()
    with connect() as conn:
        conn.execute(
            "UPDATE dc_subscriptions SET extra_days = COALESCE(extra_days,0) + ?, updated_at=? WHERE client_id=?",
            (days, now, client_id),
        )
        conn.execute(
            "INSERT INTO dc_audit_log (admin_name, action, target, detail, created_at) VALUES (?,?,?,?,?)",
            (admin_name, "add_days", str(client_id), f"days={days}", now),
        )
        conn.commit()


def admin_pause_monitors(client_id: int, paused: int, admin_name: str = "admin") -> None:
    now = time.time()
    with connect() as conn:
        conn.execute(
            "UPDATE dc_monitors SET active=? WHERE client_id=?",
            (0 if paused else 1, client_id),
        )
        conn.execute(
            "INSERT INTO dc_audit_log (admin_name, action, target, detail, created_at) VALUES (?,?,?,?,?)",
            (admin_name, "pause_monitors" if paused else "resume_monitors", str(client_id), "", now),
        )
        conn.commit()


# ---------------------------------------------------------------------------
# Invite codes
# ---------------------------------------------------------------------------

def create_invite_code(
    tier: str = "tester",
    max_uses: int = 1,
    expires_days: Optional[int] = None,
    note: str = "",
) -> str:
    code = secrets.token_urlsafe(12)
    expires_at = (time.time() + expires_days * 86400) if expires_days else None
    with connect() as conn:
        conn.execute(
            "INSERT INTO dc_invite_codes (code, tier, max_uses, expires_at, note, created_at) VALUES (?,?,?,?,?,?)",
            (code, tier, max_uses, expires_at, note, time.time()),
        )
        conn.commit()
    return code


def get_invite_code(code: str) -> Optional[Dict[str, Any]]:
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM dc_invite_codes WHERE code=?", (code,)
        ).fetchone()
    return dict(row) if row else None


def list_invite_codes() -> List[Dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM dc_invite_codes ORDER BY created_at DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def redeem_invite_code(code: str, client_id: int) -> tuple[bool, str]:
    """
    Validate and redeem an invite code for a client.
    Returns (success, message).
    """
    now = time.time()
    with connect() as conn:
        # IMMEDIATE lock prevents double-redemption race condition
        conn.execute("BEGIN IMMEDIATE")
        ic = conn.execute(
            "SELECT * FROM dc_invite_codes WHERE code=?", (code,)
        ).fetchone()
        if not ic:
            conn.execute("ROLLBACK")
            return False, "Invalid invite code."
        if not ic["active"]:
            conn.execute("ROLLBACK")
            return False, "This invite code has been deactivated."
        if ic["expires_at"] and now > ic["expires_at"]:
            conn.execute("ROLLBACK")
            return False, "This invite code has expired."
        if ic["uses_count"] >= ic["max_uses"]:
            conn.execute("ROLLBACK")
            return False, "This invite code has no uses remaining."
        # Check not already redeemed by this client
        already = conn.execute(
            "SELECT id FROM dc_invite_redemptions WHERE code=? AND client_id=?",
            (code, client_id),
        ).fetchone()
        if already:
            conn.execute("ROLLBACK")
            return False, "You have already redeemed this invite code."

        # Redeem
        conn.execute(
            "UPDATE dc_invite_codes SET uses_count = uses_count + 1 WHERE code=?", (code,)
        )
        conn.execute(
            "INSERT INTO dc_invite_redemptions (code, client_id, redeemed_at) VALUES (?,?,?)",
            (code, client_id, now),
        )
        # Apply tier to subscription
        sub = conn.execute(
            "SELECT id FROM dc_subscriptions WHERE client_id=?", (client_id,)
        ).fetchone()
        if sub:
            conn.execute(
                "UPDATE dc_subscriptions SET tier=?, status='active', updated_at=? WHERE client_id=?",
                (ic["tier"], now, client_id),
            )
        else:
            conn.execute(
                "INSERT INTO dc_subscriptions (client_id, tier, status, created_at, updated_at) VALUES (?,?,?,?,?)",
                (client_id, ic["tier"], "active", now, now),
            )
        conn.commit()
    return True, f"Invite code redeemed — you are now on the {ic['tier'].title()} plan."


def deactivate_invite_code(code: str, admin_name: str = "admin") -> None:
    now = time.time()
    with connect() as conn:
        conn.execute(
            "UPDATE dc_invite_codes SET active=0 WHERE code=?", (code,)
        )
        conn.execute(
            "INSERT INTO dc_audit_log (admin_name, action, target, detail, created_at) VALUES (?,?,?,?,?)",
            (admin_name, "deactivate_code", code, "", now),
        )
        conn.commit()


def extend_invite_code(code: str, extra_days: int, admin_name: str = "admin") -> None:
    now = time.time()
    with connect() as conn:
        # If the code had no expiry, start expiry from now; otherwise extend from existing expiry
        ic = conn.execute("SELECT expires_at FROM dc_invite_codes WHERE code=?", (code,)).fetchone()
        if ic:
            base = ic["expires_at"] if ic["expires_at"] else now
            conn.execute(
                "UPDATE dc_invite_codes SET expires_at = ? WHERE code=?",
                (base + extra_days * 86400, code),
            )
        conn.execute(
            "INSERT INTO dc_audit_log (admin_name, action, target, detail, created_at) VALUES (?,?,?,?,?)",
            (admin_name, "extend_code", code, f"days={extra_days}", now),
        )
        conn.commit()


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


def add_monitor(client_id: int, url: str, label: str, discord_webhook: str,
                platform: str = "ticketmaster") -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO dc_monitors (client_id, url, label, discord_webhook, platform, created_at) VALUES (?,?,?,?,?,?)",
            (client_id, url, label or "", discord_webhook or "", platform, time.time()),
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
    """Return all active monitors with their proxy list — consumed by drop_catcher loop."""
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
            result.append({**dict(m), "proxies": [p["proxy"] for p in proxies]})
    return result


# ---------------------------------------------------------------------------
# Expiry warnings — called by bot scheduler
# ---------------------------------------------------------------------------

def get_clients_expiring_in(days: int) -> List[Dict[str, Any]]:
    """Return active clients whose subscription expires in exactly `days` days (±12h window)."""
    now = time.time()
    window_start = now + (days * 86400) - 43200  # 12h before
    window_end   = now + (days * 86400) + 43200  # 12h after
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT c.id, c.discord_id, c.display_name, c.email,
                   s.tier, s.current_period_end, s.extra_days
            FROM dc_clients c
            JOIN dc_subscriptions s ON s.client_id = c.id
            WHERE c.active = 1
              AND s.status IN ('active','trialing')
              AND (s.current_period_end + COALESCE(s.extra_days,0)*86400)
                  BETWEEN ? AND ?
            """,
            (window_start, window_end),
        ).fetchall()
    return [dict(r) for r in rows]


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
            FROM dc_monitors WHERE active=1
            GROUP BY url ORDER BY client_count DESC LIMIT 5
            """
        ).fetchall()
        all_clients = conn.execute(
            """
            SELECT
                c.id, c.display_name, c.email, c.created_at, c.last_login, c.active,
                COALESCE(s.tier,'tester')   as tier,
                COALESCE(s.status,'none')   as sub_status,
                s.current_period_end,
                s.extra_days,
                (SELECT COUNT(*) FROM dc_monitors WHERE client_id=c.id) as monitor_count,
                (SELECT COUNT(*) FROM dc_proxies  WHERE client_id=c.id) as proxy_count,
                (SELECT code FROM dc_invite_redemptions WHERE client_id=c.id LIMIT 1) as invite_code
            FROM dc_clients c
            LEFT JOIN dc_subscriptions s ON s.client_id=c.id
            ORDER BY c.created_at DESC
            """
        ).fetchall()
        invite_codes = list_invite_codes()
        audit_log = conn.execute(
            "SELECT * FROM dc_audit_log ORDER BY created_at DESC LIMIT 50"
        ).fetchall()
        tier_counts = conn.execute(
            """
            SELECT COALESCE(s.tier,'tester') as tier, COUNT(*) as cnt
            FROM dc_clients c
            LEFT JOIN dc_subscriptions s ON s.client_id=c.id
            WHERE c.active=1
            GROUP BY tier
            """
        ).fetchall()
    return {
        "total_clients": total_clients,
        "total_monitors": total_monitors,
        "top_urls": [dict(r) for r in top_urls],
        "all_clients": [dict(r) for r in all_clients],
        "invite_codes": invite_codes,
        "audit_log": [dict(r) for r in audit_log],
        "tier_counts": {r["tier"]: r["cnt"] for r in tier_counts},
    }


# ---------------------------------------------------------------------------
# Phase 3: platform column helper on monitors
# ---------------------------------------------------------------------------

def update_monitor_price(monitor_id: int, price_min: Optional[float], price_max: Optional[float]) -> None:
    now = time.time()
    with connect() as conn:
        conn.execute(
            "UPDATE dc_monitors SET last_price_min=?, last_price_max=?, last_checked=? WHERE id=?",
            (price_min, price_max, now, monitor_id),
        )
        conn.commit()


def get_monitors_by_platform(platform: str) -> List[Dict[str, Any]]:
    """Return all active monitors for a given platform, with their proxies."""
    with connect() as conn:
        monitors = conn.execute(
            """
            SELECT m.*, c.active as client_active
            FROM dc_monitors m
            JOIN dc_clients c ON c.id = m.client_id
            WHERE m.active=1 AND c.active=1 AND m.platform=?
            """,
            (platform,),
        ).fetchall()
        result = []
        for m in monitors:
            proxies = conn.execute(
                "SELECT proxy FROM dc_proxies WHERE client_id=?", (m["client_id"],)
            ).fetchall()
            result.append({**dict(m), "proxies": [p["proxy"] for p in proxies]})
    return result


def increment_monitor_alert_count(monitor_id: int) -> None:
    with connect() as conn:
        conn.execute(
            "UPDATE dc_monitors SET alert_count = alert_count + 1 WHERE id=?", (monitor_id,)
        )
        conn.commit()


# ---------------------------------------------------------------------------
# Phase 3: Watchlist (personal)
# ---------------------------------------------------------------------------

def get_watchlist_limit(client_id: int) -> Optional[int]:
    tier = get_tier(client_id)
    return WATCHLIST_LIMITS.get(tier)


def get_personal_watchlist(client_id: int) -> List[Dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM dc_watchlist_personal WHERE client_id=? ORDER BY added_at DESC",
            (client_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def add_personal_watchlist(client_id: int, artist: str) -> tuple[bool, str]:
    artist = (artist or "").strip()
    if not artist:
        return False, "Artist name is required."
    limit = get_watchlist_limit(client_id)
    now = time.time()
    with connect() as conn:
        if limit is not None:
            count = conn.execute(
                "SELECT COUNT(*) FROM dc_watchlist_personal WHERE client_id=?", (client_id,)
            ).fetchone()[0]
            if count >= limit:
                return False, f"Your plan allows {limit} watchlist entries. Upgrade to add more."
        try:
            conn.execute(
                "INSERT INTO dc_watchlist_personal (client_id, artist, added_at) VALUES (?,?,?)",
                (client_id, artist, now),
            )
            conn.commit()
        except Exception:
            return False, f"{artist} is already on your watchlist."
    return True, f"{artist} added to your watchlist."


def remove_personal_watchlist(entry_id: int, client_id: int) -> None:
    with connect() as conn:
        conn.execute(
            "DELETE FROM dc_watchlist_personal WHERE id=? AND client_id=?",
            (entry_id, client_id),
        )
        conn.commit()


# ---------------------------------------------------------------------------
# Phase 3: Global watchlist (admin-managed)
# ---------------------------------------------------------------------------

def get_global_watchlist(active_only: bool = True) -> List[Dict[str, Any]]:
    with connect() as conn:
        if active_only:
            rows = conn.execute(
                "SELECT * FROM dc_watchlist_global WHERE active=1 ORDER BY created_at DESC"
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM dc_watchlist_global ORDER BY created_at DESC"
            ).fetchall()
    return [dict(r) for r in rows]


def admin_add_global_watchlist(artist: str, note: str = "", image_url: str = "") -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO dc_watchlist_global (artist, note, image_url, created_at) VALUES (?,?,?,?)",
            (artist.strip(), note[:200], image_url, time.time()),
        )
        conn.commit()


def admin_remove_global_watchlist(wl_id: int, admin_name: str = "admin") -> None:
    now = time.time()
    with connect() as conn:
        conn.execute("DELETE FROM dc_watchlist_global WHERE id=?", (wl_id,))
        conn.execute(
            "INSERT INTO dc_audit_log (admin_name, action, target, detail, created_at) VALUES (?,?,?,?,?)",
            (admin_name, "remove_global_watchlist", str(wl_id), "", now),
        )
        conn.commit()


# ---------------------------------------------------------------------------
# Phase 3: Hot picks (admin-flagged, shown on all dashboards)
# ---------------------------------------------------------------------------

def get_hot_picks(active_only: bool = True) -> List[Dict[str, Any]]:
    with connect() as conn:
        if active_only:
            rows = conn.execute(
                "SELECT * FROM dc_hot_picks WHERE active=1 ORDER BY created_at DESC"
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM dc_hot_picks ORDER BY created_at DESC"
            ).fetchall()
    return [dict(r) for r in rows]


def admin_add_hot_pick(artist: str, note: str = "", image_url: str = "") -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO dc_hot_picks (artist, note, image_url, created_at) VALUES (?,?,?,?)",
            (artist.strip(), note[:300], image_url, time.time()),
        )
        conn.commit()


def admin_deactivate_hot_pick(pick_id: int, admin_name: str = "admin") -> None:
    now = time.time()
    with connect() as conn:
        conn.execute("UPDATE dc_hot_picks SET active=0 WHERE id=?", (pick_id,))
        conn.execute(
            "INSERT INTO dc_audit_log (admin_name, action, target, detail, created_at) VALUES (?,?,?,?,?)",
            (admin_name, "deactivate_hot_pick", str(pick_id), "", now),
        )
        conn.commit()


def toggle_client(client_id: int, active: int) -> None:
    with connect() as conn:
        conn.execute(
            "UPDATE dc_clients SET active=? WHERE id=?", (active, client_id)
        )
        conn.commit()


def get_client_detail(client_id: int) -> Optional[Dict[str, Any]]:
    with connect() as conn:
        client = conn.execute(
            "SELECT * FROM dc_clients WHERE id=?", (client_id,)
        ).fetchone()
        if not client:
            return None
        sub = conn.execute(
            "SELECT * FROM dc_subscriptions WHERE client_id=? ORDER BY id DESC LIMIT 1",
            (client_id,),
        ).fetchone()
        monitors = conn.execute(
            "SELECT * FROM dc_monitors WHERE client_id=? ORDER BY created_at DESC",
            (client_id,),
        ).fetchall()
        proxies = conn.execute(
            "SELECT * FROM dc_proxies WHERE client_id=?", (client_id,)
        ).fetchall()
        audit = conn.execute(
            "SELECT * FROM dc_audit_log WHERE target=? ORDER BY created_at DESC LIMIT 20",
            (str(client_id),),
        ).fetchall()
        redemption = conn.execute(
            "SELECT code FROM dc_invite_redemptions WHERE client_id=? LIMIT 1",
            (client_id,),
        ).fetchone()
    return {
        "client": dict(client),
        "subscription": dict(sub) if sub else None,
        "monitors": [dict(m) for m in monitors],
        "proxies": [dict(p) for p in proxies],
        "audit": [dict(a) for a in audit],
        "invite_code": redemption["code"] if redemption else None,
    }
