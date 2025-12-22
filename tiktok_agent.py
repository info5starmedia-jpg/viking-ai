# agents/tiktok_agent.py
# ------------------------------------------------------------
# TikTok Intelligence Agent
# Returns lightweight viral indicators via TikAPI-style logic
# ------------------------------------------------------------

import os
import httpx
from typing import Dict, Any

TIKTOK_API_KEY = os.getenv("TIKTOK_API_KEY")


async def get_tiktok_stats(artist_name: str) -> Dict[str, Any]:
    """
    Returns approximate TikTok engagement:
      - hashtag view count
      - recent spike indicator
    """

    if not TIKTOK_API_KEY:
        return {"error": "missing_tiktok_api_key"}

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                "https://api.tikapi.io/public/hashtag/search",
                params={"q": artist_name},
                headers={"X-API-Key": TIKTOK_API_KEY}
            )
            data = r.json()

        if "hashtags" not in data:
            return {"error": "no_data"}

        tag = data["hashtags"][0]

        return {
            "name": tag.get("name"),
            "views": tag.get("stats", {}).get("views", 0),
            "weekly_growth": tag.get("stats", {}).get("weekly_growth", 0),
            "trend": "rising" if tag.get("stats", {}).get("weekly_growth", 0) > 0 else "stable",
        }

    except Exception as e:
        return {"error": str(e)}
