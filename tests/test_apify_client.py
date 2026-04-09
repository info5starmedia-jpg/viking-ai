"""
tests/test_apify_client.py
Unit tests for apify_client.py — all offline, HTTP mocked.
"""
import sys
import os
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import apify_client as ac


# ---------------------------------------------------------------------------
# is_available
# ---------------------------------------------------------------------------

class TestIsAvailable:
    def test_false_without_token(self):
        with patch.object(ac, "APIFY_API_TOKEN", ""), \
             patch.object(ac, "_http", MagicMock()):
            assert ac.is_available() is False

    def test_false_without_http(self):
        with patch.object(ac, "APIFY_API_TOKEN", "tok123"), \
             patch.object(ac, "_http", None):
            assert ac.is_available() is False

    def test_true_with_token_and_http(self):
        with patch.object(ac, "APIFY_API_TOKEN", "tok123"), \
             patch.object(ac, "_http", MagicMock()):
            assert ac.is_available() is True


# ---------------------------------------------------------------------------
# _run_sync
# ---------------------------------------------------------------------------

class TestRunSync:
    def _mock_response(self, items, status=200):
        resp = MagicMock()
        resp.status_code = status
        resp.json.return_value = items
        resp.raise_for_status = MagicMock()
        return resp

    def test_returns_list_on_success(self):
        items = [{"name": "Event A", "lowestPrice": 75.0}]
        with patch.object(ac, "APIFY_API_TOKEN", "tok"), \
             patch.object(ac, "_http", MagicMock(post=MagicMock(return_value=self._mock_response(items)))):
            result = ac._run_sync("some/actor", {"key": "val"})
        assert result == items

    def test_returns_empty_on_no_token(self):
        with patch.object(ac, "APIFY_API_TOKEN", ""):
            result = ac._run_sync("some/actor", {})
        assert result == []

    def test_returns_empty_on_http_error(self):
        http = MagicMock()
        http.post.side_effect = Exception("connection refused")
        with patch.object(ac, "APIFY_API_TOKEN", "tok"), \
             patch.object(ac, "_http", http):
            result = ac._run_sync("some/actor", {})
        assert result == []

    def test_returns_empty_on_402(self):
        resp = self._mock_response([], status=402)
        resp.raise_for_status = MagicMock()
        with patch.object(ac, "APIFY_API_TOKEN", "tok"), \
             patch.object(ac, "_http", MagicMock(post=MagicMock(return_value=resp))):
            result = ac._run_sync("some/actor", {})
        assert result == []

    def test_unwraps_items_wrapper(self):
        wrapped = {"items": [{"name": "Test"}]}
        with patch.object(ac, "APIFY_API_TOKEN", "tok"), \
             patch.object(ac, "_http", MagicMock(post=MagicMock(return_value=self._mock_response(wrapped)))):
            result = ac._run_sync("some/actor", {})
        assert result == [{"name": "Test"}]


# ---------------------------------------------------------------------------
# search_tm_events
# ---------------------------------------------------------------------------

class TestSearchTmEvents:
    def test_returns_empty_without_inputs(self):
        assert ac.search_tm_events() == []

    def test_builds_url_from_keyword(self):
        with patch.object(ac, "_run_sync", return_value=[{"id": "1"}]) as mock_run:
            result = ac.search_tm_events(keyword="Taylor Swift")
        assert result == [{"id": "1"}]
        call_input = mock_run.call_args[0][1]
        assert "startUrls" in call_input
        assert "taylor+swift" in call_input["startUrls"][0]["url"].lower() or \
               "taylor%20swift" in call_input["startUrls"][0]["url"].lower() or \
               "taylor" in call_input["startUrls"][0]["url"].lower()

    def test_passes_url_directly(self):
        with patch.object(ac, "_run_sync", return_value=[]) as mock_run:
            ac.search_tm_events(url="https://www.ticketmaster.com/search?q=test")
        call_input = mock_run.call_args[0][1]
        assert call_input["startUrls"][0]["url"] == "https://www.ticketmaster.com/search?q=test"


# ---------------------------------------------------------------------------
# get_tm_event
# ---------------------------------------------------------------------------

class TestGetTmEvent:
    def test_returns_first_item(self):
        with patch.object(ac, "search_tm_events", return_value=[{"id": "TM999"}]):
            result = ac.get_tm_event("TM999")
        assert result == {"id": "TM999"}

    def test_returns_none_when_empty(self):
        with patch.object(ac, "search_tm_events", return_value=[]):
            assert ac.get_tm_event("TM999") is None


# ---------------------------------------------------------------------------
# get_stubhub_price / get_seatgeek_price / get_vividseats_price
# ---------------------------------------------------------------------------

class TestPlatformGetters:
    def test_stubhub_returns_first_item(self):
        item = {"lowestPrice": 120.0}
        with patch.object(ac, "_run_sync", return_value=[item]):
            result = ac.get_stubhub_price("https://www.stubhub.com/event/123")
        assert result == item

    def test_stubhub_returns_none_on_empty(self):
        with patch.object(ac, "_run_sync", return_value=[]):
            assert ac.get_stubhub_price("https://www.stubhub.com/event/123") is None

    def test_seatgeek_returns_first_item(self):
        item = {"minPrice": 55.0}
        with patch.object(ac, "_run_sync", return_value=[item]):
            result = ac.get_seatgeek_price("https://seatgeek.com/events/1")
        assert result == item

    def test_vividseats_returns_first_item(self):
        item = {"price": 80.0}
        with patch.object(ac, "_run_sync", return_value=[item]):
            result = ac.get_vividseats_price("https://www.vividseats.com/concerts/1")
        assert result == item


# ---------------------------------------------------------------------------
# extract_price_from_result
# ---------------------------------------------------------------------------

class TestExtractPrice:
    def test_lowest_price_key(self):
        assert ac.extract_price_from_result({"lowestPrice": 75.0}) == pytest.approx(75.0)

    def test_min_price_key(self):
        assert ac.extract_price_from_result({"min_price": 50}) == pytest.approx(50.0)

    def test_price_key(self):
        assert ac.extract_price_from_result({"price": 99.99}) == pytest.approx(99.99)

    def test_string_price_with_dollar(self):
        assert ac.extract_price_from_result({"lowestPrice": "$45.00"}) == pytest.approx(45.0)

    def test_nested_price_range(self):
        assert ac.extract_price_from_result({"priceRange": {"min": 60.0}}) == pytest.approx(60.0)

    def test_returns_none_on_missing(self):
        assert ac.extract_price_from_result({}) is None
        assert ac.extract_price_from_result({"name": "Event"}) is None

    def test_ignores_zero_price(self):
        assert ac.extract_price_from_result({"price": 0}) is None

    def test_price_with_comma(self):
        assert ac.extract_price_from_result({"price": "1,200.00"}) == pytest.approx(1200.0)

    def test_prefers_first_match(self):
        # lowestPrice should win over price
        result = ac.extract_price_from_result({"lowestPrice": 50.0, "price": 80.0})
        assert result == pytest.approx(50.0)


# ---------------------------------------------------------------------------
# extract_name_from_result
# ---------------------------------------------------------------------------

class TestExtractName:
    def test_name_key(self):
        assert ac.extract_name_from_result({"name": "Taylor Swift"}) == "Taylor Swift"

    def test_title_key(self):
        assert ac.extract_name_from_result({"title": "Big Concert"}) == "Big Concert"

    def test_event_name_key(self):
        assert ac.extract_name_from_result({"eventName": "Rock Show"}) == "Rock Show"

    def test_default_on_missing(self):
        assert ac.extract_name_from_result({}) == "Event"

    def test_truncates_long_names(self):
        long_name = "A" * 200
        result = ac.extract_name_from_result({"name": long_name})
        assert len(result) <= 120


# ---------------------------------------------------------------------------
# drop_catcher integration — TM fallback counter logic
# ---------------------------------------------------------------------------

class TestDropCatcherFallback:
    """Test that drop_catcher's _TM_FAIL_COUNT logic works correctly."""

    def test_fail_count_resets_on_success(self):
        import drop_catcher as dc_mod
        from unittest.mock import patch as _patch

        original_count = dc_mod._TM_FAIL_COUNT
        try:
            dc_mod._TM_FAIL_COUNT = 2
            mock_resp = MagicMock()
            mock_resp.raise_for_status = MagicMock()
            mock_resp.json.return_value = {"_embedded": {"events": []}}
            with _patch.object(dc_mod, "_requests", MagicMock(get=MagicMock(return_value=mock_resp))), \
                 _patch.dict(os.environ, {"TICKETMASTER_API_KEY": "fake_key"}):
                dc_mod._tm_get_with_fallback({"keyword": "test"})
            assert dc_mod._TM_FAIL_COUNT == 0
        finally:
            dc_mod._TM_FAIL_COUNT = original_count

    def test_fail_count_increments_on_429(self):
        import drop_catcher as dc_mod

        original_count = dc_mod._TM_FAIL_COUNT
        original_reset = dc_mod._TM_FAIL_RESET_AT
        try:
            dc_mod._TM_FAIL_COUNT = 0
            err_resp = MagicMock()
            err_resp.status_code = 429
            http_err = Exception("429 Too Many Requests")
            http_err.response = err_resp
            with patch.object(dc_mod, "_requests", MagicMock(get=MagicMock(side_effect=http_err))), \
                 patch.dict(os.environ, {"TICKETMASTER_API_KEY": "fake_key"}), \
                 patch.object(dc_mod, "_apify", None):
                try:
                    dc_mod._tm_get_with_fallback({"keyword": "test"})
                except Exception:
                    pass
            assert dc_mod._TM_FAIL_COUNT > 0
        finally:
            dc_mod._TM_FAIL_COUNT = original_count
            dc_mod._TM_FAIL_RESET_AT = original_reset
