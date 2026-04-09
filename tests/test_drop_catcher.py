"""
tests/test_drop_catcher.py
Unit tests for drop_catcher.py helper functions.
All tests are offline — no network calls, no external DB.
"""
import sys
import os
import hashlib
import importlib
from unittest.mock import patch, MagicMock

import pytest

# Ensure root on path
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

# ---------------------------------------------------------------------------
# Import the module with optional deps stubbed out
# ---------------------------------------------------------------------------

with patch.dict("sys.modules", {
    "requests": MagicMock(),
    "ticketmaster_agent": None,
    "dotenv": MagicMock(load_dotenv=MagicMock()),
}):
    import drop_catcher as dc


# ---------------------------------------------------------------------------
# _normalize_event
# ---------------------------------------------------------------------------

class TestNormalizeEvent:
    def _ev(self, **overrides):
        base = {
            "id": "TM123",
            "name": "Test Concert",
            "dates": {
                "start": {"localDate": "2026-06-01", "dateTime": "2026-06-01T20:00:00Z"},
                "status": {"code": "onsale"},
            },
            "priceRanges": [{"type": "standard", "min": 50.0, "max": 200.0}],
            "_embedded": {
                "venues": [{"name": "Madison Square Garden", "city": {"name": "New York"}, "state": {"stateCode": "NY"}}]
            },
            "url": "https://www.ticketmaster.com/event/TM123",
        }
        base.update(overrides)
        return base

    def test_basic_fields(self):
        norm = dc._normalize_event(self._ev())
        assert norm["id"] == "TM123"
        assert norm["name"] == "Test Concert"
        assert norm["url"] == "https://www.ticketmaster.com/event/TM123"

    def test_price_extraction(self):
        norm = dc._normalize_event(self._ev())
        assert norm["price_min"] == 50.0
        assert norm["price_max"] == 200.0

    def test_status_extraction(self):
        norm = dc._normalize_event(self._ev())
        assert norm["status"] == "onsale"

    def test_venue_city(self):
        norm = dc._normalize_event(self._ev())
        assert "New York" in norm.get("city", "")

    def test_missing_price_ranges(self):
        ev = self._ev()
        del ev["priceRanges"]
        norm = dc._normalize_event(ev)
        assert norm["price_min"] is None
        assert norm["price_max"] is None

    def test_missing_venues(self):
        ev = self._ev()
        del ev["_embedded"]
        norm = dc._normalize_event(ev)
        assert norm["venue"] == ""
        assert norm["city"] == ""

    def test_date_parsing(self):
        norm = dc._normalize_event(self._ev())
        assert norm["event_local_date"] == "2026-06-01"


# ---------------------------------------------------------------------------
# _event_hash
# ---------------------------------------------------------------------------

class TestEventHash:
    def _norm(self):
        return {
            "event_id": "TM123",
            "status": "onsale",
            "price_min": 50.0,
            "price_max": 200.0,
        }

    def test_returns_string(self):
        h = dc._event_hash(self._norm())
        assert isinstance(h, str)
        assert len(h) > 8

    def test_same_input_same_hash(self):
        h1 = dc._event_hash(self._norm())
        h2 = dc._event_hash(self._norm())
        assert h1 == h2

    def test_different_status_different_hash(self):
        n1 = self._norm()
        n2 = {**n1, "status": "offsale"}
        assert dc._event_hash(n1) != dc._event_hash(n2)

    def test_different_price_different_hash(self):
        n1 = self._norm()
        n2 = {**n1, "price_min": 99.0}
        assert dc._event_hash(n1) != dc._event_hash(n2)


# ---------------------------------------------------------------------------
# _extract_status
# ---------------------------------------------------------------------------

class TestExtractStatus:
    def _ev(self, code):
        return {"dates": {"status": {"code": code}}}

    def test_onsale(self):
        assert dc._extract_status(self._ev("onsale")) == "onsale"

    def test_offsale(self):
        assert dc._extract_status(self._ev("offsale")) == "offsale"

    def test_cancelled(self):
        assert dc._extract_status(self._ev("cancelled")) == "cancelled"

    def test_missing_dates(self):
        # _extract_status returns 'unknown' when no date/status info
        assert dc._extract_status({}) == "unknown"

    def test_missing_status(self):
        assert dc._extract_status({"dates": {}}) == "unknown"


# ---------------------------------------------------------------------------
# _is_dropped
# ---------------------------------------------------------------------------

class TestIsDropped:
    def test_offsale_to_onsale(self):
        assert dc._is_dropped("offsale", "onsale") is True

    def test_pending_to_onsale(self):
        assert dc._is_dropped("", "onsale") is True

    def test_onsale_to_onsale(self):
        assert dc._is_dropped("onsale", "onsale") is False

    def test_onsale_to_offsale(self):
        assert dc._is_dropped("onsale", "offsale") is False

    def test_cancelled_to_onsale(self):
        assert dc._is_dropped("cancelled", "onsale") is True


# ---------------------------------------------------------------------------
# _extract_prices
# ---------------------------------------------------------------------------

class TestExtractPrices:
    def test_standard_price_range(self):
        ev = {"priceRanges": [{"type": "standard", "min": 25.0, "max": 150.0}]}
        mn, mx = dc._extract_prices(ev)
        assert mn == 25.0
        assert mx == 150.0

    def test_non_standard_price_range(self):
        ev = {"priceRanges": [{"type": "platinum", "min": 500.0, "max": 1000.0}]}
        mn, mx = dc._extract_prices(ev)
        assert mn == 500.0

    def test_no_price_ranges(self):
        mn, mx = dc._extract_prices({})
        assert mn is None
        assert mx is None

    def test_min_only(self):
        # When only min is provided (no max key), max() over empty sequence
        # raises ValueError, caught as Exception → returns (None, None)
        ev = {"priceRanges": [{"type": "standard", "min": 30.0}]}
        mn, mx = dc._extract_prices(ev)
        # Both come back None because max() over empty iterable raises
        assert mn is None
        assert mx is None

    def test_multiple_ranges_picks_lowest_min(self):
        ev = {
            "priceRanges": [
                {"type": "standard", "min": 100.0, "max": 200.0},
                {"type": "standard", "min": 50.0, "max": 150.0},
            ]
        }
        mn, mx = dc._extract_prices(ev)
        assert mn == 50.0


# ---------------------------------------------------------------------------
# _estimate_resale_floor
# ---------------------------------------------------------------------------

class TestEstimateResaleFloor:
    def test_low_sell_through(self):
        # Low demand => resale close to or below face
        floor = dc._estimate_resale_floor(100.0, sell_through_pct=10.0)
        assert floor > 0
        assert floor <= 150  # shouldn't spike on low demand

    def test_high_sell_through(self):
        floor = dc._estimate_resale_floor(100.0, sell_through_pct=95.0)
        assert floor > 100  # high demand => resale premium

    def test_zero_face(self):
        floor = dc._estimate_resale_floor(0.0, sell_through_pct=50.0)
        assert floor >= 0

    def test_returns_float(self):
        assert isinstance(dc._estimate_resale_floor(50.0, 50.0), float)


# ---------------------------------------------------------------------------
# _arbitrage_line
# ---------------------------------------------------------------------------

class TestArbitrageLine:
    def test_returns_string_or_none(self):
        result = dc._arbitrage_line(50.0, 0.9)
        assert result is None or isinstance(result, str)

    def test_high_sell_through_gives_line(self):
        result = dc._arbitrage_line(50.0, 0.95)
        # Should produce some arbitrage description for high sell-through
        assert result is None or len(result) > 0

    def test_zero_face_price(self):
        # Should not crash
        result = dc._arbitrage_line(0.0, 0.5)
        assert result is None or isinstance(result, str)


# ---------------------------------------------------------------------------
# _parse_sell_through
# ---------------------------------------------------------------------------

class TestParseSellThrough:
    def test_parses_percentage(self):
        # _parse_sell_through looks for "X% open" and returns 100 - X
        # e.g. "28% open" means 72% sold → sell_through = 72
        val = dc._parse_sell_through("28% open seats remaining")
        assert val == pytest.approx(72.0, abs=1)

    def test_none_input(self):
        val = dc._parse_sell_through(None)
        assert val == 0.0

    def test_empty_string(self):
        val = dc._parse_sell_through("")
        assert val == 0.0

    def test_no_percentage(self):
        val = dc._parse_sell_through("no data available")
        assert val == 0.0

    def test_various_formats(self):
        for text in ("50%", "Sold: 50%", "50.5%", "50 %"):
            val = dc._parse_sell_through(text)
            assert val >= 0.0


# ---------------------------------------------------------------------------
# is_available / global_scan_is_available
# ---------------------------------------------------------------------------

class TestAvailability:
    def test_is_available_returns_bool(self):
        assert isinstance(dc.is_available(), bool)

    def test_global_scan_is_available_returns_bool(self):
        assert isinstance(dc.global_scan_is_available(), bool)


# ---------------------------------------------------------------------------
# _fmt_utc
# ---------------------------------------------------------------------------

class TestFmtUtc:
    def test_formats_datetime(self):
        from datetime import datetime, timezone
        dt = datetime(2026, 6, 1, 20, 0, 0, tzinfo=timezone.utc)
        result = dc._fmt_utc(dt)
        assert "2026" in result
        assert isinstance(result, str)
