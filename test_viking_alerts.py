from viking_alerts import ABParts, extract_artist_from_title


def test_extract_artist_from_title():
    assert extract_artist_from_title("Taylor Swift - The Eras Tour") == "Taylor Swift"
    assert extract_artist_from_title("New Tour Item: Drake — 2024 Dates") == "Drake"


def test_abparts_combined_truncation():
    fast = "A" * 1000
    full = "B" * 1200
    combined = ABParts(fast=fast, full=full).combined(max_chars=1900)
    assert len(combined) <= 1900
    assert combined.endswith("…")