"""
agents/tavily_agent.py

Thin, hardened wrapper around the Tavily HTTP API.

- Reads TAVILY_API_KEY from your .env
- Provides:
    - tavily_search() returning normalized JSON
    - search_news() convenience wrapper used by bot.py
- Raises TavilyError with a clear message on failure.
"""

from __future__ import annotations

import os
import logging
from typing import Any, Dict, Optional, List

import requests
from dotenv import load_dotenv

log = logging.getLogger("tavily_agent")

# Load .env so TAVILY_API_KEY is available even when run standalone
load_dotenv()

TAVILY_API_KEY: Optional[str] = os.getenv("TAVILY_API_KEY")
TAVILY_API_URL: str = "https://api.tavily.com/search"
DEFAULT_MAX_RESULTS: int = 8


class TavilyError(RuntimeError):
    """Custom error for Tavily-related failures."""


def tavily_search(
    query: str,
    *,
    search_type: str = "news",          # "news", "general", etc. (depending on Tavily plan)
    max_results: int = DEFAULT_MAX_RESULTS,
    include_raw_content: bool = False,
    timeout_s: int = 20,
) -> Dict[str, Any]:
    """
    Perform a Tavily search and return a normalized result structure.

    Returns:
        {
          "query": str,
          "results": list[dict],
          "raw": dict    # full Tavily response
        }
    """
    if not TAVILY_API_KEY:
        raise TavilyError("Missing TAVILY_API_KEY in environment/.env")

    if not query or not query.strip():
        return {"query": query, "results": [], "raw": {}}

    payload: Dict[str, Any] = {
        "api_key": TAVILY_API_KEY,
        "query": query.strip(),
        "search_type": search_type,
        "max_results": max_results,
        "include_raw_content": include_raw_content,
    }

    log.info("Tavily search: type=%s max_results=%s query=%r", search_type, max_results, query)

    try:
        resp = requests.post(TAVILY_API_URL, json=payload, timeout=timeout_s)
        resp.raise_for_status()
    except requests.exceptions.HTTPError as e:
        msg = f"Tavily HTTP {resp.status_code}: {resp.text}"
        log.error("Tavily HTTP error: %s", msg)
        raise TavilyError(msg) from e
    except requests.exceptions.RequestException as e:
        msg = f"Tavily request failed: {e}"
        log.error(msg)
        raise TavilyError(msg) from e

    try:
        data = resp.json()
    except Exception as e:
        msg = f"Tavily returned non-JSON response: {resp.text[:200]}"
        log.error(msg)
        raise TavilyError(msg) from e

    results = data.get("results") or []
    if not isinstance(results, list):
        log.warning("Tavily 'results' not a list; normalizing to empty list.")
        results = []

    return {
        "query": query,
        "results": results,
        "raw": data,
    }


def search_news(
    query: str,
    *,
    max_results: int = DEFAULT_MAX_RESULTS,
    include_raw_content: bool = False,
) -> List[Dict[str, Any]]:
    """
    Convenience wrapper for bot.py.
    Returns ONLY the list of results (each a dict).

    This matches a common bot expectation:
        results = search_news("artist tour news")
        for r in results: r.get("title"), r.get("url"), ...
    """
    try:
        out = tavily_search(
            query,
            search_type="news",
            max_results=max_results,
            include_raw_content=include_raw_content,
        )
        results = out.get("results", [])
        return results if isinstance(results, list) else []
    except TavilyError as e:
        log.warning("Tavily search_news failed: %s", e)
        return []
