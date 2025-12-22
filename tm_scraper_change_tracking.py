"""
tm_scraper_change_tracking.py

Utility module for Viking AI:

- scan_recent_changes(hours=24)
    -> returns new/updated concert events in a time window based on
       Ticketmaster's on-sale start times (US/CA, segment=Music).

- scan_tomorrow()
    -> returns all concerts whose on-sale start time is tomorrow (US/CA),
       and writes a CSV file for Excel / Sheets.

Used by:
- /tm_changes
- /tm_tomorrow
- /tm_tomorrow_csv
"""

import os
import csv
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, Any, List, Tuple

import requests
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger("tm_scraper")

TM_API_KEY = os.getenv("TICKETMASTER_API_KEY")
TM_BASE = "https://app.ticketmaster.com/discovery/v2/events.json"
CHANGE_STATE_FILE = "tm_change_state.json"
TOMORROW_CSV_FILE = "tm_tomorrow_music_onsales.csv"

SEGMENT_NAME = "Music"  # concerts
COUNTRY_CODES = ["US", "CA"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _require_api_key() -> None:
    if not TM_API_KEY:
        raise RuntimeError("Missing TICKETMASTER_API_KEY in environment / .env")


def _fmt_utc(dt: datetime) -> str:
    """
    Ticketmaster expects date-time filters in strict UTC ISO format:
    YYYY-MM-DDTHH:mm:ssZ
    """
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _tm_get(params: Dict[str, Any]) -> Dict[str, Any]:
    _require_api_key()
    q = dict(params)
    q["apikey"] = TM_API_KEY
    resp = requests.get(TM_BASE, params=q, timeout=20)
    resp.raise_for_status()
    return resp.json()


def _load_change_state() -> Dict[str, Any]:
    if not os.path.exists(CHANGE_STATE_FILE):
        return {}
    try:
        with open(CHANGE_STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_change_state(state: Dict[str, Any]) -> None:
    try:
        with open(CHANGE_STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)
    except Exception as e:
        logger.warning("Failed to save %s: %s", CHANGE_STATE_FILE, e)


def _normalize_event(e: Dict[str, Any]) -> Dict[str, Any]:
    """
    Flatten key information from a Ticketmaster event.
    """
    dates = e.get("dates") or {}
    start = dates.get("start") or {}
    local_date = start.get("localDate")
    local_time = start.get("localTime")

    venues = (e.get("_embedded") or {}).get("venues", []) or [{}]
    v0 = venues[0] or {}
    city = (v0.get("city") or {}).get("name")
    country = (v0.get("country") or {}).get("name")
    venue_name = v0.get("name")

    atts = (e.get("_embedded") or {}).get("attractions", []) or []
    artist_name = atts[0].get("name") if atts else None

    return {
        "id": e.get("id"),
        "name": e.get("name"),
        "artist": artist_name,
        "url": e.get("url"),
        "event_local_date": local_date,
        "event_local_time": local_time,
        "venue": venue_name,
        "city": city,
        "country": country,
    }


def _paginate_events(params: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Fetch multiple pages with Ticketmaster's page/size system, obeying:
      (page * size) < 1000
    """
    all_events: List[Dict[str, Any]] = []
    size = int(params.get("size", 200))
    max_pages = 1000 // max(size, 1)  # safety

    for page in range(max_pages):
        p = dict(params)
        p["page"] = page
        try:
            data = _tm_get(p)
        except requests.HTTPError as e:
            # If Ticketmaster complains about paging depth or similar, stop.
            logger.warning("Ticketmaster page fetch error on page %d: %s", page, e)
            break

        events = (data.get("_embedded") or {}).get("events", []) or []
        if not events:
            break

        all_events.extend(events)

        # If this is the last page, break
        page_info = data.get("page") or {}
        number = page_info.get("number")
        total_pages = page_info.get("totalPages")
        if total_pages is not None and number is not None and number >= total_pages - 1:
            break

    return all_events


# ---------------------------------------------------------------------------
# scan_recent_changes
# ---------------------------------------------------------------------------

def scan_recent_changes(hours: int = 24) -> Dict[str, Any]:
    """
    Return concerts whose *on-sale window* started in the last `hours`.

    This is an approximation of "new/changed" events: anything that just
    opened for sale in that window is treated as NEW or CHANGED vs prior
    snapshot (using tm_change_state.json).

    Returns:
        {
          "since": "2025-11-18T00:00:00Z",
          "until": "2025-11-19T00:00:00Z",
          "changes": [ {event_dict...}, ... ]
        }
    """
    now_utc = datetime.now(timezone.utc)
    since_dt = now_utc - timedelta(hours=hours)
    since_str = _fmt_utc(since_dt)
    until_str = _fmt_utc(now_utc)

    params: Dict[str, Any] = {
        "countryCode": ",".join(COUNTRY_CODES),
        "segmentName": SEGMENT_NAME,
        # filter by on-sale window
        "onsaleStartDateTime": since_str,
        "onsaleEndDateTime": until_str,
        "size": 200,
        # IMPORTANT: use supported sort field; NO "onsaleStartDate"
        "sort": "date,asc",
    }

    logger.info(
        "Running Ticketmaster change scan hours=%d (%s → %s)",
        hours,
        since_str,
        until_str,
    )

    raw_events = _paginate_events(params)
    state = _load_change_state()
    new_state: Dict[str, Any] = {}
    changes: List[Dict[str, Any]] = []

    for e in raw_events:
        norm = _normalize_event(e)
        eid = norm.get("id")
        if not eid:
            continue

        # Simple hash: name + date + city + country
        key_parts = [
            norm.get("name") or "",
            norm.get("event_local_date") or "",
            norm.get("city") or "",
            norm.get("country") or "",
        ]
        h = "|".join(key_parts)

        old = state.get(eid)
        if not old:
            change_type = "NEW"
            changes.append({**norm, "change_type": change_type})
        else:
            if old.get("hash") != h:
                change_type = "UPDATED"
                changes.append({**norm, "change_type": change_type})

        new_state[eid] = {
            "hash": h,
            "last_seen": _fmt_utc(now_utc),
        }

    # Also keep old state for ids not seen in this window
    for eid, info in state.items():
        if eid not in new_state:
            new_state[eid] = info

    _save_change_state(new_state)

    return {
        "since": since_str,
        "until": until_str,
        "changes": changes,
    }


# ---------------------------------------------------------------------------
# scan_tomorrow (for /tm_tomorrow & /tm_tomorrow_csv)
# ---------------------------------------------------------------------------

def scan_tomorrow() -> Dict[str, Any]:
    """
    Build list of concerts whose on-sale window starts tomorrow (US/CA).

    Returns:
        {
          "date": "YYYY-MM-DD",   # tomorrow's date in UTC
          "events": [ {...}, ... ],
          "csv_path": "tm_tomorrow_music_onsales.csv"
        }
    """
    _require_api_key()

    now_utc = datetime.now(timezone.utc).date()
    tomorrow = now_utc + timedelta(days=1)

    start_dt = datetime(
        year=tomorrow.year,
        month=tomorrow.month,
        day=tomorrow.day,
        hour=0,
        minute=0,
        second=0,
        tzinfo=timezone.utc,
    )
    end_dt = start_dt + timedelta(days=1)

    start_str = _fmt_utc(start_dt)
    end_str = _fmt_utc(end_dt)

    params: Dict[str, Any] = {
        "countryCode": ",".join(COUNTRY_CODES),
        "segmentName": SEGMENT_NAME,
        "onsaleStartDateTime": start_str,
        "onsaleEndDateTime": end_str,
        "size": 200,
        # IMPORTANT: only supported sort fields ("date,asc" etc.)
        "sort": "date,asc",
    }

    logger.info("Building tomorrow music on-sale list for %s", tomorrow.isoformat())
    raw_events = _paginate_events(params)

    events_norm = []
    for e in raw_events:
        events_norm.append(_normalize_event(e))

    # Write CSV for tm_tomorrow_csv
    csv_path = os.path.join(os.getcwd(), TOMORROW_CSV_FILE)
    fieldnames = [
        "id",
        "name",
        "artist",
        "event_local_date",
        "event_local_time",
        "venue",
        "city",
        "country",
        "url",
    ]

    try:
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for ev in events_norm:
                writer.writerow(ev)
    except Exception as e:
        logger.warning("Failed to write tomorrow CSV: %s", e)

    return {
        "date": tomorrow.isoformat(),
        "events": events_norm,
        "csv_path": csv_path,
    }


# ---------------------------------------------------------------------------
# CLI debug usage
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # Simple local test from PowerShell:
    #   python tm_scraper_change_tracking.py
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    print("Running Ticketmaster change scan (last 24h, concerts, US/CA)...")
    try:
        res_changes = scan_recent_changes(hours=24)
        ch = res_changes.get("changes", [])
        print(
            f"- Window: {res_changes.get('since')} → {res_changes.get('until')}, "
            f"{len(ch)} change(s)"
        )
        if ch:
            for ev in ch[:5]:
                print(
                    f"  {ev.get('change_type','?')} – {ev.get('name','?')} – "
                    f"{ev.get('event_local_date','TBA')} – {ev.get('city','')}, {ev.get('country','')}"
                )
    except Exception as e:
        print("!! Error in scan_recent_changes:", e)

    print("\nBuilding tomorrow music on-sale CSV...")
    try:
        res_tom = scan_tomorrow()
        print(
            f"- Date: {res_tom.get('date')}, "
            f"events in CSV: {len(res_tom.get('events', []))}"
        )
        print(f"- CSV: {res_tom.get('csv_path')}")
    except Exception as e:
        print("!! Error in scan_tomorrow:", e)
