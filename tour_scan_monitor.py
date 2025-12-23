"""tour_scan_monitor.py

Viking AI — Tour Scan Monitor
-----------------------------
Polls a small set of RSS feeds for likely tour-announcement hits.

Enhancement:
  • When we detect a likely tour-announcement headline, we attempt to infer the
    artist name from the title and attach a quick artist-rating block.

How it posts:
  - If `discord_client` and `channel_id` provided: posts in Discord.
  - Else if `TOUR_SCAN_WEBHOOK_URL` is set: posts to the webhook.

Notes:
  - Everything is best-effort and failure-safe (no crash loops).
  - Keep requests light; cache is handled in underlying agents.
"""

from __future__ import annotations

import os
import time
import hashlib
import logging
from typing import List, Dict, Any, Optional
from datetime import datetime

import requests
import feedparser

import viking_db

from agents.artist_resolver import resolve_artist
from agents.artist_rating_engine import rate_artist, stars_to_emoji
from agents.demand_heatmap import top_cities_for_artist


logger = logging.getLogger("tour_scan")

POLL_INTERVAL_SECONDS = 60 * 60  # 60 minutes

TOUR_SCAN_WEBHOOK_URL = (os.getenv("TOUR_SCAN_WEBHOOK_URL") or "").strip()

# Keywords that usually mean "new tour info"
TRIGGERS = [
    "announces tour", "tour announced", "new tour", "adds show", "added show",
    "adds date", "added date", "second show", "new dates", "additional dates",
    "on sale", "presale", "tickets on sale", "rescheduled", "postponed", "cancelled", "canceled",
]

# Noise words that commonly create false positives
NOISE = [
    "review", "album review", "track review", "interview", "op-ed", "opinion",
    "how to get tickets", "ticket tips", "fan education",
]

# RSS sources (best effort). If a feed breaks, it simply yields 0 items.
RSS_SOURCES = [
    {"name": "Billboard", "url": "https://www.billboard.com/c/music/music-news/feed/"},
    {"name": "RollingStone", "url": "https://www.rollingstone.com/music/music-news/feed/"},
    {"name": "Pitchfork", "url": "https://pitchfork.com/rss/news/"},
    {"name": "Chorus.fm", "url": "https://chorus.fm/feed/"},
]


def _score_item(title: str, summary: str) -> int:
    t = (title or "").lower()
    s = (summary or "").lower()
    text = t + " " + s

    if any(n in text for n in NOISE):
        return 0

    score = 0
    for trig in TRIGGERS:
        if trig in text:
            score += 20

    for w in ["tour", "dates", "shows", "tickets", "presale"]:
        if w in text:
            score += 5

    return score


def _seen_table(conn) -> None:
    cur = conn.cursor()
    cur.execute("CREATE TABLE IF NOT EXISTS tour_scan_seen (id TEXT PRIMARY KEY, created_at REAL)")
    conn.commit()


def _is_seen(conn, sid: str) -> bool:
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM tour_scan_seen WHERE id=?", (sid,))
    return cur.fetchone() is not None


def _mark_seen(conn, sid: str) -> None:
    cur = conn.cursor()
    cur.execute("INSERT OR IGNORE INTO tour_scan_seen (id, created_at) VALUES (?, ?)", (sid, time.time()))
    conn.commit()


def _make_id(url: str) -> str:
    return hashlib.sha1((url or "").encode("utf-8")).hexdigest()[:16]


def fetch_rss_items() -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for src in RSS_SOURCES:
        try:
            feed = feedparser.parse(src["url"])
            for e in (feed.entries or [])[:30]:
                title = getattr(e, "title", "") or ""
                link = getattr(e, "link", "") or ""
                summary = getattr(e, "summary", "") or ""
                published = getattr(e, "published", "") or ""

                score = _score_item(title, summary)
                if score < 25:
                    continue

                out.append({
                    "source": src["name"],
                    "title": title.strip(),
                    "url": link.strip(),
                    "summary": (summary or "")[:240],
                    "published": published,
                    "score": score,
                })
        except Exception as ex:
            logger.warning("RSS fetch failed for %s: %s", src["name"], ex)
    out.sort(key=lambda x: x.get("score", 0), reverse=True)
    return out


def post_webhook(msg: str) -> None:
    if not TOUR_SCAN_WEBHOOK_URL:
        logger.info("TOUR_SCAN_WEBHOOK_URL not set, skipping webhook post")
        return
    try:
        requests.post(TOUR_SCAN_WEBHOOK_URL, json={"content": msg}, timeout=15).raise_for_status()
    except Exception as e:
        logger.warning("Webhook post failed: %s", e)


def _guess_artist_from_title(title: str) -> Optional[str]:
    """Heuristic artist extraction from common headline formats."""
    t = (title or "").strip()
    if not t:
        return None

    # Common patterns: "Artist announces ...", "Artist adds ...", "Artist — ..."
    lower = t.lower()
    for token in [" announces ", " add", " adds ", " unveil", " unveils ", " reveal", " reveals "]:
        idx = lower.find(token)
        if idx > 0:
            cand = t[:idx].strip(" -–—:|")
            return cand if 2 <= len(cand) <= 60 else None

    for sep in [" — ", " – ", " - ", ": "]:
        if sep in t:
            cand = t.split(sep, 1)[0].strip()
            return cand if 2 <= len(cand) <= 60 else None

    return None


def _build_enrichment_block(artist_guess: str) -> str:
    """Fast enrichment: rating + best cities (no event scraping here)."""
    try:
        resolved = resolve_artist(artist_guess)
        canonical = resolved.get("name") or artist_guess
        spotify = resolved.get("spotify") or {}
        youtube = resolved.get("youtube") or {}
        rating = rate_artist(spotify=spotify, youtube=youtube, tiktok={})
        stars = stars_to_emoji(rating.get("stars", 1))
        cities = top_cities_for_artist(canonical, 5)
        cities_txt = ", ".join([c for c, _w in (cities or [])]) or "(no city data yet)"
        official = ((resolved.get("ticketmaster") or {}).get("official_site") or "").strip()
        return (
            f"\n\n🎸 **Artist Intel (fast)**\n"
            f"{stars} **{canonical}** — {rating.get('label','')} (score {rating.get('score','—')}/100)\n"
            f"🔥 Best cities: {cities_txt}\n"
            f"🌐 Official: {official if official else '(not found)'}"
        )
    except Exception:
        return ""


def poll_tour_scan_loop(discord_client=None, channel_id: Optional[int] = None) -> None:
    """Runs forever. If discord_client+channel_id provided, posts there."""
    conn = viking_db.get_db_connection()
    _seen_table(conn)

    logger.info("Tour scan loop started (60-minute interval).")

    while True:
        try:
            hits = fetch_rss_items()
            for h in hits[:15]:
                sid = _make_id(h["url"])
                if _is_seen(conn, sid):
                    continue
                _mark_seen(conn, sid)

                title = h.get("title", "")
                artist_guess = _guess_artist_from_title(title)
                enrichment = _build_enrichment_block(artist_guess) if artist_guess else ""

                msg = (
                    f"🧭 **Tour Scan Hit** ({h['source']})\n"
                    f"**{title}**\n"
                    f"{h['url']}\n"
                    f"_score={h['score']} • {datetime.utcnow().isoformat()} UTC_"
                    f"{enrichment}"
                )

                if discord_client and channel_id:
                    ch = discord_client.get_channel(int(channel_id))
                    if ch:
                        try:
                            discord_client.loop.create_task(ch.send(msg[:1900]))
                        except Exception:
                            post_webhook(msg[:1900])
                    else:
                        post_webhook(msg[:1900])
                else:
                    post_webhook(msg[:1900])

        except Exception:
            logger.exception("Tour scan polling error")

        time.sleep(POLL_INTERVAL_SECONDS)

