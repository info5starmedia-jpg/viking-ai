# tour_news_agent_v3.py
# ------------------------------------------------------------
# Tour news & rumor intelligence using Tavily
# Provides: get_tour_news(artist_name) -> str
# ------------------------------------------------------------

from __future__ import annotations

import os
import asyncio
import hashlib
from typing import Dict, Any, List, Optional

import httpx

TAVILY_API_KEY = os.getenv("TAVILY_API_KEY")


async def _fetch_news_async(artist: str) -> List[Dict[str, Any]]:
    if not TAVILY_API_KEY:
        return []

    query = (
        f"{artist} tour dates tickets presale on sale added show "
        f"postponed canceled venue upgrade interview"
    )

    payload = {
        "api_key": TAVILY_API_KEY,
        "query": query,
        "search_depth": "advanced",
        "max_results": 15,
    }

    async with httpx.AsyncClient(timeout=25) as client:
        r = await client.post("https://api.tavily.com/search", json=payload)
        r.raise_for_status()
        data = r.json()

    raw = data.get("results", []) or []
    results: List[Dict[str, Any]] = []

    for item in raw:
        title = (item.get("title") or "").strip()
        snippet = (item.get("content") or "").strip()
        url = (item.get("url") or "").strip()

        text = (title + " " + snippet).lower()
        a = artist.lower().strip()

        relevance = 0
        if a and a in text:
            relevance += 40
        if any(w in text for w in ["tour", "concert", "show", "residency", "festival"]):
            relevance += 30
        if any(w in text for w in ["sold out", "sell out", "added date", "second show", "new date", "announces"]):
            relevance += 20
        if any(w in text for w in ["cancel", "postpone", "moved", "rescheduled"]):
            relevance += 10

        hid = hashlib.sha1(url.encode("utf-8")).hexdigest()[:16] if url else hashlib.sha1(title.encode("utf-8")).hexdigest()[:16]

        results.append(
            {
                "id": hid,
                "title": title,
                "snippet": (snippet[:220] + ("..." if len(snippet) > 220 else "")) if snippet else "",
                "url": url,
                "relevance": relevance,
            }
        )

    return sorted(results, key=lambda x: x.get("relevance", 0), reverse=True)


def _run(coro) -> Any:
    """
    Safely run an async coroutine from sync context.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        # If we're already in an event loop, create a new task and wait for it
        return asyncio.run_coroutine_threadsafe(coro, loop).result(timeout=30)
    return asyncio.run(coro)


def get_tour_news(artist_name: str, max_items: int = 5) -> str:
    """
    Sync wrapper used by bot.py.
    Returns a human-readable string.
    """
    artist = (artist_name or "").strip()
    if not artist:
        return "❌ Missing artist name."

    if not TAVILY_API_KEY:
        return "ℹ️ Tour news is disabled (missing TAVILY_API_KEY)."

    try:
        items = _run(_fetch_news_async(artist))
    except Exception as e:
        return f"❌ Tour news lookup failed: {e}"

    items = items[: max_items or 5]
    if not items:
        return f"📰 No recent tour news found for **{artist}**."

    lines = [f"📰 **Tour news (Tavily): {artist}**"]
    for i, it in enumerate(items, start=1):
        title = it.get("title") or "Untitled"
        url = it.get("url") or ""
        snippet = it.get("snippet") or ""
        lines.append(f"{i}. **{title}**")
        if snippet:
            lines.append(f"   - {snippet}")
        if url:
            lines.append(f"   - {url}")

    return "\n".join(lines)
