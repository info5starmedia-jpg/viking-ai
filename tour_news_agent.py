"""
Stable shim module for tour news.

Keeps old imports working:
    from tour_news_agent import search_tour_news
"""

from typing import Any, Dict, List

def search_tour_news(artist: str, *args, **kwargs) -> List[Dict[str, Any]]:
    try:
        from agents.tour_news_agent_v3 import search_tour_news as impl
        return impl(artist, *args, **kwargs)
    except Exception:
        from tour_news_agent_v3 import get_tour_news as impl
        return impl(artist, *args, **kwargs)

def get_tour_news(artist: str, *args, **kwargs):
    return search_tour_news(artist, *args, **kwargs)
