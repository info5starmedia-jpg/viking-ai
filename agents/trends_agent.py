# agents/trends_agent.py
# ------------------------------------------------------------
# Google Trends Intelligence Agent
# ------------------------------------------------------------

import os
import httpx
from typing import Dict, Any

TAVILY_API_KEY = os.getenv("TAVILY_API_KEY")


async def get_google_trends(keyword: str) -> Dict[str, Any]:
    """
    Uses Tavily search to approximate trend level.
    """

    if not TAVILY_API_KEY:
        return {"error": "missing_tavily_key"}

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(
                "https://api.tavily.com/search",
                headers={"Content-Type": "application/json"},
                json={
                    "api_key": TAVILY_API_KEY,
                    "query": f"Google Trends data for {keyword}",
                    "include_images": False,
                    "max_results": 5
                }
            )
            data = r.json()

        return {
            "keyword": keyword,
            "trend_score": len(data.get("results", [])) * 10, # simple heuristic
            "raw_results": data.get("results", []),
        }

    except Exception as e:
        return {"error": str(e)}
