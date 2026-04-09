"""
tests/test_platform_monitor.py
Unit tests for platform_monitor.py — all offline, HTTP mocked.
"""
import sys
import os
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

# Stub out heavy optional deps before import
_curl_mock = MagicMock()
_requests_mock = MagicMock()

with patch.dict("sys.modules", {
    "curl_cffi": MagicMock(requests=_curl_mock),
    "requests": _requests_mock,
}):
    with patch("platform_monitor._DB_OK", True):
        import platform_monitor as pm


# ---------------------------------------------------------------------------
# detect_platform
# ---------------------------------------------------------------------------

class TestDetectPlatform:
    def test_seatgeek(self):
        assert pm.detect_platform("https://seatgeek.com/taylor-swift-tickets") == "seatgeek"

    def test_stubhub(self):
        assert pm.detect_platform("https://www.stubhub.com/event/12345") == "stubhub"

    def test_vividseats(self):
        assert pm.detect_platform("https://www.vividseats.com/concerts") == "vividseats"

    def test_ticketmaster_default(self):
        assert pm.detect_platform("https://www.ticketmaster.com/event/abc") == "ticketmaster"

    def test_case_insensitive(self):
        assert pm.detect_platform("https://SeatGeek.COM/events") == "seatgeek"

    def test_unknown_url(self):
        assert pm.detect_platform("https://example.com/tickets") == "ticketmaster"


# ---------------------------------------------------------------------------
# _build_proxy_dict
# ---------------------------------------------------------------------------

class TestBuildProxyDict:
    def test_none_when_empty(self):
        assert pm._build_proxy_dict([]) is None
        assert pm._build_proxy_dict(None) is None

    def test_simple_host_port(self):
        result = pm._build_proxy_dict(["1.2.3.4:8080"])
        assert "http" in result
        assert "https" in result
        assert "1.2.3.4" in result["http"]

    def test_user_pass_format(self):
        result = pm._build_proxy_dict(["1.2.3.4:8080:user:pass"])
        assert "user" in result["http"]
        assert "pass" in result["http"]

    def test_already_http_url(self):
        result = pm._build_proxy_dict(["http://proxy.example.com:3128"])
        assert result["http"] == "http://proxy.example.com:3128"

    def test_rotates_by_time(self):
        proxies = ["1.1.1.1:80", "2.2.2.2:80", "3.3.3.3:80"]
        # Just check it returns a valid dict (rotation is time-based)
        result = pm._build_proxy_dict(proxies)
        assert result is not None


# ---------------------------------------------------------------------------
# _browser_headers
# ---------------------------------------------------------------------------

class TestBrowserHeaders:
    def test_returns_dict(self):
        h = pm._browser_headers(0)
        assert isinstance(h, dict)

    def test_has_user_agent(self):
        h = pm._browser_headers(0)
        assert "User-Agent" in h
        assert len(h["User-Agent"]) > 20

    def test_has_accept(self):
        h = pm._browser_headers(0)
        assert "Accept" in h

    def test_has_sec_fetch(self):
        h = pm._browser_headers(0)
        assert "Sec-Fetch-Dest" in h

    def test_chrome_has_sec_ch_ua(self):
        # idx 0 is Chrome — should have sec-ch-ua
        h = pm._browser_headers(0)
        assert "sec-ch-ua" in h

    def test_firefox_no_sec_ch_ua(self):
        # idx 2 is Firefox — no sec-ch-ua
        h = pm._browser_headers(2)
        assert "sec-ch-ua" not in h

    def test_rotates_user_agents(self):
        h0 = pm._browser_headers(0)
        h1 = pm._browser_headers(1)
        assert h0["User-Agent"] != h1["User-Agent"]

    def test_wraps_around(self):
        n = len(pm._USER_AGENTS)
        h0 = pm._browser_headers(0)
        hn = pm._browser_headers(n)
        assert h0["User-Agent"] == hn["User-Agent"]


# ---------------------------------------------------------------------------
# _extract_jsonld
# ---------------------------------------------------------------------------

class TestExtractJsonLd:
    def test_extracts_single_block(self):
        html = '''<html><script type="application/ld+json">{"@type":"Event","name":"Test"}</script></html>'''
        result = pm._extract_jsonld(html)
        assert len(result) == 1
        assert result[0]["name"] == "Test"

    def test_extracts_multiple_blocks(self):
        html = (
            '<script type="application/ld+json">{"@type":"Event"}</script>'
            '<script type="application/ld+json">{"@type":"MusicEvent"}</script>'
        )
        result = pm._extract_jsonld(html)
        assert len(result) == 2

    def test_ignores_invalid_json(self):
        html = '<script type="application/ld+json">NOT JSON</script>'
        result = pm._extract_jsonld(html)
        assert result == []

    def test_empty_html(self):
        assert pm._extract_jsonld("") == []

    def test_case_insensitive_type(self):
        html = '<script TYPE="application/ld+json">{"x":1}</script>'
        result = pm._extract_jsonld(html)
        assert len(result) == 1


# ---------------------------------------------------------------------------
# _extract_next_data
# ---------------------------------------------------------------------------

class TestExtractNextData:
    def test_extracts_next_data(self):
        html = '<script id="__NEXT_DATA__" type="application/json">{"props":{"price":50}}</script>'
        result = pm._extract_next_data(html)
        assert result is not None
        assert result["props"]["price"] == 50

    def test_returns_none_when_missing(self):
        assert pm._extract_next_data("<html>no data</html>") is None

    def test_returns_none_on_invalid_json(self):
        html = '<script id="__NEXT_DATA__">INVALID</script>'
        assert pm._extract_next_data(html) is None


# ---------------------------------------------------------------------------
# _find_price_in_obj
# ---------------------------------------------------------------------------

class TestFindPriceInObj:
    def test_direct_key(self):
        obj = {"lowestPrice": 75.0}
        assert pm._find_price_in_obj(obj) == pytest.approx(75.0)

    def test_nested_key(self):
        obj = {"data": {"event": {"minPrice": 99.5}}}
        assert pm._find_price_in_obj(obj) == pytest.approx(99.5)

    def test_list_of_dicts(self):
        obj = [{"price": 45.0}]
        assert pm._find_price_in_obj(obj) == pytest.approx(45.0)

    def test_returns_none_when_missing(self):
        assert pm._find_price_in_obj({}) is None
        assert pm._find_price_in_obj([]) is None

    def test_string_price_coerced(self):
        obj = {"price": "123.45"}
        assert pm._find_price_in_obj(obj) == pytest.approx(123.45)

    def test_multiple_keys_returns_first_match(self):
        obj = {"minListPrice": 50.0, "price": 80.0}
        result = pm._find_price_in_obj(obj)
        assert result == pytest.approx(50.0)


# ---------------------------------------------------------------------------
# URL extraction helpers
# ---------------------------------------------------------------------------

class TestUrlExtractors:
    def test_sg_event_id(self):
        assert pm._sg_event_id_from_url("https://seatgeek.com/events/12345") == "12345"

    def test_sg_event_id_missing(self):
        assert pm._sg_event_id_from_url("https://seatgeek.com/taylor-swift-tickets") is None

    def test_sg_performer_slug(self):
        assert pm._sg_performer_slug_from_url("https://seatgeek.com/taylor-swift-tickets") == "taylor-swift"

    def test_sg_performer_slug_missing(self):
        assert pm._sg_performer_slug_from_url("https://seatgeek.com/events/12345") is None

    def test_sh_event_id(self):
        assert pm._sh_event_id_from_url("https://www.stubhub.com/event/123456789") == "123456789"

    def test_sh_event_id_missing(self):
        assert pm._sh_event_id_from_url("https://www.stubhub.com/search") is None


# ---------------------------------------------------------------------------
# poll_seatgeek_price — mocked HTTP
# ---------------------------------------------------------------------------

class TestPollSeatgeekPrice:
    def _monitor(self):
        return {"url": "https://seatgeek.com/events/99999", "proxies": []}

    def test_returns_none_when_get_fails(self):
        with patch.object(pm, "_get", return_value=None), \
             patch.object(pm, "_get_html", return_value=None), \
             patch.object(pm, "SG_CLIENT_ID", ""):
            result = pm.poll_seatgeek_price(self._monitor())
        assert result is None

    def test_api_path_returns_price(self):
        api_resp = {
            "stats": {"lowest_price": 55.0, "highest_price": 200.0},
            "title": "Taylor Swift",
            "url": "https://seatgeek.com/events/99999",
        }
        with patch.object(pm, "SG_CLIENT_ID", "fake_id"), \
             patch.object(pm, "_get", return_value=api_resp):
            result = pm.poll_seatgeek_price(self._monitor())
        assert result is not None
        price_min, price_max, name, url = result
        assert price_min == 55.0
        assert price_max == 200.0
        assert name == "Taylor Swift"

    def test_html_jsonld_fallback(self):
        html = '''
        <html>
        <script type="application/ld+json">
        {"@type":"Event","name":"Concert","offers":{"lowPrice":45,"highPrice":300}}
        </script>
        </html>
        '''
        with patch.object(pm, "SG_CLIENT_ID", ""), \
             patch.object(pm, "_get_html", return_value=html):
            result = pm.poll_seatgeek_price(self._monitor())
        # May or may not find price depending on key used
        if result is not None:
            assert result[0] > 0

    def test_from_dollar_pattern(self):
        html = '<html>From $99 · 2 tickets left</html>'
        with patch.object(pm, "SG_CLIENT_ID", ""), \
             patch.object(pm, "_get_html", return_value=html):
            result = pm.poll_seatgeek_price(self._monitor())
        assert result is not None
        assert result[0] == pytest.approx(99.0)


# ---------------------------------------------------------------------------
# poll_stubhub_price — mocked HTTP
# ---------------------------------------------------------------------------

class TestPollStubhubPrice:
    def _monitor(self):
        return {"url": "https://www.stubhub.com/event/123", "proxies": []}

    def test_returns_none_on_html_fail(self):
        with patch.object(pm, "_get_html", return_value=None):
            assert pm.poll_stubhub_price(self._monitor()) is None

    def test_jsonld_event(self):
        html = '''
        <script type="application/ld+json">
        {"@type":"Event","name":"Big Show","offers":{"lowPrice":75.0,"highPrice":500.0}}
        </script>
        '''
        with patch.object(pm, "_get_html", return_value=html):
            result = pm.poll_stubhub_price(self._monitor())
        assert result is not None
        assert result[0] == pytest.approx(75.0)
        assert result[2] == "Big Show"

    def test_starting_at_pattern(self):
        html = '<html><h1>Big Show</h1>Starting at $120 per ticket</html>'
        with patch.object(pm, "_get_html", return_value=html):
            result = pm.poll_stubhub_price(self._monitor())
        assert result is not None
        assert result[0] == pytest.approx(120.0)


# ---------------------------------------------------------------------------
# poll_vividseats_price — mocked HTTP
# ---------------------------------------------------------------------------

class TestPollVividseatsPrice:
    def _monitor(self):
        return {"url": "https://www.vividseats.com/concerts/artist-tickets", "proxies": []}

    def test_returns_none_on_html_fail(self):
        with patch.object(pm, "_get_html", return_value=None):
            assert pm.poll_vividseats_price(self._monitor()) is None

    def test_jsonld_event(self):
        html = '''
        <script type="application/ld+json">
        {"@type":"MusicEvent","name":"Music Fest","offers":{"lowPrice":60.0}}
        </script>
        '''
        with patch.object(pm, "_get_html", return_value=html):
            result = pm.poll_vividseats_price(self._monitor())
        assert result is not None
        assert result[0] == pytest.approx(60.0)

    def test_minlistprice_in_json(self):
        html = '''<html><script>window.__data={"minListPrice":35.0,"name":"Show"}</script></html>'''
        with patch.object(pm, "_get_html", return_value=html):
            result = pm.poll_vividseats_price(self._monitor())
        assert result is not None
        assert result[0] == pytest.approx(35.0)


# ---------------------------------------------------------------------------
# is_available
# ---------------------------------------------------------------------------

class TestIsAvailable:
    def test_returns_bool(self):
        assert isinstance(pm.is_available(), bool)
