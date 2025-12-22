# tour_news_agent_v3.py
# ------------------------------------------------------------
# Tour news & rumor intelligence using Tavily
# ------------------------------------------------------------

import os
import httpx
from typing import Dict, Any, List

TAVILY_API_KEY = os.getenv("TAVILY_API_KEY")


async def fetch_news(artist: str) -> List[Dict[str, Any]]:
    if not TAVILY_API_KEY:
        return []

    query = f"{artist} tour dates tickets presale on sale added show postponed canceled venue upgrade interview"

    payload = {
        "api_key": TAVILY_API_KEY,
        "query": query,
        "search_depth": "advanced",
        "max_results": 15,
    }

    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.post("https://api.tavily.com/search", json=payload)
        r.raise_for_status()
        data = r.json()

    raw = data.get("results", [])
    results: List[Dict[str, Any]] = []

    for item in raw:
        title = item.get("title", "") or ""
        snippet = item.get("content", "") or ""
        url = item.get("url", "")

        text = (title + " " + snippet).lower()

        relevance = 0
        if artist.lower() in text:
            relevance += 40
        if any(w in text for w in ["tour", "concert", "show", "residency", "festival"]):
            relevance += 30
        if any(w in text for w in ["sold out", "sell out", "added date", "second show"]):
            relevance += 20
        if any(w in text for w in ["cancel", "postpone", "moved", "rescheduled"]):
            relevance += 10

        results.append({
            "title": title,
            "snippet": snippet[:220] + ("..." if len(snippet) > 220 else ""),
            "url": url,
            "relevance": relevance,
        })

    results_sorted = sorted(results, key=lambda x: x["relevance"], reverse=True)
    return results_sorted
