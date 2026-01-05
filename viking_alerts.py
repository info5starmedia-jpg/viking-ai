import datetime
import importlib
import logging
import re
from dataclasses import dataclass
from typing import Iterable, List, Optional

logger = logging.getLogger("viking_alerts")

SAFE_MAX_CHARS = 1900


@dataclass
class ABParts:
    fast: str
    full: str

    def combined(self, max_chars: int = SAFE_MAX_CHARS) -> str:
        parts = [p.strip() for p in [self.fast, self.full] if p and p.strip()]
        combined = "\n\n".join(parts).strip()
        return _truncate_text(combined, max_chars)


def _truncate_text(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[: max(0, max_chars - 1)] + "…"


def _clean_title(title: str) -> str:
    cleaned = (title or "").strip()
    cleaned = re.sub(r"^[\W_]+", "", cleaned)
    cleaned = re.sub(r"^(new\s+tour\s+item:|tour\s+scan:|tour\s+alert:|tour:)\s*", "", cleaned, flags=re.IGNORECASE)
    return cleaned.strip()


def extract_artist_from_title(title: str) -> str:
    cleaned = _clean_title(title)
    if not cleaned:
        return ""
    for sep in (" - ", " — ", " – ", " | ", ": "):
        if sep in cleaned:
            return cleaned.split(sep)[0].strip()
    return cleaned.strip()


def _try_import_ticketmaster():
    for module_name in ("ticketmaster_agent", "ticketmaster_agent_v2"):
        try:
            module = importlib.import_module(module_name)
        except Exception as exc:
            logger.debug("ticketmaster import failed for %s: %s", module_name, exc)
            continue
        return module
    return None


def try_fetch_events_for_artist(artist: str) -> List[dict]:
    if not artist:
        return []
    module = _try_import_ticketmaster()
    if not module:
        return []
    search_fn = getattr(module, "search_events_for_artist", None)
    if not callable(search_fn):
        return []
    try:
        return list(search_fn(artist, size=10) or [])
    except Exception as exc:
        logger.warning("Ticketmaster search failed for %s: %s", artist, exc)
        return []


def _parse_date(date_str: Optional[str]) -> Optional[datetime.date]:
    if not date_str:
        return None
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%m/%d/%Y"):
        try:
            return datetime.datetime.strptime(date_str, fmt).date()
        except Exception:
            continue
    return None


def compute_sellout_score(event: dict) -> int:
    score = 45
    venue = (event.get("venue") or "").lower()
    if venue:
        if any(token in venue for token in ("arena", "stadium", "center", "theatre", "theater")):
            score += 10
        if any(token in venue for token in ("club", "hall", "amphitheatre", "amphitheater")):
            score += 6
    if event.get("city"):
        score += 10

    date_val = _parse_date(event.get("date"))
    if date_val:
        days_out = (date_val - datetime.date.today()).days
        if days_out <= 30:
            score += 15
        elif days_out <= 90:
            score += 8

    onsale = event.get("on_sale") or event.get("onsale") or event.get("public_onsale") or event.get("onsale_date")
    onsale_date = _parse_date(onsale) if isinstance(onsale, str) else None
    if onsale_date:
        days_out = (onsale_date - datetime.date.today()).days
        if days_out <= 7:
            score += 10
        elif days_out <= 30:
            score += 5

    return max(0, min(100, score))


def _unique_cities(events: Iterable[dict], limit: int = 5) -> List[str]:
    seen = []
    for event in events:
        city = (event.get("city") or "").strip()
        if city and city not in seen:
            seen.append(city)
        if len(seen) >= limit:
            break
    return seen


def format_fast_alert(headline: str, cities: List[str], why_lines: List[str]) -> str:
    headline = (headline or "Tour activity detected").strip()
    cities = [c for c in (cities or []) if c]
    why_lines = [w for w in (why_lines or []) if w]

    lines = [f"⚡ FAST ALERT — {headline}"]
    if cities:
        lines.append("🏙️ Top cities: " + ", ".join(cities[:5]))
    else:
        lines.append("🏙️ Top cities: TBA")

    if len(why_lines) < 2:
        why_lines.append("Momentum is building — watch on-sale demand.")
    why_lines = why_lines[:4]
    lines.append("Why it matters:")
    lines.extend([f"• {line}" for line in why_lines])

    return "\n".join(lines).strip()


def _format_event_line(event: dict) -> List[str]:
    date = event.get("date") or "TBA"
    city = event.get("city") or "TBA"
    venue = event.get("venue") or "TBA"
    score = compute_sellout_score(event)
    line = f"• {date} — {city} — {venue} (Sellout: {score}/100)"
    extra_lines = [line]
    onsale = event.get("on_sale") or event.get("onsale") or event.get("public_onsale") or event.get("onsale_date")
    if onsale:
        extra_lines.append(f"  On-sale: {onsale}")
    return extra_lines


def format_full_intel(artist: str, events: List[dict]) -> str:
    header = f"📌 FULL INTEL — {artist or 'Unknown Artist'}"
    if not events:
        return "\n".join([header, "No Ticketmaster events found yet. Keep monitoring."])

    lines = [header, "Upcoming events:"]
    max_events = 8
    for event in events[:max_events]:
        lines.extend(_format_event_line(event))

    if len(events) > max_events:
        lines.append(f"…and {len(events) - max_events} more events.")

    return "\n".join(lines).strip()


def build_ab_alert_from_tour_item(item: dict) -> ABParts:
    title = (item.get("title") or "").strip()
    link = (item.get("link") or "").strip()
    published = (item.get("published") or "").strip()

    artist = extract_artist_from_title(title) or title or "Unknown Artist"
    events = try_fetch_events_for_artist(artist)

    cities = _unique_cities(events)
    why_lines = ["New tour activity detected via Tour Scan."]
    if published:
        why_lines.append(f"Signal timestamp: {published}")
    if link:
        why_lines.append("Source link available for verification.")
    if events:
        why_lines.append(f"Ticketmaster shows {len(events)} upcoming events.")

    headline = title or f"{artist} tour activity"
    fast = format_fast_alert(headline, cities, why_lines)
    full = format_full_intel(artist, events)
    return ABParts(fast=fast, full=full)


def build_ab_alert_for_tm_surge(artist: str, note: str, events: Optional[List[dict]] = None) -> ABParts:
    artist = (artist or "").strip() or "Unknown Artist"
    note = (note or "Demand surge detected.").strip()

    if events is None:
        events = try_fetch_events_for_artist(artist)

    cities = _unique_cities(events)
    why_lines = [note, "Inventory shifts can accelerate sellouts.", "Confirm pricing strategy and watch presales."]
    headline = f"{artist} demand surge"

    fast = format_fast_alert(headline, cities, why_lines)
    full = format_full_intel(artist, events)
    return ABParts(fast=fast, full=full)
