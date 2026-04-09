"""
tests/test_dashboard.py
Unit tests for dashboard db layer and Flask routes.
Uses an in-memory SQLite DB; no external services needed.
"""
import sys
import os
import time
import sqlite3
import tempfile
import pytest
from unittest.mock import patch, MagicMock

# Ensure root on path
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "dashboard"))

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_db(tmp_path):
    """Return a temp DB path and patch db.DB_PATH to use it."""
    db_path = str(tmp_path / "test.sqlite")
    with patch.dict("os.environ", {"VIKING_DB_PATH": db_path}):
        import importlib
        import db as dashboard_db
        importlib.reload(dashboard_db)
        dashboard_db.init_db()
        yield dashboard_db


# ---------------------------------------------------------------------------
# init_db — schema creation
# ---------------------------------------------------------------------------

class TestInitDb:
    def test_creates_all_tables(self, tmp_db):
        with tmp_db.connect() as conn:
            tables = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()}
        expected = {
            "dc_clients", "dc_proxies", "dc_monitors", "dc_subscriptions",
            "dc_invite_codes", "dc_invite_redemptions", "dc_audit_log",
            "dc_watchlist_global", "dc_watchlist_personal", "dc_hot_picks",
            "dc_price_history",
        }
        assert expected.issubset(tables)

    def test_idempotent(self, tmp_db):
        # Calling init_db twice should not raise
        tmp_db.init_db()
        tmp_db.init_db()


# ---------------------------------------------------------------------------
# upsert_client
# ---------------------------------------------------------------------------

class TestUpsertClient:
    def test_creates_new_discord_client(self, tmp_db):
        row = tmp_db.upsert_client(discord_id="disc_123", display_name="Alice")
        assert row is not None
        assert row["discord_id"] == "disc_123"
        assert row["display_name"] == "Alice"

    def test_updates_existing_client(self, tmp_db):
        tmp_db.upsert_client(discord_id="disc_123", display_name="Alice")
        tmp_db.upsert_client(discord_id="disc_123", display_name="Alice Updated")
        row = tmp_db.upsert_client(discord_id="disc_123", display_name="Alice Updated")
        assert row["display_name"] == "Alice Updated"

    def test_creates_google_client(self, tmp_db):
        row = tmp_db.upsert_client(google_id="google_456", email="bob@example.com")
        assert row["google_id"] == "google_456"
        assert row["email"] == "bob@example.com"

    def test_distinct_discord_and_google(self, tmp_db):
        r1 = tmp_db.upsert_client(discord_id="disc_1", display_name="User1")
        r2 = tmp_db.upsert_client(google_id="google_1", email="u2@x.com")
        assert r1["id"] != r2["id"]


# ---------------------------------------------------------------------------
# ensure_subscription
# ---------------------------------------------------------------------------

class TestEnsureSubscription:
    def test_creates_tester_subscription(self, tmp_db):
        client = tmp_db.upsert_client(discord_id="disc_sub_test")
        sub = tmp_db.ensure_subscription(client["id"])
        assert sub["tier"] == "tester"
        assert sub["status"] == "active"

    def test_idempotent(self, tmp_db):
        client = tmp_db.upsert_client(discord_id="disc_sub_idem")
        sub1 = tmp_db.ensure_subscription(client["id"])
        sub2 = tmp_db.ensure_subscription(client["id"])
        assert sub1["id"] == sub2["id"]


# ---------------------------------------------------------------------------
# add_monitor / get_monitors_by_platform
# ---------------------------------------------------------------------------

class TestMonitors:
    def _make_client(self, tmp_db, suffix=""):
        return tmp_db.upsert_client(discord_id=f"disc_mon_{suffix}")

    def test_add_monitor_defaults_to_ticketmaster(self, tmp_db):
        client = self._make_client(tmp_db, "a")
        tmp_db.ensure_subscription(client["id"])
        # add_monitor(client_id, url, label, discord_webhook, platform='ticketmaster')
        tmp_db.add_monitor(client["id"], "https://ticketmaster.com/event/1", "Test", "")
        monitors = tmp_db.get_monitors_by_platform("ticketmaster")
        assert any(m["url"] == "https://ticketmaster.com/event/1" for m in monitors)

    def test_get_monitors_by_platform_seatgeek(self, tmp_db):
        client = self._make_client(tmp_db, "b")
        with tmp_db.connect() as conn:
            conn.execute(
                "INSERT INTO dc_monitors (client_id, url, label, platform, created_at) VALUES (?,?,?,?,?)",
                (client["id"], "https://seatgeek.com/events/1", "SG", "seatgeek", time.time()),
            )
            conn.commit()
        monitors = tmp_db.get_monitors_by_platform("seatgeek")
        assert any(m["platform"] == "seatgeek" for m in monitors)

    def test_inactive_monitors_excluded(self, tmp_db):
        client = self._make_client(tmp_db, "c")
        with tmp_db.connect() as conn:
            conn.execute(
                "INSERT INTO dc_monitors (client_id, url, label, platform, active, created_at) VALUES (?,?,?,?,?,?)",
                (client["id"], "https://seatgeek.com/events/999", "Inactive", "seatgeek", 0, time.time()),
            )
            conn.commit()
        monitors = tmp_db.get_monitors_by_platform("seatgeek")
        assert not any(m["url"] == "https://seatgeek.com/events/999" for m in monitors)


# ---------------------------------------------------------------------------
# update_monitor_price + get_price_history
# ---------------------------------------------------------------------------

class TestPriceHistory:
    def _make_monitor(self, tmp_db):
        client = tmp_db.upsert_client(discord_id="disc_price_hist")
        with tmp_db.connect() as conn:
            conn.execute(
                "INSERT INTO dc_monitors (client_id, url, label, platform, created_at) VALUES (?,?,?,?,?)",
                (client["id"], "https://seatgeek.com/events/42", "Hist Test", "seatgeek", time.time()),
            )
            conn.commit()
            row = conn.execute("SELECT id FROM dc_monitors WHERE url=?",
                               ("https://seatgeek.com/events/42",)).fetchone()
        return row["id"]

    def test_update_price_stores_history(self, tmp_db):
        mid = self._make_monitor(tmp_db)
        tmp_db.update_monitor_price(mid, 75.0, 200.0)
        history = tmp_db.get_price_history(mid)
        assert len(history) >= 1
        assert history[0]["price_min"] == pytest.approx(75.0)
        assert history[0]["price_max"] == pytest.approx(200.0)

    def test_multiple_snapshots_ordered_newest_first(self, tmp_db):
        mid = self._make_monitor(tmp_db)
        tmp_db.update_monitor_price(mid, 50.0, 100.0)
        time.sleep(0.01)
        tmp_db.update_monitor_price(mid, 60.0, 120.0)
        history = tmp_db.get_price_history(mid)
        assert len(history) >= 2
        # Newest first
        assert history[0]["price_min"] == pytest.approx(60.0)
        assert history[1]["price_min"] == pytest.approx(50.0)

    def test_history_limit(self, tmp_db):
        mid = self._make_monitor(tmp_db)
        for i in range(10):
            tmp_db.update_monitor_price(mid, float(i), float(i * 2))
        history = tmp_db.get_price_history(mid, limit=5)
        assert len(history) == 5


# ---------------------------------------------------------------------------
# redeem_invite_code
# ---------------------------------------------------------------------------

class TestRedeemInviteCode:
    def _make_code(self, tmp_db, code="TEST123", tier="pro", max_uses=1):
        with tmp_db.connect() as conn:
            conn.execute(
                "INSERT INTO dc_invite_codes (code, tier, max_uses, uses_count, active, created_at) "
                "VALUES (?,?,?,0,1,?)",
                (code, tier, max_uses, time.time()),
            )
            conn.commit()

    def test_valid_code_redeems(self, tmp_db):
        self._make_code(tmp_db)
        client = tmp_db.upsert_client(discord_id="disc_redeem")
        ok, msg = tmp_db.redeem_invite_code("TEST123", client["id"])
        assert ok, f"Expected ok=True, got: {msg}"

    def test_invalid_code_fails(self, tmp_db):
        client = tmp_db.upsert_client(discord_id="disc_invalid_code")
        ok, msg = tmp_db.redeem_invite_code("DOESNOTEXIST", client["id"])
        assert not ok

    def test_used_up_code_fails(self, tmp_db):
        self._make_code(tmp_db, code="USED1", max_uses=1)
        c1 = tmp_db.upsert_client(discord_id="disc_redeemer1")
        c2 = tmp_db.upsert_client(discord_id="disc_redeemer2")
        ok1, _ = tmp_db.redeem_invite_code("USED1", c1["id"])
        ok2, _ = tmp_db.redeem_invite_code("USED1", c2["id"])
        assert ok1
        assert not ok2

    def test_redeemed_code_upgrades_tier(self, tmp_db):
        self._make_code(tmp_db, code="PRO_CODE", tier="pro")
        client = tmp_db.upsert_client(discord_id="disc_tier_up")
        tmp_db.ensure_subscription(client["id"])
        ok, msg = tmp_db.redeem_invite_code("PRO_CODE", client["id"])
        assert ok
        sub = tmp_db.get_subscription(client["id"])
        assert sub["tier"] == "pro"


# ---------------------------------------------------------------------------
# get_global_watchlist / admin_add_global_watchlist
# ---------------------------------------------------------------------------

class TestGlobalWatchlist:
    def test_empty_initially(self, tmp_db):
        assert tmp_db.get_global_watchlist() == []

    def test_add_and_retrieve(self, tmp_db):
        tmp_db.admin_add_global_watchlist("Taylor Swift", note="Top priority")
        wl = tmp_db.get_global_watchlist()
        assert len(wl) == 1
        assert wl[0]["artist"] == "Taylor Swift"

    def test_active_only_filter(self, tmp_db):
        tmp_db.admin_add_global_watchlist("Active Artist")
        with tmp_db.connect() as conn:
            conn.execute(
                "INSERT INTO dc_watchlist_global (artist, active, created_at) VALUES (?,0,?)",
                ("Inactive Artist", time.time()),
            )
            conn.commit()
        active = tmp_db.get_global_watchlist(active_only=True)
        all_entries = tmp_db.get_global_watchlist(active_only=False)
        assert len(active) == 1
        assert len(all_entries) == 2

    def test_remove_watchlist(self, tmp_db):
        tmp_db.admin_add_global_watchlist("Remove Me")
        wl = tmp_db.get_global_watchlist()
        wl_id = wl[0]["id"]
        tmp_db.admin_remove_global_watchlist(wl_id)
        assert tmp_db.get_global_watchlist() == []


# ---------------------------------------------------------------------------
# get_hot_picks
# ---------------------------------------------------------------------------

class TestHotPicks:
    def test_empty_initially(self, tmp_db):
        assert tmp_db.get_hot_picks() == []

    def test_add_and_retrieve(self, tmp_db):
        if hasattr(tmp_db, "admin_add_hot_pick"):
            tmp_db.admin_add_hot_pick("Hot Artist", note="Fire")
        else:
            with tmp_db.connect() as conn:
                conn.execute(
                    "INSERT INTO dc_hot_picks (artist, note, active, created_at) VALUES (?,?,1,?)",
                    ("Hot Artist", "Fire", time.time()),
                )
                conn.commit()
        picks = tmp_db.get_hot_picks()
        assert len(picks) >= 1
        assert any(p["artist"] == "Hot Artist" for p in picks)
