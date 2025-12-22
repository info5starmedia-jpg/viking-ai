import hashlib
import logging
from datetime import datetime
from viking_db import store_news_item
from tour_scan.sources import (
    billboard,
    rollingstone,
    pitchfork,
    pollstar,
    chorusfm,
    reddit,
    tiktok_news,
)

logger = logging.getLogger("tour_scan")

SOURCES = [
    billboard,
    rollingstone,
    pitchfork,
    pollstar,
    chorusfm,
    reddit,
    tiktok_news,
]

def _fingerprint(item: dict) -> str:
    raw = f"{item.get('artist','')}|{item.get('title','')}|{item.get('url','')}"
    return hashlib.sha256(raw.encode()).hexdigest()

def run_tour_scan(notify_callback):
    logger.info("Running tour scan...")
    new_hits = []

    for src in SOURCES:
        try:
            results = src.fetch()
            for r in results:
                fp = _fingerprint(r)
                if not store_news_item(
                    artist=r["artist"],
                    title=r["title"],
                    url=r["url"],
                    source=r["source"],
                ):
                    continue  # already seen

                new_hits.append(r)

        except Exception:
            logger.exception(f"Source failed: {src.__name__}")

    for hit in new_hits:
        notify_callback(hit)

    logger.info(f"Tour scan complete — new items: {len(new_hits)}")
